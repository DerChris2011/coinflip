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
    found = [(kind, new) for _, kind, new in canary.candidates("m.ts", js)]
    assert ("equality", "if (a !== b && !c) {") in found
    assert ("boolean", "if (a === b || !c) {") in found
    assert ("constant", "  return false;") in found


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


def test_html_report(fake, project, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_ORIG", str(project))
    assert main(["review", str(project), "-r", fake, "-n", "4", "--canary", "2"]) == 0
    assert "HTML report:" in capsys.readouterr().err
    page = (_latest(tmp_path) / "report.html").read_text(encoding="utf-8")
    assert page.startswith("<!DOCTYPE html>") and "</html>" in page
    assert "The coin-flip matrix" in page and "Canaries" in page and "What each run said" in page
    assert page.count('class="dot hit"') >= 4
    assert "SELECT * FROM t WHERE name" in page  # code snippet from the project
    assert "http" not in page.replace("https://github.com/DerChris2011/coinflip", "")  # no external resources
    out = tmp_path / "share.html"
    assert main(["report", "-f", "html", "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == page


def test_html_escapes_reviewer_text(fake, project, tmp_path, monkeypatch):
    from coinflip import report_html
    result = {"mode": "review", "project": "<script>x</script>", "diff": None, "scope_files": 1, "reviewer": "r",
              "answered_runs": 1, "started": "", "distinct_findings": 1, "consistent": 1, "coin_flips": 0, "once": 0,
              "stability": None, "seconds_per_run": 1.0, "single_run_coverage": 100.0, "run_indexes": [1],
              "findings": [{"file": "a.py", "line": 1, "severity": "high", "title": "<img src=x onerror=alert(1)>",
                            "hits": 1, "rate": 1.0, "status": "consistent", "runs": [1]}], "canary": None}
    page = report_html.render(result)
    assert "<script>x" not in page and "<img src=x" not in page


def test_canary_new_kinds():
    ts = "async function f(id) {\n  const r = await get(encodeURIComponent(id));\n  total += r;\n}\n"
    kinds = {kind: new for _, kind, new in canary.candidates("m.ts", ts)}
    assert kinds["missing await"] == "  const r = get(encodeURIComponent(id));"
    assert kinds["dropped escaping"] == "  const r = await get(id);"
    assert kinds["accumulator"] == "  total = r;"
    py = "for x in xs:\n    if x:\n        break\n"
    assert ("loop control", "        continue") in [(k, n) for _, k, n in canary.candidates("m.py", py)]


def test_canaries_are_balanced_across_kinds(tmp_path):
    # 30 equality lines and a few of other kinds: the pick must not be all equality
    lines = [f"if v{i} == {i}:\n    pass\n" for i in range(30)]
    lines += ["total += 1\n\n" * 1, "\n" * 8, "ok = a and b\n", "\n" * 8, "return True\n"]
    write(tmp_path, "m.py", "".join(lines))
    kinds = [c.kind for c in canary.plant(tmp_path, ["m.py"], 4, seed=1)]
    assert len(set(kinds)) == 4


def test_test_code_is_never_mutated():
    for rel in ["test/helpers/mock-api.ts", "src/__tests__/a.ts", "src/a.spec.ts", "tests/b.py", "pkg/x_test.go",
                "src/fixtures/data.py", "spec/models/user_spec.rb"]:
        assert canary._is_test_file(rel), rel
    for rel in ["src/latest.ts", "src/contest.py", "app/attestation.go"]:
        assert not canary._is_test_file(rel), rel


def test_canary_match_must_be_close(fake):
    from coinflip.findings import Finding
    from coinflip.metrics import Run, summarize_review
    canaries = [{"id": "canary-1", "file": "m.py", "line": 50, "kind": "equality", "original": "a", "mutated": "b"}]
    near = Run("review", 1, 1.0, None, parsed=True, findings=[Finding("m.py", 52, "", "x")])
    far = Run("review", 2, 1.0, None, parsed=True, findings=[Finding("m.py", 53, "", "y")])
    s = summarize_review([near, far], canaries)
    assert s["canary"]["canaries"][0]["hits"] == 1  # 2 lines off counts, 3 lines off does not
    assert s["canary"]["by_kind"] == {"equality": {"planted": 1, "found": 50.0}}


def test_canaries_avoid_spots_of_earlier_findings(tmp_path):
    body = "\n".join(f"if x{i} == {i}:\n    pass" for i in range(20))
    write(tmp_path, "m.py", body)
    lines = {n for n, _, _ in canary.candidates("m.py", body)}
    avoid = {("m.py", n) for n in lines if n < 30}
    planted = canary.plant(tmp_path, ["m.py"], 3, seed=1, avoid=avoid)
    assert planted and all(c.line > 30 + canary.AVOID_LINES - 1 for c in planted)


def test_canary_run_uses_earlier_review_of_the_same_project(fake, project, tmp_path, monkeypatch, capsys):
    assert main(["review", str(project), "-r", fake, "-n", "2"]) == 0
    capsys.readouterr()
    monkeypatch.setenv("FAKE_ORIG", str(project))
    assert main(["review", str(project), "-r", fake, "-n", "2", "--canary", "2", "-f", "json"]) == 0
    captured = capsys.readouterr()
    assert "away from" in captured.err and "spots earlier reviews already flagged" in captured.err
    result = json.loads(captured.out)
    flagged = {("src/service.py", 14)}  # the SUSPICIOUS line every run reports
    for c in result["canary"]["canaries"]:
        assert all(c["file"] != f or abs(c["line"] - l) > canary.AVOID_LINES for f, l in flagged)
