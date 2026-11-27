import pytest

from coinflip import benchmark, prompt


def test_pyshop_builds_and_both_variants_compile():
    bench, buggy, clean = benchmark.build("pyshop")
    assert bench.label == "pyshop-v1"
    assert len(bench.bugs) == 15
    assert sorted(buggy) == sorted(clean)
    for files in (buggy, clean):
        for rel, text in files.items():
            compile(text, rel, "exec")


def test_build_is_deterministic():
    first = benchmark.build("pyshop")
    second = benchmark.build("pyshop")
    assert first == second


def test_bug_lines_point_at_the_changed_code():
    bench, buggy, clean = benchmark.build("pyshop")
    for bug in bench.bugs:
        assert buggy[bug.file] != clean[bug.file]
        block = "\n".join(buggy[bug.file].splitlines()[bug.start_line - 1 : bug.end_line + 1])  # +1 catches pure deletions
        assert block.strip(), bug.id
        assert block not in clean[bug.file], bug.id
        assert bug.match_start <= bug.start_line <= bug.end_line <= bug.match_end


def test_bug_ids_are_unique_and_kinds_valid():
    bench, _, _ = benchmark.build("pyshop")
    ids = [b.id for b in bench.bugs]
    assert len(ids) == len(set(ids))
    assert {b.kind for b in bench.bugs} <= {"security", "logic"}


def test_prompt_contains_no_spoilers():
    bench, buggy, _ = benchmark.build("pyshop")
    text = prompt.render(buggy).lower()
    for bug in bench.bugs:
        assert bug.id not in text
        assert bug.title.lower() not in text
    assert "planted" not in text and "cwe-" not in text


def test_prompt_numbers_lines():
    text = prompt.render({"a.py": "x = 1\ny = 2\n"})
    assert "===== FILE: a.py =====" in text
    assert "1 | x = 1" in text and "2 | y = 2" in text


def test_unknown_benchmark():
    with pytest.raises(benchmark.BenchmarkError, match="unknown benchmark"):
        benchmark.build("nope")


def test_export(tmp_path):
    _, buggy, _ = benchmark.build("pyshop")
    benchmark.export(buggy, tmp_path / "out")
    assert (tmp_path / "out" / "app" / "auth.py").read_text(encoding="utf-8") == buggy["app/auth.py"]
    assert not list((tmp_path / "out").rglob("bugs.toml"))
