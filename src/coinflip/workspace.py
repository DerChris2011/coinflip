"""The user's repository: which files to review, what changed, and a throw-away copy.

coinflip never touches the user's working tree. Every review session runs on a
temporary copy (tracked + untracked-but-not-ignored files), which is deleted afterwards.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from fnmatch import fnmatch
from pathlib import Path

SOURCE_EXTENSIONS = frozenset(
    "py js mjs cjs ts tsx jsx vue java kt kts cs go rs php rb swift scala c h cc cpp hpp sql sh".split()
)
SKIP_DIRS = frozenset(
    "node_modules vendor _vendor .venv venv __pycache__ dist build out target coverage .git .next .nuxt".split()
)
MAX_FILE_BYTES = 2_000_000
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class WorkspaceError(Exception):
    pass


def _git(root: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)
    except FileNotFoundError:
        return None
    return proc.stdout.decode("utf-8", "replace") if proc.returncode == 0 else None


def is_git(root: Path) -> bool:
    return _git(root, "rev-parse", "--is-inside-work-tree") is not None


def git_state(root: Path) -> tuple[str | None, bool | None]:
    """(short commit, has uncommitted changes) - so reviews can be told apart before/after a fix."""
    commit = _git(root, "rev-parse", "--short", "HEAD")
    if commit is None:
        return None, None
    return commit.strip(), bool((_git(root, "status", "--porcelain") or "").strip())


def list_files(root: Path) -> list[str]:
    """All files of the project: git-tracked + untracked-not-ignored, or a plain walk."""
    out = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if out is not None:
        names = [p for p in out.split("\0") if p]
    else:
        names = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            names += [Path(dirpath, f).relative_to(root).as_posix() for f in filenames]
    return sorted({n for n in names if (root / n).is_file() and not _in_skipped_dir(n)})


def _in_skipped_dir(rel: str) -> bool:
    return any(part in SKIP_DIRS for part in rel.split("/")[:-1])


def is_source(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    if name.endswith((".min.js", ".d.ts")) or "." not in name:
        return False
    return name.rsplit(".", 1)[-1].lower() in SOURCE_EXTENSIONS


def scope(files: list[str], includes: tuple[str, ...]) -> list[str]:
    if includes:
        return [f for f in files if any(fnmatch(f, pattern) for pattern in includes)]
    return [f for f in files if is_source(f)]


def changed_files(root: Path, ref: str) -> list[str]:
    if not is_git(root):
        raise WorkspaceError("--diff needs a git repository")
    if _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}") is None:
        raise WorkspaceError(f"unknown git reference '{ref}'")
    changed = (_git(root, "diff", "--name-only", "--relative", ref) or "").splitlines()
    untracked = (_git(root, "ls-files", "--others", "--exclude-standard") or "").splitlines()
    return sorted({p for p in changed + untracked if p and (root / p).is_file()})


def diff_text(root: Path, ref: str, files: list[str]) -> str:
    tracked = _git(root, "diff", "--relative", "-U3", ref, "--", *files) or ""
    untracked = set((_git(root, "ls-files", "--others", "--exclude-standard") or "").splitlines())
    new = [f"New file (not yet committed): {f}" for f in files if f in untracked]
    return "\n".join(filter(None, [tracked.rstrip(), *new]))


def changed_lines(root: Path, diff: str, files: list[str]) -> dict[str, set[int]]:
    """Line numbers (in the working tree) of added/changed lines, per file."""
    result: dict[str, set[int]] = {}
    current = None
    line_no = 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[4:].strip()
            current = path[2:] if path.startswith("b/") else None
            continue
        hunk = _HUNK.match(raw)
        if hunk:
            line_no = int(hunk.group(1))
            continue
        if current is None or raw.startswith("---"):
            continue
        if raw.startswith("+"):
            result.setdefault(current, set()).add(line_no)
            line_no += 1
        elif not raw.startswith("-"):
            line_no += 1
    for f in files:  # untracked files are new as a whole
        if f not in result and f"New file (not yet committed): {f}" in diff:
            count = (root / f).read_text(encoding="utf-8", errors="replace").count("\n") + 1
            result[f] = set(range(1, count + 1))
    return result


def patch_diff(diff: str, replacements: dict[tuple[str, int], str]) -> str:
    """Rewrite added/context lines of a diff at (file, new line number) - keeps the diff
    consistent with a copy whose lines were changed (canaries)."""
    out = []
    current = None
    line_no = 0
    for raw in diff.split("\n"):
        if raw.startswith("+++ "):
            path = raw[4:].strip()
            current = path[2:] if path.startswith("b/") else None
        elif (hunk := _HUNK.match(raw)) is not None:
            line_no = int(hunk.group(1))
        elif current is not None and raw[:1] in ("+", " ") and not raw.startswith("+++"):
            key = (current, line_no)
            if key in replacements:
                raw = raw[0] + replacements[key]
            line_no += 1
        out.append(raw)
    return "\n".join(out)


def make_copy(root: Path, files: list[str]) -> Path:
    dest = Path(tempfile.mkdtemp(prefix="coinflip-ws-"))
    for rel in files:
        src = root / rel
        if src.stat().st_size > MAX_FILE_BYTES:
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
    return dest


def read_files(root: Path, files: list[str]) -> dict[str, str]:
    return {f: (root / f).read_text(encoding="utf-8", errors="replace") for f in files}
