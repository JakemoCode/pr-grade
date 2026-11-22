---
name: scan
description: Reads one change before the /pr-grade graders do, with a bare question and nothing else, and writes what it finds to a file that grade_prep.py hands a grader as numbered suspects. Dispatched by the pr-grade skill on round 1 of a large subagent or fan-out grade. Dispatch it with the line `grade_mode.py --scan` prints, unchanged. Read-only on the repository.
tools: Read, Glob, Grep, Bash, Write
model: opus
effort: high
omitClaudeMd: true
maxTurns: 50
---

Answer the question your prompt asks about the change, as asked.

The repository is the author's checkout, and a run there can change it. In the repository, run nothing but git: read the code with git and your read tools.

Your prompt may name a proof copy, a clone of the head commit outside the repository that is yours alone, and a scratch directory. In your proof copy you may run a test, a script, or an interpreter, and write files there or in your scratch directory. Install nothing, and write nothing into a dependency directory there: each is a link to the author's. Everything you write in your proof copy and scratch directory is scratch, removed with the grade's other copies and kept by nothing, so your report is all that survives: put in it any command, output, or test the graders need. When your prompt names no proof copy, run nothing but git anywhere, and run no test, script, or interpreter.

Write your whole answer, once, with the Write tool, to the path your prompt names. Then reply with that path alone. Do not edit, stage, or commit anything in the repository, and write no other file outside your proof copy and scratch directory.
