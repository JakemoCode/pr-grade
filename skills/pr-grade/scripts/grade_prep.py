#!/usr/bin/env python3
"""Everything a /pr-grade coordinator hands its graders, in one call.

    python3 grade_prep.py [--base origin/main] [--groups NAME=L1,L2 ...] [--skip L2,L3] [--settled TEXT]
                          [--declined TEXT] [--callers TEXT]
    python3 grade_prep.py --surface PATH [PATH ...] [the options above except --base]

Run it from inside the repository once the change is committed. It picks the mode as grade_mode.py
does, splits the lenses among graders (the lens file's `## Fan-out groups` table, or correctness, timing, and
lifetime and contract), lists the callers of each function the branch modified, runs check_then_act.py
for whoever holds L7, near_miss.py for whoever holds L9, history_replay.py for whoever holds L14, ambient.py
for whoever holds L16, and makes
one proof copy per grader with proof_dir.py, plus one for the env sweep when the repository declares one. A grader's
prompt is a shared block followed by its own block. The prep writes it to `prompt.md` in the grader's directory
under the proof root and prints one line per grader naming that file, which the coordinator passes as the Agent
prompt unchanged, and the line names the diff command, so a grader's first turn reads its file and the change. After
the dispatch, each file carries the grader's lens text: SKILL.md section 3 up to its first lens, each lens the grader
holds, sections 4 and 5, then the lens file less the lens sections of lenses the grader does not hold, so the grader
reads neither file. `--skill PATH` names the SKILL.md to slice; with none beside this directory, the files carry the
lens file alone and a `notice:` line says so. `"sliceLenses": false` leaves the lens text out. In-thread, or with
`"promptFiles": false` in the config, it prints the blocks instead.
It ends with the command that merges the graders' reports and the one that removes the proof copies.

`--base` is where the branch left, or the last commit graded for a re-grade. `--settled` names the
checks that already passed at the head, with their results, and `--declined` the review findings the
author declined, with reasons; each is pasted into every prompt as given. `--groups` replaces the
groups, one `NAME=L1,L2` per grader. `--skip` names lenses that do not apply to this change: no grader
holds them, no proof copy is made for them, and `grade.json` records them, so the merge clears each as
`L2 (not applicable)`. `--callers` adds callers found by hand to the derived list. `--exclude-path
PREFIX`, repeatable, leaves every path starting with PREFIX out of the change and its file count, as lock
files are left out of the diff: a repository's kept grade reports, say, which a re-grade's diff would
otherwise grade as code. A branch's mode still counts them, as `grade_block.py check` does.

`--surface` grades the code under the given paths as committed at HEAD, with the empty tree as the base,
so every function counts as new. One surface per command. Tests under the paths are left out of the
change and the caller list but stay in the proof copy. Callers outside the surface are listed, the rest
are counted. L7 scans the whole surface. `--base` and `--branch-base` are refused, since a surface has
no before.

Callers are derived for functions in TypeScript, JavaScript, and Python that existed before the branch.
A changed file in any other language or one an adapter could not read, and a changed entry point or
dunder, is named as not derived. A changed type, option, or constant gets no line. The list says it covers functions only, and
`--callers` adds the callers found by hand. A hit in a file `notCode` names is dropped, unless `countAsCode` names it too: a
function called `clear` is also a word, and prose that uses it is not a caller.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import posixpath
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent


def _load(name: str):
    """A script beside this one, loaded by path under a private name, like grade_block.py loads grade_mode."""
    spec = importlib.util.spec_from_file_location(f'_pr_grade_{name}', HERE / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


grade_mode, grade_block, proof_dir, check_then_act, history_replay, ambient, near_miss = (
    _load(n) for n in ('grade_mode', 'grade_block', 'proof_dir', 'check_then_act', 'history_replay', 'ambient',
                       'near_miss'))

# forgesmith-os's groups, which its lens file keeps; L16 joins timing, since it asks about the world around a run.
DEFAULT_GROUPS = {'correctness': ['L9', 'L10', 'L4', 'L11', 'L15'], 'timing': ['L1', 'L2', 'L7', 'L16'],
                  'lifetime-and-contract': ['L3', 'L5', 'L6', 'L8', 'L14']}
# The lens that reads history_replay.py's output, unless the config's `oldStateLens` names another.
OLD_STATE_LENS = 'L14'
# The lens that reads ambient.py's output, unless the config's `ambientLens` names another.
AMBIENT_LENS = 'L16'
# The lens that reads near_miss.py's output, unless the config's `nearMissLens` names another: its Boundaries probe.
NEAR_MISS_LENS = 'L9'
# The proof copy the env sweep perturbs, beside the graders' own.
SWEEP_COPY = 'env-sweep'
GROUP_ROW = re.compile(r'(?m)^\|\s*([^|]*?[A-Za-z][^|]*?)\s*\|\s*(L\d+(?:\s*,\s*L\d+)*)\s*\|')
LOCKS = ('package-lock.json', 'npm-shrinkwrap.json', 'yarn.lock', 'pnpm-lock.yaml', 'bun.lock', 'bun.lockb',
         'poetry.lock', 'uv.lock', 'Pipfile.lock', 'Cargo.lock', 'go.sum', 'composer.lock', 'Gemfile.lock')
VENDORED_DIRS = ('vendor/', 'third_party/', 'node_modules/')
VENDORED = tuple(f':(exclude){d}*' for d in VENDORED_DIRS)
# A surface has no parent commit to diff against, so the diff is against git's empty tree.
EMPTY_TREE = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'
CALLERS_SHOWN = 30

# The skill text sliced into each grader's prompt file, unless --skill names another copy. A vendored copy of the
# scripts may have none beside it.
SKILL = HERE.parent / 'SKILL.md'
SKILL_SECTION = re.compile(r'##[ \t]+(\d+)\.[ \t]')
HEADING = re.compile(r'(#{1,6})[ \t]')
FENCE = re.compile(r'[ \t]{0,3}(```|~~~)')


def headings(text: str) -> list[tuple[int, int, str]]:
    """Each Markdown heading outside a code fence, as its offset, its level, and its line: a `# comment` in a
    fenced shell block is not a heading."""
    found, fenced, offset = [], False, 0
    for line in text.splitlines(keepends=True):
        if FENCE.match(line):
            fenced = not fenced
        elif not fenced:
            heading = HEADING.match(line)
            if heading:
                found.append((offset, len(heading.group(1)), line))
        offset += len(line)
    return found


def lens_pieces(text: str) -> list[tuple[str | None, str]]:
    """The text cut into its `### L<n>.` sections, each under its lens id, and the pieces between them under None.
    A lens section runs to the next heading of its own level or higher, so a `## Ranking` between two lenses
    belongs to none. Joined back, the pieces are the text."""
    pieces: list[tuple[str | None, str]] = []
    lens, start = None, 0
    for offset, level, line in headings(text):
        opens = grade_block.LENS_HEADING.match(line)
        if opens or (lens is not None and level <= 3):
            pieces.append((lens, text[start:offset]))
            lens, start = (opens.group(1) if opens else None), offset
    pieces.append((lens, text[start:]))
    return [(lens, piece) for lens, piece in pieces if piece]


def sliced_lens_file(text: str, held: list[str]) -> str:
    """The lens file less the lens sections of lenses not in `held`. Every other piece stays, whatever it is."""
    return ''.join(piece for lens, piece in lens_pieces(text) if lens is None or lens in held)


def skill_lens_text(text: str) -> tuple[str, dict[str, str], str] | None:
    """The skill's lens text as a prompt file takes it: section 3 up to its first lens, each lens's `### L<n>.`
    section by id in the skill's order, and sections 4 and 5 whole. None when a section is missing. The one reader
    of where the skill keeps its lenses, so splitting SKILL.md re-points this function alone."""
    level_two = [(offset, line) for offset, level, line in headings(text) if level == 2]
    starts = {}
    for offset, line in level_two:
        numbered = SKILL_SECTION.match(line)
        if numbered:
            starts.setdefault(numbered.group(1), offset)
    if not {'3', '4', '5'} <= starts.keys():
        return None
    after = [offset for offset, _ in level_two if offset > starts['5']]
    pieces = lens_pieces(text[starts['3']:starts['4']])
    sections = {lens: piece for lens, piece in pieces if lens is not None}
    if not sections or pieces[0][0] is not None:
        return None
    return pieces[0][1], sections, text[starts['4']:after[0] if after else len(text)]


def prompt_lens_text(held: list[str], skill: tuple[str, dict[str, str], str] | None, skill_path: Path,
                     lens_file: str | None, lens_path: Path) -> str:
    """What a grader holding `held` reads in its prompt file in place of SKILL.md sections 3 to 5 and the lens file:
    each copied whole but for the lens sections of the lenses it does not hold. Empty when there is neither."""
    parts = []
    if skill is not None:
        opening, sections, rules = skill
        parts += [f'===== skill: {skill_path}, section 3 up to its first lens, the lenses you hold, then sections 4 '
                  'and 5 =====', '', opening + ''.join(text for lens, text in sections.items() if lens in held) + rules]
    if lens_file is not None:
        parts += [f'===== lens file: {lens_path}, less the lens sections of lenses you do not hold =====', '',
                  sliced_lens_file(lens_file, held)]
    if not parts:
        return ''
    notes = ['Each part below is a copy of the file its heading names, every piece of it but the lens sections of '
             'lenses you do not hold, so do not read that file.']
    if skill is None:
        notes.append('SKILL.md is not copied here: read sections 3 to 5 of your installed SKILL.md, as your Inputs say.')
    if lens_file is None:
        notes.append("No lens file: the skill's lenses apply as written.")
    return '\n'.join(['', '===== lens text =====', '', ' '.join(notes), '', *parts])


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, check=check)


def slug(name: str) -> str:
    return re.sub(r'[^A-Za-z0-9]+', '-', name).strip('-').lower() or 'group'


def skipped_lenses(given: list[str], lenses: list[str]) -> list[str]:
    """The lenses `--skip` names, in the lens file's order. Each is a lens id the lens file defines, and
    at least one lens is left to grade."""
    named = [item for value in given for item in re.split(r'[\s,]+', value) if item]
    bad = [item for item in named if not re.fullmatch(r'L\d+', item) or item not in lenses]
    if bad:
        sys.exit(f"grade_prep: --skip names {', '.join(bad)}, which the lens file does not define as lenses")
    skipped = [lens for lens in lenses if lens in named]
    if skipped and len(skipped) == len(lenses):
        sys.exit('grade_prep: --skip names every lens, which leaves nothing to grade')
    return skipped


def groups_for(mode: str, lenses: list[str], lens_text: str | None, given: list[str],
               skipped: list[str] | None = None) -> dict[str, list[str]]:
    """Grader name to its lenses. Every lens not in `skipped` lands in exactly one group: one the table
    leaves out gets a group of its own, so a lens the lens file added is never left ungraded. A skipped
    lens lands in none, and a group it leaves empty is dropped."""
    skipped = skipped or []
    graded = [lens for lens in lenses if lens not in skipped]
    if given:
        groups = {}
        for item in given:
            name, _, ids = item.partition('=')
            if slug(name) in groups:
                sys.exit(f'grade_prep: two --groups are named {slug(name)!r}')
            groups[slug(name)] = re.findall(r'L\d+', ids)
        both = sorted({lens for ids in groups.values() for lens in ids if lens in skipped})
        if both:
            sys.exit(f"grade_prep: {', '.join(both)} is in --groups and --skip; grade it or skip it")
    elif mode != 'fan-out':
        return {'grade': graded}
    else:
        section = re.search(r'(?ms)^## Fan-out groups\s*$(.*?)(?=^## |\Z)', lens_text or '')
        rows = GROUP_ROW.findall(section.group(1)) if section else []
        groups = {slug(name): [lens for lens in re.findall(r'L\d+', ids) if lens not in skipped]
                  for name, ids in rows if name.lower() != 'group'}
        groups = groups or {name: [lens for lens in ids if lens in graded] for name, ids in DEFAULT_GROUPS.items()}
    held = [lens for ids in groups.values() for lens in ids]
    twice = sorted({lens for lens in held if held.count(lens) > 1})
    if twice:
        sys.exit(f"grade_prep: {', '.join(twice)} is in more than one group; give each lens one grader")
    placed = set(held)
    unknown = sorted(placed - set(lenses))
    if unknown:
        sys.exit(f"grade_prep: groups name {', '.join(unknown)}, which the lens file does not define")
    rest = [lens for lens in graded if lens not in placed]
    if rest:
        groups['other-lenses'] = rest
    return {name: ids for name, ids in groups.items() if ids}


def symbol(name: str) -> str | None:
    """What a caller writes to reach a function the adapters named: the method, or the class for a
    constructor. None for a callback or an anonymous function, which nothing calls by name, and for an
    entry point: every script has a `main` and every class a `__repr__`, so a search for one lists the
    rest, not callers."""
    if ' > ' in name or name.startswith('<'):
        return None
    head, _, last = name.rpartition('.')
    if last in ('constructor', '__init__'):
        return head.rpartition('.')[2] or None
    return None if last == 'main' or (last.startswith('__') and last.endswith('__')) else last


def modified_functions(root: Path, fork: str, lines: dict, config: dict,
                       surface: bool = False) -> tuple[dict[str, list[str]], list[str]]:
    """Each function the branch changed that existed at `fork`, as symbol to every `file:line` defining
    it, and a note for each changed code file whose functions could not be read. For a surface every
    named function counts: there is no before to have existed in."""
    code = [f for f in lines if (root / f).is_file() and check_then_act.is_code(f, config)]
    files = [f for f in code if check_then_act.supported(f)]
    typescript = [f for f in files if check_then_act.is_typescript(f)]
    try:
        parsed, skipped, _ = check_then_act.parse_typescript(typescript, root)
    except SystemExit as failed:
        # The adapter crashed; the callers of those files are searched by hand rather than the prep failing.
        parsed, skipped = {}, [{'file': f, 'reason': f'the TypeScript adapter failed: {failed.code}'} for f in typescript]
    python, python_skipped = check_then_act.parse_python([f for f in files if not check_then_act.is_typescript(f)], root)
    parsed.update(python)
    notes = [f"{s['file']}: no adapter reads this language" for s in check_then_act.unscannable(code)]
    notes += [f"{s['file']}: {s['reason']}" for s in skipped + python_skipped]
    found: dict[str, list[str]] = {}
    for file in files:
        ranges = lines.get(file)
        for fn in parsed.get(file, []):
            first, last = fn['span'][0][0], fn['span'][1][0]
            name = symbol(fn['name'])
            if ranges is not None and not any(start <= last and first <= end for start, end in ranges):
                continue
            if name is None:
                # Say so, so a changed method missing from the list reads as skipped, never as uncalled.
                if not fn['name'].startswith('<') and ' > ' not in fn['name']:
                    notes.append(f"{file}:{first} `{fn['name']}`: an entry point or dunder, which the language "
                                 'calls implicitly')
                continue
            # A function new on the branch has no caller the branch did not write.
            if surface or git(root, 'grep', '-q', '-F', '-w', '-e', name, fork, '--', file, check=False).returncode == 0:
                found.setdefault(name, []).append(f'{file}:{first}')
    return found, notes


def callers(root: Path, head: str, names: dict[str, list[str]], config: dict,
            inside: Callable[[str], bool] | None = None) -> list[str]:
    """`inside` marks a path a surface grader already reads, or a test: its hits are counted, not listed.
    A hit in a file the config's `notCode` names, and `countAsCode` does not, is dropped: a function named
    `clear` or `apply` is also a word, and prose that uses it is not a caller. The mode's own matcher
    decides, so the search and the size count agree on what is code."""
    out = []

    def code_or_test(path: str) -> bool:
        return check_then_act.is_code(path, config) or bool(grade_mode.matches(path, config['tests']))

    for name, defined in sorted(names.items()):
        # -F: a name is a fixed string. As a regex, `items$` matches a line that ends in `items`.
        run = git(root, '-c', 'core.quotePath=false', 'grep', '-n', '-a', '-F', '-w', '-e', name, head, '--', '.',
                  *VENDORED, check=False)
        # `<sha>:<path>:<line>:<text>`, and only the location is kept.
        hits = [':'.join(line.split(':', 3)[1:3]) for line in run.stdout.splitlines()]
        hits = [hit for hit in hits if hit not in defined and code_or_test(hit.rsplit(':', 1)[0])]
        near = [hit for hit in hits if inside and inside(hit.rsplit(':', 1)[0])]
        hits = [hit for hit in hits if hit not in near]
        shown = ', '.join(hits[:CALLERS_SHOWN]) or ('none found outside the surface' if inside else 'none found')
        more = f', and {len(hits) - CALLERS_SHOWN} more' if len(hits) > CALLERS_SHOWN else ''
        within = f', and {len(near)} more inside the surface or in tests' if near else ''
        out.append(f"- `{name}`, defined {', '.join(sorted(defined))}: {shown}{more}{within}")
    return out


def surface_files(root: Path, head: str, paths: list[str], config: dict) -> tuple[list[str], list[str], set[str]]:
    """The given paths made repository-relative, the code files committed under them at `head`, and every
    committed file under them. Stops with the path named when one is outside the repository or holds
    nothing committed: graders see commits, so a file only on disk is not part of a surface."""
    relative = []
    for given in paths:
        target = Path(given) if Path(given).is_absolute() else root / given
        clean = posixpath.normpath(target.as_posix())
        try:
            relative.append(Path(clean).relative_to(root).as_posix())
        except ValueError:
            sys.exit(f'grade_prep: --surface {given} is outside the repository at {root}')
    every: set[str] = set()
    for given, path in zip(paths, relative):
        listed = git(root, '-c', 'core.quotePath=false', 'ls-tree', '-r', '--name-only', head, '--',
                     path).stdout.splitlines()
        if not listed:
            sys.exit(f'grade_prep: --surface {given} holds no file committed at HEAD')
        every.update(listed)
    left_out = config['tests'] + config['notCode']
    code = sorted(path for path in every if path.rsplit('/', 1)[-1] not in LOCKS
                  and not path.startswith(VENDORED_DIRS) and not grade_mode.matches(path, left_out))
    return list(dict.fromkeys(relative)), code, every


def pathspec_excludes(patterns: list[str]) -> str:
    """Config globs as git exclude pathspecs. A pattern with a `/` matches the whole path and
    its `*` crosses directories, as git's default pathspec does; one without matches the file name in any
    directory, which needs the glob form."""
    return ''.join(' ' + shlex.quote(f':(exclude){p}' if '/' in p else f':(exclude,glob)**/{p}') for p in patterns)


def l7_block(root: Path, base: str, committed: set[str], paths: list[str] | None = None) -> str:
    try:
        # A surface names its files, so the scan is whole by construction; absolute so the scan resolves them
        # from the repository, not from where this ran.
        result = (check_then_act.scan(root, base, False, []) if paths is None
                  else check_then_act.scan(root, base, True, [str(root / p) for p in paths]))
    except SystemExit as failed:
        return f'check_then_act.py failed, so no windows are listed: {failed.code}. Find the L7 windows by hand.'
    # The scan reads untracked files too; a grader's diff and copy hold commits only.
    result['candidates'] = [c for c in result['candidates'] if c['file'] in committed]
    result['skipped'] = [s for s in result['skipped'] if s['file'] in committed]
    skipped = [f"skipped {s['file']}: {s['reason']}" for s in result['skipped']]
    return '\n'.join([check_then_act.render(result), *skipped])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--base')
    ap.add_argument('--branch-base', help='on a re-grade, where the whole branch began, for the Mode the grade '
                                           'block keeps (default origin/main, or --base when that is not given)')
    ap.add_argument('--surface', nargs='+', action='extend', metavar='PATH',
                    help='grade the code under these paths as it stands at HEAD, not a branch diff')
    ap.add_argument('--groups', action='append', default=[], metavar='NAME=L1,L2')
    ap.add_argument('--skip', action='append', default=[], metavar='L2,L3',
                    help='lenses that do not apply to this change; no grader holds them')
    ap.add_argument('--settled', default='')
    ap.add_argument('--declined', default='')
    ap.add_argument('--callers', default='', help='callers found by hand, for a type, option, or other language')
    ap.add_argument('--exclude-path', action='append', default=[], metavar='PREFIX',
                    help='leave paths starting with PREFIX out of the change and its file count; repeats')
    ap.add_argument('--skill', metavar='PATH', help='the SKILL.md whose lens text the prompt files carry (default: '
                                                     'the one beside this script)')
    args = ap.parse_args()
    surface = args.surface is not None
    if surface and (args.base is not None or args.branch_base is not None):
        sys.exit('grade_prep: a surface grade has no base; drop --base and --branch-base')
    args.base = args.base or 'origin/main'
    root = grade_mode.repo_root()
    if surface:
        args.branch_base = args.base
    elif args.branch_base is None:
        has_main = git(root, 'rev-parse', '--verify', '--quiet', 'origin/main', check=False).returncode == 0
        args.branch_base = 'origin/main' if has_main else args.base
    if git(root, 'status', '--porcelain', '--untracked-files=no').stdout.strip():
        sys.exit('grade_prep: the change has uncommitted edits. Graders see commits only; commit, then run this again.')
    config = grade_mode.load_config(root)
    prompt_files = config.get('promptFiles', True)
    if not isinstance(prompt_files, bool):
        # "false" as a string is truthy, and would write the files the author turned off.
        sys.exit(f'grade_prep: promptFiles in {grade_mode.CONFIG} must be true or false, not {json.dumps(prompt_files)}')
    slice_lenses = config.get('sliceLenses', True)
    if not isinstance(slice_lenses, bool):
        sys.exit(f'grade_prep: sliceLenses in {grade_mode.CONFIG} must be true or false, not {json.dumps(slice_lenses)}')
    skill_path = Path(args.skill).resolve() if args.skill else SKILL
    skill = skill_lens_text(skill_path.read_text()) if skill_path.is_file() else None
    if args.skill and skill is None:
        # A caller that names its own copy, such as one with the pool's pull request numbers stripped, must never
        # have its graders fall back to the installed text.
        sys.exit(f'grade_prep: --skill {args.skill} is not a file whose sections 3 to 5 hold `### L<n>.` lenses')
    notice = grade_mode.ordinary_notice(root)
    head = git(root, 'rev-parse', 'HEAD').stdout.strip()
    kept = tuple(args.exclude_path)

    def included(found: list[str] | set[str]) -> list[str]:
        return [path for path in found if not path.startswith(kept)]

    if surface:
        paths, changed, under = surface_files(root, head, args.surface, config)
        changed, under = included(changed), set(included(under))
        fork, committed = EMPTY_TREE, set(changed)
        if not changed:
            sys.exit(f"grade_prep: no code to grade under {', '.join(args.surface)}: every committed file there is a "
                     'test, documentation, a lock file, or vendored')
    else:
        try:
            fork = git(root, 'merge-base', args.base, 'HEAD').stdout.strip()
        except subprocess.CalledProcessError as failed:
            sys.exit(f'grade_prep: cannot find where HEAD left {args.base}: {failed.stderr.strip()}')
        # Graders see commits only, so a file the author never added is left out of everything here.
        committed = set(git(root, '-c', 'core.quotePath=false', 'diff', '--name-only', '--no-renames', fork, head).stdout.split('\n')) - {''}
        changed = [path for path in grade_mode.changed_files(args.base, root) if path in committed]
        if not changed:
            sys.exit(f'grade_prep: no committed changes since {args.base}; nothing to grade')
    mode, reasons, _ = grade_mode.assess(changed, root, config)
    # The grade block keeps the whole branch's mode through every re-grade; grade_block.py checks it.
    branch_mode = mode
    if args.branch_base != args.base:
        try:
            branch_fork = git(root, 'merge-base', args.branch_base, 'HEAD').stdout.strip()
        except subprocess.CalledProcessError as failed:
            sys.exit(f'grade_prep: cannot find where the branch left {args.branch_base}: {failed.stderr.strip()}. '
                     'Pass --branch-base.')
        branch = git(root, '-c', 'core.quotePath=false', 'diff', '--name-only', '--no-renames', branch_fork,
                     head).stdout.split('\n')
        branch = [path for path in branch if path]
        branch_mode = max(mode, grade_mode.assess(branch, root, config)[0], key=grade_mode.MODES.index)
    if not surface:
        # The mode counted the excluded paths, since grade_block.py check sizes it from every file the pull request
        # touches; the graders see the rest.
        changed, committed = included(changed), set(included(committed))
        if not changed:
            sys.exit(f"grade_prep: every committed change since {args.base} is under {', '.join(kept)}; nothing to grade")
    lens_path = root / config['lenses']
    lens_text = lens_path.read_text() if lens_path.is_file() else None
    lenses = grade_block.lens_ids(lens_text)
    skipped = skipped_lenses(args.skip, lenses)
    groups = groups_for(mode, lenses, lens_text, args.groups, skipped)
    slicing = prompt_files and slice_lenses and mode != 'in-thread'

    # None means all of a file is new, which is what every file of a surface is.
    lines = ({path: None for path in changed} if surface else
             {path: ranges for path, ranges in grade_mode.changed_lines(args.base, root).items() if path in committed})
    modified, notes = modified_functions(root, fork, lines, config, surface)
    # Everything that can stop the run happens before the copies exist, so a failure leaves none behind.
    l7 = (l7_block(root, args.base, committed, changed if surface else None)
          if any('L7' in ids for ids in groups.values()) else '')
    old_state = config.get('oldStateLens', OLD_STATE_LENS)
    # A surface has no before. The empty tree would hold no instance and make the transplant call every
    # record kind in the repository an addition, so the replay reads the head as its own base.
    replayed = ([] if old_state in skipped
                else history_replay.report(root, head if surface else fork, head, config, changed))
    holder = next((name for name, ids in groups.items() if old_state in ids), None)
    near_miss_lens = config.get('nearMissLens', NEAR_MISS_LENS)
    near_miss_lines = (near_miss.report(root, head, lines, changed, config)
                       if near_miss_lens in lenses and near_miss_lens not in skipped else [])
    near_miss_holder = next((name for name, ids in groups.items() if near_miss_lens in ids), None)
    ambient_lens = config.get('ambientLens', AMBIENT_LENS)
    # The sweep writes stray files into its copy and puts them back, so it gets a copy of its own.
    # A lens file without the ambient lens grades nothing with it, so nothing is scanned or swept for it.
    ambient_graded = ambient_lens in lenses and ambient_lens not in skipped
    sweep_tests = ambient.sweep_targets(changed, config, root) if config.get('envSweep') and ambient_graded else []
    try:
        made = proof_dir.make(root, head, list(groups) + ([SWEEP_COPY] if sweep_tests else []), config,
                              Path(tempfile.gettempdir()), since=None if surface else fork)
    except subprocess.CalledProcessError as failed:
        sys.exit(f"grade_prep: making the proof copies failed at `git {' '.join(failed.cmd[1:3])}`: "
                 f"{(failed.stderr or '').strip()}")
    fields = dict(line.split(': ', 1) for line in made if ': ' in line)
    proof_root = Path(fields['root'])
    # Until the remove: line prints, nothing else names the copies, so an interrupted prep removes them itself.
    try:
        ambient_lines = (ambient.report(root, lines, changed, config, Path(fields[f'copy {SWEEP_COPY}']) if sweep_tests else None)
                         if ambient_graded else [])
        ambient_holder = next((name for name, ids in groups.items() if ambient_lens in ids), None)
        (proof_root / 'grade.json').write_text(json.dumps(
            {'repository': str(root), 'mode': branch_mode, 'roundMode': mode, 'base': fork, 'head': head, 'lenses': lenses,
             'groups': groups, 'skipped': skipped, **({'surface': paths} if surface else {})},
            indent=1) + '\n')

        locks = sorted({path for path in (under if surface else changed) if path.rsplit('/', 1)[-1] in LOCKS})
        excludes = ''.join(f" ':!{path}'" for path in locks) + ''.join(' ' + shlex.quote(f':(exclude){p}*') for p in kept)
        left_out = f"; lock files left out: {', '.join(locks)}" if locks else ''
        left_out += '; --exclude-path prefixes left out' if kept else ''
        if surface:
            excludes += pathspec_excludes(config['tests'] + config['notCode']) + ''.join(f" '{v}'" for v in VENDORED)
            change = f"git -C {root} diff {fork} {head} -- {' '.join(map(shlex.quote, paths))}{excludes}"
            left_out += '; tests, documentation, and vendored directories left out'
        else:
            change = f'git -C {root} diff {fork} {head} -- .{excludes}'
        why = '; '.join(f'{path} ({reason})' for path, reason in sorted(reasons.items())) or 'no silent-failure or unclassified files'
        copy_notes = [line for line in made if line.startswith(('not linked', 'missing', 'warning', 'note', 'linked'))]
        if surface:
            def inside(path: str) -> bool:
                return path in under or bool(grade_mode.matches(path, config['tests']))
            caller_lines = callers(root, head, modified, config, inside) or ['- none: the surface defines no function a caller can name']
        else:
            caller_lines = callers(root, head, modified, config) or ['- none: the branch modified no function that existed before it']
        caller_lines += [f'- not derived for {note}; search by hand' for note in notes]
        caller_lines += [f'- {line.strip()}' for line in args.callers.splitlines() if line.strip()]

        print(f'mode: {mode} ({why})' + (f'; the grade block keeps {branch_mode}, the whole branch' if branch_mode != mode else ''))
        if notice:
            print(notice)
        if slicing and skill is None:
            print(f'notice: no SKILL.md beside {HERE} holds the lenses in sections 3 to 5, so each prompt file carries '
                  'the lens file alone and each grader reads sections 3 to 5 of its installed SKILL.md')
        print('graders: ' + ', '.join(f"{name} ({', '.join(ids)})" for name, ids in groups.items()))
        if skipped:
            print(f"skipped: {', '.join(skipped)}, which no grader holds; the block clears each as "
                  f'`{skipped[0]} (not applicable)`')
        if mode == 'in-thread':
            print('In-thread: grade it yourself from the shared block, proving in the copy below.')
        shared = '\n'.join([
            f'- Repository: {root}',
            f"- Lens file: {lens_path if lens_text is not None else 'none; the skill lenses apply as written'}",
            f'- Base: {fork}' + (' (the empty tree: a surface has no before)' if surface else ''),
            f'- Head: {head}',
            *([f"- Surface: {' '.join(paths)}"] if surface else []),
            f'- Change: `{change}` ({len(changed)} files{left_out})',
            *(['', 'This is a surface grade. Treat every function in the change as if the diff introduced it. Tests under '
                   'the surface are left out of the change but are in your proof copy: read them as evidence.']
              if surface else []),
            '',
            'Settled at the head, do not re-run: ' + (args.settled or 'nothing was given, so run only what a proof needs, '
                                                      'never the whole suite.'),
            '',
            'Declined review findings, with reasons: ' + (args.declined or 'none.'),
            '',
            ('Callers of the functions the surface defines, outside the surface. Hits inside it, and in tests, are counted '
             'rather than listed: you read the surface already. ' if surface else 'Callers of what the branch modified. ')
            + 'The list is a floor, never a ceiling: it was found with '
            f"`git -C {root} grep -n -a -F -w -e '<name>' {head} -- .` outside vendored directories, with hits in the "
            'paths `notCode` names dropped (documentation is not a caller), covers functions only, and cannot see a '
            're-export, dynamic dispatch, or a name built from a string.',
            *caller_lines,
            *(['', 'Proof copies:', *[f'- {line}' for line in copy_notes]] if copy_notes else []),
            *(['', f'No grader holds {old_state}, so every grader reads what history_replay.py found:', *replayed]
              if replayed and holder is None else []),
            *(['', f'No grader holds {near_miss_lens}, so every grader reads what near_miss.py found:', *near_miss_lines]
              if near_miss_lines and near_miss_holder is None else []),
            *(['', f'No grader holds {ambient_lens}, so every grader reads what ambient.py found:', *ambient_lines]
              if ambient_lines and ambient_holder is None else []),
        ])
        blocks = {}
        for name, ids in groups.items():
            block = [f"Apply pr-grade lenses {', '.join(ids)} to the change in the shared block.",
                     f"- Your proof copy: {fields[f'copy {name}']} (a clone of the head commit, yours alone; not the repository)",
                     f"- Your scratch directory: {fields[f'scratch {name}']}",
                     f"- Your proofs directory, for each proof test you keep: {fields[f'proofs {name}']}",
                     # Claude Code refuses a subagent's Write to a file named report*.md, summary*.md, and the like.
                     f'- Write your report to: {proof_root / name / "grade.md"}']
            if 'L7' in ids:
                block += ['', 'check_then_act.py at the head:', l7]
            if replayed and name == holder:
                block += ['', f'history_replay.py at the head, for {old_state}:', *replayed]
            if near_miss_lines and name == near_miss_holder:
                block += ['', f'near_miss.py at the head, for {near_miss_lens}:', *near_miss_lines]
            if ambient_lines and name == ambient_holder:
                block += ['', f'ambient.py at the head, for {ambient_lens}:', *ambient_lines]
            blocks[name] = '\n'.join(block)
        if prompt_files and mode != 'in-thread':
            # Each prompt is the shared block and the grader's own, as a coordinator pasted them, so the grader is
            # told the same text and the coordinator retypes none of it. Then the lens text for its lenses, so it
            # reads neither source, and the line names the diff so the first turn reads the file and the change.
            print('\n===== dispatch =====\n')
            for name, block in blocks.items():
                path = proof_root / name / 'prompt.md'
                carried = prompt_lens_text(groups[name], skill, skill_path, lens_text, lens_path) if slicing else ''
                path.write_text(f'{shared}\n\n{block}\n{carried}')
                print(f'Read your pr-grade dispatch from {path} and run `{change}` in your first turn, in parallel, '
                      'then grade exactly as it says.')
        else:
            print('\n===== shared block =====\n')
            print(shared)
            for name, block in blocks.items():
                print(f'\n===== {name} =====\n')
                print(block)
        if mode != 'in-thread':
            print(f"\nmerge: python3 {HERE / 'grade_block.py'} merge {proof_root}")
        print(f"remove: {fields['remove']}")
    except BaseException:
        try:
            proof_dir.remove(proof_root, drop_proofs=True)
        except Exception as failed:  # noqa: BLE001
            # The interrupt or fault that stopped the prep is the error to show, not a cleanup that could not run.
            print(f'grade_prep: could not remove {proof_root}: {failed}', file=sys.stderr)
        raise


if __name__ == '__main__':
    main()
