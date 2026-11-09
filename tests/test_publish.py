import re

from atlas import config, publish


def _kreise_und_land():
    rows, *_ = publish._load(config.DB_FILE)
    land = next(r for r in rows if r["geometry"] is None)
    return [r for r in rows if r["geometry"] is not None], land


def test_karte_zeichnet_genau_die_kreise_der_datenbank():
    kreise, land = _kreise_und_land()
    auswahl = kreise[:5]  # simuliert ein anderes Gebiet: nur diese Kreise dürfen erscheinen
    svg, _ = publish._svg_map(auswahl, config.GEO_FILE, land)
    gezeichnet = set(re.findall(r'<path class="kreis" data-ars="(\d+)"', svg))
    assert gezeichnet == {r["ars"] for r in auswahl}


def test_seite_nimmt_landname_und_anzahl_aus_den_daten(tmp_path):
    kreise, land = _kreise_und_land()
    out = tmp_path / "karte.html"
    publish.write_map(config.DB_FILE, config.GEO_FILE, out)
    page = out.read_text(encoding="utf-8")
    assert f"je Kreis, {land['name']}," in page
    assert f"Rangfolge der {len(kreise)} Kreise" in page
    assert f"von {len(kreise)}</div>" in page
