"""Collect reviewer answers into a session folder, and score a session folder.

Collecting and scoring are separate on purpose: raw answers are kept, so a
session can be re-scored later (e.g. with improved matching) without paying
for the AI runs again.

Layout of a session folder:
  meta.json        what was run, how, and per-run timing/errors
  prompt-*.txt     the exact prompts
  buggy-01.txt     raw answer of run 1 on the buggy code, ...
  clean-01.txt     raw answer of run 1 on the clean twin, ...
  results.json     the computed summary
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from coinflip import __version__, benchmark, metrics, prompt, report_html, reviewers
from coinflip.findings import match, parse_answer

Progress = Callable[[str, int, int, reviewers.Answer], None]
HEARTBEAT_SECONDS = 30


def _heartbeat(label: str, running: dict, lock: threading.Lock, stop: threading.Event) -> None:
    """Show that long runs are alive - an agentic review can take several minutes."""
    while not stop.wait(HEARTBEAT_SECONDS):
        with lock:
            active = sorted(running.items())
        if active:
            now = time.monotonic()
            parts = [f"{variant} run {index} ({int(now - t) // 60}:{int(now - t) % 60:02d})"
                     for (variant, index), t in active]
            print(f"  [{label}] still working: {', '.join(parts)}", file=sys.stderr, flush=True)


def home() -> Path:
    """Where sessions live: $COINFLIP_HOME or ~/.coinflip - never inside the user's project."""
    return Path(os.environ.get("COINFLIP_HOME") or Path.home() / ".coinflip") / "sessions"


