#!/usr/bin/env python3
"""Unit tests for grade_block.py: the grade a pull request must carry before it goes ready.

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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'skills/pr-grade/scripts'))
import grade_block  # noqa: E402
import grade_mode  # noqa: E402

GRADED = 'a' * 40
HEAD = 'b' * 40
REPO = 'owner/name'
LENSES = ['L1', 'L2', 'L3']
CLEAR = {'status': 'ahead', 'files': []}
LEAF = tuple(f'src/leaf_{n}.py' for n in range(4))


def body(**fields: str) -> str:
    lines = {'Mode': 'subagent', 'Graded': GRADED, 'Score': '5/5', 'Blocking': 'nothing',
             'Verified and clear': 'L1, L2, L3', 'Could not verify': 'none', **fields}
    return 'Summary\n\n## Grade\n\n' + '\n'.join(f'{k}: {v}' for k, v in lines.items()) + '\n\n## Tests\n\nMode: fan-out\n'


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.config = grade_mode.load_config(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()


class ProblemsTest(Fixture):
    def problems(self, text: str, pr_files: tuple[str, ...] = ('.github/workflows/ci.yml',),
                 compare: dict | None = CLEAR) -> list[str]:
        # One silent file, so the PR requires subagent: the mode body() writes.
        return grade_block.problems(grade_block.parse(text), pr_files=list(pr_files), lenses=LENSES, compare=compare,
                                    root=self.root, config=self.config)

    def test_a_complete_current_block_passes(self) -> None:
        self.assertEqual(self.problems(body()), [])

    def test_a_body_without_a_grade_section_is_refused(self) -> None:
        self.assertTrue(any('## Grade' in p for p in self.problems('Summary\n\nScore: 5/5\n')))

    def test_a_grade_in_a_code_block_is_not_read(self) -> None:
        self.assertTrue(any('## Grade' in p for p in self.problems(f'```\n{body()}```\n')))

    def test_a_crlf_body_parses(self) -> None:
        self.assertEqual(self.problems(body().replace('\n', '\r\n')), [])

    def test_only_the_grade_section_is_read(self) -> None:
        # body() ends with a later section that says `Mode: fan-out`; the grade's own Mode wins.
        self.assertEqual(grade_block.parse(body()).get('Mode'), 'subagent')

    def test_a_grade_below_five_is_refused(self) -> None:
        self.assertTrue(any('4/5' in p for p in self.problems(body(Score='4/5'))))

    def test_a_cheaper_mode_than_required_is_refused(self) -> None:
        refused = self.problems(body(), pr_files=('.github/workflows/ci.yml', *LEAF))
        self.assertTrue(any('requires fan-out' in p and 'ci.yml' in p for p in refused))

    def test_a_dearer_mode_than_required_passes(self) -> None:
        self.assertEqual(self.problems(body(Mode='fan-out'), pr_files=('src/a.py',)), [])

    def test_an_unknown_mode_is_refused(self) -> None:
        self.assertTrue(any("'thorough'" in p for p in self.problems(body(Mode='thorough'))))

    def test_a_lens_left_out_of_verified_is_refused(self) -> None:
        refused = self.problems(body(**{'Verified and clear': 'L1, L3 (L2 not applicable)'}))
        self.assertEqual([p for p in refused if 'leaves out' in p], ['`Verified and clear` leaves out L2. Apply every lens.'])

    def test_a_lens_id_inside_another_is_not_counted(self) -> None:
        self.assertTrue(any('L1' in p for p in self.problems(body(**{'Verified and clear': 'L12, L2, L3'}))))

    def test_a_short_sha_is_refused(self) -> None:
        self.assertTrue(any('40-character' in p for p in self.problems(body(Graded='abc1234'))))

    def test_an_unreadable_graded_commit_is_refused(self) -> None:
        self.assertTrue(any('could not be read' in p for p in self.problems(body(), compare=None)))

    def test_a_graded_commit_outside_the_pr_history_is_refused(self) -> None:
        self.assertTrue(any('diverged' in p for p in self.problems(body(), compare={'status': 'diverged', 'files': []})))

    def test_code_changed_after_the_grade_is_refused(self) -> None:
        after = {'status': 'ahead', 'files': ['.github/workflows/ci.yml', 'docs/notes.md']}
        refused = self.problems(body(), compare=after)
        self.assertTrue(any('ci.yml' in p and 'notes.md' not in p for p in refused))

    def test_a_silent_test_changed_after_the_grade_is_refused(self) -> None:
        self.config['silent'] = ['tests/architecture/*']
        after = {'status': 'ahead', 'files': ['tests/architecture/owners.test.ts']}
        refused = self.problems(body(), pr_files=('tests/architecture/owners.test.ts',), compare=after)
        self.assertTrue(any('owners.test.ts' in p for p in refused))

    def test_only_docs_and_tests_after_the_grade_pass(self) -> None:
        after = {'status': 'ahead', 'files': ['docs/notes.md', 'tests/a_test.py']}
        self.assertEqual(self.problems(body(), pr_files=('.github/workflows/ci.yml', 'docs/notes.md', 'tests/a_test.py'),
                                       compare=after), [])

    def test_base_branch_merged_after_the_grade_passes(self) -> None:
        # The comparison spans the base branch's commits; only this PR's files need the grade.
        self.assertEqual(self.problems(body(), compare={'status': 'ahead', 'files': ['src/elsewhere.py']}), [])

    def test_a_truncated_comparison_is_refused(self) -> None:
        after = {'status': 'ahead', 'files': [f'docs/g_{n}.md' for n in range(300)]}
        self.assertTrue(any('300' in p for p in self.problems(body(), compare=after)))


class LensIdsTest(Fixture):
    def test_every_lens_heading_is_required(self) -> None:
        lens_file = self.root / 'lenses.md'
        lens_file.write_text('# lenses\n\n### L1. Abandoned work\n\ntext L9.\n\n### L10. New lens\n')
        self.assertEqual(grade_block.lens_ids(lens_file), ['L1', 'L10'])

    def test_without_a_lens_file_the_skills_eight_are_required(self) -> None:
        self.assertEqual(grade_block.lens_ids(self.root / 'missing.md'), [f'L{n}' for n in range(1, 9)])


class CheckTest(Fixture):
    """`check <pr>` against a faked `gh`."""

    def run_check(self, *, text: str | None = None, files: tuple[str, ...] = ('.github/workflows/ci.yml',),
                  draft: bool = False, branch: str = 'feat/x', moved: bool = False,
                  after: dict | None = None) -> tuple[bool, list[str]]:
        text = body(**{'Verified and clear': ', '.join(f'L{n}' for n in range(1, 9))}) if text is None else text
        after = after or CLEAR
        views = []

        def gh(*args: str) -> str:
            if args[:2] == ('pr', 'view'):
                views.append(args)
                oid = 'c' * 40 if moved and len(views) > 1 else HEAD
                return json.dumps({'body': text, 'headRefName': branch, 'headRefOid': oid, 'isDraft': draft})
            if args[:3] == ('api', '--paginate', f'repos/{REPO}/pulls/7/files'):
                return ''.join(f'{f}\n' for f in files)
            if args[:2] == ('api', f'repos/{REPO}/compare/{GRADED}...{HEAD}'):
                return json.dumps({'status': after['status'], 'files': [{'filename': f} for f in after['files']]})
            raise AssertionError(f'unexpected gh call {args}')

        with mock.patch.object(grade_block, 'gh', gh):
            return grade_block.check(7, REPO, self.root)

    def test_a_current_grade_passes(self) -> None:
        self.assertEqual(self.run_check(), (True, []))

    def test_a_missing_grade_is_refused(self) -> None:
        self.assertTrue(any('## Grade' in p for p in self.run_check(text='Summary\n')[1]))

    def test_a_draft_needs_no_grade(self) -> None:
        self.assertEqual(self.run_check(text='Summary\n', draft=True), (False, []))

    def test_a_branch_outside_require_grade_needs_none(self) -> None:
        (self.root / '.claude').mkdir()
        (self.root / grade_mode.CONFIG).write_text(json.dumps({'requireGrade': {'branches': '^feat/wp-'}}))
        self.assertEqual(self.run_check(text='Summary\n', branch='docs/x'), (False, []))

    def test_a_rename_out_of_silent_code_keeps_the_mode(self) -> None:
        # GitHub lists src/x.py, previously .github/workflows/x.yml; the old side still requires subagent.
        refused = self.run_check(text=body(Mode='in-thread', **{'Verified and clear': ', '.join(f'L{n}' for n in range(1, 9))}),
                                 files=('src/x.py\t.github/workflows/x.yml',))[1]
        self.assertTrue(any('requires subagent' in p for p in refused))

    def test_a_pr_at_githubs_file_list_cap_is_refused(self) -> None:
        self.assertTrue(any('3000' in p for p in self.run_check(files=tuple(f'docs/g_{n}.md' for n in range(3000)))[1]))

    def test_a_push_during_the_check_is_refused(self) -> None:
        self.assertTrue(any('moved' in p for p in self.run_check(moved=True)[1]))


if __name__ == '__main__':
    unittest.main()
