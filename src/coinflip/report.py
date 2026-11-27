"""Human-readable reports. ASCII only, so they survive every terminal and CI log."""

from __future__ import annotations

import json

BAR_WIDTH = 10


def _bar(rate: float) -> str:
    filled = round(rate * BAR_WIDTH)
    return "#" * filled + "." * (BAR_WIDTH - filled)


def _p(value) -> str:
    return "n/a" if value is None else f"{value:.0f}%"


def _n(value, unit: str = "") -> str:
    return "n/a" if value is None else f"{value}{unit}"


def headline(r: dict) -> str:
    """The one sentence that sums up a result."""
    if r["mode"] == "readability":
        from coinflip.readability import headline as readability_headline
        return readability_headline(r)
    if r["mode"] == "review":
        if not r["answered_runs"]:
            return "no usable answers"
        if not r["distinct_findings"]:
            return "no findings in any run"
        noun = "finding" if r["distinct_findings"] == 1 else "findings"
        return (f"{r['distinct_findings']} distinct {noun} - {r['consistent']} consistent, "
                f"{r['coin_flips']} coin flips, {r['once']} seen once")
    if r.get("reliability") is None:
        return "no usable answers"
    return (f"reliability {_p(r['reliability'])} - {r['coin_flips']} of {r['planted_bugs']} "
            f"planted bugs are a coin flip")


def _failure_lines(r: dict) -> list[str]:
    f = r["failures"]
    if not (f["errors"] or f["unparseable"]):
        return []
    lines = ["", f"  ! {f['errors']} runs failed, {f['unparseable']} answers had no readable JSON"]
    lines += [f"    {m}" for m in f["messages"]]
    return lines


def _benchmark_text(r: dict) -> list[str]:
    rec = r["recall"]
    out = [
        f"  Reliability     {_p(r['reliability']):>5}   bugs found in >= 90% of runs ({r['reliable']} of {r['planted_bugs']})",
        f"  Coin flips      {r['coin_flips']:>5}   bugs found only sometimes",
        f"  Missed          {r['missed']:>5}   bugs found in <= 10% of runs",
        f"  Recall per run  {_p(rec['mean']):>5}   average (min {_p(rec['min'])}, max {_p(rec['max'])}),"
        f" all runs together {_p(rec['all_runs'])}",
        f"  Stability       {_p(r['stability']):>5}   overlap of found bugs between two runs",
        f"  Extra findings  {_n(r['extra_findings_per_run']):>5}   per run, not matching a planted bug",
        f"  Clean twin      {_n(r['clean_findings_per_run']):>5}   findings per run on the bug-free version",
        f"  Time            {_n(r['seconds_per_run'], 's'):>5}   per run",
        "",
        f"  {'':10}  {'HITS':>5}  {'STATUS':<10} {'WHERE':<22} BUG",
    ]
    for b in r["bugs"]:
        hits = f"{b['hits']}/{r['answered_runs']}"
        where = f"{b['file']}:{b['line']}"
        out.append(f"  {_bar(b['rate'])}  {hits:>5}  {b['status'].upper():<10} {where:<22} {b['title']}")
    return out


def _subject(r: dict) -> str:
    if r["mode"] == "benchmark":
        return r["benchmark"]
    scope = f"diff vs {r['diff']}" if r.get("diff") else "whole project"
    return f"review: {r['project']} ({scope}, {r['scope_files']} files)"


def _canary_text(c: dict, runs: int) -> list[str]:
    out = [
        "",
        f"  Canaries        {c['planted']:>5}   bugs planted into a temporary copy of your code",
        f"    Reliability   {_p(c['reliability']):>5}   canaries found in >= 90% of runs",
        f"    Coin flips    {c['coin_flips']:>5}   canaries found only sometimes ({c['missed']} never)",
        f"    Recall/run    {_p(c['recall_per_run']):>5}   all runs together {_p(c['recall_all_runs'])}",
        "    By kind       " + "  |  ".join(f"{k} {_p(v['found'])}" for k, v in c.get("by_kind", {}).items()),
    ]
    for k in c["canaries"]:
        where = f"{k['file']}:{k['line']}"
        out.append(f"    {_bar(k['rate'])}  {k['hits']}/{runs}  {k['status'].upper():<10} {where:<28} "
                   f"{k['kind']}: {k['original'][:40]}  ->  {k['mutated'][:40]}")
    return out


def _review_text(r: dict) -> list[str]:
    out = [
        f"  Consistent      {r['consistent']:>5}   reported in >= 90% of runs - act on these",
        f"  Coin flips      {r['coin_flips']:>5}   reported only sometimes - worth a human look",
        f"  Seen once       {r['once']:>5}   reported in a single run",
        f"  Stability       {_p(r['stability']):>5}   overlap of findings between two runs",
        f"  Time            {_n(r['seconds_per_run'], 's'):>5}   per run",
    ]
    if r.get("canary"):
        out += _canary_text(r["canary"], r["answered_runs"])
    if r["findings"]:
        out += ["", f"  {'CONFIDENCE':<10}  {'':>5}  {'STATUS':<10} {'SEVERITY':<8}  {'WHERE':<28} FINDING"]
    for f in r["findings"][:40]:
        hits = f"{f['hits']}/{r['answered_runs']}"
        where = f"{f['file']}:{f['line']}"
        out.append(f"  {_bar(f['rate'])}  {hits:>5}  {f['status'].upper():<10} {f['severity'] or '-':<8}  "
                   f"{where:<28} {f['title']}")
    if len(r["findings"]) > 40:
        out.append(f"  ... {len(r['findings']) - 40} more in results.json")
    return out


