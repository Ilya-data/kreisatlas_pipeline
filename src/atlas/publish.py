"""Schritt 8: Veröffentlichen – GeoJSON und eine statische, eigenständige HTML-Karte.

Die Karte braucht keine externen Bibliotheken oder Kartenkacheln: Die
Kreisflächen werden direkt aus den UTM-Koordinaten (metrisch, winkeltreu) als
SVG gezeichnet. Alle Werte kommen aus der Datenbank, nicht aus dem Parser.
"""
from __future__ import annotations

import html
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .geo import inner_point, read_districts

# Kreisfreie Städte sind zu klein für eine Innenbeschriftung: Name außerhalb,
# mit Hinweislinie. Versatz in Pixeln (dx, dy) vom Flächenmittelpunkt.
CITY_LABEL_OFFSET = {
    "12051": (-22, 46),   # Brandenburg an der Havel
    "12052": (34, -34),   # Cottbus
    "12053": (10, -32),   # Frankfurt (Oder)
    "12054": (-34, -26),  # Potsdam
}
# Feinkorrektur für Innenbeschriftungen in schmalen Flächen (Pixel)
LABEL_NUDGE = {"12066": (1, -14), "12071": (-12, 4)}  # Oberspreewald-Lausitz, Spree-Neiße
# Zeilenumbruch von Hand, wo die Fläche für zwei Zeilen zu schmal ist
LABEL_LINES = {"12066": ["Ober-", "spreewald-", "Lausitz"]}
SHORT_NAMES = {"Brandenburg an der Havel": "Brandenburg a. d. Havel"}

# 5 Klassen mit runden Grenzen (Prozent). Untergrenze inklusiv.
BREAKS = [0, 5, 10, 15, 20]
# Sequenzielle Skala, ein Farbton (Referenzpalette "blue", Stufen 150/300/400/500/650)
RAMP_LIGHT = ["#b7d3f6", "#6da7ec", "#3987e5", "#256abf", "#104281"]
# Dunkelmodus: Anker gedreht – kleine Werte nah an der dunklen Fläche
RAMP_DARK = ["#184f95", "#1c5cab", "#2a78d6", "#5598e7", "#9ec5f4"]


def _fmt_pct(v: float) -> str:
    return f"{v:.1f}".replace(".", ",") + " %"


def _fmt_int(v: float) -> str:
    return f"{int(v):,}".replace(",", ".")


def _class_of(v: float) -> int:
    return max(i for i, b in enumerate(BREAKS) if v >= b)


def _class_label(i: int) -> str:
    if i == len(BREAKS) - 1:
        return f"ab {BREAKS[i]} %"
    if i == 0:
        return f"unter {BREAKS[1]} %"
    return f"{BREAKS[i]} bis unter {BREAKS[i + 1]} %"


def _load(db_path: Path) -> tuple[list[dict], dict, list[dict], dict]:
    con = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM v_industrieanteil ORDER BY ars")]
    ind = dict(con.execute("SELECT * FROM indicator WHERE indicator_id = 'industrieanteil'").fetchone())
    sources = [dict(r) for r in con.execute("SELECT * FROM source ORDER BY source_id DESC")]
    run = dict(con.execute("SELECT run_id, started_at, raw_manifest FROM load_run ORDER BY run_id DESC LIMIT 1").fetchone())
    geoms = {r["ars"]: json.loads(r["geojson"]) for r in con.execute("SELECT ars, geojson FROM region_geometry")}
    con.close()
    for r in rows:
        r["geometry"] = geoms.get(r["ars"])
    return rows, ind, sources, run


