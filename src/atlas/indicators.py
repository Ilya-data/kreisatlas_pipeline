"""Schritt 7: Indikatoren berechnen.

Anteile werden immer als Zähler und Nenner gespeichert, damit sie sich später
richtig auf größere Gebiete aggregieren lassen (Summe der Zähler / Summe der
Nenner, nie Mittelwert der Anteile).
"""
from __future__ import annotations

from dataclasses import dataclass

from .parse_regio import Observation

SOURCE_ID = "regio_13111-07-05-4"


@dataclass(frozen=True)
class Indicator:
    indicator_id: str
    name: str
    unit: str
    topic: str
    definition: str
    source_id: str
    numerator_id: str | None = None
    denominator_id: str | None = None


INDICATORS = [
    Indicator(
        "svb_ao_insgesamt",
        "SV-Beschäftigte am Arbeitsort",
        "Personen",
        "Arbeit",
        "Sozialversicherungspflichtig Beschäftigte am Arbeitsort, alle Wirtschaftszweige, Stichtag 30.06.",
        SOURCE_ID,
    ),
    Indicator(
        "svb_ao_verarb_gewerbe",
        "SV-Beschäftigte im Verarbeitenden Gewerbe",
        "Personen",
        "Arbeit",
        "Sozialversicherungspflichtig Beschäftigte am Arbeitsort im Abschnitt C der WZ 2008 (Verarbeitendes Gewerbe)",
        SOURCE_ID,
    ),
    Indicator(
        "industrieanteil",
        "Industrieanteil der Beschäftigung",
        "%",
        "Wirtschaftsstruktur",
        "Anteil der SV-Beschäftigten im Verarbeitenden Gewerbe (WZ C) an allen SV-Beschäftigten am Arbeitsort. "
        "Näherung für Industrie; Bergbau und Energie (B, D, E) und Bau (F) sind nicht enthalten.",
        SOURCE_ID,
        numerator_id="svb_ao_verarb_gewerbe",
        denominator_id="svb_ao_insgesamt",
    ),
]


@dataclass(frozen=True)
class FactRow:
    region_ars: str
    indicator_id: str
    period: str
    value: float | None
    quality: str


def compute(obs: list[Observation]) -> list[FactRow]:
    pick = {
        (o.ars, o.wz_code): o
        for o in obs
        if o.nationalitaet == "insgesamt" and o.geschlecht == "insgesamt" and o.wz_code in ("TOTAL", "C")
    }
    rows: list[FactRow] = []
    for ars in sorted({a for a, _ in pick}):
        total, manu = pick[(ars, "TOTAL")], pick[(ars, "C")]
        rows.append(FactRow(ars, "svb_ao_insgesamt", total.period, total.value, total.quality))
        rows.append(FactRow(ars, "svb_ao_verarb_gewerbe", manu.period, manu.value, manu.quality))
        if total.value and manu.value is not None:
            share, quality = round(100 * manu.value / total.value, 2), "ok"
        else:
            share, quality = None, "geheim"
        rows.append(FactRow(ars, "industrieanteil", total.period, share, quality))
    return rows


def check_ranges(rows: list[FactRow]) -> list[str]:
    """Wertebereich der berechneten Indikatoren."""
    problems = []
    for r in rows:
        if r.value is None:
            continue
        if r.value < 0 or (r.indicator_id == "industrieanteil" and r.value > 100):
            problems.append(f"{r.region_ars} {r.indicator_id}={r.value}")
    return problems
