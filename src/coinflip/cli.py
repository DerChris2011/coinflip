"""coinflip command line.

  coinflip review [PATH]      review your own project N times, confidence per finding
  coinflip bench run          measure reviewers on the built-in benchmark (leaderboard)
  coinflip report / compare   re-score stored sessions / leaderboard
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from coinflip import __version__, benchmark, prompt, report, review, reviewers, session, workspace
from coinflip.findings import match, parse_answer

RENDER = {"text": report.to_text, "markdown": report.to_markdown, "json": report.to_json}
EXIT_USAGE, EXIT_MISSING_CLI = 2, 3


def _err(message: str) -> None:
    print(f"coinflip: {message}", file=sys.stderr)


def _common(p: argparse.ArgumentParser, runs: int) -> None:
    p.add_argument("-r", "--reviewer", action="append", metavar="SPEC",
                   help="claude, codex, gemini, ollama:<model>, cmd:<command> (repeatable)")
    p.add_argument("-n", "--runs", type=int, default=runs, help=f"runs per reviewer (default {runs})")
    p.add_argument("--timeout", type=float, default=600, help="seconds per run before it is given up (default 600)")
    p.add_argument("-j", "--parallel", type=int, default=1, help="runs in parallel (default 1)")
    p.add_argument("-o", "--out", type=Path, help="where sessions are stored (default ~/.coinflip/sessions)")
    p.add_argument("-f", "--format", choices=RENDER, default="text")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="coinflip", description="Is your AI code review a coin flip? Measure it.")
    p.add_argument("--version", action="version", version=f"coinflip {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    rev = sub.add_parser("review", help="review your project N times - get a confidence score per finding")
    rev.add_argument("path", nargs="?", type=Path, default=Path("."), help="project folder (default: .)")
    _common(rev, runs=5)
    rev.add_argument("--diff", metavar="REF", help="only review changes against a git ref, e.g. main")
    rev.add_argument("--include", action="append", default=[], metavar="GLOB", help="files to review (repeatable)")
    rev.add_argument("--canary", type=int, default=0, metavar="N",
                     help="plant N small bugs into a temporary copy to measure the reviewer on your code")
    rev.add_argument("--seed", type=int, default=1, help="seed for canary placement (default 1)")
    rev.add_argument("--inline", action="store_true", help="send the code inside the prompt (default for ollama)")
    rev.add_argument("--max-chars", type=int, default=review.DEFAULT_MAX_INLINE_CHARS, help=argparse.SUPPRESS)

    bench = sub.add_parser("bench", help="the built-in benchmark with planted bugs")
    bsub = bench.add_subparsers(dest="bench_command", required=True)
    brun = bsub.add_parser("run", help="run reviewers on the benchmark and measure how reliable they are")
    _common(brun, runs=10)
    brun.add_argument("--clean-runs", type=int, default=3, help="runs on the bug-free twin (default 3, 0 = skip)")
    brun.add_argument("--bench", default="pyshop", help="which benchmark (default pyshop)")
    bsub.add_parser("list", help="list benchmarks")
    show = bsub.add_parser("show", help="list the planted bugs (spoilers!)")
    show.add_argument("name")
    exp = bsub.add_parser("export", help="write a benchmark variant to a folder")
    exp.add_argument("name")
    exp.add_argument("dest", type=Path)
    exp.add_argument("--variant", choices=("buggy", "clean"), default="buggy")

    rep = sub.add_parser("report", help="(re-)score stored sessions")
    rep.add_argument("sessions", nargs="*", type=Path, help="session folders (default: the latest one)")
    rep.add_argument("-f", "--format", choices=RENDER, default="text")

    cmp_ = sub.add_parser("compare", help="benchmark leaderboard across stored sessions")
    cmp_.add_argument("sessions", nargs="*", type=Path, help="session folders or parents (default: all)")
    cmp_.add_argument("-f", "--format", choices=("text", "markdown"), default="text")

    sub.add_parser("reviewers", help="show which reviewer CLIs are installed")
    return p


# ---------------------------------------------------------------- shared run logic

def _reviewers(specs: list[str] | None) -> list[reviewers.Reviewer]:
    if not specs:
        default = reviewers.default_spec()
        if default is None:
            raise reviewers.ReviewerError("no AI CLI found - install Claude Code, Codex or Gemini CLI, or pass -r")
        specs = [default]
    return [reviewers.parse(s) for s in specs]


def _progress(label: str, bench=None, known=None):
    def callback(variant: str, index: int, done: int, answer: reviewers.Answer) -> None:
        if answer.error:
            detail = f"FAILED: {answer.error.splitlines()[-1][:80]}"
        else:
            findings, ok = parse_answer(answer.text, known or [])
            detail = f"{len(findings)} findings" if ok else "no readable JSON"
            if ok and bench is not None and variant == "buggy":
                detail += f", planted bugs found {len(match(findings, bench.bugs)[0])}/{len(bench.bugs)}"
        print(f"  [{label}] {variant} run {index:>2}  {answer.seconds:6.1f}s  {detail}", file=sys.stderr, flush=True)
    return callback


def _check_counts(args) -> bool:
    ok = args.runs >= 1 and args.parallel >= 1 and getattr(args, "clean_runs", 0) >= 0
    if not ok:
        _err("--runs and --parallel must be >= 1, --clean-runs >= 0")
    return ok


def _execute(args, specs, meta, jobs, label, bench=None, known=None, cwd=None) -> list[dict]:
    out = args.out or session.home()
    results = []
    for reviewer in specs:
        folder = session.new_session_dir(out, label, reviewer)
        try:
            session.collect(folder, meta, reviewer, jobs, args.timeout, args.parallel,
                            _progress(reviewer.spec, bench, known), cwd=cwd if reviewer.agentic else None)
        except KeyboardInterrupt:
            _err(f"interrupted - partial results kept in {folder}")
            raise
        result = session.score(folder)
        results.append(result)
        sys.stdout.write(RENDER[args.format](result))
        print(f"  session saved to {folder}", file=sys.stderr)
    return results


# ---------------------------------------------------------------- commands

def _cmd_review(args) -> int:
    if not _check_counts(args) or args.canary < 0:
        return EXIT_USAGE
    try:
        specs = _reviewers(args.reviewer)
    except reviewers.ReviewerError as exc:
        _err(str(exc))
        return EXIT_USAGE
    missing = [r.argv[0] for r in specs if not r.available()]
    if missing:
        _err(f"not installed or not on PATH: {', '.join(missing)} (see `coinflip reviewers`)")
        return EXIT_MISSING_CLI
    inline = args.inline or not all(r.agentic for r in specs)
    try:
        plan = review.prepare(args.path, tuple(args.include), args.diff, args.canary, args.seed, inline,
                              args.max_chars)
    except workspace.WorkspaceError as exc:
        _err(str(exc))
        return EXIT_USAGE
    what = f"changes vs {args.diff}" if args.diff else "whole project"
    canaries = f", {len(plan.meta['canaries'])} canaries planted" if args.canary else ""
    print(f"coinflip: reviewing {plan.meta['project']} ({what}, {len(plan.scope)} files{canaries}) - "
          f"{len(specs)} reviewer(s) x {args.runs} runs on a temporary copy", file=sys.stderr)
    try:
        results = _execute(args, specs, plan.meta, [("review", plan.prompt, args.runs)], "review",
                           known=plan.meta["files"], cwd=plan.copy)
    except KeyboardInterrupt:
        return 130
    finally:
        plan.cleanup()
    return 0 if any(r["answered_runs"] for r in results) else 1


def _cmd_bench_run(args) -> int:
    if not _check_counts(args):
        return EXIT_USAGE
    try:
        specs = _reviewers(args.reviewer)
        bench, buggy, clean = benchmark.build(args.bench)
    except (reviewers.ReviewerError, benchmark.BenchmarkError) as exc:
        _err(str(exc))
        return EXIT_USAGE
    missing = [r.argv[0] for r in specs if not r.available()]
    if missing:
        _err(f"not installed or not on PATH: {', '.join(missing)} (see `coinflip reviewers`)")
        return EXIT_MISSING_CLI
    meta = {"mode": "benchmark", "benchmark": bench.label, "benchmark_name": args.bench}
    jobs = [("buggy", prompt.render(buggy), args.runs)]
    if args.clean_runs:
        jobs.append(("clean", prompt.render(clean), args.clean_runs))
    print(f"coinflip: {len(specs)} reviewer(s) x {sum(j[2] for j in jobs)} runs on {bench.label}", file=sys.stderr)
    try:
        results = _execute(args, specs, meta, jobs, bench.label, bench=bench, known=sorted(buggy))
    except KeyboardInterrupt:
        return 130
    if len(results) > 1 and args.format != "json":
        sys.stdout.write("\n" + report.compare(results, markdown=args.format == "markdown"))
    return 0 if any(r.get("answered_runs") for r in results) else 1


def _cmd_bench(args) -> int:
    if args.bench_command == "run":
        return _cmd_bench_run(args)
    if args.bench_command == "list":
        for name in benchmark.available():
            bench, _, _ = benchmark.build(name)
            print(f"{bench.label:<14} {len(bench.bugs):>3} bugs  {bench.description}")
        return 0
    try:
        bench, buggy, clean = benchmark.build(args.name)
    except benchmark.BenchmarkError as exc:
        _err(str(exc))
        return EXIT_USAGE
    if args.bench_command == "show":
        for b in bench.bugs:
            print(f"{b.file + ':' + str(b.start_line):<22} {b.kind:<8} {b.cwe or '':<8} {b.title}")
        return 0
    benchmark.export(buggy if args.variant == "buggy" else clean, args.dest)
    print(f"wrote {args.variant} {bench.label} to {args.dest}")
    return 0


def _expand(paths: list[Path]) -> list[Path]:
    found = []
    for p in paths or [session.home()]:
        if (p / "meta.json").is_file():
            found.append(p)
        elif p.is_dir():
            found += sorted(c for c in p.iterdir() if (c / "meta.json").is_file())
    return found


def _cmd_report(args) -> int:
    folders = _expand(args.sessions)
    if not args.sessions:
        folders = folders[-1:]  # latest session
    if not folders:
        _err("no session folders found")
        return EXIT_USAGE
    for folder in folders:
        try:
            sys.stdout.write(RENDER[args.format](session.score(folder)))
        except (FileNotFoundError, benchmark.BenchmarkError) as exc:
            _err(str(exc))
            return EXIT_USAGE
    return 0


def _cmd_compare(args) -> int:
    folders = _expand(args.sessions)
    try:
        results = [r for r in (session.score(f) for f in folders) if r["mode"] == "benchmark"]
    except benchmark.BenchmarkError as exc:
        _err(str(exc))
        return EXIT_USAGE
    if not results:
        _err("no benchmark sessions found - run `coinflip bench run` first")
        return EXIT_USAGE
    sys.stdout.write(report.compare(results, markdown=args.format == "markdown"))
    return 0


def _cmd_reviewers() -> int:
    for name in reviewers.PRESET_NAMES:
        spec = "ollama:<model>" if name == "ollama" else name
        installed = "installed" if reviewers.shutil.which(name) else "not found"
        print(f"  {spec:<16} {installed}")
    print("  cmd:<command>    any command that reads the prompt on stdin and answers on stdout")
    default = reviewers.default_spec()
    print(f"\n  default for `coinflip review`: {default or 'none - install an AI CLI or pass -r'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    handlers = {
        "review": lambda: _cmd_review(args),
        "bench": lambda: _cmd_bench(args),
        "report": lambda: _cmd_report(args),
        "compare": lambda: _cmd_compare(args),
        "reviewers": _cmd_reviewers,
    }
    return handlers[args.command]()


if __name__ == "__main__":
    raise SystemExit(main())
