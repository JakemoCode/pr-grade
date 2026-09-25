# Changelog

Claude Code uses `version` in `.claude-plugin/plugin.json` to decide whether an update exists, so every release bumps it. After updating, run `/reload-plugins`.

## 0.2.0

- `templates/pr-grade-check.yml`: a CI workflow that makes the grade a merge gate. It runs the checker from a pinned pr-grade ref, so a pull request cannot edit the check that judges it. Tested live in GitHub Actions.
- The README shows a real grade.
- `grade_mode.py` stops on a failed `silentCommand` with the command's own message instead of a traceback.

## 0.1.0

- First release: the pr-grade skill, the `grade` agent, `grade_mode.py`, `grade_block.py check`, and the lens and config templates.
