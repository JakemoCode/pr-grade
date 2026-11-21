#!/usr/bin/env python3
"""scanAbove counts the additions plus deletions in the diff the graders read; a pure rename is none there."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'skills/pr-grade/scripts'))
import grade_mode  # noqa: E402


def git(root: Path, *args: str) -> str:
    run = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    return run.stdout.strip()


class RenameTest(unittest.TestCase):
    def test_a_pure_rename_of_a_120_line_file_is_not_over_the_scan_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            git(root, 'init', '-q', '-b', 'main')
            git(root, 'config', 'user.email', 'a@b')
            git(root, 'config', 'user.name', 'x')
            (root / '.claude').mkdir()
            (root / '.claude/pr-grade.json').write_text(json.dumps({'silent': ['store.py', 'lib/store.py']}))
            (root / 'store.py').write_text(''.join(f'X{i} = {i}\n' for i in range(120)))
            git(root, 'add', '.')
            git(root, 'commit', '-qm', 'base')
            git(root, 'checkout', '-qb', 'topic')
            (root / 'lib').mkdir()
            git(root, 'mv', 'store.py', 'lib/store.py')
            git(root, 'commit', '-qm', 'move')
            yes, why, fork = grade_mode.scan_decision(root, 'main', 'main', grade_mode.load_config(root))
            self.assertEqual(git(root, 'diff', '--numstat', fork, 'HEAD'), '0\t0\tstore.py => lib/store.py')
            self.assertFalse(yes, f'the graders read 0 changed lines, and the scan counted: {why}')


if __name__ == '__main__':
    unittest.main()
