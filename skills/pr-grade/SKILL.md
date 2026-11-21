---
name: pr-grade
description: Grade a change the way an external reviewer will, before pushing it. Applies fourteen lenses for the defects a review misses, picks how many agents grade from what the diff touches, and reports a score out of five in a block a PR check can enforce. Use before opening a pull request or marking one ready, when asked for a bot-reviewer-style assessment, or after a bot review finds something the local review missed.
---

# Grading a change before a reviewer does

A thorough local review is not the same as a good one. One pull request went through two independent design proposals, an adversarial breaker, an arbiter, two rounds of high-effort code review, and six acceptance-criterion verifiers. A bot reviewer then opened it and found a P1 in about a minute.

The miss was not effort. Every pass had noticed the connection pool. Not one asked what happened to the database read the timeout stopped waiting for.

That is the pattern. A correctness review asks whether the code does the right thing. The defects that get through are about **what happens to the things you stopped looking at**, and about **who else is standing in the same room**.

Run this before you push. It began as the pass after a correctness review, for the class that review misses, and since 0.16.0 it carries a correctness lens of its own (L9) and the cleanup lens (L11), so a repository can run it alone.

Section 0, repository setup, is in [`setup-and-enforcement.md`](setup-and-enforcement.md): read it when you set a repository up for this skill. Section 9, grading a surface, is in [`surface-grades.md`](surface-grades.md): read it before step 1 when you are asked to grade code that no branch is changing.

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

Commit the change. Then, from inside the repository and with this skill's base directory in place of `<base>`, ask whether a first read runs, passing the `--base`, `--branch-base`, and `--exclude-path` you will give the prep:

```sh
python3 <base>/scripts/grade_mode.py --scan
```

It prints `scan: yes` or `scan: no` with its reason, and a re-grade always gets a no. Never decide it yourself. On `scan: yes`, dispatch one `pr-grade:scan` agent at once, with the line it prints as the Agent prompt, unchanged, and run the prep below while the scan reads, adding the `--suspects <dir>` it prints. The prep then holds back the dispatch line of the grader the first read goes to and prints an `attach:` line in its place; dispatch the other graders as usual. When the scan returns, whatever it returns, run the `attach:` line and dispatch the line it prints. The attach moves the scan's report into that grader's prompt file as numbered suspects, so never read the report or type any of it. When `pr-grade:scan` cannot be dispatched, as with agents older than 0.19.0, or the scan writes nothing, run the `attach:` line all the same: that grader goes with no suspects, and the merge prints `first read: did not run`, which you say below the block. A surface grade, section 9, has no branch to scan and skips this step.

Then run the prep:

```sh
python3 <base>/scripts/grade_prep.py --settled '<each check that passed at the head, with its result>' --declined '<each finding you declined, with its reason>'
```

It picks the mode from what the branch touches, as `grade_mode.py` does alone, and prints it with the silent-failure and unclassified files that decided it:

- **in-thread**: apply every lens yourself.
- **subagent**: dispatch one `pr-grade:grade` agent to apply every lens.
- **fan-out**: dispatch one `pr-grade:grade` agent per fan-out group. The lens file names the groups; without them use correctness (L9, L10, L4, L11, L15), timing (L1, L2, L7, L16), and lifetime and contract (L3, L5, L6, L8, L14).

A lens that does not apply to the change, by a rule the repository keeps for itself, such as a lens file that scopes a lens to some paths, goes to `--skip L2,L3`. No grader holds it and no proof copy is made for it. The merge clears it as `L2 (not applicable)`; in-thread, write it that way yourself. Skip only by a rule written down before the change, never because a lens looks unlikely to find anything.

Then it writes a prompt for every grader, a shared block followed by the grader's own block, to `prompt.md` in that grader's directory under the proof root, and prints one line per grader under `===== dispatch =====` naming the file. Pass a grader's line as the Agent prompt, unchanged, followed by the `Probe first:` list from section 1 when it has an entry; the grader reads the file and runs the diff the line names in its first turn. After the dispatch, each file carries the grader's lens text: section 3 less the lens sections of lenses the grader does not hold, sections 4 and 5 whole, and the lens file less the lens sections of lenses it does not hold, so the grader reads neither file. The prep slices the skill's files beside its `scripts/` directory, or the copy `--skill <path>` names, a directory or one file holding sections 3 to 5; with none beside it, it says so in a `notice:` line and the grader reads its installed copy. `"sliceLenses": false` writes the files with no lens text. In-thread, or when `.claude/pr-grade.json` sets `"promptFiles": false`, it prints the blocks instead: paste the shared block and the grader's own block into its prompt, unchanged, then the list. The shared block holds the repository, the lens file, the base and head as full SHAs, the change as one diff command, what is settled, the declined findings, and the callers list. The base is the branch point, since `origin/main` moves when anything fetches and a diff against it shows main's new work as this change reverting it. A grader's own block holds its lenses, its proof copy, its scratch directory, its proofs directory, where a proof test it kept waits for the repair, and where to write its report. The grader holding L7 also gets `check_then_act.py`'s list of check-then-act windows in the functions the branch changed. For TypeScript and JavaScript the scanner needs Node and the repository's own `typescript` package; Python needs nothing, and the scanner names the files it skipped.

