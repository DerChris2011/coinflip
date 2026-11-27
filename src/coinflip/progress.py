"""Compare review sessions of one project over time: review -> fix -> review.

Counts per session are exact (they come from each session's own scoring). Following a
single finding from one review to the next is approximate: after a fix, line numbers
shift and every run words its titles differently. Findings are therefore linked by file,
line distance and title similarity, and the report says so.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from coinflip import session

SEVERITIES = ("critical", "high", "medium", "low")
_STOP = frozenset(
    "the and for with that this from into when then than are was were not can may will its has have "
    "but any all one two use used uses using without only also more less".split()
)


class ProgressError(Exception):
    pass


def local_time(started: str | None) -> str:
    """Sessions store UTC; people think in their own time zone."""
    if not started:
        return ""
    try:
        return datetime.fromisoformat(started).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return started[:16].replace("T", " ")


# ---------------------------------------------------------------- which sessions belong to a project

def _belongs(meta: dict, project: Path) -> bool:
    if meta.get("mode") != "review":
        return False
    if meta.get("project_path"):
        return Path(meta["project_path"]) == project
    return meta.get("project") == project.name  # sessions from coinflip <= 0.2.2


def project_sessions(sessions_dir: Path, project: Path) -> list[Path]:
    """Review session folders of this project, oldest first."""
    project = project.resolve()
    if not sessions_dir.is_dir():
        return []
    found = []
    for folder in sessions_dir.iterdir():
        try:
            meta = session.load_meta(folder)
        except (FileNotFoundError, ValueError, OSError):
            continue
        if _belongs(meta, project) and any(r.get("error") is None for r in meta.get("runs", [])):
            found.append((meta.get("started") or "", folder.name, folder))
    return [f for _, _, f in sorted(found)]


def describe(folder: Path, number: int) -> dict:
    """One line of `coinflip sessions` - scored fresh, so old sessions get today's rules."""
    r = session.score(folder)
    meta = session.load_meta(folder)
    return {
        "number": number,
        "session": folder.name,
        "started": r.get("started"),
        "commit": meta.get("commit"),
        "dirty": meta.get("dirty"),
        "scope": f"diff vs {r['diff']}" if r.get("diff") else "whole project",
        "reviewer": r.get("reviewer"),
        "runs": r.get("answered_runs", 0),
        "canaries": len(meta.get("canaries") or []),
        "distinct_findings": r.get("distinct_findings", 0),
        "consistent": r.get("consistent", 0),
        "coin_flips": r.get("coin_flips", 0),
        "once": r.get("once", 0),
    }


# ---------------------------------------------------------------- linking findings across sessions

def _words(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9_]+", title.lower()) if len(w) >= 3 and w not in _STOP}


def _similarity(a: dict, b: dict) -> float | None:
    """Score how likely two findings of different reviews are the same problem (None = not at all)."""
    if a["file"] != b["file"]:
        return None
    wa, wb = _words(a["title"]), _words(b["title"])
    sim = len(wa & wb) / len(wa | wb) if wa | wb else 0.0
    distance = abs(a["line"] - b["line"])
    if distance <= 3 or sim >= 0.4 or (sim >= 0.2 and distance <= 20):
        return sim + (1.0 if distance <= 3 else 0.5 if distance <= 20 else 0.0)
    return None


def _link(chains: list[dict], rows: list[dict], index: int) -> None:
    """Attach the findings of session `index` to existing chains (greedy, best pairs first)."""
    pairs = []
    for ci, chain in enumerate(chains):
        last = chain["last"]
        for ri, row in enumerate(rows):
            score = _similarity(last, row)
            if score is not None:
                pairs.append((score, ci, ri))
    used_chains, used_rows = set(), set()
    for score, ci, ri in sorted(pairs, key=lambda p: (-p[0], p[1], p[2])):
        if ci in used_chains or ri in used_rows:
            continue
        used_chains.add(ci)
        used_rows.add(ri)
        chains[ci]["cells"][index] = rows[ri]
        chains[ci]["last"] = rows[ri]
    for ri, row in enumerate(rows):
        if ri not in used_rows:
            chains.append({"cells": {index: row}, "last": row, "first_seen": index})


