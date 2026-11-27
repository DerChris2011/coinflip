"""Progress report: how the findings of one project developed over several reviews.

Self-contained HTML with hand-built SVG charts (no external resources). Colour use:
- confidence (consistent / coin flip / once) is ordinal -> one blue ramp, darkest = most certain
- severity is a status -> the reserved status colours, always with a legend and labels
- the line chart has two series (single-run coverage, stability) -> categorical slots 1 and 2,
  every point labelled with its value
Every chart has a table view, and every mark a tooltip.
"""

from __future__ import annotations

import html

from coinflip.progress import local_time
from coinflip.report_html import CSS as BASE_CSS

PROGRESS_CSS = """
.viz-root{--conf-1:#1757ac;--conf-2:#338ced;--conf-3:#84b9f3;--sev-critical:#dc3c3d;--sev-high:#f18a59;
 --sev-medium:#fbb315;--sev-low:#a3a3a3;--sev-other:#d4d4d4;--s1:#257de0;--s2:#f47230;--grid:#e7e7e7;--axis:#c2c2c2;
 --up-good:#006c10;--down-bad:#b3261e}
@media (prefers-color-scheme: dark){:root:where(:not([data-theme="light"])) .viz-root{--conf-1:#84b9f3;--conf-2:#338ced;
 --conf-3:#1757ac;--sev-low:#6b6b6b;--sev-other:#444444;--s1:#338ced;--s2:#e76624;--grid:#303030;--axis:#474747;
 --up-good:#53c556;--down-bad:#eb6263}}
svg.chart{width:100%;height:auto;display:block}
svg.chart text{fill:var(--text-secondary);font-size:12px;font-family:inherit}
svg.chart text.val{fill:var(--text-primary);font-weight:600}
svg.chart .grid{stroke:var(--grid);stroke-width:1}
svg.chart .axis{stroke:var(--axis);stroke-width:1}
.f-conf-1{fill:var(--conf-1)}.f-conf-2{fill:var(--conf-2)}.f-conf-3{fill:var(--conf-3)}
.f-critical{fill:var(--sev-critical)}.f-high{fill:var(--sev-high)}.f-medium{fill:var(--sev-medium)}
.f-low{fill:var(--sev-low)}.f-other{fill:var(--sev-other)}
.s-1{stroke:var(--s1);fill:none;stroke-width:2}.s-2{stroke:var(--s2);fill:none;stroke-width:2}
.m-1{fill:var(--s1);stroke:var(--surface-1);stroke-width:2}.m-2{fill:var(--s2);stroke:var(--surface-1);stroke-width:2}
svg.chart rect:hover,svg.chart path.seg:hover,svg.chart circle:hover{opacity:.82}
.legend .sw{display:inline-block;width:12px;height:12px;border-radius:3px}
.sw.f-conf-1{background:var(--conf-1)}.sw.f-conf-2{background:var(--conf-2)}.sw.f-conf-3{background:var(--conf-3)}
.sw.f-critical{background:var(--sev-critical)}.sw.f-high{background:var(--sev-high)}.sw.f-medium{background:var(--sev-medium)}
.sw.f-low{background:var(--sev-low)}.sw.f-other{background:var(--sev-other)}
.chart-card{margin-top:12px}
.delta{font-weight:650}.delta.good{color:var(--up-good)}.delta.bad{color:var(--down-bad)}
table.data{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px}
table.data th,table.data td{border-bottom:1px solid var(--border);padding:5px 8px;text-align:right}
table.data th:first-child,table.data td:first-child{text-align:left}
td.cellrate{text-align:center;font-variant-numeric:tabular-nums;border-radius:6px}
.warn-box{border:1px solid var(--warn);background:var(--warn-bg);color:var(--text-primary);border-radius:10px;padding:10px 14px;margin:16px 0}
"""

FATE = {  # label, icon, badge class - never colour alone
    "fixed": ("Fixed", "✓", "good"),
    "still there": ("Still there", "●", "warn"),
    "new": ("New", "+", "bad"),
    "came and went": ("Came and went", "·", "muted"),
}
CONF = (("consistent", "conf-1", "Consistent"), ("coin_flips", "conf-2", "Coin flip"), ("once", "conf-3", "Seen once"))
SEV = (("critical", "Critical"), ("high", "High"), ("medium", "Medium"), ("low", "Low"), ("other", "No severity"))


