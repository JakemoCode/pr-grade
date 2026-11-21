#!/usr/bin/env python3
"""How /pr-grade runs over a branch: in the author's thread, in one subagent, or fanned out by lens group.

    python3 grade_mode.py [--base origin/main]
    python3 grade_mode.py --scan [--base origin/main] [--branch-base REF] [--exclude-path PREFIX ...]

Run it from inside the repository being graded. It reads `.claude/pr-grade.json` there when present
(every key optional, defaults below) and prints the mode, the code file count, and each silent-failure
file with the reason it is one.

A silent-failure file is one where a defect would pass every test: a check, a gate, persistence, CI,
hooks. The config names them with `silent` patterns, and `silentCommand` can print more, one path per
line, for a repository that already keeps that list somewhere (a map, a guarantee file). `ordinary`
patterns name the code a test would catch a defect in. A code file that nothing names is unclassified
and grades as silent: no rule has looked at it, and grading it too cheaply is a failure no check reports.

    silent or unclassified, more than fanOutAbove code files   fan-out
    silent or unclassified, or more than fanOutAbove           subagent
    neither                                                    in-thread

`countAsCode` lists paths that count toward size whatever `tests` and `notCode` say, for a data
file the repository treats as code under a directory it otherwise leaves out. Each entry is a glob
over the whole path, so an exact path matches only itself and `docs/*/owners.yaml` matches one file
name in every folder under docs/.

Patterns are fnmatch globs. One with a `/` matches the whole path, and its `*` crosses directories;
one without matches the file name. The branch's files include uncommitted and untracked work. Both
sides of a rename count, so a rename can raise the mode but never lowers it.

`--scan` also says whether a first read runs before the graders: one `pr-grade:scan` agent, Opus with a bare question
and no lenses, whose report grade_prep.py hands one grader as numbered suspects. It prints `scan: yes` or `scan: no`
with the reason. A first read runs in round 1 of a `subagent` or `fan-out` grade with prompt files, on a change of
more than `scanAbove` changed lines (default 150; `false` turns it off, and 0 scans every change). Changed lines are
the additions plus the deletions in the diff the graders read: committed, from where the branch left `--base` to
HEAD, with lock files and each `--exclude-path` prefix left out, and a binary file counted as none. A round is a
re-grade when the commit it starts from is on the branch, after where the branch left `--branch-base` (default
origin/main, or `--base` when there is none). On a yes it makes a directory for the report and prints the scan's
prompt and the `--suspects <dir>` to pass the prep.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import posixpath
import re
import subprocess
import sys
import tempfile
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
    # Empty, so a repository grades its code in-thread only once it says which code is ordinary.
    'ordinary': [],
    'tests': ['tests/*', 'test/*', '*/tests/*', '*/test/*', '__tests__/*', '*/__tests__/*', '*.test.*', '*.spec.*',
              '*_test.*', 'test_*'],
    'notCode': ['*.md', 'docs/*', 'LICENSE', 'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', '*.lock', 'go.sum'],
    'countAsCode': [],
}
LOCKS = ('package-lock.json', 'npm-shrinkwrap.json', 'yarn.lock', 'pnpm-lock.yaml', 'bun.lock', 'bun.lockb',
         'poetry.lock', 'uv.lock', 'Pipfile.lock', 'Cargo.lock', 'go.sum', 'composer.lock', 'Gemfile.lock')
SCAN_ABOVE = 150
# The prompt the scan-then-proof test gave Opus, word for word: anything added to it is untested.
SCAN_PROMPT = ('Review the change between {base} and {head} in this repository. Use git to read it. Report every bug or '
               'defect you find in the changed code, giving the file path and line for each.')
# Claude Code refuses a subagent's Write to report*.md, summary*.md, findings*.md and analysis*.md.
SCAN_REPORT = 'first-read.md'
# A string where a list belongs is read one character at a time, and a lone `*` matches every file:
# under `ordinary` that grades everything in-thread.
LIST_KEYS = ('silent', 'ordinary', 'tests', 'notCode', 'countAsCode')
UNCLASSIFIED = (f'unclassified: add it to `ordinary` in {CONFIG} if a test would catch a defect here, '
                'or to `silent` if none would')
# Printed once per run while the config sets no `ordinary`, so the dearer grade after an upgrade has a reason.
NO_ORDINARY = (f'notice: no `ordinary` key in {CONFIG}, so unclassified code raises the grade mode (pr-grade 0.9.0); '
               'list ordinary code there to grade it in-thread')


def _git(root: Path, *args: str) -> str:
    return subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, check=True).stdout


def repo_root(start: Path | None = None) -> Path:
    return Path(_git(start or Path.cwd(), 'rev-parse', '--show-toplevel').strip())


def _given(root: Path, text: str | None = None) -> dict:
    """The keys the repository's config sets, read from `text` when given, else from `root`. A missing file, or
    one holding only whitespace, sets none."""
    if text is None:
        path = root / CONFIG
        text = path.read_text() if path.exists() else None
    return json.loads(text) if text and text.strip() else {}


def load_config(root: Path, text: str | None = None) -> dict:
    """The repository's config over the defaults, read from `text` when given, else from `root`. A key it
    sets replaces the default list whole. A pattern or path key that is not a list of strings stops the
    run."""
    config = {**DEFAULTS, **_given(root, text)}
    for key in LIST_KEYS:
        value = config[key]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            sys.exit(f'{key} in {CONFIG} must be a list of strings')
    return config


def matches(path: str, patterns: list[str]) -> str | None:
    """The first pattern `path` matches, or None."""
    name = posixpath.basename(path)
    return next((p for p in patterns if fnmatch.fnmatchcase(path if '/' in p else name, p)), None)


def ordinary_notice(root: Path) -> str | None:
    """NO_ORDINARY when the repository's config sets no `ordinary` key, else None."""
    return None if 'ordinary' in _given(root) else NO_ORDINARY


