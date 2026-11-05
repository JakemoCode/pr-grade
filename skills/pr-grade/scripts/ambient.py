#!/usr/bin/env python3
"""Ambient assumptions: what a change assumes about the world outside the process.

    python3 ambient.py [--base origin/main] [--copy <proof copy>]

Run it from inside the repository, at the committed head. It prints two lists for the grader holding
L10, each a floor and never a ceiling:

- exit codes: every process call the change adds or modifies, in Python, JavaScript, TypeScript, and
  shell scripts, with the command it starts, what the call site does with the exit code, and the
  registry's note when that command's exit codes surprise people. `grep` exits 1 on no match,
  `gh pr checks` exits 1 when a pull request has no checks, GNU `tar` exits 2 on a fatal error. A
  repository extends the registry under `exitCodes` in `.claude/pr-grade.json`, command to note.
- env sweep: the tests the change adds or modifies, run once per perturbation of the environment in
  a proof copy, with a pass or fail per run. The perturbations are a timezone east and one west of
  UTC, a non-English locale, a restrictive umask, a stray dotfile in each directory the change
  touches, CRLF line endings in each data file the change touches, and a file holding a NUL byte in
  each such directory. A repository opts in under `envSweep`:

    "envSweep": {"command": ["python3", "-m", "unittest", "{tests}"], "timeout": 300}

  `{tests}` stands for the changed test files, one argument each, matched by the config's `tests`
  globs. A repository that declares no `envSweep` pays nothing, and so does a change that touches no
  test. The command runs from the copy's root. A baseline run comes first; when it fails, the sweep
  says so and runs nothing else, since nothing it found would mean anything.

Nothing here stops a grade: a scan or a run that fails is printed as a note.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f'_pr_grade_{name}', HERE / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


grade_mode = _load('grade_mode')

# Commands whose exit codes surprise people. Looked up by the command and its first subcommands, longest
# match first, so `gh pr checks` finds its own note before `gh` does.
REGISTRY = {
    'gh pr checks': 'exits 1 when the pull request has no checks and 8 while a check is pending; neither is a failed check',
    'gh': 'exits 1 for a missing pull request, issue, or repository as well as for an API error',
    'grep': 'exits 1 on no match and 2 on an error, so no match read as failure stops a run that found nothing wrong',
    'rg': 'exits 1 on no match and 2 on an error, like grep',
    'git grep': 'exits 1 on no match, like grep',
    'git diff': 'exits 1 on a difference with --exit-code or --quiet, 0 otherwise even when inputs differ',
    'git merge-base': 'exits 1 when the commits share no ancestor',
    'git': 'exits 128 for a repository or ref error and 1 for most refusals; `git diff` reports a difference as 1 only with --exit-code',
    'diff': 'exits 1 when the inputs differ and 2 on trouble, so a difference is not an error',
    'cmp': 'exits 1 when the inputs differ and 2 on trouble',
    'tar': 'GNU tar exits 1 when a file changed while reading and 2 on a fatal error; bsdtar exits 1 for everything',
    'curl': 'exits 0 on an HTTP 4xx or 5xx unless --fail or --fail-with-body is given, then 22',
    'wget': 'exits 8 on a server error and 4 on a network failure',
    'timeout': 'exits 124 when the command timed out, and the command\'s own code otherwise',
    'xargs': 'exits 123 when any invocation exited 1-125, and 124 when one exited 255',
    'find': 'exits 1 after printing its results when any directory could not be read, so output and failure arrive together',
    'jq': 'exits 0 with `null` for a missing key; -e makes a false or null last output exit 1; 2 is usage, 3 a compile error',
    'ssh': 'exits 255 on a connection error and the remote command\'s code otherwise',
    'pytest': 'exits 5 when no tests were collected and 2 when collection was interrupted',
    'npm ci': 'exits 1 when the lock file disagrees with package.json, before installing anything',
    'npm': 'passes a script\'s exit code through and exits 1 for its own failures',
    'tsc': 'exits 2 on type errors and 1 on a usage error',
    'eslint': 'exits 1 for lint errors and 2 for a crash or a config error',
    'test': 'exits 1 when the expression is false and 2 on a bad expression, so a typo reads as false',
    '[': 'exits 1 when the expression is false and 2 on a bad expression, so a typo reads as false',
}

PY_CALL = re.compile(r'\b(?:subprocess\.(?:run|call|check_call|check_output|Popen)|os\.system|os\.popen)\s*\(')
JS_CALL = re.compile(r'(?:\b(?:child_process|childProcess|cp)\.|(?<![\w.]))(?:execFileSync|execFile|execSync|exec|spawnSync|spawn)\s*\(')
# One alternative per quote, each body a run of escapes or non-quote characters, so a literal the window cuts
# before its closing quote fails in one pass instead of backtracking over every escape.
STRING = re.compile(r'''(?:[rbfu]{0,2})(?:"((?:\\.|[^"\\])*)"|'((?:\\.|[^'\\])*)'|`((?:\\.|[^`\\])*)`)''', re.S)
PY_HANDLES = ('check=True', 'check_returncode', 'returncode', 'CalledProcessError', 'check_call', 'check_output')
JS_HANDLES = ('.status', '.exitCode', 'catch', 'error', 'ExecException', '.code')
SH_WORDS = {'if', 'then', 'elif', 'else', 'while', 'until', 'do', 'exec', 'time', 'sudo', 'env', 'command', '!',
            'nice', 'nohup'}
SH_SUFFIXES = ('.sh', '.bash', '.zsh', '.ksh')
WINDOW_LINES = 6
WINDOW_CHARS = 600
OUTPUT_LINES = 5
OUTPUT_CHARS = 400


def registry(config: dict) -> dict:
    extra = config.get('exitCodes') or {}
    if not isinstance(extra, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in extra.items()):
        return dict(REGISTRY)
    return {**REGISTRY, **extra}


def note_for(words: list[str], notes: dict) -> tuple[str, str] | None:
    """The registry entry for a command and its first subcommands, longest match first."""
    names = [os.path.basename(words[0]), *words[1:3]] if words else []
    for n in range(min(3, len(names)), 0, -1):
        key = ' '.join(names[:n])
        if key in notes:
            return key, notes[key]
    return None


def _in_ranges(line: int, ranges) -> bool:
    return ranges is None or any(start <= line <= end for start, end in ranges)


def _body(match: re.Match) -> str:
    return next(group for group in match.groups() if group is not None)


def _words(text: str) -> list[str]:
    """Shell words, or whitespace-split words when a quote is unmatched: one apostrophe costs that call its
    exact words, never the list."""
    try:
        return shlex.split(text, posix=True)
    except ValueError:
        return text.split()


def command_words(call: str) -> list[str]:
    """The command a call starts, as words, from the first string literal of its first argument: the words of
    a command string, or each literal of a list up to its closing bracket. Empty when the argument is a name."""
    after = call[call.index('(') + 1:].lstrip()
    if after.startswith('['):
        end = after.find(']')
        return [_body(m) for m in STRING.finditer(after[1:end if end > 0 else None])][:4]
    found = STRING.match(after)
    if not found:
        return []
    words = _words(_body(found))[:4] if _body(found).strip() else []
    rest = after[found.end():].lstrip()
    if words and rest.startswith(',') and rest[1:].lstrip().startswith('['):
        # execFile('tsc', ['--noEmit']): the arguments come as a list after the command.
        rest = rest[1:].lstrip()
        end = rest.find(']')
        words += [_body(m) for m in STRING.finditer(rest[1:end if end > 0 else None])]
    return words[:4]


def _handles(window: str, tokens: tuple[str, ...]) -> str:
    seen = [t for t in tokens if t in window]
    return ', '.join(seen) if seen else f'nothing within {WINDOW_LINES} lines examines the exit code'


def scan_file(path: str, text: str, ranges, notes: dict) -> list[dict]:
    """Every process call on a changed line of one file."""
    lines = text.split('\n')
    found = []
    is_py, is_sh = path.endswith('.py'), is_shell(path, text)
    pattern = PY_CALL if is_py else None if is_sh else JS_CALL
    if pattern is not None:
        matches = list(pattern.finditer(text))
        for i, m in enumerate(matches):
            line = text.count('\n', 0, m.start()) + 1
            if not _in_ranges(line, ranges):
                continue
            call = text[m.start():m.start() + WINDOW_CHARS]
            # The lines after the call up to the next call: a `check=True` on the next call is not this one's.
            until = text.count('\n', 0, matches[i + 1].start()) + 1 if i + 1 < len(matches) else line + WINDOW_LINES
            stop = min(until - 1, line - 1 + WINDOW_LINES) if until > line else line - 1 + WINDOW_LINES
            window = '\n'.join(lines[line - 1:stop])
            words = command_words(call)
            found.append({'file': path, 'line': line, 'command': ' '.join(words) or m.group(0).rstrip('( ').lstrip('.') + ' with a command built at runtime',
                          'handles': _handles(window, PY_HANDLES if is_py else JS_HANDLES), 'note': note_for(words, notes)})
    elif is_sh:
        strict = re.search(r'(?m)^\s*set\s+(?:-[a-zA-Z]*e|-o\s+errexit)', text) is not None
        for number, line in enumerate(lines, 1):
            if not _in_ranges(number, ranges) or line.lstrip().startswith('#'):
                continue
            # Segments and the operators between them, so `grep x; cp a b || exit 1` tests only the cp.
            parts = re.split(r'(\|\||&&|\||;|\$\(|\)|`)', line)
            for i in range(0, len(parts), 2):
                segment, after = parts[i], parts[i + 1] if i + 1 < len(parts) else ''
                raw = _words(segment)
                words = [w for w in raw if ('=' not in w or w.startswith('-')) and w not in SH_WORDS]
                note = note_for(words, notes)
                if note is None:
                    continue
                tested = after in ('||', '&&') or any(w in ('if', '!', 'while', 'until', 'elif') for w in raw)
                found.append({'file': path, 'line': number, 'command': ' '.join(words[:3]),
                              'handles': 'tested by the line\'s if, !, && or ||' if tested
                              else 'set -e stops the script on any nonzero' if strict else 'nothing on the line examines the exit code',
                              'note': note})
    return found


def is_shell(path: str, text: str) -> bool:
    return path.endswith(SH_SUFFIXES) or bool(re.match(r'#!.*\b(?:ba|z|k|da)?sh\b', text[:120]))


def exit_codes(root: Path, lines: dict, config: dict) -> list[str]:
    """The exit-code list for every changed code or shell file. `lines` maps a changed path to its new-side
    ranges, or None for a whole new file."""
    notes = registry(config)
    calls, unreadable = [], []
    for path, ranges in sorted(lines.items()):
        file = root / path
        if not file.is_file() or not (path.endswith(('.py',) + SH_SUFFIXES) or _is_js(path) or _has_shebang(file)):
            continue
        try:
            text = file.read_text(encoding='utf-8', errors='replace')
        except OSError as failed:
            unreadable.append(f'{path}: {failed.strerror}')
            continue
        calls += scan_file(path, text, ranges, notes)
    out = ['Process calls the change adds or modifies, with what the call site does with the exit code. A command built at '
           'runtime is listed without a registry note; read it by hand.']
    noted = [c for c in calls if c['note']]
    plain = [c for c in calls if not c['note']]
    for c in noted:
        key, note = c['note']
        out.append(f"- {c['file']}:{c['line']} runs `{c['command']}`; handles: {c['handles']}. Registry, `{key}`: {note}.")
    for c in plain:
        out.append(f"- {c['file']}:{c['line']} runs `{c['command']}`; handles: {c['handles']}. No registry note.")
    if not calls:
        out.append('- none: the change adds or modifies no process call in Python, JavaScript, TypeScript, or a shell script')
    out += [f'- not scanned {u}' for u in unreadable]
    return out


def _is_js(path: str) -> bool:
    return path.endswith(('.ts', '.tsx', '.mts', '.cts', '.js', '.jsx', '.mjs', '.cjs'))


def _has_shebang(file: Path) -> bool:
    try:
        with file.open('rb') as handle:
            head = handle.read(120)
    except OSError:
        return False
    return head.startswith(b'#!') and re.search(rb'\b(?:ba|z|k|da)?sh\b', head) is not None


# ---- env sweep


def _test_globs(config: dict) -> list[str]:
    globs = config.get('tests')
    return globs if isinstance(globs, list) else grade_mode.DEFAULTS['tests']


def sweep_targets(changed: list[str], config: dict, tree: Path | None = None) -> list[str]:
    """The changed files the config's `tests` globs match and, given `tree`, that exist there: the changed
    list names a deleted file and the old side of a rename, which no command can run."""
    return [path for path in changed if grade_mode.matches(path, _test_globs(config))
            and (tree is None or (tree / path).is_file())]


def malformed_sweep(sweep: object) -> str | None:
    if not isinstance(sweep, dict):
        return '`envSweep` must be an object'
    command = sweep.get('command')
    if not isinstance(command, list) or not command or not all(isinstance(c, str) for c in command):
        return '`envSweep.command` must be a list of strings'
    if '{tests}' not in command:
        return '`envSweep.command` must hold a `{tests}` argument for the changed test files'
    timeout = sweep.get('timeout', 300)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        return '`envSweep.timeout` must be a positive number of seconds'
    return None


def _run(copy: Path, argv: list[str], env: dict, timeout: float, umask: int | None = None) -> tuple[str, str]:
    """(verdict, detail) for one run of the command in the copy."""
    started = time.monotonic()
    pre = (lambda: os.umask(umask)) if umask is not None else None
    try:
        # Its own session, so a timeout can kill the test command and every child it started.
        proc = subprocess.Popen(argv, cwd=copy, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                errors='replace', start_new_session=True, preexec_fn=pre)
    except OSError as failed:
        return 'could not run', failed.strerror or str(failed)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        return 'timed out', f'after {timeout:g}s; the run and its children were killed'
    seconds = time.monotonic() - started
    if proc.returncode == 0:
        return 'pass', f'{seconds:.1f}s'
    tail = [line for line in (stderr + '\n' + stdout).splitlines()
            if line.strip() and not re.fullmatch(r'[-=]{5,}', line.strip())][-OUTPUT_LINES:]
    detail = ' | '.join(tail)
    return f'FAIL (exit {proc.returncode})', detail[:OUTPUT_CHARS] + ('...' if len(detail) > OUTPUT_CHARS else '')


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()
    try:
        proc.communicate(timeout=5)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass


def _unlink(path: Path) -> None:
    """Removes a stray the sweep wrote; one the tests removed first is already gone."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _write_strays(copy: Path, dirs: list[str], name: str, content: bytes) -> list[Path]:
    made = []
    for d in dirs:
        target = copy / d / name
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        made.append(target)
    return made


