#!/usr/bin/env python3
"""The grade block a pull request carries, and the check that refuses a PR without a current one.

    python3 grade_block.py check <pr> [--repo owner/name]
    python3 grade_block.py merge <proof root> [--report <group>=<file or ->]

Run it from inside the repository, locally before marking a PR ready or in CI on every push. It needs
`gh`, authenticated, with read access to the repository's contents and pull requests.

The block is a `## Grade` section in the PR body, outside any code block, one field per line:

    ## Grade

    Mode: subagent
    Graded: <the full SHA of the last commit graded>
    Score: 5/5
    Blocking: nothing
    Verified and clear: L1, L2, L3, L4, L5, L6, L7, L8, L9, L10, L11, L14, L15, L16
    Could not verify: none

A grade of paths instead of a change (`grade_prep.py --surface`) adds a `Surface:` line after `Mode`.
`check` refuses any block that carries one: it graded code no pull request changed.

`check` refuses the PR when the block is missing or below 5/5; when a lens the lens file defines (or
L1 to L11 and L14 to L16, without one) is missing from `Verified and clear`; when `Mode` is cheaper than grade_mode.py
requires for the PR's files; when the graded commit is not in the PR's history or a file the grade
must cover changed after it; when GitHub's lists are cut off; or when the PR moved during the check.

It skips a draft, and a PR whose branch does not match `requireGrade.branches` in the config when that
key is set. The config and the lens file are read from the PR's base branch, so a PR cannot loosen the
rules it is checked against.

`merge` reads the report each grader wrote under the root grade_prep.py printed, or the one `--report`
gives for a group whose grader replied without writing its file. It prints where every lens ended
(clear, not applicable, a finding, an open claim, or unaccounted), the findings with any two at one line
flagged, the lowest score the rules allow, what it leaves to the coordinator's judgment, and the whole
block, each finding below the fields with the lines its grader wrote under it and its `Proof test:` at
the repository path. It flags a finding whose line touches no line the graded diff changed, naming the
nearest changed line and the function it sits in, and never refuses one: a defect can sit in a caller
the change left alone. The block keeps each grader's notes in `Verified and clear` and writes a lens
grade_prep.py skipped as `L2 (not applicable)`. It settles nothing that needs judgment: two lines with
one cause, a clearance against a proof, a 3 for a design decision, or a 1. While a duplicate line, an
`anchor:` flag, an open claim that would block, a defect outside its lens, an unassessed lens, a
proof test with no repository path, a handed suspect with no line, or a proven suspect naming no finding is open, its `Score` reads
`<floor>/5 floor; ...`, which `check` refuses until the coordinator writes one.

`grade.json` may hold `suspects`, each group's count of suspects its prompt file numbered `S1` to `S<n>` under
`Suspects from a first read:`. The merge reads each grader's `Suspects:` lines: a `could not verify` line is an open
claim at its rank, and a `refuted` line is one too, as refuted without a run, unless a command in it gives the code an
input and its output follows. A group with no `suspects` count and no `Suspects:` field merges as it did before.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent


def _load(name: str, file: str):
    """A script beside this file, loaded by path under a private name. A repository that embeds these
    scripts can have a `grade_mode` module of its own, which this neither picks up nor replaces."""
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


grade_mode = _load('_pr_grade_mode', 'grade_mode.py')
CONFIG, MODES, load_config, repo_root = grade_mode.CONFIG, grade_mode.MODES, grade_mode.load_config, grade_mode.repo_root
# What check_then_act.py hands its TypeScript adapter, which names each function with its span.
TS_ADAPTER = HERE / 'check_then_act_ts.cjs'
TS_SUFFIXES = ('.ts', '.tsx', '.mts', '.cts', '.js', '.jsx', '.mjs', '.cjs')

# The skill's lenses, numbered as forgesmith-os's lens file numbers them; L12 and L13 are unassigned.
DEFAULT_LENSES = [f'L{n}' for n in range(1, 12)] + ['L14', 'L15', 'L16']
# GitHub lists at most this many files, and says nothing when it stops.
COMPARE_CAP = 300
PR_FILE_CAP = 3000
SECTION = re.compile(r'(?ms)^##[ \t]+Grade[ \t]*$(.*?)(?=^##[ \t]|\Z)')
FIELD_NAMES = 'Mode|Surface|Graded|Score|Verified and clear'
FIELD = re.compile(rf'(?m)^({FIELD_NAMES}):[ \t]*(.*?)[ \t]*$')
# A finding's line the merge prints into the block that `parse` would read as one of the block's fields.
FIELD_LEAD = re.compile(rf'^({FIELD_NAMES}):')
HEADING_LEAD = re.compile(r'^#')
# A fence as `without_code` reads one: at the start of the line, closed by a line that starts with the same run.
FENCE = re.compile(r'^(`{3,}|~{3,})')
SURFACE_REFUSED = 'a surface grade is not a pull request grade'
LENS_HEADING = re.compile(r'(?m)^###[ \t]+(L\d+)\.')
LENS_ID = re.compile(r'L\d+\b')
LEAD_ID = re.compile(r'(L\d+)\b[ \t]*')
SHA = re.compile(r'[0-9a-f]{40}')
REPORT_LINE = re.compile(r'^(Score|Blocking|Verified and clear|Could not verify|Outside my lenses|L7 candidates|Suspects|L\d+):'
                         r'[ \t]*(.*)$')
# `P1 L4 <file>:<line> in <symbol> - <title>`, or `<file>:<start>-<end>`. The symbol runs to the first ` - `,
# so a symbol written with words after it still parses, and a finding without one still reads. A column
# after the line, as in `<file>:78:5`, is dropped.
FINDING = re.compile(r'^(P[123])[ \t]+((?:L\d+[ \t,]*)*)(\S+?):(\d+)(?::\d+)?(?:-(\d+))?(?:[ \t]+in[ \t]+(.+?))?[ \t]+-[ \t]+(.+)$')
# The line under a finding naming the test the grader kept, and the repository path it ran at. Each path
# may be written as inline code, which keeps a space or comma inside it; a parenthetical may sit between the
# file and `ran at`; words after the second path are not part of it.
PROOF_TEST = re.compile(r'^Proof test:[ \t]*(?:`([^`]+)`|([^`\s,;]+))'
                        r'(?:[ \t]*(?:\([^)]*\))?[ \t]*,?[ \t]*ran at[ \t]+(?:`([^`]+)`|([^`\s,;]+)))?')
BLOCKING = re.compile(r'\bP[12]\b')
BULLET = re.compile(r'^[-*+][ \t]+')
NEW_ITEM = re.compile(r'([-*+]|\d+[.)])[ \t]|(L\d+|P[123])\b')
# `L2's pool releases` continues a claim, unless its line names a rank or the claim above is a P3.
LENS_POSSESSIVE = re.compile(r"L\d+['’]s\b")
RANK = re.compile(r'\bP[123]\b')
SKIPPED = 'not applicable: skipped by grade_prep.py --skip'
# A line of the `Suspects:` field: `S3 proven: ...`, `S3 - Proven, ...`, `S3. could not verify ...`.
SUSPECT = re.compile(r'^S(\d+)\b[ \t]*[.:)-]?[ \t]*(.*)$')
VERDICT = re.compile(r'^(proven|refuted|could not verify)\b[ \t]*[:,.-]?[ \t]*(.*)$', re.I)
COMMAND = re.compile(r'\bCommand:[ \t]*(.*?)(?=[ \t]+Output:|$)', re.M)
OUTPUT = re.compile(r'\bOutput:[ \t]*(.*)', re.S)
FILE_LINE = re.compile(r'([^\s`(]+?):(\d+)')
# Programs that execute code. A refutation is a run only when one of its commands starts one of these, or a script in
# the repository: a grep that shows a guard exists is an argument, and the defect PR 1435 lost was refuted that way.
# A runner missing here fails closed, to the coordinator's list; a reader missing from a list of readers would not.
RUNNER = re.compile(r'(python|pypy)[\d.]*|node|npx|npm|pnpm|yarn|bun|deno|tsx|ts-node|pytest|py\.test|vitest|jest|'
                    r'mocha|go|cargo|rustc|make|ruby|rake|rspec|perl|php|phpunit|java|mvn|gradle|dotnet|uv|uvx|poetry|'
                    r'pipenv|tox|nox|swift|gcc|cc|clang|sqlite3|psql|docker')
SEPARATORS = {'&&', '||', '|', ';', '&', '(', ')', ';;', '|&'}
# Commands that start the program after their own words, and the options among those words that take a value.
WRAPPERS = {'env', 'xargs', 'timeout', 'nice', 'command', 'time', 'sudo', 'nohup', 'exec'}
WRAPPER_VALUES = {'-u', '-C', '-S', '-s', '-k', '-n', '-I', '-L', '-P', '-d', '-E', '-a', '-g', '--unset', '--chdir',
                  '--signal', '--kill-after'}
DURATION = re.compile(r'\d+(\.\d+)?[smhd]?')
SHELLS = {'sh', 'bash', 'zsh', 'dash'}


def without_code(markdown: str) -> str:
    """Drop fenced blocks and code spans, so a quoted example block is never read as the grade."""
    fenced = re.sub(rf'(?ms){FENCE.pattern}.*?^\1[^\n]*$', '', markdown)
    return re.sub(r'`[^`\n]*`', '', fenced)


def lens_ids(text: str | None) -> list[str]:
    """Every lens the lens file text defines as a `### L<n>.` heading. A lens added there is required
    with no code change. The skill's fourteen apply without a file, or when no heading matches, so a
    reworded heading can never leave nothing required."""
    return (LENS_HEADING.findall(text) if text else []) or DEFAULT_LENSES


def _note_end(text: str) -> int | None:
    """The index after the parenthesis that closes the one `text` opens with, or None when none does."""
    depth = 0
    for i, ch in enumerate(text):
        depth += (ch == '(') - (ch == ')')
        if depth == 0:
            return i + 1
    return None


def verified_lenses(text: str) -> set[str]:
    """The lenses a `Verified and clear` value clears. Each comma-separated item is lens ids, each alone
    or followed by a note in parentheses: `L1 L2` and `L1 (a) L2 (b)` clear both, `L3 (L2 not applicable)`
    clears L3 only, and `L2 not applicable` clears nothing, since only the grader knows what the words
    meant."""
    return set(cleared_notes(text))


def cleared_notes(text: str) -> dict[str, str]:
    """Each lens a `Verified and clear` value clears, as `verified_lenses` reads it, with the notes that
    follow its id joined by a space, or an empty string for a bare id."""
    items, depth, item = [], 0, ''
    for ch in text + ',':
        if ch == ',' and depth == 0:
            items.append(item)
            item = ''
            continue
        depth = max(depth + (ch == '(') - (ch == ')'), 0)
        item += ch
    cleared: dict[str, str] = {}
    for item in items:
        rest, found, last = item.strip().rstrip('.'), {}, None
        while rest:
            lead = LEAD_ID.match(rest)
            end = None if lead or not rest.startswith('(') else _note_end(rest)
            if lead:
                last = lead[1]
                found.setdefault(last, [])
                rest = rest[lead.end():]
            elif end is not None:
                if last is not None:
                    found[last].append(rest[:end])
                rest = rest[end:].lstrip()
            else:
                found = {}
                break
        for lens, notes in found.items():
            cleared[lens] = ' '.join(note for note in (cleared.get(lens, ''), *notes) if note)
    return cleared


def parse(body: str) -> dict[str, str] | None:
    """The block's fields, or None when the body has no `## Grade` section. A body edited in GitHub's
    web UI comes back with CRLF endings."""
    section = SECTION.search(without_code(body.replace('\r\n', '\n')))
    if section is None:
        return None
    # The first of each field is the block's own: a pasted agent report below it repeats `Score:`.
    fields: dict[str, str] = {}
    for name, value in FIELD.findall(section.group(1)):
        fields.setdefault(name, value)
    return fields


def problems(block: dict[str, str] | None, *, pr_files: list[str], lenses: list[str], compare: dict | None,
             root: Path, config: dict, assess: Callable[[list[str], Path, dict], tuple] | None = None) -> list[str]:
    """What stops the PR going ready. `pr_files` is every path the PR touches, both sides of a rename.
    `compare` is GitHub's comparison of the graded commit with the PR head: its `status` and the paths
    it changed, or `error` when it could not be read. `assess` replaces grade_mode's selector for a
    repository with rules of its own; it takes and returns what `grade_mode.assess` does."""
    if block is None:
        return ['The body has no `## Grade` section. Grade the branch with /pr-grade and put its block in the body.']
    if 'Surface' in block:
        return [SURFACE_REFUSED]
    required, reasons, covered = (assess or grade_mode.assess)(pr_files, root, config)
    if required not in MODES:
        return [f"The selector returned mode {required!r}, which is not one of {', '.join(MODES)}."]
    found = []
    mode = block.get('Mode', '')
    if mode not in MODES:
        found.append(f"Grade mode {mode!r} is not one of {', '.join(MODES)}.")
    elif MODES.index(mode) < MODES.index(required):
        why = '; '.join(f'{path}: {reason}' for path, reason in sorted(reasons.items())) or 'its code file count'
        found.append(f'Graded {mode}, but this change requires {required} ({why}). Re-grade.')
    if block.get('Score') != '5/5':
        found.append(f"Grade score is {block.get('Score') or 'missing'}; the PR goes ready only at 5/5.")
    verified = verified_lenses(block.get('Verified and clear', ''))
    missing = [lens for lens in lenses if lens not in verified]
    if missing:
        # 0.16.0 renumbered the lenses, so a block from before it lacks L14 and lists an L9 that meant old state.
        renumbered = ' The ids changed in 0.16.0 (L9 is Correctness, old state is L14): re-grade rather than add ids.' if 'L14' in missing else ''
        found.append(f"`Verified and clear` leaves out {', '.join(missing)}. Apply every lens.{renumbered}")
    graded = block.get('Graded', '')
    if not SHA.fullmatch(graded):
        found.append(f'`Graded` must be the full 40-character SHA of the graded commit, not {graded or "nothing"}.')
    elif 'error' in compare:
        found.append(f"Graded commit {graded} could not be compared on GitHub ({compare['error']}). Push it, or "
                     'check the token can read contents.')
    elif compare['status'] not in ('ahead', 'identical'):
        found.append(f"Graded commit {graded} is not in this PR's history ({compare['status']}). Re-grade the branch.")
    elif len(compare['files']) >= COMPARE_CAP:
        found.append(f'GitHub lists {COMPARE_CAP} files changed after graded commit {graded}, its cap, so the rest '
                     'are unseen. Re-grade at the head commit.')
    else:
        # The comparison also spans the base branch's commits when it was merged in; only this PR's files count.
        changed = [path for path in compare['files'] if path in covered]
        if changed:
            found.append(f"Code changed after the graded commit: {', '.join(changed)}. Re-grade those commits.")
    return found


def blocks(claim: str) -> bool:
    """Whether an open claim counts against the score: a P1 or P2, or no rank at all."""
    return bool(BLOCKING.search(claim)) or not re.search(r'\bP3\b', claim)


def _opens_item(line: str, field: str, above: str) -> bool:
    """Whether an indented line starts an item of `field` rather than continuing `above`. Only under
    `Could not verify` does a split count against the score, so only there is a possessive lens id prose,
    and only below a claim that already blocks: joined to a P3, it would stop counting."""
    if field == 'Could not verify' and LENS_POSSESSIVE.match(line) and not RANK.search(line) and blocks(above):
        return False
    return bool(NEW_ITEM.match(line))


def _inside(path: str, roots: tuple[str, ...]) -> str:
    """An absolute `path` as the path inside the root it sits in, or as written when it sits in none. The
    longest root is tried first, so a proof copy under the repository reads as the copy. The path is tried
    resolved too: roots are the resolved paths git prints, and a grader may cite /var for /private/var."""
    if not path.startswith('/'):
        return path
    for spelling in (path, os.path.realpath(path)):
        for root in sorted(roots, key=len, reverse=True):
            if spelling.startswith(root + '/'):
                return spelling[len(root) + 1:]
    return path


def _program(words: list[str]) -> list[str]:
    """`words` from the program they start: past `NAME=value` words, and past each wrapper with its options, the
    values those options take, and a duration, so `env -u HOME grep` and `timeout -s KILL 5 cat` start at the read."""
    i = 0
    while i < len(words):
        if re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', words[i]):
            i += 1
        elif os.path.basename(words[i]) in WRAPPERS:
            i += 1
            while i < len(words) and (words[i].startswith('-') or '=' in words[i] or DURATION.fullmatch(words[i])):
                i += 2 if words[i] in WRAPPER_VALUES else 1
        else:
            break
    return words[i:]


def is_run(command: str) -> bool:
    """Whether `command` gives the code an input: some command in it, split at `&&`, `|`, `;` and the like, starts a
    program RUNNER names or a script by a path inside the repository (relative, such as `./scripts/check.sh`). A
    shell's `-c` text, in any flag holding `c` such as `-lc`, is judged the same way. Anything else, a search, a read,
    or prose, is not a run, so a runner this does not know fails closed."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        tokens = re.sub(r'(&&|\|\||[|;&()])', r' \1 ', command).split()
    segments, segment = [], []
    for token in tokens + [';']:
        if token in SEPARATORS:
            segments.append(segment)
            segment = []
        else:
            segment.append(token)
    for words in segments:
        words = _program([word for word in words if word.strip()])
        if not words:
            continue
        program, name = words[0], os.path.basename(words[0])
        if name in SHELLS:
            flag = next((i for i, word in enumerate(words[1:], 1) if re.fullmatch(r'-[A-Za-z]*c[A-Za-z]*', word)), None)
            if flag is not None:
                if flag + 1 < len(words) and is_run(words[flag + 1]):
                    return True
            elif any(not word.startswith('-') for word in words[1:]):
                return True
        elif RUNNER.fullmatch(name) or ('/' in program and not program.startswith(('/', '~'))):
            return True
    return False


