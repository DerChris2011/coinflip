"""AI readability rating with spread - the coin-flip idea applied to "how readable is this code?".

The deterministic quality score (quality.py) counts what rules can count. Whether code is easy
to *understand* is a judgement - so ask the AI, several times, and show how much its verdict
wobbles. A file rated 4 in one run and 8 in the next has no reliable readability rating.
"""

from __future__ import annotations

import json
import math
import re
from statistics import mean, pstdev

from coinflip import prompt as prompts
from coinflip.canary import _is_test_file
from coinflip.findings import _FENCE, _normalise_path

CRITERIA = ("naming", "structure", "comments", "overall")
DEFAULT_FILES = 8
MAX_FILES = 30
CONSISTENT_SPREAD = 1  # max - min of the overall rating across runs
COIN_FLIP_SPREAD = 3

TASK = """\
You are a senior engineer judging how easy code is to read and maintain for a new team member.
Rate the readability of each file listed below. Judge readability only - not correctness,
performance or security.

Score each criterion from 1 (very poor) to 10 (excellent):
  naming:    are names clear, accurate and consistent
  structure: are functions small and focused, is the control flow easy to follow
  comments:  do comments and docs explain the why where it is needed (neither missing nor noise)
  overall:   how easy is the file to understand and change safely
"""

OUTPUT = """\
Answer with ONLY a JSON array inside a ```json fenced block, nothing else - one object per file:
  {"file": "<path as listed>", "naming": 1-10, "structure": 1-10, "comments": 1-10, "overall": 1-10,
   "reason": "one short sentence: the main thing that helps or hurts readability"}
"""


# ---------------------------------------------------------------- which files, which prompt

def select_files(root, files: list[str], count: int) -> list[str]:
    """The `count` largest non-test source files (by lines), in path order - deterministic."""
    sized = []
    for rel in files:
        if _is_test_file(rel):
            continue
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.split("\n")
        if max((len(line) for line in lines), default=0) > 2000:
            continue  # minified or generated
        code = sum(1 for line in lines if line.strip())
        if code >= 5:
            sized.append((-code, rel))
    return sorted(rel for _, rel in sorted(sized)[:count])


def agent_prompt(files: list[str]) -> str:
    """Prompt for agentic CLIs: they read the files in their working directory themselves."""
    return "\n".join([TASK, "The project is in your current working directory. Read these files "
                      "(other files only for context). Do not modify any file.", "",
                      *[f"- {f}" for f in files], "", OUTPUT])


def inline_prompt(contents: dict[str, str]) -> str:
    """Prompt with the code inside it, for reviewers that cannot read files."""
    return "\n".join([TASK, "Rate these files:", *[f"- {f}" for f in sorted(contents)], "", OUTPUT,
                      "The code:", "", *prompts._numbered(contents)])


# ---------------------------------------------------------------- parsing answers

def _score(value) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    match = re.match(r"\s*(\d+(?:\.\d+)?)", str(value))
    if not match:
        return None
    number = float(match.group(1))
    return int(round(number)) if 1 <= number <= 10 else None


def _load(text: str) -> list | None:
    blocks = [m.group(1) for m in _FENCE.finditer(text)][::-1]
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        blocks.append(text[start:end + 1])
    for block in blocks:
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            data = data.get("files", data.get("ratings"))
        if isinstance(data, list):
            return data
    return None


def parse_ratings(text: str, expected: list[str]) -> tuple[dict[str, dict], bool]:
    """file -> {naming, structure, comments, overall, reason} for the expected files only."""
    data = _load(text)
    if data is None:
        return {}, False
    ratings: dict[str, dict] = {}
    for item in data:
        if not isinstance(item, dict) or "file" not in item:
            continue
        rel = _normalise_path(str(item["file"]), expected)
        overall = _score(item.get("overall"))
        if rel not in expected or overall is None or rel in ratings:
            continue
        ratings[rel] = {c: _score(item.get(c)) for c in CRITERIA}
        ratings[rel]["reason"] = str(item.get("reason", ""))[:300]
    return ratings, True


# ---------------------------------------------------------------- scoring