def code_files(changed: list[str], config: dict) -> list[str]:
    """Changed files that count toward size: those `countAsCode` matches, and the rest that are neither
    tests nor `notCode`."""
    # A caller that embeds the selector may build its config without this key.
    listed = config.get('countAsCode', ())
    # Equality too: as a glob, `docs/[id]/x.yaml` would match `docs/i/x.yaml` and never itself.
    return [p for p in changed if any(p == entry or fnmatch.fnmatchcase(p, entry) for entry in listed)
            or not matches(p, config['tests']) and not matches(p, config['notCode'])]


def silent_reasons(changed: list[str], root: Path, config: dict, named: dict[str, str] | None = None) -> dict[str, str]:
    """Each changed silent-failure file, with why it is one, unclassified code included. `named` maps
    exact paths a caller knows are silent to the reason, for a repository that embeds this selector. A
    failing `silentCommand` stops the run with its own message: a grade picked without it could be too
    cheap."""
    listed: set[str] = set()
    if config['silentCommand']:
        run = subprocess.run(config['silentCommand'], shell=True, cwd=root, capture_output=True, text=True)
        if run.returncode != 0:
            sys.exit(f"silentCommand failed (exit {run.returncode}): {run.stderr.strip() or run.stdout.strip()}")
        listed = set(run.stdout.split())
    named = named or {}
    code = set(code_files(changed, config))
    # A caller that embeds the selector may build its config without this key.
    ordinary = config.get('ordinary', ())
    reasons = {}
    for path in changed:
        pattern = matches(path, config['silent'])
        if pattern:
            reasons[path] = f'matches {pattern}'
        elif path in named:
            reasons[path] = named[path]
        elif path in listed:
            reasons[path] = 'named by silentCommand'
        elif path in code and not matches(path, ordinary):
            reasons[path] = UNCLASSIFIED
    return reasons


def mode_for(silent: bool, code_count: int, fan_out_above: int) -> str:
    large = code_count > fan_out_above
    if silent and large:
        return MODES[2]
    return MODES[1] if silent or large else MODES[0]


def assess(changed: list[str], root: Path, config: dict | None = None,
           named: dict[str, str] | None = None) -> tuple[str, dict[str, str], list[str]]:
    """The mode, each silent-failure or unclassified file with its reason, and every file the grade must cover: all of
    them but `notCode`, plus any silent file or `countAsCode` path there. Tests are covered, since a test weakened after the
    grade can undo the proof a finding rested on. `named` is as `silent_reasons` takes it."""
    config = config or load_config(root)
    reasons, code = silent_reasons(changed, root, config, named), code_files(changed, config)
    mode = mode_for(bool(reasons), len(code), config['fanOutAbove'])
    return mode, reasons, [p for p in changed if p in reasons or p in code or not matches(p, config['notCode'])]


