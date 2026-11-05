#!/usr/bin/env python3
"""An interrupted sweep row must still remove the strays it wrote."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / 'skills/pr-grade/scripts'
sys.path.insert(0, str(SCRIPTS))
import ambient  # noqa: E402


class InterruptStrayTest(unittest.TestCase):
    def test_an_interrupted_row_removes_its_strays(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp)
            (copy / 'pkg').mkdir()
            (copy / 'pkg' / 'test_x.py').write_text('')
            calls = []

            def fake_run(copy_, argv, env, timeout, umask=None):
                calls.append(1)
                if len(calls) >= 6:  # the .DS_Store row
                    raise KeyboardInterrupt
                return 'pass', '0.1s'

            config = {'envSweep': {'command': ['true', '{tests}']}, 'tests': ['*test_*.py']}
            with mock.patch.object(ambient, '_run', fake_run), mock.patch.object(ambient, '_locale_installed', lambda n: True):
                with self.assertRaises(KeyboardInterrupt):
                    ambient.env_sweep(copy, ['pkg/test_x.py'], ['pkg/test_x.py'], config)
            left = sorted(p.name for p in (copy / 'pkg').iterdir())
            self.assertEqual(left, ['test_x.py'], f'strays left behind after the interrupt: {left}')


if __name__ == '__main__':
    unittest.main()
