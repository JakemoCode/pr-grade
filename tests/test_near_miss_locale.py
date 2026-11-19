#!/usr/bin/env python3
"""Proof: a node error message with a non-ASCII character stops the whole near-miss table under a
non-UTF-8 locale, because _evaluate decodes the child's output with the locale's encoding."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / 'skills/pr-grade/scripts'

CHILD = r'''
import sys
sys.path.insert(0, sys.argv[1])
import near_miss
predicate = {'lang': 'js', 'pattern': '(\U0001F600+', 'flags': ''}
print(near_miss._evaluate(predicate, ['x']))
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


if __name__ == '__main__':
    unittest.main()
