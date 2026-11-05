#!/usr/bin/env python3
"""A prep stopped by SIGTERM (a tool timeout, a kill) must not leave the sweep's test command running."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / 'skills/pr-grade/scripts'

PARENT = '''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import ambient
copy = Path(sys.argv[2])
argv = [sys.executable, '-c',
        'import os, time, pathlib; pathlib.Path("pid").write_text(str(os.getpid())); time.sleep(30)']
ambient._run(copy, argv, dict(os.environ), 60)
'''


class SigtermOrphanTest(unittest.TestCase):
    def test_a_terminated_prep_kills_the_test_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp)
            parent = subprocess.Popen([sys.executable, '-c', PARENT, str(SCRIPTS), str(copy)])
            deadline = time.monotonic() + 10
            while not (copy / 'pid').is_file() and time.monotonic() < deadline:
                time.sleep(0.05)
            time.sleep(0.2)
            pid = int((copy / 'pid').read_text())
            parent.send_signal(signal.SIGTERM)
            parent.wait(timeout=10)
            time.sleep(0.3)
            try:
                os.kill(pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            if alive:
                os.kill(pid, signal.SIGKILL)
            self.assertFalse(alive, 'the test command kept running after the prep was terminated')


if __name__ == '__main__':
    unittest.main()