def _fate(cells: dict, last_index: int) -> str:
    first, last = 0 in cells, last_index in cells
    if first and last:
        return "still there"
    if first:
        return "fixed"
    if last:
        return "new"
    return "came and went"


# ---------------------------------------------------------------- the comparison

def _session_stats(result: dict, meta: dict, folder: Path) -> dict:
    severities = {s: 0 for s in (*SEVERITIES, "other")}
    files: dict[str, int] = {}
    for f in result.get("findings", []):
        severities[f["severity"] if f["severity"] in SEVERITIES else "other"] += 1
        files[f["file"]] = files.get(f["file"], 0) + 1
    return {
        "session": folder.name,
        "started": result.get("started"),
        "commit": meta.get("commit"),
        "dirty": meta.get("dirty"),
        "scope": f"diff vs {result['diff']}" if result.get("diff") else "whole project",
        "reviewer": result.get("reviewer"),
        "runs": result.get("answered_runs", 0),
        "canaries": len(meta.get("canaries") or []),
        "distinct_findings": result.get("distinct_findings", 0),
        "consistent": result.get("consistent", 0),
        "coin_flips": result.get("coin_flips", 0),
        "once": result.get("once", 0),
        "single_run_coverage": result.get("single_run_coverage"),
        "stability": result.get("stability"),
        "severity": severities,
        "files": files,
        "quality": _quality_stats(meta.get("quality")),
    }


def _quality_stats(q: dict | None) -> dict | None:
    if not q or q.get("score") is None:
        return None
    return {"score": q["score"], "grade": q.get("grade"), "parts": q.get("parts", {}), "version": q.get("version"),
            "nloc": q.get("nloc"), "hygiene_counts": q.get("hygiene_counts", {})}


def _warnings(stats: list[dict]) -> list[str]:
    out = []
    if len({s["scope"] for s in stats}) > 1:
        out.append("The reviews cover different scopes (whole project vs. a diff) - counts are not directly comparable.")
    if len({s["runs"] for s in stats}) > 1:
        out.append("The reviews used different numbers of runs - more runs find more coin flips and one-offs.")
    if len({s["reviewer"] for s in stats}) > 1:
        out.append("Different reviewers were used.")
    measured = [s["quality"] for s in stats if s["quality"]]
    if measured and len(measured) < len(stats):
        out.append("Code quality was only measured for some reviews (it exists since coinflip 0.4.0).")
    if len({q.get("version") for q in measured}) > 1:
        out.append("Code quality was measured with different rule versions - the scores are not directly comparable.")
    if any(s["canaries"] for s in stats):
        out.append("Some reviews had canaries planted; their findings exclude the canaries but ran on slightly changed code.")
    return out


def _pct_change(before: int, after: int) -> float | None:
    return None if before == 0 else round(100.0 * (after - before) / before, 1)


