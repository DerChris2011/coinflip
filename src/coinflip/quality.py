"""Deterministic code quality - no AI, no tokens, the same commit always gets the same score.

Six parts, each 0-100, averaged into one score:

  complexity     share of function code in functions with cyclomatic complexity <= 10
  size           share of function code in functions with <= 50 lines of code
  duplication    100 at 0 % copy-pasted lines, 0 at 25 %
  readability    nesting depth, parameter count, long lines, unclear names
  documentation  share of public functions with a doc comment / docstring
  hygiene        commented-out code, debug leftovers, empty catch blocks, suppressed
                 warnings, `any` in TypeScript, TODO/FIXME - per 1,000 lines of code

Test code is measured separately (test-to-code ratio) and not held to these rules.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

import lizard

from coinflip.canary import _is_test_file
from coinflip.duplication import find_duplicates

MAX_COMPLEXITY = 10
MAX_FUNCTION_LINES = 50
MAX_NESTING = 4
MAX_PARAMETERS = 5
MAX_LINE_LENGTH = 120
DUPLICATION_MIN_LINES = 6
DUPLICATION_CEILING = 0.25
LONG_LINE_CEILING = 0.10  # 10 % long lines -> readability part 0
UNCLEAR_NAME_CEILING = 0.10  # 10 % unclear names -> readability part 0
HYGIENE_CEILING = 20.0  # issues per 1,000 lines of code -> hygiene 0
TOP = 10
VERSION = 1  # bump when the rules change - scores of different versions are not comparable
GRADES = ((90.0, "A"), (80.0, "B"), (70.0, "C"), (60.0, "D"))

_HASH_COMMENT = {"py", "rb", "sh"}
_SLASH_COMMENT = {"js", "mjs", "cjs", "ts", "tsx", "jsx", "vue", "java", "kt", "kts", "cs", "go", "rs", "swift",
                  "scala", "c", "h", "cc", "cpp", "hpp", "php"}
_BRACE = _SLASH_COMMENT - {"vue"}
_TS = {"ts", "tsx"}

_CODE_START = re.compile(
    r"^(if|for|while|return|def|class|function|const|let|var|import|from|export|await|async|elif|else|try|"
    r"except|catch|throw|raise|print|console\.|self\.|this\.|switch|case|yield|new)\b"
)
_ASSIGNMENT = re.compile(r"^[\w.\[\]'\"]+\s*(=|\+=|-=|\*=|/=)\s*[^=\s]")
_CALL = re.compile(r"^[\w.$]+\(.*\)\s*;?$")
_NOT_CODE = re.compile(r"^(todo|fixme|note|hack|xxx|@|eslint|prettier|noqa|type:|pylint|istanbul|#region|"
                       r"#endregion|http|https|see |e\.g\.|i\.e\.|copyright|license|spdx)", re.IGNORECASE)

_DEBUG = re.compile(r"\bconsole\.(debug|trace)\(|\bdebugger\s*;|\bpdb\.set_trace\(|\bbreakpoint\(\)")
_CONSOLE_LOG = re.compile(r"\bconsole\.log\(")  # fine in command line tools, a leftover elsewhere
_CONTROL = re.compile(r"\b(if|else|for|foreach|while|do|switch|try|catch|finally|using|lock|match|loop|select)\b")
_TODO = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b")
# written with [x] classes so this file does not flag itself
_SUPPRESS = re.compile(r"eslint-disabl[e]|@ts-ignor[e]|@ts-expect-erro[r]|#\s*type:\s*ignor[e]|#\s*noq[a]\b|"
                       r"@SuppressWarning[s]|//\s*nolin[t]|#pragma warning disabl[e]|pylint:\s*disabl[e]")
_TS_ANY = re.compile(r":\s*any\b|\bas\s+any\b|<any>")
_EMPTY_CATCH = re.compile(r"\bcatch\s*(\([^)]*\))?\s*\{\s*\}")
_EXCEPT = re.compile(r"^\s*except\b[^:]*:\s*(pass)?\s*(#.*)?$")

_GENERIC_NAMES = frozenset(
    "data tmp temp foo baz qux obj stuff thing things info val vals ret arr str1 str2 myvar "
    "handledata processdata dostuff dosomething helper helpers util utils manager item2 res2 result2 "
    "value2 data2 data3 obj2 temp2 tmp2 test1 test2 func func2 callback2 something".split()
)
_SHORT_OK = frozenset("i j k n m x y z e f _ id db ok io fs os re el ex cb fn tx ui up dt dx dy xs ys t a b c s r v w h".split())


@dataclass
class _Function:
    file: str
    name: str
    line: int
    end_line: int
    ccn: int
    nloc: int
    params: list[str]
    nesting: int = 0
    documented: bool | None = None  # None = not a public function that needs docs


@dataclass
class _FileFacts:
    path: str
    nloc: int = 0
    code_lines: int = 0
    long_lines: list[int] = field(default_factory=list)
    commented_blocks: list[tuple[int, int, str]] = field(default_factory=list)  # start, lines, preview
    issues: dict[str, list[tuple[int, str]]] = field(default_factory=dict)  # kind -> [(line, text)]
    names: list[tuple[int, str]] = field(default_factory=list)
    functions: list[_Function] = field(default_factory=list)


# ---------------------------------------------------------------- per-line helpers

def _ext(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _comment_text(line: str, ext: str) -> str | None:
    """The text of a comment line without its marker, or None for a code line."""
    s = line.strip()
    if ext in _HASH_COMMENT and s.startswith("#") and not s.startswith("#!"):
        return s.lstrip("#").strip()
    if ext in _SLASH_COMMENT:
        for marker in ("///", "//", "/**", "/*", "*/", "*"):
            if s.startswith(marker):
                return s[len(marker):].rstrip("*/").strip()
    if ext == "sql" and s.startswith("--"):
        return s[2:].strip()
    return None


def looks_like_code(text: str) -> bool:
    """Heuristic: is this comment text a line of (commented-out) code rather than prose?"""
    if not text or _NOT_CODE.match(text):
        return False
    words = text.split()
    if len(words) > 3 and sum(w.isalpha() for w in words) / len(words) > 0.8 and not text.endswith((";", "{", "}")):
        return False  # a sentence
    if text.endswith((";", "{", "}", ");", "),")) or text in ("}", "{"):
        return True
    if _CODE_START.match(text) and re.search(r"[(=\[{:;.]", text):
        return True
    return bool(_ASSIGNMENT.match(text) or _CALL.match(text))


def _scan_comments(facts: _FileFacts, lines: list[str], ext: str) -> None:
    block_start, block_lines = 0, []
    for number, line in enumerate(lines, start=1):
        text = _comment_text(line, ext)
        if text is not None and _TODO.search(text):
            facts.issues.setdefault("todo", []).append((number, line.strip()))
        if text is not None and looks_like_code(text):
            if not block_lines:
                block_start = number
            block_lines.append(text)
            continue
        if text is not None and not text and block_lines:
            continue  # an empty comment line inside a block of commented-out code
        if block_lines:
            facts.commented_blocks.append((block_start, len(block_lines), block_lines[0][:80]))
            block_lines = []
    if block_lines:
        facts.commented_blocks.append((block_start, len(block_lines), block_lines[0][:80]))


def _is_cli(path: str, lines: list[str]) -> bool:
    """Command line tools print on purpose - console.log there is output, not a leftover."""
    parts = path.lower().split("/")
    return (bool(lines) and lines[0].startswith("#!")) or bool({"bin", "scripts", "script", "cli"} & set(parts[:-1])) \
        or parts[-1].split(".")[0] in ("cli", "main", "index", "setup")


def _scan_code(facts: _FileFacts, lines: list[str], ext: str) -> None:
    cli = _is_cli(facts.path, lines)
    for number, line in enumerate(lines, start=1):
        if not line.strip() or _comment_text(line, ext) is not None:
            if _SUPPRESS.search(line):
                facts.issues.setdefault("suppressed warning", []).append((number, line.strip()))
            continue
        facts.code_lines += 1
        if len(line) > MAX_LINE_LENGTH:
            facts.long_lines.append(number)
        checks = (("debug leftover", _DEBUG), ("suppressed warning", _SUPPRESS), ("empty catch", _EMPTY_CATCH))
        for kind, pattern in checks:
            if pattern.search(line):
                facts.issues.setdefault(kind, []).append((number, line.strip()))
        if not cli and _CONSOLE_LOG.search(line):
            facts.issues.setdefault("debug leftover", []).append((number, line.strip()))
        if ext in _TS and _TS_ANY.search(line):
            facts.issues.setdefault("any type", []).append((number, line.strip()))
        if ext == "py" and _is_swallowed_except(lines, number):
            facts.issues.setdefault("empty catch", []).append((number, line.strip()))


def _is_swallowed_except(lines: list[str], number: int) -> bool:
    match = _EXCEPT.match(lines[number - 1])
    if not match:
        return False
    if match.group(1):
        return True
    following = next((l.strip() for l in lines[number:] if l.strip()), "")
    return following == "pass"


# ---------------------------------------------------------------- functions: nesting, docs, names

def _python_nesting(text: str) -> dict[int, int]:
    """def line -> deepest nesting of if/for/while/try/with/match blocks inside it (elif is not deeper)."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return {}
    blocks = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith, ast.Match)
    if hasattr(ast, "TryStar"):
        blocks += (ast.TryStar,)
    result: dict[int, int] = {}

    def depth(node: ast.AST, elif_chain: bool = False) -> int:
        best = 0
        for name, value in ast.iter_fields(node):
            children = value if isinstance(value, list) else [value]
            for child in children:
                if not isinstance(child, ast.AST) or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                                                         ast.ClassDef, ast.Lambda)):
                    continue
                is_elif = isinstance(node, ast.If) and name == "orelse" and len(children) == 1 \
                    and isinstance(child, ast.If)
                inner = depth(child)
                best = max(best, inner + (1 if isinstance(child, blocks) and not is_elif else 0))
        return best

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result[node.lineno] = depth(node)
    return result


