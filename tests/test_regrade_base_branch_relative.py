from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_first_read import Repo, git  # noqa: E402


class RegradeRefTest(Repo):
    def test_a_branch_relative_base_naming_a_commit_on_the_branch_is_a_regrade(self) -> None:
        # Round 1 graded the commit `topic` stood at; the fix lands on top, so that commit is now `topic~1`.
        (self.repo / 'store.py').write_text((self.repo / 'store.py').read_text() + 'Y = 2\n' * 200)
        git(self.repo, 'commit', '-q', '-am', 'fix')
        out = self.ok('grade_mode.py', '--base', 'topic~1', '--branch-base', 'main', '--scan')
        self.assertIn('scan: no (a re-grade', out)

    def test_the_pushed_branch_as_the_base_is_a_regrade(self) -> None:
        # Round 1's head was pushed, so origin/topic names the last commit graded.
        git(self.repo, 'update-ref', 'refs/remotes/origin/topic', 'topic')
        (self.repo / 'store.py').write_text((self.repo / 'store.py').read_text() + 'Y = 2\n' * 200)
        git(self.repo, 'commit', '-q', '-am', 'fix')
        out = self.ok('grade_mode.py', '--base', 'origin/topic', '--branch-base', 'main', '--scan')
        self.assertIn('scan: no (a re-grade', out)


if __name__ == '__main__':
    unittest.main()
