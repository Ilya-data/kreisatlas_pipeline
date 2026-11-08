"""Pipeline: Rohdaten prüfen → einlesen → prüfen → laden → berechnen → veröffentlichen.

Aufruf:
    python -m atlas.pipeline              # läuft nur, wenn sich Rohdaten geändert haben
    python -m atlas.pipeline --force      # immer neu rechnen
    python -m atlas.pipeline --register   # Rohdateien neu im Manifest erfassen (neue Version)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone

from . import config, db, geo, indicators, parse_regio, publish, raw, validate

log = logging.getLogger("atlas")

GEO_SOURCE_ID = "bkg_vg2500"
GEBIETSSTAND = "2025-12-31"


def run(force: bool = False) -> int:
    # 2–3 Abruf und Rohdaten sichern: Prüfsummen gegen Manifest
    manifest = raw.verify_manifest()
    fp = raw.fingerprint(manifest)
    if not force and config.STATE_FILE.exists():
        last = json.loads(config.STATE_FILE.read_text(encoding="utf-8"))
        if last.get("fingerprint") == fp and config.DB_FILE.exists():
            log.info("Rohdaten unverändert (%s…), nichts zu tun.", fp[:12])
            return 0

    # 4 Einlesen und Normalisieren
    table = parse_regio.parse(config.REGIO_FILE)
    obs = [o for o in table.observations if o.ars.startswith(config.LAND_PREFIX)]
    districts = geo.read_districts(config.GEO_FILE, config.LAND_PREFIX)
    log.info("Eingelesen: %d Werte aus %s (Stichtag %s), %d Kreisgeometrien", len(obs), table.table_code, table.period, len(districts))

    # 5 Prüfen
    checks = validate.run_all(obs, {d.ars for d in districts}, land=config.LAND_PREFIX)
    facts = indicators.compute(obs)
    range_problems = indicators.check_ranges(facts)
    range_check = validate.Check("wertebereich_indikatoren", "error", "Anzahlen ≥ 0, Anteile zwischen 0 und 100 %", checked=len(facts))
    for p in range_problems:
        range_check.fail(p)
    checks.append(range_check)
    report = validate.report(checks)
    config.OUT_DIR.mkdir(parents=True, exist_ok=True)
    config.REPORT_FILE.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for c in checks:
        log.info("Prüfung %-28s %-7s geprüft %5d, auffällig %d", c.rule, "OK" if c.ok else c.level.upper(), c.checked, c.failed)
    if not report["passed"]:
        log.error("Prüfregeln verletzt, Laden abgebrochen. Details: %s", config.REPORT_FILE)
        return 1

    # 6–7 Laden und Berechnen
    run_id = db.rebuild(
        config.DB_FILE, raw.load_sources(), manifest, fp, report, obs, districts, facts, GEBIETSSTAND, GEO_SOURCE_ID
    )
    log.info("Geladen: %s (Lauf %d)", config.DB_FILE.name, run_id)

    # 8 Veröffentlichen
    publish.write_geojson(config.DB_FILE, config.GEOJSON_FILE)
    publish.write_map(config.DB_FILE, config.GEO_FILE, config.MAP_FILE)
    log.info("Veröffentlicht: %s, %s", config.GEOJSON_FILE.name, config.MAP_FILE.name)

    config.STATE_FILE.write_text(
        json.dumps({"fingerprint": fp, "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Atlas-Prototyp: Pipeline Industrieanteil je Kreis")
    ap.add_argument("--force", action="store_true", help="auch bei unveränderten Rohdaten neu rechnen")
    ap.add_argument("--register", action="store_true", help="Rohdateien im Manifest neu erfassen")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    if args.register:
        m = raw.write_manifest()
        log.info("Manifest geschrieben: %d Dateien", len(m["files"]))
        return 0
    return run(force=args.force)


if __name__ == "__main__":
    sys.exit(main())
