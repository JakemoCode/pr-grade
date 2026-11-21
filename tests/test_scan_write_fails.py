#!/usr/bin/env python3
"""A `grade_mode.py --scan` that makes the scan's proof copy and then cannot write the scan's directory leaves
nothing in the temp directory: the prep never learns that root, so no remove: line could name it.

    python3 -m unittest discover -s tests -p test_scan_write_fails.py
"""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from test_first_read import Repo, grade_mode

WRITE_TEXT = Path.write_text


def failing_scan_json(self: Path, *args, **kwargs):
    if self.name == 'scan.json':
        raise OSError(28, 'No space left on device')
    return WRITE_TEXT(self, *args, **kwargs)


class ScanWriteFailsTest(Repo):
    def scan_failing(self, **keys) -> str:
        self.configure(**keys)
        printed = io.StringIO()
        with mock.patch.object(Path, 'write_text', failing_scan_json), mock.patch('tempfile.tempdir', str(self.temp)), \
                redirect_stdout(printed), self.assertRaises((SystemExit, OSError)) as stopped:
            grade_mode.scan(self.repo, 'main', 'main', grade_mode.load_config(self.repo), ())
        self.assertIn('No space left on device', str(getattr(stopped.exception, 'code', stopped.exception)))
        return printed.getvalue()

    def test_a_scan_json_write_that_fails_after_the_copy_leaves_no_root(self) -> None:
        out = self.scan_failing()
        self.assertEqual(list(self.temp.iterdir()), [])
        # Nothing was dispatched, so no scan waits on a copy that is gone.
        self.assertNotIn('Dispatch one pr-grade:scan agent', out)

    def test_without_a_copy_a_failed_write_leaves_no_root_either(self) -> None:
        self.scan_failing(scanProofCopy=False)
        self.assertEqual(list(self.temp.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