def _suspects(items: list[str]) -> list[dict]:
    """The `Suspects:` field's items as `id`, `number`, `verdict` (`proven`, `refuted`, `could not verify`, or None
    for a line naming none of them), `rank` (the first `P<n>` on the line, or None), `text` (the words after the
    verdict on the item's first line), `runs` (each `Command:` in it, in order, as `command` and its own `output`: the
    text after an `Output:` before the next `Command:`, or else the lines between the two; None when there is none),
    and `item`, the whole of it."""
    read = []
    for item in items:
        first, _, rest = item.partition('\n')
        head = SUSPECT.match(first)
        if not head:
            continue
        verdict = VERDICT.match(head[2])
        found = list(COMMAND.finditer(item))
        runs = []
        for command, after in zip(found, found[1:] + [None]):
            span = item[command.end():after.start() if after else len(item)]
            said = OUTPUT.search(span)
            lines = (said[1] if said else span).splitlines()
            output = '\n'.join(line for line in lines if line.strip() and not FENCE.match(line.strip())).strip() or None
            runs.append({'command': command[1].strip().strip('`').strip(), 'output': output})
        rank = RANK.search(first)
        read.append({'id': f'S{int(head[1])}', 'number': int(head[1]),
                     'verdict': verdict[1].lower() if verdict else None, 'rank': rank[0] if rank else None,
                     'text': (verdict[2] if verdict else head[2]).strip(), 'runs': runs, 'item': item})
    return read


