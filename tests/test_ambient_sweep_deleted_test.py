#!/usr/bin/env python3
"""Proof: a test file the change deletes or renames away is handed to the sweep command, whose baseline then fails."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / 'skills/pr-grade/scripts'
sys.path.insert(0, str(SCRIPTS))
import ambient  # noqa: E402

FINE = 'import unittest\n\n\nclass Fine(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n'


class DeletedTestTest(unittest.TestCase):
    def test_a_deleted_test_beside_a_changed_one_does_not_stop_the_sweep(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp)
            (copy / 'tests').mkdir()
            (copy / 'tests/test_kept.py').write_text(FINE)
            # grade_prep passes grade_mode.changed_files(), which lists tests/test_gone.py: `git diff --name-only`
            # names a deleted path, and a rename lists its old side too. The head copy has no such file.
            changed = ['tests/test_gone.py', 'tests/test_kept.py']
            config = {'envSweep': {'command': [sys.executable, '-m', 'unittest', '{tests}'], 'timeout': 60},
                      'tests': ['test_*.py']}
            out = ambient.report(copy, {}, changed, config, copy)
        text = '\n'.join(out)
        self.assertNotIn('The baseline fails', text, text)


if __name__ == '__main__':
    unittest.main()
