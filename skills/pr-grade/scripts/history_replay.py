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


def malformed(fmt: object) -> str | None:
    """Why a declared format cannot run, or None."""
    if not isinstance(fmt, dict):
        return f'a format is {json.dumps(fmt)}, not an object'
    for key in ('readers', 'check'):
        value = fmt.get(key)
        if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
            return f"format {fmt.get('name', '?')!r} needs `{key}` as a list of strings"
    if not isinstance(fmt.get('instances'), list):
        return f"format {fmt.get('name', '?')!r} needs `instances` as a list"
    return None


def touched(formats: list, changed: list[str]) -> list:
    """The formats whose readers the branch changed, and every malformed one, which `report` names."""
    return [f for f in formats if malformed(f) or any(grade_mode.matches(path, f['readers']) for path in changed)]


def shape(value: str) -> str:
    """A value with what varies between records folded: commit hashes, numbers, and free text after a colon."""
    value = re.sub(r'\b[0-9a-f]{7,40}\b', '<sha>', value)
    value = re.sub(r'\d+', 'N', value)
    return re.sub(r':\s.*', ': ...', value)


def instances(root: Path, fork: str, fmt: dict, before: str | None) -> tuple[list[tuple[str, str]], list[str]]:
    """Every recorded instance as (where, text), oldest first, and a note for each source that failed."""
    found, notes = [], []
    tracked = None
    for source in fmt.get('instances', []):
        if 'prBodies' in source:
            run = subprocess.run(['gh', 'pr', 'list', '--state', 'merged', '--limit', str(int(source['prBodies'])),
                                  '--json', 'number,body,mergedAt'], cwd=root, capture_output=True, text=True)
            if run.returncode != 0:
                notes.append(f'pull request bodies were not read: gh exited {run.returncode}: {run.stderr.strip()}')
                continue
            prs = sorted(json.loads(run.stdout), key=lambda pr: pr['mergedAt'] or '')
            found += [(f"PR #{pr['number']}", pr['body'] or '') for pr in prs
                      if before is None or (pr['mergedAt'] or '') < before]
            continue
        glob = source.get('files') or source.get('lines')
        if not glob:
            notes.append(f'an instance source names no known key: {json.dumps(source)}')
            continue
        if tracked is None:
            tracked = git(root, 'ls-tree', '-r', '--name-only', fork).stdout.splitlines()
        for path in (p for p in tracked if grade_mode.matches(p, [glob])):
            text = git(root, 'show', f'{fork}:{path}').stdout
            if 'files' in source:
                found.append((path, text))
            else:
                found += [(f'{path}:{n}', line) for n, line in enumerate(text.splitlines(), 1) if line.strip()]
    return found, notes


