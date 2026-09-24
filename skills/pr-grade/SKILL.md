---
name: pr-grade
description: Grade a change the way an external reviewer will, before pushing it. Applies eight lenses for the defects a correctness review misses, picks how many agents grade from what the diff touches, and reports a score out of five in a block a PR check can enforce. Use before opening a pull request or marking one ready, when asked for a bot-reviewer-style assessment, or after a bot review finds something the local review missed.
---

# Grading a change before a reviewer does

A thorough local review is not the same as a good one. One pull request went through two independent design proposals, an adversarial breaker, an arbiter, two rounds of high-effort code review, and six acceptance-criterion verifiers. A bot reviewer then opened it and found a P1 in about a minute.

The miss was not effort. Every pass had noticed the connection pool. Not one asked what happened to the database read the timeout stopped waiting for.

That is the pattern. A correctness review asks whether the code does the right thing. The defects that get through are about **what happens to the things you stopped looking at**, and about **who else is standing in the same room**.

Run this before you push. It does not replace a correctness review such as `/code-review`. It catches a different class.

## 0. Repository setup

Two optional files at the repository root tune this skill. Read both first when they exist.

- **`.claude/pr-grade-lenses.md`** restates each lens for this codebase: its real bounded resources, its callers, its proof recipe, its fan-out groups. Where it and this file differ, it wins. [`templates/pr-grade-lenses.md`](templates/pr-grade-lenses.md) is a starting point.
- **`.claude/pr-grade.json`** tells the mode selector which paths are silent-failure code. [`templates/pr-grade.json`](templates/pr-grade.json) shows every key.

Without them the generic lenses below apply, and the selector uses its defaults.

## 1. Establish the diff and the blast radius

`git diff origin/main...HEAD --stat`, plus any uncommitted work, then read every changed hunk. Grade the change itself, never a summary of it.

For every function you modified rather than added, list its callers with the repository's own search, for example `grep -rn '<functionName>' --include='*.<ext>' .` with vendored directories excluded. Write the list down. L4 uses it, and it is the lens that most often fires.

## 2. Pick the mode

Commit the change, then run the selector from inside the repository, with this skill's base directory in place of `<base>`:

```sh
python3 <base>/scripts/grade_mode.py
```

It prints `in-thread`, `subagent`, or `fan-out`, and the silent-failure files that decided it:

- **in-thread**: apply every lens yourself.
- **subagent**: dispatch one `pr-grade:grade` agent to apply every lens.
- **fan-out**: dispatch one `pr-grade:grade` agent per fan-out group. The lens file names the groups; without them use timing (L1, L2, L7), reach (L3, L4), and lifetime and contract (L5, L6, L8).

A grading agent needs the diff range, the lens file path, its lenses, and a list of what is already settled: the tests, type checks, and other deterministic checks that already passed. Their results are inputs, never questions to reopen, and re-running them is how a grader runs out of turns before it reports.

## 3. The eight lenses

Apply each one by name. A lens you skipped is a lens that finds nothing.

**This list is incomplete by construction.** It was written from the findings of one external review and then missed the next one, which arrived fifteen minutes later against the fix for the first. Each lens is a defect that already escaped a careful review. When a reviewer finds something no lens names, add the lens to the repository's lens file and name the case. A list that only ever gets applied stops growing while the code does not.

### L1. Abandoned work

Every timeout, race, early return, cancel path, retry and give-up. Ask: **what happens to the operation nobody is waiting for any more?**

It is still running. It still holds whatever it held. It still writes when it lands. Cancelling a wait is not cancelling work, and many drivers offer no way to cancel the work at all: an abandoned database read keeps its connection until the database answers.

The repair is almost never a longer timeout. It is refusing to start the next one, bounding what can be in flight, or making the abandoned result harmless: a late result that checks, inside the same transaction as its write, that the thing it reports on is still open.

*Example:* a dialogue handler timed a ledger read at three seconds and returned on time. Each abandoned read kept its place in a three-connection pool. Repeated dialogue opens starved the hand-in path that shared it.

### L2. Shared bounded resources

Name every pool, queue, lock, semaphore, cache, socket and rate limit the change touches. For each, answer three questions in writing:

- What is the bound, and where is it configured?
- **Who else uses it?** Name them.
- What happens at the bound: block forever, reject, or drop?

Then multiply. How often does the new work run: per request, per open, per tick, per user? At realistic concurrency, does it fit inside the tightest timeout anywhere in the path? Compare the numbers directly. A budget longer than the caller's patience is not a budget.

*Example:* a pool of three connections with no queue bound served both best-effort reads and writes that must land. The gateway dropped a request at eight seconds; the driver waited ten to connect.

### L3. Unguarded failure reach

Where does a failure in the new code go? Frameworks that call your code (message handlers, request handlers, job workers, event listeners) differ in what they do with an error you did not catch. Some log it. Some drop the promise. Some end the process.

For every new `await` or throwing call in code a framework calls, find the `try` that contains it, including the expression that builds its arguments. A store constructed in an argument list sits outside the `try` that appears to guard the call.

Some codebases have a sharper reach question: which modules may call a write method that only one owner should reach. When the repository's lens file restates L3 that way, use its version.

### L4. Every other caller

Take the caller list from step 1. Walk each one. Not the one you were thinking about: the others.

