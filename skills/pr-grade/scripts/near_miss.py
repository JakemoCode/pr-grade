#!/usr/bin/env python3
"""Near misses and second readers: the inputs at the edge of each check a change makes, and the other code
that reads the same format.

    python3 near_miss.py [--base origin/main]

Run it from inside the repository, at the committed head. It prints two lists for the grader holding L9,
each a floor and never a ceiling:

- near misses: every predicate the change adds or modifies, in Python, JavaScript, or TypeScript, run
  against near misses of a string it accepts, one row per input, accepted or refused. A predicate is a
  regex literal (`re.compile`, `re.search` and the rest of `re`'s functions, a `/.../` literal, `new
  RegExp`), a `startswith` or `endswith` test, and an `in`, `includes`, or `==` test whose literal looks
  like a marker: it starts with markup such as `#`, `*`, `-`, or `[`, ends in `:`, or holds whitespace.
  A string predicate's seed is its own literal. A regex's seed is the first string literal it matches in
  its own file, then in the tests the change touches; with none, the row says "no seed found" and the
  grader supplies one. The near misses come from `near_misses()`: the seed with a prefix, a suffix, in
  other casing, with surrounding whitespace, with CRLF, with a Unicode lookalike, empty, and embedded in
  a longer word. After them come the other literals of those files that the predicate accepts, since a
  heading the same module writes is the nearest miss there is. A `.strip()` or `.lower()` on the input
  in the same expression is applied first. Python's `re` runs a Python regex and `node` a JavaScript one,
  each in a child process with a timeout; a regex found and not run gets a row that says why.
- second readers: every parse anchor the change touches (the string a predicate tests, the longest
  literal run of a regex, the separator of a `split`, `partition`, or `find`), with every other file at
  the head whose code holds the same text, found with `git grep -F` and with documentation dropped as
  the callers search drops it. Two readers of one format drift.

Nothing here stops a grade: a scan or a run that fails is printed as a note.
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import warnings
from pathlib import Path

try:
    import resource
except ImportError:  # pragma: no cover - not on a Unix
    resource = None

HERE = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f'_pr_grade_{name}', HERE / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


grade_mode = _load('grade_mode')
ambient = _load('ambient')

MAX_PREDICATES = 20
MAX_ROWS = 16
MAX_ANCHORS = 20
MAX_READERS = 10
MAX_POOL = 5000
# Seconds per child process. A regex that backtracks without end on one input costs its predicate, never the prep.
RUN_TIMEOUT = 10
MIN_ANCHOR = 4
TEXT_CHARS = 120
OUTPUT_CHARS = 400

# Cyrillic letters drawn like Latin ones. A seed with none of these letters gets a fullwidth form instead.
HOMOGLYPHS = {'a': '\u0430', 'c': '\u0441', 'e': '\u0435', 'i': '\u0456', 'o': '\u043e', 'p': '\u0440',
              'x': '\u0445', 'y': '\u0443', 'A': '\u0410', 'B': '\u0412', 'C': '\u0421', 'E': '\u0415',
              'H': '\u041d', 'K': '\u041a', 'M': '\u041c', 'O': '\u041e', 'P': '\u0420', 'T': '\u0422',
              'X': '\u0425'}
RE_FUNCS = {'compile': 'search', 'search': 'search', 'match': 'match', 'fullmatch': 'fullmatch',
            'findall': 'search', 'finditer': 'search', 'sub': 'search', 'subn': 'search', 'split': 'search'}
RE_INPUT = {'search': 1, 'match': 1, 'fullmatch': 1, 'findall': 1, 'finditer': 1, 'split': 1, 'sub': 2, 'subn': 2}
RE_FLAGS = {'compile': 1, 'search': 2, 'match': 2, 'fullmatch': 2, 'findall': 2, 'finditer': 2, 'split': 3,
            'sub': 4, 'subn': 4}
FLAG_NAMES = ('IGNORECASE', 'MULTILINE', 'DOTALL', 'VERBOSE', 'ASCII')
PY_TRANSFORMS = {'strip', 'lstrip', 'rstrip', 'lower', 'upper', 'casefold'}
JS_TRANSFORMS = {'trim': 'strip', 'trimStart': 'lstrip', 'trimLeft': 'lstrip', 'trimEnd': 'rstrip',
                 'trimRight': 'rstrip', 'toLowerCase': 'lower', 'toLocaleLowerCase': 'lower',
                 'toUpperCase': 'upper', 'toLocaleUpperCase': 'upper'}
PY_SPLITTERS = {'split', 'rsplit', 'partition', 'rpartition', 'find', 'rfind', 'index', 'rindex',
                'removeprefix', 'removesuffix'}
MARKUP = '#*>-|[<=~`+'
# The vendored directories grade_prep.py's callers search leaves out.
VENDORED = (':(exclude)vendor/*', ':(exclude)third_party/*', ':(exclude)node_modules/*')

_QUOTED = r'''("(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\$])*`)'''
_JS_CHAIN = r'((?:\.(?:' + '|'.join(JS_TRANSFORMS) + r')\(\s*\))*)'
JS_METHOD = re.compile(_JS_CHAIN + r'\.(startsWith|endsWith|includes)\(\s*' + _QUOTED + r'\s*[,)]')
JS_EQUALS = re.compile(_JS_CHAIN + r'\s*([!=]==?)\s*' + _QUOTED + r'|' + _QUOTED + r'\s*([!=]==?)')
JS_REGEX = re.compile(r'(?:^|(?<=[=(,:\[!&|?{};])|(?<==>)|(?<=\breturn))[ \t]*'
                      r'/((?![*/])(?:\\.|\[(?:\\.|[^\]\\\n])*\]|[^/\\\n\[])+)/([dgimsuyv]*)', re.M)
JS_NEW_REGEXP = re.compile(r'\bnew\s+RegExp\(\s*' + _QUOTED + r'(?:\s*,\s*' + _QUOTED + r')?\s*\)')
JS_SPLITTER = re.compile(r'\.(split|indexOf|lastIndexOf)\(\s*' + _QUOTED + r'\s*[,)]')
JS_ESCAPE = re.compile(r'\\(u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|\n|.)', re.S)

PY_RUNNER = r'''
import json, re, sys
job = json.load(sys.stdin)
try:
    rx = re.compile(job['pattern'], job['flags'])
except re.error as failed:
    print(json.dumps({'error': str(failed)}))
    sys.exit()
run = getattr(rx, job['method'])
print(json.dumps({'results': [run(s) is not None for s in job['inputs']]}))
'''
# The g and y flags make test() stateful across calls, so each input would start where the last one stopped.
JS_RUNNER = r'''
const job = JSON.parse(require('fs').readFileSync(0, 'utf8'));
let rx;
try { rx = new RegExp(job.pattern, job.flags.replace(/[gy]/g, '')); }
catch (failed) { console.log(JSON.stringify({error: String(failed.message)})); process.exit(0); }
console.log(JSON.stringify({results: job.inputs.map((s) => rx.test(s))}));
'''


# ---- the generator, which issue #43 reuses


def _lookalike(seed: str) -> str | None:
    for i, char in enumerate(seed):
        if char in HOMOGLYPHS:
            return seed[:i] + HOMOGLYPHS[char] + seed[i + 1:]
    for i, char in enumerate(seed):
        if '!' <= char <= '~':
            return seed[:i] + chr(ord(char) + 0xFEE0) + seed[i + 1:]
    return None


def near_misses(seed: str) -> list[tuple[str, str]]:
    """Labelled near misses of `seed`, the seed itself first. Two labels may give one text, as a lower-case
    seed in lower case does; the caller dedupes when it needs to."""
    out = [('as given', seed), ('with a prefix', f'x {seed}'), ('with a suffix', f'{seed} x')]
    cased = [i for i, char in enumerate(seed) if char.upper() != char.lower()]
    if cased:
        first = cased[0]
        out += [('in upper case', seed.upper()), ('in lower case', seed.lower()),
                ('with its first letter in the other case', seed[:first] + seed[first].swapcase() + seed[first + 1:])]
    out.append(('with surrounding whitespace', f' {seed} '))
    out.append(('with CRLF line endings',
                seed.replace('\r\n', '\n').replace('\n', '\r\n') if '\n' in seed else seed + '\r\n'))
    lookalike = _lookalike(seed)
    if lookalike is not None:
        out.append(('with a Unicode lookalike', lookalike))
    out += [('empty', ''), ('embedded in a longer word', f'x{seed}x')]
    return out


# ---- finding predicates


def _is_marker(text: str) -> bool:
    core = text.strip()
    return bool(core) and (core[0] in MARKUP or core.endswith(':') or any(char.isspace() for char in core))


def _overlaps(first: int, last: int, ranges) -> bool:
    return ranges is None or any(start <= last and first <= end for start, end in ranges)


def _str(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _chain(node) -> list[tuple[str, tuple]]:
    """The str methods the expression applies to its input, innermost first, up to the first other call."""
    steps = []
    while isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and not node.keywords:
        name, args = node.func.attr, [_str(arg) for arg in node.args]
        if name not in PY_TRANSFORMS or None in args or (args and name not in ('strip', 'lstrip', 'rstrip')):
            break
        steps.append((name, tuple(args)))
        node = node.func.value
    return list(reversed(steps))


def _flags(node) -> int | None:
    if node is None:
        return 0
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == 're':
        value = getattr(re, node.attr, None)
        return int(value) if isinstance(value, int) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left, right = _flags(node.left), _flags(node.right)
        return None if left is None or right is None else left | right
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    return None


def _arg(node: ast.Call, position: int, keyword: str):
    if len(node.args) > position:
        return node.args[position]
    return next((kw.value for kw in node.keywords if kw.arg == keyword), None)


def _fold(node, names: dict[str, str]) -> str | None:
    """A string literal, a module-level name bound to one, or a `+` of those; None for anything else."""
    if _str(node) is not None:
        return node.value
    if isinstance(node, ast.Name):
        return names.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _fold(node.left, names), _fold(node.right, names)
        return None if left is None or right is None else left + right
    return None


def _module_strings(tree: ast.Module) -> dict[str, str]:
    names: dict[str, str] = {}
    for statement in tree.body:
        targets = (statement.targets if isinstance(statement, ast.Assign)
                   else [statement.target] if isinstance(statement, ast.AnnAssign) and statement.value else [])
        if len(targets) == 1 and isinstance(targets[0], ast.Name):
            value = _fold(statement.value, names)
            if value is not None:
                names[targets[0].id] = value
            else:
                # A rebinding to something else makes the earlier value wrong for later reads.
                names.pop(targets[0].id, None)
    return names


def _python_regex(path: str, node: ast.Call, func: str, names: dict[str, str]) -> dict:
    found = {'file': path, 'line': node.lineno, 'col': node.col_offset, 'kind': 'regex', 'lang': 'python',
             'method': RE_FUNCS[func]}
    pattern = _arg(node, 0, 'pattern')
    flags_node = _arg(node, RE_FLAGS[func], 'flags')
    flags = _flags(flags_node)
    found['flags'] = flags or 0
    target = _arg(node, RE_INPUT[func], 'string') if func in RE_INPUT else None
    found['transforms'] = _chain(target) if target is not None else []
    if isinstance(pattern, ast.Constant) and isinstance(pattern.value, bytes):
        found.update(pattern=ascii(pattern.value), why='a bytes pattern, which the table does not run')
    elif _fold(pattern, names) is None:
        shown = ast.unparse(pattern)[:TEXT_CHARS] if pattern is not None and hasattr(ast, 'unparse') else 'a runtime value'
        found.update(pattern=shown, why='the pattern is built at runtime')
    else:
        found['pattern'] = _fold(pattern, names)
        if flags is None:
            found['why'] = f'its flags `{ast.unparse(flags_node)[:TEXT_CHARS]}` are not literal re flags'
    return found


def python_scan(path: str, text: str, ranges) -> tuple[list[dict], list[dict], list[tuple[str, int]]]:
    """Predicates and splitter anchors on the changed lines of a Python file, and every string literal in it."""
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        tree = ast.parse(text)
    names = _module_strings(tree)
    predicates, anchors, literals = [], [], []
    for node in ast.walk(tree):
        if _str(node) is not None:
            literals.append((node.lineno, node.col_offset, node.value))
        if not isinstance(node, (ast.Call, ast.Compare)) or not _overlaps(node.lineno, node.end_lineno or node.lineno, ranges):
            continue
        base = {'file': path, 'line': node.lineno, 'col': node.col_offset, 'lang': 'python'}
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            func = node.func
            if isinstance(func.value, ast.Name) and func.value.id == 're' and func.attr in RE_FUNCS:
                predicates.append(_python_regex(path, node, func.attr, names))
            elif func.attr in ('startswith', 'endswith') and node.args:
                first = node.args[0]
                items = [_fold(e, names) for e in first.elts] if isinstance(first, ast.Tuple) else [_fold(first, names)]
                if items and None not in items:
                    predicates.append({**base, 'kind': func.attr, 'literals': items, 'transforms': _chain(func.value)})
            elif func.attr in PY_SPLITTERS and node.args and _str(node.args[0]) is not None:
                anchors.append({**base, 'kind': f'.{func.attr}', 'term': node.args[0].value})
        elif isinstance(node, ast.Compare) and len(node.ops) == 1:
            op, left, right = node.ops[0], node.left, node.comparators[0]
            if (isinstance(op, (ast.In, ast.NotIn)) and _str(left) is not None and _is_marker(left.value)
                    and not isinstance(right, (ast.Tuple, ast.List, ast.Set, ast.Dict, ast.Constant))):
                predicates.append({**base, 'kind': 'contains', 'literals': [left.value], 'transforms': _chain(right),
                                   'negated': isinstance(op, ast.NotIn)})
            elif isinstance(op, (ast.Eq, ast.NotEq)):
                literal, other = (right, left) if _str(right) is not None else (left, right)
                if _str(literal) is not None and _is_marker(literal.value) and _str(other) is None:
                    predicates.append({**base, 'kind': 'equals', 'literals': [literal.value], 'transforms': _chain(other),
                                       'negated': isinstance(op, ast.NotEq)})
    # ast.walk goes breadth first; a regex is seeded with the first literal in reading order.
    return predicates, anchors, [(value, number) for number, _, value in sorted(literals)]


def _js_unescape(body: str) -> str:
    def one(match: re.Match) -> str:
        code = match.group(1)
        if code.startswith('u{'):
            return chr(int(code[2:-1], 16))
        if code[0] in 'ux' and len(code) > 1:
            return chr(int(code[1:], 16))
        return {'n': '\n', 'r': '\r', 't': '\t', 'b': '\b', 'f': '\f', 'v': '\v', '0': '\0', '\n': ''}.get(code, code)
    return JS_ESCAPE.sub(one, body)


def _js_literal(quoted: str) -> str:
    return _js_unescape(quoted[1:-1])


def js_scan(path: str, text: str, ranges) -> tuple[list[dict], list[dict], list[tuple[str, int]]]:
    """The same for a JavaScript or TypeScript file, read with patterns rather than a parser."""
    def line(at: int) -> int:
        return text.count('\n', 0, at) + 1

    predicates, anchors = [], []
    literals = [(_js_unescape(ambient._body(m)), line(m.start())) for m in ambient.STRING.finditer(text)
                if not (m.group(0).startswith('`') and '${' in m.group(0))]

    def add(at: int, found: dict) -> None:
        number = line(at)
        if _overlaps(number, number, ranges):
            predicates.append({'file': path, 'line': number, 'col': at, 'lang': 'js', **found})

    def transforms(chain: str) -> list[tuple[str, tuple]]:
        return [(JS_TRANSFORMS[name], ()) for name in re.findall(r'\.(\w+)\(', chain or '')]

    for m in JS_REGEX.finditer(text):
        add(m.start(1), {'kind': 'regex', 'pattern': m.group(1), 'flags': m.group(2), 'transforms': []})
    for m in JS_NEW_REGEXP.finditer(text):
        add(m.start(), {'kind': 'regex', 'pattern': _js_literal(m.group(1)),
                        'flags': _js_literal(m.group(2)) if m.group(2) else '', 'transforms': []})
    for m in JS_METHOD.finditer(text):
        literal = _js_literal(m.group(3))
        kind = {'startsWith': 'startswith', 'endsWith': 'endswith', 'includes': 'contains'}[m.group(2)]
        if kind != 'contains' or _is_marker(literal):
            add(m.start(), {'kind': kind, 'literals': [literal], 'transforms': transforms(m.group(1))})
    for m in JS_EQUALS.finditer(text):
        quoted, operator = (m.group(3), m.group(2)) if m.group(3) else (m.group(4), m.group(5))
        literal = _js_literal(quoted)
        if _is_marker(literal):
            add(m.start(), {'kind': 'equals', 'literals': [literal], 'transforms': transforms(m.group(1)),
                            'negated': operator.startswith('!')})
    for m in JS_SPLITTER.finditer(text):
        number = line(m.start())
        if _overlaps(number, number, ranges):
            anchors.append({'file': path, 'line': number, 'col': m.start(), 'lang': 'js', 'kind': f'.{m.group(1)}',
                            'term': _js_literal(m.group(2))})
    return predicates, anchors, literals


def _read(root: Path, path: str) -> str:
    return (root / path).read_text(encoding='utf-8', errors='replace')


def _scanner(path: str):
    if path.endswith('.py'):
        return python_scan
    return js_scan if ambient._is_js(path) else None


def scan(root: Path, lines: dict, config: dict) -> tuple[list[dict], list[dict], list[str], dict[str, list]]:
    """Predicates and anchors on the changed lines of every changed code file, a note for each code file not
    scanned, and each scanned file's string literals."""
    predicates, anchors, notes, literals = [], [], [], {}
    for path, ranges in sorted(lines.items()):
        if not (root / path).is_file() or not grade_mode.code_files([path], config):
            continue
        scanner = _scanner(path)
        if scanner is None:
            notes.append(f'{path}: no scanner reads this language')
            continue
        try:
            found, anchored, literals[path] = scanner(path, _read(root, path), ranges)
        except (OSError, SyntaxError, ValueError) as failed:
            notes.append(f'{path}: {type(failed).__name__}: {str(failed)[:OUTPUT_CHARS]}')
            continue
        predicates += sorted(found, key=lambda p: (p['line'], p['col']))
        anchors += anchored
    return predicates, anchors, notes, literals


