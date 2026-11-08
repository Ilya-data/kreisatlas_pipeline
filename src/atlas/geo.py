"""Kreisgrenzen aus dem BKG-GeoPackage (VG2500) lesen, ohne Geo-Bibliotheken.

Ein GeoPackage ist eine SQLite-Datenbank. Die Geometrie steht als
GeoPackage-Binary (Kopf + WKB) in der Spalte `geom`. Koordinaten sind in
ETRS89 / UTM Zone 32N (EPSG:25832) und werden für GeoJSON (RFC 7946) nach
WGS84 umgerechnet.
"""
from __future__ import annotations

import math
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path

Ring = list[tuple[float, float]]
Polygon = list[Ring]  # erster Ring außen, weitere Löcher
MultiPolygon = list[Polygon]

ENVELOPE_DOUBLES = {0: 0, 1: 4, 2: 6, 3: 6, 4: 8}


@dataclass(frozen=True)
class District:
    ars: str
    name: str
    bez: str
    srs_id: int
    geometry: MultiPolygon  # in EPSG:25832


def _read_wkb(buf: bytes, offset: int = 0) -> tuple[MultiPolygon, int]:
    endian = "<" if buf[offset] == 1 else ">"
    (gtype,) = struct.unpack_from(endian + "I", buf, offset + 1)
    offset += 5
    base = gtype % 1000  # ISO-Codes für Z/M abfangen
    dims = 2 + (1 if gtype // 1000 in (1, 2) else 0) + (1 if gtype // 1000 == 3 else 0)
    if dims != 2:
        raise ValueError(f"Nur 2D-Geometrien erwartet, Typ {gtype}")

    def read_polygon(off: int) -> tuple[Polygon, int]:
        (nrings,) = struct.unpack_from(endian + "I", buf, off)
        off += 4
        rings: Polygon = []
        for _ in range(nrings):
            (npts,) = struct.unpack_from(endian + "I", buf, off)
            off += 4
            coords = struct.unpack_from(endian + "d" * (2 * npts), buf, off)
            off += 16 * npts
            rings.append(list(zip(coords[0::2], coords[1::2])))
        return rings, off

    if base == 3:
        poly, offset = read_polygon(offset)
        return [poly], offset
    if base == 6:
        (n,) = struct.unpack_from(endian + "I", buf, offset)
        offset += 4
        polys: MultiPolygon = []
        for _ in range(n):
            part, offset = _read_wkb(buf, offset)
            polys.extend(part)
        return polys, offset
    raise ValueError(f"Geometrietyp {gtype} nicht unterstützt (erwartet Polygon/MultiPolygon)")


def decode_gpkg_geometry(blob: bytes) -> tuple[int, MultiPolygon]:
    if blob[:2] != b"GP":
        raise ValueError("Kein GeoPackage-Binary")
    flags = blob[3]
    endian = "<" if flags & 1 else ">"
    envelope = (flags >> 1) & 0b111
    if flags & 0b10000:
        return 0, []  # leere Geometrie
    (srs_id,) = struct.unpack_from(endian + "i", blob, 4)
    wkb_start = 8 + 8 * ENVELOPE_DOUBLES[envelope]
    geom, _ = _read_wkb(blob, wkb_start)
    return srs_id, geom


def read_districts(gpkg: Path | str, ars_prefix: str = "") -> list[District]:
    con = sqlite3.connect(f"file:{gpkg}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT ARS, GEN, BEZ, geom FROM vg2500_krs WHERE GF = 9 AND ARS LIKE ? ORDER BY ARS",
            (ars_prefix + "%",),
        ).fetchall()
    finally:
        con.close()
    out = []
    for ars, gen, bez, blob in rows:
        srs, geom = decode_gpkg_geometry(blob)
        out.append(District(ars=ars, name=gen, bez=bez, srs_id=srs, geometry=geom))
    return out


# --- ETRS89 / UTM 32N → geografische Koordinaten (GRS80) -------------------
_A = 6378137.0
_F = 1 / 298.257222101
_K0 = 0.9996
_E2 = _F * (2 - _F)
_EP2 = _E2 / (1 - _E2)
_LON0 = math.radians(9.0)  # Zone 32


def utm32_to_lonlat(x: float, y: float) -> tuple[float, float]:
    """Inverse transversale Mercatorprojektion (Snyder 1987), Genauigkeit im mm-Bereich."""
    e1 = (1 - math.sqrt(1 - _E2)) / (1 + math.sqrt(1 - _E2))
    m = y / _K0
    mu = m / (_A * (1 - _E2 / 4 - 3 * _E2**2 / 64 - 5 * _E2**3 / 256))
    phi1 = (
        mu
        + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
        + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
        + (151 * e1**3 / 96) * math.sin(6 * mu)
        + (1097 * e1**4 / 512) * math.sin(8 * mu)
    )
    sin1, cos1, tan1 = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    n1 = _A / math.sqrt(1 - _E2 * sin1**2)
    t1 = tan1**2
    c1 = _EP2 * cos1**2
    r1 = _A * (1 - _E2) / (1 - _E2 * sin1**2) ** 1.5
    d = (x - 500000.0) / (n1 * _K0)
    lat = phi1 - (n1 * tan1 / r1) * (
        d**2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * _EP2) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * _EP2 - 3 * c1**2) * d**6 / 720
    )
    lon = _LON0 + (
        d - (1 + 2 * t1 + c1) * d**3 / 6 + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * _EP2 + 24 * t1**2) * d**5 / 120
    ) / cos1
    return math.degrees(lon), math.degrees(lat)


