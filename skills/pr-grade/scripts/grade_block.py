#!/usr/bin/env python3
"""The grade block a pull request carries, and the check that refuses a PR without a current one.

    python3 grade_block.py check <pr> [--repo owner/name]
    python3 grade_block.py merge <proof root>

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

`merge` reads the report each grader wrote under the root grade_prep.py printed, and prints where every
lens ended (clear, a finding, an open claim, or unaccounted), the findings with any two at one line
flagged, the lowest score the rules allow, and a draft block. It settles nothing that needs
judgment: two lines with one cause, a clearance against a proof, a 3 for a design decision, or a 1.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

# The selector beside this file, loaded by path under a private name. A repository that embeds these
# scripts can have a `grade_mode` module of its own, which this neither picks up nor replaces.
_spec = importlib.util.spec_from_file_location('_pr_grade_mode', Path(__file__).resolve().parent / 'grade_mode.py')
grade_mode = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = grade_mode
_spec.loader.exec_module(grade_mode)
CONFIG, MODES, load_config, repo_root = grade_mode.CONFIG, grade_mode.MODES, grade_mode.load_config, grade_mode.repo_root

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
FINDING = re.compile(r'^(P[123])[ \t]+((?:L\d+[ \t,]*)*)(\S+:\d+)[ \t]+-[ \t]+(.+)$')
BLOCKING = re.compile(r'\bP[12]\b')
NEW_ITEM = re.compile(r'([-*+]|\d+[.)])[ \t]|(L\d+|P[123])\b')


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
    items, depth, item = [], 0, ''
    for ch in text + ',':
        if ch == ',' and depth == 0:
            items.append(item)
            item = ''
            continue
        depth = max(depth + (ch == '(') - (ch == ')'), 0)
        item += ch
    cleared: set[str] = set()
    for item in items:
        rest, found = item.strip().rstrip('.'), []
        while rest:
            lead = LEAD_ID.match(rest)
            end = None if lead or not rest.startswith('(') else _note_end(rest)
            if lead:
                found.append(lead[1])
                rest = rest[lead.end():]
            elif end is not None:
                rest = rest[end:].lstrip()
            else:
                found = []
                break
        cleared.update(found)
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


def read_report(text: str) -> dict:
    """A grader's report as its fields and findings. A field runs on to the next field, finding, or
    closing line, and each of its lines is one item, joined by any more-indented lines that follow it:
    a claim wrapped onto a second line is still one claim. A line that opens with a bullet, a lens id, or
    a rank starts a new item at any depth. Under `Could not verify` or `Outside my lenses` a line shaped
    like a finding is still an item of that field: an unproven P2 is a claim."""
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
            findings.append({'rank': finding[1], 'lenses': LENS_ID.findall(finding[2]), 'where': finding[3],
                             'title': finding[4].strip()})
            current = None
        elif field:
            current = field[1]
            fields[current] = [field[2].strip()] if field[2].strip() else []
            indent = depth if fields[current] else None
        elif current and line and fields[current] and indent is not None and depth > indent and not NEW_ITEM.match(line):
            fields[current][-1] += ' ' + line
        elif current and line:
            fields[current].append(line)
            indent = depth if indent is None else indent
    return {'fields': fields, 'findings': findings}


def merge(proof_root: Path) -> str:
    """Where each lens ended across the graders' reports, and the draft block they support."""
    meta = json.loads((proof_root / 'grade.json').read_text())
    lenses, status = meta['lenses'], {}
    findings: list[dict] = []
    open_claims, outside, scores, out = [], [], [], []
    for group, held in meta['groups'].items():
        path = proof_root / group / 'report.md'
        if not path.is_file():
            status.update({lens: f'no report from {group}' for lens in held})
            continue
        report = read_report(path.read_text())
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
            blocking = bool(BLOCKING.search(claim)) or not re.search(r'\bP3\b', claim)
            open_claims.append((group, claim, blocking))
            for lens in LENS_ID.findall(claim) if blocking else []:
                # An open claim outranks a clearance, even another grader's; only a proof outranks it.
                if not status.get(lens, '').startswith('P'):
                    status[lens] = f'open claim from {group}'
        outside += [f'{group}: {item}' for item in fields.get('Outside my lenses', [])
                    if item.lower().rstrip('.') != 'none']
        verified = verified_lenses(', '.join(fields.get('Verified and clear', [])))
        for lens in held:
            status.setdefault(lens, 'clear' if lens in verified else f'unaccounted: ask {group} which')
    blocking_findings = [f for f in findings if f['rank'] != 'P3']
    count = len(blocking_findings) + sum(1 for _, _, blocking in open_claims if blocking)
    unapplied = [lens for lens in lenses if not status.get(lens, '').startswith(('clear', 'P', 'open'))]
    floor = 2 if unapplied else 5 if count == 0 else 4 if count == 1 else 3
    clear = [lens for lens in lenses if status.get(lens) == 'clear']
    # A 2 comes from a lens no report assessed, so that explains the score before any finding does.
    first = ('not assessed: ' + '; '.join(f"{lens} ({status.get(lens, 'not held by any grader')})" for lens in unapplied)
             if unapplied else blocking_findings[0]['title'] if blocking_findings
             else next((claim for _, claim, blocking in open_claims if blocking), None))
    out += ['reports: ' + (', '.join(scores) or 'none'), '', 'lenses:']
    out += [f'  {lens}  {status.get(lens, "not held by any grader")}' for lens in lenses]
    out += ['', f'findings ({len(findings)}):']
    lines_seen = [f['where'] for f in findings]
    # Two findings at one line may be one defect or two; counting both keeps the floor on the safe side.
    out += [f"  {f['rank']} {f['where']} - {f['title']}  [{f['by']}]"
            + ('  (same line as another finding: one defect or two?)' if lines_seen.count(f['where']) > 1 else '')
            for f in findings] or ['  none']
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    run = sub.add_parser('check', help='refuse the PR unless it carries a current 5/5 grade')
    run.add_argument('pr', type=int)
    run.add_argument('--repo', help='owner/name; defaults to the current repository')
    merging = sub.add_parser('merge', help="merge the graders' reports under a grade_prep.py root")
    merging.add_argument('root', type=Path)
    args = ap.parse_args()
    if args.command == 'merge':
        if not (args.root / 'grade.json').is_file():
            sys.exit(f'{args.root} holds no grade.json; pass the root grade_prep.py printed')
        print(merge(args.root))
        return
    repo = args.repo or gh('repo', 'view', '--json', 'nameWithOwner', '--jq', '.nameWithOwner').strip()
    required, found = check(args.pr, repo, repo_root())
    if found:
        sys.exit('\n'.join(found))
    print(f'PR #{args.pr}: ' + ('the grade is current' if required else 'no grade required (draft, or branch not in scope)'))


if __name__ == '__main__':
    main()
