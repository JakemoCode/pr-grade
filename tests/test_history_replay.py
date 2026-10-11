#!/usr/bin/env python3
"""Unit tests for history_replay.py: a changed reader run over what earlier versions wrote.

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / 'skills/pr-grade/scripts'
sys.path.insert(0, str(SCRIPTS))
import grade_mode  # noqa: E402
import history_replay  # noqa: E402

# A record is `- Rec <n>`, optionally tagged `| <tag>`, with one value per nested line.
BASE_READER = '''import re

def problems(text):
    found = []
    for line in text.splitlines():
        if line.startswith('- Rec ') and ' | ' in line:
            found.append(f'record {line!r} takes no tag')
        elif line.startswith('  - '):
            value = line[4:]
            if not (value == 'fixed' or value.startswith('declined: ') or re.fullmatch(r'issue #\\d+', value)):
                found.append(f'value {value!r} is unknown')
    return found
'''
# The head adds the `guard` tag, whose records take `record only`, and stops reading `issue #N`. Its
# defect: a guard record still takes every old value.
HEAD_READER = '''def problems(text):
    found, tag = [], None
    for line in text.splitlines():
        if line.startswith('- Rec '):
            tag = line.partition(' | ')[2] or None
            if tag not in (None, 'guard'):
                found.append(f'record {line!r} has tag {tag!r}')
        elif line.startswith('  - '):
            value = line[4:]
            if value == 'record only':
                if tag != 'guard':
                    found.append(f'value {value!r} needs a guard record')
            elif not (value == 'fixed' or value.startswith('declined: ')):
                found.append(f'value {value!r} is unknown')
    return found
'''
CHECK = '''import importlib.util, json, sys
spec = importlib.util.spec_from_file_location('reader', sys.argv[1] + '/fmt/reader.py')
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)
print(json.dumps([reader.problems(text) for text in json.load(open(sys.argv[2]))]))
'''
TEST_AT_HEAD = 'GUARDED = "- Rec 1 | guard\\n  - record only\\n"\n'


def git(root: Path, *args: str) -> str:
    return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


def fmt(**overrides) -> dict:
    return {'name': 'records', 'readers': ['fmt/reader.py'],
            'check': [sys.executable, 'check.py', '{tree}', '{instances}'],
            'instances': [{'files': 'records/*.txt'}],
            'records': r'- (?P<type>Rec) \d+(?P<kind>(?: \| \w+)*)',
            'values': r'  - (?P<value>[\w :#]+)', **overrides}


class ReportTest(unittest.TestCase):
    """A repository whose branch changes the reader of `records/*.txt` and adds the `guard` tag."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name).resolve() / 'repo'
        (self.repo / 'fmt').mkdir(parents=True)
        (self.repo / 'records').mkdir()
        git(self.repo, 'init', '-q', '-b', 'main')
        (self.repo / 'check.py').write_text(CHECK)
        (self.repo / 'fmt/reader.py').write_text(BASE_READER)
        (self.repo / 'records/a.txt').write_text('- Rec 1\n  - fixed\n  - declined: out of scope\n')
        (self.repo / 'records/b.txt').write_text('- Rec 1\n  - issue #4\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'base')
        self.fork = git(self.repo, 'rev-parse', 'HEAD').strip()
        (self.repo / 'fmt/reader.py').write_text(HEAD_READER)
        (self.repo / 'fmt/test_reader.py').write_text(TEST_AT_HEAD)
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'guard')
        self.head = git(self.repo, 'rev-parse', 'HEAD').strip()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def report(self, *formats: dict, changed: list[str] | None = None) -> list[str]:
        changed = ['fmt/reader.py', 'fmt/test_reader.py'] if changed is None else changed
        return history_replay.report(self.repo, self.fork, self.head, {'formats': list(formats)}, changed)

    def test_a_branch_that_touches_no_declared_reader_gets_nothing(self) -> None:
        self.assertEqual(self.report(fmt(), changed=['check.py']), [])

    def test_a_repository_that_declares_no_format_gets_nothing(self) -> None:
        self.assertEqual(history_replay.report(self.repo, self.fork, self.head, grade_mode.load_config(self.repo),
                                               ['fmt/reader.py']), [])

    def test_the_replay_names_an_instance_the_base_accepts_and_the_head_refuses(self) -> None:
        out = '\n'.join(self.report(fmt()))
        self.assertIn('read 2 recorded instances. The head refuses 1 the base accepts, and accepts 0', out)
        self.assertIn("- records/b.txt: value 'issue #4' is unknown", out)

    def test_the_transplant_runs_recorded_values_through_the_new_kind(self) -> None:
        out = self.report(fmt())
        self.assertIn('Transplant, kind ` | guard` of `Rec` records, which the change adds. Each value ran through '
                      'the head reader in records/a.txt, under the new kind and under the record\'s own kind ``:', out)
        self.assertIn('- `declined: ...` (1 recorded): new kind accepted; own kind accepted', out)
        self.assertIn('- `fixed` (1 recorded): new kind accepted; own kind accepted', out)
        self.assertIn("- `issue #N` (1 recorded): new kind refused: value '<value>' is unknown; "
                      "own kind refused: value '<value>' is unknown", out)
        self.assertIn("- `record only` (new in this change): new kind accepted; own kind refused: value '<value>' "
                      'needs a guard record', out)
        self.assertIn('The new kind accepts 2 of the 2 values the old kind accepts.', '\n'.join(out))

    def test_a_kind_the_base_already_wrote_is_not_new(self) -> None:
        (self.repo / 'records/c.txt').write_text('- Rec 1 | guard\n  - fixed\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '--amend', '--no-edit')
        self.head = git(self.repo, 'rev-parse', 'HEAD').strip()
        git(self.repo, 'checkout', '-q', '-b', 'later', self.fork)
        out = '\n'.join(history_replay.report(self.repo, self.fork, self.head, {'formats': [fmt()]},
                                              ['fmt/reader.py']))
        # c.txt is not at the fork, so it is no instance; the kind is still new against the base.
        self.assertIn('Transplant, kind ` | guard`', out)

    def test_a_kind_a_recorded_instance_holds_is_not_new(self) -> None:
        (self.repo / 'records/c.txt').write_text('- Rec 1 | guard\n  - fixed\n')
        git(self.repo, 'checkout', '-q', self.fork)
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'an old guard record')
        self.fork = git(self.repo, 'rev-parse', 'HEAD').strip()
        out = '\n'.join(history_replay.report(self.repo, self.fork, self.head, {'formats': [fmt()]},
                                              ['fmt/reader.py']))
        self.assertIn('Transplant: the change adds no kind of record', out)

    def test_without_both_regexes_only_the_replay_runs(self) -> None:
        out = '\n'.join(self.report(fmt(records=None)))
        self.assertIn('Replay:', out)
        self.assertNotIn('Transplant', out)

    def test_a_check_that_fails_is_a_note_not_a_crash(self) -> None:
        out = '\n'.join(self.report(fmt(check=[sys.executable, '-c', 'import sys; sys.exit("boom")'])))
        self.assertIn('The replay failed, so nothing was replayed:', out)
        self.assertIn('exited 1: boom, from `', out)

    def test_a_check_that_raises_names_the_error_not_the_frames(self) -> None:
        out = '\n'.join(self.report(fmt(check=[sys.executable, '-c', 'import missing_reader_module'])))
        self.assertIn("exited 1: ModuleNotFoundError: No module named 'missing_reader_module', from `", out)

    def test_a_check_printing_the_wrong_shape_is_a_note(self) -> None:
        out = '\n'.join(self.report(fmt(check=[sys.executable, '-c', 'print("[]")'])))
        self.assertIn('not one list of problem strings per instance (2)', out)

    def test_a_malformed_format_is_named_even_when_untouched(self) -> None:
        out = self.report({'name': 'odd', 'readers': 'fmt/reader.py'}, changed=['check.py'])
        self.assertEqual(out, ["Note: format 'odd' needs `readers` as a list of strings in .claude/pr-grade.json, "
                               'so it was not replayed.'])

    def test_an_unknown_source_is_a_note(self) -> None:
        out = self.report(fmt(instances=[{'rows': 'x'}, {'files': 'records/*.txt'}]))
        self.assertIn('Note: an instance source names no known key: {"rows": "x"}', out)

    def test_lines_reads_each_non_empty_line_as_an_instance(self) -> None:
        found, notes = history_replay.instances(self.repo, self.fork, {'instances': [{'lines': 'records/a.txt'}]}, None)
        self.assertEqual((found, notes), ([('records/a.txt:1', '- Rec 1'), ('records/a.txt:2', '  - fixed'),
                                          ('records/a.txt:3', '  - declined: out of scope')], []))

    def test_pull_request_bodies_are_read_oldest_first_and_before_drops_later_ones(self) -> None:
        prs = [{'number': 9, 'body': 'nine', 'mergedAt': '2026-10-09T00:00:00Z'},
               {'number': 7, 'body': 'seven', 'mergedAt': '2026-10-07T00:00:00Z'},
               {'number': 8, 'body': None, 'mergedAt': '2026-10-08T00:00:00Z'}]
        real = subprocess.run

        def run(command, *args, **kwargs):
            if command[0] == 'gh':
                return subprocess.CompletedProcess(command, 0, json.dumps(prs), '')
            return real(command, *args, **kwargs)

        with mock.patch.object(history_replay.subprocess, 'run', run):
            found, _ = history_replay.instances(self.repo, self.fork, {'instances': [{'prBodies': 3}]},
                                                '2026-10-09T00:00:00Z')
        self.assertEqual(found, [('PR #7', 'seven'), ('PR #8', '')])

    def test_a_failing_gh_is_a_note(self) -> None:
        def run(command, *args, **kwargs):
            return subprocess.CompletedProcess(command, 1, '', 'not logged in')

        with mock.patch.object(history_replay.subprocess, 'run', run):
            found, notes = history_replay.instances(self.repo, self.fork, {'instances': [{'prBodies': 3}]}, None)
        self.assertEqual(found, [])
        self.assertEqual(notes, ['pull request bodies were not read: gh exited 1: not logged in'])

    def test_the_base_reader_runs_from_the_base_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tree = history_replay.base_tree(self.repo, self.fork, fmt(), Path(tmp))
            # The reader's directory as the base held it: no records, and no test the head added.
            self.assertEqual({p.relative_to(tree).as_posix(): p.read_text() for p in tree.rglob('*') if p.is_file()},
                             {'fmt/reader.py': BASE_READER})


class ShapeTest(unittest.TestCase):
    def test_hashes_numbers_and_reasons_fold(self) -> None:
        cases = {'fixed in 8d957ce, red 3a1f2c9': 'fixed in <sha>, red <sha>', 'issue #12': 'issue #N',
                 'declined: both callers read it': 'declined: ...', 'decision WP-24-D29': 'decision WP-N-DN'}
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(history_replay.shape(value), expected)


if __name__ == '__main__':
    unittest.main()
