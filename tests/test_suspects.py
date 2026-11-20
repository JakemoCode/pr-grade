#!/usr/bin/env python3
"""Unit tests for the `Suspects:` report field: how grade_block.py reads it, and what the merge does with each
line a grader writes for a suspect it was handed.

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'skills/pr-grade/scripts'))
import grade_block  # noqa: E402

AGENT = (ROOT / 'agents/grade.md').read_text()
SCORING = (ROOT / 'skills/pr-grade/scoring.md').read_text()
FIXTURES = ROOT / 'tests/fixtures/suspects'
HEAD = 'b' * 40
FINDING = 'P2 L1 src/a.py:9 in f - late write lands\ntwo writers race at line 9\n'
UNSETTLED = 'floor; settle the items above, then write the score'


def printed(merged: str) -> dict:
    """The block's fields as the merge printed them: `parse` reads only the ones `check` needs."""
    block = merged[merged.index('\n## Grade\n'):]
    return {**dict(re.findall(r'(?m)^(Blocking|Could not verify): (.*)$', block)), **grade_block.parse(block)}


def left(merged: str) -> list[str]:
    """The items the merge leaves to the coordinator that concern suspects."""
    return [line for line in merged.splitlines() if line.startswith('  ') and 'suspect' in line and 'ask' in line]


def floor(block: dict) -> str:
    return block['Score'].split()[0]


def report(suspects: str, *, findings: str = FINDING, verified: str = 'L2, L3', unverified: str = 'none') -> str:
    return (f'Score: 4/5\nBlocking: nothing\n{findings}Verified and clear: {verified}\n'
            f'Could not verify: {unverified}\nOutside my lenses: none\nL7 candidates: none given\n'
            + (f'Suspects:\n{suspects}' if suspects else ''))


class ReadTest(unittest.TestCase):
    def test_a_report_without_the_field_reads_as_before(self) -> None:
        read = grade_block.read_report(report(''))
        self.assertEqual(read['suspects'], [])
        self.assertNotIn('Suspects', read['fields'])

    def test_each_suspect_line_is_read_with_its_verdict_and_its_command(self) -> None:
        read = grade_block.read_report(report(
            'S1 proven: P2 L1 src/a.py:9, above.\n'
            'S2 refuted: P2, a second writer after the first commit.\n'
            '  Command: `python3 -c "import a; a.f(); a.f()"`\n'
            '  Output: `2`\n'
            'S3 could not verify: P3, the fixture needs a database the copy cannot start.\n'))
        self.assertEqual([(s['id'], s['verdict']) for s in read['suspects']],
                         [('S1', 'proven'), ('S2', 'refuted'), ('S3', 'could not verify')])
        self.assertEqual(read['suspects'][1]['command'], 'python3 -c "import a; a.f(); a.f()"')
        self.assertEqual(read['suspects'][1]['output'], '`2`')
        self.assertEqual(read['suspects'][2]['rank'], 'P3')
        self.assertEqual(len(read['fields']['Suspects']), 3)

    def test_a_fenced_output_stays_with_its_suspect(self) -> None:
        read = grade_block.read_report(report(
            'S1 refuted: P2, the empty list.\nCommand: `pytest tests/test_a.py`\n```\nScore: 1 passed\nL4: ok\n```\n'
            'S2 could not verify: P3, no run.\n'))
        self.assertEqual([s['id'] for s in read['suspects']], ['S1', 'S2'])
        self.assertIn('Score: 1 passed', read['suspects'][0]['output'])
        self.assertNotIn('L4', read['fields'])

    def test_a_verdict_is_read_whatever_its_case_or_punctuation(self) -> None:
        read = grade_block.read_report(report('S1 - Proven, P2 L1 src/a.py:9.\nS2. Could not verify - P3, no run.\n'))
        self.assertEqual([s['verdict'] for s in read['suspects']], ['proven', 'could not verify'])

    def test_a_line_with_no_verdict_the_merge_knows_has_none(self) -> None:
        read = grade_block.read_report(report('S1 latent: the bases are frozen.\n'))
        self.assertIsNone(read['suspects'][0]['verdict'])