# ---- running them


def _limit_cpu() -> None:
    # A child the prep can no longer kill, after a SIGKILL, still stops once it has spent its CPU budget.
    if resource is not None:
        resource.setrlimit(resource.RLIMIT_CPU, (RUN_TIMEOUT + 1, RUN_TIMEOUT + 1))


def _evaluate(predicate: dict, inputs: list[str]) -> tuple[list[bool] | None, str | None]:
    """Whether the predicate's regex finds each input, run in a child process, or why it could not run."""
    if predicate['lang'] == 'js':
        node = shutil.which('node')
        if node is None:
            return None, 'node is not on PATH'
        argv = [node, '-e', JS_RUNNER]
    else:
        argv = [sys.executable, '-I', '-c', PY_RUNNER]
    job = {'pattern': predicate['pattern'], 'flags': predicate['flags'], 'method': predicate.get('method', 'search'),
           'inputs': inputs}
    try:
        with ambient._terminating_raises():
            run = subprocess.run(argv, input=json.dumps(job), capture_output=True, text=True, timeout=RUN_TIMEOUT,
                                 preexec_fn=_limit_cpu if resource is not None else None)
    except subprocess.TimeoutExpired:
        return None, f'timed out after {RUN_TIMEOUT}s, so the pattern may backtrack without end on one of its inputs'
    except OSError as failed:
        return None, failed.strerror or str(failed)
    try:
        result = json.loads(run.stdout)
    except ValueError:
        return None, f"the runner exited {run.returncode}: {' '.join(run.stderr.split())[-OUTPUT_CHARS:]}"
    if 'error' in result:
        return None, result['error']
    return result['results'], None


