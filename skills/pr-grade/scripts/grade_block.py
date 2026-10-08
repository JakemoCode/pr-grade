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
    Verified and clear: L1, L2, L3, L4, L5, L6, L7, L8
    Could not verify: none

`check` refuses the PR when the block is missing or below 5/5; when a lens the lens file defines (or
L1 to L8, without one) is missing from `Verified and clear`; when `Mode` is cheaper than grade_mode.py
requires for the PR's files; when the graded commit is not in the PR's history or a file the grade
must cover changed after it; when GitHub's lists are cut off; or when the PR moved during the check.

It skips a draft, and a PR whose branch does not match `requireGrade.branches` in the config when that
key is set. The config and the lens file are read from the PR's base branch, so a PR cannot loosen the
rules it is checked against.

`merge` reads the report each grader wrote under the root grade_prep.py printed, or the one `--report`
gives for a group whose grader replied without writing its file. It prints where every lens ended
(clear, not applicable, a finding, an open claim, or unaccounted), the findings with any two at one line
flagged, the lowest score the rules allow, and a draft block. It flags a finding whose line touches no
line the graded diff changed, naming the nearest changed line and the function it sits in, and never
refuses one: a defect can sit in a caller the change left alone. The draft keeps each grader's notes in
`Verified and clear` and writes a lens grade_prep.py skipped as `L2 (not applicable)`. It settles
nothing that needs judgment: two lines with one cause, a clearance against a proof, a 3 for a design
decision, or a 1.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
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

DEFAULT_LENSES = [f'L{n}' for n in range(1, 9)]
# GitHub lists at most this many files, and says nothing when it stops.
COMPARE_CAP = 300
PR_FILE_CAP = 3000
SECTION = re.compile(r'(?ms)^##[ \t]+Grade[ \t]*$(.*?)(?=^##[ \t]|\Z)')
FIELD = re.compile(r'(?m)^(Mode|Graded|Score|Verified and clear):[ \t]*(.*?)[ \t]*$')
LENS_HEADING = re.compile(r'(?m)^###[ \t]+(L\d+)\.')
LENS_ID = re.compile(r'L\d+\b')
LEAD_ID = re.compile(r'(L\d+)\b[ \t]*')
SHA = re.compile(r'[0-9a-f]{40}')
REPORT_LINE = re.compile(r'^(Score|Blocking|Verified and clear|Could not verify|Outside my lenses|L7 candidates|L\d+):'
                         r'[ \t]*(.*)$')
# `P1 L4 <file>:<line> in <symbol> - <title>`, or `<file>:<start>-<end>`. The symbol runs to the first ` - `,
# so a symbol written with words after it still parses, and a finding without one still reads. A column
# after the line, as in `<file>:78:5`, is dropped.
FINDING = re.compile(r'^(P[123])[ \t]+((?:L\d+[ \t,]*)*)(\S+?):(\d+)(?::\d+)?(?:-(\d+))?(?:[ \t]+in[ \t]+(.+?))?[ \t]+-[ \t]+(.+)$')
BLOCKING = re.compile(r'\bP[12]\b')
NEW_ITEM = re.compile(r'([-*+]|\d+[.)])[ \t]|(L\d+|P[123])\b')
# `L2's pool releases` continues a claim, unless its line names a rank or the claim above is a P3.
LENS_POSSESSIVE = re.compile(r"L\d+['’]s\b")
RANK = re.compile(r'\bP[123]\b')
SKIPPED = 'not applicable: skipped by grade_prep.py --skip'


def without_code(markdown: str) -> str:
    """Drop fenced blocks and code spans, so a quoted example block is never read as the grade."""
    fenced = re.sub(r'(?ms)^(`{3,}|~{3,}).*?^\1[^\n]*$', '', markdown)
    return re.sub(r'`[^`\n]*`', '', fenced)


