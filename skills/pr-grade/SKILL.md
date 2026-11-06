---
name: pr-grade
description: Grade a change the way an external reviewer will, before pushing it. Applies fourteen lenses for the defects a review misses, picks how many agents grade from what the diff touches, and reports a score out of five in a block a PR check can enforce. Use before opening a pull request or marking one ready, when asked for a bot-reviewer-style assessment, or after a bot review finds something the local review missed.
---

# Grading a change before a reviewer does

A thorough local review is not the same as a good one. One pull request went through two independent design proposals, an adversarial breaker, an arbiter, two rounds of high-effort code review, and six acceptance-criterion verifiers. A bot reviewer then opened it and found a P1 in about a minute.

The miss was not effort. Every pass had noticed the connection pool. Not one asked what happened to the database read the timeout stopped waiting for.

That is the pattern. A correctness review asks whether the code does the right thing. The defects that get through are about **what happens to the things you stopped looking at**, and about **who else is standing in the same room**.

Run this before you push. It began as the pass after a correctness review, for the class that review misses, and since 0.16.0 it carries a correctness lens of its own (L9) and the cleanup lens (L11), so a repository can run it alone.

## 0. Repository setup

Two optional files at the repository root tune this skill. Read both first when they exist.

- **`.claude/pr-grade-lenses.md`** restates each lens for this codebase: its real bounded resources, its callers, its proof recipe, its fan-out groups. Where it and this file differ, it wins. [`templates/pr-grade-lenses.md`](templates/pr-grade-lenses.md) is a starting point.
- **`.claude/pr-grade.json`** tells the mode selector which paths are silent-failure code and which are ordinary; code it names as neither grades as silent. [`templates/pr-grade.json`](templates/pr-grade.json) shows every key. Its `formats` declare the records the repository keeps, for L14, and its `envSweep` the command that runs the tests a change touches, for L16; one that declares neither pays nothing.

Without them the generic lenses below apply, and the selector uses its defaults.

## 1. Establish the diff and the blast radius

`git diff origin/main...HEAD --stat`, plus any uncommitted work, then read every changed hunk. Grade the change itself, never a summary of it.

Every grader gets the callers of what the branch modified, with file and line. `grade_prep.py` in step 2 lists them for each function in TypeScript, JavaScript, or Python that existed before the branch, with `git grep -n -a -F -w -e '<name>'`, outside the paths `notCode` names. A plain `grep` or `rg` skips a file it takes for binary: on one branch that hid a caller behind a single NUL byte. For a type, option, or constant you modified, or code in another language, list the callers yourself with the same command and pass them with `--callers`. L4, the lens that most often fires, starts from this list.

Then list the hunks to probe first. Three shapes keep sitting beside real defects, and a grader that starts its proof turns on them finds the defect while it still has turns to prove it:

- **Structure inferred from prose.** A status, kind, or flag read out of free text with a regex or a substring test, where a field could hold it. EngOS PR #900's marker regex `\*\*\s*withdrawn\b` settled a row headed `**Withdrawn rows**`, and the review had noted the inference beside the P2 it caused.
- **A second reader of a format another module already reads.** A hand parser over a heading, record prefix, or field that another function already parses. EngOS PR #990 split `## Loose ends` by hand while the check had its own reader; the hand parser missed CRLF and ran into the footer, and the grade had flagged the second reader as a cleanup P3.
- **A long compound condition.** A ternary or boolean chain of four or more clauses. EngOS PR #930's five-clause ternary held a reversed range and a degenerate one.

Write each as `<file>:<line> <shape>` and hand the list to every grader after its prompt, under the heading `Probe first:`. A smell is not a finding and moves no score; it decides where a grader's boundary probe runs first. Add a fourth shape only when it keeps preceding real defects.

Grade after a correctness review, never alongside one. Run the review the repository uses, fix what it proves, and commit. Keep each finding you declined, with the reason. This skill was built against Claude Code's `/code-review`, but any review that returns findings works the same way: another plugin, a second model, or a person's comments. Grading first spends the graders' turns re-finding what the review catches: on one pull request, four of the graders' seven findings were already in the review. With no review, grade anyway; the lenses do not depend on one.

