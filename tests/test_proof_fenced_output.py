import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_suspects as ts


class FencedOutputPrintsACommand(unittest.TestCase):
    setUp = ts.MergeTest.setUp
    tearDown = ts.MergeTest.tearDown
    meta = ts.MergeTest.meta
    merged = ts.MergeTest.merged
    block = ts.MergeTest.block

    GREP_OUTPUT = ('  tests/test_x.py:12:  Command: `pytest t.py`\n'
                   '  tests/test_x.py:13:  Output: `1 passed`\n')

    def refuted(self, opening: str) -> str:
        # a grep, a read, whose fenced output holds the lines it matched
        text = ('S1 refuted: P2, grep shows the guard.\n  Command: `grep -nE "Command:|Output:" tests/test_x.py`\n'
                f'  Output:{opening}\n  ```\n{self.GREP_OUTPUT}  ```\n')
        return self.block(ts.report(text))['Could not verify']

    def test_output_fence_directly_under_output_is_blanked(self) -> None:
        self.assertIn('refuted without a run', self.refuted(''))

    def test_output_fence_after_a_word_on_the_output_line_is_not_a_run(self) -> None:
        self.assertIn('refuted without a run', self.refuted(' matched:'))

    def test_output_fence_opened_on_the_output_line_is_not_a_run(self) -> None:
        text = ('S1 refuted: P2, grep shows the guard.\n  Command: `grep -nE "Command:|Output:" tests/test_x.py`\n'
                f'  Output: ```\n{self.GREP_OUTPUT}  ```\n')
        self.assertIn('refuted without a run', self.block(ts.report(text))['Could not verify'])


if __name__ == '__main__':
    unittest.main()
