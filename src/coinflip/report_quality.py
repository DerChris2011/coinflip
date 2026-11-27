"""Code quality output: text, markdown, an HTML section for the review report, a standalone HTML page."""

from __future__ import annotations

import html
import json

from coinflip.quality import (MAX_COMPLEXITY, MAX_FUNCTION_LINES, MAX_LINE_LENGTH, MAX_NESTING, MAX_PARAMETERS,
                              PART_NAMES)

EXPLAIN = {
    "complexity": f"share of function code with cyclomatic complexity <= {MAX_COMPLEXITY}",
    "size": f"share of function code in functions of <= {MAX_FUNCTION_LINES} lines",
    "duplication": "100 = no copy-paste, 0 = a quarter of all lines duplicated",
    "readability": f"nesting <= {MAX_NESTING}, <= {MAX_PARAMETERS} parameters, lines <= {MAX_LINE_LENGTH} chars, clear names",
    "documentation": "public functions with a docstring or doc comment",
    "hygiene": "commented-out code, debug leftovers, empty catch, suppressed warnings, TODOs",
}
ISSUE_LABELS = {
    "commented-out code": "commented-out code blocks",
    "debug leftover": "debug leftovers",
    "empty catch": "empty catch / except: pass",
    "suppressed warning": "suppressed warnings",
    "any type": "`any` types",
    "todo": "TODO / FIXME / HACK",
}


def _bar(value: float | None, width: int = 20) -> str:
    if value is None:
        return "." * width
    filled = round(value / 100 * width)
    return "#" * filled + "." * (width - filled)


def _v(value) -> str:
    return "n/a" if value is None else f"{value:.0f}"


def to_text(q: dict, title: str = "") -> str:
    """Plain-text quality report with the worst offenders."""
    head = f"coinflip quality  |  {title}  |  " if title else "coinflip quality  |  "
    out = [f"{head}{q['files']} files, {q['nloc']:,} lines of code, {q['functions']} functions", ""]
    if q["score"] is None:
        return "\n".join(out + ["  >> nothing to measure - no supported source files", ""])
    out.append(f"  >> code quality {q['score']:.0f}/100 - grade {q['grade']}")
    out.append("")
    for key, value in q["parts"].items():
        out.append(f"  {PART_NAMES[key]:<14} {_v(value):>4}  {_bar(value)}  {EXPLAIN[key]}")
    rp = q["readability_parts"]
    out.append(f"  {'':14}       nesting {_v(rp['nesting'])} | parameters {_v(rp['parameters'])} | "
               f"long lines {_v(rp['long_lines'])} | names {_v(rp['names'])}")
    out.append("")
    doc = f"{q['documented_functions']} of {q['public_functions']} public functions documented"
    tests = "n/a" if q["test_ratio"] is None else f"{q['test_ratio']:.2f} lines of test per line of code"
    out.append(f"  {doc}  |  {q['duplicated_percent']:.1f}% duplicated  |  tests: {tests}")
    issues = {k: v for k, v in q["hygiene_counts"].items() if v}
    if issues:
        out.append("  hygiene: " + ", ".join(f"{v} {ISSUE_LABELS.get(k, k)}" for k, v in issues.items()))
    off = q["offenders"]
    sections = [
        ("Most complex", off["complex"], lambda f: f"CCN {f['ccn']:<3} {f['nloc']:>4} lines"),
        ("Longest", off["long"], lambda f: f"{f['nloc']:>4} lines"),
        ("Deepest nesting", off["deeply_nested"], lambda f: f"depth {f['nesting']}"),
        ("Undocumented", off["undocumented"][:5], lambda f: f"{f['nloc']:>4} lines"),
    ]
    for name, rows, what in sections:
        if rows:
            out += ["", f"  {name}:"]
            out += [f"    {what(f):<16} {f['file']}:{f['line']}  {f['name']}" for f in rows[:5]]
    if off["commented_out"]:
        out += ["", "  Commented-out code:"]
        out += [f"    {c['lines']:>3} lines  {c['file']}:{c['line']}  {c['preview']}" for c in off["commented_out"][:5]]
    hard = [i for i in off["issues"] if i["kind"] != "todo"]
    if hard:
        out += ["", "  Leftovers:"]
        out += [f"    {i['kind']:<18} {i['file']}:{i['line']}  {i['text'][:70]}" for i in hard[:8]]
    if off["duplicated"]:
        out += ["", "  Duplicated blocks:"]
        out += [f"    {d['lines']:>3} lines  {d['file']}:{d['line']}  also in "
                + ", ".join(f"{a['file']}:{a['line']}" for a in d["also_in"]) for d in off["duplicated"][:5]]
    return "\n".join(out) + "\n"