class MergeTest(unittest.TestCase):
    """One grader holding L1 to L3, handed suspects through grade.json."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.meta(suspects={'grade': 1})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def meta(self, **extra: object) -> None:
        (self.root / 'grade.json').write_text(json.dumps(
            {'mode': 'subagent', 'head': HEAD, 'lenses': ['L1', 'L2', 'L3'], 'groups': {'grade': ['L1', 'L2', 'L3']},
             **extra}))

    def merged(self, text: str) -> str:
        (self.root / 'grade').mkdir(exist_ok=True)
        (self.root / 'grade' / 'grade.md').write_text(text)
        return grade_block.merge(self.root)

    def block(self, text: str) -> dict:
        return printed(self.merged(text))

    def test_a_grade_handed_no_suspects_reads_as_today(self) -> None:
        self.meta()
        today = self.merged(report(''))
        self.assertNotIn('suspect', today.lower())
        self.assertEqual((floor(printed(today)), left(today)), ('4/5', []))
        for handed in ({}, {'grade': 0}):
            with self.subTest(handed=handed):
                self.meta(suspects=handed)
                self.assertEqual(self.merged(report('')), today)

    def test_a_refutation_with_a_run_closes_the_suspect(self) -> None:
        block = self.block(report('S1 refuted: P2, a second writer.\n  Command: `python3 -c "import a; a.f()"`\n'
                                  '  Output: `ok`\n'))
        self.assertEqual((floor(block), block['Could not verify']), ('4/5', 'none'))

    def test_a_refutation_written_on_one_line_with_its_run_closes_the_suspect(self) -> None:
        block = self.block(report('S1 refuted: P2, a second writer. Command: `pytest tests/t.py` Output: 3 passed\n'))
        self.assertEqual(block['Could not verify'], 'none')

    def test_a_refutation_with_no_command_moves_to_could_not_verify(self) -> None:
        merged = self.merged(report('S1 refuted: P2, the guard at line 40 stops it.\n'))
        block = printed(merged)
        self.assertIn('refuted without a run', block['Could not verify'])
        self.assertIn('S1', block['Could not verify'])
        self.assertEqual(floor(block), '3/5')

    def test_a_refutation_with_a_command_and_no_output_is_not_a_run(self) -> None:
        block = self.block(report('S1 refuted: P2, ran it.\n  Command: `pytest tests/t.py`\n'))
        self.assertIn('refuted without a run', block['Could not verify'])

    def test_a_refutation_whose_command_is_none_is_not_a_run(self) -> None:
        block = self.block(report('S1 refuted: P2, read it.\n  Command: none\n  Output: n/a\n'))
        self.assertIn('refuted without a run', block['Could not verify'])

    def test_a_search_or_a_read_is_a_citation_and_not_a_run(self) -> None:
        for command in ('grep -n guard src/a.py', 'git -C /work/repo grep -n -F -e guard HEAD --',
                        'cd /work/copy && rg guard | head -3', 'git show HEAD:src/a.py', 'sed -n 30,50p src/a.py',
                        "bash -c 'grep -n guard src/a.py'", 'env LC_ALL=C grep guard src/a.py',
                        'ls src | xargs -n1 grep -l guard', 'timeout 5 cat src/a.py'):
            with self.subTest(command=command):
                block = self.block(report(f'S1 refuted: P2, the guard exists.\n  Command: `{command}`\n'
                                          '  Output: `40: if guard:`\n'))
                self.assertIn('refuted without a run', block['Could not verify'])

    def test_a_run_inside_a_wrapper_is_a_run(self) -> None:
        for command in ("bash -c 'cd /work/copy && pytest t.py'", 'env CI=1 npx vitest run t.test.ts', 'timeout 30 node t.js'):
            with self.subTest(command=command):
                block = self.block(report(f'S1 refuted: P2, ran it.\n  Command: `{command}`\n  Output: `1 passed`\n'))
                self.assertEqual(block['Could not verify'], 'none')

    def test_a_run_chained_after_a_read_is_a_run(self) -> None:
        block = self.block(report('S1 refuted: P2, ran it.\n  Command: `cd /work/copy && grep -c x a.py && pytest t.py`\n'
                                  '  Output: `1 passed`\n'))
        self.assertEqual(block['Could not verify'], 'none')

    def test_an_unranked_refutation_with_no_run_blocks(self) -> None:
        block = self.block(report('S1 refuted: no input I could construct breaks it. Not run as a test.\n'))
        self.assertEqual(floor(block), '3/5')

    def test_a_p3_refutation_with_no_run_leaves_the_score(self) -> None:
        merged = self.merged(report('S1 refuted: P3, a message only.\n'))
        self.assertIn('refuted without a run', printed(merged)['Could not verify'])
        self.assertIn('(P3, does not block)', merged)

    def test_a_suspect_not_verified_at_p3_leaves_the_score(self) -> None:
        block = self.block(report('S1 could not verify: P3, a nit whose run would cost a fixture.\n'))
        self.assertEqual(floor(block), '4/5')
        self.assertIn('S1', block['Could not verify'])

    def test_a_suspect_not_verified_at_p2_blocks(self) -> None:
        block = self.block(report('S1 could not verify: P2 L1, the copy cannot start the database.\n'))
        self.assertEqual(floor(block), '3/5')

    def test_a_suspect_not_verified_with_no_rank_blocks(self) -> None:
        block = self.block(report('S1 could not verify: the copy cannot start the database.\n'))
        self.assertEqual(floor(block), '3/5')

    def test_a_handed_suspect_with_no_line_is_left_to_the_coordinator(self) -> None:
        self.meta(suspects={'grade': 3})
        merged = self.merged(report('S1 proven: P2 L1 src/a.py:9, above.\nS3 could not verify: P3, no run.\n'))
        self.assertRegex(merged, r'(?m)^  .*S2.*ask .*grade')
        self.assertEqual(floor(printed(merged)), '4/5')

    def test_handed_suspects_with_no_field_at_all_are_each_left_to_the_coordinator(self) -> None:
        self.meta(suspects={'grade': 2})
        merged = self.merged(report(''))
        self.assertRegex(merged, r'(?m)^  .*S1, S2.*ask .*grade')
        self.assertTrue(left(merged))
        self.assertIn(UNSETTLED, printed(merged)['Score'])

    def test_a_line_with_no_verdict_the_merge_reads_counts_as_no_line(self) -> None:
        merged = self.merged(report('S1 latent: the bases are frozen.\n'))
        self.assertRegex(merged, r'(?m)^  .*S1.*ask .*grade')
        self.assertTrue(left(merged))
        self.assertIn(UNSETTLED, printed(merged)['Score'])

    def test_a_proven_suspect_naming_no_finding_in_its_report_is_left_to_the_coordinator(self) -> None:
        merged = self.merged(report('S1 proven: P2 L1 src/b.py:40, above.\n'))
        self.assertRegex(merged, r'(?m)^  .*S1.*names no finding')
        self.assertTrue(left(merged))
        self.assertIn(UNSETTLED, printed(merged)['Score'])

    def test_a_proven_suspect_at_its_finding_settles_nothing(self) -> None:
        merged = self.merged(report('S1 proven: P2 L1 src/a.py:9, above.\n'))
        self.assertEqual((floor(printed(merged)), left(merged)), ('4/5', []))

    def test_a_proven_p3_keeps_its_reason_in_the_print(self) -> None:
        merged = self.merged(report('S1 proven: P3 L1 src/a.py:9, only because no record lacks `hit` today.\n',
                                    findings='P3 L1 src/a.py:9 in f - late write lands\nwhat breaks\n',
                                    verified='L1, L2, L3'))
        line = next(line for line in merged.splitlines() if line.lstrip().startswith('grade S1'))
        self.assertIn('only because no record lacks `hit` today', line)
        self.assertIn('re-rank', line)

    def test_a_proven_p3_sharing_its_line_with_a_p2_is_matched_to_the_p3(self) -> None:
        # The PR 1280 run: the grader's own P2 and the suspect's P3 both sat at run-report-store.ts:292.
        merged = self.merged(report('S1 proven: P3, L1, src/a.py:9; a P3 because only the error type reads wrong.\n',
                                    findings=FINDING + 'P3 L1 src/a.py:9 in f - throws TypeError\nwhat breaks\n'))
        line = next(line for line in merged.splitlines() if line.lstrip().startswith('grade S1'))
        self.assertIn('P3 at src/a.py:9', line)
        self.assertIn('only the error type reads wrong', line)

    def test_a_suspect_beyond_the_count_handed_is_read_like_the_rest(self) -> None:
        block = self.block(report('S1 proven: P2 L1 src/a.py:9, above.\nS2 refuted: P2, none.\n'))
        self.assertIn('S2', block['Could not verify'])

    def test_the_refuted_by_argument_case_from_pr_1435_ends_under_could_not_verify(self) -> None:
        # ForgeSmith-OS PR 1435: a true defect closed as refuted, "Not run as a test" beside it.
        lenses = grade_block.DEFAULT_LENSES
        (self.root / 'grade.json').write_text(json.dumps(
            {'mode': 'subagent', 'head': HEAD, 'lenses': lenses, 'groups': {'grade': lenses}, 'suspects': {'grade': 4}}))
        merged = self.merged((FIXTURES / 'pr1435-refuted-by-argument.md').read_text())
        block = printed(merged)
        self.assertRegex(block['Could not verify'], r'S2 .*refuted without a run.*revert of a revert')
        self.assertNotRegex(merged, r'(?m)^  .*S[134]\b.*(ask|names no finding)')
        self.assertTrue(block['Score'].startswith('3/5'))


class ContractTest(unittest.TestCase):
    def section(self, text: str, heading: str) -> str:
        return re.search(rf'(?ms)^## {re.escape(heading)}\n(.*?)(?=^## |\Z)', text).group(1)

    def test_the_hand_off_is_described_where_the_grader_reads_its_dispatch(self) -> None:
        inputs = self.section(AGENT, 'Inputs')
        self.assertIn('`Suspects from a first read:`', inputs)
        self.assertIn('`S1`', inputs)

    def test_the_report_template_carries_each_verdict_the_merge_reads(self) -> None:
        template = re.search(r'(?s)```\n(Score:.*?)```', self.section(AGENT, 'Report')).group(1)
        for line in ('Suspects: ', 'S<n> proven: ', 'S<n> refuted: ', 'Command: ', 'Output: ', 'S<n> could not verify: '):
            with self.subTest(line=line):
                self.assertIn(line, template)
        read = grade_block.read_report('Suspects:\nS1 proven: P2 L1 a.py:3\n')
        self.assertEqual(read['suspects'][0]['verdict'], 'proven')

    def test_the_rule_for_a_graders_own_suspicion_is_in_its_work(self) -> None:
        self.assertIn('never argued away', self.section(AGENT, 'Work'))

    def test_section_5_says_what_the_coordinator_does_with_each_kind(self) -> None:
        scoring = self.section(SCORING, '5. Score it')
        for phrase in ('`Suspects:`', 'refuted without a run', 'no line', 're-rank'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, scoring)


if __name__ == '__main__':
    unittest.main()
