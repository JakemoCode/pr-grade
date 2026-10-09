# CLAUDE.md

pr-grade: a Claude Code plugin that grades a change through eight lenses before it is pushed. Install name `pr-grade@jakemocode`.

## Grader agent

- Keep `agents/grade.md` at `effort: high`. Revisit `xhigh` only if one of these shows up:
  - `Could not verify` items pile up.
  - Proofs regularly use all three runs.
  - A reviewer finds a P1 or P2 in code a grader marked clear.
- Name grader output files `grade.md`. Claude Code refuses a subagent's Write to any basename that starts with report, summary, findings, or analysis and ends in `.md`.
- Have subagents write file text with Write, not a Bash heredoc.

## Releases

- After a release, give Jake the shell form of the update, then `/reload-plugins` in the session:
  `! claude plugin marketplace update jakemocode && claude plugin update pr-grade@jakemocode`
- Use this form in PR bodies, issue bodies, and chat. `/plugin update` opens the generic menu instead of updating.
