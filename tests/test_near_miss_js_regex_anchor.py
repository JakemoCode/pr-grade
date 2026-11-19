#!/usr/bin/env python3
"""A JavaScript regex that Python's re parser refuses still has an anchor the second-reader list must search."""
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


class JsRegexAnchorTest(unittest.TestCase):
    def readers(self, js: str) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            git(root, 'init', '-q', '-b', 'main')
            (root / 'reader.py').write_text("def r(line):\n    return line == '## Loose ends'\n")
            (root / 'a.js').write_text(js)
            git(root, 'add', '-A')
            git(root, 'commit', '-q', '-m', 'x')
            head = git(root, 'rev-parse', 'HEAD').strip()
            return near_miss.second_readers(root, head, {'a.js': None}, CONFIG)

    def test_a_plain_js_regex_finds_the_python_reader(self) -> None:
        self.assertTrue(any('reader.py:2' in line for line in self.readers('const r = /^## Loose ends$/m;\n')))

    def test_a_js_regex_with_a_named_group_finds_the_python_reader(self) -> None:
        out = self.readers('const r = /^(?<h>## Loose ends)$/m;\n')
        self.assertTrue(any('reader.py:2' in line for line in out), out)

    def test_a_js_regex_with_a_unicode_property_finds_the_python_reader(self) -> None:
        out = self.readers('const r = /^\\p{L}+ Loose ends$/u;\n')
        self.assertTrue(any('reader.py:2' in line for line in out), out)


if __name__ == '__main__':
    unittest.main()
