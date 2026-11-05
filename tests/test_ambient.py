#!/usr/bin/env python3
"""Unit tests for ambient.py: the exit-code list and the env sweep behind L10.

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / 'skills/pr-grade/scripts'
sys.path.insert(0, str(SCRIPTS))
import ambient  # noqa: E402
import grade_mode  # noqa: E402

CONFIG = dict(grade_mode.DEFAULTS)


class ExitCodeScanTest(unittest.TestCase):
    def scan(self, path: str, text: str, ranges=None, config: dict | None = None) -> list[dict]:
        return ambient.scan_file(path, text, ranges, ambient.registry(config or CONFIG))

    def test_a_python_call_names_its_command_its_handling_and_the_registry_note(self) -> None:
        text = ("import subprocess\n\ndef checks(pr):\n"
                "    run = subprocess.run(['gh', 'pr', 'checks', str(pr)], check=True, capture_output=True)\n"
                "    return run.stdout\n")
        [call] = self.scan('ci.py', text)
        self.assertEqual((call['file'], call['line'], call['command']), ('ci.py', 4, 'gh pr checks'))
        self.assertEqual(call['handles'], 'check=True')
        self.assertEqual(call['note'][0], 'gh pr checks')
        self.assertIn('no checks', call['note'][1])

    def test_a_call_on_an_unchanged_line_is_left_out(self) -> None:
        text = "import subprocess\nsubprocess.run(['grep', 'x', 'f'])\nsubprocess.run(['diff', 'a', 'b'])\n"
        calls = self.scan('a.py', text, ranges=[(3, 3)])
        self.assertEqual([c['command'] for c in calls], ['diff a b'])

    def test_a_command_built_at_runtime_is_listed_without_a_note(self) -> None:
        [call] = self.scan('a.py', 'import subprocess\ncmd = build()\nsubprocess.run(cmd)\n')
        self.assertEqual(call['command'], 'subprocess.run with a command built at runtime')
        self.assertIsNone(call['note'])

    def test_a_command_string_is_split_into_words(self) -> None:
        [call] = self.scan('a.py', 'import os\nos.system("tar -xzf out.tgz -C here")\n')
        self.assertEqual((call['command'], call['note'][0]), ('tar -xzf out.tgz -C', 'tar'))
        self.assertEqual(call['handles'], 'nothing within 6 lines examines the exit code')

    def test_a_returncode_read_below_the_call_counts_as_handling(self) -> None:
        text = ("import subprocess\nrun = subprocess.run(['grep', '-q', 'x', 'f'])\n"
                "if run.returncode == 1:\n    print('none')\n")
        [call] = self.scan('a.py', text)
        self.assertEqual(call['handles'], 'returncode')

    def test_a_javascript_call_is_scanned_with_its_own_handling_tokens(self) -> None:
        text = ("import { execSync, spawnSync } from 'node:child_process';\n"
                "const out = execSync('grep -rn TODO src');\n"
                "const run = spawnSync('tsc', ['--noEmit']);\nif (run.status !== 0) throw new Error('types');\n")
        calls = self.scan('build.ts', text)
        self.assertEqual([(c['command'], c['note'][0]) for c in calls], [('grep -rn TODO src', 'grep'), ('tsc --noEmit', 'tsc')])
        self.assertEqual(calls[0]['handles'], 'nothing within 6 lines examines the exit code')
        self.assertEqual(calls[1]['handles'], '.status')

    def test_a_module_qualified_javascript_call_is_scanned_and_a_regexp_exec_is_not(self) -> None:
        text = ("const cp = require('node:child_process');\n"
                "const m = /x(\\d+)/.exec(line);\nconst out = cp.execSync('diff a b');\n"
                "child_process.spawn('tar', ['-xzf', 'x.tgz']);\n")
        calls = self.scan('run.js', text)
        self.assertEqual([(c['line'], c['command']) for c in calls], [(3, 'diff a b'), (4, 'tar -xzf x.tgz')])

    def test_a_shell_script_lists_registry_commands_only_with_how_the_line_tests_them(self) -> None:
        text = ("#!/bin/bash\necho start\ntar -xzf pr-grade.tgz -C out\n"
                "grep -q secret report.txt || echo clean\nif diff a b; then echo same; fi\n")
        calls = self.scan('scan.sh', text)
        self.assertEqual([c['command'] for c in calls], ['tar -xzf pr-grade.tgz', 'grep -q secret', 'diff a b'])
        self.assertEqual(calls[0]['handles'], 'nothing on the line examines the exit code')
        self.assertEqual(calls[1]['handles'], "tested by the line's if, !, && or ||")
        self.assertEqual(calls[2]['handles'], "tested by the line's if, !, && or ||")

    def test_an_operator_tests_only_the_segment_before_it(self) -> None:
        [grep, diff] = self.scan('s.sh', '#!/bin/sh\ngrep x f; diff a b || exit 1\n')
        self.assertEqual((grep['command'], grep['handles']), ('grep x f', 'nothing on the line examines the exit code'))
        self.assertEqual((diff['command'], diff['handles']), ('diff a b', "tested by the line's if, !, && or ||"))
        [negated] = self.scan('s.sh', '#!/bin/sh\n! grep -q x f\n')
        self.assertEqual(negated['handles'], "tested by the line's if, !, && or ||")

    def test_two_calls_on_one_line_both_see_the_lines_after_them(self) -> None:
        text = "import subprocess\na = subprocess.run(['grep', 'x']); b = subprocess.run(['diff', 'a', 'b'])\n"\
               "if a.returncode == 1 and b.returncode == 1:\n    pass\n"
        calls = self.scan('a.py', text)
        self.assertEqual([c['handles'] for c in calls], ['returncode', 'returncode'])

    def test_set_e_is_named_as_what_handles_a_bare_shell_line(self) -> None:
        [call] = self.scan('scan.sh', '#!/bin/sh\nset -euo pipefail\ntar -xzf x.tgz\n')
        self.assertEqual(call['handles'], 'set -e stops the script on any nonzero')

    def test_a_shebang_makes_a_file_without_a_suffix_a_shell_script(self) -> None:
        self.assertTrue(ambient.is_shell('bin/scan', '#!/usr/bin/env bash\n'))
        self.assertFalse(ambient.is_shell('bin/scan', '#!/usr/bin/env python3\n'))

    def test_the_repository_extends_the_registry(self) -> None:
        config = {**CONFIG, 'exitCodes': {'mytool': 'exits 3 when idle'}}
        [call] = self.scan('a.py', "import subprocess\nsubprocess.run(['mytool', 'poll'])\n", config=config)
        self.assertEqual(call['note'], ('mytool', 'exits 3 when idle'))
        self.assertIn('grep', ambient.registry(config))

    def test_a_malformed_registry_extension_is_ignored(self) -> None:
        self.assertEqual(ambient.registry({**CONFIG, 'exitCodes': ['grep']}), ambient.REGISTRY)

    def test_the_longest_registry_key_wins(self) -> None:
        notes = ambient.registry(CONFIG)
        self.assertEqual(ambient.note_for(['gh', 'pr', 'checks', '12'], notes)[0], 'gh pr checks')
        self.assertEqual(ambient.note_for(['gh', 'pr', 'view'], notes)[0], 'gh')
        self.assertEqual(ambient.note_for(['/usr/bin/git', 'grep', 'x'], notes)[0], 'git grep')
        self.assertIsNone(ambient.note_for(['python3', '-m', 'x'], notes))


class ExitCodeListTest(unittest.TestCase):
    def test_the_list_names_noted_calls_first_then_plain_ones_then_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'a.py').write_text("import subprocess\nsubprocess.run(['ls'])\nsubprocess.run(['grep', 'x'], check=True)\n")
            (root / 'README.md').write_text('subprocess.run\n')
            out = ambient.exit_codes(root, {'a.py': None, 'README.md': None}, CONFIG)
            self.assertIn('- a.py:3 runs `grep x`; handles: check=True. Registry, `grep`: exits 1 on no match', out[1])
            self.assertEqual(out[2], '- a.py:2 runs `ls`; handles: nothing within 6 lines examines the exit code. No registry note.')
            self.assertEqual(ambient.exit_codes(root, {'README.md': None}, CONFIG)[1],
                             '- none: the change adds or modifies no process call in Python, JavaScript, TypeScript, or a shell script')


class SweepTest(unittest.TestCase):
    """A copy whose one test asserts the unperturbed world: no TZ, a C locale, a loose umask, one file in
    `data`, and LF line endings."""

    STRICT = '''import os, stat, unittest

class World(unittest.TestCase):
    def test_tz(self):
        self.assertIsNone(os.environ.get('TZ'))

    def test_locale(self):
        self.assertEqual(os.environ.get('LC_ALL'), 'C.UTF-8')

    def test_umask(self):
        path = 'data/made.tmp'
        open(path, 'w').close()
        try:
            self.assertTrue(os.stat(path).st_mode & stat.S_IRGRP)
        finally:
            os.unlink(path)

    def test_listing(self):
        self.assertEqual(sorted(os.listdir('data')), ['a.txt'])

    def test_line_endings(self):
        self.assertEqual(open('data/a.txt', 'rb').read(), b'x\\n')
'''
    LOOSE = 'import unittest\n\nclass Fine(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n'

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.copy = Path(self.tmp.name)
        (self.copy / 'tests').mkdir()
        (self.copy / 'data').mkdir()
        (self.copy / 'data/a.txt').write_text('x\n')
        self.config = {**CONFIG, 'envSweep': {'command': [sys.executable, '-m', 'unittest', '{tests}'], 'timeout': 60}}
        self.changed = ['tests/test_world.py', 'data/a.txt']

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def sweep(self, test_text: str) -> list[str]:
        (self.copy / 'tests/test_world.py').write_text(test_text)
        return ambient.env_sweep(self.copy, ['tests/test_world.py'], self.changed, self.config)

    def test_every_perturbation_fails_a_test_that_pins_the_authors_world(self) -> None:
        out = self.sweep(self.STRICT)
        self.assertIn('- baseline, no perturbation: pass', out[1])
        failed = [line for line in out[2:] if 'FAIL (exit 1)' in line]
        self.assertEqual(len(failed), 7, '\n'.join(out))
        self.assertTrue(any('Kiritimati' in line for line in failed))
        self.assertTrue(any('CRLF line endings in each data file the change touches (data/a.txt)' in line for line in failed))
        # The copy is left as it was found.
        self.assertEqual(sorted(p.name for p in (self.copy / 'data').iterdir()), ['a.txt'])
        self.assertEqual((self.copy / 'data/a.txt').read_bytes(), b'x\n')

    def test_a_test_that_assumes_nothing_passes_every_run(self) -> None:
        out = self.sweep(self.LOOSE)
        self.assertEqual(len([line for line in out if ': pass (' in line]), 8, '\n'.join(out))

    def test_a_failing_baseline_stops_the_sweep(self) -> None:
        out = self.sweep('import unittest\n\nclass Bad(unittest.TestCase):\n    def test_no(self):\n        self.fail("broken")\n')
        self.assertIn('FAIL (exit 1)', out[1])
        self.assertIn('broken', out[1])
        self.assertEqual(out[2], 'The baseline fails, so no perturbation was run: a failure under one would prove nothing.')
        self.assertEqual(len(out), 3)

    def test_a_change_with_no_data_file_says_the_crlf_run_was_skipped(self) -> None:
        self.changed = ['tests/test_world.py']
        out = self.sweep(self.LOOSE)
        self.assertIn('- CRLF line endings: not run, the change touches no data file', out[-1])

    def test_a_command_that_times_out_is_reported_as_such(self) -> None:
        self.config['envSweep'] = {'command': [sys.executable, '-c', 'import time; time.sleep(5)', '{tests}'], 'timeout': 0.5}
        out = self.sweep(self.LOOSE)
        self.assertIn('- baseline, no perturbation: timed out (after 0.5s)', out[1])

    def test_a_malformed_declaration_is_a_note(self) -> None:
        for sweep, word in (({'command': 'pytest {tests}'}, 'list of strings'), ({'command': ['pytest']}, '{tests}'),
                            ({'command': ['x', '{tests}'], 'timeout': 0}, 'positive'), ([], 'object')):
            with self.subTest(sweep=sweep):
                self.assertIn(word, ambient.malformed_sweep(sweep))
        self.assertIsNone(ambient.malformed_sweep({'command': ['x', '{tests}']}))


class ReportTest(unittest.TestCase):
    def test_without_a_declaration_or_a_changed_test_the_sweep_says_why_it_did_not_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'a.py').write_text('x = 1\n')
            out = ambient.report(root, {'a.py': None}, ['a.py'], CONFIG, None)
            self.assertEqual(out[0], '### Exit codes')
            self.assertIn('Not run: the repository declares no `envSweep`', out[-1])
            config = {**CONFIG, 'envSweep': {'command': ['x', '{tests}']}}
            self.assertIn('no test file', ambient.report(root, {'a.py': None}, ['a.py'], config, None)[-1])
            self.assertIn('no proof copy', ambient.report(root, {}, ['tests/test_a.py'], config, None)[-1])

    def test_a_config_without_tests_globs_uses_the_defaults_and_does_not_stop_the_report(self) -> None:
        config = {'envSweep': {'command': ['x', '{tests}']}}
        self.assertEqual(ambient.sweep_targets(['tests/test_a.py', 'a.py'], config), ['tests/test_a.py'])
        with tempfile.TemporaryDirectory() as tmp:
            out = ambient.report(Path(tmp), {}, ['tests/test_a.py'], config, None)
            self.assertIn('no proof copy', out[-1])

    def test_sweep_targets_follow_the_tests_globs(self) -> None:
        self.assertEqual(ambient.sweep_targets(['src/a.py', 'tests/test_a.py', 'b.spec.ts'], CONFIG),
                         ['tests/test_a.py', 'b.spec.ts'])


if __name__ == '__main__':
    unittest.main()
