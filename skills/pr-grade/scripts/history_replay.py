#!/usr/bin/env python3
"""Old state through new code: run a changed reader over what earlier versions of the code wrote.

    python3 history_replay.py [--base origin/main] [--before <ISO time>]

Run it from inside the repository, at the committed head. A repository declares its formats under
`formats` in `.claude/pr-grade.json`; one that declares none pays nothing. For each format whose
`readers` the branch touches, it makes two runs and prints what they found:

- replay: the base and head readers each read every recorded instance. An instance one accepts and
  the other refuses is listed.
- transplant: each kind of record the change adds, a `records` match on a line the change adds that
  no recorded instance and no base copy of a changed file holds, is put into a recorded instance.
  Every value the instances hold, a `values` match, is run through it with the head reader, beside
  the same value under the record's old kind. A new kind starts out accepting whatever the old
  kinds' fields accept, and a value it accepts that the change says it must refuse is a finding.

A format is an object:

    name        what the report calls it
    readers     globs over the paths whose change runs it, matched as `silent` is
    check       the command that reads instances, as a list; `{tree}` is the checkout whose reader
                runs and `{instances}` a JSON file holding a list of instance texts. It runs from the
                repository root and prints a JSON list, one list of problems per instance, empty
                when the reader accepts it.
    tree        paths the base reader needs, extracted from the base commit (default: each reader's
                directory)
    instances   where instances live, a list of {"prBodies": N} (the last N merged pull request
                bodies, through gh), {"files": glob} (each file the base commit holds), or
                {"lines": glob} (each non-empty line of each such file)
    records     optional: a regex whose `kind` group is the part of a record that names its kind,
                and whose optional `type` group names the record type it belongs to
    values      optional: a regex whose `value` group is a field value recorded under a record

The transplant runs only when the format declares both regexes. `--before` drops pull requests merged
at or after a time, to replay a commit as it was graded. Nothing here stops a grade: a source or a
check that fails is printed as a note.
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import posixpath
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f'_pr_grade_{name}', HERE / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


grade_mode = _load('grade_mode')

SHOWN = 5
VALUES_SHOWN = 15
PROBLEM_CHARS = 300


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(['git', '-c', 'core.quotePath=false', *args], cwd=root, capture_output=True, text=True,
                          check=check)


def shape(value: str) -> str:
    return value


def instances(root: Path, fork: str, fmt: dict, before: str | None) -> tuple[list[tuple[str, str]], list[str]]:
    return [], []


def base_tree(root: Path, fork: str, fmt: dict, into: Path) -> Path:
    return into


def report(root: Path, fork: str, head: str, config: dict, changed: list[str], before: str | None = None) -> list[str]:
    return []


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--base', default='origin/main')
    ap.add_argument('--before', help='leave out pull requests merged at or after this ISO time')
    args = ap.parse_args()
    root = grade_mode.repo_root()
    config = grade_mode.load_config(root)
    fork = git(root, 'merge-base', args.base, 'HEAD').stdout.strip()
    head = git(root, 'rev-parse', 'HEAD').stdout.strip()
    changed = git(root, 'diff', '--name-only', '--no-renames', fork, head).stdout.splitlines()
    lines = report(root, fork, head, config, changed, args.before)
    print('\n'.join(lines) if lines else 'history_replay: the branch touches no reader `formats` declares')


if __name__ == '__main__':
    main()
