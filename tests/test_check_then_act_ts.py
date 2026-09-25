#!/usr/bin/env python3
"""check_then_act.py through its TypeScript adapter: the fixtures, compiler resolution, and the CLI.

Needs Node and a TypeScript package named by PR_GRADE_TYPESCRIPT. Without them the suite skips, unless
PR_GRADE_REQUIRE_TS=1, which CI sets so a missing compiler fails instead of passing on a silent skip.

    PR_GRADE_TYPESCRIPT=<path>/node_modules/typescript python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / 'skills/pr-grade/scripts'
sys.path.insert(0, str(SCRIPTS))
import check_then_act as cta  # noqa: E402

FIXTURES = REPO / 'tests/fixtures/check_then_act/ts'
TYPESCRIPT = os.environ.get('PR_GRADE_TYPESCRIPT')
AVAILABLE = bool(shutil.which('node') and TYPESCRIPT and Path(TYPESCRIPT).exists())
MARKER = re.compile(r'// expect: (check|write|gap (\S+))\s*$')


def setUpModule() -> None:
    if not AVAILABLE and os.environ.get('PR_GRADE_REQUIRE_TS') == '1':
        raise RuntimeError('PR_GRADE_REQUIRE_TS=1 but Node or PR_GRADE_TYPESCRIPT is missing')


def expected(fixture: Path) -> tuple | None:
    """The one candidate a fixture's `// expect:` markers describe, or None when it has none."""
    checks, gaps, writes = [], [], []
    for number, line in enumerate(fixture.read_text().splitlines(), 1):
        marker = MARKER.search(line)
        if marker is None:
            continue
        if marker[1] == 'check':
            checks.append(number)
        elif marker[1] == 'write':
            writes.append(number)
        else:
            gaps.append((number, marker[2]))
    return (checks, gaps, writes) if checks or gaps or writes else None


def shape(candidate: dict) -> tuple:
    return ([c['line'] for c in candidate['checks']], [(g['line'], g['kind']) for g in candidate['gaps']],
            [w['line'] for w in candidate['writes']])


def candidates_for(fixture: Path, cfg: dict = cta.DEFAULTS) -> list[dict]:
    relative = fixture.relative_to(REPO).as_posix()
    parsed, skipped, _ = cta.parse_typescript([relative], REPO)
    if skipped:
        raise AssertionError(f'{relative} was not parsed: {skipped}')
    return [c for fn in parsed[relative] for c in cta.analyze(fn, cfg)]


def git(root: Path, *args: str) -> None:
    subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args], cwd=root, check=True,
                   capture_output=True)


def run_cli(root: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPTS / 'check_then_act.py'), *args], cwd=root, capture_output=True,
                          text=True, env=env if env is not None else os.environ.copy())


@unittest.skipUnless(AVAILABLE, 'needs node and PR_GRADE_TYPESCRIPT')
class FixtureTest(unittest.TestCase):
    def test_every_fixture_yields_exactly_its_marked_candidate(self) -> None:
        fixtures = sorted(FIXTURES.glob('*_fixture.ts'))
        self.assertGreaterEqual(len(fixtures), 12)
        for fixture in fixtures:
            if fixture.name == 'second_read_fixture.ts':
                continue
            with self.subTest(fixture=fixture.name):
                want = expected(fixture)
                self.assertEqual([shape(c) for c in candidates_for(fixture)], [] if want is None else [want])

    def test_a_second_read_is_a_gap_only_when_configured(self) -> None:
        fixture = FIXTURES / 'second_read_fixture.ts'
        self.assertEqual(candidates_for(fixture), [])
        configured = cta.cta_config({'checkThenAct': {'secondReads': ['callBilling']}})
        self.assertEqual([shape(c) for c in candidates_for(fixture, configured)], [expected(fixture)])

    def test_a_check_is_linked_to_the_write_on_the_same_store(self) -> None:
        [candidate] = candidates_for(FIXTURES / 'positive_await_fixture.ts')
        recorded = next(c for c in candidate['checks'] if 'recorded' in c['text'])
        self.assertEqual(recorded['sameStoreWrites'], [12])


class TempRepo(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, 'init', '-q', '-b', 'main')

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def env(self, typescript: bool = True) -> dict:
        env = os.environ.copy()
        env.pop('PR_GRADE_TYPESCRIPT', None)
        if typescript:
            env['PR_GRADE_TYPESCRIPT'] = TYPESCRIPT or ''
        return env


WINDOW = '''  {name}(repo: any, id: string) {{
    const value = repo.get(id);
    if (value) return;
    {gap}
    repo.update(id);
  }}
'''


def source(a_gap: str, b_gap: str = 'await repo.sync(id);') -> str:
    return ('export class Store {\n' + WINDOW.format(name='async a', gap=a_gap) + '\n'
            + WINDOW.format(name='async b', gap=b_gap) + '}\n')


