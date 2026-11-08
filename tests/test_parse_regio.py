from pathlib import Path

import pytest

from atlas import config
from atlas.parse_regio import ParseError, parse, parse_value, wz_code_for


@pytest.fixture(scope="module")
def table():
    return parse(config.REGIO_FILE)


def test_kopf_und_umfang(table):
    assert table.table_code == "13111-07-05-4"
    assert table.period == "2025-06-30"
    # 19 Regionen (Land + 18 Kreise) × 14 Wirtschaftszweige × 6 Spalten
    assert len(table.observations) == 19 * 14 * 6
    assert len({o.ars for o in table.observations if o.region_level == "kreis"}) == 18


def test_mehrzeiliges_label_mit_semikolon(table):
    codes = {o.wz_code for o in table.observations}
    assert {"M-N", "O-Q", "R-U", "TOTAL", "C"} <= codes


def test_regionsnamen_bereinigt(table):
    names = {o.ars: o.region_name for o in table.observations}
    assert names["12"] == "Brandenburg"
    assert names["12051"] == "Brandenburg an der Havel"
    assert names["12073"] == "Uckermark"


def test_symbole_werden_qualitaetsmarken(table):
    obs = {(o.ars, o.wz_code, o.nationalitaet, o.geschlecht): o for o in table.observations}
    geheim = obs[("12051", "K", "auslaendisch", "insgesamt")]
    assert geheim.raw == "." and geheim.value is None and geheim.quality == "geheim"
    null = obs[("12051", "L", "auslaendisch", "weiblich")]
    assert null.raw == "-" and null.value == 0 and null.quality == "ok"


def test_bekannter_wert(table):
    obs = {(o.ars, o.wz_code, o.nationalitaet, o.geschlecht): o.value for o in table.observations}
    assert obs[("12", "TOTAL", "insgesamt", "insgesamt")] == 883830
    assert obs[("12067", "C", "insgesamt", "insgesamt")] == 18552


def test_unbekannte_werte_brechen_ab():
    with pytest.raises(ParseError):
        parse_value("x")
    with pytest.raises(ParseError):
        wz_code_for("Neuer Wirtschaftszweig ohne Code")


def test_schemaaenderung_wird_erkannt(tmp_path: Path):
    raw = config.REGIO_FILE.read_bytes().decode("cp1252")
    changed = raw.replace(";;;Geschlecht;Geschlecht;Geschlecht;Geschlecht;Geschlecht;Geschlecht", ";;;Alter;Alter;Alter;Alter;Alter;Alter", 1)
    assert changed != raw
    f = tmp_path / "geaendert.csv"
    f.write_bytes(changed.encode("cp1252"))
    with pytest.raises(ParseError, match="Nationalität × Geschlecht"):
        parse(f)