def read_report(text: str, roots: tuple[str, ...] = ()) -> dict:
    """A grader's report as its fields and findings. A field runs on to the next field, finding, or
    closing line, and each of its lines is one item, joined by any more-indented lines that follow it:
    a claim wrapped onto a second line is still one claim. A heading that repeats adds to its field. A line that opens with a bullet, a lens id, or
    a rank starts a new item at any depth, except a possessive under `Could not verify` (`L2's pool`) on a
    line naming no rank, below a claim that blocks. Under `Could not verify` or `Outside my lenses` a line shaped like a finding
    is still an item of that field: an unproven P2 is a claim. The lines below a finding, up to the next
    finding or field, are its `body`: what breaks, the command, its output, with their fences and their
    indent past the finding's own, since output reads verbatim only inside its fence. A line inside such a
    fence is output even when it is shaped like a field or a finding. A `Proof test:` line among them
    names the test the grader kept, with any words after its paths kept as `proof_note`. A finding's file written
    from one of `roots`, the repository or a proof copy, reads as the path inside it. Under `Suspects:` an item starts
    at each `S<n>` line and runs on, through its `Command:`, `Output:` and fenced lines, to the next one, the next
    field, or a finding; `suspects` is those items as `_suspects` reads them, empty for a report without the field."""
    fields: dict[str, list[str]] = {}
    findings, current, indent, last_finding, finding_depth, fence = [], None, None, None, 0, None
    for raw in text.splitlines():
        line = raw.strip()
        depth = len(raw) - len(raw.lstrip())
        in_body = current is None and last_finding is not None
        body_line = raw.rstrip()[min(depth, finding_depth):]
        opens = FENCE.match(line)
        if in_body and (fence or opens):
            # Inside a finding's fence every line is its output, never a field or a finding. A fence left open
            # holds the rest of the report, so its lenses read unaccounted: the merge fails closed.
            last_finding['body'].append(body_line)
            if not fence:
                fence = opens[1]
            elif line.startswith(fence):
                fence = None
            continue
        joins_suspect = current == 'Suspects' and bool(fields[current]) and bool(line) and not SUSPECT.match(line)
        if joins_suspect and (fence or opens):
            # A suspect's output, fenced, is part of it, even a line shaped like a field.
            fields[current][-1] += '\n' + line
            if not fence:
                fence = opens[1]
            elif line.startswith(fence):
                fence = None
            continue
        if line.startswith('```'):
            continue
        finding, field = FINDING.match(line), REPORT_LINE.match(line)
        if finding and current in ('Could not verify', 'Outside my lenses'):
            fields[current].append(line)
            indent = depth if indent is None else indent
        elif finding:
            start, end = sorted((int(finding[4]), int(finding[5] or finding[4])))
            file = finding[3][2:] if finding[3].startswith('./') else finding[3]
            file = _inside(file, roots)
            findings.append({'rank': finding[1], 'lenses': LENS_ID.findall(finding[2]), 'file': file,
                             'start': start, 'end': end, 'where': f'{file}:{start}' + (f'-{end}' if end != start else ''),
                             'symbol': finding[6], 'title': finding[7].strip(), 'body': []})
            current, last_finding, finding_depth = None, findings[-1], depth
        elif in_body and PROOF_TEST.match(line):
            kept = PROOF_TEST.match(line)
            # A sentence-ending period is not part of a path.
            file, at = kept[1] or kept[2], kept[3] or kept[4]
            last_finding['proof_test'] = {'file': file.rstrip('.'), 'at': at.rstrip('.') if at else None}
            last_finding['proof_note'] = line[kept.end():].rstrip()
        elif field:
            current, last_finding = field[1], None
            inline = field[2].strip()
            fields.setdefault(current, []).extend([inline] if inline else [])
            indent = depth if inline else None
        elif joins_suspect:
            # A suspect runs on to the next `S<n>` line or field, its `Command:` and `Output:` lines included.
            fields[current][-1] += '\n' + line
        elif in_body and line:
            last_finding['body'].append(body_line)
        elif (current and line and fields[current] and indent is not None and depth > indent
              and not _opens_item(line, current, fields[current][-1])):
            fields[current][-1] += ' ' + line
        elif current and line:
            fields[current].append(line)
            indent = depth if indent is None else indent
    return {'fields': fields, 'findings': findings, 'suspects': _suspects(fields.get('Suspects', []))}


