#!/usr/bin/env python3
"""Proof: a test that deletes the stray file makes the sweep lose every row it already ran."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / 'skills/pr-grade/scripts'
sys.path.insert(0, str(SCRIPTS))
import ambient  # noqa: E402

WIPES = ('import os\nimport unittest\n\n\nclass Wipes(unittest.TestCase):\n    def test_wipe(self):\n'
         '        for n in os.listdir("tests"):\n            if n.startswith("."):\n'
         '                os.remove(os.path.join("tests", n))\n')


class StrayGoneTest(unittest.TestCase):
    def test_a_stray_the_test_removed_keeps_the_rows_already_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp)
            (copy / 'tests').mkdir()
            (copy / 'tests/test_wipe.py').write_text(WIPES)
            config = {'envSweep': {'command': [sys.executable, '-m', 'unittest', '{tests}'], 'timeout': 60},
                      'tests': ['test_*.py']}
            out = ambient.report(copy, {}, ['tests/test_wipe.py'], config, copy)
        text = '\n'.join(out)
        self.assertIn('baseline', text, 'the baseline row is gone: ' + text[-200:])
        self.assertIn('NUL byte', text, 'the NUL-byte row is gone')


if __name__ == '__main__':
    unittest.main()
