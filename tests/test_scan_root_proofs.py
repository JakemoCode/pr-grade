#!/usr/bin/env python3
"""The scan's proof root keeps no proofs: nothing in the grade flow moves a test out of it, so the prep's remove:
line drops it whatever the scan wrote under `scan/proofs/`, while a grader's kept test still refuses its own root.

    python3 -m unittest discover -s tests -p test_scan_root_proofs.py
"""
from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

from test_first_read import ROOT, Repo


class ScanRootProofsTest(Repo):
    def prep(self) -> tuple[Path, Path, str]:
        scan = Path(re.search(r'^--suspects (\S+)$', self.scan(), re.M).group(1))
        scan_root = Path(json.loads((scan / 'scan.json').read_text())['proof'])
        out = self.ok('grade_prep.py', '--base', 'main', '--suspects', str(scan))
        grade_root = Path(re.search(r'^merge: .* merge (\S+)$', out, re.M).group(1))
        return scan_root, grade_root, re.search(r'^remove: (.+)$', out, re.M).group(1)

    def run_line(self, remove: str) -> subprocess.CompletedProcess:
        return subprocess.run(remove, shell=True, cwd=self.repo, capture_output=True, text=True)

    def test_a_test_the_scan_wrote_under_its_proofs_does_not_stop_the_remove_line(self) -> None:
        scan_root, grade_root, remove = self.prep()
        (scan_root / 'scan/proofs/test_scan_wrote.py').write_text('assert False\n')
        run = self.run_line(remove)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse(scan_root.exists())
        self.assertFalse(grade_root.exists())

    def test_a_graders_kept_test_still_refuses_its_root(self) -> None:
        scan_root, grade_root, remove = self.prep()
        (scan_root / 'scan/proofs/test_scan_wrote.py').write_text('assert False\n')
        kept = next(grade_root.glob('*/proofs')) / 'test_kept.py'
        kept.write_text('assert False\n')
        run = self.run_line(remove)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn(str(kept), run.stderr)
        self.assertNotIn('test_scan_wrote.py', run.stderr)
        self.assertTrue(kept.is_file())
        self.assertFalse(scan_root.exists())


class AgentTest(unittest.TestCase):
    def test_the_scan_is_told_its_runs_are_scratch_and_its_report_survives(self) -> None:
        body = (ROOT / 'agents/scan.md').read_text().split('---\n', 2)[2]
        self.assertIn('Everything you write in your proof copy and scratch directory is scratch', body)
        self.assertIn('your report is all that survives', body)


if __name__ == '__main__':
    unittest.main()
