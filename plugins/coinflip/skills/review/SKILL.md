---
name: review
description: Review the user's code several times with coinflip and report every finding with a confidence score (consistent / coin flip / seen once), then verify the findings in the code. Use when the user runs /coinflip:review, mentions coinflip, or asks for a reliable, repeated or "double-checked" AI code review of their project or their changes.
---

# coinflip review

An AI code review run twice on the same code returns two different lists. coinflip runs the
review several times and scores each finding by how often it comes back. Your job: run it with
as little friction as possible, then turn the result into a short, trustworthy answer.

The user should not have to know any command. Do the steps below yourself and only ask when a
step says so.

## 1. Check the prerequisites

Run `uv --version`.

- If it works, continue.
- If it fails, coinflip cannot run yet. Tell the user in one sentence that coinflip needs `uv`
  (a small Python tool runner) and **ask before installing it**. Install command:
  - macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
  - Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
  After installing, a new shell may be needed; try `~/.local/bin/uv --version` (macOS/Linux) or
  `$HOME\.local\bin\uv.exe --version` (Windows) before giving up.

The reviewer is Claude Code itself (`claude`), which is already installed and logged in because
the user is talking to you through it.

## 2. Decide what to review

Use what the user asked for. If they named nothing, decide from the git state of the current
project and tell the user in one line what you chose:

1. Not a git repository: review the whole project.
2. Uncommitted changes (`git status --porcelain` is not empty): review those changes:
   `--diff HEAD`.
3. On a branch other than the default branch, with commits the default branch does not have:
   review the branch: `--diff <default-branch>`. Find the default branch with
   `git symbolic-ref --short refs/remotes/origin/HEAD` (strip `origin/`), else `main`, else `master`.
4. Otherwise: the whole project.

For a whole-project review, count the source files (`git ls-files | wc -l` as a rough guide).
Above about 300 files, ask whether to review only a folder (`--include "src/payments/**"`)
or the last commits (`--diff HEAD~3`) instead, because every run reads the project.

Other options the user may ask for:
- more or fewer runs: `-n 10` (default here: 5)
- measure how good the reviewer is on this code: `--canary 5` (plants 5 small bugs into a
  temporary copy; the user's files are never changed)

## 3. Run coinflip

Run from the project root. It takes several minutes (each run is a full review), so run it
**in the background** and check its output until it has finished. Never use a short timeout.

```bash
uvx --from git+https://github.com/DerChris2011/coinflip@e06c98f29a4c6004f4965231824a9f8b39c3a7c7 coinflip review <scope options> -n 5 -j 3 -r claude
```

`-j 3` runs three reviews in parallel. Tell the user roughly how long to expect (about
1-3 minutes per run, divided by the parallelism) and that their files are not touched:
coinflip reviews a temporary copy.

While it runs, each finished run prints a line such as
`[claude] review run  2   84.0s  7 findings`. If runs print `FAILED` or `no readable JSON`,
show the user the message and stop instead of retrying in a loop.

## 4. Read the result

When it has finished, get the structured result of the latest session:

```bash
uvx --from git+https://github.com/DerChris2011/coinflip@e06c98f29a4c6004f4965231824a9f8b39c3a7c7 coinflip report -f json
```

Key fields: `distinct_findings`, `consistent`, `coin_flips`, `once`, `single_run_coverage`
(how much of all findings one review alone showed, in percent), `stability`, `findings`
(each with `file`, `line`, `severity`, `title`, `hits`, `status`), and `canary` when canaries
were planted.

## 5. Verify and present

Open every **consistent** and every **coin flip** finding in the code (read a few lines around
`file:line`) and judge whether it is a real problem. Findings seen once: look at the high or
critical ones only.

Present the result short and in the user's language:

1. **One line headline**, for example: "12 findings over 5 reviews - 4 come back every time,
   6 are a coin flip. A single review showed only 48 % of them."
2. **Fix these** - consistent findings you confirmed in the code: `file:line`, what is wrong,
   in one sentence each. Mention it if you think a consistent finding is a false alarm.
3. **Worth a look** - coin flips you judge to be real, with a short reason. Say plainly that
   this part is your assessment, while the counts come from coinflip.
4. **Probably noise** - one line summarising the rest.
5. With canaries: "On your code the reviewer found the planted bugs reliably in X % of cases,
   Y were a coin flip." Never list canaries as findings - they only exist in the temporary copy.

Never invent findings that are not in the report, and never change the counts.

## 6. The HTML report and the next step

Every session already has a self-contained HTML report (the coin-flip matrix: one row per
finding, one column per run, plus code snippets and every raw answer). coinflip printed its
path as `HTML report: ...`. Mention it in one line and offer to open it:

```bash
uvx --from git+https://github.com/DerChris2011/coinflip@e06c98f29a4c6004f4965231824a9f8b39c3a7c7 coinflip report --open
```

If the user wants to keep or share it, ask where to save it. Do not write it into the project
unasked. To save a copy: `coinflip report -f html -o <path>/coinflip-report.html`.
For a pull request or issue: `coinflip report -f markdown`.

Then offer one of these, whichever fits:
- fix the confirmed findings (ask before editing any file)
- measure the reviewer on this code with `--canary 5`

## Rules

- Do not edit the user's files during the review. coinflip itself only works on a temporary copy.
- Every run costs a full review on the user's plan; do not start extra runs without asking.
- Sessions are stored in `~/.coinflip/sessions`; nothing is written into the project.
