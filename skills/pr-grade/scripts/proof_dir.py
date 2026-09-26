#!/usr/bin/env python3
"""Proof copies for /pr-grade: one checkout of the graded commit per grader, outside the repository,
with the repository's installed dependencies linked in, so a grader writes a failing test and runs it
without building anything first.

    python3 proof_dir.py make [--sha HEAD] [name ...]
    python3 proof_dir.py remove <root>

Run `make` from inside the repository being graded, once per grade, with one name per grader, for
example its fan-out group; with no name it makes one copy called `grade`. It prints each path absolute
and resolved, for the dispatch to carry as printed, and ends with the `remove` command to run once
every grader has reported. Copies go under the temp directory, or under TMPDIR when it is set.

Each copy is a local clone of the repository, checked out detached at the graded commit. Git hardlinks
a local clone's objects rather than borrowing them, so a reset and `gc` in the author's checkout while
graders run cannot pull objects out from under a copy. It is not a git worktree, so nothing is registered in the repository:
a copy left behind is a directory in the temp directory and nothing more, and code that finds its
repository through git finds the copy, never the author's checkout. A copy holds committed files
only. No hook runs, submodules are not checked out, and Git LFS files stay pointers.

A dependency directory is linked from the author's checkout only when every file that pins it reads
the same there as at the graded commit, so a change that edits a manifest or lock file gets no stale
link; `make` names the files that differ and the install to run in each copy instead. The defaults
link `node_modules` and `.venv`. `proofDir` in `.claude/pr-grade.json` replaces them. A `pinnedBy`
pattern is an fnmatch glob matched against the whole path from the repository root, and an entry
without `pinnedBy` is always linked:

    "proofDir": [
      {"path": "node_modules", "pinnedBy": ["package.json", "package-lock.json"], "install": "npm ci"},
      {"path": ".env.test"}
    ]

A linked directory is still the author's: installing into it from a copy changes the author's
checkout. `remove` deletes the link, never what it points to, and refuses a directory `make` did not
make. `make` first removes every root it made more than a day ago, so a grade that died before
`remove` leaves nothing behind for long.
"""
from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_spec = importlib.util.spec_from_file_location('_pr_grade_mode', Path(__file__).resolve().parent / 'grade_mode.py')
grade_mode = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = grade_mode
_spec.loader.exec_module(grade_mode)
load_config, repo_root = grade_mode.load_config, grade_mode.repo_root

PREFIX = 'pr-grade-proof-'
MARKER = '.pr-grade-proof'
STALE_AFTER = 24 * 60 * 60
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]*')
DEFAULTS = [
    {'path': 'node_modules', 'pinnedBy': ['package.json', 'package-lock.json', 'npm-shrinkwrap.json',
                                          'pnpm-lock.yaml', 'yarn.lock', 'bun.lock', 'bun.lockb', '.npmrc']},
    {'path': '.venv', 'pinnedBy': ['uv.lock', 'poetry.lock', 'Pipfile.lock', 'requirements*.txt', 'pyproject.toml',
                                   'setup.py', 'setup.cfg']},
]
# The install an entry without its own suggests: the first of its pins present at the graded commit.
INSTALLS = {'package-lock.json': 'npm ci', 'npm-shrinkwrap.json': 'npm ci',
            'pnpm-lock.yaml': 'pnpm install --frozen-lockfile', 'yarn.lock': 'yarn install --frozen-lockfile',
            'bun.lock': 'bun install --frozen-lockfile', 'bun.lockb': 'bun install --frozen-lockfile',
            'uv.lock': 'uv sync --frozen', 'poetry.lock': 'poetry install'}


def git(cwd: Path, *args: str, env: dict | None = None) -> str:
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, check=True, env=env).stdout


def entries(config: dict) -> list[dict]:
    """`proofDir` from the config, or the defaults. A malformed entry stops the run before anything is
    made: a copy missing a dependency fails its proof for a reason unrelated to the finding."""
    given = config.get('proofDir')
    if given is None:
        return DEFAULTS
    if not isinstance(given, list):
        sys.exit('proofDir in .claude/pr-grade.json must be a list')
    for entry in given:
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str) or not entry['path']:
            sys.exit('proofDir: each entry must be an object with a "path"')
        unknown = sorted(set(entry) - {'path', 'pinnedBy', 'install'})
        if unknown:
            sys.exit(f"proofDir: unknown key {', '.join(unknown)}; the keys are path, pinnedBy, install")
        if Path(entry['path']).is_absolute() or '..' in Path(entry['path']).parts:
            sys.exit(f"proofDir: {entry['path']} must be a path inside the repository")
        pins = entry.get('pinnedBy', [])
        if not isinstance(pins, list) or not all(isinstance(p, str) for p in pins):
            sys.exit(f"proofDir: pinnedBy for {entry['path']} must be a list of strings")
        if not isinstance(entry.get('install', ''), str):
            sys.exit(f"proofDir: install for {entry['path']} must be a string")
    return given


def _at(repo: Path, sha: str, path: str) -> bytes | None:
    run = subprocess.run(['git', 'show', f'{sha}:{path}'], cwd=repo, capture_output=True)
    return run.stdout if run.returncode == 0 else None


def _here(repo: Path, path: str) -> bytes | None:
    file = repo / path
    return file.read_bytes() if file.is_file() else None


def drifted(repo: Path, sha: str, pins: list[str]) -> list[str]:
    """The files matching `pins` that read differently in the author's checkout and at `sha`, a file
    on one side only included."""
    if not pins:
        return []
    listed = git(repo, '-c', 'core.quotePath=false', 'ls-tree', '-r', '--name-only', sha).splitlines()
    listed += git(repo, '-c', 'core.quotePath=false', 'ls-files', '--cached', '--others',
                  '--exclude-standard').splitlines()
    paths = sorted({p for p in listed if any(fnmatch.fnmatchcase(p, pin) for pin in pins)})
    return [p for p in paths if _at(repo, sha, p) != _here(repo, p)]


