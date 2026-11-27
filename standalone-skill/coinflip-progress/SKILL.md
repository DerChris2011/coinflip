---
name: coinflip-progress
description: Compare several coinflip reviews of the current project (review, fix, review again) and build an HTML progress report with charts that shows how the findings went down. Only when the user explicitly runs /coinflip-progress.
disable-model-invocation: true
---

# coinflip progress

The user has reviewed this project with coinflip more than once - typically review, fix, review
again - and wants to see whether the fixes worked. coinflip compares the chosen review sessions
and writes a self-contained HTML report with charts: findings per review by confidence and by
severity, how reliable each review was, the code quality score per review (measured without AI),
what happened to each finding (fixed, still there, new), and findings per file.

Run every command from the project root.

## 1. Check the prerequisites

Run `uv --version`. If it fails, tell the user coinflip needs `uv` and ask before installing it
(macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`, Windows PowerShell:
`powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`).

## 2. Find the reviews of this project

```bash
uvx --from git+https://github.com/DerChris2011/coinflip@e1b7746029480877fe8b2b10c25c3f3c126a95d4 coinflip sessions -f json
```

Each entry has `number`, `started` (UTC - show it in the user's local time), `commit`
(`dirty` = uncommitted changes at review time), `scope`, `runs`, `canaries`,
`distinct_findings`, `consistent`, `coin_flips`, `once`.

**Fewer than 2 reviews:** stop here. Say that a progress report needs at least two reviews of
this project and suggest the loop: `/coinflip`, fix what it found, `/coinflip`
again. Do not create a report.

## 3. Ask which reviews to compare

If the user already said which ones (for example `/coinflip-progress 1 3` or "the last two"),
use that and skip the question.

Otherwise show the list as a short table (number, date and time, commit, scope, runs, findings,
consistent) and ask - with the question tool if you have one - which reviews to compare.
Good options:
- all reviews
- the last two
- the first and the last (the overall effect of all fixes)
- specific numbers (free answer)

Point out anything that makes a comparison less fair, in one line each: different scopes
(whole project vs. a diff), different numbers of runs, canaries in a review, a review of
uncommitted work. Recommend comparing reviews with the same scope and run count.

## 4. Build the report

```bash
uvx --from git+https://github.com/DerChris2011/coinflip@e1b7746029480877fe8b2b10c25c3f3c126a95d4 coinflip progress <numbers> --open
```

Without numbers it compares all reviews; `--last 2` compares the last two. It prints a short
summary, writes the HTML report to `~/.coinflip/progress/` (path in the line
`progress report: ...`) and opens it. Save it elsewhere only if the user asks:
add `-o <path>/coinflip-progress.html`.

## 5. Present the result

Short and in the user's language:

1. **Headline:** consistent findings before → after and all findings before → after, with the
   change in percent, for example "Consistent findings 3 → 0, all findings 8 → 4 (-50 %)."
2. **Fixed:** how many findings of the first review are gone, and how many of them were consistent.
3. **Still there** and **new:** name the consistent ones that are still there and any new
   high-severity ones (`coinflip progress <numbers> -f json` gives the full list in `findings`,
   each with `fate`, `file`, `line`, `title`, `cells`).
4. **Code quality** before → after, when the JSON has `summary.quality_before` and
   `summary.quality_after` (for example "Code quality 68 → 74"), and which part changed most
   (`sessions[].quality.parts`). Reviews made before coinflip 0.4.0 have no quality score.
5. The warnings coinflip printed, if any.
6. The path of the HTML report.

Say once that following a single finding from one review to the next is a best guess (lines move
when code changes, wording differs between runs), while the counts per review are exact.

Offer one next step: look at the consistent findings that are still there, or run
`/coinflip` again after the next fix. Do not edit any file unless the user asks.
