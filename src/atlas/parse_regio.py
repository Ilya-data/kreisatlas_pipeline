"""Schritt 4: Regionalstatistik-CSV (GENESIS-Tabellenformat) einlesen und normalisieren.

Eigenheiten der Datei, die hier abgefangen werden:
- Kodierung cp1252, Semikolon, Kopfblock und Metadaten-Fußzeile.
- Spaltenkopf über mehrere Zeilen (Nationalität × Geschlecht).
- Labels in Anführungszeichen, die selbst Semikolons enthalten.
- Regionsnamen mit führenden Leerzeichen (Einrückung = Ebene).
- Symbole statt Zahlen: "." und "-".
- Wirtschaftszweig-Zeilen, die sich überlappen (B-F ⊃ B-E ⊃ C), und drei
  Zeilen ohne Buchstabencode im Label.

Ergebnis ist eine lange Tabelle: eine Zeile je Region × Wirtschaftszweig ×
Nationalität × Geschlecht.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path

ENCODING = "cp1252"

# Zeichenerklärung der Regionaldatenbank. Die Datei selbst enthält keine Legende;
# Bedeutung nach der Standard-Zeichenerklärung von GENESIS/Regionalstatistik.
SYMBOLS = {
    ".": (None, "geheim"),  # Zahlenwert unbekannt oder geheim zu halten
    "-": (0, "ok"),  # nichts vorhanden (genau null)
}

# Zeilen ohne Buchstabencode im Label → Abschnitte der WZ 2008
LABEL_CODES = {
    "Insgesamt": "TOTAL",
    "Erbringung von freiberuflichen, wissenschaftlichen, tech. und sonst. wirtsch. Dienstleistungen": "M-N",
    "Öff. Verwaltung, Verteidig.; Sozialvers., Erziehung und Unterricht, Gesundheits- und Sozialwesen": "O-Q",
    "Kunst, Unterhaltung und Erholung, sonstige Dienstl., Private Haushalte, Exterritoriale Org.": "R-U",
}
CODE_IN_LABEL = re.compile(r"\(([A-Z](?:-[A-Z])?)\)\s*$")

# Disjunkte Zerlegung von "Insgesamt" (für Prüfregeln und Berechnungen)
WZ_DISJOINT = ["A", "B-E", "F", "G-I", "J", "K", "L", "M-N", "O-Q", "R-U"]
# Hierarchie: Oberabschnitt → Bestandteile
WZ_HIERARCHY = {
    "B-F": ["B-E", "F"],
    "G-U": ["G-I", "J", "K", "L", "M-N", "O-Q", "R-U"],
    "TOTAL": ["A", "B-F", "G-U"],
}

NATIONALITAET = {"Insgesamt": "insgesamt", "ausländisch": "auslaendisch"}
GESCHLECHT = {"Insgesamt": "insgesamt", "männlich": "maennlich", "weiblich": "weiblich"}


class ParseError(ValueError):
    """Die Datei weicht vom erwarteten Aufbau ab (z. B. Schemaänderung der Quelle)."""


@dataclass(frozen=True)
class Observation:
    ars: str
    region_name: str
    region_level: str  # "land" | "kreis"
    wz_code: str
    wz_label: str
    nationalitaet: str
    geschlecht: str
    period: str  # ISO-Datum des Stichtags
    value: int | None
    quality: str
    raw: str


@dataclass(frozen=True)
class ParsedTable:
    table_code: str
    period: str
    observations: list[Observation]


def wz_code_for(label: str) -> str:
    if label in LABEL_CODES:
        return LABEL_CODES[label]
    m = CODE_IN_LABEL.search(label)
    if m:
        return m.group(1)
    raise ParseError(f"Unbekannter Wirtschaftszweig: {label!r}")


def parse_value(raw: str) -> tuple[int | None, str]:
    s = raw.strip()
    if s.isdigit():
        return int(s), "ok"
    if s in SYMBOLS:
        return SYMBOLS[s]
    raise ParseError(f"Unbekannter Zellwert: {raw!r}")


def _clean_region(name: str) -> tuple[str, str]:
    indent = len(name) - len(name.lstrip(" "))
    clean = name.strip()
    clean = re.sub(r",\s*(kreisfreie Stadt|Landkreis)$", "", clean)
    return clean, ("land" if indent <= 2 else "kreis")


def parse(path: Path | str) -> ParsedTable:
    text = Path(path).read_bytes().decode(ENCODING)
    rows = list(csv.reader(io.StringIO(text), delimiter=";", quotechar='"'))

    table_code = None
    period = None
    header_rows: list[list[str]] = []
    data_rows: list[list[str]] = []

    for row in rows:
        if not row:
            continue
        first = row[0].strip()
        if first.startswith("Tabelle:"):
            table_code = first.split(":", 1)[1].strip()
        elif first.startswith("Stichtag:"):
            d, m, y = first.split(":", 1)[1].strip().split(".")
            period = f"{y}-{m}-{d}"
        elif first.startswith("___"):
            break  # Beginn der Metadaten-Fußzeile
        elif first == "" and len(row) > 3 and not data_rows:
            header_rows.append(row)
        elif first.isdigit():
            data_rows.append(row)

    if not table_code or not period:
        raise ParseError("Tabellencode oder Stichtag im Kopfblock nicht gefunden")
    if len(header_rows) != 4:
        raise ParseError(f"Erwartet 4 Kopfzeilen (Nationalität, Werte, Geschlecht, Werte), gefunden {len(header_rows)}")

    dim1_name, dim1_vals, dim2_name, dim2_vals = header_rows
    if {v for v in dim1_name[3:]} != {"Nationalität"} or {v for v in dim2_name[3:]} != {"Geschlecht"}:
        raise ParseError("Spaltenkopf hat nicht den Aufbau Nationalität × Geschlecht")
    try:
        columns = [(NATIONALITAET[n], GESCHLECHT[g]) for n, g in zip(dim1_vals[3:], dim2_vals[3:])]
    except KeyError as e:
        raise ParseError(f"Unbekannte Ausprägung im Spaltenkopf: {e}") from None

    observations: list[Observation] = []
    for row in data_rows:
        if len(row) != 3 + len(columns):
            raise ParseError(f"Zeile hat {len(row)} statt {3 + len(columns)} Felder: {row[:3]}")
        ars = row[0].strip()
        region_name, level = _clean_region(row[1])
        label = row[2].strip()
        code = wz_code_for(label)
        for (nat, ges), raw in zip(columns, row[3:]):
            value, quality = parse_value(raw)
            observations.append(
                Observation(ars, region_name, level, code, label, nat, ges, period, value, quality, raw.strip())
            )

    return ParsedTable(table_code=table_code, period=period, observations=observations)
