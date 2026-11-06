#!/usr/bin/env python3
"""An env-sweep run interrupted in the parent must not leave the test command running."""
from __future__ import annotations

import os
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / 'skills/pr-grade/scripts'
sys.path.insert(0, str(SCRIPTS))
import ambient  # noqa: E402


class InterruptOrphanTest(unittest.TestCase):
    def test_an_interrupted_run_kills_the_test_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp)
            argv = [sys.executable, '-c',
                    'import os, time, pathlib; pathlib.Path("pid").write_text(str(os.getpid())); time.sleep(30)']

            def interrupt(*_args) -> None:
                raise KeyboardInterrupt

            old = signal.signal(signal.SIGALRM, interrupt)
            signal.alarm(2)
            try:
                ambient._run(copy, argv, dict(os.environ), 60)
            except KeyboardInterrupt:
                pass
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old)
            pid = int((copy / 'pid').read_text())
            time.sleep(0.3)
            try:
                os.kill(pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            if alive:
                os.kill(pid, signal.SIGKILL)
            self.assertFalse(alive, 'the test command kept running after the sweep was interrupted')


if __name__ == '__main__':
    unittest.main()
