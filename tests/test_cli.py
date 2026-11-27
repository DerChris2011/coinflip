import json

from coinflip.cli import main


def _run(fake, tmp_path, *extra):
    return main(["bench", "run", "-r", fake, "-n", "10", "--clean-runs", "2", "-o", str(tmp_path / "out"), *extra])


def _sessions(tmp_path):
    return sorted((tmp_path / "out").iterdir())


def test_benchmark_run_end_to_end(fake, tmp_path, capsys):
    assert _run(fake, tmp_path) == 0
    out = capsys.readouterr().out
    assert ">> reliability 33%" in out
    assert "RELIABLE" in out and "COIN FLIP" in out and "MISSED" in out
    (folder,) = _sessions(tmp_path)
    names = sorted(p.name for p in folder.iterdir())
    assert "buggy-10.txt" in names and "clean-02.txt" in names and "prompt-buggy.txt" in names
    result = json.loads((folder / "results.json").read_text())
    assert result["reliable"] == 5 and result["answered_runs"] == 10
    assert result["clean_findings_per_run"] == 1
    assert result["extra_findings_per_run"] == 1.0


def test_report_rescoring_gives_identical_results(fake, tmp_path, capsys):
    _run(fake, tmp_path, "-f", "json")
    first = json.loads(capsys.readouterr().out)
    (folder,) = _sessions(tmp_path)
    assert main(["report", str(folder), "-f", "json"]) == 0
    assert json.loads(capsys.readouterr().out) == first


def test_parallel_runs_give_same_counts(fake, tmp_path, capsys):
    assert _run(fake, tmp_path, "-j", "4", "-f", "json") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["answered_runs"] == 10 and result["clean_runs"] == 2


def test_two_reviewers_and_compare(fake, tmp_path, capsys, monkeypatch):
    assert main(["bench", "run", "-r", fake, "-r", fake + " ", "-n", "4", "--clean-runs", "0",
                 "-o", str(tmp_path / "out")]) == 0
    assert "coinflip leaderboard - pyshop-v1" in capsys.readouterr().out
    assert main(["compare", str(tmp_path / "out"), "-f", "markdown"]) == 0
    table = capsys.readouterr().out
    assert table.count("| cmd:") == 2 and "| Reliability |" in table


def test_failing_reviewer_is_reported(fake, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "fail")
    assert main(["bench", "run", "-r", fake, "-n", "2", "--clean-runs", "0", "-o", str(tmp_path / "out")]) == 1
    captured = capsys.readouterr()
    assert "no usable answers" in captured.out
    assert "rate limit exceeded" in captured.out + captured.err


def test_garbage_answers_count_as_nothing_found(fake, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "garbage")
    assert main(["bench", "run", "-r", fake, "-n", "2", "--clean-runs", "0", "-o", str(tmp_path / "out"), "-f", "json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["failures"]["unparseable"] == 2 and result["reliability"] == 0.0


def test_usage_errors(tmp_path, capsys):
    assert main(["bench", "run", "-r", "nonsense", "-o", str(tmp_path)]) == 2
    assert main(["bench", "run", "-r", "ollama", "-o", str(tmp_path)]) == 2
    assert main(["bench", "run", "-r", "cmd:definitely-not-installed-xyz", "-o", str(tmp_path)]) == 3
    assert main(["bench", "run", "-r", "claude", "--bench", "nope", "-o", str(tmp_path)]) == 2
    assert main(["bench", "run", "-r", "claude", "-n", "0"]) == 2
    assert main(["compare", str(tmp_path / "empty")]) == 2


def test_bench_commands(tmp_path, capsys):
    assert main(["bench", "list"]) == 0
    assert "pyshop-v1" in capsys.readouterr().out
    assert main(["bench", "show", "pyshop"]) == 0
    assert "CWE-89" in capsys.readouterr().out
    assert main(["bench", "export", "pyshop", str(tmp_path / "clean"), "--variant", "clean"]) == 0
    assert "pbkdf2_hmac" in (tmp_path / "clean" / "app" / "auth.py").read_text()


def test_reviewers_command(capsys):
    assert main(["reviewers"]) == 0
    assert "cmd:<command>" in capsys.readouterr().out
