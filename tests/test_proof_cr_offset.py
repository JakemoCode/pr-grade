from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_first_read import Repo  # noqa: E402


class CrOffsetTest(Repo):
    def test_suspects_land_before_the_lens_text_when_the_prompt_holds_a_cr(self) -> None:
        scan = Path(re.search(r'^--suspects (\S+)$', self.scan(), re.M).group(1))
        out = self.ok('grade_prep.py', '--base', 'main', '--suspects', str(scan), '--settled', 'ok\r\n' * 300)
        root = Path(re.search(r'^merge: .* merge (\S+)$', out, re.M).group(1))
        (scan / 'first-read.md').write_text('1. one\n2. two\n')
        self.ok('grade_prep.py', '--attach', str(root))
        prompt = (root / 'grade/prompt.md').read_text()
        self.assertLess(prompt.index('\nSuspects from a first read:\n'), prompt.index('===== lens text ====='))
        self.assertLess(prompt.index('- Write your report to:'), prompt.index('\nSuspects from a first read:\n'))
        # and it must not cut a line in half
        before = prompt[:prompt.index('\nSuspects from a first read:\n')]
        self.assertTrue(before.endswith('\n') or before.endswith('grade.md'), before[-60:])


if __name__ == '__main__':
    unittest.main()
