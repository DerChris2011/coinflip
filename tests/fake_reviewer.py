"""A stand-in reviewer for tests: reads the prompt on stdin, answers like an LLM CLI would.

Benchmark prompts: it cheats (knows the planted bugs). The first 5 bugs are always found,
the next 5 in about half of the runs, the rest never.
Review prompts: lines containing SUSPICIOUS are always reported, lines containing MAYBE
in about half of the runs. Canaries are spotted by comparing the copy in its working
directory with the original project (FAKE_ORIG) and reported in about half of the runs.

Env: FAKE_STATE (run counter file), FAKE_MODE = ok | garbage | fail, FAKE_ORIG
"""

import json
import os
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from coinflip.benchmark import build  # noqa: E402

prompt = sys.stdin.read()
if os.environ.get("FAKE_SLEEP"):
    import time
    time.sleep(float(os.environ["FAKE_SLEEP"]))
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "fail":
    print("rate limit exceeded", file=sys.stderr)
    raise SystemExit(1)
if mode == "garbage":
    print("I looked at the code and it seems fine to me!")
    raise SystemExit(0)

state = Path(os.environ["FAKE_STATE"])
with state.open("a") as fh:  # appends are atomic, so parallel runs never crash
    fh.write("x")
run = len(state.read_text())
rng = random.Random(run)


def bench_findings() -> list[dict]:
    if "pbkdf2_hmac" in prompt:  # clean twin
        return [{"file": "app/db.py", "line": 12, "severity": "low", "title": "connection not pooled"}]
    bench, _, _ = build("pyshop")
    found = []
    for n, bug in enumerate(bench.bugs):
        chance = 1.0 if n < 5 else 0.5 if n < 10 else 0.0
        if rng.random() < chance:
            found.append({"file": "./" + bug.file, "line": str(bug.start_line + 1), "severity": "high",
                          "title": bug.title})
    found.append({"file": "app/api.py", "line": 5, "severity": "low", "title": "noise"})
    return found


def marker_findings(files: dict[str, list[str]]) -> list[dict]:
    found = []
    for rel in sorted(files):
        for n, line in enumerate(files[rel], start=1):
            if "SUSPICIOUS" in line or ("MAYBE" in line and rng.random() < 0.5):
                found.append({"file": rel, "line": n, "severity": "medium", "title": f"issue at {rel}:{n}"})
    return found


def canary_findings() -> list[dict]:
    orig = os.environ.get("FAKE_ORIG")
    if not orig:
        return []
    found = []
    for path in sorted(Path.cwd().rglob("*.py")):
        rel = path.relative_to(Path.cwd()).as_posix()
        original = Path(orig, rel)
        if not original.is_file():
            continue
        for n, (a, b) in enumerate(zip(path.read_text().split("\n"), original.read_text().split("\n")), start=1):
            if a != b and rng.random() < 0.5:
                found.append({"file": rel, "line": n, "severity": "high", "title": "wrong condition"})
    return found


if "The project is in your current working directory" in prompt:
    files = {p.relative_to(Path.cwd()).as_posix(): p.read_text().split("\n")
             for p in Path.cwd().rglob("*.py")}
    findings = marker_findings(files) + canary_findings()
elif "===== FILE: app/auth.py =====" in prompt:
    findings = bench_findings()
else:  # inline review: rebuild the files from the numbered listing
    files: dict[str, list[str]] = {}
    current = None
    for line in prompt.split("\n"):
        header = re.match(r"===== FILE: (.+) =====", line)
        if header:
            current = header.group(1)
            files[current] = []
        elif current and re.match(r"\s*\d+ \| ", line):
            files[current].append(line.split(" | ", 1)[1])
    findings = marker_findings(files)

print("Here is my review.\n```json\n" + json.dumps(findings, indent=1) + "\n```")
