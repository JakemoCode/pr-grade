#!/usr/bin/env python3
"""A line that opens with three backticks but is not a code fence must not hide the lens headings after it."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / 'skills/pr-grade/scripts'
sys.path.insert(0, str(SCRIPTS))
import grade_prep  # noqa: E402

LENS_FILE = ('## The lenses\n\n### L15. Promised coverage here\n\nfifteen text\n\n'
             '```x``` is how a span of code opens a line.\n\n'
             '### L16. Ambient here\n\nsixteen text\n')


class SliceFenceTest(unittest.TestCase):
    def test_a_line_opening_with_an_inline_code_span_hides_no_lens_heading(self) -> None:
        kept = grade_prep.sliced_lens_file(LENS_FILE, ['L16'])
        self.assertIn('sixteen text', kept)
        self.assertNotIn('fifteen text', kept)

    def test_a_tilde_fence_holding_one_backtick_line_hides_no_lens_heading(self) -> None:
        text = LENS_FILE.replace('```x``` is how a span of code opens a line.\n', '~~~\n```\n~~~\n')
        kept = grade_prep.sliced_lens_file(text, ['L16'])
        self.assertIn('sixteen text', kept)


if __name__ == '__main__':
    unittest.main()