def _apply(transforms: list[tuple[str, tuple]], text: str) -> str:
    for name, args in transforms:
        text = getattr(text, name)(*args)
    return text


def _string_test(predicate: dict, text: str) -> bool:
    text = _apply(predicate['transforms'], text)
    items = predicate['literals']
    if predicate['kind'] == 'startswith':
        return text.startswith(tuple(items))
    if predicate['kind'] == 'endswith':
        return text.endswith(tuple(items))
    if predicate['kind'] == 'contains':
        return items[0] in text
    return text == items[0]


def _run(predicate: dict, inputs: list[str]) -> tuple[list[bool] | None, str | None]:
    if predicate['kind'] != 'regex':
        return [_string_test(predicate, text) for text in inputs], None
    return _evaluate(predicate, [_apply(predicate['transforms'], text) for text in inputs])


def _show(text: str) -> str:
    shown = ascii(text)
    return shown if len(shown) <= TEXT_CHARS else shown[:TEXT_CHARS] + "...'"


def _describe(predicate: dict) -> str:
    where = f"{predicate['file']}:{predicate['line']}"
    if predicate['kind'] == 'regex':
        if predicate['lang'] == 'js':
            what = f"regex `/{predicate['pattern']}/{predicate['flags']}`, run with RegExp.test under node"
        else:
            names = [name for name in FLAG_NAMES if predicate['flags'] & getattr(re, name)]
            what = (f"regex `{predicate['pattern']}`" + (f" ({', '.join(names)})" if names else '')
                    + f", run with re.{predicate['method']}")
    else:
        items = predicate['literals']
        what = f"{predicate['kind']} {_show(items[0]) if len(items) == 1 else '(' + ', '.join(map(_show, items)) + ')'}"
    if predicate['transforms']:
        what += ' on the input after ' + ''.join(f".{name}({', '.join(map(_show, args))})"
                                                 for name, args in predicate['transforms'])
    if predicate.get('negated'):
        what += ', and the code branches on its negation'
    return f'{where} {what}'


