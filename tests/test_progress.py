import json
from pathlib import Path

import pytest

from coinflip import progress, report_progress_html
from coinflip.cli import main
from conftest import git, write


def _review(fake, project, runs="3"):
    assert main(["review", str(project), "-r", fake, "-n", runs]) == 0


def _fix_sql(project: Path) -> None:
    path = project / "src" / "service.py"
    path.write_text(path.read_text().replace(
        'return db.execute("SELECT * FROM t WHERE name = \'" + name + "\'")  # SUSPICIOUS',
        'return db.execute("SELECT * FROM t WHERE name = ?", (name,))'))
    git(project, "commit", "-qam", "fix sql")


def test_review_records_commit(fake, project, tmp_path, capsys):
    assert main(["review", str(project), "-r", fake, "-n", "2", "-f", "json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["commit"]) >= 7 and result["dirty"] is False


def test_sessions_lists_reviews_of_the_project(fake, project, capsys):
    _review(fake, project)
    _fix_sql(project)
    _review(fake, project)
    capsys.readouterr()
    assert main(["sessions", str(project), "-f", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [s["number"] for s in data["sessions"]] == [1, 2]
    assert data["sessions"][0]["commit"] != data["sessions"][1]["commit"]
    assert main(["sessions", str(project)]) == 0
    assert "coinflip progress 1 2" in capsys.readouterr().out


def test_progress_shows_the_fix(fake, project, tmp_path, capsys):
    _review(fake, project)
    _fix_sql(project)
    _review(fake, project)
    capsys.readouterr()
    assert main(["progress", "1", "2", "--project", str(project), "-f", "json"]) == 0
    p = json.loads(capsys.readouterr().out)
    s = p["summary"]
    assert (s["consistent_before"], s["consistent_after"]) == (1, 0)
    assert s["fixed"] >= 1 and s["consistent_fixed"] == 1
    fixed = [r for r in p["findings"] if r["fate"] == "fixed"]
    assert fixed[0]["file"] == "src/service.py" and fixed[0]["cells"][1] is None


def test_progress_html_default_location(fake, project, tmp_path, capsys):
    _review(fake, project)
    _fix_sql(project)
    _review(fake, project)
    capsys.readouterr()
    assert main(["progress", "--project", str(project)]) == 0
    captured = capsys.readouterr()
    assert "consistent findings 1 -> 0" in captured.out
    reports = list((tmp_path / "home" / "progress").glob("*.html"))
    assert len(reports) == 1
    page = reports[0].read_text(encoding="utf-8")
    assert "<svg" in page and "What happened to each finding" in page and "Show as table" in page
    assert "http" not in page.replace("https://github.com/DerChris2011/coinflip", "")  # no external resources


def test_progress_needs_two_reviews(fake, project, capsys):
    _review(fake, project)
    capsys.readouterr()
    assert main(["progress", "--project", str(project)]) == 2
    err = capsys.readouterr().err
    assert "at least 2" in err and "review again" in err
    assert main(["progress", "1", "7", "--project", str(project)]) == 2
    assert "there is no review #7" in capsys.readouterr().err


def test_progress_last_n(fake, project, capsys):
    for _ in range(3):
        _review(fake, project, runs="2")
    capsys.readouterr()
    assert main(["progress", "--last", "2", "--project", str(project), "-f", "json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["sessions"]) == 2


def test_other_projects_are_not_listed(fake, project, tmp_path, capsys):
    other = tmp_path / "other"
    write(other, "src/x.py", "def f(a):\n    return a == 1  # SUSPICIOUS\n")
    git(other, "init", "-q", "-b", "main")
    git(other, "add", "-A")
    git(other, "commit", "-qm", "x")
    _review(fake, project)
    _review(fake, other)
    capsys.readouterr()
    assert main(["sessions", str(project), "-f", "json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["sessions"]) == 1


def test_findings_are_linked_across_shifted_lines():
    a = {"file": "a.py", "line": 10, "title": "SQL query built from user input", "hits": 3, "rate": 1.0,
         "status": "consistent", "severity": "high"}
    moved = {**a, "line": 24, "title": "user input concatenated into SQL query", "hits": 2, "rate": 0.67,
             "status": "coin flip"}
    unrelated = {**a, "line": 80, "title": "missing timeout on HTTP call"}
    chains: list[dict] = []
    progress._link(chains, [a], 0)
    progress._link(chains, [moved, unrelated], 1)
    assert len(chains) == 2
    assert chains[0]["cells"][1]["line"] == 24
    assert progress._similarity(a, {**a, "file": "b.py"}) is None


def test_progress_html_escapes_titles():
    st = {"session": "s", "started": "2026-11-27T10:00:00+00:00", "commit": "abc1234", "dirty": False,
          "scope": "whole project", "reviewer": "r", "runs": 2, "canaries": 0, "distinct_findings": 1,
          "consistent": 1, "coin_flips": 0, "once": 0, "single_run_coverage": 100.0, "stability": 100.0,
          "severity": {"critical": 0, "high": 1, "medium": 0, "low": 0, "other": 0}, "files": {"<b>.py": 1}}
    p = {"mode": "progress", "project": "<script>", "sessions": [st, st], "warnings": [],
         "summary": {"distinct_before": 1, "distinct_after": 1, "distinct_change": 0.0, "consistent_before": 1,
                     "consistent_after": 1, "consistent_change": 0.0, "fixed": 0, "consistent_fixed": 0,
                     "still_there": 1, "new": 0, "came_and_went": 0},
         "findings": [{"file": "<b>.py", "line": 1, "title": "<img src=x onerror=alert(1)>", "severity": "high",
                       "fate": "still there", "cells": [{"hits": 2, "rate": 1.0, "status": "consistent"}] * 2}]}
    page = report_progress_html.render(p)
    assert "<script>" not in page
    assert "<img src=x" not in page and "<b>.py" not in page


def test_local_time_falls_back_gracefully():
    assert progress.local_time(None) == ""
    assert progress.local_time("not a date at all") == "not a date at all"[:16]
    assert len(progress.local_time("2026-11-27T19:13:00+00:00")) == 16


def test_progress_tracks_code_quality(fake, project, capsys):
    _review(fake, project)
    write(project, "src/legacy.py", "def handle_data(data, tmp):\n    # old = data * 2\n    # print(old)\n"
                                    "    try:\n        return data\n    except Exception:\n        pass\n")
    git(project, "add", "-A")
    git(project, "commit", "-qm", "legacy")
    _review(fake, project)
    capsys.readouterr()
    assert main(["progress", "--project", str(project), "-f", "json"]) == 0
    p = json.loads(capsys.readouterr().out)
    s = p["summary"]
    assert s["quality_before"] is not None and s["quality_after"] < s["quality_before"]
    assert all(st["quality"]["parts"] for st in p["sessions"])
    html = report_progress_html.render(p)
    assert "Code quality score per review" in html and "code quality (no AI)" in html
    assert "code quality" in progress.to_text(p)


def test_progress_warns_when_old_sessions_have_no_quality(fake, project, capsys):
    _review(fake, project)
    _review(fake, project)
    first = progress.project_sessions(project.parent / "home" / "sessions", project)[0]
    meta = json.loads((first / "meta.json").read_text())
    meta.pop("quality")
    (first / "meta.json").write_text(json.dumps(meta))
    capsys.readouterr()
    assert main(["progress", "--project", str(project), "-f", "json"]) == 0
    p = json.loads(capsys.readouterr().out)
    assert p["summary"]["quality_before"] is None
    assert any("only measured for some reviews" in w for w in p["warnings"])
    assert "Code quality" in report_progress_html.render(p)
