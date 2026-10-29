# Changelog

- Claude Code uses `version` in `.claude-plugin/plugin.json` to decide if an update exists, so each release increases it.
- After you update, run `/reload-plugins`.
- CI refuses a pull request that changes `skills/`, `agents/`, or `plugin.json` if it does not increase the version and add its heading here.
- When a change lands over several pull requests, label one `hold-release` to merge it without a release.

Each entry gives the change first. Under it, **Before** is the old behavior and **Seen on** is the grade that exposed it.

## 0.12.3

- The grade agent deletes only what it created, by its literal path in its scratch directory or proof copy. It never deletes by a pattern in a shared directory.
  - **Seen on:** EngOS PR #1211's round 3 grade, where a grader used `rm -rf` on every `grade-rules-*` entry in the macOS temp directory. Another session's test kept its repositories under the same prefix.

## 0.12.2

- `merge` keeps every list when a grader's report repeats a heading: the repeat adds to its field.
  - **Before:** `read_report` replaced a field's items each time its heading opened, so a report with two `Could not verify:` lists kept only the second, and the first list's open claims stayed out of the score floor or the draft block.
  - **Seen on:** EngOS's round 6 grade of `feat/review-records` (EngOS issue #742).

## 0.12.1

- `history_replay.py`: when a carrier record's own text holds the example value, its other findings no longer read as a refusal of every value.
  - **Before:** the transplant took the carrier's problems from the intersection of problems masked per value. Masking changed that finding for the example value only, so it dropped out of the intersection and showed on every other row as `new kind refused`.
  - **Now:** the carrier's problems are the ones every value gets identically, unmasked and counted, so a value the carrier's text also holds still shows its own refusal. A problem that names the value differs per value, so a new kind that refuses every value by name still shows as refusing them. Masking occurs only when a row is printed.
  - **Seen on:** EngOS PR #1064's re-grade prep, with PR #1063 as the carrier (issue #49).

## 0.12.0

- **L9, old state meets new code.** A changed reader must still read every instance the base read, and a new kind of record must refuse the old values its rules exclude.
  - The default groups put L9 with lifetime and contract. `grade_block.py check` requires L9 of a repository without a lens file. A repository with one adds a `### L9.` heading, or names its own id in `oldStateLens`.
- **`history_replay.py`** does both checks for each format that `.claude/pr-grade.json` declares under `formats`, when the branch changed its reader.
  - The replay and the transplant work as the README's [Declaring a format](README.md#declaring-a-format) describes. Instances are merged pull request bodies, files, or lines of files at the base.
  - `grade_prep.py` gives the output to the grader that holds the lens `oldStateLens` names, `L9` by default. When no grader holds it, every grader gets the output.
  - A repository that declares no format pays nothing.
- **Why the transplant:** EngOS PR #825 added a tagged review pass, and its findings take one disposition. But its validator let the tagged pass take every disposition an ordinary pass takes.
  - Three grades missed it, the last after the lens file asked for this probe. In a blind replay on PR #825's head, with the transplant's output and the L9 text, two graders out of two reported it at P2.

## 0.11.1

- `merge` reads a finding's file written as an absolute path in the repository or a proof copy as the path inside that tree, so `<repo>/scripts/canary_scoring.py:95` reads as `scripts/canary_scoring.py:95`.
  - **Before:** 0.11.0 flagged it as not in the head commit, though the diff changed it, and missed its overlap with the same line written relative.
  - `merge` resolves a path cited through a link, such as macOS's `/var` for the `/private/var` git prints.
  - A proof copy under the repository reads as the copy, and an absolute path outside both keeps the note.
- The grade agent writes a finding's `<file>` as the path inside the repository, as `git diff --name-only` prints it.
  - The merge reads only finding lines this way. A `Could not verify` claim still has the path as the grader wrote it.

## 0.11.0

- A finding line names the function it is in, and can cite a span: `P1 L4 src/reports/run-report-renderer.ts:78 in RunReportRenderer.renderHtml - cached render is dropped`, or `<file>:<start>-<end>`.
  - The grade agent cites the head line where the defect is: the changed line, the head line where removed code was, or the unchanged line the change broke.
  - The agent takes every number from the head file, never by counting from a hunk header.
  - **Seen on:** EngOS's grader canary of 2026-10-08. A grader proved a P1 on line 78 and cited line 77, the lookup above it.
