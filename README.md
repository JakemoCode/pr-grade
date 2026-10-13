# pr-grade

A Claude Code plugin that grades a change the way an external reviewer will, before you push it.

A correctness review asks whether the code does the right thing. The defects that get past one are usually somewhere else: in the operation a timeout stopped waiting for, which still holds its connection; in the second caller nobody had in mind; in the records an older version wrote that the new reader now rejects. One pull request went through two design proposals, an adversarial breaker, two rounds of high-effort review, and six acceptance verifiers, and a bot reviewer still found a P1 in about a minute. Every pass had looked at the connection pool. None asked what happened to the read the timeout abandoned.

pr-grade asks those questions on purpose. It applies nine lenses aimed at that class of defect, requires every finding to be proven by running something, and writes a score into the PR body that a CI check can enforce.

## Install

```
/plugin marketplace add JakemoCode/pr-grade
/plugin install pr-grade@jakemocode
```

To update later, run this in the shell, then `/reload-plugins` in the session:

```
claude plugin marketplace update jakemocode && claude plugin update pr-grade@jakemocode
```

The scripts need Python 3.9 or later and nothing outside the standard library. The CI check and pull-request-body history also need an authenticated `gh`. The check-then-act scanner needs Node and the repository's own `typescript` package to read TypeScript and JavaScript; it reads Python with no extra setup.

## What it does

Run your usual code review first, fix what it proves, and commit. Then run `/pr-grade`.

1. **Prepare.** `grade_prep.py` reads the branch and picks a mode from what it touches: grade in the current thread, in one subagent, or fan out one subagent per lens group. It lists the callers of every function the branch modified, scans for check-then-act windows, replays recorded state through any changed reader, and makes a throwaway clone of the head commit for each grader to run proofs in.
2. **Grade.** Each `pr-grade:grade` agent applies its lenses to the diff. A finding counts only with a run behind it, usually a test that fails against the unfixed code. Anything a grader believes but cannot demonstrate goes under `Could not verify`.
3. **Merge and score.** `grade_block.py merge` combines the graders' reports and prints the lowest score the rules allow, plus a draft `## Grade` block for the PR body.
4. **Fix and re-grade.** Commit the fixes and grade the fix commits alone with `grade_prep.py --base <last graded commit>`. Repeat until a round finds no P1 or P2.
5. **Enforce.** `grade_block.py check <pr>` refuses a PR whose grade is missing, below 5/5, missing a lens, in a cheaper mode than its files need, or older than its code.

Hand the graders the review findings you declined, each with its reason. They treat those as known and spend their turns elsewhere. Any review works: Claude Code's `/code-review`, another plugin, a second model, or a person's comments. Without one, pr-grade still runs, but the graders spend turns re-finding what the review would have caught.

## Why it works

**Each lens is a defect that already escaped a careful review.** The lenses are questions a correctness review does not ask by habit. A repository adds its own whenever a reviewer finds something no lens names.

**A finding needs a run.** A failing test, or real inputs producing a wrong state. This keeps plausible-sounding findings out of the score and keeps real ones from being argued away.

**Silent-failure code gets more graders.** Code where a defect would pass every test (CI, gates, persistence, hooks, migrations) raises the mode, and so does code the config has not classified yet. Cheap grading is the failure nobody reports, so unknown code defaults to the expensive side.

**The fix is graded too.** A repair is the most likely place for the next defect. In the run the lenses came from, the fix for one finding introduced the next one three times in a row, with the test suite green each time.

**The grade is bound to a commit.** The block records the SHA it graded, and the check compares it with the PR head through GitHub. A commit pushed after the grade fails the check until it is graded. The check reads its config from the base branch, so a PR cannot loosen the rules it is checked against.

## The nine lenses

| Lens | What it asks |
|---|---|
| L1. Abandoned work | For every timeout, race, early return, cancel, and retry: what happens to the operation nobody is waiting for any more? It still runs, still holds what it held, and still writes when it lands. |
| L2. Shared bounded resources | For every pool, queue, lock, cache, and rate limit the change touches: what is the bound, who else uses it, and what happens when it is reached? |
| L3. Unguarded failure reach | Where does a failure in the new code go? A handler, job, or listener that a framework calls may log an uncaught error, drop it, or end the process. |
| L4. Every other caller | The change was written with one caller in mind. What does it do to the others? A default that was right when written can turn wrong once a change binds what it stood in for. |
| L5. State that outlives its reason | For everything written and never deleted (cache entries, listeners, pending rows, leases, timers, temp files): which line removes it? |
| L6. Contract drift | The behaviour moved. Did the docs, types, tests, error messages, tool descriptions, and registries that describe it move too? |
| L7. Check then act | When does a guard's state change, relative to the work it guards? An `await` or a second read between the check and the write lets every caller in that window through. |
| L8. Failure direction | For every new fallback, default, and failure path: which way does it fail, and why is that the cheap direction? |
| L9. Old state meets new code | What did earlier versions write that the new code will read? A changed reader must still read it, and a new kind of record must refuse the old values its rules exclude. |