def write_geojson(db_path: Path, out: Path) -> None:
    rows, ind, sources, run = _load(db_path)
    features = []
    for r in rows:
        if r["geometry"] is None:
            continue  # Landeszeile hat keine eigene Geometrie
        features.append(
            {
                "type": "Feature",
                "id": r["ars"],
                "geometry": r["geometry"],
                "properties": {
                    "ars": r["ars"],
                    "name": r["name"],
                    "bez": r["bez"],
                    "stichtag": r["period"],
                    "svb_verarb_gewerbe": r["verarb_gewerbe"],
                    "svb_insgesamt": r["insgesamt"],
                    "industrieanteil_prozent": r["anteil_prozent"],
                    "qualitaet": r["quality"],
                },
            }
        )
    fc = {
        "type": "FeatureCollection",
        "metadata": {
            "indikator": ind["name"],
            "definition": ind["definition"],
            "einheit": ind["unit"],
            "quellen": [s["attribution"] for s in sources],
            "lauf": run["run_id"],
            "erzeugt": run["started_at"],
        },
        "features": features,
    }
    out.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def _svg_map(rows: list[dict], gpkg: Path, width: int = 560) -> tuple[str, int]:
    by_ars = {r["ars"]: r for r in rows}
    districts = [d for d in read_districts(gpkg, "12") if d.ars in by_ars]
    xs = [x for d in districts for p in d.geometry for ring in p for x, _ in ring]
    ys = [y for d in districts for p in d.geometry for ring in p for _, y in ring]
    pad, pad_right = 8, 72  # rechts Platz für Städtenamen an der Landesgrenze
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    s = (width - pad - pad_right) / (maxx - minx)
    height = int((maxy - miny) * s + 2 * pad)

    def pt(x: float, y: float) -> str:
        return f"{(x - minx) * s + pad:.1f},{(maxy - y) * s + pad:.1f}"

    paths, labels = [], []
    for d in districts:
        r = by_ars[d.ars]
        dpath = " ".join("M" + " L".join(pt(x, y) for x, y in ring) + " Z" for poly in d.geometry for ring in poly)
        cls = _class_of(r["anteil_prozent"]) if r["anteil_prozent"] is not None else None
        fill = f"var(--c{cls})" if cls is not None else "url(#hatch)"
        title = f"{d.name}: {_fmt_pct(r['anteil_prozent'])}" if r["anteil_prozent"] is not None else f"{d.name}: geheim"
        paths.append(
            f'<path class="kreis" data-ars="{d.ars}" d="{dpath}" fill="{fill}" fill-rule="evenodd" '
            f'tabindex="0" role="img" aria-label="{html.escape(title)}"></path>'
        )
        lx, ly = inner_point(d.geometry)
        x, y = (float(v) for v in pt(lx, ly).split(","))
        name = SHORT_NAMES.get(d.name, d.name)
        if d.ars in CITY_LABEL_OFFSET:
            dx, dy = CITY_LABEL_OFFSET[d.ars]
            tx, ty = x + dx, y + dy
            anchor = "start" if dx > 10 else ("end" if dx < -10 else "middle")
            ex, ey = tx - (4 if anchor == "start" else -4 if anchor == "end" else 0), ty + (5 if dy < 0 else -9)
            labels.append(
                f'<circle class="city-dot" cx="{x:.1f}" cy="{y:.1f}" r="2"/>'
                f'<line class="leader" x1="{x:.1f}" y1="{y:.1f}" x2="{ex:.1f}" y2="{ey:.1f}"/>'
                f'<text class="lbl lbl-city" x="{tx:.1f}" y="{ty:.1f}" style="text-anchor:{anchor}" data-ars="{d.ars}">{html.escape(name)}</text>'
            )
            continue
        nx, ny = LABEL_NUDGE.get(d.ars, (0, 0))
        x, y = x + nx, y + ny
        if d.ars in LABEL_LINES:
            parts = LABEL_LINES[d.ars]
        elif "-" in name and len(name) > 11:
            first, rest = name.split("-", 1)
            parts = [first + "-", rest]
        else:
            parts = [name]
        hi = " lbl-hi" if cls is not None and cls >= 3 else ""
        tspans = "".join(
            f'<tspan x="{x:.1f}" dy="{(-0.55 * (len(parts) - 1) if k == 0 else 1.1):.2f}em">{html.escape(part)}</tspan>'
            for k, part in enumerate(parts)
        )
        labels.append(f'<text class="lbl{hi}" x="{x:.1f}" y="{y:.1f}" data-ars="{d.ars}">{tspans}</text>')
    # Berlin liegt als Loch in der Mitte – ohne Hinweis wirkt es wie fehlende Daten
    berlin = read_districts(gpkg, "11")
    bx, by = (float(v) for v in pt(*inner_point(berlin[0].geometry)).split(",")) if berlin else (-100.0, -100.0)
    svg = (
        f'<svg class="map" viewBox="0 0 {width} {height}" role="group" aria-label="Karte der Kreise Brandenburgs">'
        '<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
        '<line x1="0" y1="0" x2="0" y2="6" stroke="var(--text-muted)" stroke-width="1.5"/></pattern></defs>'
        + "".join(paths)
        + '<g class="labels" aria-hidden="true">'
        + "".join(labels)
        + f'<text class="lbl-berlin" x="{bx:.1f}" y="{by:.1f}">Berlin</text>'
        + "</g></svg>"
    )
    return svg, height