def _brace_nesting(lines: list[str], fn: _Function) -> int:
    """Deepest stack of braces opened by control statements (object literals and callbacks do not count)."""
    stack: list[bool] = []
    best = 0
    for line in lines[fn.line - 1: fn.end_line]:
        code = re.sub(r"(\"(\\.|[^\"\\])*\"|'(\\.|[^'\\])*'|`[^`]*`)", '""', line.split("//")[0])
        last = 0
        for index, ch in enumerate(code):
            if ch == "{":
                before = code[last:index]
                stack.append(bool(_CONTROL.search(before)))
                best = max(best, sum(stack))
                last = index + 1
            elif ch == "}":
                if stack:
                    stack.pop()
                last = index + 1
    return best


_JS = {"js", "mjs", "cjs", "ts", "tsx", "jsx", "vue"}
_METHOD = re.compile(r"^\s+(?:(?:public|static|async|get|set|override|readonly)\s+)*[A-Za-z_$][\w$]*\s*[(<]")


def _is_public(line: str, name: str, ext: str) -> bool:
    """Part of the module's interface - only those need documentation."""
    short = name.split(".")[-1].split("::")[-1]
    if short.startswith(("_", "#")) or "anonymous" in name:
        return False
    if ext == "py":
        return "." not in name  # outer.inner is a local helper
    if ext in _JS:
        return line.lstrip().startswith("export ") or (bool(_METHOD.match(line)) and not re.search(
            r"\b(private|protected|const|let|var|function)\b", line))
    if ext == "go":
        return short[:1].isupper()
    if ext == "rs":
        return bool(re.search(r"\bpub\b", line))
    if ext in ("java", "cs"):
        return bool(re.search(r"\b(public|protected)\b", line))
    if ext in ("c", "h", "cc", "cpp", "hpp"):
        return not line.lstrip().startswith("static ")
    return not re.search(r"\b(private|internal|fileprivate)\b", line)


