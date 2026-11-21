from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_first_read import Repo, git  # noqa: E402


class RegradeWithNoOriginMainTest(Repo):
    def test_a_regrade_with_no_origin_main_and_no_branch_base_gets_no_scan(self) -> None:
        # The fixture has no origin/main, as a repository whose default branch is master has none.
        (self.repo / 'store.py').write_text((self.repo / 'store.py').read_text() + 'Y = 2\n' * 200)
        git(self.repo, 'commit', '-q', '-am', 'fix')
        out = self.ok('grade_mode.py', '--base', 'topic~1', '--scan')
        self.assertIn('scan: no (a re-grade', out)


if __name__ == '__main__':
    unittest.main()
