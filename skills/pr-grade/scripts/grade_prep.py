#!/usr/bin/env python3
"""Everything a /pr-grade coordinator hands its graders, in one call.

    python3 grade_prep.py [--base origin/main] [--groups NAME=L1,L2 ...] [--settled TEXT] [--declined TEXT]
                          [--callers TEXT]

Run it from inside the repository once the change is committed. It picks the mode as grade_mode.py
does, splits the lenses among graders (the lens file's `## Fan-out groups` table, or timing, reach, and
lifetime and contract), lists the callers of each function the branch modified, runs check_then_act.py
for whoever holds L7, and makes one proof copy per grader with proof_dir.py. It prints a shared block
and one block per grader: a grader's prompt is the shared block followed by its own, pasted unchanged.
It ends with the command that merges the graders' reports and the one that removes the proof copies.

`--base` is where the branch left, or the last commit graded for a re-grade. `--settled` names the
checks that already passed at the head, with their results, and `--declined` the review findings the
author declined, with reasons; each is pasted into every prompt as given. `--groups` replaces the
groups, one `NAME=L1,L2` per grader. `--callers` adds callers found by hand to the derived list.

Callers are derived for functions in TypeScript, JavaScript, and Python that existed before the branch.
A changed type, option, or constant, and code in any other language, is named as not derived, never
left out silently.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str):
    """A script beside this one, loaded by path under a private name, like grade_block.py loads grade_mode."""
    spec = importlib.util.spec_from_file_location(f'_pr_grade_{name}', HERE / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


grade_mode, grade_block, proof_dir, check_then_act = (_load(n) for n in ('grade_mode', 'grade_block', 'proof_dir',
                                                                          'check_then_act'))

DEFAULT_GROUPS = {'timing': ['L1', 'L2', 'L7'], 'reach': ['L3', 'L4'], 'lifetime-and-contract': ['L5', 'L6', 'L8']}
GROUP_ROW = re.compile(r'(?m)^\|\s*([^|]*?[A-Za-z][^|]*?)\s*\|\s*(L\d+(?:\s*,\s*L\d+)*)\s*\|')
LOCKS = ('package-lock.json', 'npm-shrinkwrap.json', 'yarn.lock', 'pnpm-lock.yaml', 'bun.lock', 'bun.lockb',
         'poetry.lock', 'uv.lock', 'Pipfile.lock', 'Cargo.lock', 'go.sum', 'composer.lock', 'Gemfile.lock')
VENDORED = (':!vendor/*', ':!third_party/*', ':!node_modules/*')
CALLERS_SHOWN = 30


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, check=check)


def slug(name: str) -> str:
    return re.sub(r'[^A-Za-z0-9]+', '-', name).strip('-').lower() or 'group'


def groups_for(mode: str, lenses: list[str], lens_text: str | None, given: list[str]) -> dict[str, list[str]]:
    """Grader name to its lenses. Every lens lands in exactly one group: one the table leaves out gets a
    group of its own, so a lens the lens file added is never left ungraded."""
    if given:
        groups = {}
        for item in given:
            name, _, ids = item.partition('=')
            groups[slug(name)] = re.findall(r'L\d+', ids)
    elif mode != 'fan-out':
        return {'grade': lenses}
    else:
        section = re.search(r'(?ms)^## Fan-out groups\s*$(.*?)(?=^## |\Z)', lens_text or '')
        rows = GROUP_ROW.findall(section.group(1)) if section else []
        groups = {slug(name): re.findall(r'L\d+', ids) for name, ids in rows if name.lower() != 'group'}
        groups = groups or {name: [lens for lens in ids if lens in lenses] for name, ids in DEFAULT_GROUPS.items()}
    placed = {lens for ids in groups.values() for lens in ids}
    unknown = sorted(placed - set(lenses))
    if unknown:
        sys.exit(f"grade_prep: groups name {', '.join(unknown)}, which the lens file does not define")
    rest = [lens for lens in lenses if lens not in placed]
    if rest:
        groups['other-lenses'] = rest
    return {name: ids for name, ids in groups.items() if ids}


def symbol(name: str) -> str | None:
    """What a caller writes to reach a function the adapters named: the method, or the class for a
    constructor. None for a callback or an anonymous function, which nothing calls by name."""
    if ' > ' in name or name.startswith('<'):
        return None
    head, _, last = name.rpartition('.')
    return head.rpartition('.')[2] if last in ('constructor', '__init__') and head else last


def modified_functions(root: Path, fork: str, lines: dict, config: dict) -> tuple[dict[str, str], list[str]]:
    """Each function the branch changed that existed at `fork`, as symbol to `file:line`, and a note for
    each changed code file whose functions could not be read."""
    code = [f for f in lines if (root / f).is_file() and check_then_act.is_code(f, config)]
    files = [f for f in code if check_then_act.supported(f)]
    parsed, skipped, _ = check_then_act.parse_typescript([f for f in files if check_then_act.is_typescript(f)], root)
    python, python_skipped = check_then_act.parse_python([f for f in files if not check_then_act.is_typescript(f)], root)
    parsed.update(python)
    notes = [f"{s['file']}: no adapter reads this language" for s in check_then_act.unscannable(code)]
    notes += [f"{s['file']}: {s['reason']}" for s in skipped + python_skipped]
    found: dict[str, str] = {}
    for file in files:
        ranges = lines.get(file)
        for fn in parsed.get(file, []):
            first, last = fn['span'][0][0], fn['span'][1][0]
            name = symbol(fn['name'])
            if name is None or name in found or (ranges is not None and not any(
                    start <= last and first <= end for start, end in ranges)):
                continue
            # A function new on the branch has no caller the branch did not write.
            if git(root, 'grep', '-q', '-w', '-e', name, fork, '--', file, check=False).returncode == 0:
                found[name] = f'{file}:{first}'
    return found, notes


def callers(root: Path, head: str, names: dict[str, str]) -> list[str]:
    out = []
    for name, where in sorted(names.items()):
        run = git(root, '-c', 'core.quotePath=false', 'grep', '-n', '-a', '-w', '-e', name, head, '--', '.', *VENDORED,
                  check=False)
        # `<sha>:<path>:<line>:<text>`, and only the location is kept.
        hits = [':'.join(line.split(':', 3)[1:3]) for line in run.stdout.splitlines()]
        hits = [hit for hit in hits if hit != where]
        shown = ', '.join(hits[:CALLERS_SHOWN]) or 'none found'
        more = f', and {len(hits) - CALLERS_SHOWN} more' if len(hits) > CALLERS_SHOWN else ''
        out.append(f'- `{name}`, defined {where}: {shown}{more}')
    return out


def l7_block(root: Path, base: str) -> str:
    result = check_then_act.scan(root, base, False, [])
    skipped = [f"skipped {s['file']}: {s['reason']}" for s in result['skipped']]
    return '\n'.join([check_then_act.render(result), *skipped])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--base', default='origin/main')
    ap.add_argument('--groups', action='append', default=[], metavar='NAME=L1,L2')
    ap.add_argument('--settled', default='')
    ap.add_argument('--declined', default='')
    ap.add_argument('--callers', default='', help='callers found by hand, for a type, option, or other language')
    args = ap.parse_args()
    root = grade_mode.repo_root()
    if git(root, 'status', '--porcelain', '--untracked-files=no').stdout.strip():
        sys.exit('grade_prep: the change has uncommitted edits. Graders see commits only; commit, then run this again.')
    config = grade_mode.load_config(root)
    try:
        fork = git(root, 'merge-base', args.base, 'HEAD').stdout.strip()
    except subprocess.CalledProcessError as failed:
        sys.exit(f'grade_prep: cannot find where HEAD left {args.base}: {failed.stderr.strip()}')
    head = git(root, 'rev-parse', 'HEAD').stdout.strip()
    changed = grade_mode.changed_files(args.base, root)
    if not changed:
        sys.exit(f'grade_prep: no changes since {args.base}; nothing to grade')
    mode, reasons, _ = grade_mode.assess(changed, root, config)
    lens_path = root / config['lenses']
    lens_text = lens_path.read_text() if lens_path.is_file() else None
    lenses = grade_block.lens_ids(lens_text)
    groups = groups_for(mode, lenses, lens_text, args.groups)

    lines = grade_mode.changed_lines(args.base, root)
    modified, notes = modified_functions(root, fork, lines, config)
    try:
        made = proof_dir.make(root, head, list(groups), config, Path(tempfile.gettempdir()))
    except subprocess.CalledProcessError as failed:
        sys.exit(f"grade_prep: making the proof copies failed at `git {' '.join(failed.cmd[1:3])}`: "
                 f"{(failed.stderr or '').strip()}")
    fields = dict(line.split(': ', 1) for line in made if ': ' in line)
    proof_root = Path(fields['root'])
    (proof_root / 'grade.json').write_text(json.dumps(
        {'repository': str(root), 'mode': mode, 'base': fork, 'head': head, 'lenses': lenses, 'groups': groups},
        indent=1) + '\n')

    locks = sorted({path for path in changed if path.rsplit('/', 1)[-1] in LOCKS})
    excludes = ''.join(f" ':!{path}'" for path in locks)
    left_out = f"; lock files left out: {', '.join(locks)}" if locks else ''
    why = '; '.join(f'{path} ({reason})' for path, reason in sorted(reasons.items())) or 'no silent-failure files'
    copy_notes = [line for line in made if line.startswith(('not linked', 'missing', 'warning', 'note', 'linked'))]
    caller_lines = callers(root, head, modified) or ['- none: the branch modified no function that existed before it']
    caller_lines += [f'- not derived for {note}; search by hand' for note in notes]
    caller_lines += [f'- {line.strip()}' for line in args.callers.splitlines() if line.strip()]

    print(f'mode: {mode} ({why})')
    print('graders: ' + ', '.join(f"{name} ({', '.join(ids)})" for name, ids in groups.items()))
    if mode == 'in-thread':
        print('In-thread: grade it yourself from the shared block, proving in the copy below.')
    print('\n===== shared block =====\n')
    print('\n'.join([
        f'- Repository: {root}',
        f"- Lens file: {lens_path if lens_text is not None else 'none; the skill lenses apply as written'}",
        f'- Base: {fork}',
        f'- Head: {head}',
        f"- Change: `git -C {root} diff {fork} {head} -- .{excludes}` ({len(changed)} files{left_out})",
        '',
        'Settled at the head, do not re-run: ' + (args.settled or 'nothing was given, so run only what a proof needs, '
                                                  'never the whole suite.'),
        '',
        'Declined review findings, with reasons: ' + (args.declined or 'none.'),
        '',
        'Callers of what the branch modified. The list is a floor, never a ceiling: it was found with '
        f"`git -C {root} grep -n -a -w -e '<name>' {head} -- .` outside vendored directories, covers functions only, "
        'and cannot see a re-export, dynamic dispatch, or a name built from a string.',
        *caller_lines,
        *(['', 'Proof copies:', *[f'- {line}' for line in copy_notes]] if copy_notes else []),
    ]))
    for name, ids in groups.items():
        print(f'\n===== {name} =====\n')
        block = [f"Apply pr-grade lenses {', '.join(ids)} to the change in the shared block.",
                 f"- Your proof copy: {fields[f'copy {name}']} (a clone of the head commit, yours alone; not the repository)",
                 f"- Your scratch directory: {fields[f'scratch {name}']}",
                 f'- Write your report to: {proof_root / name / "report.md"}']
        if 'L7' in ids:
            block += ['', 'check_then_act.py at the head:', l7_block(root, args.base)]
        print('\n'.join(block))
    print(f"\nmerge: python3 {HERE / 'grade_block.py'} merge {proof_root}")
    print(f"remove: {fields['remove']}")


if __name__ == '__main__':
    main()