def _functions(repo: Path, head: str, paths: list[str]) -> dict[str, list[dict]]:
    """Each path's functions as the scanner's adapters name them, read from `head`, never the working
    tree. A path no adapter reads, or one that fails to read or parse, has none; the others keep theirs."""
    found: dict[str, list[dict]] = {}
    with tempfile.TemporaryDirectory() as tmp:
        read = []
        for path in paths:
            show = subprocess.run(['git', 'show', f'{head}:{path}'], cwd=repo, capture_output=True)
            if show.returncode == 0:
                (Path(tmp) / path).parent.mkdir(parents=True, exist_ok=True)
                (Path(tmp) / path).write_bytes(show.stdout)
                read.append(path)
        python = [path for path in read if path.endswith('.py')]
        if python:
            cta_python = sys.modules.get('_pr_grade_check_then_act_python') or _load(
                '_pr_grade_check_then_act_python', 'check_then_act_python.py')
            found.update(cta_python.parse_python(python, Path(tmp))[0])
        typescript = [path for path in read if path.endswith(TS_SUFFIXES) and not path.endswith('.d.ts')]
        node = shutil.which('node')
        if typescript and node:
            # The adapter looks for `typescript` from its root, a temporary directory here, then in
            # PR_GRADE_TYPESCRIPT, so name the one Node resolves from the repository, as the scanner finds it.
            env = dict(os.environ)
            if not env.get('PR_GRADE_TYPESCRIPT'):
                resolved = subprocess.run([node, '-p', "require.resolve('typescript', {paths: [process.argv[1]]})",
                                           str(repo)], capture_output=True, text=True)
                if resolved.returncode == 0:
                    env['PR_GRADE_TYPESCRIPT'] = resolved.stdout.strip()
            run = subprocess.run([node, str(TS_ADAPTER)], input=json.dumps({'root': tmp, 'files': typescript}),
                                 cwd=tmp, env=env, capture_output=True, text=True)
            if run.returncode == 0:
                found.update({entry['path']: entry['functions'] for entry in json.loads(run.stdout)['files']
                              if 'functions' in entry})
    return found


