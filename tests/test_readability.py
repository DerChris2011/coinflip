import json
from pathlib import Path

import pytest

from coinflip import readability
from coinflip.cli import main
from conftest import write

ANSWER = """Sure.
```json
[{"file": "./src/a.py", "naming": 8, "structure": "7", "comments": 5, "overall": 7.4, "reason": "clear"},
 {"file": "src/b.py", "overall": 11},
 {"file": "src/unknown.py", "overall": 5},
 {"file": "src/a.py", "overall": 1}]
```"""


def test_parse_ratings_keeps_only_valid_expected_files():
    ratings, ok = readability.parse_ratings(ANSWER, ["src/a.py", "src/b.py"])
    assert ok and list(ratings) == ["src/a.py"]
    assert ratings["src/a.py"]["overall"] == 7 and ratings["src/a.py"]["structure"] == 7
    assert ratings["src/a.py"]["reason"] == "clear"
    assert readability.parse_ratings("no json here", ["src/a.py"]) == ({}, False)
    wrapped = '```json\n{"files": [{"file": "src/a.py", "overall": 6}]}\n```'
    assert readability.parse_ratings(wrapped, ["src/a.py"])[0]["src/a.py"]["overall"] == 6


@pytest.mark.parametrize("spread,expected", [(0, "consistent"), (1, "consistent"), (2, "wobbly"),
                                             (3, "coin flip"), (7, "coin flip")])
def test_status(spread, expected):
    assert readability.status(spread) == expected


def test_spearman():
    assert readability.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert readability.spearman([1, 2, 3, 4], [40, 30, 20, 10]) == -1.0
    assert readability.spearman([1, 2, 3], [1, 2, 3]) is None
    assert readability.spearman([5, 5, 5, 5], [1, 2, 3, 4]) is None


def test_summarize():
    def run(i, a, b=None, error=None):
        ratings = {"src/a.py": {"overall": a, "naming": a, "structure": a, "comments": a, "reason": "r"}}
        if b is not None:
            ratings["src/b.py"] = {"overall": b, "naming": b, "structure": b, "comments": b, "reason": "r"}
        return {"index": i, "seconds": 10.0, "error": error, "parsed": error is None, "ratings": ratings if not error else {}}
    s = readability.summarize([run(1, 3, 7), run(2, 8, 7), run(3, 5, 6), run(4, 0, error="boom")],
                              ["src/a.py", "src/b.py"], {"src/a.py": 50.0, "src/b.py": 90.0})
    assert s["parsed_runs"] == 3 and s["failures"]["errors"] == 1
    a = next(f for f in s["files"] if f["file"] == "src/a.py")
    assert (a["min"], a["max"], a["spread"], a["status"]) == (3, 8, 5, "coin flip")
    assert s["files"][0]["file"] == "src/a.py"  # biggest disagreement first
    assert s["coin_flips"] == 1 and s["consistent"] == 1
    assert s["run_scores"] == [5.0, 7.5, 5.5] and s["run_range"] == [5.0, 7.5]
    assert s["coverage"] == 100.0
    assert "coin flip" in readability.headline(s)


def test_select_files_takes_the_largest_non_test_files(tmp_path):
    write(tmp_path, "src/big.py", "x = 1\n" * 40)
    write(tmp_path, "src/mid.py", "x = 1\n" * 20)
    write(tmp_path, "src/tiny.py", "x = 1\n")
    write(tmp_path, "tests/test_big.py", "x = 1\n" * 99)
    files = ["src/big.py", "src/mid.py", "src/tiny.py", "tests/test_big.py"]
    assert readability.select_files(tmp_path, files, 1) == ["src/big.py"]
    assert readability.select_files(tmp_path, files, 5) == ["src/big.py", "src/mid.py"]


def test_cli_readability_end_to_end(fake, project, capsys):
    assert main(["readability", str(project), "-r", fake, "-n", "4", "-f", "json"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["mode"] == "readability" and r["parsed_runs"] == 4
    by_file = {f["file"]: f for f in r["files"]}
    assert by_file["src/service.py"]["mean"] == 4 and by_file["src/service.py"]["status"] == "consistent"
    assert by_file["src/util.py"]["status"] == "coin flip" and by_file["src/util.py"]["spread"] == 5
    assert r["quality"]["score"] is not None
    assert all(f["rules_score"] is not None for f in r["files"])
    sessions = sorted((project.parent / "home" / "sessions").glob("*readability*"))
    page = (sessions[-1] / "report.html").read_text(encoding="utf-8")
    assert "Every rating of every file" in page and "Coin flip" in page and "<circle" in page
    assert main(["report", str(sessions[-1])]) == 0
    assert "readability" in capsys.readouterr().out
    assert main(["report", str(sessions[-1]), "-f", "markdown"]) == 0
    assert "| Ratings per run |" in capsys.readouterr().out


def test_cli_readability_inline(fake, project, capsys):
    assert main(["readability", str(project), "-r", fake, "-n", "2", "--inline", "--files", "1", "-f", "json"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["files_rated"] == 1 and r["files"][0]["file"] == "src/service.py"


def test_readability_sessions_stay_out_of_progress(fake, project, capsys):
    assert main(["readability", str(project), "-r", fake, "-n", "2"]) == 0
    capsys.readouterr()
    assert main(["sessions", str(project), "-f", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["sessions"] == []


def test_cli_readability_rejects_bad_counts(fake, project):
    assert main(["readability", str(project), "-r", fake, "--files", "0"]) == 2
    assert main(["readability", str(project), "-r", fake, "--files", str(readability.MAX_FILES + 1)]) == 2


def test_cli_readability_unreadable_answers(fake, project, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_MODE", "garbage")
    assert main(["readability", str(project), "-r", fake, "-n", "2"]) == 1
    assert "no usable ratings" in capsys.readouterr().out


def test_project_path_is_untouched(fake, project):
    before = {p: p.read_bytes() for p in Path(project).rglob("*") if p.is_file() and ".git" not in p.parts}
    assert main(["readability", str(project), "-r", fake, "-n", "2"]) == 0
    after = {p: p.read_bytes() for p in Path(project).rglob("*") if p.is_file() and ".git" not in p.parts}
    assert before == after
