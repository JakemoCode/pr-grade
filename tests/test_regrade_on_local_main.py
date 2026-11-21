#!/usr/bin/env python3
"""A re-grade of a branch that is itself the local main, ahead of a stale origin/main, is read as round 1."""
from __future__ import annotations

import unittest

from test_first_read import Repo, git


class RegradeOnLocalMainTest(Repo):
    def test_a_regrade_on_the_local_main_does_not_scan(self) -> None:
        # origin/main is where the work began; the author grades from local main, which is ahead of it.
        git(self.repo, 'update-ref', 'refs/remotes/origin/main', 'main')
        git(self.repo, 'checkout', '-q', 'main')
        git(self.repo, 'merge', '-q', '--ff-only', 'topic')
        graded = git(self.repo, 'rev-parse', 'HEAD').strip()
        (self.repo / 'store.py').write_text((self.repo / 'store.py').read_text() + 'Y = 2\n' * 200)
        git(self.repo, 'commit', '-q', '-am', 'fix')
        out = self.ok('grade_mode.py', '--base', graded, '--scan')
        self.assertIn(f'scan: no (a re-grade: {graded} is after where the branch left origin/main)', out)


if __name__ == '__main__':
    unittest.main()
