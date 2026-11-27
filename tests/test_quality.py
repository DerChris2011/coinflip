import json
from pathlib import Path

import pytest

from coinflip import quality, report_quality
from coinflip.cli import main
from conftest import git, write

CLEAN_PY = '''\
def add(first, second):
    """Add two numbers."""
    return first + second


def label(price):
    """Format a price."""
    if price < 0:
        raise ValueError("negative")
    return f"{price:.2f} EUR"
'''

MESSY_PY = '''\
import pdb


def handle_data(data, tmp, x1, foo, q, w, extra):
    # total = 0
    # for item in data:
    #     total += item.price
    try:
        pdb.set_trace()
    except Exception:
        pass
    # TODO: remove this
    if data:
        for d in data:
            if d:
                while d:
                    if tmp:
                        with open(d) as fh:
                            return fh.read()
    return None  # noqa
'''

MESSY_TS = '''\
export function formatPrice(value: any): string {
  // const old = value * 100;
  // console.log(old);
  console.log(value);
  try {
    debugger;
  } catch (e) {}
  const cfg = { a: { b: { c: { d: 1 } } } };
  return String(value);
}
'''


def _measure(tmp_path: Path, files: dict[str, str]) -> dict:
    for rel, text in files.items():
        write(tmp_path, rel, text)
    return quality.analyse(tmp_path, sorted(files))


def test_clean_code_scores_high(tmp_path):
    q = _measure(tmp_path, {"src/calc.py": CLEAN_PY})
    assert q["score"] >= 95 and q["grade"] == "A"
    assert q["parts"]["documentation"] == 100
    assert q["parts"]["hygiene"] == 100
    assert q["offenders"]["commented_out"] == [] and q["offenders"]["issues"] == []


def test_messy_code_finds_every_kind_of_problem(tmp_path):
    q = _measure(tmp_path, {"src/shop.py": MESSY_PY, "src/app.ts": MESSY_TS})
    counts = q["hygiene_counts"]
    assert counts["commented-out code"] == 2
    assert counts["debug leftover"] == 3          # pdb.set_trace, console.log, debugger
    assert counts["empty catch"] == 2             # except: pass, catch {}
    assert counts["suppressed warning"] == 1      # noqa
    assert counts["any type"] == 1
    assert counts["todo"] == 1
    assert q["parts"]["hygiene"] == 0
    nested = q["offenders"]["deeply_nested"]
    assert nested[0]["name"] == "handle_data" and nested[0]["nesting"] == 6
    names = {n["name"] for n in q["offenders"]["unclear_names"]}
    assert {"data", "tmp", "x1", "foo", "q"} <= names
    assert q["score"] < 75


def test_object_literals_and_elif_do_not_count_as_nesting(tmp_path):
    ts = "export function f(x: number) {\n  const a = { b: { c: { d: { e: 1 } } } };\n  return a;\n}\n"
    py = ("def g(x):\n    if x == 1:\n        return 1\n    elif x == 2:\n        return 2\n    elif x == 3:\n"
          "        return 3\n    elif x == 4:\n        return 4\n    elif x == 5:\n        return 5\n    return 0\n")
    q = _measure(tmp_path, {"src/a.ts": ts, "src/b.py": py})
    assert q["readability_parts"]["nesting"] == 100


@pytest.mark.parametrize("text,expected", [
    ("total = price * 2", True),
    ("return compute(a, b);", True),
    ("if (user == null) {", True),
    ("for item in items:", True),
    ("doSomething(x)", True),
    ("This keeps the cache warm for the next request.", False),
    ("TODO: handle the timeout", False),
    ("see https://example.com/docs", False),
    ("eslint-disable-next-line", False),
    ("", False),
])
def test_commented_out_code_heuristic(text, expected):
    assert quality.looks_like_code(text) is expected


@pytest.mark.parametrize("name,unclear", [
    ("data", True), ("tmp", True), ("data2", True), ("handleData", True), ("q", True), ("x1", True),
    ("i", False), ("id", False), ("_e", False), ("price", False), ("user_id", False), ("MAX", False),
])
def test_unclear_names(name, unclear):
    assert quality.is_unclear(name) is unclear