def compare(folders: list[Path]) -> dict:
    """Compare two or more review sessions of one project, oldest first."""
    if len(folders) < 2:
        raise ProgressError("comparing needs at least 2 review sessions of the same project")
    loaded = []
    for folder in folders:
        meta = session.load_meta(folder)
        if meta.get("mode") != "review":
            raise ProgressError(f"{folder.name} is not a review session")
        loaded.append((meta.get("started") or "", folder, meta, session.score(folder)))
    loaded.sort(key=lambda t: (t[0], t[1].name))
    projects = {m.get("project_path") or m.get("project") for _, _, m, _ in loaded}
    if len(projects) > 1:
        raise ProgressError("the selected sessions belong to different projects")

    stats = [_session_stats(r, m, f) for _, f, m, r in loaded]
    chains: list[dict] = []
    for index, (_, _, _, result) in enumerate(loaded):
        _link(chains, result.get("findings", []), index)
    last = len(loaded) - 1
    rows = []
    for chain in chains:
        cells = chain["cells"]
        anchor = cells[min(cells)]
        rows.append({
            "file": chain["last"]["file"],
            "line": chain["last"]["line"],
            "title": anchor["title"],
            "severity": max((c["severity"] for c in cells.values()),
                            key=lambda s: (SEVERITIES[::-1].index(s) + 1) if s in SEVERITIES else 0),
            "fate": _fate(cells, last),
            "cells": [
                {"hits": cells[i]["hits"], "rate": cells[i]["rate"], "status": cells[i]["status"]} if i in cells else None
                for i in range(len(loaded))
            ],
        })
    order = {"fixed": 0, "still there": 1, "new": 2, "came and went": 3}
    rows.sort(key=lambda r: (order[r["fate"]], -max((c["rate"] for c in r["cells"] if c), default=0), r["file"], r["line"]))

    first, final = stats[0], stats[-1]
    fates = {f: sum(1 for r in rows if r["fate"] == f) for f in order}
    consistent_fixed = sum(1 for r in rows if r["fate"] == "fixed" and r["cells"][0]["status"] == "consistent")
    return {
        "mode": "progress",
        "project": loaded[0][2].get("project"),
        "sessions": stats,
        "summary": {
            "distinct_before": first["distinct_findings"],
            "distinct_after": final["distinct_findings"],
            "distinct_change": _pct_change(first["distinct_findings"], final["distinct_findings"]),
            "consistent_before": first["consistent"],
            "consistent_after": final["consistent"],
            "consistent_change": _pct_change(first["consistent"], final["consistent"]),
            "fixed": fates["fixed"],
            "consistent_fixed": consistent_fixed,
            "still_there": fates["still there"],
            "new": fates["new"],
            "came_and_went": fates["came and went"],
            "quality_before": (first["quality"] or {}).get("score"),
            "quality_after": (final["quality"] or {}).get("score"),
        },
        "warnings": _warnings(stats),
        "findings": rows,
    }


def to_text(p: dict) -> str:
    """Short plain-text summary of a progress comparison."""
    s = p["summary"]

    def change(v):
        return "n/a" if v is None else f"{v:+.0f}%"

    out = [f"coinflip progress  |  {p['project']}  |  {len(p['sessions'])} reviews", ""]
    for i, st in enumerate(p["sessions"], start=1):
        commit = f" @{st['commit']}{'*' if st['dirty'] else ''}" if st.get("commit") else ""
        out.append(f"  {i}. {local_time(st['started'])}{commit:<12} {st['scope']:<16} "
                   f"{st['runs']} runs  {st['distinct_findings']:>3} findings  {st['consistent']:>3} consistent  "
                   f"{st['coin_flips']:>3} coin flips  {st['once']:>3} once")
    out += [
        "",
        f"  >> consistent findings {s['consistent_before']} -> {s['consistent_after']} ({change(s['consistent_change'])}), "
        f"all findings {s['distinct_before']} -> {s['distinct_after']} ({change(s['distinct_change'])})",
        f"     fixed {s['fixed']} (of them consistent: {s['consistent_fixed']}), still there {s['still_there']}, "
        f"new {s['new']}  - linking findings across reviews is approximate",
    ]
    if s.get("quality_before") is not None and s.get("quality_after") is not None:
        out.append(f"     code quality {s['quality_before']:.0f} -> {s['quality_after']:.0f} "
                   f"({s['quality_after'] - s['quality_before']:+.0f} points, no AI)")
    out += [f"  ! {w}" for w in p["warnings"]]
    return "\n".join(out) + "\n"


def to_json(p: dict) -> str:
    return json.dumps(p, indent=2, ensure_ascii=False) + "\n"
