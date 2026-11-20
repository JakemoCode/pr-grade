#!/usr/bin/env python3
"""A JavaScript file's string literals seed its regexes; a comment or a regex literal must not be read as one."""
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


class JsLiteralSeedTest(unittest.TestCase):
    def table(self, js: str) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / 'a.js').write_text(js)
            return near_miss.near_miss_table(root, {'a.js': None}, ['a.js'], CONFIG)

    def header(self, js: str) -> str:
        return next(line for line in self.table(js)
                    if line.startswith('- a.js:') and 'regex' in line and 'Loose ends$' in line)

    def test_an_apostrophe_in_a_line_comment_leaves_the_seed_alone(self) -> None:
        header = self.header("// don't touch this\nconst r = /^## Loose ends$/m;\nconst h = '## Loose ends';\n")
        self.assertIn("seed '## Loose ends'", header)

    def test_an_apostrophe_in_a_block_comment_leaves_the_seed_alone(self) -> None:
        header = self.header("/* it's a heading */\nconst r = /^## Loose ends$/m;\nconst h = '## Loose ends';\n")
        self.assertIn("seed '## Loose ends'", header)

    def test_a_quote_inside_a_regex_literal_is_no_seed(self) -> None:
        header = self.header("const q = /['\"]/;\nconst r = /^## Loose ends$/m;\nconst h = '## Loose ends';\n")
        self.assertIn("seed '## Loose ends'", header)

    def test_a_quoted_string_in_a_comment_is_no_seed(self) -> None:
        header = self.header("// const h = '## Loose ends';\nconst r = /^## Loose ends$/m;\n")
        self.assertIn('no seed found', header)

    def test_a_comparison_spelled_inside_a_regex_literal_is_no_predicate(self) -> None:
        out = self.table("const r = /x === '## Loose ends'/;\n")
        self.assertFalse(any(line.startswith('- a.js:1 equals') for line in out), out)


if __name__ == '__main__':
    unittest.main()
