"""A self-contained HTML report: one file, no external resources, works offline.

The centrepiece is the coin-flip matrix - one row per finding, one column per run,
a filled dot where that run reported the finding.
"""

from __future__ import annotations

import html
from pathlib import Path

from coinflip.report import headline
from coinflip.report_quality import QUALITY_CSS, html_section

MAX_ROWS = 60
MAX_RAW_CHARS = 20_000
STATUS = {  # label, icon, css class - status is never shown by colour alone
    "consistent": ("Consistent", "✓", "good"),
    "reliable": ("Reliable", "✓", "good"),
    "coin flip": ("Coin flip", "~", "warn"),
    "once": ("Seen once", "·", "muted"),
    "missed": ("Missed", "✕", "bad"),
}

CSS = """
.viz-root{color-scheme:light;--surface-0:#f7f7f7;--surface-1:#ffffff;--border:#e3e3e3;
 --text-primary:#191919;--text-secondary:#666666;--hit:#257de0;--miss:#d9d9d9;
 --good:#006c10;--good-bg:#e3f5e6;--warn:#8a5a00;--warn-bg:#fdf1d6;--bad:#b3261e;--bad-bg:#fbe5e4;
 --muted-bg:#efefef;--code-bg:#f3f3f3;--mark:#fff4c2}
@media (prefers-color-scheme: dark){:root:where(:not([data-theme="light"])) .viz-root{color-scheme:dark;
 --surface-0:#111111;--surface-1:#191919;--border:#303030;--text-primary:#efefef;--text-secondary:#9c9c9c;
 --hit:#338ced;--miss:#3a3a3a;--good:#5fd06f;--good-bg:#173a1d;--warn:#fbb315;--warn-bg:#3a2e10;
 --bad:#eb6263;--bad-bg:#3d1a1a;--muted-bg:#262626;--code-bg:#202020;--mark:#3a3416}}
*{box-sizing:border-box}
body{margin:0;background:var(--surface-0)}
.viz-root{font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--text-primary);
 background:var(--surface-0);max-width:1100px;margin:0 auto;padding:32px 24px 48px}
h1{font-size:28px;margin:4px 0 6px}h2{font-size:20px;margin:40px 0 6px}
.eyebrow{text-transform:uppercase;letter-spacing:.08em;font-size:12px;color:var(--text-secondary);margin:0}
.meta,.sub{color:var(--text-secondary);margin:0 0 12px}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:12px;padding:20px 24px}
.hero{display:flex;gap:32px;align-items:center;flex-wrap:wrap}
.hero-number{font-size:64px;font-weight:700;line-height:1;font-variant-numeric:tabular-nums}
.hero p{margin:6px 0 0;max-width:46ch;font-size:17px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-top:16px}
.tile{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:12px 14px}
.tile .v{font-size:24px;font-weight:650;font-variant-numeric:tabular-nums}
.tile .l{font-size:13px;color:var(--text-secondary)}
.scroll{overflow-x:auto}
table.matrix{border-collapse:separate;border-spacing:0;width:100%;font-size:14px}
.matrix th,.matrix td{padding:6px 8px;border-bottom:1px solid var(--border);text-align:left;white-space:nowrap}
.matrix th{font-weight:600;color:var(--text-secondary);font-size:12px}
.matrix td.label{white-space:normal;min-width:260px;max-width:460px}
.matrix .loc{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:var(--text-secondary)}
.matrix td.cell,.matrix th.cell{text-align:center;padding:6px 3px}
.dot{display:inline-block;width:14px;height:14px;border-radius:50%;vertical-align:middle}
.dot.hit{background:var(--hit)}.dot.miss{border:2px solid var(--miss)}
.matrix td.num{text-align:right;font-variant-numeric:tabular-nums}
.badge{display:inline-flex;gap:5px;align-items:center;border-radius:999px;padding:2px 10px;font-size:12px;font-weight:600}
.badge.good{color:var(--good);background:var(--good-bg)}.badge.warn{color:var(--warn);background:var(--warn-bg)}
.badge.bad{color:var(--bad);background:var(--bad-bg)}.badge.muted{color:var(--text-secondary);background:var(--muted-bg)}
.legend{display:flex;gap:18px;font-size:13px;color:var(--text-secondary);margin:10px 0 0;flex-wrap:wrap}
.legend span{display:inline-flex;gap:6px;align-items:center}
.finding{margin:12px 0}
.finding h3{font-size:15px;margin:0 0 6px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.finding .loc{font-family:ui-monospace,Consolas,monospace;font-size:13px;color:var(--text-secondary)}
pre{background:var(--code-bg);border-radius:8px;padding:10px 0;overflow-x:auto;font:12.5px/1.5 ui-monospace,Consolas,monospace;margin:8px 0 0}
pre .ln{display:block;padding:0 14px;white-space:pre}pre .ln.hl{background:var(--mark)}
pre .no{display:inline-block;width:4ch;color:var(--text-secondary);user-select:none}
pre.raw{padding:12px 14px;white-space:pre-wrap;max-height:420px}
details{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:10px 14px;margin:8px 0}
summary{cursor:pointer;font-weight:600}
footer{margin-top:40px;font-size:13px;color:var(--text-secondary)}
a{color:var(--hit)}
"""


