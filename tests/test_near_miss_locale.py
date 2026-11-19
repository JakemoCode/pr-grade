#!/usr/bin/env python3
"""Proof: a node error message with a non-ASCII character stops the whole near-miss table under a
non-UTF-8 locale, because _evaluate decodes the child's output with the locale's encoding."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / 'skills/pr-grade/scripts'

CHILD = r'''
import sys
sys.path.insert(0, sys.argv[1])
import near_miss
predicate = {'lang': 'js', 'pattern': '(\U0001F600+', 'flags': ''}
print(ascii(near_miss._evaluate(predicate, ['x'])))
'''


@unittest.skipUnless(shutil.which('node'), 'needs node')
class LocaleTest(unittest.TestCase):
    def test_an_invalid_regex_with_an_emoji_is_reported_not_raised_under_a_legacy_locale(self) -> None:
        for locale in ('ja_JP.SJIS', 'en_US.US-ASCII', 'ja_JP.eucJP'):
            env = {**os.environ, 'LC_ALL': locale}
            env.pop('PYTHONUTF8', None)
            run = subprocess.run([sys.executable, '-c', CHILD, str(SCRIPTS)], env=env, capture_output=True)
            with self.subTest(locale=locale):
                self.assertEqual(run.returncode, 0, run.stderr.decode('utf-8', 'replace')[-300:])


READERS = r'''
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import grade_mode, near_miss
print(ascii(near_miss.second_readers(Path(sys.argv[2]), sys.argv[3], {'a.py': None}, dict(grade_mode.DEFAULTS))))
'''


class GrepLocaleTest(unittest.TestCase):
    def test_a_reader_with_a_non_ascii_path_keeps_its_name_under_a_legacy_locale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()

            def git(*args: str) -> str:
                return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args], cwd=root, check=True,
                                      capture_output=True, text=True).stdout

            git('init', '-q', '-b', 'main')
            (root / 'a.py').write_text("def f(s):\n    return s.startswith('Unique-marker:')\n")
            (root / 'café.py').write_text("M = 'Unique-marker:'\n", encoding='utf-8')
            git('add', '-A')
            git('commit', '-q', '-m', 'x')
            head = git('rev-parse', 'HEAD').strip()
            env = {**os.environ, 'LC_ALL': 'ja_JP.SJIS'}
            env.pop('PYTHONUTF8', None)
            run = subprocess.run([sys.executable, '-c', READERS, str(SCRIPTS), str(root), head], env=env,
                                 capture_output=True, encoding='utf-8', errors='replace')
        self.assertEqual(run.returncode, 0, run.stderr[-300:])
        self.assertIn('caf\\xe9.py:1', run.stdout)


if __name__ == '__main__':
    unittest.main()
