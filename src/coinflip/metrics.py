"""Reliability metrics over repeated runs of the same reviewer on the same code.

Per planted bug: detection rate = runs that found it / runs with an answer.
  reliable   found in >= 90 % of runs
  coin flip  found in between
  missed     found in <= 10 % of runs
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from statistics import mean

from coinflip.benchmark import Benchmark
from coinflip.findings import Finding

RELIABLE_RATE = 0.9
MISSED_RATE = 0.1
CLUSTER_LINES = 3
CANARY_LINES = 2  # a mutation is one line - a finding must point at it closely to count


@dataclass
class Run:
    variant: str  # "buggy", "clean" or "target"
    index: int
    seconds: float
    error: str | None
    parsed: bool = False
    findings: list[Finding] = field(default_factory=list)
    hits: set[str] = field(default_factory=set)
    unmatched: list[Finding] = field(default_factory=list)

    @property
    def answered(self) -> bool:
        return self.error is None


def status(rate: float) -> str:
    if rate >= RELIABLE_RATE:
        return "reliable"
    if rate <= MISSED_RATE:
        return "missed"
    return "coin flip"


def jaccard_mean(sets: list[set]) -> float | None:
    if len(sets) < 2:
        return None
    scores = [len(a & b) / len(a | b) if a | b else 1.0 for a, b in combinations(sets, 2)]
    return mean(scores)


def _pct(value: float | None) -> float | None:
    return None if value is None else round(100 * value, 1)


def _avg(values: list[float]) -> float | None:
    return round(mean(values), 1) if values else None


def _failures(runs: list[Run]) -> dict:
    return {
        "errors": sum(1 for r in runs if not r.answered),
        "unparseable": sum(1 for r in runs if r.answered and not r.parsed),
        "messages": sorted({r.error for r in runs if r.error})[:5],
    }


def _bug_rows(bench: Benchmark, answered: list[Run]) -> list[dict]:
    rows = []
    for bug in bench.bugs:
        hits = sum(1 for r in answered if bug.id in r.hits)
        rate = hits / len(answered)
        rows.append(
            {
                "id": bug.id,
                "title": bug.title,
                "kind": bug.kind,
                "cwe": bug.cwe,
                "file": bug.file,
                "line": bug.start_line,
                "hits": hits,
                "rate": round(rate, 3),
                "status": status(rate),
                "runs": sorted(r.index for r in answered if bug.id in r.hits),
            }
        )
    rows.sort(key=lambda r: (-r["rate"], r["id"]))
    return rows


def summarize_benchmark(bench: Benchmark, buggy: list[Run], clean: list[Run]) -> dict:
    answered = [r for r in buggy if r.answered]
    clean_answered = [r for r in clean if r.answered]
    base = {
        "mode": "benchmark",
        "planted_bugs": len(bench.bugs),
        "runs": len(buggy),
        "answered_runs": len(answered),
        "clean_runs": len(clean),
        "failures": _failures(buggy + clean),
        "seconds_per_run": _avg([r.seconds for r in answered + clean_answered]),
    }
    if not answered:
        return {**base, "reliability": None, "bugs": []}

    total = len(bench.bugs)
    rows = _bug_rows(bench, answered)
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("reliable", "coin flip", "missed")}
    recalls = [len(r.hits) / total for r in answered]
    union = set().union(*(r.hits for r in answered))
    return {
        **base,
        "reliability": _pct(counts["reliable"] / total),
        "reliable": counts["reliable"],
        "coin_flips": counts["coin flip"],
        "missed": counts["missed"],
        "recall": {
            "mean": _pct(mean(recalls)),
            "min": _pct(min(recalls)),
            "max": _pct(max(recalls)),
            "all_runs": _pct(len(union) / total),
        },
        "stability": _pct(jaccard_mean([r.hits for r in answered])),
        "extra_findings_per_run": _avg([len(r.unmatched) for r in answered]),
        "clean_findings_per_run": _avg([len(r.findings) for r in clean_answered]),
        "run_indexes": [r.index for r in answered],
        "bugs": rows,
    }


SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}


def _cluster(runs: list[Run]) -> list[dict]:
    """Group findings from different runs that point at (nearly) the same place."""
    items = sorted(
        ((f.file, f.line, r.index, f) for r in runs for f in r.findings),
        key=lambda t: (t[0], t[1], t[2]),
    )
    clusters: list[dict] = []
    for file, line, run_index, finding in items:
        last = clusters[-1] if clusters else None
        if last and last["file"] == file and line - last["last_line"] <= CLUSTER_LINES:
            last["runs"].add(run_index)
            last["last_line"] = line
            last["severities"].append(finding.severity)
        else:
            clusters.append({"file": file, "line": line, "last_line": line, "runs": {run_index},
                             "title": finding.title, "severities": [finding.severity]})
    return clusters


def _review_status(hits: int, runs: int) -> str:
    if hits == 1 and runs >= 3:
        return "once"
    return {"reliable": "consistent", "coin flip": "coin flip", "missed": "once"}[status(hits / runs)]


def split_canary_findings(run: Run, canaries: list[dict]) -> None:
    """Move findings that point at a planted canary out of the run's real findings."""
    real = []
    for f in run.findings:
        near = [c for c in canaries if c["file"] == f.file and abs(c["line"] - f.line) <= CANARY_LINES]
        if near:
            run.hits.add(min(near, key=lambda c: abs(c["line"] - f.line))["id"])
        else:
            real.append(f)
    run.findings = real