def new_session_dir(out: Path, label: str, reviewer: reviewers.Reviewer) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out / f"{stamp}-{label}-{reviewer.slug}"
    suffix = 1
    while path.exists():
        suffix += 1
        path = out / f"{stamp}-{label}-{reviewer.slug}-{suffix}"
    path.mkdir(parents=True)
    return path


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def collect(
    session: Path,
    meta: dict,
    reviewer: reviewers.Reviewer,
    jobs_spec: list[tuple[str, str, int]],
    timeout: float,
    parallel: int,
    progress: Progress | None = None,
    cwd: Path | None = None,
) -> None:
    """jobs_spec: (variant, prompt text, number of runs)."""
    meta = {**meta, "coinflip": __version__, "reviewer": reviewer.spec, "argv": list(reviewer.argv),
            "started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "runs": []}
    for variant, text, _ in jobs_spec:
        (session / f"prompt-{variant}.txt").write_text(text, encoding="utf-8")
        meta.setdefault("prompts", {})[variant] = prompt.fingerprint(text)
    _write_json(session / "meta.json", meta)
    lock = threading.Lock()
    running: dict[tuple[str, int], float] = {}

    def one(variant: str, text: str, index: int) -> None:
        with lock:
            running[(variant, index)] = time.monotonic()
        try:
            answer = reviewers.ask(reviewer, text, timeout, str(cwd) if cwd else None)
        finally:
            with lock:
                running.pop((variant, index), None)
        name = f"{variant}-{index:02d}.txt"
        (session / name).write_text(answer.text, encoding="utf-8")
        with lock:
            meta["runs"].append({"variant": variant, "index": index, "file": name,
                                 "seconds": round(answer.seconds, 1), "exit_code": answer.exit_code,
                                 "error": answer.error})
            meta["runs"].sort(key=lambda r: (r["variant"], r["index"]))
            _write_json(session / "meta.json", meta)
            if progress:
                progress(variant, index, sum(1 for r in meta["runs"] if r["variant"] == variant), answer)

    tasks = [(variant, text, i) for variant, text, count in jobs_spec for i in range(1, count + 1)]
    stop = threading.Event()
    beat = threading.Thread(target=_heartbeat, args=(reviewer.spec, running, lock, stop), daemon=True)
    beat.start()
    try:
        with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
            for future in [pool.submit(one, *t) for t in tasks]:
                future.result()
    finally:
        stop.set()


def previous_findings(sessions_dir: Path, project_path: Path) -> set[tuple[str, int]]:
    """(file, line) of every finding earlier review sessions reported for this project."""
    spots: set[tuple[str, int]] = set()
    if not sessions_dir.is_dir():
        return spots
    target = str(project_path.resolve())
    for folder in sessions_dir.iterdir():
        results = folder / "results.json"
        if not results.is_file():
            continue
        try:
            data = json.loads(results.read_text(encoding="utf-8"))
            meta = load_meta(folder)
        except (ValueError, OSError):
            continue
        if data.get("mode") == "review" and meta.get("project_path") == target:
            spots.update((f["file"], f["line"]) for f in data.get("findings", []))
    return spots


def load_meta(session: Path) -> dict:
    path = session / "meta.json"
    if not path.is_file():
        raise FileNotFoundError(f"not a coinflip session folder: {session}")
    return json.loads(path.read_text(encoding="utf-8"))


def _runs(session: Path, meta: dict, variant: str, known: list[str], bench=None) -> list[metrics.Run]:
    runs = []
    for record in meta["runs"]:
        if record["variant"] != variant:
            continue
        run = metrics.Run(variant=variant, index=record["index"], seconds=record["seconds"], error=record["error"])
        if run.answered:
            text = (session / record["file"]).read_text(encoding="utf-8")
            run.findings, run.parsed = parse_answer(text, known)
            if bench is not None and variant == "buggy":
                run.hits, run.unmatched = match(run.findings, bench.bugs)
        runs.append(run)
    return runs


SNIPPET_CONTEXT = 2
MAX_SNIPPETS = 60


def _add_snippets(rows: list[dict], read) -> None:
    """Attach the lines around each finding, so reports can show the code itself."""
    cache: dict[str, list[str] | None] = {}
    for row in rows[:MAX_SNIPPETS]:
        if row["file"] not in cache:
            cache[row["file"]] = read(row["file"])
        lines = cache[row["file"]]
        if not lines or not 1 <= row["line"] <= len(lines):
            continue
        lo, hi = max(1, row["line"] - SNIPPET_CONTEXT), min(len(lines), row["line"] + SNIPPET_CONTEXT)
        row["snippet"] = [[n, lines[n - 1][:300]] for n in range(lo, hi + 1)]


def _project_reader(root: str | None):
    def read(rel: str) -> list[str] | None:
        if not root:
            return None
        path = Path(root) / rel
        try:
            return path.read_text(encoding="utf-8", errors="replace").splitlines() if path.is_file() else None
        except OSError:
            return None
    return read


def score(session: Path) -> dict:
    meta = load_meta(session)
    header = {k: meta.get(k) for k in ("coinflip", "reviewer", "started", "benchmark", "project", "diff",
                                       "scope_files")}
    header["session"] = session.name
    if meta["mode"] == "benchmark":
        bench, buggy_files, _ = benchmark.build(meta["benchmark_name"])
        if bench.label != meta["benchmark"]:
            raise benchmark.BenchmarkError(
                f"session used {meta['benchmark']} but this coinflip ships {bench.label} - results not comparable"
            )
        known = sorted(buggy_files)
        buggy = _runs(session, meta, "buggy", known, bench)
        clean = _runs(session, meta, "clean", known)
        summary = metrics.summarize_benchmark(bench, buggy, clean)
        _add_snippets(summary.get("bugs", []), lambda rel: buggy_files.get(rel, "").splitlines() or None)
    else:
        runs = _runs(session, meta, "review", meta["files"])
        summary = metrics.summarize_review(runs, meta.get("canaries", []))
        _add_snippets(summary.get("findings", []), _project_reader(meta.get("project_path")))
    result = {**header, **summary, "run_records": meta["runs"]}
    _write_json(session / "results.json", result)
    (session / "report.html").write_text(report_html.render(result, session), encoding="utf-8")
    return result
