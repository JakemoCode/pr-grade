import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_suspects as ts


class IsRunGaps(unittest.TestCase):
    setUp = ts.MergeTest.setUp
    tearDown = ts.MergeTest.tearDown
    meta = ts.MergeTest.meta
    merged = ts.MergeTest.merged
    block = ts.MergeTest.block

    def refuted(self, command: str) -> str:
        return self.block(ts.report(f'S1 refuted: P2, ran it.\n  Command: {command}\n  Output: `1 passed`\n'))['Could not verify']

    def test_the_runs_and_reads_the_check_already_tells_apart_stay_apart(self) -> None:
        runs = ('.venv/bin/python -m pytest t.py -k name',
                'cd /tmp/x && .venv/bin/python scripts/run-logged.py -- .venv/bin/python -m unittest m',
                "FOO=1 python3 -c 'print(1)'", 'npx vitest run a.test.ts', '/work/copy/.venv/bin/python t.py',
                'node --test a.test.mjs', 'bash scripts/check.sh', "sh -c 'printf x | python3 parse.py'",
                'npm run test -- a', 'uv run pytest t.py')
        for command in runs:
            with self.subTest(run=command):
                self.assertEqual(self.refuted(f'`{command}`'), 'none')
        with self.subTest(run='a code span with words after it'):
            self.assertEqual(self.refuted('`.venv/bin/python -m unittest m` in the proof copy'), 'none')
        for command in ('grep -n guard src/a.py', 'git show HEAD:src/a.py | sed -n 1,20p', 'cat src/a.py'):
            with self.subTest(read=command):
                self.assertIn('refuted without a run', self.refuted(f'`{command}`'))

    def test_a_probe_or_a_read_that_starts_with_a_runner_is_not_a_run(self) -> None:
        for command in ('command -v pytest', 'command -v python3', 'python3 --version', 'node -v', 'npm ls foo',
                        'docker ps', "perl -ne 'print if /guard/' src/a.py", 'go read src/a.py'):
            with self.subTest(command=command):
                self.assertIn('refuted without a run', self.refuted(f'`{command}`'))

    def test_prose_that_opens_with_a_path_or_a_runner_word_is_not_a_run(self) -> None:
        for command in ('src/a.py:40 shows the guard', 'make sure no caller passes it', 'src/a.py shows the guard'):
            for written in (command, f'`{command}`'):
                with self.subTest(command=written):
                    self.assertIn('refuted without a run', self.refuted(written))

    def test_a_wrapper_option_that_takes_no_value_leaves_the_program_in_place(self) -> None:
        for command in ('sudo -E pytest t.py', 'sudo -n python3 t.py', 'sudo -u ci pytest t.py'):
            with self.subTest(command=command):
                self.assertEqual(self.refuted(f'`{command}`'), 'none')

    def test_a_shells_dash_c_after_its_script_is_the_scripts_argument(self) -> None:
        for command in ('bash scripts/t.sh -c config.yml', "bash -o pipefail -c 'pytest t.py'"):
            with self.subTest(command=command):
                self.assertEqual(self.refuted(f'`{command}`'), 'none')

    def test_a_run_behind_a_shell_keyword_or_by_an_absolute_script_path_is_a_run(self) -> None:
        for command in ('for f in a b; do pytest $f; done', 'if python3 t.py; then echo ok; fi',
                        '/work/copy/scripts/check.sh'):
            with self.subTest(command=command):
                self.assertEqual(self.refuted(f'`{command}`'), 'none')

    def test_a_run_whose_fenced_output_has_a_command_line_still_counts_as_run(self) -> None:
        # The python3 run printed a line that starts `Command:`; its output is in the fence below its own Output:.
        block = self.block(ts.report('S1 refuted: P2, a second writer.\n  Command: `python3 -c "import a; a.f()"`\n'
                                     '  Output:\n  ```\n  Command: a.f()\n  2\n  ```\n'))
        self.assertNotIn('refuted without a run', block['Could not verify'])


if __name__ == '__main__':
    unittest.main()
