"""Canaries: small, realistic bugs planted into the temporary copy of the user's code.

They give ground truth on the user's own code base: if the reviewer finds a canary
in only 4 of 10 runs, it would find a real bug of that kind with similar luck.

Mutations are line-local and keep the line count, so every other finding keeps its
line number. They cover classic logic slips: flipped comparisons and equality,
swapped boolean operators, dropped negations, inverted constants, off-by-one.
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

# (kind, regex, replacement, languages)
MUTATIONS = [
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


def _is_test_file(rel: str) -> bool:
    lowered = rel.lower()
    return "test" in lowered.split("/")[-1] or "/tests/" in f"/{lowered}" or "/__tests__/" in lowered


def candidates(rel: str, text: str) -> list[tuple[int, str, str]]:
    """(line number, kind, mutated line) for every line that can carry a canary."""
    ext = rel.rsplit(".", 1)[-1].lower()
    found = []
    for number, line in enumerate(text.split("\n"), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(_COMMENT) or stripped.startswith(_IMPORT):
            continue
        for kind, pattern, replacement, langs in MUTATIONS:
            if ext not in langs:
                continue
            match = re.search(pattern, line)
            if match and not _in_string(line, match.start()):
                found.append((number, kind, line[: match.start()] + replacement + line[match.end():]))
                break
    return found


def _spread(pool: list[tuple[str, int, str, str]], count: int, rng: random.Random) -> list:
    rng.shuffle(pool)
    chosen: list[tuple[str, int, str, str]] = []
    for cand in pool:
        file, line = cand[0], cand[1]
        if all(c[0] != file or abs(c[1] - line) >= MIN_DISTANCE for c in chosen):
            chosen.append(cand)
        if len(chosen) == count:
            break
    return chosen


def plant(copy: Path, files: list[str], count: int, seed: int,
          prefer: dict[str, set[int]] | None = None) -> list[Canary]:
    """Plant up to `count` canaries into files of the copy. Deterministic for a given seed."""
    texts = {f: (copy / f).read_text(encoding="utf-8", errors="replace") for f in files
             if (copy / f).is_file() and not _is_test_file(f)}
    pool = [(f, n, kind, new) for f in sorted(texts) for n, kind, new in candidates(f, texts[f])]
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
