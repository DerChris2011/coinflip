"""Reviewer adapters. coinflip never talks to an AI API itself - it drives
whatever command-line tool is already installed and logged in.

Spec syntax:
  claude                  Claude Code CLI        (claude:<model> to pick a model)
  codex                   OpenAI Codex CLI       (codex:<model>)
  gemini                  Gemini CLI             (gemini:<model>)
  ollama:<model>          local model via Ollama
  cmd:<command line>      anything that reads the prompt on stdin and answers on stdout
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass


class ReviewerError(Exception):
    pass


@dataclass(frozen=True)
class Reviewer:
    spec: str
    argv: tuple[str, ...]
    agentic: bool = True  # can read files in its working directory itself

    @property
    def slug(self) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "-", self.spec.removeprefix("cmd:")).strip("-")[:60] or "reviewer"

    def available(self) -> bool:
        return shutil.which(self.argv[0]) is not None


@dataclass(frozen=True)
class Answer:
    text: str
    seconds: float
    exit_code: int
    error: str | None


def _preset(name: str, model: str | None) -> list[str]:
    if name == "claude":
        # Read-only tools only: the reviewer may look around, never change anything.
        return (["claude", "-p", "--output-format", "text"] + (["--model", model] if model else [])
                + ["--allowedTools", "Read", "Grep", "Glob", "LS"])
    if name == "codex":
        return ["codex", "exec", "--sandbox", "read-only", "--skip-git-repo-check"] + (["-m", model] if model else []) + ["-"]
    if name == "gemini":
        return ["gemini"] + (["-m", model] if model else [])
    if name == "ollama":
        if not model:
            raise ReviewerError("ollama needs a model, e.g. ollama:qwen3-coder")
        return ["ollama", "run", model]
    raise ReviewerError(f"unknown reviewer '{name}' (use claude, codex, gemini, ollama:<model> or cmd:<command>)")


def _split(command: str) -> list[str]:
    if os.name != "nt":
        return shlex.split(command)
    # Windows: keep backslashes in paths, only strip surrounding quotes.
    return [part[1:-1] if len(part) > 1 and part[0] == part[-1] and part[0] in "\"'" else part
            for part in shlex.split(command, posix=False)]


def parse(spec: str) -> Reviewer:
    if spec.startswith("cmd:"):
        argv = _split(spec[4:])
        if not argv:
            raise ReviewerError("cmd: needs a command")
        return Reviewer(spec=spec, argv=tuple(argv))
    name, _, model = spec.partition(":")
    return Reviewer(spec=spec, argv=tuple(_preset(name, model or None)), agentic=name != "ollama")


PRESET_NAMES = ("claude", "codex", "gemini", "ollama")


def default_spec() -> str | None:
    """The first agentic CLI that is installed."""
    for name in ("claude", "codex", "gemini"):
        if shutil.which(name):
            return name
    return None


def ask(reviewer: Reviewer, prompt: str, timeout: float, cwd: str | None = None) -> Answer:
    """Run the reviewer once. Without cwd it runs in an empty folder so it cannot peek at anything."""
    executable = shutil.which(reviewer.argv[0])
    if executable is None:
        raise ReviewerError(f"'{reviewer.argv[0]}' is not installed or not on PATH")
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="coinflip-") as empty:
        try:
            proc = subprocess.run(
                [executable, *reviewer.argv[1:]],
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=cwd or empty,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return Answer("", time.monotonic() - started, -1, f"timed out after {timeout:.0f}s")
    seconds = time.monotonic() - started
    error = None if proc.returncode == 0 else (proc.stderr.strip()[-500:] or f"exit code {proc.returncode}")
    return Answer(proc.stdout, seconds, proc.returncode, error)