def _symbol(functions: list[dict], line: int) -> str | None:
    """The innermost named function or method holding `line`. A callback reads as the function it is
    written in."""
    holding = sorted((fn for fn in functions if fn['span'][0][0] <= line <= fn['span'][1][0]),
                     key=lambda fn: fn['span'][1][0] - fn['span'][0][0])
    for fn in holding:
        name = fn['name'].split(' > ')[0]
        if not name.startswith('<'):
            return name
    return None


def anchors(meta: dict, findings: list[dict]) -> tuple[list[str], str | None]:
    """For each finding, why its line looks misplaced, or an empty string when it touches a line the
    graded diff changed; and why none could be checked, when that is so. A defect can sit in code the diff
    left alone, such as a caller the change broke, so this flags and never refuses."""
    if meta.get('surface'):
        return [''] * len(findings), 'a surface grade has no diff to anchor a finding to'
    repo, base, head = meta.get('repository'), meta.get('base'), meta.get('head')
    missing = [key for key, value in (('repository', repo), ('base', base), ('head', head)) if not value]
    if missing:
        return [''] * len(findings), f"grade.json names no {' or '.join(missing)}"
    try:
        changed = grade_mode.changed_lines(base, Path(repo), head)
    except (subprocess.CalledProcessError, OSError) as failed:
        why = getattr(failed, 'stderr', None) or failed
        return [''] * len(findings), f'git diff {base}..{head} failed in {repo}: {str(why).strip()}'
    notes, nearest = [], {}
    for f in findings:
        ranges = changed.get(f['file'])
        if not ranges:
            exists = subprocess.run(['git', 'cat-file', '-e', f"{head}:{f['file']}"], cwd=repo, capture_output=True)
            notes.append(f"the graded diff changed nothing in {f['file']}" if exists.returncode == 0
                         else f"{f['file']} is not in the head commit")
        elif any(start <= f['end'] and f['start'] <= end for start, end in ranges):
            notes.append('')
        else:
            # The changed lines closest to the cited span, both when one sits either side at the same distance:
            # picking one would hand the coordinator a coin toss.
            gaps = [(f['start'] - end, end) if end < f['start'] else (start - f['end'], start) for start, end in ranges]
            closest = min(gap for gap, _ in gaps)
            nearest[len(notes)] = sorted({line for gap, line in gaps if gap == closest})
            notes.append('')
    try:
        functions = _functions(Path(repo), head, sorted({findings[i]['file'] for i in nearest}))
    except (subprocess.CalledProcessError, OSError, ValueError, KeyError):
        # A symbol is a help to the coordinator; the flag stands without one.
        functions = {}
    for i, lines in nearest.items():
        file = findings[i]['file']
        named = []
        for line in lines:
            symbol = _symbol(functions.get(file, []), line)
            named.append(f'{file}:{line}' + (f' in {symbol}' if symbol else ''))
        notes[i] = (f"touches no line the graded diff changed; nearest {'is' if len(named) == 1 else 'are'} "
                    + ' and '.join(named))
    return notes, None