The grader holding L9, or the lens `nearMissLens` names, gets `near_miss.py`'s output. The near-miss table runs every predicate the change adds or modifies, a regex or a `startswith`, `endswith`, `in`, or `==` test against a marker string, against near misses of a string it accepts, one row per input, accepted or refused: the seed with a prefix, with a suffix, in other casing, with surrounding whitespace, with CRLF, with a Unicode lookalike, empty, and embedded in a longer word, then the other literals of its file and the touched tests that it accepts. The second-reader list names, for each parse anchor the change touches, every other file whose code holds the same text. Both lists are floors, never ceilings, and neither moves the score. When no grader holds that lens, every grader gets the output in the shared block, and a skipped lens skips both.

The grader holding L14, or the lens `oldStateLens` in `.claude/pr-grade.json` names, gets `history_replay.py`'s output when the branch touches a reader that a declared format names. The replay runs the base and head readers over every recorded instance, such as the last few hundred merged pull request bodies, and lists each one they disagree on. The transplant takes each kind of record the change adds, puts it in a recorded instance, and runs every recorded value through it beside the record's old kind. A repository declares a format with the command that runs its reader over a list of instances, where the instances live, and the two regexes that find a record's kind and a value under it; the script's docstring has the keys. When no grader holds that lens, every grader gets the output in the shared block, and a skipped lens skips the run.

The grader holding L16, or the lens `ambientLens` names, gets `ambient.py`'s output: every process call the change adds or modifies, in Python, JavaScript, TypeScript, or a shell script, with what the call site does with the exit code and the registry's note when the command's exit codes surprise people, and, when the repository declares `envSweep`, the tests the change touches run once per perturbation in a proof copy of their own. A repository extends the registry under `exitCodes`. When no grader holds that lens, every grader gets the output in the shared block, and a skipped lens skips both.

A proof copy is a clone of the head commit outside the repository, with its installed dependencies linked in, one per grader. A grader copies a failing test there from its scratch directory and runs it without setup, and nothing is registered in the repository. Name any dependency beyond `node_modules` and `.venv` under `proofDir` in `.claude/pr-grade.json`. The prep ends with a `merge:` line, which section 5 runs, and a `remove:` line to run once every grader has reported. In-thread there are no reports to merge, and only the `remove:` line is printed. A branch that changed a dependency's pin files gets no linked dependency: the prep names the install to run in each copy instead, since what the author installed may predate the change. A grader told where nothing is searches the filesystem for it. Graders who did spent up to eight of their fifty turns on it, and read three different installed copies of this file.

Settled means the tests, type checks, and other deterministic checks that already passed at the head, with their results. They are inputs, never questions to reopen, and re-running them is how a grader runs out of turns before it reports. Nothing else is settled: a recorded decision, a comment saying the behaviour is intended, or an earlier grade is a claim the lenses grade. Never tell a grader an earlier score.

Pass the declined findings as the review returned them, in whatever form it used. Leave out the ones you fixed: the fix is in the diff, and a grader should judge it without being told it is right.

A grader has fifty turns and cannot see which one it is on. When the harness says one stopped at its turn limit, send it one message with SendMessage: "Stop investigating and report now, in your report shape, from what you have. Put every claim you have not proven under Could not verify." Do not ask it to continue; a turn spent investigating is not spent reporting. Below the grade block, say which graders needed that message. When a group's grader stops at its limit in two rounds, split the group next round, one grader per lens. Each extra grader reads the whole change again, so split no sooner.

Section 3, the fourteen lenses, is in [`lenses.md`](lenses.md), and section 5, scoring, is in [`scoring.md`](scoring.md). In-thread, read both, and section 0 in [`setup-and-enforcement.md`](setup-and-enforcement.md), before you grade.

In subagent or fan-out mode, never read section 3: each grader gets its lens text from the prep, or reads it itself. Read section 5 in [`scoring.md`](scoring.md) once every grader has reported, before you run the `merge:` line.

## 4. What a finding must carry

A finding without a run is a suspicion. The one exception is an L11 finding, a cleanup: it names the helper to call or the cheaper alternative and the concrete cost, needs no run, and is a P3 that never moves the score.

Each one needs a concrete failure: real inputs or real state, and the wrong output or wrong resource state that follows. Prove it by running something. The strongest proof is a test that fails on an assertion naming the defect, run against the unfixed code; the repository's lens file may name its own recipe. A grader keeps that test under its proofs directory and names it in the finding, so the repair starts from the test that already reached the trigger instead of a second one written from the finding's description.

Then try to refute it. Name the sub-claims that did not survive. The author of a claim does not verify it: if the change is yours, hand the verification to a grading agent, or let a failing test do it, and accept a result that contradicts you. Where a failing test is the proof, the test does the breaking and no separate verifier runs; otherwise run the verifier on the strongest model available, since a cheaper model accepts a plausible claim instead of breaking it.

A finding you believe and cannot demonstrate goes under `Could not verify`, with what you tried. Never drop it.

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

The merge prints this block, so paste it and never retype it. When the merge says nothing is left to your judgment, paste it as printed, changing `Score` only for a 3 or a 1. Otherwise edit only the lines your judgment touched: `Score` and `Blocking`, a finding you joined or re-anchored, and a `Could not verify` item you proved, refuted, or left a guess. A finding a re-check proved moves into the findings, and one it refuted leaves `Could not verify`.

Section 7, fixing and re-grading, is in [`fix-and-regrade.md`](fix-and-regrade.md): read it when the grade holds a P1 or P2, or a P3 you mean to fix. Section 8, enforcement, is in [`setup-and-enforcement.md`](setup-and-enforcement.md): read it before you mark a pull request ready, or to make the grade a merge gate.
