# Contributing

What a pull request to pr-grade has to carry. [CLAUDE.md](CLAUDE.md) has the working rules for a Claude Code session in this repository; this file is the short list for anyone.

## Branch

- Cut every branch from fresh `origin/main` in a worktree: `git fetch origin main && git worktree add .claude/worktrees/<branch> -b <branch> origin/main`. `.claude/worktrees/` is gitignored.
- Name it `type/short-description`, for example `fix/merge-anchor`.
- Commits follow [Conventional Commits](https://www.conventionalcommits.org/): `type(scope): subject`, imperative, at most 50 characters, with a body that says why.

## Tests

- Run `python3 -m unittest discover -s tests` before pushing. CI runs it on Python 3.9 and 3.12.
- The TypeScript adapter tests need a `typescript` package. CI installs one and sets `PR_GRADE_TYPESCRIPT` to it; locally they skip when none is found.
- A new test is watched failing against the defect it pins before the fix lands.

## Release

- A change to `skills/`, `agents/`, or `.claude-plugin/plugin.json` raises `version` in `plugin.json` and adds a `## <version>` heading to [CHANGELOG.md](CHANGELOG.md), with a bullet per change in the shape the changelog's preamble gives, naming the file it touched. `.github/scripts/check_release.py` refuses a pull request whose version did not rise or whose heading is missing.
- A change that lands over several pull requests carries the `hold-release` label on all but the last.

## Grade

- Every pull request carries a `## Grade` block from `/pr-grade` in its body: `Score: 5/5`, `Graded` at the head commit, every lens in `Verified and clear`. The `grade` check is required, and a push that changes code after the grade fails it until those commits are re-graded. `dependabot/` branches are exempt.
- A pull request body has a Summary, Evidence (the checks that passed), Merge Danger (door, blast radius, grade notes), and the Grade block. Pull request #48 is the model.

## Merging

- `main` takes merge commits only, after the `test (3.9)`, `test (3.12)`, and `grade` checks pass on a branch that is up to date with `main`. No force pushes, no deletion, no bypass.
- Review threads must be resolved. No approval count is required.

## Security

Report a vulnerability as [SECURITY.md](SECURITY.md) says, never in a public issue.
