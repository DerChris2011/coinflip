"""Exact duplicated-code detection (copy-paste clones), language independent.

Each file is reduced to its *significant* lines: whitespace is collapsed, and
blank lines, comment lines, import lines, lone brackets and pure number/data
rows are dropped. Every window of N consecutive significant lines is hashed;
a window that occurs more than once (in another file, or non-overlapping in
the same file) marks its lines as duplicated.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_PUNCTUATION_ONLY = re.compile(r"^[\s{}()\[\];,.:]*$")
# Rows of numeric tables (lookup tables, test vectors): numbers, signs and punctuation only.
_DATA_ROW = re.compile(r"^[\d\s.,:;+\-\[\](){}xX]+$")
_COMMENT_PREFIXES = ("//", "#", "/*", "*", "--", "<!--", "'''", '"""')
_IMPORT_PREFIXES = (
    "import ", "from ", "using ", "#include", "package ", "require ", "use ", "export * from", "export {",
)
_TRIVIAL_LINES = frozenset(
    {
        "else", "else:", "else {", "} else {", "end", "pass", "try:", "finally:", "default:",
        "break", "break;", "continue", "continue;", "return", "return;", "});", "})", "};",
    }
)

Location = tuple[str, int]  # (path, index into the file's significant lines)


@dataclass(frozen=True)
class CloneBlock:
    path: str
    start_line: int
    end_line: int
    lines: int  # significant lines in the block
    also_in: tuple[tuple[str, int], ...]  # (path, start line) of other copies


@dataclass(frozen=True)
class DuplicationResult:
    significant_lines: int
    duplicated_lines: int
    per_file: dict[str, tuple[int, int]]  # path -> (significant, duplicated)
    blocks: tuple[CloneBlock, ...]

    @property
    def ratio(self) -> float:
        return self.duplicated_lines / self.significant_lines if self.significant_lines else 0.0


def _is_noise(line: str) -> bool:
    return (
        not line
        or line in _TRIVIAL_LINES
        or _PUNCTUATION_ONLY.match(line) is not None
        or _DATA_ROW.match(line.split("#", 1)[0].split("//", 1)[0]) is not None
        or line.startswith(_COMMENT_PREFIXES)
        or line.startswith(_IMPORT_PREFIXES)
    )


def significant_lines(source: str) -> list[tuple[int, str]]:
    """(original line number, normalised text) for every line that carries logic."""
    result = []
    for number, raw in enumerate(source.splitlines(), start=1):
        line = " ".join(raw.split())
        if not _is_noise(line):
            result.append((number, line))
    return result


def _window_hashes(lines: list[tuple[int, str]], size: int) -> list[bytes]:
    return [
        hashlib.blake2b("\n".join(t for _, t in lines[i : i + size]).encode("utf-8"), digest_size=16).digest()
        for i in range(len(lines) - size + 1)
    ]


def _real_copies(occ: list[Location], size: int) -> list[Location]:
    """Drop occurrences that only 'repeat' by overlapping themselves in one file."""
    if len({p for p, _ in occ}) > 1:
        return occ
    path = occ[0][0]
    idx = sorted(i for _, i in occ)
    keep = []
    for n, i in enumerate(idx):
        gap_before = n > 0 and i - idx[n - 1] >= size
        gap_after = n + 1 < len(idx) and idx[n + 1] - i >= size
        if gap_before or gap_after:
            keep.append((path, i))
    return keep


def _runs(indices: list[int]) -> list[tuple[int, int]]:
    """Merge sorted indices into (first, last) runs of consecutive values."""
    runs: list[tuple[int, int]] = []
    for i in indices:
        if runs and i == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], i)
        else:
            runs.append((i, i))
    return runs


def _find_copies(hashes: dict[str, list[bytes]], size: int) -> dict[bytes, list[Location]]:
    """Window hash -> every location where that window really is a copy."""
    occurrences: dict[bytes, list[Location]] = {}
    for path in sorted(hashes):
        for i, h in enumerate(hashes[path]):
            occurrences.setdefault(h, []).append((path, i))
    copies = {}
    for h, occ in occurrences.items():
        real = _real_copies(occ, size) if len(occ) > 1 else []
        if len(real) > 1:
            copies[h] = real
    return copies


def _block(path: str, run: tuple[int, int], sig: dict, others: list[Location]) -> CloneBlock:
    first, last = run
    return CloneBlock(
        path=path,
        start_line=sig[path][first][0],
        end_line=sig[path][last][0],
        lines=last - first + 1,
        also_in=tuple(sorted((p, sig[p][i][0]) for p, i in others if (p, i) != (path, first))),
    )


def find_duplicates(sources: dict[str, str], min_lines: int) -> DuplicationResult:
    """Find blocks of at least `min_lines` significant lines that appear more than once."""
    paths = sorted(sources)
    sig = {p: significant_lines(sources[p]) for p in paths}
    hashes = {p: _window_hashes(sig[p], min_lines) for p in paths}
    copies = _find_copies(hashes, min_lines)

    duplicated: dict[str, set[int]] = {p: set() for p in paths}
    for locations in copies.values():
        for path, i in locations:
            duplicated[path].update(range(i, i + min_lines))

    blocks = [
        _block(path, run, sig, copies[hashes[path][run[0]]])
        for path in paths
        for run in _runs(sorted(duplicated[path]))
    ]
    blocks.sort(key=lambda b: (-b.lines, b.path, b.start_line))

    per_file = {p: (len(sig[p]), len(duplicated[p])) for p in paths}
    return DuplicationResult(
        significant_lines=sum(s for s, _ in per_file.values()),
        duplicated_lines=sum(d for _, d in per_file.values()),
        per_file=per_file,
        blocks=tuple(blocks),
    )
