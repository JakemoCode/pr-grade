# CLAUDE.md

pr-grade: a Claude Code plugin that grades a change through nine lenses before it is pushed. Install name `pr-grade@jakemocode`.

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
- Write each `CHANGELOG.md` bullet as "`file`: <added/fixed/changed/removed> X with Y to <outcome>". Put the old behavior in a **Before** sub-bullet and the grade that exposed it in a **Seen on** sub-bullet. Name the file the release's commit touched: grader behavior is `agents/grade.md`, coordinator rules are `SKILL.md`.
- Keep the `## X.Y.Z` headings. `.github/scripts/check_release.py` matches them with `^## <version>`.
- A docs-only PR (`README.md`, `CLAUDE.md`) fails the `grade` check, because `grade_block.py check` has no docs-only exemption. The `protecc mister main` ruleset requires only `test (3.9)` and `test (3.12)`, so the PR stays mergeable. Say the failure is expected. Do not propose an exemption or an in-thread grade for docs PRs unless Jake raises it. PR #37, PR #51 and PR #55 merged over it.
