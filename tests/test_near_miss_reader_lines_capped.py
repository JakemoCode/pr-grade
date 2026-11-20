#!/usr/bin/env python3
"""A second reader's hit lines are bounded, as its file count is."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / 'skills/pr-grade/scripts'
sys.path.insert(0, str(SCRIPTS))
import grade_mode  # noqa: E402
import near_miss  # noqa: E402

CONFIG = dict(grade_mode.DEFAULTS)


def git(root: Path, *args: str) -> str:
    return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


class ReaderLinesTest(unittest.TestCase):
    def test_a_common_anchor_does_not_list_every_hit_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            git(root, 'init', '-q', '-b', 'main')
            (root / 'a.py').write_text("def f(line):\n    return line.startswith('import ')\n")
            for i in range(12):
                (root / f'm{i}.py').write_text('import os\n' * 400)
            git(root, 'add', '-A')
            git(root, 'commit', '-q', '-m', 'x')
            head = git(root, 'rev-parse', 'HEAD').strip()
            out = near_miss.second_readers(root, head, {'a.py': None}, CONFIG)
        self.assertLess(max(map(len, out)), 2000)
        row = next(line for line in out if line.startswith("- 'import', "))
        self.assertIn('m0.py:1, 2, 3', row)
        self.assertIn('and 390 more lines', row)


if __name__ == '__main__':
    unittest.main()