- `merge` flags a finding whose line touches no line the graded diff changed.
  - The flag names the nearest changed line and its function, which `merge` reads at the head with the scanner's Python and TypeScript adapters. When two changed lines are at the same distance, it names both.
  - It flags and does not refuse, because a defect can be in a caller the change did not touch. SKILL.md section 5 tells how the coordinator re-anchors or confirms each flag.
  - When a `grade.json` names no repository, or git cannot read a diff, `merge` prints `anchors: not checked` with the reason.
- `merge` reads the symbol and the span, and still reads `<file>:<line> - <title>`, so earlier reports still score.
  - It removes a leading `./` and a column after the line (`<file>:78:5`).
  - When two findings' spans overlap in one file, it flags them as at one line.
  - A 0.10.1 merge dropped all of a finding line with ` in <symbol>`, so a report in the new shape needs a 0.11.0 merge.

## 0.10.1

- `proof_dir.py make` removes two kinds of root no make reached before:
  - A root whose marker names a repository that no longer exists, such as a worktree removed after its grade. It is removed after seven idle days. **Before:** only that repository's own make could remove it.
  - A root with no marker, removed on the same terms. A grader makes one when it writes its report after the coordinator removed the root.
- A root of a repository that still exists is still left to that repository. A markerless root's own time protects one that a make is creating now.

## 0.10.0

- `grade_prep.py --skip L2,L3` keeps lenses out of a grade.
  - A skipped lens gets no grader and no proof copy. `grade.json` records it under `skipped`, and the prep prints a `skipped:` line.
  - `--skip` refuses a word that is not a lens id the lens file defines, a lens a `--groups` group also names, and a list that skips every lens.
  - **Before:** a repository whose lens file limits a lens to some paths had one way to keep it out. It passed the lens to `--groups` as a separate group. That group got a proof copy no grader used. The author also wrote the group's report by hand, so the merge did not score the round 2/5.
- `merge` counts a skipped lens as applied, and drafts it as `L2 (not applicable)`.
  - A finding or open claim another grader raises under a skipped lens has priority over the skip. A `grade.json` from an earlier prep has no `skipped` key, and skips nothing.
- `merge` keeps each grader's notes in the draft's `Verified and clear`: `L3 (not applicable)` stays as written, where 0.9.0 drafted only `L3`.
  - Why: a checker can refuse a note it does not agree with, such as a grader that says a lens is not applicable when the repository's rules put it in scope. The checker could not see a note that the draft removed.
  - The draft still removes a note on a lens that a finding holds, because that lens is not clear.

## 0.9.0

- Code that no rule names grades as silent.
  - `ordinary` in `.claude/pr-grade.json` lists code where a test would find a defect.
  - A code file is unclassified when `ordinary`, `silent`, `silentCommand`, and a caller's named files do not cover it. An unclassified file increases the mode, as a silent-failure file does.
  - `ordinary` is empty by default and in the template, so a repository without it grades every code change in one subagent or more, until it lists its ordinary code.
  - Tests and `notCode` are never unclassified, and `silent` has priority over `ordinary`.
  - Each unclassified path's reason says to add it to `ordinary` if a test would find a defect there, or to `silent` if none would.
  - While the config has no `ordinary` key, `grade_mode.py` and `grade_prep.py` print one `notice:` line that tells why the mode increased.
  - **Before:** an unnamed path graded as ordinary code, the cheap direction. EngOS fixed four such holes by hand. They include its Prettier config and a module its architecture tests use.
- `countAsCode` entries are globs over the full path.
  - An exact path still matches only itself. An entry without a `/` is still a full path, not a file name.
  - **Before:** 0.7.0 took only exact paths, so a repository that adds a data file in each folder had to edit the config for each one. (EngOS adds owner, port, and write-path maps in each slice.) Until it did, the size count did not include the file, and the grade was one tier too cheap.
- `silent`, `ordinary`, `tests`, `notCode`, and `countAsCode` must each be a list of strings, or the selector stops with a message.
  - **Before:** it read `"ordinary": "src/*"` one character at a time, and its single `*` would make every file ordinary.

## 0.8.1

