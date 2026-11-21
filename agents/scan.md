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

Write your whole answer, once, with the Write tool, to the path your prompt names. Then reply with that path alone. Do not edit, stage, or commit anything in the repository, and write no other file.
