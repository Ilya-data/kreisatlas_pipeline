import dataclasses

import pytest

from atlas import config, geo, validate
from atlas.parse_regio import parse


@pytest.fixture(scope="module")
def obs():
    return parse(config.REGIO_FILE).observations


@pytest.fixture(scope="module")
def kreise():
    return {d.ars for d in geo.read_districts(config.GEO_FILE, "12")}


def _by_rule(checks):
    return {c.rule: c for c in checks}


def test_echte_daten_bestehen_alle_fehlerregeln(obs, kreise):
    checks = validate.run_all(obs, kreise, land="12")
    failed = [c.rule for c in checks if c.level == "error" and not c.ok]
    assert failed == []
    assert validate.report(checks)["passed"]


def test_ohne_zuordnung_ist_nur_hinweis(obs, kreise):
    c = _by_rule(validate.run_all(obs, kreise, land="12"))["ohne_wz_zuordnung"]
    assert c.level == "warning" and c.failed == 3  # Land, Potsdam, Spree-Neiße


def _manipulate(obs, ars, wz, nat, ges, delta):
    out = []
    for o in obs:
        if (o.ars, o.wz_code, o.nationalitaet, o.geschlecht) == (ars, wz, nat, ges):
            o = dataclasses.replace(o, value=o.value + delta)
        out.append(o)
    return out


def test_geaenderter_kreiswert_faellt_auf(obs, kreise):
    bad = _manipulate(obs, "12067", "C", "insgesamt", "weiblich", 100)
    checks = _by_rule(validate.run_all(bad, kreise, land="12"))
    assert not checks["maennlich_plus_weiblich"].ok
    assert not checks["summe_kreise_gleich_land"].ok


def test_bruch_der_wz_hierarchie_faellt_auf(obs, kreise):
    # Baugewerbe (F) muss mit B-E genau B-F ergeben
    bad = _manipulate(obs, "12067", "F", "insgesamt", "insgesamt", 5)
    assert not _by_rule(validate.run_all(bad, kreise, land="12"))["wz_hierarchie"].ok


def test_fehlende_geometrie_faellt_auf(obs, kreise):
    checks = _by_rule(validate.run_all(obs, kreise - {"12054"}, land="12"))
    assert not checks["geo_abgleich"].ok
    assert not checks["vollstaendigkeit"].ok
