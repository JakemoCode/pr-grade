#!/usr/bin/env python3
"""Proof: ambient.scan_file hangs on a command string longer than its 600-character window."""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / 'skills/pr-grade/scripts'


class ScanHangTest(unittest.TestCase):
    def test_a_long_command_string_with_many_escapes_returns_promptly(self) -> None:
        # A 30-line shell script in one string literal: 30 `\n` escapes, 640 characters, so the
        # 600-character window cuts the literal before its closing quote.
        body = 'echo hi\\n' * 30 + 'x' * 400
        source = 'import subprocess\nsubprocess.run("' + body + '", shell=True)\n'
        code = (f'import sys; sys.path.insert(0, {str(SCRIPTS)!r}); import ambient; '
                f'ambient.scan_file("a.py", {source!r}, None, ambient.registry({{}}))')
        try:
            subprocess.run([sys.executable, '-c', code], timeout=10, check=True)
        except subprocess.TimeoutExpired:
            self.fail('scan_file did not return in 10s for one call with a 640-character command string')


if __name__ == '__main__':
    unittest.main()