- The grade agent has the Write tool. It writes its report and any scratch script with it, only to the report path and in the scratch directory its dispatch names.
  - Why: a worktree-isolated session refuses a `python3 - <<'EOF'` heredoc whose text names git, and a report almost always cites `git grep`, so a grader in such a session could not write its report.
  - **Seen on:** EngOS Slice 001, where these heredocs were 216 of the 1,730 refusals.
  - It writes a proof test in the scratch directory and copies it into the proof copy with `cp`. It still never edits, stages, or commits anything in the repository.
- A grader's report is `<root>/<group>/grade.md`. `grade_prep.py` prints this path, `merge` reads it, and the stale-root sweep checks it.
  - Why: Claude Code refuses a subagent's Write to a file named `report*.md`, so `report.md` would fail as the heredoc did.
  - A grader that a 0.8.0 prep dispatched still writes `report.md`, which a 0.8.1 merge does not read. Pass its reply with `merge --report`.

## 0.8.0

- `merge --report <group>=<file or ->` takes the report of a group that wrote no `<group>/report.md`, from a file or stdin.
  - **Before:** a grader could reply with its report but not write the file. Its lenses then showed `no report from <group>`, and the floor was 2/5. A coordinator that ran as a subagent could not write the file for it.
  - The merge refuses `--report` for a group that wrote its file, for a group the grade does not have, and for an empty report.
- The grade agent confirms its report file exists before it replies, and writes it again if not.
- The grade agent writes each `Could not verify` claim as its own `- ` bullet, which the merge reads as one claim.
- Under `Could not verify`, `merge` reads an indented line that starts with a possessive lens id as part of the claim above it, such as `L2's pool releases the handle`.
  - This does not apply when the line names a rank, or the claim above is a P3, because then it would not count.
  - **Before:** 0.7.1 made the line a separate claim with no rank, counted it as blocking, and put the floor one point low.
  - A bare lens id still starts a claim, because `L2 pool releases` and `L2 handle may leak` read the same. To count a claim two times is the safe error.

## 0.7.1

- `Verified and clear` reads each lens id with its own note: `L1 (a) L2 (b)` and `L1 (a) L2` both clear both lenses, where 0.7.0 cleared only L1 in the first and nothing in the second. `L2 not applicable` still clears nothing.
- `merge` reads a `Verified and clear` with one lens per line as one item per line, so `L1 (proved by test_x)` then `L3 (no await)` clears both.
- `merge` keeps an indented line that starts with a lens id or a rank as a separate claim, not joined to the claim above. Wrapped prose still joins.

## 0.7.0

- `countAsCode` in `.claude/pr-grade.json` lists exact paths that count toward size even when `notCode` matches them, such as a manifest under `docs/` that the code reads.
  - The mode selector, `grade_prep.py` (its modes, callers, and L7 scan), `check_then_act.py`, and `grade_block.py check` all read it.
  - A listed path that changes after the grade counts as changed code.
- `grade_block.py merge` joins a line indented under a report item to that item, so a wrapped `Could not verify` claim is one claim. **Before:** it counted the second line as a separate, blocking claim.
- `Verified and clear` clears a lens only when its item is lens ids alone, or lens ids followed by a note in parentheses.
  - `L2 not applicable` no longer clears L2, in `merge` or `check`. `L1 L2 L4` in one item clears all three.
- When a lens is unassessed, the draft block's `Blocking` names it. **Before:** it showed `nothing` under a 2/5.

## 0.6.0

- `scripts/grade_prep.py`: one call prepares a grade. It:
  - picks the mode;
  - divides the lenses among graders from the lens file's `## Fan-out groups` table;
  - lists the callers of each function the branch changed, for TypeScript, JavaScript, and Python, and names any other language as not derived;
  - runs the L7 scan for the grader that holds L7;
  - makes the proof copies, and prints every grader's prompt.
  - Why: on a large session, one coordinator turn costs several grader turns, and the coordinator did this prep by hand.
- `scripts/proof_dir.py`: each grader proves in its own clone of the graded commit, outside the repository.
  - The clone links in `node_modules` and `.venv` when their pin files match the graded commit and the branch did not change them. `proofDir` in `.claude/pr-grade.json` names other items.
  - A clone registers nothing in the repository. **Before:** graders built worktrees, and one of them leaked at the turn cap.
  - When a root stays behind, the same repository's next grade removes it after a week with no copy made and no report written.