def merge(proof_root: Path, given: dict[str, str] | None = None) -> str:
    """Where each lens ended across the graders' reports, and the draft block they support. `given` maps a
    group to its report's text, for a group whose grader replied without writing the file."""
    given = given or {}
    meta = json.loads((proof_root / 'grade.json').read_text())
    for group, text in given.items():
        if group not in meta['groups']:
            sys.exit(f"--report {group}: this grade has no group {group!r}; its groups are {', '.join(meta['groups'])}")
        if (proof_root / group / 'grade.md').is_file():
            sys.exit(f'--report {group}: {group} wrote {proof_root / group / "grade.md"}, which the merge reads')
        if not text.strip():
            sys.exit(f'--report {group}: the report is empty')
    # A finding's file may be written from the repository or any grader's proof copy.
    roots = tuple([meta['repository']] if meta.get('repository') else []) + tuple(
        str((proof_root / group / 'repo').resolve()) for group in meta['groups'])
    lenses, status, notes = meta['lenses'], {}, {}
    findings: list[dict] = []
    open_claims, outside, scores, out = [], [], [], []
    suspect_lines, unread_suspects, no_finding = [], [], []
    for group, held in meta['groups'].items():
        path = proof_root / group / 'grade.md'
        if group in given:
            report = read_report(given[group], roots)
        elif path.is_file():
            report = read_report(path.read_text(), roots)
        else:
            status.update({lens: f'no report from {group}' for lens in held})
            continue
        fields = report['fields']
        scores.append(f"{group} {' '.join(fields.get('Score', ['?']))}")
        for found in report['findings']:
            if found.get('proof_test'):
                # The grader names the kept test from its own directory, or by its name alone; the coordinator
                # moves it from the path printed, so a file the merge cannot find gets no move line.
                file = Path(found['proof_test']['file'])
                tried = [file] if file.is_absolute() else [proof_root / group / file, proof_root / group / 'proofs' / file]
                there = next((path for path in tried if path.is_file()), None)
                found['proof_test']['path'] = str((there or tried[0]).resolve())
                found['proof_test']['missing'] = there is None
            findings.append({**found, 'by': f"{group} {','.join(found['lenses']) or 'no lens named'}"})
            if found['rank'] != 'P3':
                for lens in found['lenses'] or held:
                    status[lens] = f"{found['rank']} at {found['where']}"
        claims = [claim for claim in fields.get('Could not verify', []) if claim.lower().rstrip('.') != 'none']
        handed = int((meta.get('suspects') or {}).get(group, 0))
        if handed or report['suspects']:
            claims += settle_suspects(group, handed, report, roots, suspect_lines, unread_suspects, no_finding)
        for claim in claims:
            # A claim with no rank is counted as one that would block, and says so.
            blocking = blocks(claim)
            open_claims.append((group, claim, blocking))
            for lens in LENS_ID.findall(claim) if blocking else []:
                # An open claim outranks a clearance, even another grader's; only a proof outranks it.
                if not status.get(lens, '').startswith('P'):
                    status[lens] = f'open claim from {group}'
        outside += [f'{group}: {item}' for item in fields.get('Outside my lenses', [])
                    if item.lower().rstrip('.') != 'none']
        verified = cleared_notes(', '.join(fields.get('Verified and clear', [])))
        for lens in held:
            status.setdefault(lens, 'clear' if lens in verified else f'unaccounted: ask {group} which')
            notes[lens] = verified.get(lens, '')
    # A skipped lens holds no grader, so a finding or open claim another grader raised under it outranks the skip.
    for lens in meta.get('skipped', []):
        status.setdefault(lens, SKIPPED)
    blocking_findings = [f for f in findings if f['rank'] != 'P3']
    count = len(blocking_findings) + sum(1 for _, _, blocking in open_claims if blocking)
    unapplied = [lens for lens in lenses if status.get(lens) != SKIPPED
                 and not status.get(lens, '').startswith(('clear', 'P', 'open'))]
    floor = 2 if unapplied else 5 if count == 0 else 4 if count == 1 else 3
    clear = [f"{lens} {notes[lens]}".rstrip() if status.get(lens) == 'clear' else f'{lens} (not applicable)'
             for lens in lenses if status.get(lens) in ('clear', SKIPPED)]
    # A 2 comes from a lens no report assessed, so that explains the score before any finding does.
    first = ('not assessed: ' + '; '.join(f"{lens} ({status.get(lens, 'not held by any grader')})" for lens in unapplied)
             if unapplied else blocking_findings[0]['title'] if blocking_findings
             else next((claim for _, claim, blocking in open_claims if blocking), None))
    out += ['reports: ' + (', '.join(scores) or 'none'), '', 'lenses:']
    out += [f'  {lens}  {status.get(lens, "not held by any grader")}' for lens in lenses]
    out += ['', f'findings ({len(findings)}):']
    anchor_notes, unchecked = anchors(meta, findings) if findings else ([], None)
    shared_count = 0
    for f, anchor in zip(findings, anchor_notes):
        # Two findings at one line may be one defect or two; counting both keeps the floor on the safe side.
        shared = any(g is not f and g['file'] == f['file'] and g['start'] <= f['end'] and f['start'] <= g['end']
                     for g in findings)
        shared_count += shared
        out.append(f"  {f['rank']} {f['where']}" + (f" in {f['symbol']}" if f['symbol'] else '')
                   + f" - {f['title']}  [{f['by']}]"
                   + (f"  (proof test: {f['proof_test']['path']}, "
                      + ('not found, ' if f['proof_test']['missing'] else '')
                      + (f"ran at {f['proof_test']['at']})" if f['proof_test']['at'] else 'no repository path given)')
                      if f.get('proof_test') else '')
                   + ('  (same line as another finding: one defect or two?)' if shared else '')
                   + (f'  (anchor: {anchor})' if anchor else ''))
    if not findings:
        out.append('  none')
    if unchecked:
        out.append(f'  anchors: not checked, {unchecked}')
    kept = [f for f in findings if f.get('proof_test') and f['proof_test']['at'] and not f['proof_test']['missing']]
    if kept:
        # The paths are grader text on their way to a shell, so each one is quoted.
        repo = meta.get('repository') or '<repository>'
        out += ['', 'proof tests: move each to its path, see it red, and commit it alone before the remove: line, '
                'which refuses while one is still here:']
        out += [f"  mv {shlex.quote(f['proof_test']['path'])} {shlex.quote(repo + '/' + f['proof_test']['at'])}"
                for f in kept]
    if suspect_lines:
        handed = ', '.join(f'{group} {n}' for group, n in (meta.get('suspects') or {}).items() if n) or 'none'
        out += ['', f'suspects (handed: {handed}):', *suspect_lines]
    if meta.get('firstRead'):
        # The coordinator says below the block when it did not run.
        out += ['', f"first read: {meta['firstRead']}"]
    out += ['', 'could not verify:']
    out += [f"  {group}: {claim}{'' if blocking else '  (P3, does not block)'}"
            for group, claim, blocking in open_claims] or ['  none']
    out += ['', 'outside the lenses that raised them, prove or carry each under its own lens:']
    out += [f'  {item}' for item in outside] or ['  none']
    out += ['', f'score floor: {floor}/5. Two lines with one cause, a clearance against a proof, a 3 for a design',
            'decision, and a 1 for a finding that contradicts the stated purpose are yours to judge.',
            'The Score line is yours to raise or lower.']
    pathless = sum(1 for f in findings if f.get('proof_test') and not f['proof_test']['at'])
    settle = to_settle(meta, status, unapplied, outside, sum(blocking for _, _, blocking in open_claims),
                       shared_count, sum(1 for anchor in anchor_notes if anchor), unchecked, pathless,
                       unread_suspects=unread_suspects, no_finding=no_finding)
    if settle:
        out += ['left to your judgment; settle each, then edit only the lines it touches in the block below:']
        out += [f'  {item}' for item in settle]
    else:
        out += ['nothing above is left to your judgment: paste the block below as printed.']
    # While anything is left to judgment the Score is a floor, written so that `check` refuses it as printed.
    score = f'{floor}/5' + (' floor; settle the items above, then write the score' if settle else '')
    # A claim's own bullet is noise once the block joins the claims on one line.
    guesses = '; '.join('guess: ' + BULLET.sub('', claim) for _, claim, _ in open_claims)
    out += ['', '## Grade', '', f"Mode: {meta['mode']}", *([f"Surface: {' '.join(meta['surface'])}"] if meta.get('surface') else []),
            f"Graded: {meta['head']}", f'Score: {score}',
            f"Blocking: {first or 'nothing'}", f"Verified and clear: {', '.join(clear) or 'none'}",
            f"Could not verify: {guesses or 'none'}"]
    for f in findings:
        out += ['', f"{f['rank']} " + ''.join(f'{lens} ' for lens in f['lenses']) + f['where']
                + (f" in {f['symbol']}" if f['symbol'] else '') + f" - {f['title']}"]
        # `parse` drops a closed fence before it reads, so a line inside one prints as the grader wrote it.
        body, fence = [], None
        for line in f['body']:
            opens = FENCE.match(line)
            body.append(line if fence or opens else _inert(line))
            if fence and line.startswith(fence):
                fence = None
            elif not fence and opens:
                fence = opens[1]
        # A fence the grader left open would swallow every finding below it.
        out += body + ([fence] if fence else [])
        if f.get('proof_test'):
            kept = f['proof_test']
            out.append(f"Proof test: {kept['at']}{f['proof_note']}" if kept['at']
                       else f"Proof test: {kept['file']}, no repository path given")
    return '\n'.join(out)


