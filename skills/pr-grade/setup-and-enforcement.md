## 0. Repository setup

Two optional files at the repository root tune this skill. Read both first when they exist.

- **`.claude/pr-grade-lenses.md`** restates each lens for this codebase: its real bounded resources, its callers, its proof recipe, its fan-out groups. Where it and this file differ, it wins. [`templates/pr-grade-lenses.md`](templates/pr-grade-lenses.md) is a starting point.
- **`.claude/pr-grade.json`** tells the mode selector which paths are silent-failure code and which are ordinary; code it names as neither grades as silent. [`templates/pr-grade.json`](templates/pr-grade.json) shows every key. Its `formats` declare the records the repository keeps, for L14, and its `envSweep` the command that runs the tests a change touches, for L16; one that declares neither pays nothing.

Without them the generic lenses below apply, and the selector uses its defaults.

## 8. Enforce it

`grade_block.py check <pr>` refuses a pull request whose grade block is missing, below 5/5, missing a lens, in a cheaper mode than its files need, or older than its code:

```sh
python3 <base>/scripts/grade_block.py check <pr>
```

Run it before marking a PR ready. To make it a merge gate, copy [`templates/pr-grade-check.yml`](templates/pr-grade-check.yml) to `.github/workflows/` and make its check required. It runs on `opened`, `edited`, `synchronize`, `reopened`, and `ready_for_review`, so a commit pushed after the grade fails until it is graded and a block pasted into the body is read at once. It runs the checker from a pinned pr-grade ref, and the checker reads the config and lens file from the base branch, so a pull request cannot loosen the rules it is checked against.