- `grade_block.py merge`: graders write their reports to files, and one call merges them.
  - The result shows where each lens ended, the findings, the lowest score the rules allow, and a draft block.
  - It never joins two findings at one line, and an unproven P2 under `Could not verify` stays a claim. The coordinator keeps the judgment calls.
- `check_then_act.py` marks a window the diff did not change, so a grader does not run the scan again with `--json` to find out.
- `tools/tally_run.py`, outside the plugin, measures a grading run: each subagent's turns, cache reads, model time, and turn-limit stops, from a session transcript.

## 0.5.0

- The grade agent reads its skill at `${CLAUDE_PLUGIN_ROOT}/skills/pr-grade/SKILL.md`, the copy installed with it. It never searches the filesystem.
  - **Before:** graders that searched used up to eight of fifty turns on it, and read three different installed versions.
- The agent's `description` names everything a dispatch gives: the repository and lens file paths, base and head SHAs, lenses, settled checks, declined findings, the callers list, and the L7 scanner output. So a coordinator that never loads the skill still sees the contract.
- The grader has a turn budget it can follow:
  - parallel calls for everything it knows it needs;
  - whole-file reads for short files;
  - `git grep -a` at the graded commit, and shell forms that a worktree-isolated session accepts;
  - a proof ladder of three runs for each finding.
  - "Report by turn 45" is removed, because a grader cannot see its turn number.
- The report ends every lens with a sentence, and adds `Outside my lenses`. An unproven claim that would be a P1 or P2 keeps its lens out of `Verified and clear`, and scores as the finding it would be.
- The grade agent runs at `high` effort, not `xhigh`.
  - Why: most of a grader's turns are reading and judgment. Anthropic's guidance keeps `xhigh` for the hardest coding and agentic work.
- The coordinator:
  - merges the graders' reports before it scores;
  - sends a capped grader one message to report;
  - reviews fix commits and merges before it grades them again;
  - gives every grader the callers list, found with `git grep -a`.

## 0.4.0

- A repository that embeds the scripts can pass in its own rules.
  - `grade_mode.assess` takes `named`, exact paths mapped to the reason each is a silent-failure file.
  - `grade_block.problems` takes `assess`, a selector to use in place of the plugin's.
- `grade_block.py` loads the `grade_mode.py` beside it by path, under a private name, so it does not pick up or replace a caller's own `grade_mode` module.
  - It also no longer adds its directory to `sys.path`, and has no module-level `assess`.
  - A caller that rebound `grade_block.assess`, or imported `grade_mode` after `grade_block` without its own path entry, must pass `assess=` instead.

## 0.3.1

- The fix commits alone set the size of a re-grade: `grade_mode.py --base <the last commit graded>`, and the scanner with the same base for L7.
  - **Before:** without `--base`, the whole branch set the size of every round, so a two-line fix cost what the first grade did.
  - Every lens still runs, and the loop stops at the first round with no P1 or P2.
- When a run fails either way and in the same direction, and the only defect is how the failure reads, the finding is a P3.
  - An example is a traceback where a message belongs. This holds in a re-grade whose fix was about messages.
- pr-grade is public.
  - Installing needs no git access, and `templates/pr-grade-check.yml` needs no `PR_GRADE_TOKEN`.
  - The template also pins its actions by commit SHA.

## 0.3.0

- `scripts/check_then_act.py` lists the check-then-act windows (lens L7) in the functions a branch changed, so a grader starts from a list instead of searching by hand.
  - It reads TypeScript and JavaScript through Node with the repository's own `typescript` package, and Python with the standard library's `ast`.
  - It never gates: it exits 0 whenever it runs, and an empty list clears nothing.
  - The skill runs it once per grade, and gives the output to the grader that holds L7.
- Grade after a correctness review, not at the same time.
  - The graders get the declined findings, each with its reason. A grader reports one again only when it can show the reason is wrong.
  - Any review that returns findings works, and `/code-review` is not necessary.

## 0.2.0

- `templates/pr-grade-check.yml` is a CI workflow that makes the grade a merge gate. It runs the checker from a pinned pr-grade ref, so a pull request cannot edit the check that judges it. It passed a live test in GitHub Actions.
- The README shows a real grade.
- When a `silentCommand` fails, `grade_mode.py` stops with the command's own message, not a traceback.

## 0.1.0

- First release: the pr-grade skill, the `grade` agent, `grade_mode.py`, `grade_block.py check`, and the lens and config templates.
