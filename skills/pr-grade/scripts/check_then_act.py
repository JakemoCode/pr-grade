#!/usr/bin/env python3
"""Check-then-act candidates for lens L7: a check, then an await, a transaction boundary, or a second
read, then a write that relies on the check.

    python3 check_then_act.py [--base origin/main] [--whole] [--json] [paths ...]

Run it from inside the repository being graded. With no paths it scans the functions the branch
changed; `--whole` scans every function in the changed files; paths scan those files and directories
whole. The output is a list for a grader to judge, never a verdict: it exits 0 whenever the scan runs,
and an empty list does not clear L7.

TypeScript and JavaScript go through check_then_act_ts.cjs, which needs Node and the repository's own
`typescript` package (or PR_GRADE_TYPESCRIPT pointing at one). Without them those files are skipped
with a notice. Changed code in any other language is listed as skipped too, never passed over.

Name lists live under `checkThenAct` in .claude/pr-grade.json; a key you set replaces its default list.
A plain word matches a method named exactly that word, or starting with it and followed by `_` or a
capital letter (`set` matches `setStatus`, not `settle`). An entry with `*`, `?` or `[` is a glob; an
entry with a `.` is matched against the whole dotted callee.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from grade_mode import changed_lines, load_config, matches, repo_root  # noqa: E402

TS_ADAPTER = Path(__file__).resolve().parent / 'check_then_act_ts.cjs'
TS_SUFFIXES = ('.ts', '.tsx', '.mts', '.cts', '.js', '.jsx', '.mjs', '.cjs')
DEFAULTS = {
    'writes': ['append', 'insert', 'update', 'upsert', 'delete', 'remove', 'replace', 'save', 'put', 'set', 'write',
               'create', 'record', 'ensure', 'mark', 'submit', 'publish', 'persist', 'store', 'link', 'unlink', 'add',
               'enqueue', 'increment', 'decrement', 'claim', 'acquire', 'release', 'finalize', 'transition',
               'bulk_create', 'bulk_update'],
    'secondReads': ['fetch', 'urlopen', 'requests.*', 'httpx.*', 'aiohttp.*', 'urllib.request.*', 'subprocess.*',
                    'execSync', 'execFileSync', 'spawnSync'],
    'transactions': ['unitOfWork', 'unit_of_work', 'transaction', 'withTransaction', 'runInTransaction',
                     'run_in_transaction', '$transaction', 'atomic', 'begin', 'begin_nested', 'commit', 'rollback'],
    'locks': ['lock', '*_lock', '*Lock', 'mutex', '*Mutex', 'runExclusive', '*.locks.request'],
    'ignore': ['validate', 'parse', 'safeParse', 'hash', 'format', 'serialize', 'stringify', 'compute', 'derive',
               'setTimeout', 'setInterval', 'setImmediate', 'createHash', 'addEventListener', 'removeEventListener'],
}
# Receivers whose methods never touch shared state.
BUILTIN_RECEIVERS = {'Array', 'Object', 'JSON', 'Math', 'Number', 'String', 'Promise', 'Date', 'Reflect', 'console',
                     'path', 'Buffer', 'Symbol', 'os.path', 'json', 're', 'math', 'itertools', 'logging', 'logger'}
# Methods that mutate the collection they are called on: on this.x or self.x they are member writes.
MUTATORS = {'set', 'add', 'delete', 'push', 'clear', 'pop', 'splice', 'append', 'extend', 'update', 'remove',
            'discard', 'insert'}


def word_match(callee: str, entry: str) -> bool:
    method = callee.rsplit('.', 1)[-1]
    if any(ch in entry for ch in '*?['):
        return fnmatch.fnmatchcase(callee if '.' in entry else method, entry)
    if '.' in entry:
        return callee == entry
    if method == entry:
        return True
    return method.startswith(entry) and len(method) > len(entry) and (method[len(entry)] == '_'
                                                                       or method[len(entry)].isupper())


def any_match(callee: str, entries: list[str]) -> bool:
    return any(word_match(callee, entry) for entry in entries)


def cta_config(config: dict) -> dict:
    """`checkThenAct` over the defaults. A typo in a key would silently change what gets flagged, so an
    unknown key or a value that is not a list of strings stops the run."""
    given = config.get('checkThenAct') or {}
    if not isinstance(given, dict):
        sys.exit('checkThenAct in .claude/pr-grade.json must be an object')
    unknown = sorted(set(given) - set(DEFAULTS))
    if unknown:
        sys.exit(f"checkThenAct: unknown key {', '.join(unknown)}; the keys are {', '.join(DEFAULTS)}")
    for key, value in given.items():
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            sys.exit(f'checkThenAct.{key} must be a list of strings')
    return {**DEFAULTS, **given}


def _pos(point) -> tuple[int, int]:
    return (point[0], point[1])


def _span(span) -> tuple[tuple[int, int], tuple[int, int]]:
    return (_pos(span[0]), _pos(span[1]))


def _inside(inner, outer) -> bool:
    return outer[0] <= inner[0] and inner[1] <= outer[1]


def _receiver(callee: str) -> str:
    return callee.rsplit('.', 1)[0] if '.' in callee else ''


def analyze(fn: dict, cfg: dict) -> list[dict]:
    """The check-then-act candidates in one function's syntax facts."""
    calls = {call['id']: call for call in fn['calls']}
    collections = {name for a in fn['assigns'] if a['collection'] for name in a['names']}
    scope_calls = {scope.get('callId') for scope in fn['scopes']}

    kind: dict[int, str | None] = {}
    member_writes = [dict(w, span=_span(w['span'])) for w in fn['memberWrites']
                     if not w['inHandler'] and not fn['isConstructor']]
    for call in fn['calls']:
        callee, root = call['callee'], call['callee'].split('.', 1)[0]
        receiver = _receiver(callee)
        local = root in collections
        parts = callee.split('.')
        # this.cache.set(k, v) mutates the member, and this.store.append(x) may be a store call: count both.
        if (len(parts) == 3 and parts[0] in ('this', 'self') and parts[2] in MUTATORS
                and not call['inHandler'] and not fn['isConstructor']):
            member_writes.append({'path': f'{parts[0]}.{parts[1]}', 'span': _span(call['span']), 'line': call['line'],
                                  'inline': call['inline'], 'scopeIds': call['scopeIds']})
        if any_match(callee, cfg['ignore']):
            kind[call['id']] = None
        elif not call['inHandler'] and not fn['isConstructor'] and (
                (call['sql'] == 'write' and not call['chainedReceiver'])
                or (not local and any_match(callee, cfg['writes']))):
            kind[call['id']] = 'write'
        elif any_match(callee, cfg['transactions']):
            kind[call['id']] = 'transaction'
        elif call['sql'] == 'read' or call['awaited'] or (
                not call['bare'] and not local and receiver not in BUILTIN_RECEIVERS and root not in BUILTIN_RECEIVERS):
            kind[call['id']] = 'read'
        else:
            kind[call['id']] = None

    def read_origin(call_id):
        call = calls[call_id]
        return (_pos(call['span'][0]), call['line'], call['callee'])

    # Each local a read feeds, with the reads behind it, in the order assigned. A read's own arguments
    # carry no staleness forward: `store.get(id)` returns fresh state whatever `id` held.
    derived: dict[str, list[tuple[tuple[int, int], list]]] = {}

    def origins(refs, at, through_reads: bool) -> list:
        found = []
        for ref in refs:
            if ref['t'] == 'call' and kind.get(ref['id']) == 'read':
                found.append(read_origin(ref['id']))
            elif ref['t'] == 'name' and ref['name'] in derived:
                if not through_reads and any(kind.get(v) == 'read' for v in ref['via']):
                    continue
                earlier = [o for assigned, o in derived[ref['name']] if assigned < at]
                if earlier:
                    found.extend(earlier[-1])
        return found

    for assign in sorted(fn['assigns'], key=lambda a: _pos(a['span'][0])):
        at = _pos(assign['span'][0])
        found = origins(assign['refs'], at, through_reads=False)
        if found:
            for name in assign['names']:
                derived.setdefault(name, []).append((at, found))

    checks = []
    for cond in fn['conds']:
        if cond['inHandler']:
            continue
        span, cond_span = _span(cond['span']), _span(cond['condSpan'])
        reads = sorted(set(origins(cond['refs'], span[0], through_reads=True)))
        members = {ref['path'] for ref in cond['refs'] if ref['t'] == 'member'}
        if reads:
            checks.append({'cond': cond, 'span': span, 'condSpan': cond_span, 'start': reads[0][0], 'kind': 'call',
                           'reads': reads})
        elif members:
            checks.append({'cond': cond, 'span': span, 'condSpan': cond_span, 'start': span[0], 'kind': 'member',
                           'paths': members, 'reads': []})

    writes = [{'kind': 'call', 'span': _span(call['span']), 'line': call['line'], 'callee': call['callee'],
               'scopeIds': call['scopeIds']} for call in fn['calls'] if kind[call['id']] == 'write']
    writes += [{'kind': 'member', 'span': w['span'], 'line': w['line'], 'callee': w['path'], 'path': w['path'],
                'scopeIds': w['scopeIds']} for w in member_writes]

    locked = bool(fn.get('inlineCallee')) and any_match(fn['inlineCallee'], cfg['locks'])
    # A function passed to a transaction runner already runs inside that transaction.
    in_transaction = bool(fn.get('inlineCallee')) and any_match(fn['inlineCallee'], cfg['transactions'])
    transaction_scopes = [dict(scope, span=_span(scope['span'])) for scope in fn['scopes']
                          if any_match(scope['callee'], cfg['transactions'])]

    def innermost_transaction(point):
        holding = [scope for scope in transaction_scopes if scope['span'][0] <= point <= scope['span'][1]]
        return min(holding, key=lambda scope: (scope['span'][1][0] - scope['span'][0][0], scope['span'][1][1]),
                   default=None)

    def covers(check, write) -> bool:
        if write['span'][0] <= check['condSpan'][1]:
            return False
        if not check['cond']['guard']:
            return _inside(write['span'], check['span'])
        # A guard that exits by break or continue covers only the rest of its loop or switch.
        end = check['cond'].get('guardEnd')
        return end is None or write['span'][0] < _pos(end)

    def gaps_between(check, write) -> list[dict]:
        start, (w_start, w_end) = check['start'], write['span']
        found = []

        def keep(span, scope_ids, reach_end=None) -> bool:
            # Something inside a callback is between the check and the write only when the write is
            # inside that same callback.
            reaches = list(scope_ids) == list(write['scopeIds'][:len(scope_ids)])
            return (reaches and start <= span[0] < w_end and not _inside(write['span'], span)
                    and not _inside(span, check['condSpan'])
                    and not (reach_end is not None and w_start > _pos(reach_end)))

        if not locked:
            for waited in fn['awaits']:
                span = _span(waited['span'])
                if keep(span, waited.get('scopeIds', []), waited['reachEnd']):
                    found.append({'pos': span[0], 'line': waited['line'], 'kind': 'await', 'text': waited['text']})
        for call in fn['calls']:
            span = _span(call['span'])
            if not keep(span, call['scopeIds']):
                continue
            if any_match(call['callee'], cfg['secondReads']):
                found.append({'pos': span[0], 'line': call['line'], 'kind': 'second-read', 'text': call['text']})
            elif kind[call['id']] == 'transaction' and call['id'] not in scope_calls:
                found.append({'pos': span[0], 'line': call['line'], 'kind': 'transaction', 'text': call['text']})
        in_check, in_write = innermost_transaction(start), innermost_transaction(w_start)
        # A transaction nested inside the check's own is a savepoint: nothing lands between them.
        nested = in_write is not None and (in_transaction if in_check is None
                                           else _inside(in_write['span'], in_check['span']))
        if not nested and in_check is not in_write and (in_write is None
                                                         or not _inside((start, start), in_write['span'])):
            point = in_write['span'][0] if in_write else in_check['span'][1]
            line = in_write['line'] if in_write else point[0]
            if start <= point < w_start:
                found.append({'pos': point, 'line': line, 'kind': 'transaction',
                              'text': f"{(in_write or in_check)['callee']}(...) boundary"})
        return found

    def stores(check) -> set[str]:
        return {_receiver(callee) for _, _, callee in check['reads']} - {''}

    def revalidated(check, write, gaps) -> bool:
        """A later check on the write's path, reading the same store (or member) again after the last gap.
        A recheck of something else says nothing about the state the first check read, and a recheck
        through the object's own method cannot be told apart from one that reads an unrelated flag."""
        last = max(gap['pos'] for gap in gaps)
        for other in checks:
            if other is check or not (last < other['span'][0] < write['span'][0] and other['start'] > last
                                      and covers(other, write)):
                continue
            if check['kind'] == 'member':
                if other['kind'] == 'member' and other['paths'] & check['paths']:
                    return True
            elif stores(other) & stores(check):
                return True
        return False

    primary = [check for check in checks if not check['cond']['inline']]
    grouped: dict[tuple, dict] = {}
    for write in sorted(writes, key=lambda w: w['span'][0]):
        surviving, gap_set = [], {}
        for check in primary:
            if check['kind'] == 'member' and (write['kind'] != 'member' or write['path'] not in check['paths']):
                continue
            if check['kind'] == 'call' and write['kind'] != 'call':
                continue
            if not (check['start'] < write['span'][0] and covers(check, write)):
                continue
            gaps = gaps_between(check, write)
            if not gaps or revalidated(check, write, gaps):
                continue
            surviving.append(check)
            for gap in gaps:
                gap_set[(gap['line'], gap['kind'])] = gap
        if not surviving:
            continue
        key = (tuple(check['cond']['line'] for check in surviving), tuple(sorted(gap_set)))
        entry = grouped.setdefault(key, {'checks': surviving, 'gaps': list(gap_set.values()), 'writes': []})
        entry['writes'].append(write)

    candidates = []
    for entry in grouped.values():
        writes_out = [{'line': w['line'], 'callee': w['callee']} for w in entry['writes']]
        checks_out = []
        for check in entry['checks']:
            store = _receiver(check['reads'][0][2]) if check['reads'] else None
            checks_out.append({
                'line': check['cond']['line'], 'text': check['cond']['text'],
                'reads': [{'line': line, 'callee': callee} for _, line, callee in check['reads']],
                'sameStoreWrites': [w['line'] for w in entry['writes'] if store and _receiver(w['callee']) == store],
            })
        candidates.append({
            'function': fn['name'], 'span': [fn['span'][0][0], fn['span'][1][0]],
            'checks': checks_out,
            'gaps': [{'line': g['line'], 'kind': g['kind'], 'text': g['text']}
                     for g in sorted(entry['gaps'], key=lambda g: g['pos'])],
            'writes': writes_out,
        })
    return candidates


