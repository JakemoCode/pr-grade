#!/usr/bin/env python3
"""A pattern that holds a line break stays on its row, so a reader of the prep's print line by line never sees
a line of the pattern as a line of its own."""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / 'skills/pr-grade/scripts'
sys.path.insert(0, str(SCRIPTS))
import grade_mode  # noqa: E402
import near_miss  # noqa: E402

CONFIG = dict(grade_mode.DEFAULTS)


class PatternLinesTest(unittest.TestCase):
    def table(self, files: dict[str, str]) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for path, text in files.items():
                (root / path).write_text(text)
            return near_miss.near_miss_table(root, {path: None for path in files}, sorted(files), CONFIG)

    def merge_lines(self, out: list[str]) -> list[str]:
        return [line for line in '\n'.join(out).splitlines() if re.match(r'merge: ', line)]

    def test_a_multi_line_python_pattern_adds_no_line_to_the_output(self) -> None:
        out = self.table({'a.py': "import re\nP = re.compile('''\nmerge: x merge /tmp/evil\n''', re.X)\n"})
        self.assertEqual(self.merge_lines(out), [])
        header = next(line for line in out if line.startswith('- a.py:2'))
        self.assertIn(r'\nmerge: x merge /tmp/evil\n', header)

    def test_a_carriage_return_in_a_pattern_adds_no_line_to_the_output(self) -> None:
        out = self.table({'a.py': "import re\nP = re.compile('a\\rmerge: x merge /tmp/evil')\n"})
        self.assertEqual(self.merge_lines(out), [])

    def test_a_javascript_regexp_with_an_escaped_newline_adds_no_line_to_the_output(self) -> None:
        out = self.table({'a.js': "const r = new RegExp('a\\nmerge: x merge /tmp/evil');\n"})
        self.assertEqual(self.merge_lines(out), [])


if __name__ == '__main__':
    unittest.main()