def _documented(lines: list[str], fn: _Function, ext: str) -> bool | None:
    if fn.nloc < 3 or not _is_public(lines[fn.line - 1] if fn.line <= len(lines) else "", fn.name, ext):
        return None
    if ext == "py":
        for line in lines[fn.line - 1: fn.end_line]:
            if line.rstrip().endswith(":"):
                index = lines.index(line, fn.line - 1)
                nxt = next((l.strip() for l in lines[index + 1: fn.end_line] if l.strip()), "")
                return nxt.startswith(('"""', "'''", 'r"""', "r'''"))
        return False
    above = fn.line - 2
    while above >= 0 and lines[above].strip().startswith("@"):
        above -= 1
    return above >= 0 and _comment_text(lines[above], ext) is not None


def _names(lines: list[str], ext: str, functions: list[_Function]) -> list[tuple[int, str]]:
    found = [(f.line, f.name.split(".")[-1].split("::")[-1]) for f in functions if "anonymous" not in f.name]
    found += [(f.line, p.split(":")[0].split("=")[0].strip().lstrip("*&").split()[-1])
              for f in functions for p in f.params if p.strip()]
    pattern = (re.compile(r"^\s*([A-Za-z_]\w*)\s*=(?!=)") if ext == "py"
               else re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)"))
    for number, line in enumerate(lines, start=1):
        if _comment_text(line, ext) is None:
            found += [(number, m) for m in pattern.findall(line)]
    return [(n, name) for n, name in found if name and name not in ("self", "cls", "this")]


