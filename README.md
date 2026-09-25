# pr-grade

A Claude Code plugin that grades a change the way an external reviewer will, before you push it.

A correctness review asks whether the code does the right thing. The defects that get past one are about what happens to the work you stopped looking at, and who else shares the resource you just used. pr-grade applies eight lenses aimed at that class, decides how many agents should grade from what the diff touches, and writes a score into the PR body where a check can refuse a PR that was never graded or changed since.

## Install

```
/plugin marketplace add JakemoCode/pr-grade
/plugin install pr-grade@jakemocode
```

The repository is private for now, so the first command needs git access to it.

## What you get

| Piece | What it does |
|---|---|
| `pr-grade` skill | The eight lenses, the proof rule, the score, and the report shape. Run `/pr-grade` before opening a PR or marking one ready. |
| `pr-grade:grade` agent | Applies named lenses to one change and proves each finding by running something. Sonnet at `xhigh`, 50 turns, read-only on the repository. |
| `scripts/grade_mode.py` | Picks the grading mode from the branch's changed files: `in-thread`, one `subagent`, or `fan-out` with one agent per lens group. |
| `scripts/grade_block.py check <pr>` | Refuses a PR whose `## Grade` block is missing, below 5/5, missing a lens, in a cheaper mode than its files need, or older than its code. |
| `scripts/check_then_act.py` | Lists the check-then-act windows (L7) in the functions a branch changed, for the grader to clear or prove. |
| `templates/` | A lens file, a config, and a CI workflow to copy into a repository. |

The scripts use only the Python standard library (3.9 or later). `check` also needs an authenticated `gh`, and the check-then-act scanner needs Node for TypeScript and JavaScript. It reads Python with the standard library.

## A real grade

This is from the pull request that added the grade check to a repository's own merge flow, with its file names generalized. The change made the local `check-pr` command refuse a PR without a current grade. `/code-review high` came back with ten findings, and none of them was about CI.

`grade_mode.py` put the change at `fan-out`, so three `grade` agents read it. The timing group (L1, L2, L7) returned:

```
Score: 4/5
Blocking: The CI job that reruns on every push never reads the grade, so a commit
pushed after the PR is marked ready merges ungraded.

P1 scripts/check.py:468 - Grade freshness has no backstop after ready
The grade is checked only by the command the author runs before marking the PR
ready. The workflow that reruns on every push calls a different entry point, which
never reads the grade block.
Ran the CI entry point with gh faked to return a PR whose grade is stale (a code
file changed after the graded commit):
  PR #197: closing references match
  passed: the stale grade was not caught

Verified and clear: L1, L2
Could not verify: none
```

The whole enforcement would have been local-only. The fix made the CI entry point grade every ready PR. Re-grading that fix, as the skill requires, found the next defect: the check read the PR's file list in a second call after reading its head SHA, so a push landing between the two paired new files with an old comparison (L7). Every test was green throughout.

## Set up a repository

Both files are optional. Without them the generic lenses and default paths apply.

1. Copy `templates/pr-grade-lenses.md` to `.claude/pr-grade-lenses.md` and restate each lens for the codebase: its real bounded resources, the callers people forget, what a single transaction covers, how a finding is proven. A lens heading added there becomes one `check` requires.
2. Copy `templates/pr-grade.json` to `.claude/pr-grade.json` and list the silent-failure paths: code where a defect would pass every test, such as checks, gates, persistence, CI, and hooks. `silentCommand` can print more, one path per line, when the repository already keeps that list somewhere. A key you set replaces its default list whole, which is why the template repeats the defaults. `requireGrade.branches`, a regex, limits the check to matching branches; leave it out to check every ready PR.
3. To enforce the grade, copy `templates/pr-grade-check.yml` to `.github/workflows/pr-grade.yml` and make its check required. It fetches the checker from a pinned pr-grade ref rather than from your repository, so a pull request cannot edit the check that judges it. While this repository is private, it also needs a `PR_GRADE_TOKEN` secret that can read it.

