"""Schritt 5: Prüfregeln.

Jede Regel liefert ein `Check` mit Anzahl geprüfter und verletzter Fälle.
Regeln der Stufe "error" stoppen die Pipeline vor dem Laden, "warning" wird
nur berichtet. Geheim gehaltene Werte werden nie zurückgerechnet; eine Regel
prüft nur Fälle, in denen alle beteiligten Werte vorliegen, sonst eine
abgeschwächte Ungleichung.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field

from .parse_regio import WZ_HIERARCHY, Observation

COLUMNS = [(n, g) for n in ("insgesamt", "auslaendisch") for g in ("insgesamt", "maennlich", "weiblich")]


@dataclass
class Check:
    rule: str
    level: str
    description: str
    checked: int = 0
    failed: int = 0
    examples: list[str] = field(default_factory=list)

    def fail(self, msg: str) -> None:
        self.failed += 1
        if len(self.examples) < 5:
            self.examples.append(msg)

    @property
    def ok(self) -> bool:
        return self.failed == 0


def _index(obs: list[Observation]) -> dict[tuple, Observation]:
    return {(o.ars, o.wz_code, o.nationalitaet, o.geschlecht): o for o in obs}


def check_completeness(obs: list[Observation], expected_kreise: set[str], land: str) -> Check:
    c = Check("vollstaendigkeit", "error", "Jede Region hat jeden Wirtschaftszweig in allen 6 Spalten; Kreise = erwartete Liste")
    idx = _index(obs)
    regions = {o.ars for o in obs}
    codes = {o.wz_code for o in obs}
    for missing in sorted((expected_kreise | {land}) - regions):
        c.fail(f"Region fehlt: {missing}")
    for extra in sorted(regions - expected_kreise - {land}):
        c.fail(f"Unerwartete Region: {extra}")
    for ars in regions:
        for code in codes:
            for n, g in COLUMNS:
                c.checked += 1
                if (ars, code, n, g) not in idx:
                    c.fail(f"{ars} {code} {n}/{g} fehlt")
    return c


def check_gender_sum(obs: list[Observation]) -> Check:
    c = Check("maennlich_plus_weiblich", "error", "männlich + weiblich = insgesamt")
    idx = _index(obs)
    for (ars, code, nat, ges), o in idx.items():
        if ges != "insgesamt":
            continue
        m, w = idx[(ars, code, nat, "maennlich")], idx[(ars, code, nat, "weiblich")]
        if o.value is None:
            continue
        if m.value is not None and w.value is not None:
            c.checked += 1
            if m.value + w.value != o.value:
                c.fail(f"{ars} {code} {nat}: {m.value}+{w.value}≠{o.value}")
        else:
            c.checked += 1
            known = (m.value or 0) + (w.value or 0)
            if known > o.value:
                c.fail(f"{ars} {code} {nat}: bekannte Teile {known} > {o.value}")
    return c


def check_foreign_le_total(obs: list[Observation]) -> Check:
    c = Check("auslaendisch_le_insgesamt", "error", "ausländisch ≤ insgesamt")
    idx = _index(obs)
    for (ars, code, nat, ges), o in idx.items():
        if nat != "auslaendisch" or o.value is None:
            continue
        total = idx[(ars, code, "insgesamt", ges)].value
        if total is None:
            continue
        c.checked += 1
        if o.value > total:
            c.fail(f"{ars} {code} {ges}: {o.value} > {total}")
    return c


def check_hierarchy(obs: list[Observation]) -> Check:
    c = Check(
        "wz_hierarchie",
        "error",
        "B-F und G-U = Summe ihrer Bestandteile; Summe A + B-F + G-U ≤ Insgesamt; C ≤ B-E",
    )
    idx = _index(obs)
    regions = {o.ars for o in obs}
    for ars in regions:
        for n, g in COLUMNS:
            for parent, children in WZ_HIERARCHY.items():
                p = idx[(ars, parent, n, g)].value
                vals = [idx[(ars, ch, n, g)].value for ch in children]
                if p is None:
                    continue
                c.checked += 1
                known = sum(v for v in vals if v is not None)
                complete = all(v is not None for v in vals)
                # "Insgesamt" enthält zusätzlich Beschäftigte ohne WZ-Zuordnung → nur ≤ prüfen
                exact = complete and parent != "TOTAL"
                if exact and known != p:
                    c.fail(f"{ars} {parent} {n}/{g}: Summe {known} ≠ {p}")
                elif known > p:
                    c.fail(f"{ars} {parent} {n}/{g}: Teile {known} > {p}")
            cv, be = idx[(ars, "C", n, g)].value, idx[(ars, "B-E", n, g)].value
            if cv is not None and be is not None:
                c.checked += 1
                if cv > be:
                    c.fail(f"{ars} {n}/{g}: C {cv} > B-E {be}")
    return c


def check_unassigned(obs: list[Observation]) -> Check:
    c = Check(
        "ohne_wz_zuordnung",
        "warning",
        "Insgesamt − (A + B-F + G-U) = Beschäftigte ohne Zuordnung zu einem Wirtschaftszweig (Warnung, kein Fehler)",
    )
    idx = _index(obs)
    for ars in sorted({o.ars for o in obs}):
        total = idx[(ars, "TOTAL", "insgesamt", "insgesamt")].value
        parts = [idx[(ars, ch, "insgesamt", "insgesamt")].value for ch in WZ_HIERARCHY["TOTAL"]]
        if total is None or any(v is None for v in parts):
            continue
        c.checked += 1
        if total - sum(parts):
            c.fail(f"{ars}: {total - sum(parts)} ohne Zuordnung")
    return c


def check_districts_sum_to_land(obs: list[Observation], land: str) -> Check:
    c = Check("summe_kreise_gleich_land", "error", "Summe der Kreise = Landeswert (bei geheimen Werten: Summe ≤ Land)")
    groups: dict[tuple, list[Observation]] = defaultdict(list)
    land_vals: dict[tuple, Observation] = {}
    for o in obs:
        key = (o.wz_code, o.nationalitaet, o.geschlecht)
        if o.ars == land:
            land_vals[key] = o
        else:
            groups[key].append(o)
    for key, members in groups.items():
        lv = land_vals[key].value
        if lv is None:
            continue
        c.checked += 1
        known = [m.value for m in members if m.value is not None]
        if len(known) == len(members):
            if sum(known) != lv:
                c.fail(f"{'/'.join(key)}: Kreise {sum(known)} ≠ Land {lv}")
        elif sum(known) > lv:
            c.fail(f"{'/'.join(key)}: bekannte Kreise {sum(known)} > Land {lv}")
    return c


def check_geo_match(stat_kreise: set[str], geo_kreise: set[str]) -> Check:
    c = Check("geo_abgleich", "error", "Jeder Kreis der Statistik hat eine Geometrie und umgekehrt")
    c.checked = len(stat_kreise | geo_kreise)
    for a in sorted(stat_kreise - geo_kreise):
        c.fail(f"keine Geometrie für {a}")
    for a in sorted(geo_kreise - stat_kreise):
        c.fail(f"keine Statistik für {a}")
    return c


def check_secrecy_share(obs: list[Observation], max_share: float = 0.10) -> Check:
    c = Check("anteil_geheim", "warning", f"Anteil geheimer Werte ≤ {max_share:.0%}")
    c.checked = len(obs)
    secret = [o for o in obs if o.quality == "geheim"]
    if len(secret) / max(len(obs), 1) > max_share:
        c.fail(f"{len(secret)} von {len(obs)} Werten geheim")
    return c


def run_all(obs: list[Observation], geo_kreise: set[str], land: str) -> list[Check]:
    stat_kreise = {o.ars for o in obs if o.region_level == "kreis"}
    return [
        check_completeness(obs, geo_kreise, land),
        check_geo_match(stat_kreise, geo_kreise),
        check_gender_sum(obs),
        check_foreign_le_total(obs),
        check_hierarchy(obs),
        check_unassigned(obs),
        check_districts_sum_to_land(obs, land),
        check_secrecy_share(obs),
    ]


def report(checks: list[Check]) -> dict:
    return {
        "passed": all(c.ok for c in checks if c.level == "error"),
        "checks": [asdict(c) | {"ok": c.ok} for c in checks],
    }
