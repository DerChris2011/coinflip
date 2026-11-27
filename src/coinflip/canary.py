"""Canaries: small, realistic bugs planted into the temporary copy of the user's code.

They give ground truth on the user's own code base: if the reviewer finds a canary
in only 4 of 10 runs, it would find a real bug of that kind with similar luck.

Mutations are line-local and keep the line count, so every other finding keeps its
line number. They cover classic slips: flipped comparisons and equality, swapped
boolean operators, dropped negations, inverted constants, off-by-one, a forgotten
await, an accumulator that overwrites, a dropped escaping call.

Canaries are spread across kinds (round robin), never planted into test code, and
kept apart from each other so a finding can only ever match one of them.
"""

from __future__ import annotations

import random
import re
from dataclasses import asdict, dataclass
from pathlib import Path

MIN_DISTANCE = 8  # lines between two canaries in one file, so matches stay unambiguous

_C_LIKE = ("js", "mjs", "cjs", "ts", "tsx", "jsx", "java", "cs", "go", "kt", "c", "cc", "cpp", "h", "hpp",
           "swift", "scala", "php", "rs")
_PY_LIKE = ("py", "rb")

_JS_LIKE = ("js", "mjs", "cjs", "ts", "tsx", "jsx")

# (kind, regex, replacement, languages) - the replacement may use regex groups (\\1)
MUTATIONS = [
    ("missing await", r"\bawait\s+", "", _JS_LIKE + ("py", "cs", "rs", "kt", "swift", "dart")),
    ("accumulator", r" \+= ", " = ", _C_LIKE + _PY_LIKE),
    ("accumulator", r" -= ", " = ", _C_LIKE + _PY_LIKE),
    ("dropped escaping", r"\bencodeURIComponent\(([^()]*)\)", r"\1", _JS_LIKE),
    ("dropped escaping", r"\bescapeHtml\(([^()]*)\)", r"\1", _JS_LIKE),
    ("dropped escaping", r"\bhtml\.escape\(([^()]*)\)", r"\1", ("py",)),
    ("dropped escaping", r"\bshlex\.quote\(([^()]*)\)", r"\1", ("py",)),
    ("loop control", r"^(\s*)break$", r"\1continue", ("py",)),
    ("loop control", r"^(\s*)continue$", r"\1break", ("py",)),
    ("equality", r" === ", " !== ", _C_LIKE),
    ("equality", r" !== ", " === ", _C_LIKE),
    ("equality", r" == ", " != ", _C_LIKE + _PY_LIKE),
    ("equality", r" != ", " == ", _C_LIKE + _PY_LIKE),
    ("comparison", r" <= ", " < ", _C_LIKE + _PY_LIKE),
    ("comparison", r" >= ", " > ", _C_LIKE + _PY_LIKE),
    ("comparison", r" < ", " <= ", _C_LIKE + _PY_LIKE),
    ("comparison", r" > ", " >= ", _C_LIKE + _PY_LIKE),
    ("boolean", r" && ", " || ", _C_LIKE),
    ("boolean", r" \|\| ", " && ", _C_LIKE),
    ("boolean", r" and ", " or ", _PY_LIKE),
    ("boolean", r" or ", " and ", _PY_LIKE),
    ("negation", r"\bif not ", "if ", _PY_LIKE),
    ("negation", r"\bif \(!(?!=)", "if (", _C_LIKE),
    ("constant", r"\breturn True\b", "return False", _PY_LIKE),
    ("constant", r"\breturn False\b", "return True", _PY_LIKE),
    ("constant", r"\breturn true\b", "return false", _C_LIKE),
    ("constant", r"\breturn false\b", "return true", _C_LIKE),
    ("off-by-one", r" \+ 1\b", " - 1", _C_LIKE + _PY_LIKE),
    ("off-by-one", r" - 1\b", " + 1", _C_LIKE + _PY_LIKE),
]
_COMMENT = ("#", "//", "/*", "*", "--", "<!--")
_IMPORT = ("import ", "from ", "using ", "#include", "package ", "require")


@dataclass(frozen=True)
class Canary:
    file: str
    line: int
    kind: str
    original: str
    mutated: str

    def to_dict(self) -> dict:
        return asdict(self)


