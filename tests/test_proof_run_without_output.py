import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_suspects as ts


class RunWithNoOutput(unittest.TestCase):
    setUp = ts.MergeTest.setUp
    tearDown = ts.MergeTest.tearDown
    meta = ts.MergeTest.meta
    merged = ts.MergeTest.merged
    block = ts.MergeTest.block

    def test_a_run_command_with_no_output_of_its_own_is_not_a_run(self) -> None:
        # The read has an Output; the python3 command that follows it has none, so nothing shows it ran.
        block = self.block(ts.report('S1 refuted: P2, a second writer.\n  Command: `grep -n guard src/a.py`\n'
                                     '  Output: `40: if guard`\n  Command: `python3 -c "import a; a.f()"`\n'))
        self.assertIn('refuted without a run', block['Could not verify'])


if __name__ == '__main__':
    unittest.main()