def changed_files(base: str, root: Path) -> list[str]:
    """Every path the branch touches since it left `base`: committed, uncommitted, and untracked, with
    both sides of a rename."""
    fork = _git(root, 'merge-base', base, 'HEAD').strip()
    # Unquoted paths: git's default C-quotes any non-ASCII name, which then matches no file on disk.
    tracked = _git(root, '-c', 'core.quotePath=false', 'diff', '--name-only', '--no-renames', fork).splitlines()
    untracked = _git(root, '-c', 'core.quotePath=false', 'ls-files', '--others', '--exclude-standard').splitlines()
    return list(dict.fromkeys([*tracked, *untracked]))


HUNK = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@')


def _header_path(line: str) -> str | None:
    """The path in `diff --git a/P b/P`, or None when git quoted it. Without renames both sides are the
    same path, so the split is at the middle even when the path holds a space."""
    rest = line[len('diff --git '):]
    half = (len(rest) - 5) // 2
    if rest.startswith('a/') and rest[2:2 + half] == rest[len(rest) - half:] and rest[2 + half:len(rest) - half] == ' b/':
        return rest[2:2 + half]
    return None


def changed_lines(base: str, root: Path, head: str | None = None) -> dict[str, list[tuple[int, int]] | None]:
    """The new-side line ranges each changed path touches since it left `base`, uncommitted work
    included. An untracked file maps to None: all of it is new. A pure deletion marks the lines either
    side of it, so removing a lock or a re-check still puts the function in scope. With `head`, the
    ranges are those of the commits from the fork to `head` alone."""
    fork = _git(root, 'merge-base', base, head or 'HEAD').strip()
    diff = _git(root, '-c', 'core.quotePath=false', 'diff', '-U0', '--no-renames', '--no-color', '--no-ext-diff',
                '--src-prefix=a/', '--dst-prefix=b/', fork, *([head] if head else []))
    ranges: dict[str, list[tuple[int, int]] | None] = {}
    path, in_header = None, False
    for line in diff.splitlines():
        # File headers come between `diff --git` and the first hunk; after that a line starting `+++` is
        # an added line whose text starts `++`.
        if line.startswith('diff --git '):
            # An empty new file or a mode-only change has no `+++` line, only this one.
            path, in_header = _header_path(line), True
            if path is not None:
                ranges.setdefault(path, [])
        elif in_header and line.startswith('deleted file mode'):
            ranges.pop(path, None)
            path = None
        elif in_header and line.startswith('+++ '):
            # git appends a tab to a path that contains whitespace.
            path = None if line == '+++ /dev/null' else line[len('+++ b/'):].rstrip('\t')
            if path is not None:
                ranges.setdefault(path, [])
        elif line.startswith('@@'):
            in_header = False
            hunk = HUNK.match(line)
            if hunk and path is not None:
                start, count = int(hunk[1]), int(hunk[2]) if hunk[2] is not None else 1
                ranges[path].append((start, start + count - 1) if count else (max(start, 1), start + 1))
    if head:
        return ranges
    for untracked in _git(root, '-c', 'core.quotePath=false', 'ls-files', '--others', '--exclude-standard').splitlines():
        ranges[untracked] = None
    return ranges


def scan_above(config: dict) -> int | None:
    """`scanAbove` from the config: a count of changed lines, or None when it is false. Anything else stops the run,
    since `"150"` or `true` read as a count would scan by a rule nobody wrote."""
    value = config.get('scanAbove', SCAN_ABOVE)
    if value is False:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        sys.exit(f'scanAbove in {CONFIG} must be false or a whole number of changed lines, not {json.dumps(value)}')
    return value


def graded_lines(root: Path, fork: str, head: str, kept: tuple[str, ...] = ()) -> int:
    """Additions plus deletions from `fork` to `head`, as the graders' diff holds them: lock files and the `kept`
    prefixes left out, and a binary file counted as none."""
    out = _git(root, '-c', 'core.quotePath=false', 'diff', '--numstat', '-z', '--no-renames', fork, head)
    total = 0
    for record in out.split('\0'):
        added, deleted, path = (record.split('\t', 2) + ['', '', ''])[:3]
        if not path or added == '-' or posixpath.basename(path) in LOCKS or path.startswith(kept):
            continue
        total += int(added) + int(deleted)
    return total