## How the mode is picked

| | `fanOutAbove` code files or fewer (default 4) | more |
|---|---|---|
| no silent-failure file | in-thread | one subagent |
| a silent-failure file | one subagent | fan-out |

Tests, docs, and lockfiles do not count toward size. Uncommitted and untracked work counts. Both sides of a rename count, so a rename can raise the mode and never lowers it.

## The grade block

```
## Grade

Mode: subagent
Graded: <full SHA of the last commit graded>
Score: 5/5
Blocking: nothing
Verified and clear: L1, L2, L3, L4, L5, L6, L7, L8
Could not verify: none
```

The score counts P1 and P2 findings; a P3 is a note. `check` compares `Graded` with the PR head through GitHub and refuses when a file the grade covers changed after it. It counts only the PR's own files, so merging the base branch does not trip it. It fails closed when GitHub's lists are cut off (3000 PR files, 300 compared files) or the PR moves during the check. The config and lens file come from the base branch, so a PR cannot loosen the rules it is checked against, and both are silent files, so changing them raises the grade.

The workflow template runs `check` on `opened`, `edited`, `synchronize`, `reopened`, and `ready_for_review`, so a commit pushed after the grade fails until it is graded, and a block pasted into the body is read at once. Drafts are skipped.

## Check-then-act windows

`check_then_act.py` finds the L7 shape in the functions a branch changed: a check on something read, then an `await`, a transaction boundary, or a configured call to another system, then a write the check was meant to guard. A grader clears each one by naming what makes it harmless, or proves the race. The list never gates anything: it exits 0 whenever it runs, and an empty list clears nothing.

```
check-then-act candidates (L7): 1 in 6 functions the branch touched. Each is a window to judge, not a finding.

src/gates/completion.ts:44  CompletionGate.evaluate
  check  51   if (recorded !== undefined) return { evaluation: recorded, reused: true };    reads options.gates.forExecution (48), same store as write 68
  gap    55   await  await options.snapshots.capture({ ...
  write  58   options.runs.appendSnapshotAdvanced
  write  68   options.gates.ensureGateEvaluation
```

That output is the real window from the "real grade" repository, run against the commit that introduced it: two concurrent evaluations both pass the lookup at line 51. Across that repository's 858 functions the scanner lists 13 windows. Four are a filesystem check before an await in its setup commands, and four re-check the state after the gap through the class's own method, which the scanner lists because it cannot tell that from a guard on an unrelated flag. On Python, saleor's checkout, order, and payment packages (986 functions, at commit `5ff5648`) give 7 windows, most of them a batch check made before the `transaction.atomic()` block that writes the batch.

- `--whole` scans every function in the changed files, and paths scan those files or directories whole. `--json` gives the same list as data.
- TypeScript and JavaScript are parsed with the repository's own `typescript` package, or the one `PR_GRADE_TYPESCRIPT` names. Without either, those files are skipped with a notice.
- Python is parsed with the `ast` module of the `python3` running the scanner. A file in newer syntax than that interpreter reads is skipped with a notice to run a newer one. `with transaction.atomic()`, `@transaction.atomic`, and `async with <lock>` are read as the transaction and lock scopes they are.
- Any other changed file the scanner cannot read is listed as skipped, unless it is a data format such as JSON or YAML.
- The name lists (`writes`, `secondReads`, `transactions`, `locks`, `ignore`) live under `checkThenAct` in `.claude/pr-grade.json`.

## Tests

```
PR_GRADE_TYPESCRIPT=<path>/node_modules/typescript python3 -m unittest discover -s tests -v
```

Without `PR_GRADE_TYPESCRIPT` the TypeScript tests are skipped. CI installs TypeScript for them and sets `PR_GRADE_REQUIRE_TS=1`, so a missing compiler fails the run instead.