## 2. Pick the mode and prepare the graders

Commit the change, then run the prep from inside the repository, with this skill's base directory in place of `<base>`:

```sh
python3 <base>/scripts/grade_prep.py --settled '<each check that passed at the head, with its result>' --declined '<each finding you declined, with its reason>'
```

It picks the mode from what the branch touches, as `grade_mode.py` does alone, and prints it with the silent-failure and unclassified files that decided it:

- **in-thread**: apply every lens yourself.
- **subagent**: dispatch one `pr-grade:grade` agent to apply every lens.
- **fan-out**: dispatch one `pr-grade:grade` agent per fan-out group. The lens file names the groups; without them use correctness (L9, L10, L4, L11, L15), timing (L1, L2, L7, L16), and lifetime and contract (L3, L5, L6, L8, L14).

A lens that does not apply to the change, by a rule the repository keeps for itself, such as a lens file that scopes a lens to some paths, goes to `--skip L2,L3`. No grader holds it and no proof copy is made for it. The merge clears it as `L2 (not applicable)`; in-thread, write it that way yourself. Skip only by a rule written down before the change, never because a lens looks unlikely to find anything.

Then it prints a prompt for every grader: a shared block, and one block per grader. Paste the shared block and the grader's own block into its prompt, unchanged, then the `Probe first:` list from section 1 when it has an entry. The shared block holds the repository, the lens file, the base and head as full SHAs, the change as one diff command, what is settled, the declined findings, and the callers list. The base is the branch point, since `origin/main` moves when anything fetches and a diff against it shows main's new work as this change reverting it. A grader's own block holds its lenses, its proof copy, its scratch directory, its proofs directory, where a proof test it kept waits for the repair, and where to write its report. The grader holding L7 also gets `check_then_act.py`'s list of check-then-act windows in the functions the branch changed. For TypeScript and JavaScript the scanner needs Node and the repository's own `typescript` package; Python needs nothing, and the scanner names the files it skipped.

The grader holding L14, or the lens `oldStateLens` in `.claude/pr-grade.json` names, gets `history_replay.py`'s output when the branch touches a reader that a declared format names. The replay runs the base and head readers over every recorded instance, such as the last few hundred merged pull request bodies, and lists each one they disagree on. The transplant takes each kind of record the change adds, puts it in a recorded instance, and runs every recorded value through it beside the record's old kind. A repository declares a format with the command that runs its reader over a list of instances, where the instances live, and the two regexes that find a record's kind and a value under it; the script's docstring has the keys. When no grader holds that lens, every grader gets the output in the shared block, and a skipped lens skips the run.

The grader holding L16, or the lens `ambientLens` names, gets `ambient.py`'s output: every process call the change adds or modifies, in Python, JavaScript, TypeScript, or a shell script, with what the call site does with the exit code and the registry's note when the command's exit codes surprise people, and, when the repository declares `envSweep`, the tests the change touches run once per perturbation in a proof copy of their own. A repository extends the registry under `exitCodes`. When no grader holds that lens, every grader gets the output in the shared block, and a skipped lens skips both.

A proof copy is a clone of the head commit outside the repository, with its installed dependencies linked in, one per grader. A grader copies a failing test there from its scratch directory and runs it without setup, and nothing is registered in the repository. Name any dependency beyond `node_modules` and `.venv` under `proofDir` in `.claude/pr-grade.json`. The prep ends with a `merge:` line, which section 5 runs, and a `remove:` line to run once every grader has reported. In-thread there are no reports to merge, and only the `remove:` line is printed. A branch that changed a dependency's pin files gets no linked dependency: the prep names the install to run in each copy instead, since what the author installed may predate the change. A grader told where nothing is searches the filesystem for it. Graders who did spent up to eight of their fifty turns on it, and read three different installed copies of this file.

Settled means the tests, type checks, and other deterministic checks that already passed at the head, with their results. They are inputs, never questions to reopen, and re-running them is how a grader runs out of turns before it reports. Nothing else is settled: a recorded decision, a comment saying the behaviour is intended, or an earlier grade is a claim the lenses grade. Never tell a grader an earlier score.

