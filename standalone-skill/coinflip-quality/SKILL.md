---
name: coinflip-quality
description: Measure the code quality of the user's project without AI - complexity, function size, duplication, readability, documentation and hygiene (commented-out code, debug leftovers, empty catch blocks) - as one score from 0 to 100, then explain the worst spots. Use when the user runs /coinflip-quality or asks how clean, readable, maintainable or well documented their code is.
---

# coinflip quality

coinflip measures code quality with fixed rules, not with AI: the same code always gets the same
score, it takes seconds and costs nothing. Your job: run it, then explain the result briefly and
point to the spots that matter most.

## 1. Check the prerequisites

Run `uv --version`. If it fails, tell the user in one sentence that coinflip needs `uv` (a small
Python tool runner) and **ask before installing it**:
- macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

## 2. Measure

Run from the project root. Use what the user asked for: the whole project by default, a folder
with `--include "src/payments/**"`, or only changed files with `--diff main` / `--diff HEAD`.

```bash
uvx --from git+https://github.com/DerChris2011/coinflip@PINNED_COMMIT coinflip quality <options> -f json
```

Key fields: `score` (0-100) and `grade` (A-E), `parts` (complexity, size, duplication,
readability, documentation, hygiene - each 0-100), `readability_parts`, `files`, `nloc`,
`documented_functions` / `public_functions`, `duplicated_percent`, `commented_out_lines`,
`hygiene_counts`, `test_ratio`, and `offenders` (lists of the worst spots: `complex`, `long`,
`deeply_nested`, `undocumented`, `commented_out`, `issues`, `unclear_names`, `duplicated`,
each with `file` and `line`).

## 3. Present

Short and in the user's language:

1. **Headline:** "Code quality 74/100 (C) - strong on duplication and hygiene, weak on
   documentation (14 %)."
2. **The two or three weakest parts**, each with what it means and the worst spots from
   `offenders` as `file:line` (open a few of them to make sure the advice fits the code).
3. **Quick wins:** commented-out code and debug leftovers can usually be deleted right away;
   name them.

Explain once that the score is deterministic: it comes from fixed rules (no AI), so it is a fair
trend line, and every review with `/coinflip` records it automatically. Test files only
count for the test ratio.

## 4. Report and next step

For a shareable HTML page: `coinflip quality --open` writes it to `~/.coinflip/quality/` and
opens it (`-f html -o <path>` to save it elsewhere - ask first, never into the project unasked).
For CI: `coinflip quality --fail-under 70` exits with code 1 below the threshold.

Offer one next step: clean up the quick wins, refactor the most complex function, or add the
missing docstrings - ask before editing any file.