def to_wgs84(geom: MultiPolygon, digits: int = 5) -> MultiPolygon:
    return [[[tuple(round(v, digits) for v in utm32_to_lonlat(x, y)) for x, y in ring] for ring in poly] for poly in geom]


def ring_area(ring: Ring) -> float:
    return 0.5 * sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]))


def area_km2(geom: MultiPolygon) -> float:
    """Fläche in km² aus UTM-Koordinaten (Löcher abgezogen)."""
    total = 0.0
    for poly in geom:
        total += abs(ring_area(poly[0])) - sum(abs(ring_area(r)) for r in poly[1:])
    return total / 1e6


def label_point(geom: MultiPolygon) -> tuple[float, float]:
    """Flächenschwerpunkt des größten Teilpolygons (für Beschriftungen)."""
    ring = max((p[0] for p in geom), key=lambda r: abs(ring_area(r)))
    a = ring_area(ring)
    cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        cross = x1 * y2 - x2 * y1
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    return cx / (6 * a), cy / (6 * a)


def _point_in_ring(x: float, y: float, ring: Ring) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def _dist_to_segment(px: float, py: float, a: tuple[float, float], b: tuple[float, float]) -> float:
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def inner_point(geom: MultiPolygon, steps: int = 40) -> tuple[float, float]:
    """Näherung des Pols der Unzugänglichkeit: der Punkt im größten Teilpolygon,
    der am weitesten vom Rand entfernt ist. Besser für Beschriftungen als der
    Schwerpunkt, der bei gebogenen Formen außerhalb liegen kann."""
    poly = max(geom, key=lambda p: abs(ring_area(p[0])))
    outer = poly[0]
    xs = [x for x, _ in outer]
    ys = [y for _, y in outer]
    best, best_d = label_point(geom), -1.0
    for i in range(1, steps):
        for j in range(1, steps):
            x = min(xs) + (max(xs) - min(xs)) * i / steps
            y = min(ys) + (max(ys) - min(ys)) * j / steps
            if not _point_in_ring(x, y, outer) or any(_point_in_ring(x, y, h) for h in poly[1:]):
                continue
            d = min(_dist_to_segment(x, y, a, b) for ring in poly for a, b in zip(ring, ring[1:] + ring[:1]))
            if d > best_d:
                best, best_d = (x, y), d
    return best