Pass the declined findings as the review returned them, in whatever form it used. Leave out the ones you fixed: the fix is in the diff, and a grader should judge it without being told it is right.

A grader has fifty turns and cannot see which one it is on. When the harness says one stopped at its turn limit, send it one message with SendMessage: "Stop investigating and report now, in your report shape, from what you have. Put every claim you have not proven under Could not verify." Do not ask it to continue; a turn spent investigating is not spent reporting. Below the grade block, say which graders needed that message. When a group's grader stops at its limit in two rounds, split the group next round, one grader per lens. Each extra grader reads the whole change again, so split no sooner.

## 3. The fourteen lenses

The ids are the ones forgesmith-os's lens file settled on, since a lens file keeps its numbers across releases and that file grew most of this list; L12 and L13 are unassigned.

Apply each one by name. A lens you skipped is a lens that finds nothing. Close each with the strongest input you tried against it and where that input failed to break the code. A clearance that names a fact about the code and no input is an assertion nothing checked, and it is where a subtle defect survives a first look.

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

Walk the callees too. A function this change calls may have changed in the same change, and a call that was safe before can now meet a new precondition, return shape, or exception.

A changed default (a config value, a setup file, an option's default, a constant callers read) reaches only the sites that leave the value unset. Search for every site that sets it explicitly under any spelling: the config key, the option a call site passes for the same setting, and a named constant holding either. Name the value each keeps. An explicit setting wins, so each one the change meant to reach still runs on the old value. EngOS PR #1420 raised the integration test timeout in a setup file while ten tests kept their own lower `timeout` option.

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

**The scanner's list.** Answer each candidate from `check_then_act.py` in one of two ways. Clear it by naming what makes the window harmless: a re-check inside the write's own transaction or after the gap (the scanner does not follow a call into the object's own methods, so it lists a re-check made through one), a store that refuses the second write (a unique key, or an expected version compared in the same statement), or a write that is idempotent. Or prove the race, and it is a finding. The list is where to start, never where to stop. It cannot see a check and a write in different functions, a write through a helper not named like one, a `.then()` chain, a synchronous check and write with no transaction around them, a callback passed by name rather than written inline, or a language it has no adapter for (it reads TypeScript, JavaScript, and Python, and names any other source files it skipped). **An empty list clears nothing.**

### L8. Failure direction

For every new failure path, fallback value, and default, say which way it fails and why that is the cheap direction. Write the sentence. If you cannot, the direction is probably wrong.

Fail-open and fail-closed are both defensible. Choosing without noticing is not. An optional dependency that means "nothing blocking" when absent has chosen fail-open for everyone who forgets to pass it.

A wait with no bound inside a hook, gate, or guard (a lock taken with no timeout, a subprocess or a read with none) leaves the failure direction to whatever kills the wait. Name that caller and what it does on a timeout. A hook that times out and lets the call run fails open however carefully its own errors fail closed. EngOS PR #975 took a shared `flock` on a ledger with no timeout inside a removal guard.

### L9. Correctness

Read every hunk with its whole enclosing function. For each line, ask what input, state, timing, or platform makes it wrong. Report a defect only when the change caused it: it introduced the defect, made it newly reachable, or made it consequential. Every candidate with a nameable failure scenario goes through the proof rule, and one you cannot make red goes under `Could not verify`.

- **Boundaries.** For each check the diff adds or changes (a regex, a match or dedupe key, a validator, a refusal, a section or range boundary), state what it should accept and what it should refuse. Then run the nearest input on each side of that line: the refused input most like an accepted one, and the accepted input most like a refused one. The case the check was built for proves neither edge.
- **Stand-ins.** A check often tests a stand-in for the property it names. Write two sentences: what it exists to refuse, and what the code tests. Take the first from the harm it guards against, as its error message, its name, or its calling step says it, never from a docstring alone, which may describe the stand-in. A record existing stands in for a record belonging to this caller, a path inside this repository for a place another reader can see, a heuristic for a fact the input states outright. Where the two differ, build the input that has the tested property and lacks the named one, and the reverse, and run both. The condition that decides whether a check runs at all is a check too: when it keys on what the diff touched, find the change that owes the check and touches none of those paths.
- **Validator and writer.** Where input passes a validator and then a canonicalizer, serializer, or mapper that keeps only the keys it knows, run an unknown key and a misspelled one through both. One the validator accepts and the writer drops disappears with no error: the validator must refuse what the writer will not keep.
- In a normative document or a review packet, check each count, each line or section reference, and each "X does Y" claim against its source at the graded commit.

*Examples, from EngOS:* PR #900, a marker regex settling a row headed `**Withdrawn rows**`; PR #990, a hand-split section missing on CRLF and swallowing the footer; PR #905, a version bound passing a prerelease of itself; PR #825, a validator letting a field take any disposition; PR #975, a removal guard accepting any session a ledger names, not only this session's child; PR #1280, a stage name the validator accepts and canonicalization drops.

### L10. Removed behavior

For each deleted or replaced line, name the invariant it enforced, then find where the new code re-establishes it. A guard, an error path, a validation, or a check name that nothing replaces is the finding.

Enforcement can go without a deleted line. A file moved or renamed leaves every path-scoped rule its old path matched: lint `files` globs, `paths:` frontmatter, owner maps, a lens file's path table. A check moved from one tool to another keeps only what the new matcher sees. For each, list what the old enforcement caught, including the forms the new selector may not (a dynamic `import()`, a `require`, a re-export, a call through an alias), and run each through the new one. In a normative document, name the obligation a deleted or rewritten clause carried and where the new text keeps it.

*Examples, from EngOS:* issue #325, a docs gate moved into a `verify` step dropped a check a ruleset still required; PR #1355, a module moved out of the tests-only rule requiring a spawn timeout; PR #1210, an import check moved into eslint, whose rule sees no dynamic `import()`; PR #805, a rewritten CRLF clause lost its body-hash half.

### L11. Cleanup

Reuse, simplification, efficiency, and altitude in the changed code. Name the existing helper to call, or the cheaper or deeper alternative, and the concrete cost: what is duplicated, repeated, or patched over. A finding with neither, such as a bare "deduplicate", is not one. A second reader of a format another module already reads is also an L9 question: run L9's boundary inputs through both readers and rank any divergence under L9. An L11 finding is always a P3, needs no red test, and never moves the score.

*Example:* `git merge-base` ran three times per selection (EngOS PR #812).

### L14. Old state meets new code

What already exists that the new code will read, accept, or rewrite? Records, rows, ledger lines, files, and pull request bodies an earlier version wrote. L5 asks when state goes away; this asks what is already there.

- **A changed reader.** Every instance the base reader accepted should read the same at the head. One the head refuses, or reads differently, is a migration the change owes or a finding.
- **A new kind in an existing format**, such as a tag, a record type, or a mode. It starts out accepting whatever the old kinds' fields accept. Find what the change says the new kind takes, in its docstring, docs, or spec, and run the values recorded under the old kinds through it. Each one it accepts that the change says it must refuse is a finding. A check that accepts what it exists to refuse is a P2, whoever writes that input today.

When the repository declares its formats under `formats` in `.claude/pr-grade.json`, `history_replay.py` has made both runs and the prep gives its output to the grader holding this lens. The output is a floor. Prove a finding from it in the proof copy like any other, and look for state it does not cover, such as a database or a cache.

*Example:* a review record gained a tag for a pass whose findings nobody acts on, and a disposition only that pass may take. The validator confined the new disposition to the tagged pass and never refused the old ones there, so a tagged pass accepted all seven dispositions the records held. Three grades missed it, the last after its lens file asked for exactly this probe: that grader ran the new value through both kinds of pass and never an old value through the new kind. Given the transplant's table of the old values the new kind accepts, two blind graders out of two reported it at P2.

### L15. Promised coverage

L9 asks whether each line is right at its edges. This asks which inputs no line handles. For each check, gate, scanner, metric, or reader the change adds, or each input kind it adds to one, find what the change says it covers: its docstring, the issue it closes, the pull request body, and any doc that names it. List every input that statement names or implies, and where in the repository each one lives. Then trace each input to the line that reads, matches, or counts it, and run the repository's own instances through the head, not only the test fixtures. An input the stated scope covers that no line reaches, or that the code counts in the wrong bucket, is the finding. A gap here fails silently: the check passes and checks less than it says. "Only widens what goes unchecked" is the finding, never a reason to clear.

*Examples, from EngOS:* PR #1288, a documented-commands check that read the root instructions but not the per-area ones; PR #1286, a first-run-green share that counted a cancelled run as a failure though its own docstring named that cancellation.

### L16. Ambient assumptions

What does the change assume about the world outside the process, and which of those assumptions differ between the author's run and the run that matters? L1, L2, and L7 ask about concurrency inside the process. This asks about everything around it: the tools it starts, the clock, the locale, the filesystem, the runtime, and the other sessions sharing the machine.

- **A tool's exit code.** For each process the change starts, name the codes the tool uses and the ones the call distinguishes. `grep` and `gh pr checks` exit 1 to say they found nothing, and `tar` exits 2 for a fatal error. A call that reads "found nothing" as failure, or a fatal error as a finding, is a defect.
- **The clock and the locale.** A date computed in local time compared with one recorded in UTC moves a day at midnight in either zone. A lowercase, a sort, or a number format changes under another locale.
- **The filesystem.** A listing that trips on a `.DS_Store`, a parser that reads CRLF as no section, a search that skips a file holding a NUL byte, a path that collides on a case-insensitive disk.
- **The shared machine.** A ref another worktree's fetch moves between the read and the use; a runtime the daemon provides at one version and the shell at another.

`ambient.py` lists every process call the change adds or modifies, with what the call site does with the exit code and the registry's note on the command, and, when the repository declares an `envSweep`, runs the tests the change touches once per perturbation: a timezone east and one west of UTC, a non-English locale, `umask 077`, a stray dotfile and then a NUL-byte file in each directory the change touches, and CRLF line endings in each data file it touches. A failing row names the perturbation, and the proof is the same run in the proof copy with the assertion that names the defect. For a tool's exit code, the proof is a shim: a script of that name first on the copy's `PATH`, exiting with the code in question. Both lists are floors. The sweep cannot see an assumption the tests never exercise, and the scan cannot see a process a library starts.

*Example:* a merge train called `gh pr checks` and read its exit 1 as a failed check, so it stopped on every pull request that had no checks at all. A ledger tool defaulted `--on` to the local date while its records were stamped in UTC, so a correction filed in the evening landed on the next day's row. A rebuild stopped on a `.DS_Store` in a directory it listed. A report parser split a section by hand and read a CRLF body as having none. A caller search missed a caller because one NUL byte made `grep` take the file for binary. Each one passed its author's run.

## 4. What a finding must carry

A finding without a run is a suspicion. The one exception is an L11 finding, a cleanup: it names the helper to call or the cheaper alternative and the concrete cost, needs no run, and is a P3 that never moves the score.

Each one needs a concrete failure: real inputs or real state, and the wrong output or wrong resource state that follows. Prove it by running something. The strongest proof is a test that fails on an assertion naming the defect, run against the unfixed code; the repository's lens file may name its own recipe. A grader keeps that test under its proofs directory and names it in the finding, so the repair starts from the test that already reached the trigger instead of a second one written from the finding's description.

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

Rank by what the defect does to a run. Rank a defect in a check by what the check exists to refuse, never by whether today's callers write the input it lets through: a version check that passes a prerelease of the next minor is a P2 when no build writes that range yet, because the check is the only thing between that input and the code, and callers change. The same rule makes each of these a P2: a changed default that explicit sites keep from reaching them (L4), a guard whose wait can time out open (L8), a stand-in check passing an input that lacks the named property (L9), a validator accepting a key the writer drops (L9), enforcement a move or a new tool left behind (L10), and a check or metric that silently skips an input its stated scope covers (L15). It holds when no caller writes the input today, when the code the enforcement guarded is clean today, and when you found no path to the timeout. An L11 finding is always a P3 and never moves the score. When a run fails either way, in the same direction, and the only defect is how the failure reads, such as a traceback where a message belongs, the finding is a P3. That holds in a re-grade whose fix was itself about messages, where the next traceback looks like the fix left unfinished.

Merge the graders' reports before you score, with the `merge:` line the prep printed. Two graders on one branch each scored the same proven P1 as 2/5, and a third cleared the path it broke as outside its lenses. The merge reads every grader's report and prints where each lens ended, the findings with any two at one line flagged and any whose line the graded diff did not change flagged `anchor:`, the defects raised outside their lenses, the lowest score these rules allow, and a draft block. The draft keeps each grader's notes in `Verified and clear`, and writes a lens the prep skipped as `L2 (not applicable)`; leave both as written. Then settle what it leaves to you:

- Two findings with one cause, the same wrong state, are one finding, at one line or two: keep the stronger proof and name both lenses. The merge counts every finding until you join them, so its floor never assumes two defects are one.
- A finding flagged `anchor:` cites a line the graded diff did not change, and the flag names the nearest changed line and the function it sits in. Read the finding's proof. When the proof mutated or asserted on another line, usually the one the flag names, re-anchor the finding there, in the same `<file>:<line> in <symbol>` form. When the defect does sit in unchanged code, such as a caller the change broke (L4) or a default a new binding made wrong, confirm the line and name the changed line that broke it in the finding's first sentence. A flag never drops a finding or moves the floor. When the merge prints `anchors: not checked`, check each finding's line against the diff yourself.
- A proof beats a clearance, and the merge counts the finding. If the clearance names a sub-claim that would break the proof, run it.
- A defect a grader lists under `Outside my lenses` belongs to the lens it names. Prove it, or carry it to `Could not verify`.
- A lens the merge calls unaccounted: ask that grader which, in one message, before you write the block.
- A lens the merge says has no report, from a grader that replied with its report but wrote no file: run the merge again with `--report <group>=-` and the reply on stdin, or `--report <group>=<file>`. Pass the reply unedited. The merge refuses it for a group that wrote its file.
- The score is yours. A grader's score covers only its own lenses. The merge cannot see a 3 for a repair that is a design decision, or a 1 for a finding that contradicts the change's stated purpose.

Then take `Could not verify` item by item. For a claim that would be a P1 or P2 if true, prove or refute it yourself, or dispatch one grader with only that claim, its lens, and what was tried. What survives that one attempt stays under `Could not verify`, labelled a guess. Its lens stays out of `Verified and clear`, it scores as the finding it would be, and the guess is the `Blocking` sentence, for a person to decide. A guess that would only be a P3 leaves the score where it is.

## 6. Report it

Report in this shape. The `## Grade` block goes in the PR body exactly as written, one field per line and outside any code block, because `grade_block.py check` reads it:

```
## Grade

Mode: <the mode grade_mode.py printed for the whole branch>
Graded: <the full SHA of the last commit graded>
Score: <n>/5
Blocking: <the one sentence that explains anything below 5, or "nothing">
Verified and clear: <the lenses cleared, each item lens ids alone or with a note in parentheses: L1, L2, ...>
Could not verify: <every claim left unproven, labelled as a guess, or "none">

P1 <file>:<line> in <symbol> - <title>
<what breaks, with the inputs and the resulting state>
<the command that showed it, and its output>
Proof test: <the path inside the repository the kept test was committed at, when the proof was a test>
```

Say each finding once, at the line the defect lives on in the head, as the grade agent's Report section defines it.

## 7. Fix, then re-grade

Start each repair from the finding's proof test. The merge printed a `mv` line for each test a grader kept: run it, run the test at its repository path and see it red, and commit it alone, before the fix. The fix commit turns that same test green, and the pull request carries the test that reached the trigger instead of a second one written from the finding's description.

Commit the fixes and review them the way step 1 reviews the change, keeping what you declined for the graders. A merge from the base branch is a change too. On one branch, two graders each spent their whole budget proving a defect a merge brought in, and the review run after them found it in twenty turns.

Run the old `remove:` line, which refuses while a kept test is still there (`--drop-proofs` deletes them, for a P3 you leave listed), then prepare the re-grade from the fix commits alone:

```sh
python3 <base>/scripts/grade_prep.py --base <the last commit graded> --settled '...' --declined '...'
```

Grade the fix commits in the mode it prints, with every lens. Pass the same `--skip` as the first round: the prep does not remember it, and a lens left out of it is graded and drafted clear instead of not applicable. The copies it makes hold the fixed code, and the L7 scan covers the same commits. The grade block keeps the whole branch's mode, which the prep reads from `origin/main` (or `--branch-base`), prints beside the round's, and passes to the merge's draft block. A repair is a change and gets the same treatment as any other, so a two-line fix to ordinary code is graded in-thread even inside a pull request that needed a fan-out, and a fix that touches silent-failure code gets at least a subagent. Without `--base` the selector sizes the whole branch, and every round costs what the first one did.

This is not ceremony. On the pull request above:

- the repair for L5 introduced the L4 defect;
- the repair for L2 introduced the L1 defect;
- the repair for L1 introduced the L7 defect, found by an external reviewer fifteen minutes later, after these lenses had been run against that very fix.

Every time, the local suite stayed green. **A fix that satisfies a finding is the most likely place for the next one**, and the lens most likely to catch it is the one written from the finding you just repaired.

The loop ends at the first round with no P1 or P2. A P3 can stay listed; one you fix anyway is a change, ships with its proof test like a P1's fix, and its fix is graded like the rest. A P3 left listed ships nothing, since the suite stays green.

The `## Grade` block keeps the whole branch's mode and moves `Graded` to the last fix commit graded. `grade_block.py check` compares `Mode` with every file the pull request touches, so a block that names a fix's lighter mode is refused.

## 8. Enforce it

`grade_block.py check <pr>` refuses a pull request whose grade block is missing, below 5/5, missing a lens, in a cheaper mode than its files need, or older than its code:

```sh
python3 <base>/scripts/grade_block.py check <pr>
```

Run it before marking a PR ready. To make it a merge gate, copy [`templates/pr-grade-check.yml`](templates/pr-grade-check.yml) to `.github/workflows/` and make its check required. It runs on `opened`, `edited`, `synchronize`, `reopened`, and `ready_for_review`, so a commit pushed after the grade fails until it is graded and a block pasted into the body is read at once. It runs the checker from a pinned pr-grade ref, and the checker reads the config and lens file from the base branch, so a pull request cannot loosen the rules it is checked against.

## 9. Grading a surface

A surface grade applies the same fourteen lenses to code that no branch is changing: the files under some paths, as committed at HEAD. The base is the empty tree, so the prep treats every function as if a diff had introduced it. Nothing about it is a pull request, so the grade block it drafts is not one either.

### When

Only on a trigger, never on a cadence. The triggers:

- A class of bug escaped every review. On 2026-11-04 it was what a screen shows between a write and its subscription echo.
- A hotspot is about to be refactored.
- A milestone closes and the hotspot list names the surfaces that moved.

A surface nobody touched buys nothing and costs about six branch grades.

### How

Run one command per surface, from inside the repository with the code committed:

```sh
python3 <base>/scripts/grade_prep.py --surface <path> [<path> ...] --settled '...' --declined '...'
```

Choose surfaces by layer, so each grader reads one kind of code. For a React and Firestore app that is hooks, repositories, shared components, pages, engine, and views. `--base` and `--branch-base` are refused, since a surface has no before. Tests under the paths are left out of the change and the caller list, and stay in the proof copy as evidence.

Paste the prompts as printed, dispatch the graders, and run the `merge:` line. The mode comes from the surface's files by the same rule as a branch. Callers outside the surface are listed, the ones inside are counted, and L7 scans the whole surface instead of a diff. The draft block carries a `Surface:` line after `Mode:`.

### Reading the result

- The score is the surface's. Nothing enforces it, and `grade_block.py check` refuses a body whose block carries a `Surface:` line.
- Every P2 still needs a failing test before it counts, as in section 4.
- Findings go into the repository's tracker, not a pull request body. A fix is a branch like any other and gets a branch grade.

### Cost

The baby-day-planner run on 2026-11-04 graded six surfaces and 139 production files. It used about 900k subagent tokens and 25 minutes of wall clock with six graders in parallel, and proved eight P2s and fifteen P3s.
