"""Schritt 6: Laden in SQLite (Sternschema).

Beschreibungstabellen: source, indicator, region (+ region_geometry getrennt).
Faktentabelle: fact (eine Zeile je Region × Indikator × Zeitraum).
Dazu die normalisierte Rohtabelle obs_svb_wz (Staging) und load_run für die
Herkunft: jede Zeile verweist auf den Lauf und damit auf die Prüfsummen der
Rohdateien.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .geo import District, area_km2, to_wgs84
from .indicators import INDICATORS, FactRow
from .parse_regio import Observation

SCHEMA = """
CREATE TABLE source (
    source_id     TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    publisher     TEXT,
    url           TEXT,
    license       TEXT NOT NULL,
    attribution   TEXT NOT NULL,
    update_cycle  TEXT
);
CREATE TABLE load_run (
    run_id        INTEGER PRIMARY KEY,
    started_at    TEXT NOT NULL,
    fingerprint   TEXT NOT NULL,
    raw_manifest  TEXT NOT NULL,   -- JSON: Datei, SHA-256, Abrufzeit
    checks        TEXT NOT NULL    -- JSON: Prüfbericht
);
CREATE TABLE region (
    ars           TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    bez           TEXT,
    level         TEXT NOT NULL CHECK (level IN ('land', 'kreis')),
    parent_ars    TEXT REFERENCES region(ars),
    gebietsstand  TEXT NOT NULL,
    valid_from    TEXT,
    valid_to      TEXT,
    area_km2      REAL
);
CREATE TABLE region_geometry (
    ars           TEXT PRIMARY KEY REFERENCES region(ars),
    srs           TEXT NOT NULL,
    geojson       TEXT NOT NULL,
    source_id     TEXT NOT NULL REFERENCES source(source_id)
);
CREATE TABLE indicator (
    indicator_id   TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    unit           TEXT NOT NULL,
    topic          TEXT,
    definition     TEXT NOT NULL,
    source_id      TEXT NOT NULL REFERENCES source(source_id),
    numerator_id   TEXT REFERENCES indicator(indicator_id),
    denominator_id TEXT REFERENCES indicator(indicator_id)
);
CREATE TABLE fact (
    region_ars    TEXT NOT NULL REFERENCES region(ars),
    indicator_id  TEXT NOT NULL REFERENCES indicator(indicator_id),
    period        TEXT NOT NULL,
    value         REAL,
    quality       TEXT NOT NULL CHECK (quality IN ('ok', 'geheim', 'gerundet', 'geschaetzt')),
    run_id        INTEGER NOT NULL REFERENCES load_run(run_id),
    PRIMARY KEY (region_ars, indicator_id, period)
);
CREATE TABLE obs_svb_wz (
    ars           TEXT NOT NULL REFERENCES region(ars),
    wz_code       TEXT NOT NULL,
    wz_label      TEXT NOT NULL,
    nationalitaet TEXT NOT NULL,
    geschlecht    TEXT NOT NULL,
    period        TEXT NOT NULL,
    value         INTEGER,
    quality       TEXT NOT NULL,
    raw           TEXT NOT NULL,
    run_id        INTEGER NOT NULL REFERENCES load_run(run_id),
    PRIMARY KEY (ars, wz_code, nationalitaet, geschlecht, period)
);
CREATE VIEW v_industrieanteil AS
SELECT r.ars, r.name, r.bez, f.period,
       n.value AS verarb_gewerbe, d.value AS insgesamt,
       f.value AS anteil_prozent, f.quality
FROM fact f
JOIN region r ON r.ars = f.region_ars
JOIN fact n ON n.region_ars = f.region_ars AND n.period = f.period AND n.indicator_id = 'svb_ao_verarb_gewerbe'
JOIN fact d ON d.region_ars = f.region_ars AND d.period = f.period AND d.indicator_id = 'svb_ao_insgesamt'
WHERE f.indicator_id = 'industrieanteil';
"""


def rebuild(
    db_path: Path,
    sources: list[dict],
    manifest: dict,
    fingerprint: str,
    report: dict,
    observations: list[Observation],
    districts: list[District],
    facts: list[FactRow],
    gebietsstand: str,
    geo_source_id: str,
) -> int:
    """Baut die Datenbank in einer Transaktion neu auf; bei Fehlern bleibt die alte Datei erhalten."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        con.execute("PRAGMA foreign_keys = ON")
        con.executescript(SCHEMA)
        con.executemany(
            "INSERT INTO source VALUES (?,?,?,?,?,?,?)",
            [(s["source_id"], s["name"], s.get("publisher"), s.get("url"), s["license"], s["attribution"], s.get("update_cycle")) for s in sources],
        )
        cur = con.execute(
            "INSERT INTO load_run (started_at, fingerprint, raw_manifest, checks) VALUES (?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), fingerprint, json.dumps(manifest, ensure_ascii=False), json.dumps(report, ensure_ascii=False)),
        )
        run_id = cur.lastrowid

        geo_by_ars = {d.ars: d for d in districts}
        regions = {}
        for o in observations:
            regions.setdefault(o.ars, (o.region_name, o.region_level))
        land = next(a for a, (_, lvl) in regions.items() if lvl == "land")
        con.execute(
            "INSERT INTO region (ars, name, bez, level, parent_ars, gebietsstand, area_km2) VALUES (?,?,?,?,?,?,?)",
            (land, regions[land][0], "Land", "land", None, gebietsstand, round(sum(area_km2(d.geometry) for d in districts), 1)),
        )
        for ars, (name, lvl) in sorted(regions.items()):
            if lvl != "kreis":
                continue
            d = geo_by_ars[ars]
            con.execute(
                "INSERT INTO region (ars, name, bez, level, parent_ars, gebietsstand, area_km2) VALUES (?,?,?,?,?,?,?)",
                (ars, d.name, d.bez, "kreis", land, gebietsstand, round(area_km2(d.geometry), 1)),
            )
            geom = {"type": "MultiPolygon", "coordinates": to_wgs84(d.geometry)}
            con.execute(
                "INSERT INTO region_geometry VALUES (?,?,?,?)",
                (ars, "EPSG:4326", json.dumps(geom, separators=(",", ":")), geo_source_id),
            )

        con.executemany(
            "INSERT INTO indicator VALUES (?,?,?,?,?,?,?,?)",
            [(i.indicator_id, i.name, i.unit, i.topic, i.definition, i.source_id, i.numerator_id, i.denominator_id) for i in INDICATORS],
        )
        con.executemany(
            "INSERT INTO obs_svb_wz VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(o.ars, o.wz_code, o.wz_label, o.nationalitaet, o.geschlecht, o.period, o.value, o.quality, o.raw, run_id) for o in observations],
        )
        con.executemany(
            "INSERT INTO fact VALUES (?,?,?,?,?,?)",
            [(f.region_ars, f.indicator_id, f.period, f.value, f.quality, run_id) for f in facts],
        )
        con.commit()
    except Exception:
        con.close()
        tmp.unlink(missing_ok=True)
        raise
    con.close()
    tmp.replace(db_path)
    return run_id