def _pool(path: str, literals: dict, tests: list[str]) -> list[tuple[str, str]]:
    """Distinct string literals, the predicate's own file first, then the tests, each with where it is."""
    seen, out = set(), []
    for source in [path, *[t for t in tests if t != path]]:
        for value, number in literals.get(source, []):
            if value not in seen:
                seen.add(value)
                out.append((value, f'{source}:{number}'))
    return out[:MAX_POOL]


def _rows(predicate: dict, pool: list[tuple[str, str]]) -> list[str]:
    """The predicate's line and its rows."""
    head = _describe(predicate)
    if predicate.get('why'):
        return [f'- {head}: found, not run: {predicate["why"]}']
    if predicate['kind'] == 'regex':
        found, why = _run(predicate, [value for value, _ in pool])
        if why:
            return [f'- {head}: found, not run: {why}']
        accepted = [(value, where) for (value, where), ok in zip(pool, found) if ok]
        if not accepted:
            return [f'- {head}: no seed found among the string literals of {predicate["file"]} and the tests the '
                    'change touches; supply one']
        (seed, source), others = accepted[0], accepted[1:]
        source = f'a literal at {source}'
    else:
        seed, source = predicate['literals'][0], 'its own literal'
        others = [(value, where) for value, where in pool if value != seed and _string_test(predicate, value)]
    variants, seen = [], set()
    for label, text in near_misses(seed):
        if text not in seen:
            seen.add(text)
            variants.append((text, label))
    variants += [(value, f'a literal at {where}') for value, where in others if value not in seen]
    shown, cut = variants[:MAX_ROWS], len(variants) - MAX_ROWS
    results, why = _run(predicate, [text for text, _ in shown])
    if why:
        return [f'- {head}; seed {_show(seed)}, {source}: not run: {why}']
    out = [f'- {head}; seed {_show(seed)}, {source}']
    out += [f"  - {'accepted' if ok else 'refused'}: {_show(text)}, {label}" for (text, label), ok in zip(shown, results)]
    if cut > 0:
        out.append(f'  - and {cut} more rows, cut by the cap of {MAX_ROWS}')
    return out


