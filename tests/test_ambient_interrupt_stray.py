#!/usr/bin/env python3
"""A sweep interrupted while a stray row runs must still remove the stray it wrote."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / 'skills/pr-grade/scripts'
sys.path.insert(0, str(SCRIPTS))
import ambient  # noqa: E402


class InterruptStrayTest(unittest.TestCase):
    def test_an_interrupted_stray_row_leaves_no_stray_in_the_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp)
            (copy / 'tests').mkdir()
            (copy / 'tests/test_a.py').write_text('')
            calls = []

            def interrupted_on_the_ds_store_row(*_args, **_kwargs):
                calls.append(1)
                if len(calls) == 6:  # baseline, two zones, locale, umask, then the .DS_Store row
                    raise KeyboardInterrupt
                return 'pass', '0s'

            real, ambient._run = ambient._run, interrupted_on_the_ds_store_row
            try:
                with self.assertRaises(KeyboardInterrupt):
                    ambient.env_sweep(copy, ['tests/test_a.py'], ['tests/test_a.py'],
                                      {'envSweep': {'command': ['x', '{tests}']}, 'tests': ['tests/test_*.py']})
            finally:
                ambient._run = real
            self.assertEqual(sorted(p.name for p in (copy / 'tests').iterdir()), ['test_a.py'],
                             'the .DS_Store the row wrote was left behind by the interrupt')


if __name__ == '__main__':
    unittest.main()