def _in_string(line: str, pos: int) -> bool:
    before = line[:pos]
    return before.count('"') % 2 == 1 or before.count("'") % 2 == 1 or before.count("`") % 2 == 1


_TEST_DIRS = frozenset(
    "test tests __tests__ spec specs e2e fixtures __fixtures__ mocks __mocks__ testdata test-data testing".split()
)
_TEST_NAME = re.compile(r"(^test_|_test\.|\.test\.|\.spec\.|^spec_|_spec\.|mock|fixture|stub|fake)")


def _is_test_file(rel: str) -> bool:
    """Test code is reviewed far less carefully - a canary there would measure the wrong thing."""
    parts = rel.lower().split("/")
    return any(p in _TEST_DIRS for p in parts[:-1]) or bool(_TEST_NAME.search(parts[-1]))


def candidates(rel: str, text: str) -> list[tuple[int, str, str]]:
    """(line number, kind, mutated line) for every line that can carry a canary."""
    ext = rel.rsplit(".", 1)[-1].lower()
    found = []
    for number, line in enumerate(text.split("\n"), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(_COMMENT) or stripped.startswith(_IMPORT):
            continue
        kinds_here: set[str] = set()
        for kind, pattern, replacement, langs in MUTATIONS:
            if ext not in langs or kind in kinds_here:
                continue
            match = re.search(pattern, line)
            if match and not _in_string(line, match.start()):
                kinds_here.add(kind)  # every kind a line supports is a candidate; _spread picks one per line
                found.append((number, kind, line[: match.start()] + match.expand(replacement) + line[match.end():]))
    return found


def _spread(pool: list[tuple[str, int, str, str]], count: int, rng: random.Random) -> list:
    """Pick canaries round robin across kinds, so the measurement is not dominated by
    whatever pattern happens to be most common in the code (usually ==)."""
    by_kind: dict[str, list] = {}
    for cand in pool:
        by_kind.setdefault(cand[2], []).append(cand)
    kinds = sorted(by_kind)
    rng.shuffle(kinds)
    for kind in kinds:
        rng.shuffle(by_kind[kind])
    chosen: list[tuple[str, int, str, str]] = []

    def fits(cand) -> bool:
        return all(c[0] != cand[0] or abs(c[1] - cand[1]) >= MIN_DISTANCE for c in chosen)

    while len(chosen) < count and any(by_kind.values()):
        for kind in kinds:
            queue = by_kind[kind]
            while queue:
                cand = queue.pop()
                if fits(cand):
                    chosen.append(cand)
                    break
            if len(chosen) == count:
                break
    return chosen


AVOID_LINES = 3


def plant(copy: Path, files: list[str], count: int, seed: int,
          prefer: dict[str, set[int]] | None = None,
          avoid: set[tuple[str, int]] | None = None) -> list[Canary]:
    """Plant up to `count` canaries into files of the copy. Deterministic for a given seed.

    `avoid` holds (file, line) spots where reviews already report a real problem: a canary
    there could not be told apart from that finding, so none is planted near them.
    """
    texts = {f: (copy / f).read_text(encoding="utf-8", errors="replace") for f in files
             if (copy / f).is_file() and not _is_test_file(f)}
    blocked: dict[str, list[int]] = {}
    for file, line in avoid or ():
        blocked.setdefault(file, []).append(line)
    pool = [(f, n, kind, new) for f in sorted(texts) for n, kind, new in candidates(f, texts[f])
            if all(abs(n - b) > AVOID_LINES for b in blocked.get(f, ()))]
    if prefer:
        preferred = [c for c in pool if c[1] in prefer.get(c[0], set())]
        if len(preferred) >= count:
            pool = preferred
    chosen = sorted(_spread(pool, count, random.Random(seed)), key=lambda c: (c[0], c[1]))

    canaries = []
    for file, line, kind, new in chosen:
        lines = texts[file].split("\n")
        canaries.append(Canary(file, line, kind, lines[line - 1].strip(), new.strip()))
        lines[line - 1] = new
        texts[file] = "\n".join(lines)
    for file in {c.file for c in canaries}:
        (copy / file).write_text(texts[file], encoding="utf-8", newline="")
    return canaries
