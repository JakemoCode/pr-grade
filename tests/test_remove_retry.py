#!/usr/bin/env python3
"""The prep's remove: line, run again after a kept proof test refused the graders' root, must succeed."""
from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

from test_first_read import Repo


class RemoveRetryTest(Repo):
    def test_the_remove_line_run_again_after_a_refusal_succeeds(self) -> None:
        out = self.scan()
        scan = Path(re.search(r'^--suspects (\S+)$', out, re.M).group(1))
        scan_root = Path(json.loads((scan / 'scan.json').read_text())['proof'])
        prep = self.ok('grade_prep.py', '--base', 'main', '--suspects', str(scan))
        remove = re.search(r'^remove: (.+)$', prep, re.M).group(1)
        grade_root = Path(re.search(r'^merge: .* merge (\S+)$', prep, re.M).group(1))
        kept = next(grade_root.glob('*/proofs')) / 'finding.test.py'
        kept.write_text('assert False\n')
        first = subprocess.run(remove, shell=True, cwd=self.repo, capture_output=True, text=True)
        self.assertNotEqual(first.returncode, 0)
        self.assertFalse(scan_root.exists())
        self.assertTrue(grade_root.exists())
        kept.unlink()  # the coordinator moved the test out
        second = subprocess.run(remove, shell=True, cwd=self.repo, capture_output=True, text=True)
        self.assertFalse(grade_root.exists())
        self.assertEqual(second.returncode, 0, second.stderr)


if __name__ == '__main__':
    unittest.main()
