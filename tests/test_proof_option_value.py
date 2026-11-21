import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_suspects as ts


class ReadsWithABooleanOptionBeforeTheSubcommand(unittest.TestCase):
    setUp = ts.MergeTest.setUp
    tearDown = ts.MergeTest.tearDown
    meta = ts.MergeTest.meta
    merged = ts.MergeTest.merged
    block = ts.MergeTest.block

    def refuted(self, command: str) -> str:
        return self.block(ts.report(f'S1 refuted: P2, ran it.\n  Command: {command}\n  Output: `ok`\n'))['Could not verify']

    def test_a_package_read_naming_a_subcommand_word_is_not_a_run(self) -> None:
        # `-r`, `--silent` and `-g` take no value, so `ls`, `why` and `view` are the subcommand, and `run`, `test`
        # are the package they look up.
        for command in ('pnpm -r why test', 'npm --silent ls test', 'npm -g ls run', 'npm --no-audit view test'):
            with self.subTest(command=command):
                self.assertIn('refuted without a run', self.refuted(f'`{command}`'))

    def test_a_perl_filter_after_an_include_path_is_not_a_run(self) -> None:
        # `-I lib` takes its value as the next word; the filter flag `-n` after it is still perl's own switch.
        self.assertIn('refuted without a run', self.refuted('`perl -I lib -ne print data.txt`'))


if __name__ == '__main__':
    unittest.main()
