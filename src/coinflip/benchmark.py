"""Built-in benchmarks: a clean code base plus planted bugs with known locations."""

from __future__ import annotations

import ast
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

BENCH_ROOT = Path(__file__).parent / "benchmarks"
LINE_TOLERANCE = 3


class BenchmarkError(Exception):
    pass


@dataclass(frozen=True)
class Bug:
    id: str
    file: str
    kind: str
    cwe: str | None
    title: str
    scope: str
    start_line: int  # first changed line in the buggy file
    end_line: int  # last changed line in the buggy file
    match_start: int  # a finding on a line in [match_start, match_end] counts as a hit
    match_end: int


@dataclass(frozen=True)
class Benchmark:
    name: str
    version: str
    language: str
    description: str
    bugs: tuple[Bug, ...]

    @property
    def label(self) -> str:
        return f"{self.name}-v{self.version}"


def available() -> list[str]:
    return sorted(p.name for p in BENCH_ROOT.iterdir() if (p / "bugs.toml").is_file())


def _spec(name: str) -> dict:
    path = BENCH_ROOT / name / "bugs.toml"
    if not path.is_file():
        raise BenchmarkError(f"unknown benchmark '{name}' (available: {', '.join(available())})")
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _enclosing_function(source: str, line: int) -> tuple[int, int] | None:
    best = None
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno <= line <= node.end_lineno:
            if best is None or node.lineno > best[0]:
                best = (node.lineno, node.end_lineno)
    return best


def _line_of(text: str, snippet: str, what: str) -> int:
    if text.count(snippet) != 1:
        raise BenchmarkError(f"{what}: snippet must occur exactly once (found {text.count(snippet)})")
    return text[: text.index(snippet)].count("\n") + 1


def _apply(files: dict[str, str], raw_bugs: list[dict]) -> None:
    for raw in raw_bugs:
        text = files[raw["file"]]
        _line_of(text, raw["find"], f"bug {raw['id']} (find)")
        files[raw["file"]] = text.replace(raw["find"], raw["replace"])


def _locate(files: dict[str, str], raw: dict) -> Bug:
    text = files[raw["file"]]
    start = _line_of(text, raw["replace"], f"bug {raw['id']} (replace)")
    end = start + max(raw["replace"].rstrip("\n").count("\n"), 0)
    if raw.get("scope", "lines") == "function":
        span = _enclosing_function(text, start)
        if span is None:
            raise BenchmarkError(f"bug {raw['id']}: scope=function but no enclosing function")
        # The whole function, but never narrower than the plain line tolerance.
        match = (min(span[0], start - LINE_TOLERANCE), max(span[1], end + LINE_TOLERANCE))
    else:
        match = (max(1, start - LINE_TOLERANCE), end + LINE_TOLERANCE)
    return Bug(
        id=raw["id"],
        file=raw["file"],
        kind=raw["kind"],
        cwe=raw.get("cwe"),
        title=raw["title"],
        scope=raw.get("scope", "lines"),
        start_line=start,
        end_line=end,
        match_start=match[0],
        match_end=match[1],
    )


def _read_tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def build(name: str) -> tuple[Benchmark, dict[str, str], dict[str, str]]:
    """Return (benchmark, buggy files, clean files) - all in memory, deterministic."""
    spec = _spec(name)
    clean = _read_tree(BENCH_ROOT / name / "src")
    buggy = dict(clean)
    raw_bugs = spec.get("bug", [])
    for raw in raw_bugs:
        if raw["file"] not in clean:
            raise BenchmarkError(f"bug {raw['id']}: unknown file {raw['file']}")
    _apply(buggy, raw_bugs)
    bugs = tuple(_locate(buggy, raw) for raw in raw_bugs)
    bench = Benchmark(
        name=spec["name"],
        version=str(spec["version"]),
        language=spec.get("language", "unknown"),
        description=spec.get("description", ""),
        bugs=bugs,
    )
    return bench, buggy, clean


def export(files: dict[str, str], dest: Path) -> None:
    """Write the buggy or clean variant of a benchmark into a folder."""
    if dest.exists():
        shutil.rmtree(dest)
    for rel, text in files.items():
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
