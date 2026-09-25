# Changelog

Claude Code uses `version` in `.claude-plugin/plugin.json` to decide whether an update exists, so every release bumps it. After updating, run `/reload-plugins`.

## 0.3.0

- `scripts/check_then_act.py`: lists the check-then-act windows (lens L7) in the functions a branch changed, so a grader starts from a list instead of hunting by hand. It reads TypeScript and JavaScript through Node with the repository's own `typescript` package, and Python with the standard library's `ast`. It never gates: exit 0 whenever it runs, and an empty list clears nothing. The skill runs it once per grade and hands the output to whoever holds L7.
- `checkThenAct` in `.claude/pr-grade.json` overrides the scanner's name lists. An existing config without it gets the defaults.
- Grade after a correctness review, not alongside it. The declined findings, each with its reason, go to the graders, who report one again only when they can show the reason is wrong. Any review that returns findings works; `/code-review` is not required.

## 0.2.0

- `templates/pr-grade-check.yml`: a CI workflow that makes the grade a merge gate. It runs the checker from a pinned pr-grade ref, so a pull request cannot edit the check that judges it. Tested live in GitHub Actions.
- The README shows a real grade.
- `grade_mode.py` stops on a failed `silentCommand` with the command's own message instead of a traceback.

## 0.1.0

- First release: the pr-grade skill, the `grade` agent, `grade_mode.py`, `grade_block.py check`, and the lens and config templates.
