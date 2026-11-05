#!/usr/bin/env python3
"""A timed-out env-sweep run must not leave its child processes running in the copy."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / 'skills/pr-grade/scripts'
sys.path.insert(0, str(SCRIPTS))
import ambient  # noqa: E402


class TimeoutOrphanTest(unittest.TestCase):
    def test_a_timed_out_run_leaves_no_child_writing_into_the_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp)
            # The test command starts a child of its own (a subprocess a test spawned) and hangs.
            argv = [sys.executable, '-c',
                    "import subprocess, sys, time\n"
                    "subprocess.Popen([sys.executable, '-c', "
                    "\"import time, pathlib; time.sleep(2); pathlib.Path('late-write.txt').write_text('x')\"])\n"
                    "time.sleep(60)\n"]
            verdict, _ = ambient._run(copy, argv, dict(os.environ), 0.5)
            self.assertEqual(verdict, 'timed out')
            time.sleep(3)
            self.assertFalse((copy / 'late-write.txt').exists(),
                             'the abandoned run kept running and wrote into the copy after it was reported timed out')


if __name__ == '__main__':
    unittest.main()
