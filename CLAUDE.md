# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Prototype data pipeline: an open German regional-statistics table (Regionalstatistik 13111-07-05-4, employees by industry per district) plus BKG VG2500 district boundaries → validated SQLite star schema → GeoJSON + self-contained HTML choropleth of the "Industrieanteil" (manufacturing share of employment) per Kreis. Scope is currently Brandenburg only (`LAND_PREFIX = "12"` in `src/atlas/config.py`; `""` means all of Germany).

Code, docstrings, comments, identifiers in data (rule names, quality labels), README and commit messages are in **German**. Keep that convention.

## Commands

```bash
export PYTHONPATH=src                     # needed for `python -m atlas.pipeline`; pytest gets it from pyproject.toml
python -m atlas.pipeline --force          # full rebuild (without --force: skips if raw-data fingerprint unchanged)
python -m atlas.pipeline --register       # re-hash raw files into data/raw/MANIFEST.json (after changing raw data)
python -m pytest -q                       # pip install "pytest>=8" first; it is the only dependency
python -m pytest tests/test_validate.py::test_bruch_der_wz_hierarchie_faellt_auf -q   # single test
```

No linter/formatter is configured. CI (`.github/workflows/pipeline.yml`, Python 3.12) runs tests, then the pipeline (`--force` on push/PR, plain on the weekly schedule), then deploys `out/karte.html` to GitHub Pages from `main`.

## Hard constraints

- **Standard library only** for the pipeline (`dependencies = []`). The GeoPackage is read via `sqlite3` + manual GPKG/WKB decoding (`geo.py`), and UTM32 → WGS84 is computed by hand. Do not add GDAL/geopandas/shapely etc.
- **Raw data is byte-exact and checksummed.** `.gitattributes` marks `data/raw/**`, `*.sqlite`, `*.gpkg` as binary. Any change to a raw file makes `raw.verify_manifest()` fail until `--register` is run; sources' metadata live in `sources.json`.
- **`out/` is committed** (DB, GeoJSON, map, report, `last_run.json`). After code changes that affect output, rerun the pipeline with `--force` and commit the regenerated files.

## Architecture

`pipeline.run()` is the orchestrator; module docstrings are numbered by pipeline step (Schritt 2–9):

1. `raw.py` – verify SHA-256 of raw files against the manifest; `fingerprint()` over all hashes drives the "only rerun on changed data" logic via `out/last_run.json`.
2. `parse_regio.py` – parses the GENESIS CSV (cp1252, multi-row header nationality × gender, quoted labels with `;`, metadata footer) into a long list of `Observation`s. It is **fail-loud**: unknown WZ labels, value symbols or columns raise `ParseError` instead of being dropped. Symbols map to quality marks (`.` → value `None`, `geheim`; `-` → 0).
3. `geo.py` – reads district polygons from the GPKG, area, label points, coordinate transform.
4. `validate.py` – each rule returns a `Check` (level `error`/`warning`/`info`); `report()` passes only if all `error` checks pass. A failed report is still written to `out/pruefbericht.json`, and loading is aborted (exit 1). The indicator range check is added in `pipeline.py`, not `run_all()`.
5. `indicators.py` – `INDICATORS` metadata + `compute()` producing `FactRow`s. Shares are stored alongside their numerator and denominator indicators (`numerator_id`/`denominator_id`) so they can be re-aggregated correctly.
6. `db.py` – `rebuild()` builds the whole DB fresh into a `.tmp` file in one transaction and atomically replaces `out/atlas.sqlite`; the old DB survives failures. Schema (star): `source`, `indicator`, `region`, `region_geometry`, `fact`, staging `obs_svb_wz`, provenance `load_run`, view `v_industrieanteil`.
7. `publish.py` – takes all values and metadata from the DB (opened read-only via URI) and writes GeoJSON (EPSG:4326, geometry from `region_geometry`) and a standalone SVG/HTML map. Exception: the SVG shapes are re-read from the GPKG in UTM (undistorted metric drawing), restricted to the Land and Kreise in the DB. Land name and Kreis count on the page come from the data. Still Brandenburg-specific by design: hand-tuned label tweaks keyed by ARS (`CITY_LABEL_OFFSET`, `LABEL_NUDGE`, `LABEL_LINES`, `LABEL_SIZE`) and the Berlin hole hint (`HOLES`). Check label changes visually (light and dark mode). No external libs/tiles; fixed class breaks 5/10/15/20 %.

Domain rules that the code depends on:

- WZ (industry) codes overlap (B-F ⊃ B-E ⊃ C, etc.). Never sum all rows; use `WZ_DISJOINT` / `WZ_HIERARCHY` in `parse_regio.py`. Pseudo-code `TOTAL` is "Insgesamt".
- Suppressed (`geheim`) values are never back-calculated from differences; validation uses the weakened inequality (known parts ≤ total) when any part is secret.
- "Insgesamt" may exceed the sum of WZ sections by a few people (unassigned employees) — this is intentionally a `warning` check (`ohne_wz_zuordnung`), not an error.
- Adding an indicator should mean new rows in `INDICATORS`/`fact`, not a schema change.

Tests use the real raw files in `data/raw/` and mutate observations or copy files into `tmp_path` to prove rules/parse errors fire (including a Windows path-with-spaces case). `tests/test_publish.py` reads the committed `out/atlas.sqlite`.

The raw CSV contains only Brandenburg; extending to all of Germany needs a new download and a federal level in `db.rebuild()` (see README, „Nächste Schritte“).