def is_unclear(name: str) -> bool:
    """Generic names (data, tmp, handleData ...) and cryptic one- or two-letter names."""
    name = name.lstrip("_") or "_"
    plain = name.lower().replace("_", "")
    if plain in _GENERIC_NAMES or re.fullmatch(r"(data|temp|tmp|obj|val|var|item|res|result|foo|bar)\d+", plain):
        return True
    return len(name) <= 2 and name.lower() not in _SHORT_OK and not name.isupper()


# ---------------------------------------------------------------- one file

def _analyse_file(path: str, text: str) -> _FileFacts:
    ext = _ext(path)
    lines = text.split("\n")
    facts = _FileFacts(path=path)
    info = lizard.analyze_file.analyze_source_code(path, text)
    facts.nloc = info.nloc
    py_nesting = _python_nesting(text) if ext == "py" else {}
    for f in info.function_list:
        fn = _Function(path, f.name, f.start_line, f.end_line, f.cyclomatic_complexity, f.nloc,
                       [p for p in f.parameters if p])
        if ext == "py":
            fn.nesting = py_nesting.get(fn.line, 0)
        elif ext in _BRACE:
            fn.nesting = _brace_nesting(lines, fn)
        fn.documented = _documented(lines, fn, ext)
        facts.functions.append(fn)
    _scan_comments(facts, lines, ext)
    _scan_code(facts, lines, ext)
    facts.names = _names(lines, ext, facts.functions)
    return facts


# ---------------------------------------------------------------- scoring

