"""Prepare a review session on the user's own project."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from coinflip import canary, prompt, workspace

DEFAULT_MAX_INLINE_CHARS = 300_000
MAX_DIFF_CHARS = 200_000


@dataclass
class Plan:
    meta: dict
    prompt: str
    copy: Path
    scope: list[str] = field(default_factory=list)

    def cleanup(self) -> None:
        shutil.rmtree(self.copy, ignore_errors=True)


def _scope(root: Path, files: list[str], includes: tuple[str, ...], ref: str | None) -> list[str]:
    if ref is None:
        selected = workspace.scope(files, includes)
        if not selected:
            raise workspace.WorkspaceError("no source files found (use --include to select files)")
        return selected
    changed = workspace.changed_files(root, ref)
    selected = workspace.scope(changed, includes)
    if not selected:
        raise workspace.WorkspaceError(
            f"no changed source files compared to '{ref}' - nothing to review. "
            "Run `coinflip review` for the whole project, or `--diff HEAD~3` for the last 3 commits.")
    return selected


def _plant(root: Path, plan_copy: Path, scope: list[str], count: int, seed: int,
           diff: str | None, avoid: set[tuple[str, int]]) -> tuple[list[dict], str | None]:
    prefer = workspace.changed_lines(root, diff, scope) if diff is not None else None
    planted = canary.plant(plan_copy, scope, count, seed, prefer, avoid)
    canaries = [{"id": f"canary-{i}", **c.to_dict()} for i, c in enumerate(planted, start=1)]
    if diff is not None and planted:
        replacements = {}
        for c in planted:
            replacements[(c.file, c.line)] = (plan_copy / c.file).read_text(
                encoding="utf-8", errors="replace").split("\n")[c.line - 1]
        diff = workspace.patch_diff(diff, replacements)
    return canaries, diff


def prepare(root: Path, includes: tuple[str, ...] = (), ref: str | None = None, canaries: int = 0,
            seed: int = 1, inline: bool = False, max_chars: int = DEFAULT_MAX_INLINE_CHARS,
            avoid: set[tuple[str, int]] | None = None) -> Plan:
    root = root.resolve()
    if not root.is_dir():
        raise workspace.WorkspaceError(f"not a folder: {root}")
    files = workspace.list_files(root)
    scope = _scope(root, files, includes, ref)
    diff = workspace.diff_text(root, ref, scope) if ref is not None else None
    if diff is not None and len(diff) > MAX_DIFF_CHARS:
        raise workspace.WorkspaceError(
            f"the diff has {len(diff):,} characters - review a smaller change or narrow it with --include")

    copy = workspace.make_copy(root, files)
    try:
        planted, diff = _plant(root, copy, scope, canaries, seed, diff, avoid or set()) if canaries else ([], diff)
        if inline:
            content = workspace.read_files(copy, scope)
            size = sum(len(t) for t in content.values())
            if size > max_chars:
                raise workspace.WorkspaceError(
                    f"{len(scope)} files with {size:,} characters are too much to send inline - "
                    "use --diff, narrow with --include, or use an agentic reviewer (claude, codex, gemini)")
            text = prompt.inline_review(content, diff)
        else:
            text = prompt.agent(scope, len([f for f in files if workspace.is_source(f)]), diff)
    except Exception:
        shutil.rmtree(copy, ignore_errors=True)
        raise

    commit, dirty = workspace.git_state(root)
    meta = {
        "mode": "review",
        "project": root.name,
        "project_path": str(root),
        "commit": commit,
        "dirty": dirty,
        "diff": ref,
        "includes": list(includes),
        "inline": inline,
        "scope_files": len(scope),
        "files": files,
        "canaries": planted,
        "canary_seed": seed if canaries else None,
        "canary_avoided_spots": len(avoid or ()),
    }
    return Plan(meta=meta, prompt=text, copy=copy, scope=scope)
