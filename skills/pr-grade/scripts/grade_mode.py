#!/usr/bin/env python3
"""How /pr-grade runs over a branch: in the author's thread, in one subagent, or fanned out by lens group.

    python3 grade_mode.py [--base origin/main]

Run it from inside the repository being graded. It reads `.claude/pr-grade.json` there when present
(every key optional, defaults below) and prints the mode, the code file count, and each silent-failure
file with the reason it is one.

A silent-failure file is one where a defect would pass every test: a check, a gate, persistence, CI,
hooks. The config names them with `silent` patterns, and `silentCommand` can print more, one path per
line, for a repository that already keeps that list somewhere (a map, a guarantee file).

    silent, more than fanOutAbove code files   fan-out
    silent, or more than fanOutAbove           subagent
    neither                                    in-thread

Patterns are fnmatch globs. One with a `/` matches the whole path, and its `*` crosses directories;
one without matches the file name. The branch's files include uncommitted and untracked work. Both
sides of a rename count, so a rename can raise the mode but never lowers it.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import posixpath
import subprocess
import sys
from pathlib import Path

MODES = ('in-thread', 'subagent', 'fan-out')
CONFIG = '.claude/pr-grade.json'
# The grade's own rules are silent: a PR that loosens them is grading itself.
DEFAULTS = {
    'lenses': '.claude/pr-grade-lenses.md',
    'fanOutAbove': 4,
    'silent': ['.github/workflows/*', '.husky/*', '.claude/hooks/*', '.claude/settings.json', 'migrations/*',
               '*/migrations/*', CONFIG, '.claude/pr-grade-lenses.md'],
    'silentCommand': None,
    'tests': ['tests/*', 'test/*', '*/tests/*', '*/test/*', '__tests__/*', '*/__tests__/*', '*.test.*', '*.spec.*',
              '*_test.*', 'test_*'],
    'notCode': ['*.md', 'docs/*', 'LICENSE', 'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', '*.lock', 'go.sum'],
}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, check=True).stdout


def repo_root(start: Path | None = None) -> Path:
    return Path(_git(start or Path.cwd(), 'rev-parse', '--show-toplevel').strip())


def load_config(root: Path, text: str | None = None) -> dict:
    """The repository's config over the defaults, read from `text` when given, else from `root`. A key it
    sets replaces the default list whole."""
    if text is None:
        path = root / CONFIG
        text = path.read_text() if path.exists() else None
    return {**DEFAULTS, **(json.loads(text) if text else {})}


def matches(path: str, patterns: list[str]) -> str | None:
    """The first pattern `path` matches, or None."""
    name = posixpath.basename(path)
    return next((p for p in patterns if fnmatch.fnmatchcase(path if '/' in p else name, p)), None)


def code_files(changed: list[str], config: dict) -> list[str]:
    """Changed files that count toward size: neither tests nor `notCode`."""
    return [p for p in changed if not matches(p, config['tests']) and not matches(p, config['notCode'])]


def silent_reasons(changed: list[str], root: Path, config: dict) -> dict[str, str]:
    """Each changed silent-failure file, with why it is one. A failing `silentCommand` stops the run:
    a grade picked without it could be too cheap."""
    named: set[str] = set()
    if config['silentCommand']:
        named = set(subprocess.run(config['silentCommand'], shell=True, cwd=root, capture_output=True, text=True,
                                   check=True).stdout.split())
    reasons = {}
    for path in changed:
        pattern = matches(path, config['silent'])
        if pattern:
            reasons[path] = f'matches {pattern}'
        elif path in named:
            reasons[path] = 'named by silentCommand'
    return reasons


def mode_for(silent: bool, code_count: int, fan_out_above: int) -> str:
    large = code_count > fan_out_above
    if silent and large:
        return MODES[2]
    return MODES[1] if silent or large else MODES[0]


def assess(changed: list[str], root: Path, config: dict | None = None) -> tuple[str, dict[str, str], list[str]]:
    """The mode, each silent-failure file with its reason, and every file the grade must cover: all of
    them but `notCode`, plus any silent file there. Tests are covered, since a test weakened after the
    grade can undo the proof a finding rested on."""
    config = config or load_config(root)
    reasons, code = silent_reasons(changed, root, config), code_files(changed, config)
    mode = mode_for(bool(reasons), len(code), config['fanOutAbove'])
    return mode, reasons, [p for p in changed if p in reasons or not matches(p, config['notCode'])]


def changed_files(base: str, root: Path) -> list[str]:
    """Every path the branch touches since it left `base`: committed, uncommitted, and untracked, with
    both sides of a rename."""
    fork = _git(root, 'merge-base', base, 'HEAD').strip()
    tracked = _git(root, 'diff', '--name-only', '--no-renames', fork).splitlines()
    untracked = _git(root, 'ls-files', '--others', '--exclude-standard').splitlines()
    return list(dict.fromkeys([*tracked, *untracked]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--base', default='origin/main')
    args = ap.parse_args()
    root = repo_root()
    config = load_config(root)
    changed = changed_files(args.base, root)
    if not changed:
        sys.exit(f'no changes since {args.base}; nothing to grade')
    mode, reasons, _ = assess(changed, root, config)
    print(f'mode: {mode}')
    print(f"code files: {len(code_files(changed, config))} (more than {config['fanOutAbove']} raises the mode)")
    print('silent-failure files:' if reasons else 'silent-failure files: none')
    for path, reason in sorted(reasons.items()):
        print(f'  {path}: {reason}')


if __name__ == '__main__':
    main()
