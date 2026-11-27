"""Readability sessions as text, markdown and HTML (a strip plot: one dot per run and file)."""

from __future__ import annotations

import html

from coinflip.readability import COIN_FLIP_SPREAD, CRITERIA, headline

STATUS = {"consistent": ("Consistent", "✓", "good"), "wobbly": ("Wobbly", "≈", "muted"),
          "coin flip": ("Coin flip", "~", "warn")}


def _n(value, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _rules(value) -> str:
    return "-" if value is None else f"{value:.0f}"


def _failures(r: dict) -> list[str]:
    f = r["failures"]
    if not (f["errors"] or f["unparseable"]):
        return []
    return ["", f"  ! {f['errors']} runs failed, {f['unparseable']} answers had no readable JSON",
            *[f"    {m}" for m in f["messages"]]]


def to_text(r: dict) -> str:
    """Plain-text readability report."""
    out = [f"coinflip {r.get('coinflip', '')}  |  readability: {r.get('project')} ({r['files_requested']} files)  |  "
           f"reviewer: {r.get('reviewer')}  |  {r['runs']} runs", "", f"  >> {headline(r)}"]
    if r.get("readability") is not None:
        agree = r["agreement_with_rules"]
        out += [
            "",
            f"  Consistent      {r['consistent']:>5}   files rated within {1} point in every run",
            f"  Wobbly          {r['wobbly']:>5}   2 points apart",
            f"  Coin flips      {r['coin_flips']:>5}   {COIN_FLIP_SPREAD}+ points apart",
            f"  Mean spread     {_n(r['mean_spread']):>5}   points between the lowest and highest rating of a file",
            f"  Per run         {'  '.join(_n(v) for v in r['run_scores'])}",
            f"  Agreement       {_n(agree, 2):>5}   rank correlation with the rule-based quality score (1 = same order)",
            "",
            f"  {'RATINGS':<22} {'MEAN':>5} {'RANGE':>6}  {'RULES':>5}  {'STATUS':<10} FILE",
        ]
        for f in r["files"]:
            dots = " ".join(str(x["overall"]) for x in f["ratings"])
            out.append(f"  {dots:<22} {f['mean']:>5.1f} {f['min']:>3}-{f['max']:<2}  {_rules(f['rules_score']):>5}  "
                       f"{f['status'].upper():<10} {f['file']}")
    if r.get("quality") and r["quality"].get("score") is not None:
        from coinflip.quality import one_line
        out += ["", f"  {one_line(r['quality'])}  (no AI)"]
    out += _failures(r)
    return "\n".join(out) + "\n"


def to_markdown(r: dict) -> str:
    """Markdown readability report."""
    out = [f"### coinflip readability: `{r.get('reviewer')}` - {r.get('project')}", "", f"**{headline(r)}**", ""]
    if r.get("readability") is not None:
        out += ["| Ratings per run | Mean | Range | Rule score | Status | File |", "|---|---:|---:|---:|---|---|"]
        out += [f"| {' '.join(str(x['overall']) for x in f['ratings'])} | {f['mean']:.1f} | {f['min']}-{f['max']} "
                f"| {_rules(f['rules_score'])} | {f['status']} | `{f['file']}` |" for f in r["files"]]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- HTML

READABILITY_CSS = """
table.strip{border-collapse:collapse;width:100%;font-size:14px}
.strip th,.strip td{padding:6px 8px;border-bottom:1px solid var(--border);text-align:left;vertical-align:middle}
.strip th{font-size:12px;color:var(--text-secondary);font-weight:600}
.strip td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.strip td.plot{width:46%;min-width:260px}
.strip .loc{font-family:ui-monospace,Consolas,monospace;font-size:12px}
svg.dots{width:100%;height:auto;display:block}
svg.dots .tick{stroke:var(--border);stroke-width:1}
svg.dots text{fill:var(--text-secondary);font-size:10px;font-family:inherit}
svg.dots .range{fill:var(--muted-bg)}
svg.dots .dot{fill:var(--hit);stroke:var(--surface-1);stroke-width:1.5}
svg.dots .mean{stroke:var(--text-primary);stroke-width:2;stroke-linecap:round}
.reasons{margin:6px 0 0;padding-left:18px;font-size:13px;color:var(--text-secondary)}
"""

W, PAD = 300, 12


def _x(value: float) -> float:
    return PAD + (W - 2 * PAD) * (value - 1) / 9


def _dots(row: dict) -> str:
    """One row of the strip plot: grey band min..max, a dot per run (stacked when equal), the mean."""
    H, mid, step = 62, 26, 9
    groups: dict[int, list[dict]] = {}
    for x in row["ratings"]:
        groups.setdefault(x["overall"], []).append(x)
    out = [f'<svg class="dots" viewBox="0 0 {W} {H}" role="img" aria-label="ratings '
           f'{", ".join(str(x["overall"]) for x in row["ratings"])} of 10">']
    out += [f'<line class="tick" x1="{_x(v):.1f}" x2="{_x(v):.1f}" y1="{mid + 18 if v not in (1, 5, 10) else 2}" '
            f'y2="{mid + 22}"/>' for v in range(1, 11)]
    out += [f'<text x="{_x(v):.1f}" y="{H - 2}" text-anchor="middle">{v}</text>' for v in (1, 5, 10)]
    lo, hi = _x(row["min"]), _x(row["max"])
    out.append(f'<rect class="range" x="{lo - 9:.1f}" y="{mid - 11}" width="{hi - lo + 18:.1f}" height="22" rx="11"/>')
    for value, items in sorted(groups.items()):
        per_column = 5
        columns = (len(items) + per_column - 1) // per_column
        for k, x in enumerate(items):
            col, rank = divmod(k, per_column)
            in_col = min(per_column, len(items) - col * per_column)
            cy = mid + (rank - (in_col - 1) / 2) * step
            cx = _x(value) + (col - (columns - 1) / 2) * step
            out.append(f'<circle class="dot" cx="{cx:.1f}" cy="{cy:.1f}" r="4.5"><title>Run {x["run"]}: '
                       f'{x["overall"]}/10 - {html.escape(x["reason"], quote=True)}</title></circle>')
    out.append(f'<line class="mean" x1="{_x(row["mean"]):.1f}" x2="{_x(row["mean"]):.1f}" y1="{mid - 16}" '
               f'y2="{mid + 16}"><title>mean {row["mean"]:.1f}</title></line>')
    out.append("</svg>")
    return "".join(out)


def _badge(status: str) -> str:
    label, icon, cls = STATUS[status]
    return f'<span class="badge {cls}"><span aria-hidden="true">{icon}</span>{html.escape(label)}</span>'


def _tile(value, label: str) -> str:
    return (f'<div class="tile"><div class="v">{html.escape(str(value))}</div>'
            f'<div class="l">{html.escape(label)}</div></div>')


def _criteria(row: dict) -> str:
    cells = "".join(
        f'<td class="num">{c["mean"]:.1f} <span class="loc">({c["min"]}-{c["max"]})</span></td>' if c else "<td>-</td>"
        for c in (row["criteria"][k] for k in CRITERIA))
    return cells


def body(r: dict) -> str:
    """The <main> content of a readability report."""
    e = html.escape
    out = [f'<header><p class="eyebrow">coinflip readability</p><h1>{e(str(r.get("project")))}</h1>',
           f'<p class="meta">{r["files_requested"]} files - reviewer {e(str(r.get("reviewer")))} - '
           f'{r["parsed_runs"]} runs - {e((r.get("started") or "")[:10])}</p></header>']
    if r.get("readability") is None:
        return "".join(out) + '<p class="sub">No run produced usable ratings.</p>'
    low, high = r["run_range"]
    out.append(f'<section class="card hero"><div class="hero-number">{r["readability"]:.1f}'
               f'<span style="font-size:24px;color:var(--text-secondary)">/10</span></div>'
               f'<p>average AI readability rating. A single run would have told you anything between '
               f'<strong>{low:.1f}</strong> and <strong>{high:.1f}</strong>; {r["coin_flips"]} of '
               f'{r["files_rated"]} files got ratings {COIN_FLIP_SPREAD} or more points apart.</p></section>')
    agree = r["agreement_with_rules"]
    out.append('<div class="tiles">' + "".join([
        _tile(r["consistent"], "consistent (within 1 point)"),
        _tile(r["wobbly"], "wobbly (2 points)"),
        _tile(r["coin_flips"], f"coin flips ({COIN_FLIP_SPREAD}+ points)"),
        _tile(_n(r["mean_spread"]), "mean spread per file"),
        _tile(_n(agree, 2), "agreement with the rule score"),
    ]) + "</div>")
    out.append('<h2>Every rating of every file</h2><p class="sub">One dot per run (1 = very hard to read, 10 = '
               'excellent), the grey band from lowest to highest rating, the line is the mean. "Rules" is the '
               'deterministic quality score of the file (0-100, no AI) for comparison.</p>')
    rows = "".join(
        f'<tr><td><span class="loc">{e(f["file"])}</span></td><td class="plot">{_dots(f)}</td>'
        f'<td class="num">{f["mean"]:.1f}</td><td class="num">{f["min"]}-{f["max"]}</td>'
        f'<td class="num">{_rules(f["rules_score"])}</td><td>{_badge(f["status"])}</td></tr>'
        for f in r["files"])
    out.append('<div class="card scroll"><table class="strip"><thead><tr><th>File</th><th>Ratings per run</th>'
               '<th>Mean</th><th>Range</th><th>Rules</th><th>Status</th></tr></thead>'
               f'<tbody>{rows}</tbody></table></div>')
    out.append('<h2>By criterion</h2><p class="sub">Mean rating, lowest and highest in brackets.</p>')
    head = "".join(f"<th>{c.capitalize()}</th>" for c in CRITERIA)
    crit = "".join(f'<tr><td><span class="loc">{e(f["file"])}</span></td>{_criteria(f)}</tr>' for f in r["files"])
    out.append(f'<div class="card scroll"><table class="strip"><thead><tr><th>File</th>{head}</tr></thead>'
               f'<tbody>{crit}</tbody></table></div>')
    out.append('<h2>What the runs said</h2><p class="sub">The one-sentence reason of every run - often the '
               'disagreement is visible right here.</p>')
    for f in r["files"]:
        reasons = "".join(f'<li><strong>{x["overall"]}/10</strong> (run {x["run"]}): {e(x["reason"])}</li>'
                          for x in f["ratings"])
        out.append(f'<details><summary>{e(f["file"])} - {f["min"]} to {f["max"]}</summary>'
                   f'<ul class="reasons">{reasons}</ul></details>')
    return "".join(out)
