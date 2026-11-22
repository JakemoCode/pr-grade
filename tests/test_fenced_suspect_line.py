#!/usr/bin/env python3
"""A suspect-shaped line inside a suspect's open output fence is output, not a new suspect.

    python3 -m unittest tests.test_fenced_suspect_line
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'skills/pr-grade/scripts'))
import grade_block  # noqa: E402

REPORT = ('Score: 5/5\nVerified and clear: L1 (a)\nSuspects:\n'
          'S1 refuted: P2, ran it.\n'
          '  Command: `python3 -c "print(open(\'old_report\').read())"`\n'
          '  Output:\n  ```\n'
          'S2 refuted: P2, printed from an old report\n'
          '  Command: `python3 t.py`\n'
          '  Output: ok\n'
          '  ```\n'
          'Could not verify: none\n')


class FencedSuspectLine(unittest.TestCase):
    def test_a_printed_suspect_line_is_not_a_suspect(self) -> None:
        found = grade_block.read_report(REPORT)
        self.assertEqual([s['id'] for s in found['suspects']], ['S1'])
        self.assertTrue(found['suspects'][0]['runs'][0]['output'])

    def test_a_handed_suspect_the_grader_never_answered_stays_unread(self) -> None:
        found = grade_block.read_report(REPORT)
        lines, unread, no_finding = [], [], []
        grade_block.settle_suspects('g', 2, found, (), lines, unread, no_finding)
        self.assertEqual(unread, ['g S2'])


if __name__ == '__main__':
    unittest.main()