def _canary_summary(answered: list[Run], canaries: list[dict]) -> dict | None:
    if not canaries:
        return None
    rows = []
    for c in canaries:
        hits = sum(1 for r in answered if c["id"] in r.hits)
        rows.append({**c, "hits": hits, "rate": round(hits / len(answered), 3), "status": status(hits / len(answered)),
                     "runs": sorted(r.index for r in answered if c["id"] in r.hits)})
    recalls = [len(r.hits) / len(canaries) for r in answered]
    by_kind: dict[str, dict] = {}
    for row in rows:
        entry = by_kind.setdefault(row["kind"], {"planted": 0, "rates": []})
        entry["planted"] += 1
        entry["rates"].append(row["rate"])
    kinds = {k: {"planted": v["planted"], "found": _pct(mean(v["rates"]))} for k, v in sorted(by_kind.items())}
    return {
        "planted": len(canaries),
        "by_kind": kinds,
        "reliability": _pct(sum(1 for r in rows if r["status"] == "reliable") / len(canaries)),
        "coin_flips": sum(1 for r in rows if r["status"] == "coin flip"),
        "missed": sum(1 for r in rows if r["status"] == "missed"),
        "recall_per_run": _pct(mean(recalls)),
        "recall_all_runs": _pct(len(set().union(*(r.hits for r in answered))) / len(canaries)),
        "canaries": sorted(rows, key=lambda r: (-r["rate"], r["file"], r["line"])),
    }


def summarize_review(runs: list[Run], canaries: list[dict]) -> dict:
    """Confidence per finding on the user's own code, plus canary reliability if planted."""
    answered = [r for r in runs if r.answered]
    base = {
        "mode": "review",
        "runs": len(runs),
        "answered_runs": len(answered),
        "failures": _failures(runs),
        "seconds_per_run": _avg([r.seconds for r in answered]),
    }
    if not answered:
        return {**base, "stability": None, "findings": [], "canary": None}
    for r in answered:
        split_canary_findings(r, canaries)
    clusters = _cluster(answered)
    per_run = [{i for i, c in enumerate(clusters) if r.index in c["runs"]} for r in answered]
    rows = []
    for c in clusters:
        severity = max(c["severities"], key=lambda s: SEVERITY_ORDER.get(s, 0))
        rows.append({"file": c["file"], "line": c["line"], "severity": severity, "title": c["title"],
                     "hits": len(c["runs"]), "rate": round(len(c["runs"]) / len(answered), 3),
                     "status": _review_status(len(c["runs"]), len(answered)), "runs": sorted(c["runs"])})
    rows.sort(key=lambda r: (-r["rate"], -SEVERITY_ORDER.get(r["severity"], 0), r["file"], r["line"]))
    coverage = [len(s) / len(clusters) for s in per_run] if clusters else []
    return {
        **base,
        "distinct_findings": len(rows),
        "consistent": sum(1 for r in rows if r["status"] == "consistent"),
        "coin_flips": sum(1 for r in rows if r["status"] == "coin flip"),
        "once": sum(1 for r in rows if r["status"] == "once"),
        "findings_per_run": _avg([len(s) for s in per_run]),
        "single_run_coverage": _pct(mean(coverage)) if coverage else None,
        "stability": _pct(jaccard_mean(per_run)),
        "run_indexes": [r.index for r in answered],
        "findings": rows,
        "canary": _canary_summary(answered, canaries),
    }
