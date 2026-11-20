from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'skills/pr-grade/scripts'))
import grade_block  # noqa: E402


class FieldLeadTest(unittest.TestCase):
    def test_a_finding_line_a_code_span_hides_the_field_of_is_not_read_as_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'grade.json').write_text(json.dumps(
                {'mode': 'subagent', 'head': 'b' * 40, 'lenses': ['L1'], 'groups': {'g': ['L1']}}))
            (root / 'g').mkdir()
            (root / 'g' / 'grade.md').write_text(
                'Score: 5/5\nBlocking: nothing\nP3 L1 src/a.py:3 - log reads oddly\n`x`Surface: src lib\n'
                'Verified and clear: L1\nCould not verify: none\nOutside my lenses: none\n')
            block = grade_block.parse(grade_block.merge(root))
        self.assertNotIn('Surface', block)


if __name__ == '__main__':
    unittest.main()
