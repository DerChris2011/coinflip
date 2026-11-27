"""Turn a free-text reviewer answer into findings, and match them to planted bugs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from coinflip.benchmark import Bug

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    severity: str
    title: str


def _candidates(text: str) -> list[str]:
    """JSON candidates: fenced blocks (last one first), then the outermost [...] span."""
    blocks = [m.group(1) for m in _FENCE.finditer(text)][::-1]
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        blocks.append(text[start : end + 1])
    return blocks


def _load_list(text: str) -> list | None:
    for block in _candidates(text):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            data = data.get("findings", data.get("issues"))
        if isinstance(data, list):
            return data
    return None


def _normalise_path(raw: str, known: list[str]) -> str:
    path = raw.strip().replace("\\", "/").lstrip("./")
    if path in known:
        return path
    matches = [k for k in known if k.endswith("/" + path) or path.endswith("/" + k) or path.endswith(k)]
    return matches[0] if len(matches) == 1 else path


def _line(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    match = re.match(r"\s*(\d+)", str(value))
    return int(match.group(1)) if match else None


def parse_answer(text: str, known_files: list[str]) -> tuple[list[Finding], bool]:
    """Return (findings, parsed_ok). Unparseable answers count as 'found nothing'."""
    data = _load_list(text)
    if data is None:
        return [], False
    findings = []
    for item in data:
        if not isinstance(item, dict) or "file" not in item:
            continue
        line = _line(item.get("line"))
        if line is None:
            continue
        findings.append(
            Finding(
                file=_normalise_path(str(item["file"]), known_files),
                line=line,
                severity=str(item.get("severity", "")).lower(),
                title=str(item.get("title", ""))[:200],
            )
        )
    return findings, True


def _distance(bug: Bug, line: int) -> int:
    if bug.start_line <= line <= bug.end_line:
        return 0
    return min(abs(line - bug.start_line), abs(line - bug.end_line))


def match(findings: list[Finding], bugs: tuple[Bug, ...]) -> tuple[set[str], list[Finding]]:
    """Return (ids of bugs hit, findings that matched no bug).

    A finding hits a bug when it points into the bug's match range; if ranges
    overlap, the closest bug wins. Several findings on one bug count once.
    """
    hit: set[str] = set()
    unmatched: list[Finding] = []
    for f in findings:
        candidates = [b for b in bugs if b.file == f.file and b.match_start <= f.line <= b.match_end]
        if not candidates:
            unmatched.append(f)
            continue
        best = min(candidates, key=lambda b: (_distance(b, f.line), b.id))
        hit.add(best.id)
    return hit, unmatched
