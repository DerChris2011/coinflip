from coinflip.benchmark import Bug, Benchmark
from coinflip.findings import Finding, match, parse_answer
from coinflip.metrics import Run, jaccard_mean, status, summarize_benchmark, summarize_review

KNOWN = ["app/auth.py", "app/users.py"]


def bug(id, file="app/auth.py", start=10, end=10, lo=7, hi=13):
    return Bug(id=id, file=file, kind="security", cwe=None, title=id, scope="lines",
               start_line=start, end_line=end, match_start=lo, match_end=hi)


def test_parse_fenced_json_and_normalise_paths():
    text = 'Sure!\n```json\n[{"file": "./src/app/auth.py", "line": "12", "title": "x"}]\n```'
    findings, ok = parse_answer(text, KNOWN)
    assert ok and findings == [Finding("app/auth.py", 12, "", "x")]


def test_parse_takes_last_fenced_block_and_dict_wrapper():
    text = '```json\n[{"file":"a","line":1}]\n```\nfinal:\n```json\n{"findings":[{"file":"app/users.py","line":3}]}\n```'
    findings, ok = parse_answer(text, KNOWN)
    assert ok and [f.file for f in findings] == ["app/users.py"]


def test_parse_bare_array_and_empty():
    assert parse_answer('[{"file": "app/auth.py", "line": 1}]', KNOWN)[0][0].line == 1
    assert parse_answer("```json\n[]\n```", KNOWN) == ([], True)


def test_unparseable_answer():
    assert parse_answer("Looks good to me!", KNOWN) == ([], False)


def test_items_without_usable_line_are_skipped():
    findings, ok = parse_answer('[{"file":"app/auth.py","line":"n/a"},{"file":"app/auth.py","line":true},'
                                '{"file":"app/auth.py","line":"42-44"}]', KNOWN)
    assert ok and [f.line for f in findings] == [42]


def test_match_counts_each_bug_once_and_keeps_noise():
    bugs = (bug("a"),)
    findings = [Finding("app/auth.py", 9, "", ""), Finding("app/auth.py", 11, "", ""),
                Finding("app/auth.py", 40, "", ""), Finding("app/users.py", 10, "", "")]
    hits, unmatched = match(findings, bugs)
    assert hits == {"a"}
    assert [(f.file, f.line) for f in unmatched] == [("app/auth.py", 40), ("app/users.py", 10)]


def test_overlapping_ranges_go_to_the_closest_bug():
    bugs = (bug("a", start=10, end=10, lo=7, hi=13), bug("b", start=14, end=14, lo=11, hi=17))
    assert match([Finding("app/auth.py", 12, "", "")], bugs)[0] == {"a"}
    assert match([Finding("app/auth.py", 13, "", "")], bugs)[0] == {"b"}


def test_status_thresholds():
    assert status(1.0) == status(0.9) == "reliable"
    assert status(0.89) == status(0.11) == "coin flip"
    assert status(0.1) == status(0.0) == "missed"


def test_jaccard():
    assert jaccard_mean([{"a"}]) is None
    assert jaccard_mean([{"a", "b"}, {"a", "b"}]) == 1.0
    assert jaccard_mean([{"a"}, {"b"}]) == 0.0
    assert jaccard_mean([set(), set()]) == 1.0


def _run(i, hits, error=None, unmatched=0):
    return Run("buggy", i, 10.0, error, parsed=True, hits=set(hits),
               findings=[], unmatched=[Finding("x", 1, "", "")] * unmatched)


def test_summarize_benchmark():
    bench = Benchmark("t", "1", "python", "", (bug("a"), bug("b"), bug("c"), bug("d")))
    runs = [_run(1, "ab", unmatched=2), _run(2, "ac"), _run(3, "a"), _run(4, "", error="boom")]
    s = summarize_benchmark(bench, runs, [])
    assert s["answered_runs"] == 3
    assert (s["reliable"], s["coin_flips"], s["missed"]) == (1, 2, 1)
    assert s["reliability"] == 25.0
    assert s["recall"] == {"mean": 41.7, "min": 25.0, "max": 50.0, "all_runs": 75.0}
    assert s["failures"]["errors"] == 1
    assert s["extra_findings_per_run"] == 0.7
    assert s["clean_findings_per_run"] is None
    assert [b["id"] for b in s["bugs"]][0] == "a"


def test_summarize_benchmark_without_answers():
    bench = Benchmark("t", "1", "python", "", (bug("a"),))
    s = summarize_benchmark(bench, [_run(1, "", error="x")], [])
    assert s["reliability"] is None and s["bugs"] == []


def _review_run(i, *lines):
    return Run("review", i, 1.0, None, parsed=True,
               findings=[Finding("m.py", n, "high" if n == 10 else "low", f"t{n}") for n in lines])


def test_summarize_review_clusters_nearby_findings():
    s = summarize_review([_review_run(1, 10, 50), _review_run(2, 11, 80), _review_run(3, 12)], [])
    assert s["distinct_findings"] == 3
    assert (s["consistent"], s["coin_flips"], s["once"]) == (1, 0, 2)
    top = s["findings"][0]
    assert (top["line"], top["hits"], top["status"], top["severity"]) == (10, 3, "consistent", "high")
    assert s["single_run_coverage"] == 55.6  # runs saw 2, 2 and 1 of the 3 findings
    assert s["canary"] is None


def test_summarize_review_separates_canaries():
    canaries = [{"id": "canary-1", "file": "m.py", "line": 50, "kind": "equality", "original": "a", "mutated": "b"}]
    s = summarize_review([_review_run(1, 10, 51), _review_run(2, 10), _review_run(3, 10, 49)], canaries)
    assert s["distinct_findings"] == 1
    c = s["canary"]
    assert c["planted"] == 1 and c["coin_flips"] == 1 and c["recall_all_runs"] == 100.0
    assert c["canaries"][0]["hits"] == 2


def test_clusters_do_not_chain_and_never_merge_one_runs_findings():
    def run(i, *lines):
        return Run("review", i, 1.0, None, parsed=True, findings=[Finding("m.py", n, "", f"t{n}") for n in lines])
    # every run reports three distinct problems 3 lines apart
    s = summarize_review([run(1, 12, 15, 18), run(2, 12, 15, 18), run(3, 13, 15, 18)], [])
    assert s["distinct_findings"] == 3
    assert [f["hits"] for f in s["findings"]] == [3, 3, 3]
