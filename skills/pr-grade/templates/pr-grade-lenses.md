# pr-grade lenses: <repository>

`/pr-grade` reads this file when it runs in this repository. Each lens keeps its number and question from the skill and takes this codebase's form below. Where this file and the skill differ, this file wins.

Replace every `<...>`. Delete a section that adds nothing over the skill; keep its heading, since `grade_block.py` requires every lens a `### L<n>.` heading names.

## Blast radius

<The caller search for this codebase: languages, directories to include, vendored ones to exclude.>

## The lenses

### L1. Abandoned work

<Where work outlives its caller here: timeouts, cancel paths, late results from a queue or a provider. Name the fence that makes a late result harmless, with its file.>

### L2. Shared bounded resources

<The real bounds: the database's connection or write lock, pools, queues, rate limits, leases. Where each is configured, and who shares it.>

### L3. Unguarded failure reach

<What the frameworks here do with an uncaught failure, or, if another reach question matters more (who may call an owner's write method), that question instead.>

### L4. Every other caller

<The callers people forget here: CI jobs, scripts, later work that binds a default.>

### L5. State that outlives its reason

<What this codebase writes and must end: rows left pending, leases, temp directories, blobs.>

### L6. Contract drift

<Everything that describes behaviour here: specs, decision records, registries, generated docs, tool descriptions.>

### L7. Check then act

<Which operations are atomic here and which are not: what a single transaction covers, where an await or a second read opens a window. Name the transaction and lock callees and the outside clients `check_then_act.py` should know, and add them under `checkThenAct` in `.claude/pr-grade.json`.>

### L8. Failure direction

<The defaults and fallbacks worth a sentence: optional dependencies, `??` values, catches that return something neutral.>

## Already settled

<The deterministic checks graders take as given and never re-run: test suites, type checks, linters, architecture tests, mutation tests. Grade what they cannot see.>

## Proof

<How a finding is proven here, for example: a test that fails on an assertion naming the defect, run against the unfixed code and committed on its own. Say where a proof test goes in a grader's proof copy so its imports resolve, and the command that runs that one file, for example `npx vitest run tests/unit/proof.test.ts` or `.venv/bin/python -m pytest tests/test_proof.py`. If a proof needs anything uncommitted beyond `node_modules` and `.venv` (a `.env.test`, generated code, a nested workspace's `node_modules`), name it under `proofDir` in `.claude/pr-grade.json`.>

## Fan-out groups

| Group | Lenses | Shared reading |
|---|---|---|
| Timing | L1, L2, L7 | <every transaction, await, and status read in the diff> |
| Reach | L3, L4 | <the caller list and any ownership registry> |
| Lifetime and contract | L5, L6, L8 | <the specs cited and every fallback> |

## Growing this list

When a review finds a defect no lens here names, add the lens with where it was found.
