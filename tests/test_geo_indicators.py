import shutil

import pytest

from atlas import config, geo, indicators, raw
from atlas.parse_regio import parse


@pytest.fixture(scope="module")
def districts():
    return geo.read_districts(config.GEO_FILE, "12")


def test_18_kreise_mit_flaeche(districts):
    assert len(districts) == 18
    assert all(d.srs_id == 25832 for d in districts)
    total = sum(geo.area_km2(d.geometry) for d in districts)
    # amtliche Landesfläche rund 29 654 km²; VG2500 ist generalisiert
    assert total == pytest.approx(29654, rel=0.01)


def test_umrechnung_nach_wgs84(districts):
    assert geo.utm32_to_lonlat(500000, 0) == pytest.approx((9.0, 0.0), abs=1e-9)
    pts = [p for d in districts for poly in geo.to_wgs84(d.geometry) for ring in poly for p in ring]
    lons, lats = [p[0] for p in pts], [p[1] for p in pts]
    # Brandenburg liegt etwa zwischen 11,2–14,8° O und 51,3–53,6° N
    assert 11.2 < min(lons) < max(lons) < 14.8
    assert 51.3 < min(lats) < max(lats) < 53.6


def test_beschriftungspunkt_liegt_innen(districts):
    for d in districts:
        x, y = geo.inner_point(d.geometry)
        poly = max(d.geometry, key=lambda p: abs(geo.ring_area(p[0])))
        assert geo._point_in_ring(x, y, poly[0]), d.name


def test_industrieanteil():
    rows = {(r.region_ars, r.indicator_id): r for r in indicators.compute(parse(config.REGIO_FILE).observations)}
    assert rows[("12067", "industrieanteil")].value == pytest.approx(100 * 18552 / 67871, abs=0.01)
    assert rows[("12", "industrieanteil")].value == pytest.approx(13.02, abs=0.01)
    assert indicators.check_ranges(list(rows.values())) == []


def test_manipulierte_rohdatei_wird_erkannt(tmp_path, monkeypatch):
    for name in ("sources.json",):
        shutil.copy(config.ROOT / name, tmp_path / name)
    (tmp_path / "data" / "raw").mkdir(parents=True)
    for f in ("13111-07-05-4.csv", "DE_VG2500.gpkg", "MANIFEST.json"):
        shutil.copy(config.RAW_DIR / f, tmp_path / "data" / "raw" / f)
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "SOURCES_FILE", tmp_path / "sources.json")
    monkeypatch.setattr(config, "MANIFEST_FILE", tmp_path / "data" / "raw" / "MANIFEST.json")
    raw.verify_manifest()  # unverändert: ok
    with open(tmp_path / "data" / "raw" / "13111-07-05-4.csv", "ab") as f:
        f.write(b"\n")
    with pytest.raises(ValueError, match="Prüfsumme"):
        raw.verify_manifest()


def test_pfad_mit_leerzeichen(tmp_path):
    # Windows-Pfade enthalten oft Leerzeichen; SQLite bekommt eine korrekt kodierte URI
    d = tmp_path / "Mein Ordner #1"
    d.mkdir()
    shutil.copy(config.GEO_FILE, d / "vg.gpkg")
    assert len(geo.read_districts(d / "vg.gpkg", "12")) == 18
