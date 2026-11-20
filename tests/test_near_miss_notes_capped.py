#!/usr/bin/env python3
"""The `not scanned` notes have a cap, as the predicates, anchors and readers do."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / 'skills/pr-grade/scripts'
sys.path.insert(0, str(SCRIPTS))
import grade_mode  # noqa: E402
import near_miss  # noqa: E402

CONFIG = dict(grade_mode.DEFAULTS)


class NotesCappedTest(unittest.TestCase):
    def test_three_hundred_files_with_no_scanner_print_a_capped_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            files = [f'f{i:03}.go' for i in range(300)]
            for path in files:
                (root / path).write_text('package x\n')
            out = near_miss.near_miss_table(root, {path: None for path in files}, files, CONFIG)
        self.assertLess(len(out), 100)
        self.assertIn('- not scanned f000.go: no scanner reads this language', out)
        self.assertIn(f'- and {300 - near_miss.MAX_NOTES} more files not scanned, cut by the cap of '
                      f'{near_miss.MAX_NOTES}', out)


if __name__ == '__main__':
    unittest.main()