def _e(value) -> str:
    return html.escape(str(value), quote=True)


def _pct(value) -> str:
    return "n/a" if value is None else f"{value:.0f}%"


def _seconds(value) -> str:
    if value is None:
        return "n/a"
    return "<1s" if value < 1 else f"{value:.0f}s"


def _badge(status: str) -> str:
    label, icon, cls = STATUS.get(status, (status, "", "muted"))
    return f'<span class="badge {cls}"><span aria-hidden="true">{icon}</span>{_e(label)}</span>'


def _tile(value, label: str) -> str:
    return f'<div class="tile"><div class="v">{_e(value)}</div><div class="l">{_e(label)}</div></div>'


def _matrix(rows: list[dict], runs: list[int], title_of, extra_of=None) -> str:
    head = "".join(f'<th class="cell" scope="col">{i}</th>' for i in runs)
    out = ['<div class="scroll"><table class="matrix"><thead><tr><th scope="col">Finding</th>',
           f'{head}<th scope="col" class="num">Found</th><th scope="col">Status</th></tr></thead><tbody>']
    for row in rows[:MAX_ROWS]:
        hit = set(row.get("runs", []))
        name = title_of(row)
        cells = "".join(
            f'<td class="cell" title="Run {i}: {"reported" if i in hit else "not reported"} - {_e(name)}">'
            f'<span class="dot {"hit" if i in hit else "miss"}" role="img" '
            f'aria-label="run {i} {"reported" if i in hit else "not reported"}"></span></td>'
            for i in runs
        )
        loc = f'{row["file"]}:{row["line"]}'
        extra = extra_of(row) if extra_of else ""
        out.append(
            f'<tr><td class="label">{_e(name)}{extra}<div class="loc">{_e(loc)}</div></td>{cells}'
            f'<td class="num">{row["hits"]}/{len(runs)}</td><td>{_badge(row["status"])}</td></tr>'
        )
    out.append("</tbody></table></div>")
    if len(rows) > MAX_ROWS:
        out.append(f'<p class="sub">{len(rows) - MAX_ROWS} more rows in results.json</p>')
    out.append('<div class="legend"><span><span class="dot hit"></span>reported in this run</span>'
               '<span><span class="dot miss"></span>not reported</span></div>')
    return "".join(out)


def _snippet(row: dict) -> str:
    lines = row.get("snippet")
    if not lines:
        return ""
    body = "".join(
        f'<span class="ln{" hl" if n == row["line"] else ""}"><span class="no">{n}</span>{_e(text)}</span>'
        for n, text in lines
    )
    return f"<pre>{body}</pre>"


def _findings(rows: list[dict], runs: int) -> str:
    out = []
    for row in rows[:MAX_ROWS]:
        if row["status"] == "once" and row.get("severity") not in ("critical", "high"):
            continue
        sev = f' <span class="badge muted">{_e(row["severity"])}</span>' if row.get("severity") else ""
        out.append(
            f'<div class="finding card"><h3>{_badge(row["status"])}{sev}<span>{_e(row["title"])}</span></h3>'
            f'<div class="loc">{_e(row["file"])}:{row["line"]} - reported in {row["hits"]} of {runs} runs</div>'
            f"{_snippet(row)}</div>"
        )
    return "".join(out) or '<p class="sub">No findings to show.</p>'


def _raw_answers(result: dict, session: Path | None) -> str:
    if session is None:
        return ""
    out = ['<h2>What each run said</h2><p class="sub">The unedited answers, so every number above can be checked.</p>']
    for rec in result.get("run_records", []):
        path = session / rec["file"]
        text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        if len(text) > MAX_RAW_CHARS:
            text = text[:MAX_RAW_CHARS] + "\n... (truncated)"
        status = f'failed: {rec["error"]}' if rec["error"] else f'{rec["seconds"]:.0f}s'
        out.append(f'<details><summary>{_e(rec["variant"])} run {rec["index"]} - {_e(status)}</summary>'
                   f'<pre class="raw">{_e(text)}</pre></details>')
    return "".join(out)