def _clip(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


def _share_ok(functions: list[_Function], bad) -> float | None:
    total = sum(f.nloc for f in functions)
    if not total:
        return None
    return _clip(100.0 * (1 - sum(f.nloc for f in functions if bad(f)) / total))


def grade(score: float | None) -> str | None:
    """Letter grade A-E for a score, based on the rounded number people see."""
    if score is None:
        return None
    shown = round(score)  # the grade must match the number people see
    return next((letter for limit, letter in GRADES if shown >= limit), "E")


def _readability(functions, facts_list) -> tuple[float | None, dict]:
    code_lines = sum(f.code_lines for f in facts_list)
    long_lines = sum(len(f.long_lines) for f in facts_list)
    names = [n for f in facts_list for _, n in f.names]
    unclear = sum(1 for n in names if is_unclear(n))
    parts = {
        "nesting": _share_ok(functions, lambda f: f.nesting > MAX_NESTING),
        "parameters": _clip(100.0 * (1 - sum(1 for f in functions if len(f.params) > MAX_PARAMETERS) / len(functions)))
        if functions else None,
        "long_lines": _clip(100.0 * (1 - (long_lines / code_lines) / LONG_LINE_CEILING)) if code_lines else None,
        "names": _clip(100.0 * (1 - (unclear / len(names)) / UNCLEAR_NAME_CEILING)) if names else None,
    }
    available = [v for v in parts.values() if v is not None]
    return (_clip(sum(available) / len(available)) if available else None), parts


def _hygiene(facts_list, nloc: int) -> tuple[float | None, dict]:
    counts = {"commented-out code": sum(len(f.commented_blocks) for f in facts_list)}
    for f in facts_list:
        for kind, items in f.issues.items():
            counts[kind] = counts.get(kind, 0) + len(items)
    weighted = sum(v * (0.5 if k == "todo" else 1.0) for k, v in counts.items())
    if not nloc:
        return None, counts
    density = weighted / nloc * 1000
    return _clip(100.0 * (1 - density / HYGIENE_CEILING)), dict(sorted(counts.items()))


def _offenders(functions, facts_list, dup) -> dict:
    def fn(f: _Function) -> dict:
        return {"file": f.file, "line": f.line, "name": f.name, "ccn": f.ccn, "nloc": f.nloc,
                "nesting": f.nesting, "params": len(f.params)}

    issues = sorted(((f.path, n, kind, text[:120]) for f in facts_list for kind, items in f.issues.items()
                     for n, text in items), key=lambda t: (t[2] == "todo", t[0], t[1]))
    return {
        "complex": [fn(f) for f in sorted(functions, key=lambda f: (-f.ccn, -f.nloc, f.file, f.line))
                    if f.ccn > MAX_COMPLEXITY][:TOP],
        "long": [fn(f) for f in sorted(functions, key=lambda f: (-f.nloc, f.file, f.line))
                 if f.nloc > MAX_FUNCTION_LINES][:TOP],
        "deeply_nested": [fn(f) for f in sorted(functions, key=lambda f: (-f.nesting, f.file, f.line))
                          if f.nesting > MAX_NESTING][:TOP],
        "undocumented": [fn(f) for f in sorted(functions, key=lambda f: (-f.nloc, f.file, f.line))
                         if f.documented is False][:TOP],
        "commented_out": [{"file": f.path, "line": s, "lines": n, "preview": p}
                          for f in facts_list for s, n, p in f.commented_blocks][:TOP * 2],
        "issues": [{"file": p, "line": n, "kind": k, "text": t} for p, n, k, t in issues][:TOP * 3],
        "unclear_names": [{"file": p, "line": n, "name": name} for p, n, name in
                          sorted({(f.path, n, name) for f in facts_list for n, name in f.names if is_unclear(name)})][:TOP * 2],
        "duplicated": [{"file": b.path, "line": b.start_line, "end_line": b.end_line, "lines": b.lines,
                        "also_in": [{"file": p, "line": ln} for p, ln in b.also_in[:3]]} for b in dup.blocks[:TOP]],
    }


def analyse(root: Path, files: list[str]) -> dict:
    """Measure the given files (relative to root). Test files only count for the test ratio."""
    sources, tests = {}, {}
    for rel in sorted(files):
        path = root / rel
        if not path.is_file() or path.stat().st_size > 1_000_000:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if max((len(l) for l in text.split("\n")), default=0) > 2000:
            continue  # minified or generated
        (tests if _is_test_file(rel) else sources)[rel] = text
    facts_list = [_analyse_file(rel, text) for rel, text in sources.items()]
    functions = [fn for f in facts_list for fn in f.functions]
    nloc = sum(f.nloc for f in facts_list)
    dup = find_duplicates(sources, DUPLICATION_MIN_LINES)
    readability, readability_parts = _readability(functions, facts_list)
    hygiene, hygiene_counts = _hygiene(facts_list, nloc)
    eligible = [f for f in functions if f.documented is not None]
    test_nloc = sum(lizard.analyze_file.analyze_source_code(r, t).nloc for r, t in tests.items())
    parts = {
        "complexity": _share_ok(functions, lambda f: f.ccn > MAX_COMPLEXITY),
        "size": _share_ok(functions, lambda f: f.nloc > MAX_FUNCTION_LINES),
        "duplication": _clip(100.0 * (1 - dup.ratio / DUPLICATION_CEILING)) if dup.significant_lines else None,
        "readability": readability,
        "documentation": _clip(100.0 * sum(1 for f in eligible if f.documented) / len(eligible)) if eligible else None,
        "hygiene": hygiene,
    }
    available = [v for v in parts.values() if v is not None]
    score = _clip(sum(available) / len(available)) if available else None
    return {
        "version": VERSION,
        "score": score,
        "grade": grade(score),
        "parts": parts,
        "readability_parts": readability_parts,
        "files": len(sources),
        "test_files": len(tests),
        "nloc": nloc,
        "functions": len(functions),
        "documented_functions": sum(1 for f in eligible if f.documented),
        "public_functions": len(eligible),
        "duplicated_percent": round(100.0 * dup.ratio, 1),
        "commented_out_lines": sum(n for f in facts_list for _, n, _ in f.commented_blocks),
        "hygiene_counts": hygiene_counts,
        "test_ratio": round(test_nloc / nloc, 2) if nloc else None,
        "offenders": _offenders(functions, facts_list, dup),
    }


def measure(root: Path, includes: tuple[str, ...] = (), ref: str | None = None) -> dict:
    """Quality of a project folder: all source files (or only those changed against `ref`)."""
    from coinflip import workspace
    root = root.resolve()
    if not root.is_dir():
        raise workspace.WorkspaceError(f"not a folder: {root}")
    files = workspace.changed_files(root, ref) if ref is not None else workspace.list_files(root)
    return analyse(root, workspace.scope(files, includes))


PART_NAMES = {
    "complexity": "Complexity", "size": "Function size", "duplication": "Duplication",
    "readability": "Readability", "documentation": "Documentation", "hygiene": "Hygiene",
}


def one_line(q: dict | None) -> str:
    """One-line summary of a quality result for terminal output."""
    if not q or q.get("score") is None:
        return "code quality: n/a"
    parts = " · ".join(f"{PART_NAMES[k].lower()} {v:.0f}" for k, v in q["parts"].items() if v is not None)
    return f"code quality {q['score']:.0f}/100 ({q['grade']}): {parts}"
