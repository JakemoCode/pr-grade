#!/usr/bin/env python3
"""The grade block a pull request carries, and the check that refuses a PR without a current one.

    python3 grade_block.py check <pr> [--repo owner/name]

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
key is set.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from grade_mode import MODES, assess, load_config, repo_root  # noqa: E402

DEFAULT_LENSES = [f'L{n}' for n in range(1, 9)]
# GitHub lists at most this many files, and says nothing when it stops.
COMPARE_CAP = 300
PR_FILE_CAP = 3000
SECTION = re.compile(r'(?ms)^##[ \t]+Grade[ \t]*$(.*?)(?=^##[ \t]|\Z)')
FIELD = re.compile(r'(?m)^(Mode|Graded|Score|Verified and clear):[ \t]*(.*?)[ \t]*$')
LENS_HEADING = re.compile(r'(?m)^###[ \t]+(L\d+)\.')
LENS_ID = re.compile(r'L\d+\b')
SHA = re.compile(r'[0-9a-f]{40}')


def without_code(markdown: str) -> str:
    """Drop fenced blocks and code spans, so a quoted example block is never read as the grade."""
    fenced = re.sub(r'(?ms)^(`{3,}|~{3,}).*?^\1[^\n]*$', '', markdown)
    return re.sub(r'`[^`\n]*`', '', fenced)


def lens_ids(lens_file: Path) -> list[str]:
    """Every lens the lens file defines, or the skill's eight without one. A lens added to the file is
    required with no code change."""
    return LENS_HEADING.findall(lens_file.read_text()) if lens_file.exists() else DEFAULT_LENSES


def parse(body: str) -> dict[str, str] | None:
    """The block's fields, or None when the body has no `## Grade` section. A body edited in GitHub's
    web UI comes back with CRLF endings."""
    section = SECTION.search(without_code(body.replace('\r\n', '\n')))
    return None if section is None else dict(FIELD.findall(section.group(1)))


def problems(block: dict[str, str] | None, *, pr_files: list[str], lenses: list[str], compare: dict | None,
             root: Path, config: dict) -> list[str]:
    """What stops the PR going ready. `pr_files` is every path the PR touches, both sides of a rename.
    `compare` is GitHub's comparison of the graded commit with the PR head: its `status` and the paths
    it changed, or None when the graded commit could not be read."""
    if block is None:
        return ['The body has no `## Grade` section. Grade the branch with /pr-grade and put its block in the body.']
    required, reasons, covered = assess(pr_files, root, config)
    found = []
    mode = block.get('Mode', '')
    if mode not in MODES:
        found.append(f"Grade mode {mode!r} is not one of {', '.join(MODES)}.")
    elif MODES.index(mode) < MODES.index(required):
        why = '; '.join(f'{path}: {reason}' for path, reason in sorted(reasons.items())) or 'its code file count'
        found.append(f'Graded {mode}, but this change requires {required} ({why}). Re-grade.')
    if block.get('Score') != '5/5':
        found.append(f"Grade score is {block.get('Score') or 'missing'}; the PR goes ready only at 5/5.")
    # Each comma-separated item names its lens first; "L3 (L2 not applicable)" verifies L3 only.
    verified = {m[0] for item in block.get('Verified and clear', '').split(',') if (m := LENS_ID.match(item.strip()))}
    missing = [lens for lens in lenses if lens not in verified]
    if missing:
        found.append(f"`Verified and clear` leaves out {', '.join(missing)}. Apply every lens.")
    graded = block.get('Graded', '')
    if not SHA.fullmatch(graded):
        found.append(f'`Graded` must be the full 40-character SHA of the graded commit, not {graded or "nothing"}.')
    elif compare is None:
        found.append(f'Graded commit {graded} could not be read on GitHub. Push it, or re-grade.')
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


def gh(*args: str) -> str:
    run = subprocess.run(['gh', *args], capture_output=True, text=True)
    if run.returncode != 0:
        sys.exit(f"gh {' '.join(args[:3])} failed: {run.stderr.strip()}")
    return run.stdout


def graded_comparison(repo: str, graded: str, head: str) -> dict | None:
    """GitHub's comparison of the graded commit with the PR head, or None when it cannot be read."""
    if not SHA.fullmatch(graded):
        return None
    try:
        data = json.loads(gh('api', f'repos/{repo}/compare/{graded}...{head}'))
    except SystemExit:
        return None
    return {'status': data['status'], 'files': [f['filename'] for f in data.get('files', [])]}


def check(pr: int, repo: str, root: Path) -> tuple[bool, list[str]]:
    """Whether the PR needs a grade, and what stops it."""
    config = load_config(root)
    view = json.loads(gh('pr', 'view', str(pr), '--repo', repo, '--json', 'body,headRefName,headRefOid,isDraft'))
    branches = (config.get('requireGrade') or {}).get('branches')
    if view['isDraft'] or (branches and not re.search(branches, view['headRefName'])):
        return False, []
    rows = gh('api', '--paginate', f'repos/{repo}/pulls/{pr}/files', '--jq',
              '.[] | [.filename, (.previous_filename // empty)] | @tsv').splitlines()
    if len(rows) >= PR_FILE_CAP:
        return True, [f'PR #{pr} touches {PR_FILE_CAP} files, the most GitHub lists, so its grade mode cannot be '
                      'checked. Split it.']
    paths = [path for row in rows for path in row.split('\t') if path]
    # The files list came from a second read; a push since the first would pair it with an old compare.
    head = json.loads(gh('pr', 'view', str(pr), '--repo', repo, '--json', 'headRefOid'))['headRefOid']
    if head != view['headRefOid']:
        return True, [f"PR #{pr} moved from {view['headRefOid']} to {head} while it was checked. Run the check again."]
    block = parse(view['body'] or '')
    compare = graded_comparison(repo, (block or {}).get('Graded', ''), head)
    return True, problems(block, pr_files=paths, lenses=lens_ids(root / config['lenses']), compare=compare,
                          root=root, config=config)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    run = sub.add_parser('check', help='refuse the PR unless it carries a current 5/5 grade')
    run.add_argument('pr', type=int)
    run.add_argument('--repo', help='owner/name; defaults to the current repository')
    args = ap.parse_args()
    repo = args.repo or gh('repo', 'view', '--json', 'nameWithOwner', '--jq', '.nameWithOwner').strip()
    required, found = check(args.pr, repo, repo_root())
    if found:
        sys.exit('\n'.join(found))
    print(f'PR #{args.pr}: ' + ('the grade is current' if required else 'no grade required (draft, or branch not in scope)'))


if __name__ == '__main__':
    main()
