import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_suspects as ts


class ReadsCountedAsRuns(unittest.TestCase):
    setUp = ts.MergeTest.setUp
    tearDown = ts.MergeTest.tearDown
    meta = ts.MergeTest.meta
    merged = ts.MergeTest.merged
    block = ts.MergeTest.block

    def test_a_read_through_a_wrapper_with_an_option_value_or_an_unlisted_reader_is_not_a_run(self) -> None:
        for command in ('env -u HOME grep -n guard src/a.py', "bash -lc 'grep -n guard src/a.py'",
                        'timeout -s KILL 5 cat src/a.py', 'grep -n guard src/a.py | tr a-z A-Z',
                        'test -f src/a.py && grep -n guard src/a.py', 'git for-each-ref refs/heads',
                        'read src/a.py lines 30-50', 'none - read it'):
            with self.subTest(command=command):
                block = self.block(ts.report(f'S1 refuted: P2, the guard exists.\n  Command: `{command}`\n'
                                             '  Output: `40: if guard:`\n'))
                self.assertIn('refuted without a run', block['Could not verify'])

    def test_a_run_behind_a_wrapper_option_or_a_path_is_a_run(self) -> None:
        for command in ('env -u HOME pytest t.py', 'timeout -s KILL 5 node t.js', "bash -lc 'pytest t.py'",
                        '.venv/bin/python -m pytest t.py', './scripts/check.sh', 'cd /work/copy && npx vitest run t.test.ts'):
            with self.subTest(command=command):
                block = self.block(ts.report(f'S1 refuted: P2, ran it.\n  Command: `{command}`\n  Output: `1 passed`\n'))
                self.assertEqual(block['Could not verify'], 'none')

    def test_a_refutation_with_a_read_and_then_a_run_is_a_run(self) -> None:
        block = self.block(ts.report(
            'S1 refuted: P2, a second writer.\n  Command: `grep -n guard src/a.py`\n  Output: `40: if guard`\n'
            '  Command: `python3 -c "import a; a.f()"`\n  Output: `ok`\n'))
        self.assertEqual(block['Could not verify'], 'none')


if __name__ == '__main__':
    unittest.main()
