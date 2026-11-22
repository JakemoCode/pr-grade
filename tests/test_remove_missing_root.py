#!/usr/bin/env python3
"""`proof_dir.py remove` counts a missing root as removed only when it is one make could have printed: a
`pr-grade-proof-` directory in the temp directory. A mistyped root refuses, as it did before 0.20.0 (issue #94).

    python3 -m unittest tests.test_remove_missing_root
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'skills/pr-grade/scripts/proof_dir.py'


class MissingRootTest(unittest.TestCase):
    def setUp(self) -> None:
        held = tempfile.TemporaryDirectory()
        self.addCleanup(held.cleanup)
        base = Path(held.name).resolve()
        self.temp, self.scratch = base / 'temp', base / 'scratch'
        self.temp.mkdir()
        self.scratch.mkdir()

    def remove(self, *roots: Path) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), 'remove', *map(str, roots)],
                              env={**os.environ, 'TMPDIR': str(self.temp)}, capture_output=True, text=True)

    def test_a_mistyped_root_refuses(self) -> None:
        run = self.remove(self.scratch / 'typo-root')
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertNotIn('already removed', run.stdout)
        self.assertIn('typo-root holds no .pr-grade-proof', run.stderr)

    def test_a_proof_root_name_outside_the_temp_directory_refuses(self) -> None:
        run = self.remove(self.scratch / 'pr-grade-proof-abc12345')
        self.assertEqual(run.returncode, 1, run.stdout)

    def test_another_name_in_the_temp_directory_refuses(self) -> None:
        run = self.remove(self.temp / 'pr-grade-scan-abc12345')
        self.assertEqual(run.returncode, 1, run.stdout)

    def test_a_truncated_proof_root_name_refuses(self) -> None:
        # mkdtemp's suffix is eight characters, so a name cut short is no root make printed.
        run = self.remove(self.temp / 'pr-grade-proof-abc')
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn('pr-grade-proof-abc holds no .pr-grade-proof', run.stderr)

    def test_a_proof_root_gone_from_the_temp_directory_was_removed_already(self) -> None:
        gone = self.temp / 'pr-grade-proof-abc12345'
        run = self.remove(gone)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout, f'already removed: {gone}\n')

    def test_a_root_gone_beside_a_live_root_on_the_line_was_removed_already_whatever_tmpdir_says(self) -> None:
        # The remove: line can run in a shell whose TMPDIR is not the prep's. The live root is listed first, as the
        # prep lists the graders' root before the scan's, so it is gone by the time the missing one is read.
        live, gone = self.scratch / 'pr-grade-proof-live1234', self.scratch / 'pr-grade-proof-gone1234'
        live.mkdir()
        (live / '.pr-grade-proof').write_text('repo\nsha\n')
        run = self.remove(live, gone)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout, f'removed: {live}\nalready removed: {gone}\n')

    def test_a_typo_beside_a_root_gone_still_refuses_the_typo_alone(self) -> None:
        gone = self.temp / 'pr-grade-proof-abc12345'
        run = self.remove(gone, self.scratch / 'typo-root')
        self.assertEqual(run.returncode, 1)
        self.assertIn(f'already removed: {gone}', run.stdout)
        self.assertIn('typo-root holds no .pr-grade-proof', run.stderr)


if __name__ == '__main__':
    unittest.main()
