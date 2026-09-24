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
| `templates/` | A lens file and a config to copy into a repository. |

Both scripts use only the Python standard library (3.9 or later). `check` also needs an authenticated `gh`.

## Set up a repository

Both files are optional. Without them the generic lenses and default paths apply.

1. Copy `templates/pr-grade-lenses.md` to `.claude/pr-grade-lenses.md` and restate each lens for the codebase: its real bounded resources, the callers people forget, what a single transaction covers, how a finding is proven. A lens heading added there becomes one `check` requires.
2. Copy `templates/pr-grade.json` to `.claude/pr-grade.json` and list the silent-failure paths: code where a defect would pass every test, such as checks, gates, persistence, CI, and hooks. `silentCommand` can print more, one path per line, when the repository already keeps that list somewhere. A key you set replaces its default list whole, which is why the template repeats the defaults. `requireGrade.branches`, a regex, limits the check to matching branches; leave it out to check every ready PR.

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

To make it a merge gate, run `grade_block.py check` in CI on `opened`, `edited`, `synchronize`, `reopened`, and `ready_for_review`. Drafts are skipped.

## Tests

```
python3 -m unittest discover -s tests -v
```
