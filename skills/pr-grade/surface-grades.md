## 9. Grading a surface

A surface grade applies the same fourteen lenses to code that no branch is changing: the files under some paths, as committed at HEAD. The base is the empty tree, so the prep treats every function as if a diff had introduced it. Nothing about it is a pull request, so the grade block it drafts is not one either.

### When

Only on a trigger, never on a cadence. The triggers:

- A class of bug escaped every review. On 2026-11-04 it was what a screen shows between a write and its subscription echo.
- A hotspot is about to be refactored.
- A milestone closes and the hotspot list names the surfaces that moved.

A surface nobody touched buys nothing and costs about six branch grades.

### How

Run one command per surface, from inside the repository with the code committed:

```sh
python3 <base>/scripts/grade_prep.py --surface <path> [<path> ...] --settled '...' --declined '...'
```

Choose surfaces by layer, so each grader reads one kind of code. For a React and Firestore app that is hooks, repositories, shared components, pages, engine, and views. `--base` and `--branch-base` are refused, since a surface has no before. Tests under the paths are left out of the change and the caller list, and stay in the proof copy as evidence.

Dispatch the graders with the prompts as section 2 says, passing each printed line or pasting the printed blocks, and run the `merge:` line. The mode comes from the surface's files by the same rule as a branch. Callers outside the surface are listed, the ones inside are counted, and L7 scans the whole surface instead of a diff. The draft block carries a `Surface:` line after `Mode:`.

### Reading the result

- The score is the surface's. Nothing enforces it, and `grade_block.py check` refuses a body whose block carries a `Surface:` line.
- Every P2 still needs a failing test before it counts, as in section 4.
- Findings go into the repository's tracker, not a pull request body. A fix is a branch like any other and gets a branch grade.

### Cost

The baby-day-planner run on 2026-11-04 graded six surfaces and 139 production files. It used about 900k subagent tokens and 25 minutes of wall clock with six graders in parallel, and proved eight P2s and fifteen P3s.
