#!/usr/bin/env python3
"""Unit tests for tools/tally_run.py, on a synthetic session transcript.

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))
import tally_run  # noqa: E402


def at(second: int) -> str:
    return f'2026-09-26T00:00:{second:02d}.000Z'


def assistant(message: str, second: int, block: dict, **usage: int) -> dict:
    return {'type': 'assistant', 'timestamp': at(second),
            'message': {'id': message, 'content': [block], 'usage': usage}}


def user(second: int) -> dict:
    return {'type': 'user', 'timestamp': at(second), 'message': {'content': 'result'}}


def tool(call: str) -> dict:
    return {'type': 'tool_use', 'id': call}


class TallyTest(unittest.TestCase):
    """A grader with two turns, the first holding two parallel calls split across records, and a
    reviewer the session says stopped at its turn limit."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.session = base / 'abc.jsonl'
        subagents = base / 'abc' / 'subagents'
        subagents.mkdir(parents=True)
        grader = [user(0),
                  assistant('m1', 4, {'type': 'thinking'}, cache_read_input_tokens=100, input_tokens=5),
                  assistant('m1', 5, tool('t1'), cache_read_input_tokens=100, input_tokens=5),
                  assistant('m1', 5, tool('t2'), cache_read_input_tokens=100, input_tokens=5),
                  user(9),
                  assistant('m2', 16, tool('t3'), cache_read_input_tokens=300, input_tokens=2,
                            cache_creation_input_tokens=40)]
        self.write(subagents / 'agent-g1.jsonl', grader, {'agentType': 'pr-grade:grade', 'description': 'Grade timing'})
        self.write(subagents / 'agent-r1.jsonl', [user(0), assistant('m9', 1, tool('t9'))],
                   {'agentType': 'research', 'description': 'Review'})
        self.session.write_text(json.dumps({'type': 'user', 'message': {'content': (
            '<task-notification><task-id>r1</task-id><summary>Agent "Review" stopped at its 30-turn limit'
            '</summary></task-notification>')}}) + '\n')

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, path: Path, records: list[dict], meta: dict) -> None:
        path.write_text(''.join(json.dumps(r) + '\n' for r in records))
        path.with_suffix('.meta.json').write_text(json.dumps(meta))

    def test_a_turn_is_a_message_and_its_usage_counts_once(self) -> None:
        [grader] = tally_run.run(self.session, 'pr-grade:grade')
        self.assertEqual((grader['turns'], grader['toolCalls'], grader['parallelTurns']), (2, 3, 1))
        self.assertEqual((grader['cacheRead'], grader['input'], grader['contextAtEnd']), (400, 47, 342))

    def test_model_time_runs_from_each_result_to_the_message_answering_it(self) -> None:
        # 0 -> 4 and 9 -> 16; the second and third records of m1 add nothing.
        [grader] = tally_run.run(self.session, 'pr-grade:grade')
        self.assertEqual(grader['modelSeconds'], 11)

    def test_capped_comes_from_the_sessions_own_notice(self) -> None:
        capped = {row['agent']: row['capped'] for row in tally_run.run(self.session, None)}
        self.assertEqual(capped, {'g1': False, 'r1': True})

    def test_the_table_totals_the_rows(self) -> None:
        table = tally_run.render(tally_run.run(self.session, None))
        self.assertIn('1 of 2', table.splitlines()[-1])


if __name__ == '__main__':
    unittest.main()
