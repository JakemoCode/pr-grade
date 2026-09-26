#!/usr/bin/env python3
"""Unit tests for grade_block.py: the grade a pull request must carry before it goes ready.

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import importlib.util
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

    def test_a_pasted_report_below_the_block_does_not_override_it(self) -> None:
        # A fan-out agent's reply repeats `Score:` and `Verified and clear:` after the block's own.
        text = body(Score='3/5').replace('\n\n## Tests', '\nScore: 5/5\nVerified and clear: L1, L2, L3\n\n## Tests')
        self.assertEqual(grade_block.parse(text).get('Score'), '3/5')

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

    def test_a_lens_named_with_a_qualifier_is_not_clear(self) -> None:
        refused = self.problems(body(**{'Verified and clear': 'L1, L2 not applicable, L3'}))
        self.assertIn('`Verified and clear` leaves out L2. Apply every lens.', refused)

    def test_lenses_in_one_item_are_each_clear(self) -> None:
        self.assertEqual(self.problems(body(**{'Verified and clear': 'L1 L2 L3 (all walked)'})), [])

    def test_a_path_counted_as_code_raises_the_required_mode(self) -> None:
        pr_files = ('.github/workflows/ci.yml', *LEAF[:3], 'docs/manifest.yaml')
        self.assertEqual(self.problems(body(), pr_files), [])
        self.config = {**self.config, 'countAsCode': ['docs/manifest.yaml']}
        self.assertTrue(any('requires fan-out' in p for p in self.problems(body(), pr_files)))

    def test_a_short_sha_is_refused(self) -> None:
        self.assertTrue(any('40-character' in p for p in self.problems(body(Graded='abc1234'))))

    def test_an_unreadable_graded_commit_is_refused_with_githubs_reason(self) -> None:
        refused = self.problems(body(), compare={'error': 'HTTP 403: Resource not accessible'})
        self.assertTrue(any('HTTP 403' in p for p in refused))

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

    def test_only_docs_after_the_grade_pass(self) -> None:
        after = {'status': 'ahead', 'files': ['docs/notes.md']}
        self.assertEqual(self.problems(body(), pr_files=('.github/workflows/ci.yml', 'docs/notes.md'), compare=after), [])

    def test_a_test_changed_after_the_grade_is_refused(self) -> None:
        after = {'status': 'ahead', 'files': ['tests/a_test.py']}
        refused = self.problems(body(), pr_files=('.github/workflows/ci.yml', 'tests/a_test.py'), compare=after)
        self.assertTrue(any('a_test.py' in p for p in refused))

    def test_base_branch_merged_after_the_grade_passes(self) -> None:
        # The comparison spans the base branch's commits; only this PR's files need the grade.
        self.assertEqual(self.problems(body(), compare={'status': 'ahead', 'files': ['src/elsewhere.py']}), [])

    def test_a_truncated_comparison_is_refused(self) -> None:
        after = {'status': 'ahead', 'files': [f'docs/g_{n}.md' for n in range(300)]}
        self.assertTrue(any('300' in p for p in self.problems(body(), compare=after)))

    def test_a_caller_can_supply_its_own_selector(self) -> None:
        def assess(pr_files: list[str], root: Path, config: dict) -> tuple[str, dict[str, str], list[str]]:
            return 'fan-out', {'owners.yaml': 'an owner map'}, pr_files
        refused = grade_block.problems(grade_block.parse(body()), pr_files=['owners.yaml'], lenses=LENSES,
                                       compare=CLEAR, root=self.root, config=self.config, assess=assess)
        self.assertEqual(refused, ['Graded subagent, but this change requires fan-out (owners.yaml: an owner map). '
                                   'Re-grade.'])

    def test_a_selector_that_returns_an_unknown_mode_is_refused(self) -> None:
        def assess(pr_files: list[str], root: Path, config: dict) -> tuple[str, dict[str, str], list[str]]:
            return 'fanout', {}, pr_files
        refused = grade_block.problems(grade_block.parse(body()), pr_files=['a.py'], lenses=LENSES, compare=CLEAR,
                                       root=self.root, config=self.config, assess=assess)
        self.assertEqual(refused, ["The selector returned mode 'fanout', which is not one of in-thread, subagent, "
                                   'fan-out.'])

    def test_a_missing_block_is_refused_before_the_selector_runs(self) -> None:
        def assess(*args: object) -> None:
            raise AssertionError('the selector ran for a body with no grade')
        refused = grade_block.problems(None, pr_files=['a.py'], lenses=LENSES, compare=CLEAR, root=self.root,
                                       config=self.config, assess=assess)
        self.assertEqual(len(refused), 1)


class EmbeddingTest(unittest.TestCase):
    """A repository that embeds the scripts beside its own `grade_mode` module."""

    def test_grade_block_loads_the_selector_beside_it_and_leaves_the_callers_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # The caller's own grade_mode, first on sys.path and already imported, as in EngOS.
            (Path(tmp) / 'grade_mode.py').write_text("MODES = ('mine',)\n")
            theirs = type(sys)('grade_mode')
            path = list(sys.path)
            with mock.patch.dict(sys.modules, {'grade_mode': theirs}), mock.patch.object(sys, 'path', [tmp, *path]):
                source = Path(grade_block.__file__)
                spec = importlib.util.spec_from_file_location('embedded_grade_block', source)
                embedded = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(embedded)
                self.assertIs(sys.modules['grade_mode'], theirs)
                self.assertEqual(sys.path, [tmp, *path])
            refused = embedded.problems(embedded.parse(body()), pr_files=['.github/workflows/ci.yml', *LEAF],
                                        lenses=LENSES, compare=CLEAR, root=Path(tmp),
                                        config=grade_mode.load_config(Path(tmp)))
        self.assertTrue(any('requires fan-out' in p for p in refused))


class LensIdsTest(Fixture):
    def test_every_lens_heading_is_required(self) -> None:
        text = '# lenses\n\n### L1. Abandoned work\n\ntext L9.\n\n### L10. New lens\n'
        self.assertEqual(grade_block.lens_ids(text), ['L1', 'L10'])

    def test_without_a_lens_file_the_skills_eight_are_required(self) -> None:
        self.assertEqual(grade_block.lens_ids(None), [f'L{n}' for n in range(1, 9)])

    def test_a_lens_file_with_no_matching_heading_still_requires_the_eight(self) -> None:
        # `### L1 Abandoned work`, with no period, must not leave nothing required.
        self.assertEqual(grade_block.lens_ids('### L1 Abandoned work\n### L2: Shared\n'), [f'L{n}' for n in range(1, 9)])


class CheckTest(Fixture):
    """`check <pr>` against a faked `gh`."""

    def run_check(self, *, text: str | None = None, files: tuple[str, ...] = ('.github/workflows/ci.yml',),
                  draft: bool = False, branch: str = 'feat/x', moved: bool = False,
                  after: dict | None = None, base: dict | None = None) -> tuple[bool, list[str]]:
        text = body(**{'Verified and clear': ', '.join(f'L{n}' for n in range(1, 9))}) if text is None else text
        after = after or CLEAR
        views = []

        def gh(*args: str) -> str:
            if args[:2] == ('pr', 'view'):
                views.append(args)
                oid = 'c' * 40 if moved and len(views) > 1 else HEAD
                return json.dumps({'body': text, 'baseRefName': 'main', 'headRefName': branch, 'headRefOid': oid,
                                   'isDraft': draft})
            if args[:3] == ('api', '--paginate', f'repos/{REPO}/pulls/7/files?per_page=100'):
                # One JSON array per file: the path, then the path it was renamed from.
                return ''.join(json.dumps(f.split('\t')) + '\n' for f in files)
            if args[:2] == ('api', f'repos/{REPO}/compare/{GRADED}...{HEAD}'):
                return json.dumps({'status': after['status'], 'files': after['files']})
            raise AssertionError(f'unexpected gh call {args}')

        def base_file(repo: str, path: str, ref: str) -> str | None:
            self.assertEqual((repo, ref), (REPO, 'main'))
            return (base or {}).get(path)

        with mock.patch.object(grade_block, 'gh', gh), mock.patch.object(grade_block, 'base_file', base_file):
            return grade_block.check(7, REPO, self.root)

    def test_a_current_grade_passes(self) -> None:
        self.assertEqual(self.run_check(), (True, []))

    def test_a_missing_grade_is_refused(self) -> None:
        self.assertTrue(any('## Grade' in p for p in self.run_check(text='Summary\n')[1]))

    def test_a_draft_needs_no_grade(self) -> None:
        self.assertEqual(self.run_check(text='Summary\n', draft=True), (False, []))

    def test_a_branch_outside_require_grade_needs_none(self) -> None:
        config = json.dumps({'requireGrade': {'branches': '^feat/wp-'}})
        self.assertEqual(self.run_check(text='Summary\n', branch='docs/x', base={grade_mode.CONFIG: config}), (False, []))

    def test_the_pr_cannot_loosen_its_own_config(self) -> None:
        # The PR's checkout exempts every branch; the base branch has no such rule, so the grade is still required.
        (self.root / '.claude').mkdir()
        (self.root / grade_mode.CONFIG).write_text(json.dumps({'requireGrade': {'branches': '^$'}}))
        self.assertTrue(any('## Grade' in p for p in self.run_check(text='Summary\n')[1]))

    def test_lenses_come_from_the_base_branch(self) -> None:
        refused = self.run_check(base={'.claude/pr-grade-lenses.md': '### L1. A\n### L9. New\n'})[1]
        self.assertTrue(any('L9' in p for p in refused))

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


def report(score: str, verified: str, *, findings: str = '', unverified: str = 'none', outside: str = 'none') -> str:
    return (f'Score: {score}\nBlocking: nothing\n{findings}L1: closed\nVerified and clear: {verified}\n'
            f'Could not verify: {unverified}\nOutside my lenses: {outside}\nL7 candidates: none given\n')


class MergeTest(unittest.TestCase):
    """Two graders, timing (L1, L2) and reach (L3), under one grade_prep root."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'grade.json').write_text(json.dumps(
            {'mode': 'fan-out', 'head': HEAD, 'lenses': LENSES, 'groups': {'timing': ['L1', 'L2'], 'reach': ['L3']}}))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, group: str, text: str) -> None:
        (self.root / group).mkdir(exist_ok=True)
        (self.root / group / 'report.md').write_text(text)

    def merged(self) -> str:
        return grade_block.merge(self.root)

    def block(self) -> dict:
        return grade_block.parse(self.merged())

    def test_clean_reports_draft_a_block_the_check_accepts(self) -> None:
        self.write('timing', report('5/5', 'L1, L2'))
        self.write('reach', '```\n' + report('5/5', 'L3') + '```\n')
        block = self.block()
        self.assertEqual((block['Score'], block['Verified and clear'], block['Graded']), ('5/5', 'L1, L2, L3', HEAD))

    def test_one_finding_scored_as_a_2_by_its_grader_is_a_4(self) -> None:
        # The case that motivated the merge: a grader read one P1 as the table's 2.
        self.write('timing', report('2/5', 'L2', findings='P1 L1 src/a.py:9 - late write lands\nwhat breaks\n'))
        self.write('reach', report('5/5', 'L3'))
        block = self.block()
        self.assertEqual((block['Score'], block['Verified and clear']), ('4/5', 'L2, L3'))
        self.assertIn('L1  P1 at src/a.py:9', self.merged())

    def test_two_findings_at_one_line_are_flagged_and_both_count(self) -> None:
        # One defect or two is the coordinator's call; until then the floor assumes two.
        self.write('timing', report('4/5', 'L2', findings='P1 L1 src/a.py:9 - late write lands\n'))
        self.write('reach', report('4/5', '', findings='P1 L3 src/a.py:9 - caller sees stale row\n'))
        merged = self.merged()
        self.assertIn('findings (2):', merged)
        self.assertEqual(merged.count('same line as another finding'), 2)
        self.assertEqual(self.block()['Score'], '3/5')

    def test_a_finding_shaped_line_under_could_not_verify_stays_a_claim(self) -> None:
        self.write('timing', report('5/5', 'L1', unverified='\nP2 L2 src/a.py:10 - maybe a race; runs 1-3 passed'))
        self.write('reach', report('5/5', 'L3'))
        merged = self.merged()
        self.assertIn('findings (0):', merged)
        self.assertIn('Could not verify: guess: P2 L2 src/a.py:10', merged)
        self.assertEqual(self.block()['Score'], '4/5')

    def test_an_open_p2_claim_blocks_like_the_finding_it_would_be(self) -> None:
        self.write('timing', report('5/5', 'L1, L2'))
        self.write('reach', report('5/5', 'L3', unverified='L3 would be P2: script caller skips the lease; '
                                                         'three runs never reached it'))
        block = self.block()
        self.assertEqual((block['Score'], block['Verified and clear']), ('4/5', 'L1, L2'))
        self.assertIn('Could not verify: guess: L3 would be P2', self.merged())

    def test_an_open_p3_claim_does_not_block(self) -> None:
        self.write('timing', report('5/5', 'L1, L2', unverified='L1 would be P3: the log line reads oddly'))
        self.write('reach', report('5/5', 'L3'))
        self.assertEqual(self.block()['Score'], '5/5')

    def test_a_lens_no_report_accounts_for_is_asked_about(self) -> None:
        self.write('timing', report('5/5', 'L1'))
        self.write('reach', report('5/5', 'L3'))
        merged = self.merged()
        self.assertIn('L2  unaccounted: ask timing which', merged)
        self.assertEqual(self.block()['Score'], '2/5')

    def test_a_missing_report_leaves_its_lenses_unapplied(self) -> None:
        self.write('timing', report('5/5', 'L1, L2'))
        self.assertIn('L3  no report from reach', self.merged())
        self.assertEqual(self.block()['Score'], '2/5')

    def test_a_defect_outside_the_graders_lenses_is_carried(self) -> None:
        self.write('timing', report('5/5', 'L1, L2', outside='L3 src/b.py:4: a second caller skips the guard'))
        self.write('reach', report('5/5', 'L3'))
        self.assertIn('timing: L3 src/b.py:4: a second caller skips the guard', self.merged())

    def test_a_wrapped_claim_is_one_claim(self) -> None:
        self.write('timing', report('4/5', 'L1', unverified='P2 L2 src/a.py:10 - maybe a race\n'
                                                          '  ran: npm test three times, which passed'))
        self.write('reach', report('5/5', 'L3'))
        merged = self.merged()
        self.assertIn('Could not verify: guess: P2 L2 src/a.py:10 - maybe a race ran: npm test', merged)
        self.assertEqual(self.block()['Score'], '4/5')

    def test_separate_claims_stay_separate(self) -> None:
        self.write('timing', report('5/5', 'L1', unverified='\n- P2 L2 src/a.py:10 - maybe a race\n'
                                                          '  ran: npm test, which passed\n- P3 L1 the log reads oddly'))
        self.write('reach', report('5/5', 'L3'))
        self.assertIn('P3, does not block', self.merged())
        self.assertEqual(self.block()['Score'], '4/5')

    def test_an_indented_bullet_is_a_claim_of_its_own(self) -> None:
        self.write('timing', report('5/5', 'L1', unverified='P2 L2 src/a.py:10 - maybe a race\n'
                                                          '  - P2 L2 src/a.py:30 - maybe a second race'))
        self.write('reach', report('5/5', 'L3'))
        self.assertEqual(self.block()['Score'], '3/5')

    def test_a_lens_named_with_a_qualifier_is_unaccounted(self) -> None:
        self.write('timing', report('5/5', 'L1, L2 not applicable'))
        self.write('reach', report('5/5', 'L3'))
        self.assertIn('L2  unaccounted: ask timing which', self.merged())

    def test_lenses_in_one_item_are_each_clear(self) -> None:
        self.write('timing', report('5/5', 'L1 L2'))
        self.write('reach', report('5/5', 'L3'))
        self.assertEqual(self.block()['Verified and clear'], 'L1, L2, L3')

    def test_an_unapplied_lens_is_the_blocking_sentence(self) -> None:
        self.write('timing', report('5/5', 'L1, L2'))
        self.assertIn('Blocking: not assessed: L3 (no report from reach)', self.merged())