def _review_body(r: dict) -> str:
    runs = r.get("run_indexes", [])
    scope = f"changes vs {r['diff']}" if r.get("diff") else "whole project"
    out = [
        f'<header><p class="eyebrow">coinflip review</p><h1>{_e(r["project"])}</h1>',
        f'<p class="meta">{_e(scope)} - {r["scope_files"]} files - reviewer {_e(r["reviewer"])} - '
        f'{r["answered_runs"]} runs - {_e((r.get("started") or "")[:10])}</p></header>',
    ]
    if r.get("single_run_coverage") is not None:
        out.append(
            f'<section class="card hero"><div class="hero-number">{_pct(r["single_run_coverage"])}</div>'
            f'<p>of all findings were shown by a <strong>single</strong> review on average. '
            f'{_e(headline(r))}.</p></section>'
        )
    out.append('<div class="tiles">' + "".join([
        _tile(r.get("distinct_findings", 0), "distinct findings"),
        _tile(r.get("consistent", 0), "consistent (>= 90% of runs)"),
        _tile(r.get("coin_flips", 0), "coin flips"),
        _tile(r.get("once", 0), "seen once"),
        _tile(_pct(r.get("stability")), "stability between runs"),
        _tile(_seconds(r.get("seconds_per_run")), "per run"),
    ]) + "</div>")
    if r.get("findings"):
        out.append('<h2>The coin-flip matrix</h2><p class="sub">Each row is a finding, each column one review '
                   'of the same code. A filled dot means that run reported it.</p>')
        out.append(_matrix(r["findings"], runs, lambda row: row["title"]))
    canary = r.get("canary")
    if canary:
        out.append(f'<h2>Canaries</h2><p class="sub">{canary["planted"]} small bugs were planted into a temporary '
                   f'copy of the code. Found reliably: {_pct(canary["reliability"])}, coin flips: '
                   f'{canary["coin_flips"]}, never found: {canary["missed"]}. Your files were not changed.</p>')
        kinds = canary.get("by_kind", {})
        if kinds:
            out.append('<div class="tiles">' + "".join(
                _tile(_pct(v["found"]), f'{k} ({v["planted"]} planted)') for k, v in kinds.items()) + "</div>")
            out.append('<p class="sub">Share of runs in which a planted bug of this kind was found. '
                       'A low value is a blind spot of the reviewer on this code - cover it with tests.</p>')
        out.append(_matrix(canary["canaries"], runs, lambda row: f'{row["kind"]}: {row["original"]} -> {row["mutated"]}'))
    if r.get("findings"):
        out.append("<h2>Findings</h2>" + _findings(r["findings"], len(runs)))
    out.append(html_section(r.get("quality")))
    return "".join(out)


def _benchmark_body(r: dict) -> str:
    runs = r.get("run_indexes", [])
    rec = r.get("recall") or {}
    out = [
        f'<header><p class="eyebrow">coinflip benchmark</p><h1>{_e(r["benchmark"])}</h1>',
        f'<p class="meta">reviewer {_e(r["reviewer"])} - {r["answered_runs"]} runs - '
        f'{_e((r.get("started") or "")[:10])}</p></header>',
    ]
    if r.get("reliability") is not None:
        out.append(
            f'<section class="card hero"><div class="hero-number">{_pct(r["reliability"])}</div>'
            f'<p>of the {r["planted_bugs"]} planted bugs were found in at least 90% of runs. '
            f'{_e(headline(r))}.</p></section>'
        )
        out.append('<div class="tiles">' + "".join([
            _tile(r["coin_flips"], "coin flips"),
            _tile(r["missed"], "missed"),
            _tile(_pct(rec.get("mean")), "recall per run"),
            _tile(_pct(rec.get("all_runs")), "all runs together"),
            _tile(_pct(r.get("stability")), "stability between runs"),
            _tile(r.get("clean_findings_per_run") if r.get("clean_findings_per_run") is not None else "n/a",
                  "findings per run on clean code"),
        ]) + "</div>")
        out.append('<h2>The coin-flip matrix</h2><p class="sub">Each row is a planted bug, each column one run. '
                   'A filled dot means that run found it.</p>')
        out.append(_matrix(r["bugs"], runs, lambda row: row["title"],
                           lambda row: f' <span class="badge muted">{_e(row["cwe"])}</span>' if row.get("cwe") else ""))
    return "".join(out)


def render(result: dict, session: Path | None = None) -> str:
    """Self-contained HTML report of a scored session."""
    body = _benchmark_body(result) if result["mode"] == "benchmark" else _review_body(result)
    if result.get("answered_runs", 0) == 0:
        body += '<p class="sub">No run produced a usable answer.</p>'
    title = result.get("project") or result.get("benchmark") or "report"
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>coinflip - {_e(title)}</title><style>{CSS}{QUALITY_CSS}</style></head>"
        f'<body><main class="viz-root">{body}{_raw_answers(result, session)}'
        f'<footer>Generated by coinflip {_e(result.get("coinflip", ""))} - counts are computed from the raw '
        'answers above. <a href="https://github.com/DerChris2011/coinflip">github.com/DerChris2011/coinflip</a>'
        "</footer></main></body></html>\n"
    )