def near_miss_table(root: Path, lines: dict, changed: list[str], config: dict) -> list[str]:
    """The near-miss table for every predicate on a changed line. `lines` maps a changed path to its new-side
    ranges, or None for a whole new file; `changed` names the tests whose literals can seed a regex."""
    predicates, _, notes, literals = scan(root, lines, config)
    tests = [path for path in changed if grade_mode.matches(path, config['tests']) and (root / path).is_file()
             and _scanner(path) is not None]
    for test in tests:
        if test not in literals:
            try:
                literals[test] = _scanner(test)(test, _read(root, test), [])[2]
            except (OSError, SyntaxError, ValueError):
                literals[test] = []
    out = ['Each predicate the change adds or modifies, run against near misses of a string it accepts; accepted '
           'means the predicate holds. A string predicate is seeded with its own literal, a regex with the first '
           'string literal it matches in its file, then in the tests the change touches. The rows after the '
           'generated ones are other literals there that the predicate accepts. A floor, never a ceiling.']
    for predicate in predicates[:MAX_PREDICATES]:
        out += _rows(predicate, _pool(predicate['file'], literals, tests))
    if len(predicates) > MAX_PREDICATES:
        out.append(f'- and {len(predicates) - MAX_PREDICATES} more predicates, cut by the cap of {MAX_PREDICATES}')
    if not predicates:
        out.append('- none: the change adds or modifies no regex, startswith, endswith, or marker test in Python, '
                   'JavaScript, or TypeScript')
    out += [f'- not scanned {note}' for note in notes]
    return out