def status(spread: int) -> str:
    """How far the runs disagreed on one file's overall rating."""
    if spread <= CONSISTENT_SPREAD:
        return "consistent"
    return "coin flip" if spread >= COIN_FLIP_SPREAD else "wobbly"


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(a: list[float], b: list[float]) -> float | None:
    """Rank correlation (-1..1); None for fewer than 4 pairs or no variation."""
    if len(a) < 4:
        return None
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = mean(ra), mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return round(num / den, 2) if den else None


def _file_row(rel: str, per_run: list[tuple[int, dict]], rules: float | None) -> dict:
    overall = [r["overall"] for _, r in per_run]
    criteria = {}
    for c in CRITERIA:
        values = [r[c] for _, r in per_run if r.get(c) is not None]
        criteria[c] = ({"mean": round(mean(values), 1), "min": min(values), "max": max(values)}
                       if values else None)
    spread = max(overall) - min(overall)
    return {
        "file": rel,
        "ratings": [{"run": i, "overall": r["overall"], "reason": r.get("reason", "")} for i, r in per_run],
        "mean": round(mean(overall), 1),
        "min": min(overall),
        "max": max(overall),
        "spread": spread,
        "stdev": round(pstdev(overall), 2),
        "status": status(spread),
        "criteria": criteria,
        "rules_score": rules,
    }


def summarize(runs: list[dict], files: list[str], file_quality: dict[str, float | None]) -> dict:
    """runs: [{"index", "seconds", "error", "parsed", "ratings": {file: {...}}}]."""
    answered = [r for r in runs if r["error"] is None]
    parsed = [r for r in answered if r["parsed"]]
    rows = []
    for rel in files:
        per_run = [(r["index"], r["ratings"][rel]) for r in parsed if rel in r["ratings"]]
        if per_run:
            rows.append(_file_row(rel, per_run, file_quality.get(rel)))
    rows.sort(key=lambda r: (-r["spread"], r["mean"], r["file"]))
    per_run_project = [round(mean(r["ratings"][f]["overall"] for f in r["ratings"]), 1)
                       for r in parsed if r["ratings"]]
    with_rules = [r for r in rows if r["rules_score"] is not None]
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("consistent", "wobbly", "coin flip")}
    rated_cells = sum(len(r["ratings"]) for r in parsed)
    return {
        "mode": "readability",
        "runs": len(runs),
        "answered_runs": len(answered),
        "parsed_runs": len(parsed),
        "run_indexes": [r["index"] for r in parsed],
        "files_requested": len(files),
        "files_rated": len(rows),
        "coverage": round(100.0 * rated_cells / (len(files) * len(parsed)), 1) if files and parsed else None,
        "readability": round(mean(r["mean"] for r in rows), 1) if rows else None,
        "run_scores": per_run_project,
        "run_range": [min(per_run_project), max(per_run_project)] if per_run_project else None,
        "mean_spread": round(mean(r["spread"] for r in rows), 1) if rows else None,
        "consistent": counts["consistent"],
        "wobbly": counts["wobbly"],
        "coin_flips": counts["coin flip"],
        "agreement_with_rules": spearman([r["mean"] for r in with_rules], [r["rules_score"] for r in with_rules]),
        "seconds_per_run": round(mean(r["seconds"] for r in answered), 1) if answered else None,
        "failures": {
            "errors": len(runs) - len(answered),
            "unparseable": len(answered) - len(parsed),
            "messages": sorted({r["error"].splitlines()[-1][:160] for r in runs if r["error"]})[:5],
        },
        "files": rows,
    }


def headline(r: dict) -> str:
    """The one sentence that sums up a readability session."""
    if r.get("readability") is None:
        return "no usable ratings"
    low, high = r["run_range"]
    verb = "is" if r["coin_flips"] == 1 else "are"
    spread = f" - single runs said anything from {low:.1f} to {high:.1f}" if high - low >= 0.1 else ""
    return (f"readability {r['readability']:.1f}/10{spread}; {r['coin_flips']} of {r['files_rated']} files {verb} "
            f"a coin flip (ratings {COIN_FLIP_SPREAD}+ points apart)")