def scan_decision(root: Path, base: str, branch_base: str, config: dict,
                  kept: tuple[str, ...] = ()) -> tuple[bool, str]:
    """Whether a first read runs before this round's graders, and why, as `--scan` prints it."""
    above = scan_above(config)
    if above is None:
        return False, f'scanAbove is false in {CONFIG}'
    fork = _git(root, 'merge-base', base, 'HEAD').strip()
    # The mode the prep will print: it grades committed files only.
    committed = set(_git(root, '-c', 'core.quotePath=false', 'diff', '--name-only', '--no-renames', fork,
                         'HEAD').split('\n')) - {''}
    mode = assess([path for path in changed_files(base, root) if path in committed], root, config)[0]
    if mode == MODES[0]:
        return False, 'an in-thread grade'
    if config.get('promptFiles', True) is False:
        return False, 'promptFiles is false, and the first read reaches a grader only through its prompt file'
    # A re-grade starts at the last commit graded, which is on the branch; a stale main is still main.
    on_main = subprocess.run(['git', 'merge-base', '--is-ancestor', fork, branch_base], cwd=root, capture_output=True)
    if on_main.returncode != 0:
        return False, f'a re-grade: {base} is after where the branch left {branch_base}'
    lines = graded_lines(root, fork, 'HEAD', kept)
    if above == 0:
        return True, f'{lines} changed lines, scanAbove 0 scans every change, in round 1 of a {mode} grade'
    if lines <= above:
        return False, f'{lines} changed lines, not over scanAbove {above}'
    return True, f'{lines} changed lines, over scanAbove {above}, in round 1 of a {mode} grade'


def scan(root: Path, base: str, branch_base: str, config: dict, kept: tuple[str, ...]) -> None:
    """Print the decision. On a yes, make the report's directory and print the scan's prompt and the prep's option."""
    if _git(root, 'status', '--porcelain', '--untracked-files=no').strip():
        sys.exit('grade_mode: the change has uncommitted edits. The scan and the graders see commits only; commit, '
                 'then run this again.')
    yes, why = scan_decision(root, base, branch_base, config, kept)
    print(f"scan: {'yes' if yes else 'no'} ({why})")
    if not yes:
        return
    fork, head = _git(root, 'merge-base', base, 'HEAD').strip(), _git(root, 'rev-parse', 'HEAD').strip()
    where = Path(tempfile.mkdtemp(prefix='pr-grade-scan-'))
    (where / 'scan.json').write_text(json.dumps({'repository': str(root), 'base': fork, 'head': head}) + '\n')
    print('Dispatch one pr-grade:scan agent now, with this line as its prompt, unchanged:')
    print(SCAN_PROMPT.format(base=fork, head=head) + f' Write the report to {where / SCAN_REPORT} with the Write tool.')
    print('Then run the prep while it reads, with this option added:')
    print(f'--suspects {where}')


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--base', default='origin/main')
    ap.add_argument('--scan', action='store_true', help='also say whether a first read runs, and print its dispatch')
    ap.add_argument('--branch-base', help='where the whole branch began, to tell a re-grade from round 1 '
                                           '(default origin/main, or --base when there is none)')
    ap.add_argument('--exclude-path', action='append', default=[], metavar='PREFIX',
                    help='leave paths starting with PREFIX out of the changed lines, as the prep does; repeats')
    args = ap.parse_args()
    root = repo_root()
    config = load_config(root)
    changed = changed_files(args.base, root)
    if not changed:
        sys.exit(f'no changes since {args.base}; nothing to grade')
    mode, reasons, _ = assess(changed, root, config)
    print(f'mode: {mode}')
    notice = ordinary_notice(root)
    if notice:
        print(notice)
    print(f"code files: {len(code_files(changed, config))} (more than {config['fanOutAbove']} raises the mode)")
    print('silent-failure and unclassified files:' if reasons else 'silent-failure and unclassified files: none')
    for path, reason in sorted(reasons.items()):
        print(f'  {path}: {reason}')
    if args.scan:
        branch_base = args.branch_base
        if branch_base is None:
            has_main = subprocess.run(['git', 'rev-parse', '--verify', '--quiet', 'origin/main'], cwd=root,
                                      capture_output=True).returncode == 0
            branch_base = 'origin/main' if has_main else args.base
        scan(root, args.base, branch_base, config, tuple(args.exclude_path))


if __name__ == '__main__':
    main()
