#!/usr/bin/env python3
"""Unit tests for grade_mode.py: which changes grade in-thread, in a subagent, or fanned out.

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'skills/pr-grade/scripts'))
import grade_mode  # noqa: E402

LEAF = [f'src/leaf_{n}.py' for n in range(6)]


def git(root: Path, *args: str) -> None:
    subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args], cwd=root, check=True,
                   capture_output=True)


class Fixture(unittest.TestCase):
    """A scratch repository with an optional `.claude/pr-grade.json`."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, 'init', '-q', '-b', 'main')

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def configure(self, **config) -> None:
        (self.root / '.claude').mkdir(exist_ok=True)
        (self.root / grade_mode.CONFIG).write_text(json.dumps(config))

    def assess(self, changed: list[str]) -> tuple[str, dict[str, str], list[str]]:
        return grade_mode.assess(changed, self.root)


class ModeTest(Fixture):
    def test_four_leaf_files_grade_in_thread(self) -> None:
        self.assertEqual(self.assess(LEAF[:4])[0], 'in-thread')

    def test_five_leaf_files_grade_in_a_subagent(self) -> None:
        self.assertEqual(self.assess(LEAF[:5])[0], 'subagent')

    def test_one_silent_file_grades_in_a_subagent(self) -> None:
        self.assertEqual(self.assess(['.github/workflows/ci.yml'])[0], 'subagent')

    def test_silent_code_over_the_threshold_fans_out(self) -> None:
        self.assertEqual(self.assess(['.github/workflows/ci.yml', *LEAF[:4]])[0], 'fan-out')

    def test_the_threshold_is_configurable(self) -> None:
        self.configure(fanOutAbove=1)
        self.assertEqual(self.assess(['.github/workflows/ci.yml', LEAF[0]])[0], 'fan-out')

    def test_tests_docs_and_lockfiles_do_not_count_toward_size(self) -> None:
        changed = ['.github/workflows/ci.yml', 'tests/a_test.py', 'src/b.test.ts', 'src/test_c.py', 'docs/guide.txt',
                   'README.md', 'package-lock.json', 'Cargo.lock']
        self.assertEqual(self.assess(changed)[0], 'subagent')


class SilentTest(Fixture):
    def test_default_silent_paths(self) -> None:
        for path in ('.github/workflows/ci.yml', '.husky/pre-push', '.claude/settings.json', 'db/migrations/001.sql'):
            with self.subTest(path=path):
                self.assertIn(path, self.assess([path])[1])

    def test_configured_patterns_replace_the_defaults(self) -> None:
        self.configure(silent=['src/gates/*'])
        reasons = self.assess(['src/gates/deep/check.py', '.github/workflows/ci.yml'])[1]
        self.assertEqual(set(reasons), {'src/gates/deep/check.py'})

    def test_a_silent_pattern_covers_tests_too(self) -> None:
        # An architecture test is itself a gate; a broken one passes on nothing.
        self.configure(silent=['tests/architecture/*'])
        self.assertIn('tests/architecture/owners.test.ts', self.assess(['tests/architecture/owners.test.ts'])[1])

    def test_silent_command_names_more_files(self) -> None:
        self.configure(silentCommand='printf "src/store.py\\nsrc/other.py\\n"')
        self.assertEqual(self.assess(['src/store.py', 'src/leaf.py'])[1], {'src/store.py': 'named by silentCommand'})

    def test_a_failing_silent_command_stops_the_run(self) -> None:
        # A mode picked without it could be too cheap.
        self.configure(silentCommand='exit 3')
        with self.assertRaises(subprocess.CalledProcessError):
            self.assess(['src/leaf.py'])


class CoverageTest(Fixture):
    def test_the_grade_covers_code_and_silent_tests(self) -> None:
        self.configure(silent=['tests/architecture/*'])
        changed = ['tests/architecture/owners.test.ts', 'tests/unit/a.test.ts', 'docs/a.md', 'src/a.py']
        self.assertEqual(self.assess(changed)[2], ['tests/architecture/owners.test.ts', 'src/a.py'])


class ChangedFilesTest(Fixture):
    def test_committed_uncommitted_and_untracked_work_is_included(self) -> None:
        (self.root / 'base.py').write_text('1\n')
        (self.root / 'edited.py').write_text('1\n')
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-q', '-m', 'base')
        git(self.root, 'checkout', '-q', '-b', 'topic')
        (self.root / 'committed.py').write_text('1\n')
        git(self.root, 'add', 'committed.py')
        git(self.root, 'commit', '-q', '-m', 'topic')
        (self.root / 'edited.py').write_text('2\n')
        (self.root / 'untracked.py').write_text('1\n')
        self.assertEqual(sorted(grade_mode.changed_files('main', self.root)),
                         ['committed.py', 'edited.py', 'untracked.py'])


if __name__ == '__main__':
    unittest.main()