def _bars(rows: list[dict], land: dict) -> str:
    kreise = sorted((r for r in rows if r["geometry"] is not None), key=lambda r: -(r["anteil_prozent"] or -1))
    vmax = 30.0
    w_label, w_plot, row_h = 168, 200, 22
    h = row_h * len(kreise) + 28
    out = [f'<svg class="bars" viewBox="0 0 {w_label + w_plot + 56} {h}" role="img" aria-label="Rangliste Industrieanteil je Kreis">']
    for t in range(0, 31, 10):
        x = w_label + w_plot * t / vmax
        out.append(f'<line class="grid" x1="{x:.1f}" x2="{x:.1f}" y1="0" y2="{h - 22}"/>')
        out.append(f'<text class="tick" x="{x:.1f}" y="{h - 8}">{t} %</text>')
    lx = w_label + w_plot * land["anteil_prozent"] / vmax
    out.append(f'<line class="ref" x1="{lx:.1f}" x2="{lx:.1f}" y1="-2" y2="{h - 22}"/>')
    for i, r in enumerate(kreise):
        y = i * row_h
        v = r["anteil_prozent"] or 0
        bw = max(w_plot * v / vmax, 2)
        name = SHORT_NAMES.get(r["name"], r["name"])
        out.append(
            f'<g class="bar" data-ars="{r["ars"]}"><rect class="hit" x="0" y="{y}" width="{w_label + w_plot + 56}" height="{row_h}"/>'
            f'<text class="bname" x="{w_label - 8}" y="{y + 15}">{html.escape(name)}</text>'
            f'<path class="bfill" d="M{w_label},{y + 6} h{bw - 3:.1f} a3,3 0 0 1 3,3 v4 a3,3 0 0 1 -3,3 h{-(bw - 3):.1f} Z" fill="var(--c{_class_of(v)})"/>'
            f'<text class="bval" x="{w_label + bw + 5:.1f}" y="{y + 15}">{_fmt_pct(v)}</text></g>'
        )
    out.append(f'<text class="reflbl" x="{lx + 4:.1f}" y="{h - 26}">Land {_fmt_pct(land["anteil_prozent"])}</text>')
    out.append("</svg>")
    return "".join(out)


