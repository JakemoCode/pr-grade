#!/usr/bin/env python3
"""Unit tests for the contract between the skill and the grade agent: what a dispatch carries, where the
agent reads the lenses, and the report fields the coordinator merges.

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENT = (ROOT / 'agents/grade.md').read_text()
SKILL = (ROOT / 'skills/pr-grade/SKILL.md').read_text()
sys.path.insert(0, str(ROOT / 'skills/pr-grade/scripts'))
import grade_block  # noqa: E402
# What a grader needs from the coordinator. A coordinator that never loads the skill sees only the
# agent's description, so each one has to be named there, in the agent's Inputs, and in the skill.
DISPATCH = ['repository', 'lens file', 'base and head', 'settled', 'declined', 'callers', 'check_then_act.py', 'proof copy',
            'report']


def frontmatter(text: str) -> dict[str, str]:
    head = text.split('---\n')[1]
    return dict(line.split(': ', 1) for line in head.splitlines() if ': ' in line)


def section(text: str, heading: str) -> str:
    """The body under a `## ` heading, up to the next one."""
    match = re.search(rf'(?ms)^## {re.escape(heading)}\n(.*?)(?=^## |\Z)', text)
    assert match, f'no "## {heading}" section'
    return match.group(1)


class DispatchTest(unittest.TestCase):
    def test_every_dispatch_input_is_named_where_each_reader_looks(self) -> None:
        places = {'the agent description': frontmatter(AGENT)['description'],
                  "the agent's Inputs": section(AGENT, 'Inputs'),
                  "the skill's section 2": section(SKILL, '2. Pick the mode and prepare the graders')}
        for name, text in places.items():
            for word in DISPATCH:
                with self.subTest(place=name, input=word):
                    self.assertIn(word, text)

    def test_the_skill_the_agent_reads_ships_with_it(self) -> None:
        paths = re.findall(r'\$\{CLAUDE_PLUGIN_ROOT\}/(\S+?)`', AGENT)
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file())

    def test_the_turn_count_the_agent_is_told_is_its_limit(self) -> None:
        limit = frontmatter(AGENT)['maxTurns']
        self.assertIn(f'You have {limit} turns', section(AGENT, 'Turns'))


class WriteTest(unittest.TestCase):
    """A worktree-isolated session refuses a heredoc whose text names git, and a report nearly always
    does, so the grader writes files with the Write tool and keeps file text off the command line."""

    def test_the_agent_has_the_write_tool(self) -> None:
        tools = [tool.strip() for tool in frontmatter(AGENT)['tools'].split(',')]
        self.assertIn('Write', tools)
        self.assertNotIn('Edit', tools)

    def test_the_agent_is_never_told_to_write_through_a_heredoc(self) -> None:
        self.assertNotIn('<<', AGENT)

    def test_the_write_rule_names_the_only_places_it_may_write(self) -> None:
        rule = re.search(r'(?m)^- Write a file with the Write tool, (.*)$', section(AGENT, 'Shell'))
        self.assertTrue(rule, 'the Shell section has no Write tool rule')
        self.assertIn('only to the report path or inside your scratch directory', rule.group(1))

    def test_the_report_is_written_with_the_write_tool(self) -> None:
        self.assertIn('Write the report to the path the dispatch names with the Write tool', section(AGENT, 'Report'))

    def test_the_agent_still_never_edits_the_repository(self) -> None:
        self.assertIn('You do not edit, stage, or commit anything in the repository.', AGENT)
        self.assertIn('Read-only on the repository', frontmatter(AGENT)['description'])


class ReportTest(unittest.TestCase):
    def report_fields(self) -> set[str]:
        block = re.search(r'(?s)```\n(Score:.*?)```', section(AGENT, 'Report')).group(1)
        return set(re.findall(r'(?m)^([A-Z][A-Za-z0-9 ]+):', block))

    def test_the_grade_block_draws_only_on_fields_a_grader_reports(self) -> None:
        grade = re.search(r'(?s)## Grade\n\n(.*?)\n\n', SKILL).group(1)
        needed = set(re.findall(r'(?m)^([A-Z][A-Za-z ]+):', grade)) - {'Mode', 'Graded'}
        self.assertTrue(needed)
        self.assertLessEqual(needed, self.report_fields())

    def test_the_merge_script_reads_every_field_a_grader_reports(self) -> None:
        for field in self.report_fields():
            with self.subTest(field=field):
                self.assertTrue(grade_block.REPORT_LINE.match(f'{field}: x'), f'grade_block.merge does not read `{field}`')

    def test_the_finding_line_names_its_symbol_where_each_reader_looks(self) -> None:
        # A finding one line off reads as another place to anything scoring by line, so both the grader and
        # the coordinator are shown the same shape, and the merge reads it.
        self.assertIn('P<n> L<k> <file>:<line> in <symbol> - <title>', section(AGENT, 'Report'))
        # Section 6's template holds its own `## Grade` line, which ends a section read.
        self.assertIn('P1 <file>:<line> in <symbol> - <title>', SKILL)
        self.assertIn('never by counting from a hunk header', section(AGENT, 'Report'))
        example = re.search(r'`(P1 L4 \S+:\d+ in .*?)`', section(AGENT, 'Report'))
        self.assertTrue(example, "the Report section gives no example finding line")
        self.assertEqual(grade_block.read_report(example.group(1))['findings'][0]['symbol'],
                         'RunReportRenderer.renderHtml')

    def test_the_fields_the_merge_names_are_in_the_report(self) -> None:
        merge = section(SKILL, '5. Score it')
        for field in ('Outside my lenses', 'Could not verify', 'Verified and clear', 'Blocking'):
            with self.subTest(field=field):
                self.assertIn(f'`{field}`', merge)
                self.assertIn(field, self.report_fields())


if __name__ == '__main__':
    unittest.main()
