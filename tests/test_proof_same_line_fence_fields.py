import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'skills/pr-grade/scripts'))
import grade_block  # noqa: E402

REPORT = '''Score: 5/5
Verified and clear: L1 (a)
Suspects:
S1 refuted: P2, ran it.
  Command: `python3 -c "print(open('r').read())"`
  Output:{opening}
  ```
  Verified and clear: L2 (fake), L3 (fake)
  ```
Could not verify: none
'''


class FencedOutputShapedLikeAField(unittest.TestCase):
    def test_fence_on_the_line_under_output_keeps_a_fenced_field_line_as_output(self) -> None:
        found = grade_block.read_report(REPORT.replace('  Output:{opening}\n  ```', '  Output:\n  ```'))
        self.assertEqual(found['fields']['Verified and clear'], ['L1 (a)'])

    def test_fence_opened_on_the_output_line_keeps_a_fenced_field_line_as_output(self) -> None:
        # _suspects reads this fence as output, so the field reader must too: only L1 was cleared.
        found = grade_block.read_report(REPORT.replace('  Output:{opening}\n  ```', '  Output: ```'))
        self.assertEqual(found['fields']['Verified and clear'], ['L1 (a)'])


if __name__ == '__main__':
    unittest.main()