def env_sweep(copy: Path, tests: list[str], changed: list[str], config: dict) -> list[str]:
    """Run the changed tests in `copy` once unperturbed and once per perturbation; one line per run."""
    sweep = config.get('envSweep')
    why = malformed_sweep(sweep)
    if why:
        return [f'Note: {why} in .claude/pr-grade.json, so nothing was swept.']
    timeout = float(sweep.get('timeout', 300))
    base_env = {k: v for k, v in os.environ.items() if k not in ('TZ', 'LANG', 'LC_ALL')}
    base_env['LANG'] = base_env['LC_ALL'] = 'C.UTF-8'
    dirs = sorted({os.path.dirname(p) or '.' for p in changed if (copy / (os.path.dirname(p) or '.')).is_dir()})
    data = [p for p in changed if not grade_mode.matches(p, _test_globs(config)) and not _is_code_like(p)
            and (copy / p).is_file() and _is_text(copy / p)]
    gone = [t for t in tests if not (copy / t).is_file()]
    tests = [t for t in tests if (copy / t).is_file()]
    if not tests:
        return ['Not run: every test file the change touches was deleted or renamed away, so there is nothing to run.']
    argv = [arg for part in sweep['command'] for arg in (tests if part == '{tests}' else [part])]
    out = [f"Tests the change touches: {', '.join(tests)}; command: {' '.join(shlex.quote(a) for a in argv)}, run from the copy"
           + (f"; left out as deleted or renamed away: {', '.join(gone)}" if gone else '')]
    verdict, detail = _run(copy, argv, base_env, timeout)
    out.append(f'- baseline, no perturbation: {verdict} ({detail})')
    if verdict != 'pass':
        out.append('The baseline fails, so no perturbation was run: a failure under one would prove nothing.')
        return out
    runs = [
        ('TZ=Pacific/Kiritimati, 14 hours east of UTC', {'TZ': 'Pacific/Kiritimati'}, None, None),
        ('TZ=Pacific/Pago_Pago, 11 hours west of UTC', {'TZ': 'Pacific/Pago_Pago'}, None, None),
        ('LANG=tr_TR.UTF-8 and LC_ALL=tr_TR.UTF-8, a locale whose case folding differs'
         + ('' if _locale_installed('tr_TR') else ' (not installed on this machine, so this row cannot fail)'),
         {'LANG': 'tr_TR.UTF-8', 'LC_ALL': 'tr_TR.UTF-8'}, None, None),
        ('umask 077, so a file the tests create is private', {}, 0o077, None),
        (f"a .DS_Store in each directory the change touches ({', '.join(dirs)})", {}, None, ('.DS_Store', b'\x00\x00\x00\x01Bud1\x00')),
        (f"a file holding a NUL byte in each directory the change touches ({', '.join(dirs)})", {}, None, ('stray-nul-byte.txt', b'a\x00b\n')),
    ]
    for label, extra, umask, stray in runs:
        # A row that cannot be set up or cleaned up costs that row, never the rows already run.
        made = []
        try:
            made = _write_strays(copy, dirs, *stray) if stray else []
            verdict, detail = _run(copy, argv, {**base_env, **extra}, timeout, umask)
        except Exception as failed:  # noqa: BLE001
            verdict, detail = 'could not run', f'{type(failed).__name__}: {str(failed)[:OUTPUT_CHARS]}'
        finally:
            for path in made:
                _unlink(path)
        out.append(f'- {label}: {verdict} ({detail})')
    if data:
        originals = {p: (copy / p).read_bytes() for p in data}
        try:
            for p, text in originals.items():
                (copy / p).write_bytes(text.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
            verdict, detail = _run(copy, argv, base_env, timeout)
        except Exception as failed:  # noqa: BLE001
            verdict, detail = 'could not run', f'{type(failed).__name__}: {str(failed)[:OUTPUT_CHARS]}'
        finally:
            for p, text in originals.items():
                (copy / p).write_bytes(text)
        out.append(f"- CRLF line endings in each data file the change touches ({', '.join(data)}): {verdict} ({detail})")
    else:
        out.append('- CRLF line endings: not run, the change touches no data file (a changed file that is neither code nor a test)')
    return out


def _locale_installed(name: str) -> bool:
    try:
        listed = subprocess.run(['locale', '-a'], capture_output=True, text=True, errors='replace', timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return name in listed.stdout


def _is_code_like(path: str) -> bool:
    return path.endswith(('.py', '.pyi', '.sh', '.bash', '.zsh', '.rb', '.go', '.rs', '.java', '.kt', '.swift', '.c', '.h',
                          '.cc', '.cpp', '.cs', '.php', '.pl', '.lua')) or _is_js(path)


def _is_text(file: Path) -> bool:
    try:
        with file.open('rb') as handle:
            head = handle.read(4096)
    except OSError:
        return False
    return b'\x00' not in head


def report(root: Path, lines: dict, changed: list[str], config: dict, copy: Path | None) -> list[str]:
    """The lines a grader holding the ambient lens reads. `copy` is the proof copy the sweep runs in, or
    None when no sweep is declared or no test changed."""
    out = ['### Exit codes']
    try:
        out += exit_codes(root, lines, config)
    except Exception as failed:  # noqa: BLE001
        # Fails open: the list is a floor for one lens, and a prep that stops here grades nothing.
        out.append(f'The scan stopped, so no process call is listed: {type(failed).__name__}: {str(failed)[:OUTPUT_CHARS]}')
    out.append('### Env sweep')
    try:
        tests = sweep_targets(changed, config, copy)
    except Exception as failed:  # noqa: BLE001
        out.append(f'The sweep stopped before it began: {type(failed).__name__}: {str(failed)[:OUTPUT_CHARS]}')
        return out
    if not config.get('envSweep'):
        out.append('Not run: the repository declares no `envSweep` in .claude/pr-grade.json.')
    elif not tests:
        out.append('Not run: the change adds or modifies no test file, so there is nothing of its own to sweep.')
    elif copy is None:
        out.append('Not run: no proof copy was given to run it in.')
    else:
        try:
            out += env_sweep(copy, tests, changed, config)
        except Exception as failed:  # noqa: BLE001
            out.append(f'The sweep stopped: {type(failed).__name__}: {str(failed)[:OUTPUT_CHARS]}')
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--base', default='origin/main')
    ap.add_argument('--copy', type=Path, help='a proof copy of the head to run the env sweep in')
    args = ap.parse_args()
    root = grade_mode.repo_root()
    config = grade_mode.load_config(root)
    lines = grade_mode.changed_lines(args.base, root)
    print('\n'.join(report(root, lines, sorted(lines), config, args.copy)))


if __name__ == '__main__':
    main()
