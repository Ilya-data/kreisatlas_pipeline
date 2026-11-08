"""Schritt 2/3: Rohdaten abrufen und unveränderlich sichern.

Im Prototyp liegen die Rohdateien im Repository. Dieser Schritt prüft sie
gegen das Manifest (SHA-256, Größe, Abrufzeit). Ein späterer API-Abruf
schreibt eine neue Datei und einen neuen Manifest-Eintrag, statt alte Dateien
zu überschreiben.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import config


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_sources() -> list[dict]:
    return json.loads(config.SOURCES_FILE.read_text(encoding="utf-8"))["sources"]


def build_manifest() -> dict:
    entries = []
    for src in load_sources():
        path = config.ROOT / src["file"]
        entries.append(
            {
                "source_id": src["source_id"],
                "file": src["file"],
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
                "retrieved_at": src["retrieved_at"],
            }
        )
    return {"files": entries}


def write_manifest() -> dict:
    manifest = build_manifest()
    config.MANIFEST_FILE.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def verify_manifest() -> dict:
    """Bricht ab, wenn eine Rohdatei fehlt oder nicht zum Manifest passt."""
    if not config.MANIFEST_FILE.exists():
        raise FileNotFoundError(f"Manifest fehlt: {config.MANIFEST_FILE}. Erst `python -m atlas.pipeline --register` ausführen.")
    manifest = json.loads(config.MANIFEST_FILE.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        path = config.ROOT / entry["file"]
        if not path.exists():
            raise FileNotFoundError(f"Rohdatei fehlt: {path}")
        actual = sha256(path)
        if actual != entry["sha256"]:
            raise ValueError(
                f"Prüfsumme passt nicht für {entry['file']}: Manifest {entry['sha256'][:12]}…, Datei {actual[:12]}…. "
                "Neue Rohdaten bitte mit --register als neue Version erfassen."
            )
    return manifest


def fingerprint(manifest: dict) -> str:
    """Ein Wert für alle Rohdateien zusammen: ändert er sich nicht, muss nichts neu gerechnet werden."""
    joined = "|".join(sorted(e["sha256"] for e in manifest["files"]))
    return hashlib.sha256(joined.encode()).hexdigest()