The full definitions, with an example each, are in [the skill](skills/pr-grade/SKILL.md#3-the-nine-lenses).

## Set up a repository

Every step is optional. With no setup, the generic lenses and default paths apply, and every change to code grades in at least one subagent.

1. **Classify your code.** Copy [`templates/pr-grade.json`](skills/pr-grade/templates/pr-grade.json) to `.claude/pr-grade.json`. Under `silent`, list paths where a defect would pass every test. Under `ordinary`, list code a test would catch a defect in. Code that neither list names grades as silent.
2. **Restate the lenses.** Copy [`templates/pr-grade-lenses.md`](skills/pr-grade/templates/pr-grade-lenses.md) to `.claude/pr-grade-lenses.md` and rewrite each lens for your code: its real bounded resources, the callers people forget, how a finding is proven, and the fan-out groups. Where it differs from the skill, it wins. A lens heading you add becomes one the check requires.
3. **Declare your formats**, if the code reads records an earlier version wrote: ledgers, structured PR bodies, JSONL logs, config files. See [Declaring a format](#declaring-a-format). This is what gives L9 a replay to work from.
4. **Enforce the grade.** Copy [`templates/pr-grade-check.yml`](skills/pr-grade/templates/pr-grade-check.yml) to `.github/workflows/pr-grade.yml` and make its check required. It fetches the checker from a pinned pr-grade ref, so a PR cannot edit the check that judges it.

This repository grades itself the same way: see [`.claude/pr-grade.json`](.claude/pr-grade.json) and [`.github/workflows/pr-grade.yml`](.github/workflows/pr-grade.yml).

## Configuration

Every key in `.claude/pr-grade.json` is optional. A key you set replaces its default list whole, which is why the template repeats the defaults. Path patterns are globs: one with a `/` matches the whole path and its `*` crosses directories, and one without matches the file name.

| Key | Default | What it does |
|---|---|---|
| `lenses` | `.claude/pr-grade-lenses.md` | Where the repository's lens file lives. |
| `fanOutAbove` | `4` | Code files above this count raise the mode. |
| `silent` | CI workflows, hooks, Claude settings, migrations, and the two pr-grade files | Paths where a defect would pass every test. |
| `silentCommand` | none | A command that prints more silent paths, one per line, for a repository that already keeps that list. |
| `ordinary` | empty | Code a test would catch a defect in. `silent` wins over it. |
| `tests` | common test paths | Files that never count toward size and are never unclassified. |
| `notCode` | docs, Markdown, lockfiles, `LICENSE` | Files that never count toward size. |
| `countAsCode` | empty | Paths that count toward size even under `tests` or `notCode`, such as a data file in `docs/` that the code reads. |
| `checkThenAct` | built-in name lists | Overrides for the L7 scanner: `writes`, `secondReads`, `transactions`, `locks`, `ignore`. |
| `formats` | empty | Records the code reads, for the L9 replay. |
| `oldStateLens` | `L9` | The lens whose grader gets the replay's output, for a lens file that numbers its lenses differently. |
| `proofDir` | `node_modules`, `.venv` | Installed dependencies to link into each proof clone, each with the files that pin it and the command that installs it. |
| `requireGrade.branches` | every branch | A regex; the check only requires a grade on matching branches. |

### How the mode is picked

| | `fanOutAbove` code files or fewer | more |
|---|---|---|
| no silent or unclassified file | in-thread | one subagent |
| a silent or unclassified file | one subagent | fan-out |

Uncommitted and untracked work counts. Both sides of a rename count, so a rename can raise the mode but never lower it. Fan-out groups come from the lens file, or default to timing (L1, L2, L7), reach (L3, L4), and lifetime and contract (L5, L6, L8, L9).

### Declaring a format

A format tells `history_replay.py` how to find records that earlier versions wrote and how to run your reader over them. When a branch changes one of a format's `readers`, the script makes two runs:

- **Replay.** The base and head readers each read every recorded instance. Any instance one accepts and the other refuses is listed.
- **Transplant.** Each new kind of record the branch adds is placed in a recorded instance, and every value recorded under the old kinds is run through it. A value the new kind accepts but its rules say it must refuse is a finding.

```json
"formats": [{
  "name": "review ledger",
  "readers": ["scripts/ledger.py"],
  "check": ["python3", "{tree}/scripts/ledger.py", "--validate", "{instances}"],
  "instances": [{"lines": "data/reviews/*.jsonl"}, {"prBodies": 200}],
  "records": "\"pass\": \"(?P<kind>[a-z-]+)\"",
  "values": "\"disposition\": \"(?P<value>[a-z-]+)\""
}]
```

| Key | Meaning |
|---|---|
| `readers` | Globs over the paths whose change triggers the runs. |
| `check` | The command that runs a reader. `{tree}` is the checkout whose reader runs, and `{instances}` is a JSON file holding a list of instance texts. It prints a JSON list with one list of problems per instance, empty when the reader accepts it. |
| `instances` | Where records live: `{"files": glob}`, `{"lines": glob}` (each non-empty line), or `{"prBodies": N}` (the last N merged PR bodies, through `gh`). |
| `records`, `values` | Optional regexes. `records` has a `kind` group naming a record's kind, and `values` has a `value` group for a field under it. The transplant runs only when both are set. |
| `tree` | Paths the base reader needs from the base commit. Defaults to each reader's directory. |

A repository that declares no format pays nothing. A failing source or command prints a note and never stops a grade. The full contract is in the docstring of [`history_replay.py`](skills/pr-grade/scripts/history_replay.py).

## The grade block

```
## Grade

Mode: subagent
Graded: <full SHA of the last commit graded>
Score: 5/5
Blocking: nothing
Verified and clear: L1, L2, L3, L4, L5, L6, L7, L8, L9
Could not verify: none

P3 src/cache.ts:78 in Cache.evict - eviction log omits the key
<what breaks, with the inputs and the resulting state>
<the command that showed it, and its output>
```

| Score | Meaning |
|---|---|
| 5 | No P1 or P2 survives. Merge. |
| 4 | One issue that must be fixed first. |
| 3 | Several issues, or one whose repair is a design decision. |
| 2 | A lens could not be applied, so the change is not assessed. |
| 1 | A finding contradicts the change's stated purpose. |

P1 means fix before merge, P2 fix or justify, and P3 is a note that leaves the score at 5. A lens skipped by a rule the repository keeps (`grade_prep.py --skip L2`) is written `L2 (not applicable)`.

On a re-grade, `Graded` moves to the last fix commit and `Mode` keeps the whole branch's mode. The check compares `Mode` with every file the PR touches, so a block naming a fix round's lighter mode is refused.

## Enforcing it in CI

The workflow template runs `grade_block.py check` on `opened`, `edited`, `synchronize`, `reopened`, and `ready_for_review`. Drafts are skipped.

- It counts only the PR's own files, so merging the base branch into the PR does not trip it.
- It fails closed when GitHub's lists are cut off (3000 PR files, 300 compared files) or when the PR moves during the check.
- The config and lens file are silent files by default, so a PR that changes them needs a heavier grade.

## Scripts

All live in `skills/pr-grade/scripts/`. The skill calls them; you rarely run one by hand.

| Script | What it does |
|---|---|
| `grade_prep.py` | Prepares a grade in one call: mode, lens groups (minus any `--skip`), callers, the L7 scan, the L9 replay, proof clones, and every grader's prompt. |
| `grade_mode.py` | Picks the mode alone and prints the files that decided it. |
| `proof_dir.py` | Makes and removes the proof clones outside the repository, with dependencies linked in. Nothing is registered in the repository. |
| `check_then_act.py` | Lists check-then-act windows (L7) in the functions a branch changed: a check, then an `await`, transaction boundary, or external call, then the write the check guards. A grader clears each one or proves the race. It never gates anything, and an empty list clears nothing. |
| `history_replay.py` | Runs the replay and transplant for each declared format whose reader changed (L9). |
| `grade_block.py merge <root>` | Merges the graders' reports, flags two findings at one line and any finding on a line the diff did not change, and drafts the block with the lowest allowed score. |
| `grade_block.py check <pr>` | The enforcement check. |

`check_then_act.py --whole` scans every function in the changed files, and `--json` prints the list as data. `PR_GRADE_TYPESCRIPT` points the scanner at a `typescript` package when the repository has none.

The `pr-grade:grade` agent runs on Sonnet at `high` effort with 50 turns, and is read-only on the repository: it proves findings in its own clone.

### Embedding the checks

A repository that vendors the scripts and has selection rules the config cannot express can pass them in:

- `grade_mode.assess(changed, root, config, named={path: reason})` treats each exact path in `named` as a silent file with that reason.
- `grade_block.problems(..., assess=your_assess)` uses your selector in place of `grade_mode.assess`.

`grade_block.py` loads its own `grade_mode.py` by path under a private name, so a `grade_mode` module of yours is neither picked up nor replaced.

## Tests

```
PR_GRADE_TYPESCRIPT=<path>/node_modules/typescript python3 -m unittest discover -s tests -v
```

Without `PR_GRADE_TYPESCRIPT` the TypeScript tests are skipped. CI installs TypeScript and sets `PR_GRADE_REQUIRE_TS=1`, so a missing compiler fails the run.