def settle_suspects(group: str, handed: int, report: dict, roots: tuple[str, ...], lines: list[str],
                    unread: list[str], no_finding: list[str]) -> list[str]:
    """The open claims one group's `Suspects:` lines add, with a line each printed to `lines`. A `could not verify`
    line is a claim at its rank. A `refuted` line is one too, as refuted without a run, unless it carries a `Command:`
    that `is_run` and its output. A `proven` line must name a finding of its report at the finding's file:line, and
    one ranked P3 prints its reason, for the coordinator to re-rank. Each handed suspect with no line the merge reads
    goes on `unread`, and a proven one naming no finding on `no_finding`, so the block's Score stays a floor."""
    claims, read, unverdicted = [], set(), set()
    for suspect in report['suspects']:
        sid, verdict, text = suspect['id'], suspect['verdict'], suspect['text']
        if verdict is None:
            lines.append(f'  {group} {sid} names no verdict: {text}')
            unverdicted.add(suspect['number'])
            continue
        read.add(suspect['number'])
        if verdict == 'could not verify':
            claims.append(f'{sid} could not verify: {text}')
            lines.append(f'  {group} {sid} could not verify, ' + (suspect['rank'] or 'no rank') + ': see could not verify')
        elif verdict == 'refuted':
            ran = next((run['command'] for run in suspect['runs'] if run['output'] and is_run(run['command'])), None)
            if ran:
                lines.append(f'  {group} {sid} refuted by a run: {ran}')
            else:
                claims.append(f'{sid} refuted without a run: {text}')
                lines.append(f'  {group} {sid} refuted without a run: moved to could not verify')
        else:
            cited = [(_inside(file[2:] if file.startswith('./') else file, roots), int(line))
                     for file, line in FILE_LINE.findall(suspect['item'].partition('\n')[0])]
            at = [f for f in report['findings'] for file, line in cited if f['file'] == file and f['start'] <= line <= f['end']]
            # Two findings can share a line: the one at the rank the suspect's line gives is its finding.
            finding = next((f for f in at if f['rank'] == suspect['rank']), at[0] if at else None)
            if finding is None:
                no_finding.append(f'{group} {sid}')
                lines.append(f'  {group} {sid} proven, names no finding in its report: {text}')
            elif finding['rank'] == 'P3':
                lines.append(f"  {group} {sid} proven, P3 at {finding['where']}: {text}  "
                             '(P3: re-rank it if its reason does not hold)')
            else:
                lines.append(f"  {group} {sid} proven, {finding['rank']} at {finding['where']}")
    missing = [f'S{n}' for n in range(1, handed + 1) if n not in read]
    if missing:
        unread.append(f"{group} {', '.join(missing)}")
        lines += [f'  {group} {sid} has no line' for sid in missing if int(sid[1:]) not in unverdicted]
    return claims


def _inert(line: str) -> str:
    """A finding's line as the block prints it, so that `parse` reads it as neither a field nor the heading
    that ends the section. A field name goes in code, which `parse` drops, and a heading's `#` is escaped.
    A code span before either hides it from those two and not from `parse`, which drops spans before it
    reads, so such a line is indented one space, which neither of its patterns matches."""
    line = FIELD_LEAD.sub(r'`\1:`', HEADING_LEAD.sub(r'\\#', line))
    seen = without_code(line)
    return ' ' + line if FIELD_LEAD.match(seen) or HEADING_LEAD.match(seen) else line


def to_settle(meta: dict, status: dict[str, str], unapplied: list[str], outside: list[str], open_blocking: int,
              shared: int, flagged: int, unchecked: str | None, pathless: int, *, unread_suspects: list[str] = (),
              no_finding: list[str] = ()) -> list[str]:
    """What the skill's section 5, in scoring.md, leaves to the coordinator in this merge, one line each. A P3 claim
    is not on it: it leaves the score where it is."""
    def counted(n: int, one: str, many: str) -> str:
        return f'{n} {one if n == 1 else many}'
    unaccounted = [lens for lens in unapplied if status.get(lens, '').startswith('unaccounted')]
    unassessed = [lens for lens in unapplied if lens not in unaccounted]
    items = []
    if shared:
        items.append(f'{shared} findings at a line another finding holds: one defect or two? Join one cause into '
                     'one finding with the stronger proof and both lenses')
    if flagged:
        items.append(counted(flagged, 'anchor: flag', 'anchor: flags') + ': re-anchor the finding where its proof '
                     'points, or confirm its line and name the changed line that broke it')
    if unchecked and not meta.get('surface'):
        items.append("anchors not checked: check each finding's line against the diff")
    if open_blocking:
        items.append(counted(open_blocking, 'open claim', 'open claims') + ' that would block: prove or refute each, '
                     'or leave it a guess as the Blocking sentence')
    if outside:
        items.append(counted(len(outside), 'defect raised outside its lens', 'defects raised outside their lenses')
                     + ': prove each, or carry it to Could not verify')
    if unaccounted:
        items.append(counted(len(unaccounted), 'lens', 'lenses') + f" a report left unaccounted: {', '.join(unaccounted)}; "
                     'ask its grader which, in one message')
    if unassessed:
        items.append(counted(len(unassessed), 'lens', 'lenses') + ' no report assessed: '
                     + '; '.join(f"{lens} ({status.get(lens, 'not held by any grader')})" for lens in unassessed)
                     + '; grade it, or pass a reply that never reached its file with --report <group>=<file or ->')
    if pathless:
        items.append(counted(pathless, 'proof test names', 'proof tests name') + ' no repository path: write the '
                     'path it was committed at')
    if unread_suspects:
        items.append('suspects handed with no line the merge reads: ' + '; '.join(unread_suspects)
                     + '; ask its grader for each line, in one message')
    if no_finding:
        items.append(counted(len(no_finding), 'proven suspect names', 'proven suspects name') + ' no finding in its '
                     'report: ' + '; '.join(no_finding) + '; ask its grader which finding, or carry it to Could not verify')
    return items


