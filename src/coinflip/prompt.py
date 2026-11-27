"""Review prompts. Within one session every run gets exactly the same text."""

from __future__ import annotations

import hashlib

TASK = """\
You are a senior engineer doing a code review.
Find real bugs and security vulnerabilities: anything that is incorrect, unsafe,
or would misbehave in production. Do not report style, naming, missing docs or
missing tests.
"""

OUTPUT = """\
Answer with ONLY a JSON array inside a ```json fenced block, nothing else.
Each element must be an object with these keys:
  "file":     path relative to the project root
  "line":     the line number where the problem is
  "severity": "critical" | "high" | "medium" | "low"
  "title":    one short sentence describing the problem
If you find no problems, answer with an empty array: ```json [] ```
"""


def _numbered(files: dict[str, str]) -> list[str]:
    parts = []
    for rel in sorted(files):
        parts.append(f"===== FILE: {rel} =====")
        lines = files[rel].splitlines()
        width = len(str(len(lines)))
        parts.extend(f"{n:>{width}} | {line}" for n, line in enumerate(lines, start=1))
        parts.append("")
    return parts


def render(files: dict[str, str]) -> str:
    """Inline prompt: the whole code is part of the prompt (benchmark, local models)."""
    return "\n".join([TASK, OUTPUT, "The code base:", "", *_numbered(files)])


def agent(scope: list[str], all_files: int, diff: str | None) -> str:
    """Prompt for agentic CLIs that read the project in their working directory themselves."""
    parts = [TASK, "The project is in your current working directory. Read the files you need. "
             "Do not modify any file.", ""]
    if diff is not None:
        parts += ["Review THIS CHANGE (git diff of the working tree against the base). "
                  "Report problems in the changed code, or problems the change causes elsewhere. "
                  "Line numbers refer to the current files.", "", "```diff", diff, "```", ""]
    elif len(scope) < all_files:
        parts += [f"Review these {len(scope)} files (other files only for context):", *scope, ""]
    else:
        parts += [f"Review the whole project ({len(scope)} source files).", ""]
    parts.append(OUTPUT)
    return "\n".join(parts)


def inline_review(files: dict[str, str], diff: str | None) -> str:
    """Prompt with the code inside it, for reviewers that cannot read files."""
    parts = [TASK]
    if diff is not None:
        parts += ["Review THIS CHANGE. The full current content of the changed files follows the diff.",
                  "", "```diff", diff, "```", ""]
    parts += [OUTPUT, "The code:", "", *_numbered(files)]
    return "\n".join(parts)


def fingerprint(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