def install_for(entry: dict, top: list[str]) -> str | None:
    if 'install' in entry:
        return entry['install']
    return next((INSTALLS[pin] for pin in entry.get('pinnedBy', []) if pin in INSTALLS and pin in top), None)


def link(repo: Path, sha: str, wanted: list[dict], configured: bool, copies: list[Path]) -> list[str]:
    top = git(repo, '-c', 'core.quotePath=false', 'ls-tree', '--name-only', sha).splitlines()
    lines = []
    for entry in wanted:
        path, source = entry['path'], repo / entry['path']
        if not source.exists():
            if configured:
                lines.append(f"missing {path}: not in the author's checkout, so not linked")
            continue
        drift = drifted(repo, sha, entry.get('pinnedBy', []))
        if drift:
            install = install_for(entry, top)
            then = f'in each copy run: {install}' if install else 'install it in each copy that needs it'
            lines.append(f"not linked {path}: pins differ from the graded commit ({', '.join(drift)}); {then}")
            continue
        try:
            for copy in copies:
                target = copy / path
                if not (target.exists() or target.is_symlink()):  # a commit that carries it keeps its own
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.symlink(source, target, target_is_directory=source.is_dir())
        except OSError as error:
            lines.append(f'not linked {path}: {error.strerror}')
            continue
        lines.append(f'linked {path}: {source}')
    return lines


def sweep(parent: Path, now: float) -> None:
    """Removes roots made here more than STALE_AFTER ago: their grade is over, whether or not it ran
    `remove`."""
    for old in parent.glob(PREFIX + '*'):
        marker = old / MARKER
        if not old.is_symlink() and marker.is_file() and now - marker.stat().st_mtime > STALE_AFTER:
            shutil.rmtree(old, ignore_errors=True)


def make(repo: Path, sha: str, names: list[str], config: dict, parent: Path) -> list[str]:
    """One copy and one scratch directory per name under a new root in `parent`; returns the lines to
    print. A failure removes the root before it propagates."""
    if not all(NAME.fullmatch(n) for n in names) or len(set(names)) != len(names):
        sys.exit(f"copy names must be distinct, made of letters, digits, '-' and '_': {' '.join(names)}")
    wanted = entries(config)
    sweep(parent, time.time())
    common = (repo / git(repo, 'rev-parse', '--git-common-dir').strip()).resolve()
    root = Path(tempfile.mkdtemp(prefix=PREFIX, dir=parent)).resolve()
    try:
        (root / MARKER).write_text(f'{repo}\n{sha}\n')
        lines, copies = [f'root: {root}', f'commit: {sha}'], []
        # LFS content would be fetched from a remote; a pointer is enough to read, and no hook of the
        # author's, global or local, runs in a copy.
        env = {**os.environ, 'GIT_LFS_SKIP_SMUDGE': '1'}
        for name in names:
            copy, scratch = root / name / 'repo', root / name / 'scratch'
            scratch.mkdir(parents=True)
            git(root, 'clone', '--quiet', '--no-checkout', str(common), str(copy))
            git(copy, '-c', f'core.hooksPath={os.devnull}', '-c', 'advice.detachedHead=false', 'checkout',
                '--quiet', '--detach', sha, env=env)
            lines += [f'copy {name}: {copy}', f'scratch {name}: {scratch}']
            copies.append(copy)
        lines += link(repo, sha, wanted, 'proofDir' in config, copies)
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise
    if git(repo, 'rev-parse', 'HEAD').strip() != sha or git(repo, 'status', '--porcelain',
                                                            '--untracked-files=no').strip():
        lines.append("warning: the author's checkout is not at the graded commit, so an editable install or "
                     "workspace package a linked directory loads from it is not the copy's code")
    if _at(repo, sha, '.gitmodules') is not None:
        lines.append('note: submodules are not checked out; name them under proofDir to link them')
    lines.append(f'remove: python3 {shlex.quote(str(Path(__file__).resolve()))} remove {shlex.quote(str(root))}')
    return lines


def remove(root: Path) -> str:
    """Deletes a root `make` printed. A link inside it is unlinked, never followed."""
    if root.is_symlink() or not (root / MARKER).is_file():
        sys.exit(f'{root} holds no {MARKER}, so it is not a proof root; nothing was removed')
    shutil.rmtree(root)
    return f'removed: {root}'


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = ap.add_subparsers(dest='command', required=True)
    make_ap = commands.add_parser('make', help='make one proof copy per name')
    make_ap.add_argument('--sha', default='HEAD', help='the graded commit (default HEAD)')
    make_ap.add_argument('names', nargs='*', help='one per grader, for example its fan-out group')
    remove_ap = commands.add_parser('remove', help='remove a root make printed')
    remove_ap.add_argument('root', type=Path)
    args = ap.parse_args()
    try:
        if args.command == 'remove':
            print(remove(args.root))
            return
        repo = repo_root()
        sha = git(repo, 'rev-parse', '--verify', f'{args.sha}^{{commit}}').strip()
        print('\n'.join(make(repo, sha, args.names or ['grade'], load_config(repo), Path(tempfile.gettempdir()))))
    except subprocess.CalledProcessError as failed:
        sys.exit(f"{' '.join(failed.cmd[:3])} failed: {(failed.stderr or '').strip() or f'exit {failed.returncode}'}")


if __name__ == '__main__':
    main()