def to_text(r: dict) -> str:
    """Plain-text report of a scored session."""
    if r["mode"] == "readability":
        from coinflip.report_readability import to_text as readability_text
        return readability_text(r)
    out = [
        f"coinflip {r['coinflip']}  |  {_subject(r)}  |  reviewer: {r['reviewer']}  |  {r['runs']} runs",
        "",
        f"  >> {headline(r)}",
    ]
    if r["mode"] == "review" and r.get("single_run_coverage") is not None:
        out.append(f"     a single run showed on average {_p(r['single_run_coverage'])} of them")
    out.append("")
    if r["mode"] == "benchmark" and r.get("reliability") is not None:
        out += _benchmark_text(r)
    elif r["mode"] == "review" and r["answered_runs"] and r["distinct_findings"]:
        out += _review_text(r)
    elif r["mode"] == "review" and r.get("canary"):
        out += _canary_text(r["canary"], r["answered_runs"])
    if r["mode"] == "review" and r.get("quality"):
        from coinflip.quality import one_line
        out += ["", f"  {one_line(r['quality'])}  (no AI - details: coinflip quality)"]
    out += _failure_lines(r)
    return "\n".join(out) + "\n"


def to_markdown(r: dict) -> str:
    """Markdown report of a scored session, e.g. for a pull request comment."""
    if r["mode"] == "readability":
        from coinflip.report_readability import to_markdown as readability_markdown
        return readability_markdown(r)
    out = [f"### coinflip: `{r['reviewer']}` - {_subject(r)}", "", f"**{headline(r)}**", ""]
    if r["mode"] == "benchmark" and r.get("reliability") is not None:
        rec = r["recall"]
        out += [
            "| Reliability | Coin flips | Missed | Recall per run | All runs | Stability | Extra findings | Clean twin |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|",
            f"| {_p(r['reliability'])} | {r['coin_flips']} | {r['missed']} | {_p(rec['mean'])} | {_p(rec['all_runs'])} "
            f"| {_p(r['stability'])} | {_n(r['extra_findings_per_run'])} | {_n(r['clean_findings_per_run'])} |",
            "",
            "| Found | Status | Bug |",
            "|---:|---|---|",
        ]
        out += [f"| {b['hits']}/{r['answered_runs']} | {b['status']} | {b['title']} |" for b in r["bugs"]]
    elif r["mode"] == "review" and r["answered_runs"]:
        out += [f"A single run showed on average {_p(r['single_run_coverage'])} of these findings. "
                f"Stability between runs: {_p(r['stability'])}.", ""]
        if r.get("canary"):
            c = r["canary"]
            kinds = ", ".join(f"{k} {_p(v['found'])}" for k, v in c.get("by_kind", {}).items())
            out += [f"Canaries: {c['planted']} bugs planted into a copy of the code - reliably found: "
                    f"{_p(c['reliability'])}, coin flips: {c['coin_flips']}, never found: {c['missed']}. "
                    f"Found by kind: {kinds}.", ""]
        out += ["| Found | Status | Severity | Where | Finding |", "|---:|---|---|---|---|"]
        out += [f"| {f['hits']}/{r['answered_runs']} | {f['status']} | {f['severity'] or '-'} "
                f"| `{f['file']}:{f['line']}` | {f['title']} |" for f in r["findings"][:100]]
    if r["mode"] == "review" and r.get("quality") and r["quality"].get("score") is not None:
        from coinflip.quality import one_line
        out += ["", f"_{one_line(r['quality'])}_ (no AI)"]
    return "\n".join(out) + "\n"


def to_json(r: dict) -> str:
    return json.dumps(r, indent=2, ensure_ascii=False) + "\n"


def compare(results: list[dict], markdown: bool = False) -> str:
    """Leaderboard of several benchmark sessions."""
    rows = sorted(
        (r for r in results if r["mode"] == "benchmark" and r.get("reliability") is not None),
        key=lambda r: (-r["reliability"], -(r["recall"]["mean"] or 0), r["reviewer"]),
    )
    if not rows:
        return "no benchmark results to compare\n"
    head = ["Reviewer", "Runs", "Reliability", "Coin flips", "Recall/run", "All runs", "Stability",
            "Extra/run", "Clean twin", "s/run"]
    table = [
        [r["reviewer"], str(r["answered_runs"]), _p(r["reliability"]), str(r["coin_flips"]),
         _p(r["recall"]["mean"]), _p(r["recall"]["all_runs"]), _p(r["stability"]),
         _n(r["extra_findings_per_run"]), _n(r["clean_findings_per_run"]), _n(r["seconds_per_run"])]
        for r in rows
    ]
    bench = rows[0]["benchmark"]
    if markdown:
        lines = [f"### coinflip leaderboard - {bench}", "", "| " + " | ".join(head) + " |",
                 "|---|" + "---:|" * (len(head) - 1)]
        lines += ["| " + " | ".join(row) + " |" for row in table]
        return "\n".join(lines) + "\n"
    widths = [max(len(x) for x in col) for col in zip(head, *table)]
    fmt = "  ".join(f"{{:<{w}}}" if i == 0 else f"{{:>{w}}}" for i, w in enumerate(widths))
    lines = [f"coinflip leaderboard - {bench}", "", fmt.format(*head)]
    lines += [fmt.format(*row) for row in table]
    return "\n".join(lines) + "\n"
