import json
from pathlib import Path

from coinflip import canary, review, workspace
from coinflip.cli import main
from conftest import git, write


def _latest(tmp_path: Path) -> Path:
    return sorted((tmp_path / "home" / "sessions").iterdir())[-1]


def _snapshot(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): p.read_text() for p in root.rglob("*") if p.is_file() and ".git" not in p.parts}


def test_review_whole_project(fake, project, tmp_path, capsys):
    before = _snapshot(project)
    assert main(["review", str(project), "-r", fake, "-n", "6", "-f", "json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "review" and result["project"] == "myproject"
    assert result["scope_files"] == 2  # README and node_modules are not in scope
    top = result["findings"][0]
    assert (top["file"], top["hits"], top["status"]) == ("src/service.py", 6, "consistent")
    maybe = [f for f in result["findings"] if f["file"] == "src/util.py"]
    assert maybe and maybe[0]["status"] in ("coin flip", "once")
    assert 0 < result["single_run_coverage"] < 100
    assert _snapshot(project) == before  # the user's project is never touched


def test_review_runs_on_a_copy_that_is_removed_afterwards(fake, project, tmp_path, monkeypatch):
    created = []
    original = workspace.make_copy

    def spy(root, files):
        created.append(original(root, files))
        return created[-1]

    monkeypatch.setattr(workspace, "make_copy", spy)
    assert main(["review", str(project), "-r", fake, "-n", "2"]) == 0
    assert created and not created[0].exists()


def test_review_text_report(fake, project, capsys):
    assert main(["review", str(project), "-r", fake, "-n", "4"]) == 0
    out = capsys.readouterr().out
    assert "review: myproject (whole project, 2 files)" in out
    assert "a single run showed on average" in out
    assert "CONSISTENT" in out and "src/service.py:14" in out


def test_review_diff_only_reviews_changes(fake, project, tmp_path, capsys):
    git(project, "checkout", "-q", "-b", "feature")
    (project / "src" / "util.py").write_text((project / "src" / "util.py").read_text() + "\n\ndef risky(x):\n"
                                             "    return eval(x)  # SUSPICIOUS\n")
    write(project, "src/new_module.py", "def hello(name):\n    return 'hi ' + name\n")
    assert main(["review", str(project), "-r", fake, "-n", "3", "--diff", "main", "-f", "json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["diff"] == "main" and result["scope_files"] == 2
    prompt = (_latest(tmp_path) / "prompt-review.txt").read_text()
    assert "Review THIS CHANGE" in prompt and "+    return eval(x)" in prompt
    assert "New file (not yet committed): src/new_module.py" in prompt
    assert "service.py" not in prompt  # unchanged files are not part of the review scope


def test_diff_errors(fake, project, capsys):
    assert main(["review", str(project), "-r", fake, "--diff", "main"]) == 2
    assert "nothing to review" in capsys.readouterr().err
    assert main(["review", str(project), "-r", fake, "--diff", "no-such-branch"]) == 2
    assert "unknown git reference" in capsys.readouterr().err


def test_canaries_measure_the_reviewer_on_your_code(fake, project, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_ORIG", str(project))
    before = _snapshot(project)
    assert main(["review", str(project), "-r", fake, "-n", "8", "--canary", "3", "-f", "json"]) == 0
    result = json.loads(capsys.readouterr().out)
    c = result["canary"]
    assert c["planted"] == 3
    assert c["coin_flips"] >= 1  # the fake finds each canary in about half of the runs
    canary_spots = {(k["file"], k["line"]) for k in c["canaries"]}
    assert not any((f["file"], f["line"]) in canary_spots for f in result["findings"])
    assert _snapshot(project) == before


def test_canaries_are_consistent_with_the_diff(project):
    git(project, "checkout", "-q", "-b", "feature")
    path = project / "src" / "util.py"
    path.write_text(path.read_text() + "\n\ndef positive(x):\n    if x > 0:\n        return True\n    return False\n")
    plan = review.prepare(project, ref="main", canaries=2, seed=3)
    try:
        changed = workspace.changed_lines(project, workspace.diff_text(project, "main", plan.scope), plan.scope)
        for c in plan.meta["canaries"]:
            assert c["line"] in changed[c["file"]]  # canaries go into the changed lines first
            assert "+" + (plan.copy / c["file"]).read_text().split("\n")[c["line"] - 1] in plan.prompt
    finally:
        plan.cleanup()


def test_inline_mode(fake, project, tmp_path, capsys):
    assert main(["review", str(project), "-r", fake, "-n", "2", "--inline", "-f", "json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["findings"][0]["file"] == "src/service.py"
    assert "===== FILE: src/service.py =====" in (_latest(tmp_path) / "prompt-review.txt").read_text()


def test_inline_size_limit(fake, project, capsys):
    assert main(["review", str(project), "-r", fake, "--inline", "--max-chars", "100"]) == 2
    assert "too much to send inline" in capsys.readouterr().err


def test_report_defaults_to_latest_session(fake, project, capsys):
    main(["review", str(project), "-r", fake, "-n", "2"])
    capsys.readouterr()
    assert main(["report", "-f", "markdown"]) == 0
    out = capsys.readouterr().out
    assert "review: myproject" in out and "| Found | Status |" in out


def test_no_source_files(fake, tmp_path, capsys):
    empty = tmp_path / "docs"
    write(empty, "notes.md", "hello")
    assert main(["review", str(empty), "-r", fake]) == 2
    assert "no source files" in capsys.readouterr().err


# ---------------------------------------------------------------- canary unit tests

def test_canary_candidates_skip_comments_strings_and_imports():
    text = "import a == b\n# if x == 1\ns = 'a == b'\nif x == 1:\n    y = a and b\n"
    kinds = {(n, kind) for n, kind, _ in canary.candidates("m.py", text)}
    assert kinds == {(4, "equality"), (5, "boolean")}


def test_canary_language_specific_operators():
    js = "if (a === b && !c) {\n  return true;\n}\n"
    lines = [new for _, _, new in canary.candidates("m.ts", js)]
    assert lines == ["if (a !== b && !c) {", "  return false;"]


def test_canary_plant_is_deterministic_and_spread(tmp_path):
    body = "\n".join(f"if x{i} == {i}:\n    pass" for i in range(40))
    for root in (tmp_path / "a", tmp_path / "b"):
        write(root, "m.py", body)
    first = canary.plant(tmp_path / "a", ["m.py"], 4, seed=7)
    second = canary.plant(tmp_path / "b", ["m.py"], 4, seed=7)
    assert first == second and len(first) == 4
    lines = sorted(c.line for c in first)
    assert all(b - a >= canary.MIN_DISTANCE for a, b in zip(lines, lines[1:]))
    content = (tmp_path / "a" / "m.py").read_text().split("\n")
    assert all(content[c.line - 1].strip() == c.mutated for c in first)
    assert len(content) == len(body.split("\n"))


def test_canaries_avoid_test_files(tmp_path):
    write(tmp_path, "tests/test_m.py", "if a == b:\n    pass\n")
    assert canary.plant(tmp_path, ["tests/test_m.py"], 1, seed=1) == []


def test_changed_lines_and_patch_diff(tmp_path):
    diff = ("diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n@@ -1,3 +1,4 @@\n a = 1\n-b = 2\n+b = 3\n"
            "+c = 4\n d = 5\n")
    assert workspace.changed_lines(tmp_path, diff, ["m.py"]) == {"m.py": {2, 3}}
    patched = workspace.patch_diff(diff, {("m.py", 3): "c = 5", ("m.py", 4): "d = 6"})
    assert "+c = 5" in patched and " d = 6" in patched and "-b = 2" in patched


def test_heartbeat_shows_long_runs(fake, project, monkeypatch, capsys):
    from coinflip import session
    monkeypatch.setattr(session, "HEARTBEAT_SECONDS", 0.2)
    monkeypatch.setenv("FAKE_SLEEP", "0.7")
    assert main(["review", str(project), "-r", fake, "-n", "1"]) == 0
    assert "still working: review run 1 (0:00)" in capsys.readouterr().err
