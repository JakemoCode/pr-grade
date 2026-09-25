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
        for path in ('.github/workflows/ci.yml', '.husky/pre-push', '.claude/settings.json', 'db/migrations/001.sql',
                     'migrations/001.sql', '.claude/pr-grade.json', '.claude/pr-grade-lenses.md'):
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

    def test_a_failing_silent_command_stops_the_run_with_its_message(self) -> None:
        # A mode picked without it could be too cheap, and the command knows how to fix itself.
        self.configure(silentCommand="echo 'build the venv first' >&2; exit 3")
        with self.assertRaises(SystemExit) as stopped:
            self.assess(['src/leaf.py'])
        self.assertEqual(str(stopped.exception.code), 'silentCommand failed (exit 3): build the venv first')


class CoverageTest(Fixture):
    def test_the_grade_covers_everything_but_not_code(self) -> None:
        # A test weakened after the grade can undo the proof a finding rested on.
        changed = ['tests/unit/a.test.ts', 'docs/a.md', 'src/a.py', 'package-lock.json']
        self.assertEqual(self.assess(changed)[2], ['tests/unit/a.test.ts', 'src/a.py'])

    def test_a_silent_file_among_not_code_is_covered(self) -> None:
        self.assertEqual(self.assess(['.claude/pr-grade-lenses.md', 'docs/a.md'])[2], ['.claude/pr-grade-lenses.md'])


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


class ChangedLinesTest(Fixture):
    def setUp(self) -> None:
        super().setUp()
        (self.root / 'a.py').write_text(''.join(f'line {n}\n' for n in range(1, 11)))
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-q', '-m', 'base')
        git(self.root, 'checkout', '-q', '-b', 'topic')

    def edit(self, change) -> None:
        lines = (self.root / 'a.py').read_text().splitlines(keepends=True)
        change(lines)
        (self.root / 'a.py').write_text(''.join(lines))

    def test_committed_and_uncommitted_edits_give_their_new_side_lines(self) -> None:
        self.edit(lambda lines: lines.__setitem__(2, 'changed 3\n'))
        git(self.root, 'commit', '-q', '-am', 'edit 3')
        self.edit(lambda lines: lines.__setitem__(7, 'changed 8\n'))
        self.assertEqual(grade_mode.changed_lines('main', self.root), {'a.py': [(3, 3), (8, 8)]})

    def test_an_insertion_spans_the_inserted_lines(self) -> None:
        self.edit(lambda lines: lines.insert(4, 'new a\nnew b\n'))
        self.assertEqual(grade_mode.changed_lines('main', self.root), {'a.py': [(5, 6)]})

    def test_a_pure_deletion_marks_the_lines_either_side(self) -> None:
        # Removing a re-check or a lock changes the function around it.
        self.edit(lambda lines: lines.__delitem__(4))
        self.assertEqual(grade_mode.changed_lines('main', self.root), {'a.py': [(4, 5)]})

    def test_an_untracked_file_is_wholly_changed(self) -> None:
        (self.root / 'new.py').write_text('x\n')
        self.assertEqual(grade_mode.changed_lines('main', self.root), {'new.py': None})

    def test_names_with_spaces_and_non_ascii_come_back_as_they_are_on_disk(self) -> None:
        # git C-quotes non-ASCII names and appends a tab to names with spaces unless told otherwise.
        for name in ('my file.py', 'café.py'):
            (self.root / name).write_text('1\n')
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-q', '-m', 'odd names')
        for name in ('my file.py', 'café.py'):
            (self.root / name).write_text('2\n')
        self.assertEqual(grade_mode.changed_lines('main', self.root), {'café.py': [(1, 1)], 'my file.py': [(1, 1)]})
        self.assertEqual(sorted(grade_mode.changed_files('main', self.root)), ['café.py', 'my file.py'])

    def test_an_added_line_that_looks_like_a_file_header_is_content(self) -> None:
        self.edit(lambda lines: lines.insert(2, '++ b/elsewhere.py\n'))
        self.assertEqual(grade_mode.changed_lines('main', self.root), {'a.py': [(3, 3)]})


if __name__ == '__main__':
    unittest.main()