# ---- second readers


def _literal_runs(pattern: str, flags: int) -> list[str]:
    """The runs of literal characters a regex requires, read with the parser Python's `re` uses."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            try:
                from re import _parser as parser  # Python 3.11 and later
            except ImportError:
                import sre_parse as parser
            parsed = parser.parse(pattern, flags)
    except Exception:  # noqa: BLE001
        return []
    runs, current = [], []

    def flush() -> None:
        if current:
            runs.append(''.join(current))
            current.clear()

    def walk(items) -> None:
        for op, value in items:
            name = str(op)
            if name == 'LITERAL':
                current.append(chr(value))
                continue
            flush()
            if name == 'SUBPATTERN':
                walk(value[-1])
            elif name in ('MAX_REPEAT', 'MIN_REPEAT', 'POSSESSIVE_REPEAT') and value[0] >= 1:
                walk(value[2])
            elif name == 'ATOMIC_GROUP':
                walk(value)
            flush()

    walk(parsed)
    flush()
    return runs


def _anchors(predicates: list[dict], anchors: list[dict]) -> list[dict]:
    """One search term per anchor: a string predicate's literal, a regex's longest literal run, a separator."""
    out = []
    for p in predicates:
        if p['kind'] == 'regex':
            if p.get('why'):
                continue
            ignore = 'i' in p['flags'] if p['lang'] == 'js' else bool(p['flags'] & re.IGNORECASE)
            runs = _literal_runs(p['pattern'], 0 if p['lang'] == 'js' else p['flags'])
            if runs:
                out.append({**p, 'term': max(runs, key=len), 'ignore': ignore, 'from': 'the longest literal run of the regex'})
        else:
            out += [{**p, 'term': literal, 'ignore': False, 'from': f"the {p['kind']}"} for literal in p['literals']]
    out += [{**a, 'ignore': False, 'from': f"the {a['kind']}"} for a in anchors]
    for a in out:
        a['term'] = max(a['term'].splitlines() or [''], key=len).strip()
    return out


def _searchable(term: str) -> bool:
    return len(term) >= MIN_ANCHOR and sum(char.isalnum() for char in term) >= 3


def second_readers(root: Path, head: str, lines: dict, config: dict) -> list[str]:
    """Each parse anchor on a changed line, with every other file at `head` whose code holds the same text."""
    predicates, anchors, _, _ = scan(root, lines, config)
    found, seen, short = [], set(), 0
    for anchor in _anchors(predicates, anchors):
        key = (anchor['term'].lower() if anchor['ignore'] else anchor['term'], anchor['ignore'])
        if key in seen:
            continue
        seen.add(key)
        if _searchable(anchor['term']):
            found.append(anchor)
        else:
            short += 1
    out = ['Each parse anchor the change touches: the string a changed predicate tests, the longest literal run of a '
           'changed regex, and the separator of a changed split, partition, or find. Under each, every other file at the '
           'head whose code holds the same text, a second reader or a writer of the format: run the near misses above '
           f"through each. Found with `git grep -n -a -F` outside vendored directories, with the paths `notCode` names "
           'dropped and tests counted, not listed. A floor, never a ceiling: a reader that builds the text at runtime, '
           'or spells it as another regex, is not found.']
    for anchor in found[:MAX_ANCHORS]:
        run = subprocess.run(['git', '-c', 'core.quotePath=false', 'grep', '-n', '-a', '-F',
                              *(['-i'] if anchor['ignore'] else []), '-e', anchor['term'], head, '--', '.', *VENDORED],
                             cwd=root, capture_output=True, text=True, errors='replace')
        where = f"{anchor['file']}:{anchor['line']}"
        label = (f"- {_show(anchor['term'])}, from {anchor['from']} at {where}"
                 + (', searched ignoring case' if anchor['ignore'] else ''))
        if run.returncode not in (0, 1):
            out.append(f"{label}: the search failed: {' '.join(run.stderr.split())[:OUTPUT_CHARS]}")
            continue
        files: dict[str, list[str]] = {}
        tests = set()
        for hit in run.stdout.splitlines():
            parts = hit.split(':', 3)
            if len(parts) < 3 or parts[1] == anchor['file']:
                continue
            path, number = parts[1], parts[2]
            if grade_mode.code_files([path], config):
                files.setdefault(path, []).append(number)
            elif grade_mode.matches(path, config['tests']):
                tests.add(path)
        listed = [f"{path}:{', '.join(numbers)}" for path, numbers in list(files.items())[:MAX_READERS]]
        more = f', and {len(files) - MAX_READERS} more files' if len(files) > MAX_READERS else ''
        counted = f"; and {len(tests)} test file{'s' if len(tests) != 1 else ''}" if tests else ''
        out.append(f"{label}: {', '.join(listed) or 'none found'}{more}{counted}")
    if len(found) > MAX_ANCHORS:
        out.append(f'- and {len(found) - MAX_ANCHORS} more anchors, cut by the cap of {MAX_ANCHORS}')
    if not found:
        out.append(f'- none: the change touches no parse anchor of {MIN_ANCHOR} or more characters')
    if short:
        out.append(f'- left out {short} anchor{"s" if short != 1 else ""} shorter than {MIN_ANCHOR} characters or with '
                   'fewer than 3 letters or digits, too common to search')
    return out


def report(root: Path, head: str, lines: dict, changed: list[str], config: dict) -> list[str]:
    """The lines a grader holding the near-miss lens reads."""
    out = ['### Near misses']
    try:
        out += near_miss_table(root, lines, changed, config)
    except Exception as failed:  # noqa: BLE001
        # Fails open: the table is a floor for one lens, and a prep that stops here grades nothing.
        out.append(f'The scan stopped, so no predicate is listed: {type(failed).__name__}: {str(failed)[:OUTPUT_CHARS]}')
    out.append('### Second readers')
    try:
        out += second_readers(root, head, lines, config)
    except Exception as failed:  # noqa: BLE001
        out.append(f'The search stopped, so no second reader is listed: {type(failed).__name__}: '
                   f'{str(failed)[:OUTPUT_CHARS]}')
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--base', default='origin/main')
    args = ap.parse_args()
    root = grade_mode.repo_root()
    config = grade_mode.load_config(root)
    lines = grade_mode.changed_lines(args.base, root)
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    print('\n'.join(report(root, head, lines, sorted(lines), config)))


if __name__ == '__main__':
    main()