def _in_ranges(line: int, ranges) -> bool:
    return ranges is None or any(start <= line <= end for start, end in ranges)


def in_diff(candidate: dict, ranges) -> list[str]:
    roles = (('check', [c['line'] for c in candidate['checks']]), ('gap', [g['line'] for g in candidate['gaps']]),
             ('write', [w['line'] for w in candidate['writes']]))
    return [role for role, lines in roles if any(_in_ranges(line, ranges) for line in lines)]


def parse_typescript(files: list[str], root: Path) -> tuple[dict, list[dict], str | None]:
    """Each file's functions, the files that could not be read, and the compiler used."""
    if not files:
        return {}, [], None
    node = shutil.which('node')
    if node is None:
        return {}, [{'file': f, 'reason': 'node is not on PATH'} for f in files], None
    run = subprocess.run([node, str(TS_ADAPTER)], input=json.dumps({'root': str(root), 'files': files}), cwd=root,
                         capture_output=True, text=True)
    if run.returncode == 3:
        return {}, [{'file': f, 'reason': run.stderr.strip()} for f in files], None
    if run.returncode != 0:
        sys.exit(f'check_then_act: the TypeScript adapter failed: {run.stderr.strip()}')
    data = json.loads(run.stdout)
    parsed, skipped = {}, []
    for entry in data['files']:
        if 'error' in entry:
            skipped.append({'file': entry['path'], 'reason': entry['error']})
        else:
            parsed[entry['path']] = entry['functions']
    return parsed, skipped, f"{data['typescript']['version']} ({data['typescript']['path']})"