*Example:* clearing a cached option map on any reply without options fixed one flow and broke another, which answered a pick with data and no options while its dialog was still open. Fifty local tests stayed green, because they all tested the first flow.

A default that was right when written can turn wrong when a later change binds what it stood in for. The diff that binds it is the one that grades the default.

### L5. State that outlives its reason

Anything written and not deleted. Cache entries, map keys, listeners, pending rows, leases, timers, subscriptions, temporary files.

Ask when each one is removed, and name the line that removes it. "It gets overwritten next time" is not removal, because next time may not come.

*Example:* an option map was written on every reply carrying options and cleared nowhere, so a dialog that stopped offering an option still translated its label.

### L6. Contract drift

Behaviour moved. Did everything that describes the behaviour move with it?

- tool descriptions an agent reads;
- doc comments, docstrings, and error messages;
- types;
- tests that assert the old shape;
- specifications, decision records, and any registry that maps code to owners.

*Example:* a response field changed meaning while the tool description still told agents it meant the choice ran.

### L7. Check then act

Every guard you add is new code and gets graded like any other. Ask **when the guard's state changes, relative to the work it bounds.**

Name the check. Name the line that changes the state. Count what sits between them. If an `await`, a second transaction, or a second network read sits between them, the window is unbounded and every caller that arrives inside it passes the check.

A counter raised after the work starts does not bound the work. A flag set in a callback does not bound anything the callback has not reached yet. A guard compared against a value that can never change bounds nothing at all.

*Example:* a guard meant to stop reads piling up raised its count only after the read budget expired. Every open inside that window passed the check and took a connection. The repair counted reads in flight: raise the count before the query starts, lower it when the query truly settles.

### L8. Failure direction

For every new failure path, fallback value, and default, say which way it fails and why that is the cheap direction. Write the sentence. If you cannot, the direction is probably wrong.

Fail-open and fail-closed are both defensible. Choosing without noticing is not. An optional dependency that means "nothing blocking" when absent has chosen fail-open for everyone who forgets to pass it.

## 4. What a finding must carry

A finding without a run is a suspicion.

Each one needs a concrete failure: real inputs or real state, and the wrong output or wrong resource state that follows. Prove it by running something. The strongest proof is a test that fails on an assertion naming the defect, run against the unfixed code; the repository's lens file may name its own recipe.

Then try to refute it. Name the sub-claims that did not survive. The author of a claim does not verify it: if the change is yours, hand the verification to a grading agent, or let a failing test do it, and accept a result that contradicts you. Where a failing test is the proof, the test does the breaking and no separate verifier runs; otherwise run the verifier on the strongest model available, since a cheaper model accepts a plausible claim instead of breaking it.

A finding you believe and cannot demonstrate goes under `Could not verify`, with what you tried. Never drop it.

## 5. Score it

One score for the change: the honest answer to "would a reviewer merge this without asking a question."

| Score | Meaning |
| --- | --- |
| 5 | No P1 or P2 survives. Merge. |
| 4 | One issue that must be fixed first, named in one sentence. |
| 3 | Several issues, or one whose repair is a design decision. |
| 2 | A lens could not be applied, so the change is not assessed. |
| 1 | A finding contradicts the change's stated purpose. |

Rank findings `P1` (fix before merge), `P2` (fix or justify), `P3` (note). The score counts P1 and P2; a P3 is listed and leaves the score at 5, or one standing note would hold a change below 5 forever.

## 6. Report it

Report in this shape. The `## Grade` block goes in the PR body exactly as written, one field per line and outside any code block, because `grade_block.py check` reads it:

```
## Grade

Mode: <the mode grade_mode.py printed>
Graded: <the full SHA of the last commit graded>
Score: <n>/5
Blocking: <the one sentence that explains anything below 5, or "nothing">
Verified and clear: <each lens id, first in its item: L1, L2, ...>
Could not verify: <every claim left unproven, labelled as a guess, or "none">

P1 <file>:<line> - <title>
<what breaks, with the inputs and the resulting state>
<the command that showed it, and its output>
```

Say each finding once.

## 7. Fix, then re-grade

Apply the fixes, then rerun `grade_mode.py` and grade the fix commits in the mode it prints now. A repair is a change and gets the same treatment, and a fix that touches silent-failure code can need a dearer mode than the change it fixed.

This is not ceremony. On the pull request above:

- the repair for L5 introduced the L4 defect;
- the repair for L2 introduced the L1 defect;
- the repair for L1 introduced the L7 defect, found by an external reviewer fifteen minutes later, after these lenses had been run against that very fix.

Every time, the local suite stayed green. **A fix that satisfies a finding is the most likely place for the next one**, and the lens most likely to catch it is the one written from the finding you just repaired.

## 8. Enforce it

`grade_block.py check <pr>` refuses a pull request whose grade block is missing, below 5/5, missing a lens, in a cheaper mode than its files need, or older than its code:

```sh
python3 <base>/scripts/grade_block.py check <pr>
```

Run it before marking a PR ready. To make it a merge gate, run a copy of `scripts/` in CI on `opened`, `edited`, `synchronize`, `reopened`, and `ready_for_review`, so a commit pushed after the grade fails until it is graded and a block pasted into the body is read at once. The check reads the config and lens file from the base branch, so a PR cannot loosen its own rules.
