# Changelog

Claude Code uses `version` in `.claude-plugin/plugin.json` to decide whether an update exists, so every release bumps it. After updating, run `/reload-plugins`.

CI refuses a pull request that changes `skills/`, `agents/`, or `plugin.json` without raising the version and adding its heading here. Label one `hold-release` to merge it unreleased, when a change lands over several pull requests.

## 0.6.0

- `scripts/grade_prep.py`: preparing a grade is one call. It picks the mode, splits the lenses among graders from the lens file's `## Fan-out groups` table, lists the callers of each function the branch modified (TypeScript, JavaScript, and Python; anything else is named as not derived), runs the L7 scan for whoever holds L7, makes the proof copies, and prints every grader's prompt. A coordinator turn on a large session costs several grader turns, and the prep it did by hand now takes one call.
- `scripts/proof_dir.py`: each grader proves in its own clone of the graded commit outside the repository, with `node_modules` and `.venv` linked in when their pin files match the graded commit and the branch did not change them. `proofDir` in `.claude/pr-grade.json` names anything else. A clone registers nothing in the repository, unlike the worktrees graders built before, one of which leaked at the turn cap. A copy left behind is removed after a day.
- `grade_block.py merge`: graders write their report to a file, and one call merges them into where each lens ended, the findings, the lowest score the rules allow, and a draft block. It never joins two findings at one line, and an unproven P2 under `Could not verify` stays a claim. The coordinator keeps the judgment calls.
- `check_then_act.py` marks a window the diff left alone, so a grader no longer re-runs the scan with `--json` to find out.
- `tools/tally_run.py`, outside the plugin: per-subagent turns, cache reads, model time, and turn-limit stops from a session transcript, to measure a grading run.

## 0.5.0

- The grade agent reads the skill it applies at `${CLAUDE_PLUGIN_ROOT}/skills/pr-grade/SKILL.md`, the copy installed with it, and never searches the filesystem. Graders that searched spent up to eight of fifty turns on it and read three different installed versions.
- The agent's `description` names everything a dispatch carries, so a coordinator that never loads the skill still sees the contract: the repository and lens file paths, base and head SHAs, lenses, settled checks, declined findings, the callers list, and the L7 scanner output.
- A turn budget the grader can follow: parallel calls for everything it already knows it needs, whole-file reads for short files, `git grep -a` at the graded commit, shell forms a worktree-isolated session accepts, and a proof ladder of three runs per finding. "Report by turn 45" is gone; a grader cannot see its turn number.
- The report closes every lens with a sentence and adds `Outside my lenses`. An unproven claim that would be a P1 or P2 keeps its lens out of `Verified and clear` and scores as the finding it would be.
- The grade agent runs at `high` effort instead of `xhigh`. Most of a grader's turns are reading and judgment, and Anthropic's guidance keeps `xhigh` for the hardest coding and agentic work.
- The coordinator merges graders' reports before it scores, sends a capped grader one message to report, reviews fix commits and merges before re-grading them, and passes the callers list, found with `git grep -a`, to every grader.

## 0.4.0

- A repository that embeds the scripts can pass in its own rules. `grade_mode.assess` takes `named`, exact paths mapped to the reason each is a silent-failure file, and `grade_block.problems` takes `assess`, a selector to use in place of the plugin's.
- `grade_block.py` loads the `grade_mode.py` beside it by path under a private name, so it no longer picks up, or replaces, a caller's own `grade_mode` module. It also no longer adds its directory to `sys.path` or has a module-level `assess`, so a caller that rebound `grade_block.assess`, or imported `grade_mode` after `grade_block` without its own path entry, has to pass `assess=` instead.

## 0.3.1

- A re-grade is sized by the fix commits alone: `grade_mode.py --base <the last commit graded>`, and the scanner with the same base for L7. Without `--base` every round was sized by the whole branch, so a two-line fix cost what the first grade did. Every lens still runs, and the loop ends at the first round with no P1 or P2.
- When a run fails either way, in the same direction, and the only defect is how the failure reads, such as a traceback where a message belongs, the finding is a P3. That holds in a re-grade whose fix was itself about messages.
- pr-grade is public. Installing needs no git access, and `templates/pr-grade-check.yml` needs no `PR_GRADE_TOKEN`. The template also pins its actions by commit SHA.

## 0.3.0

- `scripts/check_then_act.py`: lists the check-then-act windows (lens L7) in the functions a branch changed, so a grader starts from a list instead of hunting by hand. It reads TypeScript and JavaScript through Node with the repository's own `typescript` package, and Python with the standard library's `ast`. It never gates: exit 0 whenever it runs, and an empty list clears nothing. The skill runs it once per grade and hands the output to whoever holds L7.
- Grade after a correctness review, not alongside it. The declined findings, each with its reason, go to the graders, who report one again only when they can show the reason is wrong. Any review that returns findings works; `/code-review` is not required.

## 0.2.0

- `templates/pr-grade-check.yml`: a CI workflow that makes the grade a merge gate. It runs the checker from a pinned pr-grade ref, so a pull request cannot edit the check that judges it. Tested live in GitHub Actions.
- The README shows a real grade.
- `grade_mode.py` stops on a failed `silentCommand` with the command's own message instead of a traceback.

## 0.1.0

- First release: the pr-grade skill, the `grade` agent, `grade_mode.py`, `grade_block.py check`, and the lens and config templates.