def write_map(db_path: Path, gpkg: Path, out: Path) -> None:
    rows, ind, sources, run = _load(db_path)
    land = next(r for r in rows if r["geometry"] is None)
    kreise = [r for r in rows if r["geometry"] is not None]
    valid = [r for r in kreise if r["anteil_prozent"] is not None]
    lo = min(valid, key=lambda r: r["anteil_prozent"])
    hi = max(valid, key=lambda r: r["anteil_prozent"])
    n_secret = len(kreise) - len(valid)
    svg_map, _ = _svg_map(rows, gpkg)
    bars = _bars(rows, land)
    counts = [sum(1 for r in valid if _class_of(r["anteil_prozent"]) == i) for i in range(len(BREAKS))]
    legend = "".join(
        f'<li><span class="sw" style="background:var(--c{i})"></span>{_class_label(i)} <span class="cnt">({c})</span></li>'
        for i, c in enumerate(counts)
    )
    legend += '<li class="muted">Berlin (weiß): nicht Teil der Tabelle</li>'
    if n_secret:
        legend += f'<li><span class="sw hatch"></span>geheim <span class="cnt">({n_secret})</span></li>'
    ranked = sorted(valid, key=lambda r: -r["anteil_prozent"])
    rank = {r["ars"]: i + 1 for i, r in enumerate(ranked)}
    data = {
        r["ars"]: {
            "name": r["name"],
            "bez": r["bez"],
            "anteil": r["anteil_prozent"],
            "zaehler": r["verarb_gewerbe"],
            "nenner": r["insgesamt"],
            "rang": rank.get(r["ars"]),
        }
        for r in kreise
    }
    table_rows = "".join(
        f"<tr><td>{r['ars']}</td><td>{html.escape(r['name'])}</td><td>{html.escape(r['bez'])}</td>"
        f"<td class='num'>{_fmt_int(r['verarb_gewerbe'])}</td><td class='num'>{_fmt_int(r['insgesamt'])}</td>"
        f"<td class='num'>{_fmt_pct(r['anteil_prozent'])}</td></tr>"
        for r in ranked
    )
    table_rows += (
        f"<tr class='land'><td>{land['ars']}</td><td>{html.escape(land['name'])}</td><td>Land</td>"
        f"<td class='num'>{_fmt_int(land['verarb_gewerbe'])}</td><td class='num'>{_fmt_int(land['insgesamt'])}</td>"
        f"<td class='num'>{_fmt_pct(land['anteil_prozent'])}</td></tr>"
    )
    stichtag = ".".join(reversed(land["period"].split("-")))
    manifest = json.loads(run["raw_manifest"])
    sha = {e["source_id"]: e["sha256"][:12] for e in manifest["files"]}
    css_vars_light = "".join(f"--c{i}:{c};" for i, c in enumerate(RAMP_LIGHT))
    css_vars_dark = "".join(f"--c{i}:{c};" for i, c in enumerate(RAMP_DARK))
    src_items = "".join(
        f'<li><a href="{html.escape(s["url"])}">{html.escape(s["name"])}</a>. {html.escape(s["attribution"])} '
        f'<span class="muted">(Rohdatei SHA-256 {sha.get(s["source_id"], "–")}…)</span></li>'
        for s in sources
    )

    page = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Industrieanteil je Kreis – Brandenburg {stichtag}</title>