def test_duplication_is_measured(tmp_path):
    block = "".join(f"    value_{i} = compute_{i}(value_{i - 1})\n" for i in range(1, 12))
    a = f"def first(value_0):\n{block}    return value_11\n"
    b = f"def second(value_0):\n{block}    return value_11\n"
    q = _measure(tmp_path, {"src/a.py": a, "src/b.py": b})
    assert q["duplicated_percent"] > 50
    assert q["parts"]["duplication"] == 0
    assert q["offenders"]["duplicated"]


def test_tests_only_count_for_the_ratio(tmp_path):
    test = "def test_add():\n    x = 1\n    assert x == 1\n    # result = add(1, 2)\n"
    q = _measure(tmp_path, {"src/calc.py": CLEAN_PY, "tests/test_calc.py": test})
    assert q["files"] == 1 and q["test_files"] == 1
    assert q["hygiene_counts"]["commented-out code"] == 0
    assert q["test_ratio"] > 0


def test_console_log_is_fine_in_command_line_tools(tmp_path):
    q = _measure(tmp_path, {"scripts/run.mjs": "function main() {\n  console.log('hi');\n}\n",
                            "src/lib.js": "function f() {\n  console.log('debug');\n  return 1;\n}\n"})
    files = {i["file"] for i in q["offenders"]["issues"]}
    assert files == {"src/lib.js"}


def test_deterministic(tmp_path):
    files = {"src/shop.py": MESSY_PY, "src/app.ts": MESSY_TS, "src/calc.py": CLEAN_PY}
    first = _measure(tmp_path, files)
    second = quality.analyse(tmp_path, list(reversed(sorted(files))))
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_grade_matches_the_shown_number():
    assert quality.grade(79.6) == "B"
    assert quality.grade(79.4) == "C"
    assert quality.grade(None) is None


def test_empty_project(tmp_path):
    write(tmp_path, "README.md", "# nothing\n")
    q = quality.analyse(tmp_path, [])
    assert q["score"] is None and q["grade"] is None
    assert "nothing to measure" in report_quality.to_text(q)


def test_this_repository_does_not_flag_itself():
    root = Path(__file__).resolve().parent.parent
    q = quality.analyse(root, ["src/coinflip/quality.py"])
    assert q["hygiene_counts"].get("suppressed warning", 0) == 0


def test_cli_quality_formats_and_gate(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("COINFLIP_HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    write(root, "src/shop.py", MESSY_PY)
    write(root, "src/calc.py", CLEAN_PY)
    assert main(["quality", str(root)]) == 0
    assert "code quality" in capsys.readouterr().out
    assert main(["quality", str(root), "-f", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["files"] == 2 and data["version"] == quality.VERSION
    assert main(["quality", str(root), "--fail-under", "99"]) == 1
    assert main(["quality", str(root), "--include", "src/calc.py", "--fail-under", "90"]) == 0
    assert main(["quality", str(root), "-f", "html"]) == 0
    pages = list((tmp_path / "home" / "quality").glob("*.html"))
    assert len(pages) == 1 and "Hygiene" in pages[0].read_text(encoding="utf-8")


def test_cli_quality_diff_measures_only_changed_files(tmp_path, capsys):
    root = tmp_path / "proj"
    write(root, "src/calc.py", CLEAN_PY)
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    write(root, "src/shop.py", MESSY_PY)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "mess")
    assert main(["quality", str(root), "--diff", "HEAD~1", "-f", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["files"] == 1 and data["hygiene_counts"]["debug leftover"] == 1


def test_review_stores_quality_and_reports_it(fake, project, capsys):
    assert main(["review", str(project), "-r", fake, "-n", "2", "-f", "json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["quality"]["score"] is not None
    assert main(["review", str(project), "-r", fake, "-n", "2"]) == 0
    assert "code quality" in capsys.readouterr().out
    pages = sorted((project.parent / "home" / "sessions").glob("*/report.html"))
    assert pages and "Code quality" in pages[-1].read_text(encoding="utf-8")


def test_canaries_do_not_change_the_quality_score(fake, project, capsys):
    assert main(["review", str(project), "-r", fake, "-n", "1", "-f", "json"]) == 0
    plain = json.loads(capsys.readouterr().out)["quality"]
    assert main(["review", str(project), "-r", fake, "-n", "1", "-f", "json", "--canary", "2"]) == 0
    with_canaries = json.loads(capsys.readouterr().out)["quality"]
    assert plain == with_canaries
