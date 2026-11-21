import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_suspects as ts


class IsRunSecondSpan(unittest.TestCase):
    setUp = ts.MergeTest.setUp
    tearDown = ts.MergeTest.tearDown
    meta = ts.MergeTest.meta
    merged = ts.MergeTest.merged
    block = ts.MergeTest.block

    def refuted(self, command: str) -> str:
        return self.block(ts.report(f'S1 refuted: P2, ran it.\n  Command: {command}\n  Output: `1 passed`\n'))['Could not verify']

    def test_a_read_with_a_second_code_span_of_prose_is_not_a_run(self) -> None:
        for command in ('`grep -n guard src/a.py`, the guard in `src/a.py`', '`cat src/a.py` (no `pytest` on PATH)'):
            with self.subTest(command=command):
                self.assertIn('refuted without a run', self.refuted(command))

    def test_a_fenced_command_and_output_pair_is_a_run(self) -> None:
        block = self.block(ts.report('S1 refuted: P2, ran it.\n  ```\n  Command: pytest t.py\n  Output: 1 passed\n  ```\n'))
        self.assertNotIn('refuted without a run', block['Could not verify'])

    def test_option_values_before_a_subcommand_still_run(self) -> None:
        for command in ('pnpm --filter api test', 'npm --prefix web test', 'docker compose run app pytest',
                        'yarn --cwd web test', 'pnpm -C web test', 'cargo nextest run', 'yarn test:unit'):
            with self.subTest(command=command):
                self.assertEqual(self.refuted(f'`{command}`'), 'none')

    def test_a_version_probe_with_a_redirect_or_through_a_runner_is_not_a_run(self) -> None:
        for command in ('node -v 2>&1', 'python3 --version 2>/dev/null', 'npx vitest --version', 'python3 -m pytest --version',
                        'uv run pytest --version', 'python3 -m pytest -v --version'):
            with self.subTest(command=command):
                self.assertIn('refuted without a run', self.refuted(f'`{command}`'))

    def test_a_subcommand_named_for_a_runner_reads_unless_a_package_manager_runs_its_bin(self) -> None:
        for command in ('uv python list', 'docker node ls'):
            with self.subTest(read=command):
                self.assertIn('refuted without a run', self.refuted(f'`{command}`'))
        for command in ('pnpm vitest run', 'yarn jest'):
            with self.subTest(run=command):
                self.assertEqual(self.refuted(f'`{command}`'), 'none')

    def test_a_script_file_of_each_kind_and_a_perl_script_with_its_own_options_run(self) -> None:
        for command in ('swift t.swift', 'bun t.tsx', 'bun t.jsx', 'node t.cts', 'perl t.pl -n 5', 'perl t.pl -input x'):
            with self.subTest(command=command):
                self.assertEqual(self.refuted(f'`{command}`'), 'none')

    def test_a_fence_opened_by_one_line_does_not_swallow_the_commands_below_it(self) -> None:
        block = self.block(ts.report('S1 refuted: P2, ran it.\n  ```x```\n  Command: `pytest t.py`\n  Output:\n  ```\n  1 passed\n  ```\n'))
        self.assertEqual(block['Could not verify'], 'none')


if __name__ == '__main__':
    unittest.main()
