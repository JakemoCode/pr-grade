# Security

Report a vulnerability privately through [GitHub's private vulnerability reporting](https://github.com/JakemoCode/pr-grade/security/advisories/new), not a public issue.

The scripts run on your machine and in your CI with the permissions you give them. `grade_block.py check` calls `gh` with your token. `grade_mode.py` runs the `silentCommand` in your `.claude/pr-grade.json` through a shell, and the check-then-act scanner loads your repository's `typescript` package. Treat a change to that config, or to the `typescript` package, like a change to your CI.
