#!/usr/bin/env python3
"""The first read's proof copy: grade_mode.py --scan makes one for the scan before it is dispatched, the dispatch line
names it, the prep's remove: line removes it with the graders' copies, and `scanProofCopy: false` turns it off.

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from test_first_read import ROOT, Repo, git, grade_mode

MARKER = '.pr-grade-proof'


class ScanCopyTest(Repo):
    def dispatch(self, out: str) -> str:
        return re.search(r'^Review the change between .*$', out, re.M).group(0)

    def scan_dir(self, out: str) -> Path:
        return Path(re.search(r'^--suspects (\S+)$', out, re.M).group(1))

    def test_a_yes_makes_a_proof_copy_of_the_head_and_the_dispatch_names_it(self) -> None:
        out = self.scan()
        head = git(self.repo, 'rev-parse', 'HEAD').strip()
        record = json.loads((self.scan_dir(out) / 'scan.json').read_text())
        root = Path(record['proof'])
        self.assertTrue((root / MARKER).is_file())
        copy, scratch = root / 'scan/repo', root / 'scan/scratch'
        self.assertEqual(git(copy, 'rev-parse', 'HEAD').strip(), head)
        self.assertTrue(scratch.is_dir())
        line = self.dispatch(out)
        self.assertIn(f'Your proof copy: {copy}', line)
        self.assertIn(f'Your scratch directory: {scratch}', line)
        # The bare question comes first, unchanged.
        self.assertTrue(line.startswith(grade_mode.SCAN_PROMPT.split('{base}')[0]))

    def test_the_copy_gets_the_dependency_links_a_graders_copy_gets(self) -> None:
        (self.repo / '.venv').mkdir()
        (self.repo / '.git/info/exclude').write_text('.claude/\n.venv/\n')
        record = json.loads((self.scan_dir(self.scan()) / 'scan.json').read_text())
        link = Path(record['proof']) / 'scan/repo/.venv'
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), (self.repo / '.venv').resolve())

    def test_scan_proof_copy_false_makes_no_copy_and_names_none(self) -> None:
        self.configure(scanProofCopy=False)
        out = self.scan()
        self.assertNotIn('proof copy', self.dispatch(out))
        self.assertNotIn('proof', json.loads((self.scan_dir(out) / 'scan.json').read_text()))
        self.assertEqual([p.name.startswith('pr-grade-proof-') for p in self.temp.iterdir()], [False])

    def test_a_scan_proof_copy_that_is_not_true_or_false_stops_the_run(self) -> None:
        self.configure(scanProofCopy='no')
        run = self.run_script('grade_mode.py', '--base', 'main', '--scan')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('scanProofCopy', run.stderr)

    def test_a_no_makes_no_copy(self) -> None:
        self.configure(scanAbove=False)
        self.scan()
        self.assertEqual(list(self.temp.iterdir()), [])

    def test_a_copy_that_cannot_be_made_dispatches_the_scan_git_only(self) -> None:
        failing = mock.Mock()
        failing.make.side_effect = subprocess.CalledProcessError(128, ['git', 'clone'], stderr='no space left')
        printed = io.StringIO()
        with mock.patch.object(grade_mode, 'proof_copies', return_value=failing), \
                mock.patch('tempfile.tempdir', str(self.temp)), redirect_stdout(printed):
            grade_mode.scan(self.repo, 'main', 'main', grade_mode.load_config(self.repo), ())
        out = printed.getvalue()
        self.assertIn('notice: no proof copy for the scan', out)
        self.assertNotIn('proof copy:', self.dispatch(out))
        self.assertNotIn('proof', json.loads((self.scan_dir(out) / 'scan.json').read_text()))

    def test_a_proof_dir_make_refuses_still_dispatches_the_scan(self) -> None:
        # proof_dir.make exits on a malformed proofDir rather than raising.
        self.configure(proofDir='node_modules')
        out = self.scan()
        self.assertIn('notice: no proof copy for the scan', out)
        self.assertIn('proofDir', out)
        self.assertNotIn('proof copy:', self.dispatch(out))

    def test_the_preps_remove_line_removes_the_scans_copy_with_the_graders(self) -> None:
        scan = self.scan_dir(self.scan())
        scan_root = Path(json.loads((scan / 'scan.json').read_text())['proof'])
        out = self.ok('grade_prep.py', '--base', 'main', '--suspects', str(scan))
        remove = re.search(r'^remove: (.+)$', out, re.M).group(1)
        grade_root = Path(re.search(r'^merge: .* merge (\S+)$', out, re.M).group(1))
        self.assertIn(str(scan_root), remove)
        run = subprocess.run(remove, shell=True, cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse(scan_root.exists())
        self.assertFalse(grade_root.exists())

    def test_without_a_scan_copy_the_remove_line_names_one_root(self) -> None:
        self.configure(scanProofCopy=False)
        scan = self.scan_dir(self.scan())
        out = self.ok('grade_prep.py', '--base', 'main', '--suspects', str(scan))
        remove = re.search(r'^remove: (.+)$', out, re.M).group(1)
        self.assertEqual(len(re.findall(r'pr-grade-proof-', remove)), 1)

    def test_the_scan_never_writes_in_the_authors_checkout(self) -> None:
        self.scan()
        self.assertEqual(git(self.repo, 'status', '--porcelain').strip(), '')


class AgentTest(unittest.TestCase):
    BODY = (ROOT / 'agents/scan.md').read_text().split('---\n', 2)[2]

    def test_in_its_proof_copy_the_scan_may_run_inputs(self) -> None:
        self.assertIn('In your proof copy you may run a test, a script, or an interpreter', self.BODY)

    def test_in_the_repository_it_runs_nothing_but_git(self) -> None:
        self.assertIn('In the repository, run nothing but git', self.BODY)

    def test_with_no_proof_copy_named_it_runs_nothing_but_git_anywhere(self) -> None:
        # An older or vendored grade_mode.py names no copy, and this agent must still keep out of the checkout.
        self.assertIn('When your prompt names no proof copy, run nothing but git anywhere', self.BODY)

    def test_it_installs_nothing_since_the_links_are_the_authors(self) -> None:
        self.assertIn('Install nothing', self.BODY)


class DocsTest(unittest.TestCase):
    def test_the_template_and_readme_name_scan_proof_copy(self) -> None:
        template = json.loads((ROOT / 'skills/pr-grade/templates/pr-grade.json').read_text())
        self.assertIs(template['scanProofCopy'], grade_mode.SCAN_PROOF_COPY)
        self.assertIn('| `scanProofCopy` |', (ROOT / 'README.md').read_text())


if __name__ == '__main__':
    unittest.main()