def supported(path: str) -> bool:
    return path.endswith(TS_SUFFIXES) and not path.endswith('.d.ts')


UNSUPPORTED = 'no check-then-act adapter for this language yet; grade its L7 windows by hand'
# Source in a language the scanner could one day read. Data and config files are never reported skipped.
OTHER_LANGUAGES = ('.py', '.go', '.rb', '.java', '.kt', '.kts', '.rs', '.cs', '.php', '.swift', '.scala', '.ex',
                   '.exs', '.erl', '.c', '.cc', '.cpp', '.h', '.hpp', '.m', '.dart', '.lua', '.clj', '.hs', '.ml')


def unscannable(paths: list[str]) -> list[dict]:
    return [{'file': path, 'reason': UNSUPPORTED} for path in paths if path.endswith(OTHER_LANGUAGES)]


def is_code(path: str, config: dict) -> bool:
    return not matches(path, config['tests']) and not matches(path, config['notCode'])


def expand(paths: list[str], root: Path, config: dict) -> tuple[list[str], list[dict]]:
    """Named files as given; directories through git, so ignored build output stays out, without tests.
    Code in a language with no adapter is returned as skipped, so it never passes for a clean scan."""
    found = []
    for given in paths:
        target = Path(given).resolve()
        if not target.exists():
            sys.exit(f'check_then_act: {given}: no such file or directory')
        try:
            relative = target.relative_to(root).as_posix() if target != root else '.'
        except ValueError:
            sys.exit(f'check_then_act: {given} is outside the repository at {root}')
        if target.is_file():
            found.append(relative)
            continue
        listed = subprocess.run(['git', '-c', 'core.quotePath=false', 'ls-files', '--cached', '--others',
                                 '--exclude-standard', '--', relative],
                                cwd=root, capture_output=True, text=True, check=True).stdout.splitlines()
        found += [p for p in listed if is_code(p, config)]
    found = list(dict.fromkeys(found))
    return [p for p in found if supported(p)], unscannable(found)


