## 7. Fix, then re-grade

Start each repair from the finding's proof test. The merge printed a `mv` line for each test a grader kept: run it, run the test at its repository path and see it red, and commit it alone, before the fix. The fix commit turns that same test green, and the pull request carries the test that reached the trigger instead of a second one written from the finding's description.

Commit the fixes and review them the way step 1 reviews the change, keeping what you declined for the graders. A merge from the base branch is a change too. On one branch, two graders each spent their whole budget proving a defect a merge brought in, and the review run after them found it in twenty turns.

Run the old `remove:` line, which refuses while a kept test is still there (`--drop-proofs` deletes them, for a P3 you leave listed), then prepare the re-grade from the fix commits alone:

```sh
python3 <base>/scripts/grade_prep.py --base <the last commit graded> --settled '...' --declined '...'
```

Grade the fix commits in the mode it prints, with every lens. Pass the same `--skip` as the first round: the prep does not remember it, and a lens left out of it is graded and drafted clear instead of not applicable. The copies it makes hold the fixed code, and the L7 scan covers the same commits. The grade block keeps the whole branch's mode, which the prep reads from `origin/main` (or `--branch-base`), prints beside the round's, and passes to the merge's draft block. A repair is a change and gets the same treatment as any other, so a two-line fix to ordinary code is graded in-thread even inside a pull request that needed a fan-out, and a fix that touches silent-failure code gets at least a subagent. Without `--base` the selector sizes the whole branch, and every round costs what the first one did.

This is not ceremony. On the pull request above:

- the repair for L5 introduced the L4 defect;
- the repair for L2 introduced the L1 defect;
- the repair for L1 introduced the L7 defect, found by an external reviewer fifteen minutes later, after these lenses had been run against that very fix.

Every time, the local suite stayed green. **A fix that satisfies a finding is the most likely place for the next one**, and the lens most likely to catch it is the one written from the finding you just repaired.

The loop ends at the first round with no P1 or P2. A P3 can stay listed; one you fix anyway is a change, ships with its proof test like a P1's fix, and its fix is graded like the rest. A P3 left listed ships nothing, since the suite stays green.

The `## Grade` block keeps the whole branch's mode and moves `Graded` to the last fix commit graded. `grade_block.py check` compares `Mode` with every file the pull request touches, so a block that names a fix's lighter mode is refused.
