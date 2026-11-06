#!/usr/bin/env python3
"""A prep interrupted during the env sweep must not leave its proof copies behind with no remove line."""
from __future__ import annotations

import glob
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

PREP = Path(__file__).resolve().parent.parent / 'skills/pr-grade/scripts/grade_prep.py'


class PrepInterruptTest(unittest.TestCase):
    def test_an_interrupted_sweep_leaves_no_proof_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, scratch = Path(tmp) / 'r', Path(tmp) / 'tmp'
            (repo / 'tests').mkdir(parents=True)
            (repo / '.claude').mkdir()
            scratch.mkdir()

            def git(*args: str) -> None:
                subprocess.run(['git', '-C', str(repo), '-c', 'user.email=a@b', '-c', 'user.name=a', *args],
                               check=True, capture_output=True)

            git('init', '-q', '-b', 'main')
            # Baseline and the first rows pass at once; the .DS_Store row sleeps, so the interrupt lands inside it.
            (repo / '.claude/pr-grade.json').write_text(
                '{"envSweep":{"command":["sh","-c","if [ -f tests/.DS_Store ]; then sleep 40; fi","sh","{tests}"],'
                '"timeout":60}}\n')
            (repo / 'app.py').write_text('x = 1\n')
            git('add', '-A')
            git('commit', '-qm', 'base')
            (repo / 'tests/test_a.py').write_text('def test_a(): pass\n')
            git('add', '-A')
            git('commit', '-qm', 'head')
            proc = subprocess.Popen([sys.executable, '-I', str(PREP), '--base', 'HEAD~1'], cwd=repo,
                                    env=dict(os.environ, TMPDIR=str(scratch)), stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True)
            try:
                for _ in range(120):
                    time.sleep(0.5)
                    if glob.glob(str(scratch / 'pr-grade-proof-*/env-sweep/repo/tests/.DS_Store')) or proc.poll() is not None:
                        break
                time.sleep(0.5)
                self.assertIsNone(proc.poll(), 'the prep ended before the sweep reached the .DS_Store row')
                proc.send_signal(signal.SIGINT)
            finally:
                proc.communicate(timeout=60)
            left = glob.glob(str(scratch / 'pr-grade-proof-*'))
            self.assertEqual(left, [], 'the interrupted prep left its proof root behind, with no remove: line printed')


if __name__ == '__main__':
    unittest.main()