def scan(root: Path, base: str, whole: bool, paths: list[str]) -> dict:
    config = load_config(root)
    cfg = cta_config(config)
    if paths:
        files, unsupported = expand(paths, root, config)
        scope, lines = 'paths', {}
        ranges: dict = {f: None for f in files}
    else:
        scope = 'whole' if whole else 'diff'
        try:
            # One snapshot: the file list and its line ranges come from the same diff.
            lines = changed_lines(base, root)
        except subprocess.CalledProcessError as failed:
            sys.exit(f'check_then_act: cannot diff against {base}: {(failed.stderr or "").strip()}. '
                     'Pass --base <ref> naming the branch this one will merge into.')
        code = [f for f in lines if (root / f).is_file() and is_code(f, config)]
        files = [f for f in code if supported(f)]
        unsupported = unscannable(code)
        ranges = {f: None if whole else lines[f] for f in files}
    parsed, skipped, typescript = parse_typescript(files, root)
    skipped = unsupported + skipped
    candidates, functions = [], 0
    for file in files:
        for fn in parsed.get(file, []):
            first, last = fn['span'][0][0], fn['span'][1][0]
            file_ranges = ranges.get(file)
            if file_ranges is not None and not any(start <= last and first <= end for start, end in file_ranges):
                continue
            functions += 1
            for candidate in analyze(fn, cfg):
                candidates.append({'file': file, 'language': 'typescript', **candidate,
                                   'inDiff': in_diff(candidate, lines.get(file)) if scope != 'paths' else []})
    return {'version': 1, 'scope': scope, 'base': None if paths else base, 'typescript': typescript,
            'scanned': {'files': len(parsed), 'functions': functions},
            'candidates': candidates, 'skipped': skipped}


