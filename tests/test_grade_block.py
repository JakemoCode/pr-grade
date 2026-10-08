#!/usr/bin/env python3
"""Unit tests for grade_block.py: the grade a pull request must carry before it goes ready.

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
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

    def test_each_lens_in_an_item_may_carry_its_own_note(self) -> None:
        self.assertEqual(self.problems(body(**{'Verified and clear': 'L1 (a) L2 (b), L3'})), [])
        self.assertEqual(self.problems(body(**{'Verified and clear': 'L1 (a) L2, L3 (c (nested))'})), [])

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

    def test_unclassified_code_needs_a_subagent_under_a_base_config_without_ordinary(self) -> None:
        refused = self.run_check(text=body(Mode='in-thread', **{'Verified and clear': ', '.join(f'L{n}' for n in range(1, 9))}),
                                 files=('src/x.py',), base={grade_mode.CONFIG: json.dumps({'silent': []})})[1]
        self.assertTrue(any('requires subagent' in p and 'unclassified' in p for p in refused))

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
        (self.root / group / 'grade.md').write_text(text)

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

    def test_an_indented_claim_is_a_claim_of_its_own(self) -> None:
        self.write('timing', report('5/5', 'L1', unverified='L1 P2 src/a.py:10 - maybe a race\n'
                                                          '    L2 P2 whether close leaks the handle'))
        self.write('reach', report('5/5', 'L3'))
        self.assertEqual(self.block()['Score'], '3/5')

    def test_an_indented_possessive_lens_id_is_prose(self) -> None:
        self.write('timing', report('5/5', 'L2', unverified='L1 P2 src/a.py:10 - maybe a race\n'
                                                          "    L2's pool releases the handle on close"))
        self.write('reach', report('5/5', 'L3'))
        self.assertIn("timing: L1 P2 src/a.py:10 - maybe a race L2's pool releases the handle on close\n",
                      self.merged())
        self.assertEqual(self.block()['Score'], '4/5')

    def test_an_indented_possessive_below_a_p3_claim_still_counts(self) -> None:
        # Joined to the P3 above it, the claim would stop counting.
        self.write('timing', report('5/5', 'L1', unverified="\n- L1 P3 the log reads oddly\n"
                                                          "    L2's pool may leak the handle under load"))
        self.write('reach', report('5/5', 'L3'))
        self.assertEqual(self.block()['Score'], '4/5')

    def test_an_indented_possessive_naming_a_rank_is_a_claim(self) -> None:
        self.write('timing', report('5/5', 'L1', unverified='L1 P2 src/a.py:10 - maybe a race\n'
                                                          "    L2's close would be P2: leaks the handle"))
        self.write('reach', report('5/5', 'L3'))
        self.assertEqual(self.block()['Score'], '3/5')

    def test_an_indented_unranked_claim_still_counts(self) -> None:
        # Prose and an unranked claim read alike after a bare lens id; splitting keeps the floor safe.
        self.write('timing', report('5/5', 'L1', unverified='L1 P2 src/a.py:10 - maybe a race\n'
                                                          '    L2 handle may leak on close'))
        self.write('reach', report('5/5', 'L3'))
        self.assertEqual(self.block()['Score'], '3/5')

    def test_a_possessive_under_verified_does_not_join_the_lens_above(self) -> None:
        self.write('timing', report('5/5', 'L1 (proved by test_x)\n    L2 (no await)\n'
                                                "    L2's pool checked by hand"))
        self.write('reach', report('5/5', 'L3'))
        self.assertEqual(self.block()['Verified and clear'], 'L1 (proved by test_x), L2 (no await), L3')

    def test_one_lens_per_line_with_notes_clears_each(self) -> None:
        self.write('timing', report('5/5', '\nL1 (proved by test_x)\nL2 (no await)'))
        self.write('reach', report('5/5', 'L3'))
        self.assertEqual(self.block()['Verified and clear'], 'L1 (proved by test_x), L2 (no await), L3')

    def test_the_draft_keeps_each_graders_note(self) -> None:
        # A note can say the grader judged the lens not applicable; a checker can only refuse what it sees.
        self.write('timing', report('5/5', 'L1 (not applicable), L2'))
        self.write('reach', report('5/5', 'L3 (no caller outside src/b.py, a.py:4)'))
        block = self.block()
        self.assertEqual(block['Verified and clear'], 'L1 (not applicable), L2, L3 (no caller outside src/b.py, a.py:4)')
        self.assertEqual(grade_block.verified_lenses(block['Verified and clear']), set(LENSES))

    def test_a_note_on_a_lens_a_finding_holds_is_dropped(self) -> None:
        self.write('timing', report('4/5', 'L1 (no await), L2', findings='P1 L1 src/a.py:9 - late write lands\n'))
        self.write('reach', report('5/5', 'L3'))
        self.assertEqual(self.block()['Verified and clear'], 'L2, L3')

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

    def skip(self, *lenses: str, groups: dict | None = None) -> None:
        (self.root / 'grade.json').write_text(json.dumps(
            {'mode': 'fan-out', 'head': HEAD, 'lenses': LENSES, 'groups': groups or {'timing': ['L1', 'L2']},
             'skipped': list(lenses)}))

    def test_a_skipped_lens_is_applied_and_drafted_as_not_applicable(self) -> None:
        self.skip('L3')
        self.write('timing', report('5/5', 'L1, L2'))
        merged = self.merged()
        self.assertIn('L3  not applicable: skipped by grade_prep.py --skip', merged)
        self.assertIn('reports: timing 5/5\n', merged)
        block = self.block()
        self.assertEqual((block['Score'], block['Verified and clear']), ('5/5', 'L1, L2, L3 (not applicable)'))

    def test_a_skipped_lens_keeps_the_lens_files_order(self) -> None:
        self.skip('L2', groups={'timing': ['L1'], 'reach': ['L3']})
        self.write('timing', report('5/5', 'L1'))
        self.write('reach', report('5/5', 'L3'))
        self.assertEqual(self.block()['Verified and clear'], 'L1, L2 (not applicable), L3')

    def test_a_finding_on_a_skipped_lens_outranks_the_skip(self) -> None:
        # Skipping says no rule put the lens in scope; a proven defect under it still counts.
        self.skip('L3')
        self.write('timing', report('4/5', 'L1, L2', findings='P2 L3 src/a.py:9 - a caller skips the guard\n'))
        block = self.block()
        self.assertEqual((block['Score'], block['Verified and clear']), ('4/5', 'L1, L2'))
        self.assertIn('L3  P2 at src/a.py:9', self.merged())

    def test_an_open_claim_on_a_skipped_lens_outranks_the_skip(self) -> None:
        self.skip('L3')
        self.write('timing', report('5/5', 'L1, L2', unverified='L3 would be P2: a caller may skip the guard'))
        block = self.block()
        self.assertEqual((block['Score'], block['Verified and clear']), ('4/5', 'L1, L2'))

    def test_a_grade_json_without_skipped_skips_nothing(self) -> None:
        # A root from an older grade_prep.py has no `skipped` key.
        self.write('timing', report('5/5', 'L1, L2'))
        self.assertIn('L3  no report from reach', self.merged())
        self.assertEqual(self.block()['Score'], '2/5')

    def run_merge(self, *reports: str, stdin: str = '') -> subprocess.CompletedProcess:
        args = [arg for given in reports for arg in ('--report', given)]
        return subprocess.run([sys.executable, grade_block.__file__, 'merge', str(self.root), *args],
                              input=stdin, capture_output=True, text=True)

    def test_a_report_given_on_stdin_stands_in_for_a_missing_file(self) -> None:
        # A grader that replied with its report but wrote no file, passed on by a coordinator that cannot write.
        self.write('timing', report('5/5', 'L1, L2'))
        run = self.run_merge('reach=-', stdin=report('5/5', 'L3'))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(grade_block.parse(run.stdout)['Score'], '5/5')
        self.assertIn('L3  clear', run.stdout)

    def test_a_report_given_as_a_file_stands_in_for_a_missing_file(self) -> None:
        self.write('timing', report('5/5', 'L1, L2'))
        reply = self.root / 'reply.txt'
        reply.write_text(report('5/5', 'L3'))
        run = self.run_merge(f'reach={reply}')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(grade_block.parse(run.stdout)['Verified and clear'], 'L1, L2, L3')

    def test_a_report_for_a_group_that_wrote_its_file_is_refused(self) -> None:
        # The file is the grader's own words; text from elsewhere never replaces it.
        self.write('timing', report('5/5', 'L1, L2'))
        self.write('reach', report('4/5', '', findings='P1 L3 src/a.py:9 - caller sees stale row\n'))
        run = self.run_merge('reach=-', stdin=report('5/5', 'L3'))
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('reach/grade.md', run.stderr)

    def test_an_empty_report_is_refused(self) -> None:
        self.write('timing', report('5/5', 'L1, L2'))
        run = self.run_merge('reach=-', stdin='\n')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('empty', run.stderr)

    def test_a_report_for_a_group_the_grade_does_not_have_is_refused(self) -> None:
        run = self.run_merge('timimg=-', stdin=report('5/5', 'L1, L2'))
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('timimg', run.stderr)

    def test_a_report_file_that_does_not_exist_is_refused(self) -> None:
        run = self.run_merge(f"reach={self.root / 'nowhere.txt'}")
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('nowhere.txt', run.stderr)


class FindingLineTest(unittest.TestCase):
    """`P<n> L<k> <file>:<line> in <symbol> - <title>`, with `<file>:<start>-<end>` for a span."""

    def finding(self, line: str) -> dict:
        found = grade_block.read_report(f'Score: 4/5\n{line}\nwhat breaks\n')['findings']
        self.assertEqual(len(found), 1, line)
        return found[0]

    def test_a_line_and_its_symbol_are_read(self) -> None:
        found = self.finding('P1 L4 src/reports/run-report-renderer.ts:78 in RunReportRenderer.renderHtml - cache dropped')
        self.assertEqual((found['file'], found['start'], found['end'], found['symbol'], found['where'], found['title']),
                         ('src/reports/run-report-renderer.ts', 78, 78, 'RunReportRenderer.renderHtml',
                          'src/reports/run-report-renderer.ts:78', 'cache dropped'))

    def test_a_span_is_read(self) -> None:
        found = self.finding('P2 L1 L7 src/a.py:9-12 in drain - late write lands')
        self.assertEqual((found['lenses'], found['start'], found['end'], found['where'], found['symbol']),
                         (['L1', 'L7'], 9, 12, 'src/a.py:9-12', 'drain'))

    def test_the_form_without_a_symbol_still_reads(self) -> None:
        # Reports written before the symbol was asked for keep scoring.
        found = self.finding('P1 L1 src/a.py:9 - late write - lands twice')
        self.assertEqual((found['where'], found['start'], found['end'], found['symbol'], found['title']),
                         ('src/a.py:9', 9, 9, None, 'late write - lands twice'))

    def test_a_symbol_written_with_words_after_it_still_reads(self) -> None:
        # A finding that failed to parse would vanish from the score, so the symbol runs to the first ` - `.
        found = self.finding('P1 L4 src/a.ts:78 in renderHtml (method) - cache dropped')
        self.assertEqual((found['symbol'], found['title']), ('renderHtml (method)', 'cache dropped'))


RENDER_BASE = '''def lookup(store, key):
    return store.get(key)


class Renderer:
    def render_html(self, store, key):
        rendered = lookup(store, key)
        if rendered is not None:
            return rendered
        return self.build(key)

    def build(self, key):
        return key
'''
RENDER_HEAD = RENDER_BASE.replace('            return rendered\n', '            pass\n')
RENDER_TS_BASE = '''export class RunReportRenderer {
  renderHtml(store: Map<string, string>, key: string): string {
    const rendered = store.get(key);
    if (rendered !== undefined) return rendered;
    return key;
  }
}
'''
RENDER_TS_HEAD = RENDER_TS_BASE.replace('if (rendered !== undefined) return rendered;', 'void rendered;')
TYPESCRIPT = os.environ.get('PR_GRADE_TYPESCRIPT')
TS_AVAILABLE = bool(shutil.which('node') and TYPESCRIPT and Path(TYPESCRIPT).exists())


class AnchorTest(unittest.TestCase):
    """The merge checks each finding's line against the lines the graded diff changed. In the head,
    src/render.py:9 changed inside Renderer.render_html, src/render.ts:4 inside RunReportRenderer.renderHtml,
    and the line after src/other.py:2 was removed."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo, self.root = Path(self.tmp.name) / 'repo', Path(self.tmp.name) / 'proof'
        (self.repo / 'src').mkdir(parents=True)
        self.root.mkdir()
        self.files({'src/render.py': RENDER_BASE, 'src/render.ts': RENDER_TS_BASE,
                    'src/other.py': 'a = 1\nb = 2\nc = 3\nd = 4\n', 'src/untouched.py': 'x = 1\n',
                    'src/tie.py': 'a = 1\nb = 2\nc = 3\nd = 4\ne = 5\n'})
        base = self.commit()
        self.files({'src/render.py': RENDER_HEAD, 'src/render.ts': RENDER_TS_HEAD, 'src/other.py': 'a = 1\nb = 2\nd = 4\n',
                    'src/tie.py': 'a = 1\nb = 20\nc = 3\nd = 40\ne = 5\n'})
        head = self.commit()
        (self.root / 'grade.json').write_text(json.dumps(
            {'repository': str(self.repo), 'mode': 'subagent', 'base': base, 'head': head, 'lenses': LENSES,
             'groups': {'all': LENSES}}))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def files(self, contents: dict[str, str]) -> None:
        for path, text in contents.items():
            (self.repo / path).write_text(text)

    def git(self, *args: str) -> str:
        return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', '-C', str(self.repo), *args],
                              check=True, capture_output=True, text=True).stdout.strip()

    def commit(self) -> str:
        if not (self.repo / '.git').exists():
            self.git('init', '-q', '-b', 'main')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'c')
        return self.git('rev-parse', 'HEAD')

    def merged(self, *findings: str) -> str:
        (self.root / 'all').mkdir(exist_ok=True)
        (self.root / 'all' / 'grade.md').write_text(
            report('4/5', 'L2, L3', findings=''.join(f'{line}\nwhat breaks\n' for line in findings)))
        return grade_block.merge(self.root)

    def test_a_finding_on_a_changed_line_is_not_flagged(self) -> None:
        merged = self.merged('P1 L1 src/render.py:9 in Renderer.render_html - cached render dropped')
        self.assertNotIn('anchor', merged)

    def test_a_finding_one_line_off_names_the_changed_line_and_its_symbol(self) -> None:
        # The EngOS canary: the proof mutated line 78 and the finding cited the lookup on line 77.
        merged = self.merged('P1 L1 src/render.py:8 in Renderer.render_html - cached render dropped')
        self.assertIn('P1 src/render.py:8 in Renderer.render_html - cached render dropped  [all L1]  (anchor: touches '
                      'no line the graded diff changed; nearest is src/render.py:9 in Renderer.render_html)', merged)

    # CI sets PR_GRADE_REQUIRE_TS, so a missing compiler fails these there instead of skipping them.
    @unittest.skipUnless(TS_AVAILABLE or os.environ.get('PR_GRADE_REQUIRE_TS') == '1', 'needs node and PR_GRADE_TYPESCRIPT')
    def test_a_typescript_finding_one_line_off_names_the_method(self) -> None:
        merged = self.merged('P1 L1 src/render.ts:3 in RunReportRenderer.renderHtml - cached render dropped')
        self.assertIn('(anchor: touches no line the graded diff changed; nearest is src/render.ts:4 in '
                      'RunReportRenderer.renderHtml)', merged)

    @unittest.skipUnless(TS_AVAILABLE or os.environ.get('PR_GRADE_REQUIRE_TS') == '1', 'needs node and PR_GRADE_TYPESCRIPT')
    def test_typescript_is_found_where_the_repository_finds_it(self) -> None:
        # Installed above the repository, as in a monorepo, with no PR_GRADE_TYPESCRIPT to fall back on.
        (Path(self.tmp.name) / 'node_modules').mkdir()
        (Path(self.tmp.name) / 'node_modules' / 'typescript').symlink_to(Path(TYPESCRIPT or '').resolve())
        with mock.patch.dict(os.environ, {'PR_GRADE_TYPESCRIPT': ''}):
            merged = self.merged('P1 L1 src/render.ts:3 - cached render dropped')
        self.assertIn('nearest is src/render.ts:4 in RunReportRenderer.renderHtml)', merged)

    def test_a_finding_between_two_changed_lines_names_both(self) -> None:
        merged = self.merged('P1 L1 src/tie.py:3 - c is read stale')
        self.assertIn('(anchor: touches no line the graded diff changed; nearest are src/tie.py:2 and src/tie.py:4)',
                      merged)

    def test_a_path_written_from_the_dot_reads_as_the_path(self) -> None:
        merged = self.merged('P1 L1 ./src/render.py:9 in Renderer.render_html - cached render dropped')
        self.assertNotIn('anchor', merged)
        self.assertIn('P1 src/render.py:9 in Renderer.render_html', merged)

    def test_a_path_written_from_the_repository_reads_as_the_path(self) -> None:
        # EngOS issue #925: a grader cited the file by the repository's absolute path.
        merged = self.merged(f'P1 L1 {self.repo}/src/render.py:9 in Renderer.render_html - cached render dropped')
        self.assertNotIn('anchor', merged)
        self.assertIn('P1 src/render.py:9 in Renderer.render_html', merged)

    def test_a_path_written_from_a_proof_copy_reads_as_the_path(self) -> None:
        merged = self.merged(f'P1 L1 {self.root}/all/repo/src/render.py:9 - cached render dropped',
                             f'P1 L2 {self.root}/all/repo/src/render.py:9 - stale row')
        self.assertNotIn('anchor', merged)
        self.assertIn('P1 src/render.py:9 - cached render dropped', merged)
        self.assertEqual(merged.count('same line as another finding'), 2)

    def test_a_path_written_through_a_link_to_the_repository_reads_as_the_path(self) -> None:
        # git prints the resolved path, as /private/var does for /var on macOS, so either spelling is the repository.
        link = Path(self.tmp.name) / 'link'
        link.symlink_to(self.tmp.name)
        meta = json.loads((self.root / 'grade.json').read_text())
        meta['repository'] = str(link / 'repo')
        (self.root / 'grade.json').write_text(json.dumps(meta))
        merged = self.merged(f'P1 L1 {self.repo.resolve()}/src/render.py:9 - cached render dropped')
        self.assertNotIn('anchor', merged)
        self.assertIn('P1 src/render.py:9 - cached render dropped', merged)

    def test_an_absolute_path_outside_the_repository_keeps_its_note(self) -> None:
        elsewhere = Path(self.tmp.name) / 'elsewhere' / 'src' / 'render.py'
        merged = self.merged(f'P1 L1 {elsewhere}:9 - cached render dropped')
        self.assertIn(f'(anchor: {elsewhere} is not in the head commit)', merged)

    def test_a_column_after_the_line_is_dropped(self) -> None:
        merged = self.merged('P1 L1 src/render.py:9:13 in Renderer.render_html - cached render dropped')
        self.assertNotIn('anchor', merged)
        self.assertIn('P1 src/render.py:9 in Renderer.render_html', merged)

    def test_a_root_without_a_base_names_what_is_missing(self) -> None:
        meta = json.loads((self.root / 'grade.json').read_text())
        del meta['base'], meta['repository']
        (self.root / 'grade.json').write_text(json.dumps(meta))
        self.assertIn('anchors: not checked, grade.json names no repository or base',
                      self.merged('P1 L1 src/render.py:8 - cached render dropped'))

    @unittest.skipUnless(shutil.which('node'), 'needs node')
    def test_an_adapter_that_prints_no_json_leaves_the_flag_without_a_symbol(self) -> None:
        adapter = Path(self.tmp.name) / 'adapter.cjs'
        adapter.write_text("process.stdout.write('not json');\n")
        with mock.patch.object(grade_block, 'TS_ADAPTER', adapter):
            merged = self.merged('P1 L1 src/render.ts:3 - cached render dropped')
        self.assertIn('(anchor: touches no line the graded diff changed; nearest is src/render.ts:4)', merged)

    def test_a_span_that_holds_a_changed_line_is_not_flagged(self) -> None:
        self.assertNotIn('anchor', self.merged('P1 L1 src/render.py:7-10 in Renderer.render_html - cached render dropped'))

    def test_the_head_line_where_removed_code_stood_is_not_flagged(self) -> None:
        self.assertNotIn('anchor', self.merged('P2 L1 src/other.py:3 - the c binding is gone'))

    def test_a_finding_in_a_file_the_diff_left_alone_is_flagged(self) -> None:
        # Flagged, never refused: L4 finds a caller the change broke in code it never touched.
        merged = self.merged('P2 L1 src/untouched.py:1 - reads the dropped render')
        self.assertIn('(anchor: the graded diff changed nothing in src/untouched.py)', merged)
        self.assertEqual(grade_block.parse(merged)['Score'], '4/5')

    def test_a_finding_in_a_file_the_head_lacks_is_flagged(self) -> None:
        merged = self.merged('P2 L1 src/gone.py:1 - reads the dropped render')
        self.assertIn('(anchor: src/gone.py is not in the head commit)', merged)

    def test_a_root_without_a_repository_says_the_anchors_went_unchecked(self) -> None:
        # A grade.json from before 0.6.0 names no repository.
        meta = json.loads((self.root / 'grade.json').read_text())
        del meta['repository']
        (self.root / 'grade.json').write_text(json.dumps(meta))
        self.assertIn('anchors: not checked, grade.json names no repository',
                      self.merged('P1 L1 src/render.py:8 - cached render dropped'))

    def test_an_unreadable_head_says_the_anchors_went_unchecked(self) -> None:
        meta = json.loads((self.root / 'grade.json').read_text())
        meta['head'] = 'f' * 40
        (self.root / 'grade.json').write_text(json.dumps(meta))
        self.assertIn('anchors: not checked, git diff', self.merged('P1 L1 src/render.py:8 - cached render dropped'))

    def test_two_findings_whose_spans_overlap_are_flagged(self) -> None:
        merged = self.merged('P1 L1 src/render.py:9 - cached render dropped', 'P1 L2 src/render.py:8-10 - stale row')
        self.assertEqual(merged.count('same line as another finding'), 2)