def to_markdown(q: dict, title: str = "") -> str:
    """Quality summary as a markdown table."""
    if q["score"] is None:
        return "### coinflip quality\n\nnothing to measure\n"
    out = [f"### coinflip quality{': ' + title if title else ''} - **{q['score']:.0f}/100 ({q['grade']})**", "",
           "| Part | Score | Measures |", "|---|---:|---|"]
    out += [f"| {PART_NAMES[k]} | {_v(v)} | {EXPLAIN[k]} |" for k, v in q["parts"].items()]
    out += ["", f"{q['files']} files, {q['nloc']:,} lines of code, {q['functions']} functions, "
                f"{q['duplicated_percent']:.1f}% duplicated, {q['documented_functions']}/{q['public_functions']} "
                f"public functions documented."]
    return "\n".join(out) + "\n"


def to_json(q: dict) -> str:
    return json.dumps(q, indent=2, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------- HTML

QUALITY_CSS = """
.q-grid{display:grid;grid-template-columns:minmax(120px,170px) 1fr 44px;gap:8px 14px;align-items:center;margin-top:8px}
.q-name{font-weight:600}.q-explain{display:block;font-size:12px;color:var(--text-secondary);font-weight:400}
.q-track{position:relative;height:10px;background:var(--muted-bg);border-radius:5px;overflow:hidden}
.q-fill{position:absolute;left:0;top:0;bottom:0;background:var(--hit);border-radius:0 4px 4px 0}
.q-num{text-align:right;font-variant-numeric:tabular-nums;font-weight:650}
.q-grade{display:inline-flex;align-items:center;justify-content:center;width:64px;height:64px;border-radius:14px;
 font-size:36px;font-weight:700;background:var(--muted-bg)}
.q-list{width:100%;border-collapse:collapse;font-size:13px}
.q-list td,.q-list th{border-bottom:1px solid var(--border);padding:5px 8px;text-align:left;vertical-align:top}
.q-list th{color:var(--text-secondary);font-weight:600;font-size:12px}
.q-list td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.q-list code{font-family:ui-monospace,Consolas,monospace;font-size:12px}
"""


def _e(value) -> str:
    return html.escape(str(value), quote=True)


def _rows_table(head: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{_e(h)}</th>" for h in head)
    body = "".join("<tr>" + "".join(rows_cell for rows_cell in row) + "</tr>" for row in rows)
    return f'<table class="q-list"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'


def _loc(item: dict) -> str:
    return f'<td><code>{_e(item["file"])}:{item["line"]}</code></td>'


def _offender_details(q: dict) -> str:
    off = q["offenders"]
    blocks = []

    def fn_table(rows, label, metric):
        if rows:
            blocks.append(f'<details><summary>{_e(label)} ({len(rows)})</summary>' + _rows_table(
                ["Function", "Where", metric[0]],
                [[f'<td>{_e(f["name"])}</td>', _loc(f), f'<td class="n">{_e(metric[1](f))}</td>'] for f in rows]
            ) + "</details>")

    fn_table(off["complex"], f"Functions with complexity above {MAX_COMPLEXITY}", ("Complexity", lambda f: f["ccn"]))
    fn_table(off["long"], f"Functions longer than {MAX_FUNCTION_LINES} lines", ("Lines", lambda f: f["nloc"]))
    fn_table(off["deeply_nested"], f"Nesting deeper than {MAX_NESTING}", ("Depth", lambda f: f["nesting"]))
    fn_table(off["undocumented"], "Largest undocumented public functions", ("Lines", lambda f: f["nloc"]))
    if off["commented_out"]:
        blocks.append(f'<details><summary>Commented-out code ({len(off["commented_out"])})</summary>' + _rows_table(
            ["Starts with", "Where", "Lines"],
            [[f'<td><code>{_e(c["preview"])}</code></td>', _loc(c), f'<td class="n">{c["lines"]}</td>']
             for c in off["commented_out"]]) + "</details>")
    if off["issues"]:
        blocks.append(f'<details><summary>Leftovers and TODOs ({len(off["issues"])})</summary>' + _rows_table(
            ["Kind", "Where", "Line"],
            [[f'<td>{_e(i["kind"])}</td>', _loc(i), f'<td><code>{_e(i["text"])}</code></td>'] for i in off["issues"]]
        ) + "</details>")
    if off["duplicated"]:
        blocks.append(f'<details><summary>Duplicated blocks ({len(off["duplicated"])})</summary>' + _rows_table(
            ["Lines", "Where", "Also in"],
            [[f'<td class="n">{d["lines"]}</td>', _loc(d),
              "<td>" + ", ".join(f'<code>{_e(a["file"])}:{a["line"]}</code>' for a in d["also_in"]) + "</td>"]
             for d in off["duplicated"]]) + "</details>")
    if off["unclear_names"]:
        blocks.append(f'<details><summary>Unclear names ({len(off["unclear_names"])})</summary>' + _rows_table(
            ["Name", "Where", ""],
            [[f'<td><code>{_e(n["name"])}</code></td>', _loc(n), "<td></td>"] for n in off["unclear_names"]]
        ) + "</details>")
    return "".join(blocks)


def html_section(q: dict | None, heading: str = "Code quality") -> str:
    """The quality block of the review report (no AI involved - same code, same numbers)."""
    if not q or q.get("score") is None:
        return ""
    bars = "".join(
        f'<div class="q-name">{_e(PART_NAMES[k])}<span class="q-explain">{_e(EXPLAIN[k])}</span></div>'
        f'<div class="q-track" role="img" aria-label="{_e(PART_NAMES[k])} {_v(v)} of 100" '
        f'title="{_e(PART_NAMES[k])}: {_v(v)} / 100"><div class="q-fill" style="width:{v or 0:.0f}%"></div></div>'
        f'<div class="q-num">{_v(v)}</div>'
        for k, v in q["parts"].items()
    )
    tests = "n/a" if q["test_ratio"] is None else f"{q['test_ratio']:.2f}"
    return (
        f'<h2>{_e(heading)}</h2><p class="sub">Measured without AI on the files as they are - the same code always '
        'gets the same score, so it is a fair trend line between reviews. Test files only count for the test ratio.</p>'
        f'<section class="card hero"><span class="q-grade" aria-label="grade {_e(q["grade"])}">{_e(q["grade"])}</span>'
        f'<div class="hero-number">{q["score"]:.0f}<span style="font-size:24px;color:var(--text-secondary)">/100</span></div>'
        f'<p>{q["files"]} files, {q["nloc"]:,} lines of code, {q["functions"]} functions. '
        f'{q["documented_functions"]} of {q["public_functions"]} public functions documented, '
        f'{q["duplicated_percent"]:.1f}% duplicated, {q["commented_out_lines"]} lines of commented-out code, '
        f'test ratio {tests}.</p></section>'
        f'<div class="card" style="margin-top:12px"><div class="q-grid">{bars}</div></div>'
        + _offender_details(q)
    )


def render(q: dict, project: str, version: str = "") -> str:
    """Standalone page for `coinflip quality -f html`."""
    from coinflip.report_html import CSS
    body = (f'<header><p class="eyebrow">coinflip quality</p><h1>{_e(project)}</h1></header>'
            + (html_section(q, "Score") or '<p class="sub">No supported source files to measure.</p>'))
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>coinflip quality - {_e(project)}</title><style>{CSS}{QUALITY_CSS}</style></head>"
        f'<body><main class="viz-root">{body}<footer>Generated by coinflip {_e(version)} - '
        '<a href="https://github.com/DerChris2011/coinflip">github.com/DerChris2011/coinflip</a></footer>'
        "</main></body></html>\n"
    )