def render(result: dict) -> str:
    scanned = result['scanned']['functions']
    where = 'functions the branch touched' if result['scope'] == 'diff' else 'functions'
    skipped = f" {len(result['skipped'])} files were skipped and not scanned." if result['skipped'] else ''
    if not result['candidates']:
        return f'check-then-act candidates (L7): none in {scanned} {where}.{skipped} An empty list does not clear L7.'
    lines = [f"check-then-act candidates (L7): {len(result['candidates'])} in {scanned} {where}.{skipped} "
             'Each is a window to judge, not a finding.']
    for c in result['candidates']:
        lines += ['', f"{c['file']}:{c['span'][0]}  {c['function']}"]
        for check in c['checks']:
            reads = ', '.join(f"{r['callee']} ({r['line']})" for r in check['reads'])
            same = f", same store as write {', '.join(map(str, check['sameStoreWrites']))}" if check['sameStoreWrites'] else ''
            lines.append(f"  check  {check['line']:<4} {check['text']}" + (f'    reads {reads}{same}' if reads else ''))
        for gap in c['gaps']:
            lines.append(f"  gap    {gap['line']:<4} {gap['kind']}  {gap['text']}")
        for write in c['writes']:
            lines.append(f"  write  {write['line']:<4} {write['callee']}")
    return '\n'.join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--base', default='origin/main')
    ap.add_argument('--whole', action='store_true', help='scan every function in the changed files')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('paths', nargs='*', help='files or directories to scan whole instead of the diff')
    args = ap.parse_args()
    result = scan(repo_root(), args.base, args.whole, args.paths)
    reasons: dict[str, list[str]] = {}
    for skip in result['skipped']:
        reasons.setdefault(skip['reason'], []).append(skip['file'])
    for reason, files in reasons.items():
        named = files[0] if len(files) == 1 else f'{len(files)} files'
        print(f'check_then_act: skipped {named}: {reason}', file=sys.stderr)
    print(json.dumps(result, indent=1) if args.json else render(result))


if __name__ == '__main__':
    main()
