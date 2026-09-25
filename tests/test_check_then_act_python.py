#!/usr/bin/env python3
"""check_then_act.py through its Python adapter: the fixtures and the CLI. Needs nothing beyond python3.

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / 'skills/pr-grade/scripts'
sys.path.insert(0, str(SCRIPTS))
import check_then_act as cta  # noqa: E402

FIXTURES = REPO / 'tests/fixtures/check_then_act/py'
MARKER = re.compile(r'# expect: (check|write|gap (\S+))\s*$')
CONFIGURED = {'second_read_fixture.py': {'secondReads': ['call_billing']}}


def expected(fixture: Path) -> tuple | None:
    """The one candidate a fixture's `# expect:` markers describe, or None when it has none."""
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
    parsed, skipped = cta.parse_python([relative], REPO)
    if skipped:
        raise AssertionError(f'{relative} was not parsed: {skipped}')
    return [c for fn in parsed[relative] for c in cta.analyze(fn, cfg)]


class FixtureTest(unittest.TestCase):
    def test_every_fixture_yields_exactly_its_marked_candidate(self) -> None:
        fixtures = sorted(FIXTURES.glob('*_fixture.py'))
        self.assertGreaterEqual(len(fixtures), 1)
        for fixture in fixtures:
            cfg = cta.cta_config({'checkThenAct': CONFIGURED.get(fixture.name, {})})
            with self.subTest(fixture=fixture.name):
                want = expected(fixture)
                self.assertEqual([shape(c) for c in candidates_for(fixture, cfg)], [] if want is None else [want])


def git(root: Path, *args: str) -> None:
    subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args], cwd=root, check=True,
                   capture_output=True)


WINDOW = '''async def {name}(repo, item_id):
    value = repo.get(item_id)
    if value:
        return
    {gap}
    repo.update(item_id)

'''


def source(a_gap: str) -> str:
    return WINDOW.format(name='a', gap=a_gap) + WINDOW.format(name='b', gap='await repo.sync(item_id)')


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, 'init', '-q', '-b', 'main')
        (self.root / 'store.py').write_text(source(a_gap='repo.touch(item_id)'))
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-q', '-m', 'base')
        git(self.root, 'checkout', '-q', '-b', 'topic')
        # The branch adds an await to a(); b() already had one.
        (self.root / 'store.py').write_text(source(a_gap='await repo.touch(item_id)'))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPTS / 'check_then_act.py'), '--base', 'main', *args],
                              cwd=self.root, capture_output=True, text=True)

    def result(self, *args: str) -> dict:
        run = self.run_cli('--json', *args)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def test_the_diff_scope_reports_only_the_python_function_the_branch_changed(self) -> None:
        result = self.result()
        self.assertEqual(result['skipped'], [])
        [candidate] = result['candidates']
        self.assertEqual((candidate['file'], candidate['language'], candidate['function'], candidate['inDiff']),
                         ('store.py', 'python', 'a', ['gap']))

    def test_whole_reports_every_function_in_the_changed_files(self) -> None:
        self.assertEqual(sorted(c['function'] for c in self.result('--whole')['candidates']), ['a', 'b'])

    def test_a_file_this_python_cannot_parse_is_skipped_with_the_reason(self) -> None:
        (self.root / 'broken.py').write_text('def f(:\n    pass\n')
        (self.root / 'stubs.pyi').write_text('def f() -> int: ...\n')
        [skip] = self.result()['skipped']
        self.assertEqual(skip['file'], 'broken.py')
        self.assertIn('cannot parse it', skip['reason'])
        self.assertIn('check_then_act: skipped broken.py', self.run_cli().stderr)


if __name__ == '__main__':
    unittest.main()
