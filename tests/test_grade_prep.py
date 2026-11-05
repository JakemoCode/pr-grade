#!/usr/bin/env python3
"""Unit tests for grade_prep.py: one call that builds every grader's dispatch.

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / 'skills/pr-grade/scripts'
sys.path.insert(0, str(SCRIPTS))
import grade_prep  # noqa: E402

LENSES = [f'L{n}' for n in range(1, 11)]


def git(root: Path, *args: str) -> str:
    return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


class GroupsTest(unittest.TestCase):
    def test_fan_out_without_a_table_uses_the_three_default_groups(self) -> None:
        self.assertEqual(grade_prep.groups_for('fan-out', LENSES, None, []), grade_prep.DEFAULT_GROUPS)

    def test_the_lens_files_table_names_the_groups(self) -> None:
        table = ('## Fan-out groups\n\n| Group | Lenses | Shared reading |\n|---|---|---|\n'
                 '| Timing | L1, L2, L7 | x |\n| Everything else | L3, L4, L5, L6, L8, L9, L10 | y |\n\n## Next\n')
        self.assertEqual(grade_prep.groups_for('fan-out', LENSES, table, []),
                         {'timing': ['L1', 'L2', 'L7'], 'everything-else': ['L3', 'L4', 'L5', 'L6', 'L8', 'L9', 'L10']})

    def test_a_lens_no_group_holds_gets_a_grader_of_its_own(self) -> None:
        # A lens the lens file added must not go ungraded because the default groups predate it.
        groups = grade_prep.groups_for('fan-out', [*LENSES, 'L11'], None, [])
        self.assertEqual(groups['other-lenses'], ['L11'])

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

    def test_a_skipped_lens_reaches_no_group_and_a_group_it_empties_is_dropped(self) -> None:
        self.assertEqual(grade_prep.groups_for('fan-out', LENSES, None, [], ['L2', 'L3', 'L4', 'L10']),
                         {'timing': ['L1', 'L7'], 'lifetime-and-contract': ['L5', 'L6', 'L8', 'L9']})

    def test_a_skipped_lens_leaves_the_lens_files_table_too(self) -> None:
        table = '## Fan-out groups\n\n| Group | Lenses |\n|---|---|\n| Timing | L1, L2, L7 |\n| Rest | L3, L4, L5, L6, L8, L9, L10 |\n'
        self.assertEqual(grade_prep.groups_for('fan-out', LENSES, table, [], ['L2']),
                         {'timing': ['L1', 'L7'], 'rest': ['L3', 'L4', 'L5', 'L6', 'L8', 'L9', 'L10']})

    def test_one_grader_holds_every_lens_not_skipped(self) -> None:
        self.assertEqual(grade_prep.groups_for('subagent', LENSES, None, [], ['L4']),
                         {'grade': ['L1', 'L2', 'L3', 'L5', 'L6', 'L7', 'L8', 'L9', 'L10']})

    def test_a_given_group_naming_a_skipped_lens_stops_the_run(self) -> None:
        with self.assertRaises(SystemExit) as stopped:
            grade_prep.groups_for('fan-out', LENSES, None, ['timing=L1,L2'], ['L2'])
        self.assertIn('L2', str(stopped.exception.code))

    def test_a_skipped_lens_left_out_of_given_groups_gets_no_grader(self) -> None:
        self.assertEqual(grade_prep.groups_for('fan-out', LENSES, None, ['timing=L1,L7'], ['L2', 'L3']),
                         {'timing': ['L1', 'L7'], 'other-lenses': ['L4', 'L5', 'L6', 'L8', 'L9', 'L10']})


class SkipTest(unittest.TestCase):
    def test_skipped_lenses_come_back_in_the_lens_files_order(self) -> None:
        self.assertEqual(grade_prep.skipped_lenses(['L5,L2', 'L3'], LENSES), ['L2', 'L3', 'L5'])

    def test_spaces_after_the_commas_are_allowed(self) -> None:
        self.assertEqual(grade_prep.skipped_lenses(['L2, L3'], LENSES), ['L2', 'L3'])

    def test_nothing_given_skips_nothing(self) -> None:
        self.assertEqual(grade_prep.skipped_lenses([], LENSES), [])

    def test_a_lens_the_file_does_not_define_stops_the_run(self) -> None:
        with self.assertRaises(SystemExit) as stopped:
            grade_prep.skipped_lenses(['L2,L12'], LENSES)
        self.assertIn('L12', str(stopped.exception.code))

    def test_a_word_that_is_not_a_lens_id_stops_the_run(self) -> None:
        # `--skip timing` must not read as skipping nothing.
        with self.assertRaises(SystemExit) as stopped:
            grade_prep.skipped_lenses(['timing'], LENSES)
        self.assertIn('timing', str(stopped.exception.code))

    def test_skipping_every_lens_stops_the_run(self) -> None:
        with self.assertRaises(SystemExit):
            grade_prep.skipped_lenses([','.join(LENSES)], LENSES)


class SymbolTest(unittest.TestCase):
    def test_what_a_caller_writes(self) -> None:
        cases = {'claim': 'claim', 'Store.claim': 'claim', 'Store.__init__': 'Store', 'Store.constructor': 'Store',
                 'run > items.map callback': None, '<anonymous>@12': None, 'main': None, 'Store.__repr__': None}
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
        (self.repo / 'NOTES.md').write_text('Always claim before you release.\n')
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

    def test_prose_that_uses_a_function_name_is_not_a_caller(self) -> None:
        # NOTES.md writes "claim" as a word; a hit under `notCode` is dropped, as the change command leaves it out.
        self.assertNotIn('NOTES.md', self.prep())

    def test_a_file_countascode_names_is_searched_though_notcode_covers_it(self) -> None:
        (self.repo / 'docs/tools').mkdir(parents=True)
        (self.repo / 'docs/tools/run.py').write_text('from store import claim\n\nclaim(3)\n')
        (self.repo / 'docs/tools/run.md').write_text('claim\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'docs tool')
        config = {**grade_prep.grade_mode.DEFAULTS, 'countAsCode': ['docs/tools/*.py']}
        head = git(self.repo, 'rev-parse', 'HEAD').strip()
        [line] = grade_prep.callers(self.repo, head, {'claim': ['store.py:1']}, config)
        self.assertIn('docs/tools/run.py:3', line)
        self.assertNotIn('run.md', line)

    def test_a_name_with_a_dollar_sign_is_matched_as_a_fixed_string(self) -> None:
        (self.repo / 'app.js').write_text('const items$ = load();\nitems$();\n')
        (self.repo / 'lib.js').write_text('const items = [];\nreturn items\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'js')
        head = git(self.repo, 'rev-parse', 'HEAD').strip()
        [line] = grade_prep.callers(self.repo, head, {'items$': ['app.js:1']}, grade_prep.grade_mode.DEFAULTS)
        self.assertEqual(line, '- `items$`, defined app.js:1: app.js:2')

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
        out = self.prep('--groups', 'timing=L1,L2,L7', '--groups', 'rest=L3,L4,L5,L6,L8,L9,L10')
        copies = re.findall(r'- Your proof copy: (\S+)', out)
        self.assertEqual(len(set(copies)), 2)
        for copy in copies:
            self.assertEqual(git(Path(copy), 'rev-parse', 'HEAD').strip(), git(self.repo, 'rev-parse', 'HEAD').strip())
        timing, rest = out.split('===== timing =====')[1].split('===== rest =====')
        self.assertIn('check-then-act candidates (L7)', timing)
        self.assertNotIn('check-then-act candidates (L7)', rest)
        root = Path(re.search(r'^merge: .* merge (\S+)$', out, re.M).group(1))
        meta = json.loads((root / 'grade.json').read_text())
        self.assertEqual((meta['mode'], meta['groups']['rest']), ('subagent', ['L3', 'L4', 'L5', 'L6', 'L8', 'L9', 'L10']))
        self.assertIn(f"- Write your report to: {root / 'timing' / 'grade.md'}", out)
        self.assertIn(f"- Your proofs directory, for each proof test you keep: {root / 'timing' / 'proofs'}", out)

    def test_a_skipped_lens_gets_no_grader_and_no_proof_copy(self) -> None:
        out = self.prep('--groups', 'timing=L1,L7', '--groups', 'rest=L4,L5,L6,L8,L9,L10', '--skip', 'L2,L3')
        self.assertEqual(len(re.findall(r'- Your proof copy: (\S+)', out)), 2)
        self.assertNotIn('===== other-lenses =====', out)
        self.assertIn('skipped: L2, L3', out)
        root = Path(re.search(r'^merge: .* merge (\S+)$', out, re.M).group(1))
        meta = json.loads((root / 'grade.json').read_text())
        self.assertEqual((meta['skipped'], list(meta['groups'])), (['L2', 'L3'], ['timing', 'rest']))
        self.assertEqual(meta['lenses'], LENSES)

    def test_without_skip_grade_json_records_none_skipped(self) -> None:
        root = Path(re.search(r'^merge: .* merge (\S+)$', self.prep(), re.M).group(1))
        self.assertEqual(json.loads((root / 'grade.json').read_text())['skipped'], [])

    def test_a_bad_skip_stops_the_run_before_any_copy_is_made(self) -> None:
        run = self.run_cli('--skip', 'L12')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('L12', run.stderr)
        self.assertEqual(list(self.proofs.iterdir()), [])

    def test_a_subagent_may_write_to_the_report_path(self) -> None:
        # Claude Code 2.1.286 refuses a subagent's Write to a basename matching this, case-insensitively.
        refused = re.compile(r'^(REPORT|SUMMARY|FINDINGS|ANALYSIS).*\.md$', re.I)
        paths = re.findall(r'^- Write your report to: (\S+)$', self.prep(), re.M)
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=path):
                self.assertIsNone(refused.match(Path(path).name))

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
                     'proof copy', 'proofs directory', 'ambient.py', 'report'):
            with self.subTest(word=word):
                self.assertIn(word, out)

    def test_a_declared_env_sweep_runs_the_changed_tests_in_a_copy_of_its_own(self) -> None:
        (self.repo / 'tests').mkdir()
        (self.repo / 'tests/test_claim.py').write_text('import unittest\nfrom store import claim\n\n\nclass T(unittest.TestCase):\n'
                                                      '    def test_claim(self):\n        self.assertEqual(claim(1), 2)\n')
        (self.repo / '.claude/pr-grade.json').write_text(json.dumps(
            {'silent': ['store.py'], 'envSweep': {'command': [sys.executable, '-m', 'unittest', '{tests}'], 'timeout': 60}}))
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'a test and a sweep')
        out = self.prep()
        self.assertIn('ambient.py at the head, for L10:', out)
        self.assertIn('Tests the change touches: tests/test_claim.py;', out)
        self.assertIn('- baseline, no perturbation: pass', out)
        self.assertEqual(out.count('- TZ=Pacific/Kiritimati'), 1)
        root = Path(re.search(r'^merge: .* merge (\S+)$', out, re.M).group(1))
        self.assertTrue((root / 'env-sweep' / 'repo' / 'store.py').is_file())
        self.assertNotIn('env-sweep', json.loads((root / 'grade.json').read_text())['groups'])

    def test_without_a_sweep_the_exit_code_list_still_reaches_the_l10_holder(self) -> None:
        out = self.prep()
        self.assertIn('### Exit codes', out)
        self.assertIn('Not run: the repository declares no `envSweep`', out)
        self.assertEqual(len(re.findall(r'^- Your proof copy: ', out, re.M)), 1)

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

    def test_a_modified_dunder_is_named_as_not_derived(self) -> None:
        git(self.repo, 'checkout', '-q', 'main')
        (self.repo / 'box.py').write_text('class Box:\n    def __repr__(self):\n        return "a"\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'box')
        git(self.repo, 'checkout', '-q', 'topic')
        git(self.repo, 'merge', '-q', '--no-edit', 'main')
        (self.repo / 'box.py').write_text('class Box:\n    def __repr__(self):\n        return "b"\n')
        git(self.repo, 'commit', '-q', '-am', 'change repr')
        self.assertIn('- not derived for box.py:2 `Box.__repr__`: an entry point or dunder', self.prep())

    def test_a_file_the_author_never_added_is_left_out(self) -> None:
        (self.repo / 'scratch.py').write_text('def claim(z):\n    return z\n')
        out = self.prep()
        self.assertNotIn('scratch.py', out)
        self.assertIn('(3 files;', out)

    def test_a_config_without_ordinary_prints_the_notice_before_the_shared_block(self) -> None:
        out = self.prep()
        self.assertEqual(out.count('notice: no `ordinary` key'), 1)
        self.assertLess(out.index('notice:'), out.index('===== shared block ====='))

    def test_an_empty_config_runs_and_prints_the_notice(self) -> None:
        # load_config reads an empty file as no config; prep used to stop on it after making the copies.
        (self.repo / '.claude/pr-grade.json').write_text('')
        git(self.repo, 'commit', '-q', '-am', 'empty config')
        self.assertIn('notice: no `ordinary` key', self.prep())

    def test_in_thread_prints_no_merge_line(self) -> None:
        (self.repo / '.claude/pr-grade.json').write_text('{"silent": [], "ordinary": ["*"]}\n')
        git(self.repo, 'commit', '-q', '-am', 'nothing silent')
        out = self.prep()
        self.assertIn('mode: in-thread', out)
        self.assertNotIn('notice:', out)
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

    def declare_format(self, **config) -> None:
        # A reader that accepts everything, over one recorded instance, so only where its output lands is tested.
        check = [sys.executable, '-c', 'import json, sys; print(json.dumps([[] for _ in json.load(open(sys.argv[2]))]))',
                 '{tree}', '{instances}']
        fmt = {'name': 'store records', 'readers': ['store.py'], 'check': check, 'instances': [{'files': 'app.py'}]}
        (self.repo / '.claude/pr-grade.json').write_text(json.dumps({'silent': ['store.py'], 'formats': [fmt], **config}))
        git(self.repo, 'commit', '-q', '-am', 'declare a format')

    def test_the_grader_holding_the_old_state_lens_gets_the_replay(self) -> None:
        self.declare_format()
        out = self.prep('--groups', 'timing=L1,L2,L7', '--groups', 'rest=L3,L4,L5,L6,L8,L9,L10')
        before_rest, rest = out.split('===== rest =====')
        self.assertIn('history_replay.py at the head, for L9:\n### store records (reader: store.py)\n'
                      'Replay: the base and head readers read 1 recorded instances.', rest)
        self.assertNotIn('Replay:', before_rest)

    def test_old_state_lens_names_the_lens_that_reads_it(self) -> None:
        self.declare_format(oldStateLens='L4')
        out = self.prep('--groups', 'timing=L1,L2,L7', '--groups', 'reach=L3,L4,L10', '--groups', 'rest=L5,L6,L8,L9')
        self.assertIn('history_replay.py at the head, for L4:', out.split('===== reach =====')[1].split('=====')[0])

    def test_a_skipped_old_state_lens_skips_the_replay(self) -> None:
        self.declare_format()
        out = self.prep('--skip', 'L9')
        self.assertIn('skipped: L9', out)
        self.assertNotIn('Replay:', out)

    def test_an_old_state_lens_no_grader_holds_puts_the_replay_in_the_shared_block(self) -> None:
        self.declare_format(oldStateLens='L14')
        shared = self.prep().split('===== shared block =====')[1].split('\n===== ')[0]
        self.assertIn('No grader holds L14, so every grader reads what history_replay.py found:', shared)
        self.assertIn('Replay: the base and head readers read 1 recorded instances.', shared)

    def test_a_branch_touching_no_declared_reader_gets_no_replay(self) -> None:
        self.declare_format()
        path = self.repo / '.claude/pr-grade.json'
        path.write_text(path.read_text().replace('"readers": ["store.py"]', '"readers": ["other.py"]'))
        git(self.repo, 'commit', '-q', '-am', 'another reader')
        self.assertNotIn('Replay:', self.prep())

    def test_uncommitted_edits_stop_it_before_anything_is_made(self) -> None:
        (self.repo / 'store.py').write_text('changed\n')
        run = self.run_cli()
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('uncommitted edits', run.stderr)
        self.assertEqual(list(self.proofs.iterdir()), [])


class SurfaceTest(unittest.TestCase):
    """One commit on main, graded by path: no branch touched `src`."""

    EMPTY_TREE = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.repo, self.proofs = base / 'repo', base / 'proofs'
        for folder in (self.repo / '.claude', self.repo / 'src', self.repo / 'tests', self.repo / 'lib', self.repo / 'docs',
                       self.proofs):
            folder.mkdir(parents=True)
        (self.repo / 'docs/plan.md').write_text('Callers claim a row, then release it; peek never writes.\n')
        git(self.repo, 'init', '-q', '-b', 'main')
        (self.repo / '.claude/pr-grade.json').write_text('{"silent": ["src/store.py"], "ordinary": ["src/*", "lib/*"]}\n')
        (self.repo / 'src/store.py').write_text(
            'def claim(x):\n    return x\n\n\ndef release(x):\n    return x\n\n\ndef peek():\n    return 1\n')
        (self.repo / 'src/inner.py').write_text('from store import claim\n\nclaim(1)\n')
        (self.repo / 'src/window.py').write_text(
            'async def reserve(repo, stream, item_id):\n    row = repo.get(item_id)\n    if row:\n        return\n'
            '    events = [event async for event in stream]\n    repo.update(item_id, events)\n')
        (self.repo / 'tests/test_store.py').write_text('from src.store import claim\n\nclaim(2)\n')
        (self.repo / 'lib/other.py').write_text('def unrelated():\n    return 2\n')
        (self.repo / 'app.py').write_text('from src.store import claim\n\nprint(claim(1))\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'only commit')

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPTS / 'grade_prep.py'), *args], cwd=self.repo,
                              env={**os.environ, 'TMPDIR': str(self.proofs)}, capture_output=True, text=True)

    def surface(self, *args: str) -> str:
        run = self.run_cli('--surface', *args)
        self.assertEqual(run.returncode, 0, run.stderr)
        return run.stdout

    def test_it_lists_every_function_though_no_branch_touched_one(self) -> None:
        out = self.surface('src')
        for name in ('claim', 'release', 'peek', 'reserve'):
            with self.subTest(name=name):
                self.assertRegex(out, rf'(?m)^- `{name}`, defined src/\w+\.py:\d+:')

    def test_the_change_command_names_the_empty_tree_the_head_and_only_the_paths(self) -> None:
        out = self.surface('src')
        head = git(self.repo, 'rev-parse', 'HEAD').strip()
        change = re.search(r'- Change: `(.+?)` \(', out).group(1)
        self.assertIn(f'diff {self.EMPTY_TREE} {head} -- src ', change)
        self.assertNotIn('-- .', change)
        self.assertIn('- Surface: src', out)
        self.assertIn('This is a surface grade. Treat every function in the change as if the diff introduced it.', out)

    def test_the_whole_repository_can_be_a_surface(self) -> None:
        out = self.surface('.')
        self.assertIn('- Surface: .', out)
        self.assertRegex(out, r'(?m)^- `unrelated`, defined lib/other\.py:1:')

    def test_a_repeated_surface_flag_adds_to_the_first(self) -> None:
        run = self.run_cli('--surface', 'src', '--surface', 'lib')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('- Surface: src lib', run.stdout)

    def test_the_change_command_selects_the_surfaces_code_and_no_tests(self) -> None:
        out = self.surface('src', 'tests')
        command = re.search(r'- Change: `git -C \S+ (.+?)` \(', out).group(1)
        args = shlex.split(command)
        args.insert(1, '--name-only')
        names = git(self.repo, *args).split()
        self.assertEqual(sorted(names), ['src/inner.py', 'src/store.py', 'src/window.py'])

    def test_callers_outside_the_surface_are_listed_and_the_rest_counted(self) -> None:
        line = next(l for l in self.surface('src').splitlines() if l.startswith('- `claim`'))
        # src/inner.py is inside the surface, tests/test_store.py is a test: two hits each.
        self.assertEqual(line, '- `claim`, defined src/store.py:1: app.py:1, app.py:3, and 4 more inside the surface or in tests')

    def test_a_function_with_no_outside_caller_says_so(self) -> None:
        line = next(l for l in self.surface('src').splitlines() if l.startswith('- `peek`'))
        self.assertEqual(line, '- `peek`, defined src/store.py:9: none found outside the surface')

    def test_documentation_under_notcode_is_not_searched_for_callers(self) -> None:
        out = self.surface('src')
        self.assertNotIn('docs/plan.md', out)
        self.assertIn('- `release`, defined src/store.py:5: none found outside the surface', out)

    def test_a_base_is_refused(self) -> None:
        for flag in ('--base', '--branch-base'):
            with self.subTest(flag=flag):
                run = self.run_cli('--surface', 'src', flag, 'main')
                self.assertNotEqual(run.returncode, 0)
                self.assertIn('a surface grade has no base', run.stderr)
        self.assertEqual(list(self.proofs.iterdir()), [])

    def test_a_missing_path_is_refused_by_name(self) -> None:
        run = self.run_cli('--surface', 'src', 'nope/')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('nope/', run.stderr)
        self.assertEqual(list(self.proofs.iterdir()), [])

    def test_a_path_outside_the_repository_is_refused_by_name(self) -> None:
        run = self.run_cli('--surface', '../elsewhere')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('../elsewhere', run.stderr)

    def test_a_file_that_exists_only_on_disk_is_refused(self) -> None:
        # Graders read commits, not the working tree.
        (self.repo / 'src/draft.py').write_text('def draft():\n    return 0\n')
        run = self.run_cli('--surface', 'src/draft.py')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('src/draft.py', run.stderr)

    def test_a_surface_of_tests_alone_has_nothing_to_grade(self) -> None:
        run = self.run_cli('--surface', 'tests')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('tests', run.stderr)

    def test_l7_scans_the_whole_surface_not_a_diff(self) -> None:
        out = self.surface('src')
        self.assertRegex(out, r'check-then-act candidates \(L7\): 1 in \d+ functions')
        self.assertIn('src/window.py:1  reserve', out)

    def test_grade_json_holds_the_surface_and_the_empty_tree_as_base(self) -> None:
        out = self.surface('src', 'lib')
        root = Path(re.search(r'merge (\S+)$', out, re.M).group(1))
        meta = json.loads((root / 'grade.json').read_text())
        self.assertEqual((meta['surface'], meta['base']), (['src', 'lib'], self.EMPTY_TREE))
        self.assertEqual(meta['head'], git(self.repo, 'rev-parse', 'HEAD').strip())

    def test_the_mode_comes_from_the_surfaces_files(self) -> None:
        self.assertIn('mode: subagent', self.surface('src'))
        self.assertIn('mode: in-thread', self.surface('lib'))

    def test_the_proof_copy_keeps_the_tests(self) -> None:
        copy = Path(re.search(r'- Your proof copy: (\S+)', self.surface('src')).group(1))
        self.assertTrue((copy / 'tests/test_store.py').is_file())

    def test_a_pinned_install_is_linked_because_a_surface_has_no_since(self) -> None:
        # With the empty tree as `since`, every pin file reads as changed on the branch and the
        # install would be dropped from the copy.
        config = {'silent': ['src/store.py'], 'ordinary': ['src/*'],
                  'proofDir': [{'path': '.venv', 'pinnedBy': ['requirements.txt']}]}
        (self.repo / '.claude/pr-grade.json').write_text(json.dumps(config))
        (self.repo / 'requirements.txt').write_text('pytest\n')
        (self.repo / '.gitignore').write_text('.venv/\n')
        (self.repo / '.venv').mkdir()
        (self.repo / '.venv/marker').write_text('installed\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'pin an install')
        copy = Path(re.search(r'- Your proof copy: (\S+)', self.surface('src')).group(1))
        self.assertTrue((copy / '.venv/marker').is_file())

    def test_history_replay_runs_for_a_format_whose_reader_is_under_the_surface(self) -> None:
        config = {'silent': ['src/store.py'], 'ordinary': ['src/*', 'lib/*'], 'formats': [
            {'name': 'store', 'readers': ['src/store.py'], 'check': ['true'], 'instances': []},
            {'name': 'other', 'readers': ['lib/other.py'], 'check': ['true'], 'instances': []}]}
        (self.repo / '.claude/pr-grade.json').write_text(json.dumps(config))
        git(self.repo, 'commit', '-q', '-am', 'declare formats')
        out = self.surface('src')
        self.assertIn('### store (reader: src/store.py)', out)
        self.assertNotIn('### other', out)

    def test_the_transplant_does_not_call_the_whole_repository_the_change(self) -> None:
        # Diffed against the empty tree, every record kind in the repository would read as added.
        reader = ['python3', '-c', 'import json,sys;print(json.dumps([[] for _ in json.load(open(sys.argv[1]))]))',
                  '{instances}']
        config = {'silent': ['src/store.py'], 'ordinary': ['src/*'], 'formats': [
            {'name': 'notes', 'readers': ['src/store.py'], 'check': reader, 'instances': [{'files': 'notes/*.txt'}],
             'records': r'^(?P<kind>[a-z]+): ', 'values': r'value=(?P<value>\w+)'}]}
        (self.repo / '.claude/pr-grade.json').write_text(json.dumps(config))
        (self.repo / 'notes').mkdir()
        (self.repo / 'notes/a.txt').write_text('alpha: value=one\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'record something')
        out = self.surface('src')
        self.assertIn('Transplant: the change adds no kind of record', out)

    def test_uncommitted_edits_stop_it(self) -> None:
        (self.repo / 'src/store.py').write_text('changed\n')
        run = self.run_cli('--surface', 'src')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('uncommitted edits', run.stderr)

    def test_a_branch_grade_prints_no_surface(self) -> None:
        git(self.repo, 'checkout', '-q', '-b', 'topic')
        (self.repo / 'src/store.py').write_text('def claim(x):\n    return x + 1\n')
        git(self.repo, 'commit', '-q', '-am', 'change')
        run = self.run_cli('--base', 'main')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn('Surface:', run.stdout)
        self.assertNotIn('surface grade', run.stdout)


if __name__ == '__main__':
    unittest.main()
