#!/usr/bin/env python3
"""Proof: one command string with an unmatched quote drops every other process call from the list."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / 'skills/pr-grade/scripts'
sys.path.insert(0, str(SCRIPTS))
import ambient  # noqa: E402


class UnmatchedQuoteTest(unittest.TestCase):
    def test_a_command_string_with_an_apostrophe_does_not_hide_the_other_calls(self) -> None:
        text = ("import subprocess\n"
                "subprocess.run(['grep', 'a', 'f'])\n"
                "subprocess.run(\"echo don't\", shell=True)\n"
                "subprocess.run(['tar', 'x'])\n")
        try:
            calls = ambient.scan_file('a.py', text, None, ambient.registry({}))
        except ValueError as failed:
            self.fail(f'scan_file raised {failed!r}, so exit_codes lists none of the three calls')
        self.assertEqual(len(calls), 3)


if __name__ == '__main__':
    unittest.main()