def gh(*args: str) -> str:
    run = subprocess.run(['gh', *args], capture_output=True, text=True)
    if run.returncode != 0:
        sys.exit(f"gh {' '.join(args[:3])} failed: {run.stderr.strip()}")
    return run.stdout


def graded_comparison(repo: str, graded: str, head: str) -> dict:
    """GitHub's comparison of the graded commit with the PR head, or `error` with gh's message."""
    if not SHA.fullmatch(graded):
        return {'error': 'no graded SHA'}
    try:
        data = json.loads(gh('api', f'repos/{repo}/compare/{graded}...{head}', '--jq', '{status, files: [.files[]?.filename]}'))
    except SystemExit as failed:
        return {'error': str(failed.code)}
    return {'status': data['status'], 'files': data['files'] or []}


def base_file(repo: str, path: str, ref: str) -> str | None:
    """`path` as the base branch has it, or None when it is absent there. The grade's rules come from
    the base, so the PR under check cannot loosen them for itself."""
    run = subprocess.run(['gh', 'api', f'repos/{repo}/contents/{path}?ref={ref}', '-H',
                          'Accept: application/vnd.github.raw'], capture_output=True, text=True)
    if run.returncode == 0:
        return run.stdout
    if 'Not Found' in run.stderr or '404' in run.stderr:
        return None
    sys.exit(f'reading {path} at {ref} failed: {run.stderr.strip()}')


def check(pr: int, repo: str, root: Path) -> tuple[bool, list[str]]:
    """Whether the PR needs a grade, and what stops it."""
    view = json.loads(gh('pr', 'view', str(pr), '--repo', repo, '--json',
                         'body,baseRefName,headRefName,headRefOid,isDraft'))
    config = load_config(root, base_file(repo, CONFIG, view['baseRefName']) or '')
    branches = (config.get('requireGrade') or {}).get('branches')
    if view['isDraft'] or (branches and not re.search(branches, view['headRefName'])):
        return False, []
    # One JSON array per file, so no path is escaped on the way through.
    rows = gh('api', '--paginate', f'repos/{repo}/pulls/{pr}/files?per_page=100', '--jq',
              '.[] | [.filename, (.previous_filename // empty)]').splitlines()
    if len(rows) >= PR_FILE_CAP:
        return True, [f'PR #{pr} touches {PR_FILE_CAP} files, the most GitHub lists, so its grade mode cannot be '
                      'checked. Split it.']
    paths = [path for row in rows for path in json.loads(row)]
    # The files list came from a second read; a push since the first would pair it with an old compare.
    head = json.loads(gh('pr', 'view', str(pr), '--repo', repo, '--json', 'headRefOid'))['headRefOid']
    if head != view['headRefOid']:
        return True, [f"PR #{pr} moved from {view['headRefOid']} to {head} while it was checked. Run the check again."]
    block = parse(view['body'] or '')
    compare = graded_comparison(repo, (block or {}).get('Graded', ''), head)
    lenses = lens_ids(base_file(repo, config['lenses'], view['baseRefName']))
    return True, problems(block, pr_files=paths, lenses=lenses, compare=compare, root=root, config=config)


def given_reports(pairs: list[str]) -> dict[str, str]:
    """Each `--report group=source` read into text: a file, or stdin for `-`, which carries one."""
    given: dict[str, str] = {}
    stdin_read = False
    for pair in pairs:
        group, sep, source = pair.partition('=')
        if not sep or not source:
            sys.exit(f'--report {pair}: give it as <group>=<file or ->')
        if group in given:
            sys.exit(f'--report {pair}: {group} is given twice')
        if source == '-':
            if stdin_read:
                sys.exit(f'--report {pair}: stdin can carry one report')
            given[group], stdin_read = sys.stdin.read(), True
        elif Path(source).is_file():
            given[group] = Path(source).read_text()
        else:
            sys.exit(f'--report {pair}: {source} is not a file')
    return given


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    run = sub.add_parser('check', help='refuse the PR unless it carries a current 5/5 grade')
    run.add_argument('pr', type=int)
    run.add_argument('--repo', help='owner/name; defaults to the current repository')
    merging = sub.add_parser('merge', help="merge the graders' reports under a grade_prep.py root")
    merging.add_argument('root', type=Path)
    merging.add_argument('--report', action='append', default=[], metavar='GROUP=FILE',
                         help="a group's report, as a file or - for stdin, when its grader replied without writing it")
    args = ap.parse_args()
    if args.command == 'merge':
        if not (args.root / 'grade.json').is_file():
            sys.exit(f'{args.root} holds no grade.json; pass the root grade_prep.py printed')
        print(merge(args.root, given_reports(args.report)))
        return
    repo = args.repo or gh('repo', 'view', '--json', 'nameWithOwner', '--jq', '.nameWithOwner').strip()
    required, found = check(args.pr, repo, repo_root())
    if found:
        sys.exit('\n'.join(found))
    print(f'PR #{args.pr}: ' + ('the grade is current' if required else 'no grade required (draft, or branch not in scope)'))


if __name__ == '__main__':
    main()