@unittest.skipUnless(AVAILABLE, 'needs node and PR_GRADE_TYPESCRIPT')
class CliTest(TempRepo):
    def setUp(self) -> None:
        super().setUp()
        (self.root / 'store.ts').write_text(source(a_gap='repo.touch(id);'))
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-q', '-m', 'base')
        git(self.root, 'checkout', '-q', '-b', 'topic')
        # The branch adds an await to a(); b() already had one.
        (self.root / 'store.ts').write_text(source(a_gap='await repo.touch(id);'))

    def result(self, *args: str) -> dict:
        run = run_cli(self.root, '--base', 'main', '--json', *args, env=self.env())
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_the_diff_scope_reports_only_the_function_the_branch_changed(self) -> None:
        [candidate] = self.result()['candidates']
        self.assertEqual((candidate['function'], candidate['inDiff']), ('Store.a', ['gap']))

    def test_whole_reports_every_function_in_the_changed_files(self) -> None:
        self.assertEqual(sorted(c['function'] for c in self.result('--whole')['candidates']), ['Store.a', 'Store.b'])

    def test_an_untracked_file_counts_as_wholly_changed(self) -> None:
        (self.root / 'new.ts').write_text(source(a_gap='repo.touch(id);'))
        functions = sorted((c['file'], c['function']) for c in self.result()['candidates'])
        self.assertEqual(functions, [('new.ts', 'Store.b'), ('store.ts', 'Store.a')])

    def test_a_test_file_is_left_out_of_the_diff_scope(self) -> None:
        (self.root / 'store.test.ts').write_text(source(a_gap='await repo.touch(id);'))
        self.assertEqual([c['file'] for c in self.result()['candidates']], ['store.ts'])

    def test_the_text_output_says_what_the_list_is(self) -> None:
        run = run_cli(self.root, '--base', 'main', env=self.env())
        self.assertIn('Each is a window to judge, not a finding.', run.stdout)

    def test_code_in_a_language_without_an_adapter_is_reported_skipped(self) -> None:
        # Otherwise a branch that changes only such files reads exactly like a clean scan.
        for name in ('app.py', 'deploy.sh', 'Dockerfile', 'settings.yaml', '.nvmrc', 'types.d.ts'):
            (self.root / name).write_text('x\n')
        result = self.result()
        # Anything the scanner cannot read is named, whatever its language; a data file is not reported.
        self.assertEqual(sorted(s['file'] for s in result['skipped']), ['Dockerfile', 'app.py', 'deploy.sh'])
        self.assertEqual({s['reason'] for s in result['skipped']}, {cta.UNSUPPORTED})
        run = run_cli(self.root, '--base', 'main', env=self.env())
        self.assertIn('3 files were skipped and not scanned', run.stdout)

    def test_an_empty_new_file_in_another_language_is_reported_skipped(self) -> None:
        # Its diff has no hunks, so only the `diff --git` line names it.
        (self.root / 'empty.py').write_text('')
        git(self.root, 'add', 'empty.py')
        git(self.root, 'commit', '-q', '-m', 'empty')
        self.assertEqual(self.result()['skipped'], [{'file': 'empty.py', 'reason': cta.UNSUPPORTED}])

    def test_a_base_that_does_not_resolve_stops_with_a_message(self) -> None:
        run = run_cli(self.root, '--base', 'origin/missing', env=self.env())
        self.assertEqual(run.returncode, 1)
        self.assertIn('cannot diff against origin/missing', run.stderr)
        self.assertNotIn('Traceback', run.stderr)

    def test_a_path_outside_the_repository_stops_with_a_message(self) -> None:
        run = run_cli(self.root, '..', env=self.env())
        self.assertEqual(run.returncode, 1)
        self.assertIn('outside the repository', run.stderr)

    def test_a_missing_path_stops_with_a_message(self) -> None:
        run = run_cli(self.root, 'src/nope.ts', env=self.env())
        self.assertEqual(run.returncode, 1)
        self.assertIn('no such file or directory', run.stderr)


@unittest.skipUnless(AVAILABLE, 'needs node and PR_GRADE_TYPESCRIPT')
class ResolutionTest(TempRepo):
    def test_the_repository_typescript_wins_over_the_environment(self) -> None:
        modules = self.root / 'node_modules'
        modules.mkdir()
        (modules / 'typescript').symlink_to(Path(TYPESCRIPT).resolve())
        (self.root / 'a.ts').write_text(source(a_gap='repo.touch(id);'))
        env = self.env(typescript=False)
        env['PR_GRADE_TYPESCRIPT'] = '/nonexistent'
        run = run_cli(self.root, 'a.ts', '--json', env=env)
        self.assertEqual(run.returncode, 0, run.stderr)
        # The environment names a path that does not exist, so a compiler found at all came from the repository.
        # Node reports the symlink's target.
        found = json.loads(run.stdout)['typescript']
        self.assertIn(str(Path(TYPESCRIPT).resolve()), found or '')
        self.assertNotIn('/nonexistent', found or '')

    def test_without_typescript_the_files_are_skipped_with_a_notice(self) -> None:
        (self.root / 'a.ts').write_text(source(a_gap='repo.touch(id);'))
        run = run_cli(self.root, 'a.ts', '--json', env=self.env(typescript=False))
        self.assertEqual(run.returncode, 0)
        self.assertIn('no typescript package found', run.stderr)
        self.assertEqual(json.loads(run.stdout)['candidates'], [])


class AdapterOutputTest(TempRepo):
    def test_output_that_is_not_json_stops_with_a_message(self) -> None:
        # A NODE_OPTIONS preload that prints a banner is enough to cause it.
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        (bin_dir / 'node').write_text('#!/bin/sh\necho banner\n')
        (bin_dir / 'node').chmod(0o755)
        (self.root / 'a.ts').write_text('export const a = 1;\n')
        env = self.env(typescript=False)
        env['PATH'] = f"{bin_dir}{os.pathsep}{env['PATH']}"
        run = run_cli(self.root, 'a.ts', env=env)
        self.assertEqual(run.returncode, 1)
        self.assertIn('printed something other than JSON', run.stderr)
        self.assertNotIn('Traceback', run.stderr)


class PackagingTest(unittest.TestCase):
    def test_the_plugin_has_no_npm_runtime_dependency(self) -> None:
        self.assertFalse((REPO / 'package.json').exists())
        adapter = (SCRIPTS / 'check_then_act_ts.cjs').read_text()
        self.assertEqual(sorted(set(re.findall(r"require\('([^']+)'\)", adapter))), ['fs', 'path'])


if __name__ == '__main__':
    unittest.main()