def base_tree(root: Path, fork: str, fmt: dict, into: Path) -> Path:
    """The paths the base reader needs, extracted from the base commit into `into`."""
    paths = fmt.get('tree') or sorted({posixpath.dirname(re.split(r'[*?[]', r, 1)[0]) or '.' for r in fmt['readers']})
    archive = subprocess.run(['git', 'archive', fork, '--', *paths], cwd=root, capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        # The `data` filter, where this Python has it, refuses links and paths that leave the directory.
        tar.extractall(into, **({'filter': 'data'} if hasattr(tarfile, 'data_filter') else {}))
    return into


def check(root: Path, fmt: dict, tree: Path, texts: list[str], scratch: Path) -> list[list[str]]:
    """The problems the reader in `tree` finds in each text. Raises RuntimeError naming what went wrong."""
    path = scratch / 'instances.json'
    path.write_text(json.dumps(texts))
    command = [part.replace('{tree}', str(tree)).replace('{instances}', str(path)) for part in fmt['check']]
    try:
        run = subprocess.run(command, cwd=root, capture_output=True, text=True)
    except OSError as failed:
        raise RuntimeError(f'`{" ".join(command)}` did not start: {failed}') from failed
    if run.returncode != 0:
        # The last line of a traceback is the error; the frames above it would crowd it out of the note.
        last = (run.stderr.strip().splitlines() or ['no output on stderr'])[-1]
        raise RuntimeError(f'exited {run.returncode}: {_clip(last)}, from `{" ".join(command)}`')
    try:
        results = json.loads(run.stdout)
    except ValueError as failed:
        raise RuntimeError(f'`{" ".join(command)}` printed no JSON: {failed}') from failed
    if (not isinstance(results, list) or len(results) != len(texts)
            or not all(isinstance(r, list) and all(isinstance(p, str) for p in r) for r in results)):
        raise RuntimeError(f'`{" ".join(command)}` printed {str(results)[:PROBLEM_CHARS]}, not one list of '
                           f'problem strings per instance ({len(texts)})')
    return results


def _clip(text: str) -> str:
    text = ' '.join(text.split())
    return text if len(text) <= PROBLEM_CHARS else text[:PROBLEM_CHARS] + '...'


def replay(recorded: list[tuple[str, str]], base: list[list[str]], head: list[list[str]], label: str) -> list[str]:
    refused = [(where, h) for (where, _), b, h in zip(recorded, base, head) if not b and h]
    accepted = [(where, b) for (where, _), b, h in zip(recorded, base, head) if b and not h]
    out = [f'Replay: the base and head readers read {len(recorded)} recorded {label}. '
           f'The head refuses {len(refused)} the base accepts, and accepts {len(accepted)} the base refuses.']
    for title, rows in (('Accepted at the base, refused at the head', refused),
                        ('Refused at the base, accepted at the head', accepted)):
        if rows:
            out.append(f'{title}:')
            out += [f'- {where}: {_clip(problems[0])}' + (f' (and {len(problems) - 1} more)' if len(problems) > 1 else '')
                    for where, problems in rows[:SHOWN]]
            if len(rows) > SHOWN:
                out.append(f'- and {len(rows) - SHOWN} more')
    return out


def _records(pattern: re.Pattern, text: str) -> list[re.Match]:
    return [m for m in pattern.finditer(text) if m.start('kind') >= 0]


def _type(match: re.Match) -> str:
    return (match.groupdict().get('type') or '') if 'type' in match.re.groupindex else ''


def _carrier(record: re.Pattern, value: re.Pattern, text: str, kind_type: str) -> tuple[re.Match, re.Match] | None:
    """The first record of `kind_type` in `text` with a value under it, before the next record."""
    records = _records(record, text)
    for i, rec in enumerate(records):
        if _type(rec) != kind_type:
            continue
        limit = records[i + 1].start() if i + 1 < len(records) else len(text)
        val = next((m for m in value.finditer(text, rec.end()) if m.start() < limit), None)
        if val:
            return rec, val
    return None


def transplant(root: Path, fork: str, head: str, fmt: dict, recorded: list[tuple[str, str]],
               head_results: list[list[str]], scratch: Path) -> list[str]:
    record, value = re.compile(fmt['records']), re.compile(fmt['values'])
    old_kinds = {(_type(m), m['kind']) for _, text in recorded for m in _records(record, text)}
    counts: dict[str, list[str]] = {}
    for _, text in recorded:
        for m in value.finditer(text):
            counts.setdefault(shape(m['value']), []).append(m['value'])

    diff = git(root, 'diff', '--unified=0', '--no-ext-diff', fork, head).stdout
    added = '\n'.join(line[1:] for line in diff.splitlines() if line.startswith('+') and not line.startswith('+++'))
    base_text = ''
    for path in git(root, 'diff', '--name-only', '--no-renames', fork, head).stdout.splitlines():
        shown = git(root, 'show', f'{fork}:{path}', check=False)
        base_text += shown.stdout + '\n' if shown.returncode == 0 else ''
    old_kinds |= {(_type(m), m['kind']) for m in _records(record, base_text)}
    new_kinds = sorted({(_type(m), m['kind']) for m in _records(record, added)} - old_kinds)
    if not new_kinds:
        return ['Transplant: the change adds no kind of record that the recorded instances and the base lack.']
    new_values = sorted({m['value'] for m in value.finditer(added) if shape(m['value']) not in counts})
    candidates = [(f'`{s}` ({len(vs)} recorded)', vs[-1])
                  for s, vs in sorted(counts.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:VALUES_SHOWN]]
    candidates += [(f'`{v}` (new in this change)', v) for v in new_values]

    out = []
    for kind_type, kind in new_kinds:
        named = f"kind `{kind}`" + (f' of `{kind_type}` records' if kind_type else '')
        # Prefer the newest instance the head accepts as recorded, so the carrier adds no problems of its own.
        order = sorted(range(len(recorded)), key=lambda i: (not head_results[i], i), reverse=True)
        found = next(((i, c) for i in order if (c := _carrier(record, value, recorded[i][1], kind_type))), None)
        if found is None:
            out.append(f'Transplant, {named}: no recorded instance holds such a record with a value under it.')
            continue
        i, (rec, val) = found
        text = recorded[i][1]

        def variant(new_kind: str, new_value: str) -> str:
            return (text[:rec.start('kind')] + new_kind + text[rec.end('kind'):val.start('value')] + new_value
                    + text[val.end('value'):])

        texts = [variant(k, v) for k in (kind, rec['kind']) for _, v in candidates]
        try:
            results = check(root, fmt, root, texts, scratch)
        except RuntimeError as failed:
            out.append(f'Transplant, {named}: the check failed, so no values were run: {failed}')
            continue
        # A problem that names the value is the same problem for every value once the value is masked.
        masked = [{p.replace(v, '<value>') for p in problems} for (_, v), problems in zip(candidates * 2, results)]
        new, old = masked[:len(candidates)], masked[len(candidates):]
        new_common, old_common = set.intersection(*new), set.intersection(*old)
        out.append(f'Transplant, {named}, which the change adds. Each value ran through the head reader in '
                   f'{recorded[i][0]}, under the new kind and under the record\'s own kind `{rec["kind"]}`:')
        if new_common - old_common:
            out.append('Whatever the value, the new kind in this carrier also draws: '
                       + '; '.join(_clip(p) for p in sorted(new_common - old_common)))

        def verdict(found: set[str], common: set[str]) -> str:
            extra = sorted(found - common)
            return 'accepted' if not extra else 'refused: ' + _clip(extra[0])

        both = 0
        for (label, _), n, o in zip(candidates, new, old):
            under_new, under_old = verdict(n, new_common), verdict(o, old_common)
            both += under_new == under_old == 'accepted'
            out.append(f'- {label}: new kind {under_new}; own kind {under_old}')
        accepted_old = sum(verdict(o, old_common) == 'accepted' for o in old)
        out.append(f'The new kind accepts {both} of the {accepted_old} values the old kind accepts. Find what the '
                   'change says the new kind takes, and report each accepted value it says the kind must refuse.')
    return out


def report(root: Path, fork: str, head: str, config: dict, changed: list[str], before: str | None = None) -> list[str]:
    """The lines a grader holding the old-state lens reads, or none when the branch touches no declared reader."""
    out = []
    for fmt in touched(config.get('formats') or [], changed):
        if malformed(fmt):
            out.append(f'Note: {malformed(fmt)} in .claude/pr-grade.json, so it was not replayed.')
            continue
        name = fmt.get('name', fmt['readers'][0])
        out.append(f"### {name} (reader: {', '.join(fmt['readers'])})")
        recorded, notes = instances(root, fork, fmt, before)
        out += [f'Note: {note}' for note in notes]
        if not recorded:
            out.append('No recorded instance was read, so nothing was replayed.')
            continue
        texts = [text for _, text in recorded]
        label = 'pull request bodies' if all(w.startswith('PR #') for w, _ in recorded) else 'instances'
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            try:
                tree = base_tree(root, fork, fmt, scratch / 'base')
                base = check(root, fmt, tree, texts, scratch)
                head_results = check(root, fmt, root, texts, scratch)
            except (RuntimeError, subprocess.CalledProcessError, tarfile.TarError) as failed:
                detail = failed.stderr.decode(errors='replace') if isinstance(failed, subprocess.CalledProcessError) \
                    and isinstance(failed.stderr, bytes) else str(failed)
                out.append(f'The replay failed, so nothing was replayed: {_clip(detail)}')
                continue
            out += replay(recorded, base, head_results, label)
            if fmt.get('records') and fmt.get('values'):
                out += transplant(root, fork, head, fmt, recorded, head_results, scratch)
    return out


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
