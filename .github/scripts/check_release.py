#!/usr/bin/env python3
"""Refuses a change that ships without a new plugin version.

    python3 .github/scripts/check_release.py [--base origin/main] [--hold]

Claude Code keys plugin updates on `version` in .claude-plugin/plugin.json, so a merged change to
skills/, agents/, or plugin.json reaches no installed copy until the version rises. This refuses such
a change unless it raises the version and CHANGELOG.md has a `## <version>` heading for it. A change
that raises the version for any reason gets the same checks.

`--hold` lets a change to what ships merge without a release, for work that lands over several pull
requests. CI passes it when the pull request carries the `hold-release` label.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

MANIFEST = '.claude-plugin/plugin.json'
CHANGELOG = 'CHANGELOG.md'
SHIPPED = ('skills/', 'agents/', MANIFEST)
VERSION = re.compile(r'(\d+)\.(\d+)\.(\d+)')


def _git(root: Path, *args: str) -> str:
    return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True, text=True).stdout


def changed(root: Path, fork: str) -> list[str]:
    """Every path the branch touches since `fork`: committed, uncommitted, and untracked."""
    tracked = _git(root, '-c', 'core.quotePath=false', 'diff', '--name-only', fork).splitlines()
    untracked = _git(root, '-c', 'core.quotePath=false', 'ls-files', '--others', '--exclude-standard').splitlines()
    return list(dict.fromkeys([*tracked, *untracked]))


def problems(root: Path, base: str, *, hold: bool = False) -> list[str]:
    fork = _git(root, 'merge-base', base, 'HEAD').strip()
    before = json.loads(_git(root, 'show', f'{fork}:{MANIFEST}'))['version']
    after = json.loads((root / MANIFEST).read_text())['version']
    if after == before:
        shipped = [path for path in changed(root, fork) if path.startswith(SHIPPED)]
        if not shipped or hold:
            return []
        return [f"{', '.join(shipped)} changed, but {MANIFEST} is still {before}, so no installed copy gets it. "
                f'Raise the version and add a `## <version>` heading to {CHANGELOG}, or label the pull '
                'request hold-release to merge it unreleased.']
    new = VERSION.fullmatch(after)
    if not new:
        return [f'{MANIFEST} version {after!r} is not three numbers, such as 0.3.2.']
    old = VERSION.fullmatch(before)
    found = []
    if old and tuple(map(int, new.groups())) <= tuple(map(int, old.groups())):
        found.append(f'{MANIFEST} version goes from {before} to {after}; it must rise.')
    if not re.search(rf'(?m)^## {re.escape(after)}[ \t]*$', (root / CHANGELOG).read_text()):
        found.append(f'{CHANGELOG} has no `## {after}` heading for the new version.')
    return found


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--base', default='origin/main')
    ap.add_argument('--hold', action='store_true', help='allow a change to what ships without a release')
    args = ap.parse_args()
    root = Path(_git(Path.cwd(), 'rev-parse', '--show-toplevel').strip())
    found = problems(root, args.base, hold=args.hold)
    for problem in found:
        print(problem, file=sys.stderr)
    if not found:
        print('held: this change ships in a later release' if args.hold else 'release: ok')
    sys.exit(1 if found else 0)


if __name__ == '__main__':
    main()