def lens_ids(text: str | None) -> list[str]:
    """Every lens the lens file text defines as a `### L<n>.` heading. A lens added there is required
    with no code change. The skill's eight apply without a file, or when no heading matches, so a
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
        found.append(f"`Verified and clear` leaves out {', '.join(missing)}. Apply every lens.")
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


def read_report(text: str) -> dict:
    """A grader's report as its fields and findings. A field runs on to the next field, finding, or
    closing line, and each of its lines is one item, joined by any more-indented lines that follow it:
    a claim wrapped onto a second line is still one claim. A line that opens with a bullet, a lens id, or
    a rank starts a new item at any depth, except a possessive under `Could not verify` (`L2's pool`) on a
    line naming no rank, below a claim that blocks. Under `Could not verify` or `Outside my lenses` a line shaped like a finding
    is still an item of that field: an unproven P2 is a claim."""
    fields: dict[str, list[str]] = {}
    findings, current, indent = [], None, None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith('```'):
            continue
        depth = len(raw) - len(raw.lstrip())
        finding, field = FINDING.match(line), REPORT_LINE.match(line)
        if finding and current in ('Could not verify', 'Outside my lenses'):
            fields[current].append(line)
            indent = depth if indent is None else indent
        elif finding:
            start, end = sorted((int(finding[4]), int(finding[5] or finding[4])))
            file = finding[3][2:] if finding[3].startswith('./') else finding[3]
            findings.append({'rank': finding[1], 'lenses': LENS_ID.findall(finding[2]), 'file': file,
                             'start': start, 'end': end, 'where': f'{file}:{start}' + (f'-{end}' if end != start else ''),
                             'symbol': finding[6], 'title': finding[7].strip()})
            current = None
        elif field:
            current = field[1]
            fields[current] = [field[2].strip()] if field[2].strip() else []
            indent = depth if fields[current] else None
        elif (current and line and fields[current] and indent is not None and depth > indent
              and not _opens_item(line, current, fields[current][-1])):
            fields[current][-1] += ' ' + line
        elif current and line:
            fields[current].append(line)
            indent = depth if indent is None else indent
    return {'fields': fields, 'findings': findings}


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
    lenses, status, notes = meta['lenses'], {}, {}
    findings: list[dict] = []
    open_claims, outside, scores, out = [], [], [], []
    for group, held in meta['groups'].items():
        path = proof_root / group / 'grade.md'
        if group in given:
            report = read_report(given[group])
        elif path.is_file():
            report = read_report(path.read_text())
        else:
            status.update({lens: f'no report from {group}' for lens in held})
            continue
        fields = report['fields']
        scores.append(f"{group} {' '.join(fields.get('Score', ['?']))}")
        for found in report['findings']:
            findings.append({**found, 'by': f"{group} {','.join(found['lenses']) or 'no lens named'}"})
            if found['rank'] != 'P3':
                for lens in found['lenses'] or held:
                    status[lens] = f"{found['rank']} at {found['where']}"
        for claim in fields.get('Could not verify', []):
            if claim.lower().rstrip('.') == 'none':
                continue
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
    for f, anchor in zip(findings, anchor_notes):
        # Two findings at one line may be one defect or two; counting both keeps the floor on the safe side.
        shared = any(g is not f and g['file'] == f['file'] and g['start'] <= f['end'] and f['start'] <= g['end']
                     for g in findings)
        out.append(f"  {f['rank']} {f['where']}" + (f" in {f['symbol']}" if f['symbol'] else '')
                   + f" - {f['title']}  [{f['by']}]"
                   + ('  (same line as another finding: one defect or two?)' if shared else '')
                   + (f'  (anchor: {anchor})' if anchor else ''))
    if not findings:
        out.append('  none')
    if unchecked:
        out.append(f'  anchors: not checked, {unchecked}')
    out += ['', 'could not verify:']
    out += [f"  {group}: {claim}{'' if blocking else '  (P3, does not block)'}"
            for group, claim, blocking in open_claims] or ['  none']
    out += ['', 'outside the lenses that raised them, prove or carry each under its own lens:']
    out += [f'  {item}' for item in outside] or ['  none']
    out += ['', f'score floor: {floor}/5. Two lines with one cause, a clearance against a proof, a 3 for a design',
            'decision, and a 1 for a finding that contradicts the stated purpose are yours to judge.', '',
            '## Grade', '', f"Mode: {meta['mode']}", f"Graded: {meta['head']}", f'Score: {floor}/5',
            f"Blocking: {first or 'nothing'}", f"Verified and clear: {', '.join(clear) or 'none'}",
            'Could not verify: ' + ('; '.join(f'guess: {claim}' for _, claim, _ in open_claims) or 'none')]
    return '\n'.join(out)


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