<style>
.viz-root {{
  color-scheme: light;
  --surface-1:#fcfcfb; --surface-2:#f3f2ef; --border:#e2e1dc;
  --text-primary:#0b0b0b; --text-secondary:#52514e; --text-muted:#8a8984;
  --ref:#0b0b0b; --edge:#fcfcfb; --focus:#0b0b0b; --lbl-lo:#0b0b0b; --lbl-hi:#ffffff;
  {css_vars_light}
}}
@media (prefers-color-scheme: dark) {{
  :root:where(:not([data-theme="light"])) .viz-root {{
    color-scheme: dark;
    --surface-1:#1a1a19; --surface-2:#242422; --border:#383835;
    --text-primary:#f0efec; --text-secondary:#c3c2b7; --text-muted:#8f8e86;
    --ref:#f0efec; --edge:#1a1a19; --focus:#f0efec; --lbl-lo:#ffffff; --lbl-hi:#0b0b0b;
    {css_vars_dark}
  }}
}}
:root[data-theme="dark"] .viz-root {{
  color-scheme: dark;
  --surface-1:#1a1a19; --surface-2:#242422; --border:#383835;
  --text-primary:#f0efec; --text-secondary:#c3c2b7; --text-muted:#8f8e86;
  --ref:#f0efec; --edge:#1a1a19; --focus:#f0efec; --lbl-lo:#ffffff; --lbl-hi:#0b0b0b;
  {css_vars_dark}
}}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:var(--surface-1); }}
.viz-root {{ background:var(--surface-1); color:var(--text-primary); font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; padding:28px 24px 40px; max-width:1100px; margin:0 auto; }}
h1 {{ font-size:22px; line-height:1.25; margin:0 0 6px; font-weight:650; letter-spacing:-0.01em; }}
.lead {{ color:var(--text-secondary); margin:0 0 20px; max-width:70ch; font-size:15px; }}
.grid2 {{ display:grid; grid-template-columns:minmax(0,1.25fr) minmax(0,1fr); gap:28px; align-items:start; }}
@media (max-width:820px) {{ .grid2 {{ grid-template-columns:1fr; }} }}
h2 {{ font-size:13px; font-weight:600; color:var(--text-secondary); margin:0 0 8px; text-transform:none; }}
.map {{ width:100%; height:auto; display:block; }}
.kreis {{ stroke:var(--edge); stroke-width:1.5; stroke-linejoin:round; cursor:pointer; outline:none; transition:opacity .12s; }}
.map.has-hover .kreis:not(.on) {{ opacity:.55; }}
.kreis.on {{ stroke:var(--focus); stroke-width:2; }}
.kreis:focus-visible {{ stroke:var(--focus); stroke-width:2.5; }}
.lbl {{ font-size:10.5px; fill:var(--lbl-lo); text-anchor:middle; dominant-baseline:middle; pointer-events:none; }}
.lbl-hi {{ fill:var(--lbl-hi); }}
.lbl-city {{ font-size:10.5px; font-weight:600; fill:var(--text-primary); paint-order:stroke; stroke:var(--surface-1); stroke-width:3px; stroke-linejoin:round; dominant-baseline:auto; }}
.leader {{ stroke:var(--text-primary); stroke-width:1; }}
.city-dot {{ fill:var(--text-primary); }}
.lbl-berlin {{ font-size:10px; fill:var(--text-muted); text-anchor:middle; font-style:italic; }}
.legend {{ list-style:none; padding:0; margin:12px 0 0; display:flex; flex-wrap:wrap; gap:6px 16px; color:var(--text-secondary); font-size:12.5px; }}
.legend li {{ display:flex; align-items:center; gap:6px; }}
.sw {{ width:14px; height:14px; border-radius:3px; display:inline-block; }}
.sw.hatch {{ background:repeating-linear-gradient(45deg,var(--text-muted) 0 1.5px,transparent 1.5px 5px); }}
.cnt {{ color:var(--text-muted); }}
.bars {{ width:100%; height:auto; display:block; overflow:visible; }}
.bars .grid {{ stroke:var(--border); stroke-width:1; }}
.bars .tick {{ font-size:11px; fill:var(--text-muted); text-anchor:middle; }}
.bars .bname {{ font-size:12px; fill:var(--text-secondary); text-anchor:end; }}
.bars .bval {{ font-size:11.5px; fill:var(--text-primary); font-variant-numeric:tabular-nums; paint-order:stroke; stroke:var(--surface-1); stroke-width:4px; stroke-linejoin:round; }}
.bars .hit {{ fill:transparent; }}
.bars .bar {{ cursor:pointer; }}
.bars.has-hover .bar:not(.on) {{ opacity:.55; }}
.bars .bar.on .bname {{ fill:var(--text-primary); font-weight:600; }}
.bars .ref {{ stroke:var(--ref); stroke-width:1.5; stroke-dasharray:3 3; }}
.bars .reflbl {{ font-size:11px; fill:var(--text-primary); font-weight:600; }}
.tip {{ position:fixed; pointer-events:none; background:var(--surface-1); color:var(--text-primary); border:1px solid var(--border); border-radius:8px; padding:8px 10px; font-size:12.5px; box-shadow:0 4px 16px rgba(0,0,0,.12); opacity:0; transition:opacity .08s; z-index:10; min-width:190px; }}
.tip b {{ display:block; font-size:13px; margin-bottom:2px; }}
.tip .big {{ font-size:18px; font-weight:650; font-variant-numeric:tabular-nums; }}
.tip .row {{ display:flex; justify-content:space-between; gap:12px; color:var(--text-secondary); font-variant-numeric:tabular-nums; }}
details {{ margin-top:28px; border-top:1px solid var(--border); padding-top:12px; }}
summary {{ cursor:pointer; color:var(--text-secondary); font-weight:600; }}
.tablewrap {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; margin-top:10px; font-size:13px; }}
th, td {{ text-align:left; padding:5px 8px; border-bottom:1px solid var(--border); }}
th {{ color:var(--text-secondary); font-weight:600; }}
td.num, th.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
tr.land td {{ font-weight:600; }}
.notes {{ margin-top:22px; color:var(--text-secondary); font-size:12.5px; max-width:90ch; }}
.notes p {{ margin:4px 0; }}
.notes ul {{ margin:4px 0; padding-left:18px; }}
.muted {{ color:var(--text-muted); }}
a {{ color:inherit; }}
</style>
</head>
<body>
<div class="viz-root">
<h1>Industrieanteil der Beschäftigung je Kreis, Brandenburg, {stichtag}</h1>
<p class="lead">Anteil der sozialversicherungspflichtig Beschäftigten im Verarbeitenden Gewerbe an allen Beschäftigten am Arbeitsort.
Er reicht von {_fmt_pct(lo['anteil_prozent'])} in {html.escape(lo['name'])} bis {_fmt_pct(hi['anteil_prozent'])} in {html.escape(hi['name'])}; im Land sind es {_fmt_pct(land['anteil_prozent'])}.</p>
<div class="grid2">
  <section>
    <h2>Karte, 5 Klassen</h2>
    {svg_map}
    <ul class="legend" aria-label="Legende">{legend}</ul>
  </section>
  <section>
    <h2>Rangfolge der 18 Kreise und kreisfreien Städte</h2>
    {bars}
  </section>
