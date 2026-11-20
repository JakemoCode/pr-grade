## 3. The fourteen lenses

The ids are the ones forgesmith-os's lens file settled on, since a lens file keeps its numbers across releases and that file grew most of this list; L12 and L13 are unassigned.

Apply each one by name. A lens you skipped is a lens that finds nothing. Close each with the strongest input you tried against it and where that input failed to break the code. A grader writes that input as the lens's note in `Verified and clear`, such as `L4 (walked 6 callers)`, and writes an `L<n>:` line only for a lens with a finding, a suspect, or a `Could not verify` item: a report records findings, never their absence. A clearance that names a fact about the code and no input is an assertion nothing checked, and it is where a subtle defect survives a first look.

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
- `near_miss.py` gives the Boundaries probe a floor. Its table runs each changed predicate against near misses of a string it accepts, and its second-reader list names the other files that hold the same parse anchor. For each row that accepts what the check exists to refuse, or refuses what it exists to accept, prove the wrong state in the proof copy or say why the predicate is right there. Seed a regex printed with "no seed found" yourself. Run the same near misses through each second reader, and rank a divergence here. Neither list moves the score; a finding proven from one does. The table sees only predicates written as literals, and the search cannot see a reader that builds its text at runtime.
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
