#!/usr/bin/env python3
"""Unit tests for .github/scripts/check_release.py: a change that ships needs a new version.

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / '.github/scripts'))
import check_release  # noqa: E402


def git(root: Path, *args: str) -> None:
    subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args], cwd=root, check=True,
                   capture_output=True)


class ReleaseTest(unittest.TestCase):
    """A scratch plugin at 0.3.1 on `main`, with a `topic` branch checked out."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, 'init', '-q', '-b', 'main')
        self.write('.claude-plugin/plugin.json', json.dumps({'name': 'p', 'version': '0.3.1'}))
        self.write('CHANGELOG.md', '# Changelog\n\n## 0.3.1\n\n- A fix.\n')
        self.write('skills/p/SKILL.md', 'skill\n')
        self.write('README.md', 'readme\n')
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-q', '-m', 'base')
        git(self.root, 'checkout', '-q', '-b', 'topic')

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, path: str, text: str) -> None:
        (self.root / path).parent.mkdir(parents=True, exist_ok=True)
        (self.root / path).write_text(text)

    def release(self, version: str, heading: bool = True) -> None:
        self.write('.claude-plugin/plugin.json', json.dumps({'name': 'p', 'version': version}))
        if heading:
            self.write('CHANGELOG.md', f'# Changelog\n\n## {version}\n\n- More.\n\n## 0.3.1\n\n- A fix.\n')

    def problems(self, hold: bool = False) -> list[str]:
        return check_release.problems(self.root, 'main', hold=hold)

    def test_a_skill_change_without_a_new_version_is_refused(self) -> None:
        self.write('skills/p/SKILL.md', 'changed\n')
        found = self.problems()
        self.assertEqual(len(found), 1)
        self.assertIn('skills/p/SKILL.md', found[0])
        self.assertIn('0.3.1', found[0])

    def test_an_agent_change_without_a_new_version_is_refused(self) -> None:
        self.write('agents/grade.md', 'new\n')
        self.assertEqual(len(self.problems()), 1)

    def test_a_skill_moved_out_of_skills_is_refused(self) -> None:
        git(self.root, 'mv', 'skills/p/SKILL.md', 'README.skill.md')
        found = self.problems()
        self.assertEqual(len(found), 1)
        self.assertIn('skills/p/SKILL.md', found[0])

    def test_a_new_version_with_its_heading_passes(self) -> None:
        self.write('skills/p/SKILL.md', 'changed\n')
        self.release('0.3.2')
        self.assertEqual(self.problems(), [])

    def test_a_new_version_needs_its_changelog_heading(self) -> None:
        self.write('skills/p/SKILL.md', 'changed\n')
        self.release('0.3.2', heading=False)
        found = self.problems()
        self.assertEqual(len(found), 1)
        self.assertIn('## 0.3.2', found[0])

    def test_a_heading_that_only_starts_with_the_version_does_not_count(self) -> None:
        self.release('0.3.2', heading=False)
        self.write('CHANGELOG.md', '# Changelog\n\n## 0.3.21\n\n## 0.3.1\n')
        self.assertEqual(len(self.problems()), 1)

    def test_a_version_that_does_not_rise_is_refused(self) -> None:
        self.release('0.3.0')
        found = self.problems()
        self.assertEqual(len(found), 1)
        self.assertIn('0.3.1 to 0.3.0', found[0])

    def test_versions_compare_as_numbers(self) -> None:
        self.release('0.10.0')
        self.assertEqual(self.problems(), [])

    def test_a_version_that_is_not_three_numbers_is_refused(self) -> None:
        self.release('0.4')
        self.assertIn('0.4', self.problems()[0])

    def test_a_change_that_ships_nothing_needs_no_version(self) -> None:
        self.write('README.md', 'changed\n')
        self.write('tests/test_x.py', 'x = 1\n')
        self.write('.github/workflows/test.yml', 'on: push\n')
        self.assertEqual(self.problems(), [])

    def test_a_held_release_passes_without_a_version(self) -> None:
        self.write('skills/p/SKILL.md', 'changed\n')
        self.assertEqual(self.problems(hold=True), [])

    def test_a_held_release_still_checks_a_version_it_does_change(self) -> None:
        self.release('0.3.0')
        self.assertEqual(len(self.problems(hold=True)), 1)

    def test_committed_work_counts(self) -> None:
        self.write('skills/p/SKILL.md', 'changed\n')
        git(self.root, 'commit', '-q', '-am', 'change')
        self.assertEqual(len(self.problems()), 1)

    def test_a_base_that_moved_on_counts_only_the_branch(self) -> None:
        git(self.root, 'checkout', '-q', 'main')
        self.write('skills/p/SKILL.md', 'released on main\n')
        self.release('0.3.2')
        git(self.root, 'commit', '-q', '-am', 'release on main')
        git(self.root, 'checkout', '-q', 'topic')
        self.write('README.md', 'changed\n')
        self.assertEqual(self.problems(), [])


if __name__ == '__main__':
    unittest.main()