def _e(value) -> str:
    return html.escape(str(value), quote=True)


def _label(st: dict, i: int) -> str:
    return f"Review {i + 1}"


def _sub(st: dict) -> str:
    stamp = local_time(st.get("started"))  # YYYY-MM-DD HH:MM
    when = f"{stamp[8:10]}.{stamp[5:7]}. {stamp[11:16]}" if stamp else ""
    commit = f"@{st['commit']}{'*' if st.get('dirty') else ''}" if st.get("commit") else ""
    return f"{when} {commit}".strip()


def _axis(value: float) -> tuple[int, int]:
    """(maximum, step) with four whole-number steps - findings are counted, never halved."""
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if value <= step * 4:
            return step * 4, step
    return int(value) + 1, max(1, int(value) // 4)


def _bar(x: float, y: float, w: float, h: float, top: bool) -> str:
    """A bar segment; the top segment gets 4px rounded corners at its data end."""
    if h <= 0:
        return ""
    r = min(4.0, h, w / 2) if top else 0.0
    return (f"M{x:.1f},{y + h:.1f} L{x:.1f},{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
            f"L{x + w - r:.1f},{y:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} L{x + w:.1f},{y + h:.1f} Z")


def _stacked(sessions: list[dict], series: list[tuple[str, str, str]], value_of, title: str) -> str:
    """Stacked bars, one per review; totals on top; 2px surface gap between segments."""
    W, H, left, right, top, bottom = 760, 300, 44, 16, 28, 52
    n = len(sessions)
    totals = [sum(value_of(s, key) for key, _, _ in series) for s in sessions]
    ymax, ystep = _axis(max(totals) or 1)
    plot_h, plot_w = H - top - bottom, W - left - right
    slot = plot_w / n
    bw = min(72.0, slot * 0.5)
    out = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="{_e(title)}">']
    for v in range(0, ymax + 1, ystep):
        y = top + plot_h - plot_h * v / ymax
        out.append(f'<line class="grid" x1="{left}" x2="{W - right}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end">{v}</text>')
    out.append(f'<line class="axis" x1="{left}" x2="{W - right}" y1="{top + plot_h}" y2="{top + plot_h}"/>')
    for i, st in enumerate(sessions):
        x = left + slot * i + (slot - bw) / 2
        y = top + plot_h
        present = [(key, cls, name, value_of(st, key)) for key, cls, name in series if value_of(st, key) > 0]
        for j, (key, cls, name, v) in enumerate(present):
            h = plot_h * v / ymax
            y -= h
            gap = 2 if j < len(present) - 1 else 0
            out.append(f'<path class="seg {cls}" d="{_bar(x, y + gap, bw, h - gap, j == len(present) - 1)}">'
                       f'<title>{_e(_label(st, i))}: {v} {_e(name.lower())}</title></path>')
        out.append(f'<text class="val" x="{x + bw / 2:.1f}" y="{y - 8:.1f}" text-anchor="middle">{totals[i]}</text>')
        out.append(f'<text x="{x + bw / 2:.1f}" y="{top + plot_h + 18}" text-anchor="middle">{_e(_label(st, i))}</text>')
        out.append(f'<text x="{x + bw / 2:.1f}" y="{top + plot_h + 34}" text-anchor="middle">{_e(_sub(st))}</text>')
    out.append("</svg>")
    legend = "".join(f'<span><span class="sw {cls}"></span>{_e(name)}</span>' for _, cls, name in series)
    return f'<div class="legend">{legend}</div>' + "".join(out)


def _line(sessions: list[dict]) -> str:
    """Single-run coverage and stability per review (both percent, one axis 0-100)."""
    W, H, left, right, top, bottom = 760, 260, 44, 170, 24, 44
    pad = 36  # keep the first and last point clear of the axis labels
    n = len(sessions)
    plot_h, plot_w = H - top - bottom, W - left - right
    xs = [left + pad + ((plot_w - 2 * pad) * i / (n - 1) if n > 1 else (plot_w - 2 * pad) / 2) for i in range(n)]
    out = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="Single-run coverage and stability per review">']
    for v in (0, 25, 50, 75, 100):
        y = top + plot_h - plot_h * v / 100
        out.append(f'<line class="grid" x1="{left}" x2="{W - right}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end">{v}%</text>')
    for i, st in enumerate(sessions):
        out.append(f'<text x="{xs[i]:.1f}" y="{top + plot_h + 20}" text-anchor="middle">{_e(_label(st, i))}</text>')
    for key, cls, name in (("single_run_coverage", "1", "Single-run coverage"), ("stability", "2", "Stability")):
        pts = [(xs[i], st[key]) for i, st in enumerate(sessions) if st.get(key) is not None]
        if not pts:
            continue
        coords = [(x, top + plot_h - plot_h * v / 100, v) for x, v in pts]
        if len(coords) > 1:
            d = " ".join(f"{'M' if k == 0 else 'L'}{x:.1f},{y:.1f}" for k, (x, y, _) in enumerate(coords))
            out.append(f'<path class="s-{cls}" d="{d}"/>')
        for x, y, v in coords:
            dy = -10 if cls == "1" else 18
            out.append(f'<circle class="m-{cls}" cx="{x:.1f}" cy="{y:.1f}" r="5"><title>{_e(name)}: {v:.0f}%</title></circle>')
            out.append(f'<text class="val" x="{x:.1f}" y="{y + dy:.1f}" text-anchor="middle">{v:.0f}%</text>')
        x, y, _ = coords[-1]
        out.append(f'<text x="{x + 12:.1f}" y="{y + 4:.1f}">{_e(name)}</text>')
    out.append("</svg>")
    legend = ('<div class="legend"><span><span class="sw" style="background:var(--s1)"></span>Single-run coverage</span>'
              '<span><span class="sw" style="background:var(--s2)"></span>Stability</span></div>')
    return legend + "".join(out)


def _table(head: list[str], rows: list[list]) -> str:
    th = "".join(f"<th>{_e(h)}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in row) + "</tr>" for row in rows)
    return f'<details><summary>Show as table</summary><table class="data"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></details>'


def _rate_cell(cell: dict | None, runs: int) -> str:
    if cell is None:
        return '<td class="cellrate" title="not reported in this review">-</td>'
    alpha = 0.15 + 0.7 * cell["rate"]
    ink = "color:#ffffff;" if alpha > 0.55 else ""
    return (f'<td class="cellrate" style="background:rgba(37,125,224,{alpha:.2f});{ink}" '
            f'title="{cell["hits"]} of {runs} runs - {_e(cell["status"])}">{cell["hits"]}/{runs}</td>')


def _fate_badge(fate: str) -> str:
    label, icon, cls = FATE[fate]
    return f'<span class="badge {cls}"><span aria-hidden="true">{icon}</span>{_e(label)}</span>'


def _delta(before, after, change) -> str:
    if change is None:
        return f"{before} → {after}"
    cls = "good" if after < before else "bad" if after > before else ""
    return f'{before} → {after} <span class="delta {cls}">({change:+.0f}%)</span>'


def render(p: dict) -> str:
    s, st = p["summary"], p["sessions"]
    n = len(st)
    head = (f'<header><p class="eyebrow">coinflip progress</p><h1>{_e(p["project"])}</h1>'
            f'<p class="meta">{n} reviews compared, from {_e(local_time(st[0].get("started")))} '
            f'to {_e(local_time(st[-1].get("started")))}</p></header>')
    if s["consistent_before"]:
        hero_num = f'{s["consistent_change"]:+.0f}%' if s["consistent_change"] is not None else "n/a"
        hero_txt = (f'consistent findings - the ones every run reports - went from {s["consistent_before"]} '
                    f'to {s["consistent_after"]}.')
    else:
        hero_num = f'{s["distinct_before"]} → {s["distinct_after"]}'
        hero_txt = "findings over all reviews."
    hero = (f'<section class="card hero"><div class="hero-number">{_e(hero_num)}</div><p>{_e(hero_txt)} '
            f'{s["fixed"]} findings of the first review are gone ({s["consistent_fixed"]} of them consistent), '
            f'{s["new"]} are new.</p></section>')
    tiles = ('<div class="tiles">'
             f'<div class="tile"><div class="v">{_delta(s["distinct_before"], s["distinct_after"], s["distinct_change"])}</div><div class="l">all findings</div></div>'
             f'<div class="tile"><div class="v">{_delta(s["consistent_before"], s["consistent_after"], s["consistent_change"])}</div><div class="l">consistent findings</div></div>'
             f'<div class="tile"><div class="v">{s["fixed"]}</div><div class="l">fixed (gone in the last review)</div></div>'
             f'<div class="tile"><div class="v">{s["still_there"]}</div><div class="l">still there</div></div>'
             f'<div class="tile"><div class="v">{s["new"]}</div><div class="l">new</div></div>'
             "</div>")
    warnings = "".join(f'<div class="warn-box">{_e(w)}</div>' for w in p["warnings"])

    conf_series = [(k, f"f-{c}", name) for k, c, name in CONF]
    conf = _stacked(st, conf_series, lambda x, k: x[k], "Findings per review by confidence")
    conf_table = _table(["Review", "Consistent", "Coin flip", "Seen once", "Total"],
                        [[_label(x, i), x["consistent"], x["coin_flips"], x["once"], x["distinct_findings"]] for i, x in enumerate(st)])
    sev_series = [(k, f"f-{k}", name) for k, name in SEV if any(x["severity"][k] for x in st)]
    sev = _stacked(st, sev_series, lambda x, k: x["severity"][k], "Findings per review by severity")
    sev_table = _table(["Review", *[name for _, _, name in sev_series]],
                       [[_label(x, i), *[x["severity"][k] for k, _, _ in sev_series]] for i, x in enumerate(st)])
    line = _line(st)
    line_table = _table(["Review", "Single-run coverage", "Stability"],
                        [[_label(x, i), f'{x["single_run_coverage"] or 0:.0f}%', f'{x["stability"] or 0:.0f}%'] for i, x in enumerate(st)])

    cols = "".join(f'<th class="cell" scope="col">{_e(_label(x, i))}<br><span class="loc">{_e(_sub(x))}</span></th>' for i, x in enumerate(st))
    fate_rows = "".join(
        f'<tr><td class="label">{_e(r["title"])}<div class="loc">{_e(r["file"])}:{r["line"]}</div></td>'
        + "".join(_rate_cell(c, st[i]["runs"]) for i, c in enumerate(r["cells"]))
        + f"<td>{_fate_badge(r['fate'])}</td></tr>"
        for r in p["findings"][:80]
    )
    fates = (f'<div class="scroll"><table class="matrix"><thead><tr><th scope="col">Finding</th>{cols}'
             f'<th scope="col">Fate</th></tr></thead><tbody>{fate_rows}</tbody></table></div>')

    files = sorted({f for x in st for f in x["files"]}, key=lambda f: (-sum(x["files"].get(f, 0) for x in st), f))
    file_head = "".join(f"<th>{_e(_label(x, i))}</th>" for i, x in enumerate(st))
    peak = max([x["files"].get(f, 0) for x in st for f in files] or [1])
    file_rows = "".join(
        f"<tr><td>{_e(f)}</td>" + "".join(
            f'<td class="cellrate" style="background:rgba(37,125,224,{0.08 + 0.6 * x["files"].get(f, 0) / peak:.2f})">'
            f'{x["files"].get(f, 0)}</td>' for x in st) + "</tr>"
        for f in files[:40]
    )
    file_table = f'<table class="data"><thead><tr><th>File</th>{file_head}</tr></thead><tbody>{file_rows}</tbody></table>'

    body = (
        head + hero + tiles + warnings
        + '<h2>Findings per review</h2><p class="sub">Every bar is one review; the segments show how sure the runs were.</p>'
        + f'<div class="card chart-card">{conf}{conf_table}</div>'
        + '<h2>By severity</h2><p class="sub">Severity as given by the reviewer (highest of all runs).</p>'
        + f'<div class="card chart-card">{sev}{sev_table}</div>'
        + '<h2>How reliable was each review?</h2><p class="sub">Single-run coverage: what one run alone showed of '
          'everything found. Stability: how much two runs agreed.</p>'
        + f'<div class="card chart-card">{line}{line_table}</div>'
        + '<h2>What happened to each finding</h2><p class="sub">Cells show in how many runs of that review a finding '
          'came back. Findings are linked across reviews by file, line and wording - after code changes this is a '
          'best guess, not an exact match.</p>' + fates
        + '<h2>Findings per file</h2>' + file_table
    )
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>coinflip progress - {_e(p['project'])}</title><style>{BASE_CSS}{PROGRESS_CSS}</style></head>"
        f'<body><main class="viz-root">{body}<footer>Generated by coinflip - counts come from each review\'s own '
        'session; linking single findings across reviews is approximate. '
        '<a href="https://github.com/DerChris2011/coinflip">github.com/DerChris2011/coinflip</a></footer>'
        "</main></body></html>\n"
    )