</div>
<details>
  <summary>Tabelle mit Zähler und Nenner</summary>
  <div class="tablewrap"><table>
    <thead><tr><th>Schlüssel</th><th>Kreis</th><th>Art</th><th class="num">Verarb. Gewerbe</th><th class="num">Insgesamt</th><th class="num">Anteil</th></tr></thead>
    <tbody>{table_rows}</tbody>
  </table></div>
</details>
<div class="notes">
  <p><b>Definition.</b> {html.escape(ind['definition'])}</p>
  <p><b>Gebietsstand</b> 31.12.2025, <b>Stichtag</b> der Beschäftigtenzahlen {stichtag}. Arbeitsortprinzip: gezählt wird, wo gearbeitet wird, nicht wo gewohnt wird.</p>
  <p><b>Quellen.</b></p>
  <ul>{src_items}</ul>
  <p class="muted">Erzeugt durch die Atlas-Pipeline, Lauf {run['run_id']}, {run['started_at']}. Prototyp, keine amtliche Veröffentlichung.</p>
</div>
<div class="tip" id="tip" role="status" aria-live="polite"></div>
</div>
<script>
const DATA = {json.dumps(data, ensure_ascii=False)};
const LAND = {land['anteil_prozent']};
const fmtPct = v => v.toFixed(1).replace('.', ',') + ' %';
const fmtInt = v => Math.round(v).toLocaleString('de-DE');
const tip = document.getElementById('tip');
const map = document.querySelector('.map'), bars = document.querySelector('.bars');
function show(ars, ev) {{
  const d = DATA[ars]; if (!d) return;
  map.classList.add('has-hover'); bars.classList.add('has-hover');
  document.querySelectorAll('[data-ars]').forEach(el => el.classList.toggle('on', el.dataset.ars === ars));
  const diff = d.anteil - LAND;
  tip.innerHTML = `<b>${{d.name}}</b><div class="muted">${{d.bez}} · Rang ${{d.rang}} von 18</div>`
    + `<div class="big">${{fmtPct(d.anteil)}}</div>`
    + `<div class="row"><span>${{diff >= 0 ? '+' : '−'}}${{Math.abs(diff).toFixed(1).replace('.', ',')}} Pp. zum Land</span></div>`
    + `<div class="row"><span>Verarb. Gewerbe</span><span>${{fmtInt(d.zaehler)}}</span></div>`
    + `<div class="row"><span>Beschäftigte insgesamt</span><span>${{fmtInt(d.nenner)}}</span></div>`;
  tip.style.opacity = 1;
  move(ev);
}}
function move(ev) {{
  let x, y;
  if (ev && ev.clientX !== undefined) {{ x = ev.clientX; y = ev.clientY; }}
  else {{ const r = ev.target.getBoundingClientRect(); x = r.left + r.width / 2; y = r.top + r.height / 2; }}
  const w = tip.offsetWidth, h = tip.offsetHeight;
  let left = x + 14, top = y + 14;
  if (left + w > innerWidth - 8) left = x - w - 14;
  if (top + h > innerHeight - 8) top = y - h - 14;
  tip.style.left = left + 'px'; tip.style.top = top + 'px';
}}
function hide() {{
  tip.style.opacity = 0;
  map.classList.remove('has-hover'); bars.classList.remove('has-hover');
  document.querySelectorAll('[data-ars].on').forEach(el => el.classList.remove('on'));
}}
document.querySelectorAll('.kreis, .bar').forEach(el => {{
  el.addEventListener('pointerenter', ev => show(el.dataset.ars, ev));
  el.addEventListener('pointermove', move);
  el.addEventListener('pointerleave', hide);
  el.addEventListener('focus', ev => show(el.dataset.ars, ev));
  el.addEventListener('blur', hide);
}});
</script>
</body>
</html>
"""
    out.write_text(page, encoding="utf-8")
