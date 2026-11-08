"""Pfade und feste Größen der Pipeline."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
OUT_DIR = ROOT / "out"
SOURCES_FILE = ROOT / "sources.json"
MANIFEST_FILE = RAW_DIR / "MANIFEST.json"

DB_FILE = OUT_DIR / "atlas.sqlite"
GEOJSON_FILE = OUT_DIR / "kreise_industrieanteil.geojson"
MAP_FILE = OUT_DIR / "karte.html"
REPORT_FILE = OUT_DIR / "pruefbericht.json"
STATE_FILE = OUT_DIR / "last_run.json"

REGIO_FILE = RAW_DIR / "13111-07-05-4.csv"
GEO_FILE = RAW_DIR / "DE_VG2500.gpkg"

# Prototyp-Umfang: ein Land. Für ganz Deutschland "" setzen.
LAND_PREFIX = "12"  # Brandenburg
