#!/usr/bin/env python3
"""Unit tests for grade_prep.py: one call that builds every grader's dispatch.

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

SCRIPTS = Path(__file__).resolve().parent.parent / 'skills/pr-grade/scripts'
sys.path.insert(0, str(SCRIPTS))
import grade_prep  # noqa: E402

LENSES = [f'L{n}' for n in range(1, 9)]


def git(root: Path, *args: str) -> str:
    return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


class GroupsTest(unittest.TestCase):
    def test_fan_out_without_a_table_uses_the_three_default_groups(self) -> None:
        self.assertEqual(grade_prep.groups_for('fan-out', LENSES, None, []), grade_prep.DEFAULT_GROUPS)

    def test_the_lens_files_table_names_the_groups(self) -> None:
        table = ('## Fan-out groups\n\n| Group | Lenses | Shared reading |\n|---|---|---|\n'
                 '| Timing | L1, L2, L7 | x |\n| Everything else | L3, L4, L5, L6, L8 | y |\n\n## Next\n')
        self.assertEqual(grade_prep.groups_for('fan-out', LENSES, table, []),
                         {'timing': ['L1', 'L2', 'L7'], 'everything-else': ['L3', 'L4', 'L5', 'L6', 'L8']})

    def test_a_lens_no_group_holds_gets_a_grader_of_its_own(self) -> None:
        # A lens the lens file added must not go ungraded because the default groups predate it.
        groups = grade_prep.groups_for('fan-out', [*LENSES, 'L9'], None, [])
        self.assertEqual(groups['other-lenses'], ['L9'])

    def test_a_group_naming_a_lens_the_file_lacks_stops_the_run(self) -> None:
        with self.assertRaises(SystemExit):
            grade_prep.groups_for('fan-out', LENSES, None, ['odd=L1,L12'])

    def test_a_lens_given_to_two_groups_stops_the_run(self) -> None:
        with self.assertRaises(SystemExit):
            grade_prep.groups_for('fan-out', LENSES, None, ['a=L1,L7', 'b=L7'])

    def test_two_groups_with_one_name_stop_the_run(self) -> None:
        with self.assertRaises(SystemExit):
            grade_prep.groups_for('fan-out', LENSES, None, ['Timing=L1', 'timing=L2'])

    def test_one_grader_holds_every_lens_below_fan_out(self) -> None:
        self.assertEqual(grade_prep.groups_for('subagent', LENSES, None, []), {'grade': LENSES})


class SymbolTest(unittest.TestCase):
    def test_what_a_caller_writes(self) -> None:
        cases = {'claim': 'claim', 'Store.claim': 'claim', 'Store.__init__': 'Store', 'Store.constructor': 'Store',
                 'run > items.map callback': None, '<anonymous>@12': None}
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(grade_prep.symbol(name), expected)


class CliTest(unittest.TestCase):
    """A Python repository whose branch changes `claim`, adds `fresh`, and touches a shell script."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.repo, self.proofs = base / 'repo', base / 'proofs'
        self.repo.mkdir()
        self.proofs.mkdir()
        git(self.repo, 'init', '-q', '-b', 'main')
        (self.repo / '.claude').mkdir()
        (self.repo / '.claude/pr-grade.json').write_text('{"silent": ["store.py"]}\n')
        (self.repo / 'store.py').write_text('def claim(x):\n    return x\n\n\ndef other():\n    return 1\n')
        (self.repo / 'app.py').write_text('from store import claim\n\nprint(claim(1))\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'base')
        git(self.repo, 'checkout', '-q', '-b', 'topic')
        (self.repo / 'store.py').write_text('def claim(x):\n    return x + 1\n\n\ndef other():\n    return 1\n\n\n'
                                            'def fresh():\n    return claim(2)\n')
        (self.repo / 'deploy.sh').write_text('echo hi\n')
        (self.repo / 'package-lock.json').write_text('{}\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'change')

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPTS / 'grade_prep.py'), '--base', 'main', *args], cwd=self.repo,
                              env={**os.environ, 'TMPDIR': str(self.proofs)}, capture_output=True, text=True)

    def prep(self, *args: str) -> str:
        return self.prep_with('--base', 'main', *args)

    def prep_with(self, *args: str) -> str:
        run = subprocess.run([sys.executable, str(SCRIPTS / 'grade_prep.py'), *args], cwd=self.repo,
                             env={**os.environ, 'TMPDIR': str(self.proofs)}, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        return run.stdout

    def test_it_lists_the_callers_of_a_modified_function_only(self) -> None:
        out = self.prep()
        self.assertIn('- `claim`, defined store.py:1: app.py:1, app.py:3, store.py:10', out)
        # `fresh` is new on the branch and `other` is untouched: neither has a caller worth walking.
        self.assertNotIn('`fresh`', out)
        self.assertNotIn('`other`', out)

    def test_code_it_cannot_parse_is_named_not_dropped(self) -> None:
        self.assertIn('- not derived for deploy.sh: no adapter reads this language; search by hand', self.prep())

    def test_the_change_command_leaves_out_lock_files_and_says_so(self) -> None:
        out = self.prep()
        self.assertRegex(out, r"diff [0-9a-f]{40} [0-9a-f]{40} -- \. ':!package-lock\.json'`")
        self.assertIn('lock files left out: package-lock.json', out)

    def test_settled_and_declined_go_into_the_shared_block_as_given(self) -> None:
        out = self.prep('--settled', 'pytest: 3 passed', '--declined', 'rename x: out of scope')
        self.assertIn('Settled at the head, do not re-run: pytest: 3 passed', out)
        self.assertIn('Declined review findings, with reasons: rename x: out of scope', out)

    def test_each_grader_gets_its_own_copy_and_report_path_and_the_l7_holder_gets_the_scan(self) -> None:
        out = self.prep('--groups', 'timing=L1,L2,L7', '--groups', 'rest=L3,L4,L5,L6,L8')
        copies = re.findall(r'- Your proof copy: (\S+)', out)
        self.assertEqual(len(set(copies)), 2)
        for copy in copies:
            self.assertEqual(git(Path(copy), 'rev-parse', 'HEAD').strip(), git(self.repo, 'rev-parse', 'HEAD').strip())
        timing, rest = out.split('===== timing =====')[1].split('===== rest =====')
        self.assertIn('check-then-act candidates (L7)', timing)
        self.assertNotIn('check-then-act candidates (L7)', rest)
        root = Path(re.search(r'^merge: .* merge (\S+)$', out, re.M).group(1))
        meta = json.loads((root / 'grade.json').read_text())
        self.assertEqual((meta['mode'], meta['groups']['rest']), ('subagent', ['L3', 'L4', 'L5', 'L6', 'L8']))
        self.assertIn(f"- Write your report to: {root / 'timing' / 'report.md'}", out)

    def test_the_printed_remove_line_removes_the_copies(self) -> None:
        out = self.prep()
        root = Path(re.search(r'^merge: .* merge (\S+)$', out, re.M).group(1))
        remove = re.search(r'^remove: (.+)$', out, re.M).group(1)
        subprocess.run(remove, shell=True, check=True, capture_output=True)
        self.assertFalse(root.exists())

    def test_the_prompt_carries_every_input_the_agent_description_names(self) -> None:
        # The words tests/test_grade_contract.py holds the agent, its Inputs, and the skill to.
        out = self.prep().lower()
        for word in ('repository', 'lens file', 'base', 'head', 'settled', 'declined', 'callers', 'check_then_act.py',
                     'proof copy', 'report'):
            with self.subTest(word=word):
                self.assertIn(word, out)

    def test_callers_found_by_hand_join_the_list(self) -> None:
        self.assertIn('- `Mode` type: cli.py:4', self.prep('--callers', '`Mode` type: cli.py:4'))

    def test_two_modified_functions_with_one_name_both_count_as_definitions(self) -> None:
        git(self.repo, 'checkout', '-q', 'main')
        (self.repo / 'other.py').write_text('def claim(y):\n    return y\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'second claim')
        git(self.repo, 'checkout', '-q', 'topic')
        git(self.repo, 'merge', '-q', '--no-edit', 'main')
        (self.repo / 'other.py').write_text('def claim(y):\n    return y * 2\n')
        git(self.repo, 'commit', '-q', '-am', 'change the other claim')
        line = next(l for l in self.prep().splitlines() if l.startswith('- `claim`'))
        self.assertIn('defined other.py:1, store.py:1:', line)
        self.assertNotIn('other.py:1,', line.split(': ', 1)[1])

    def test_a_file_the_author_never_added_is_left_out(self) -> None:
        (self.repo / 'scratch.py').write_text('def claim(z):\n    return z\n')
        out = self.prep()
        self.assertNotIn('scratch.py', out)
        self.assertIn('(3 files;', out)

    def test_in_thread_prints_no_merge_line(self) -> None:
        (self.repo / '.claude/pr-grade.json').write_text('{"silent": []}\n')
        git(self.repo, 'commit', '-q', '-am', 'nothing silent')
        out = self.prep()
        self.assertIn('mode: in-thread', out)
        self.assertNotIn('merge:', out)
        self.assertIn('remove:', out)

    def test_a_regrade_keeps_the_whole_branchs_mode_for_the_block(self) -> None:
        graded = git(self.repo, 'rev-parse', 'HEAD').strip()
        (self.repo / 'README.md').write_text('notes\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'docs only')
        out = self.prep_with('--base', graded, '--branch-base', 'main')
        self.assertIn('mode: in-thread', out)
        self.assertIn('the grade block keeps subagent', out)
        root = Path(re.search(r'^remove: .* remove (\S+)$', out, re.M).group(1))
        self.assertEqual(json.loads((root / 'grade.json').read_text())['mode'], 'subagent')

    def test_uncommitted_edits_stop_it_before_anything_is_made(self) -> None:
        (self.repo / 'store.py').write_text('changed\n')
        run = self.run_cli()
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('uncommitted edits', run.stderr)
        self.assertEqual(list(self.proofs.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
