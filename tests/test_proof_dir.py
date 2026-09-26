#!/usr/bin/env python3
"""Unit tests for proof_dir.py: a checkout of the graded commit per grader, its linked dependencies,
and its removal.

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / 'skills/pr-grade/scripts'
sys.path.insert(0, str(SCRIPTS))
import proof_dir  # noqa: E402

INSTALLED = 'installed\n'


def git(root: Path, *args: str) -> str:
    return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


def fields(lines: list[str]) -> dict[str, str]:
    return dict(line.split(': ', 1) for line in lines)


class Fixture(unittest.TestCase):
    """A committed repository with an installed node_modules, and a separate directory for proof roots."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.repo, self.parent = base / 'repo', base / 'proofs'
        self.repo.mkdir()
        self.parent.mkdir()
        git(self.repo, 'init', '-q', '-b', 'main')
        (self.repo / '.gitignore').write_text('node_modules/\n.env.test\n')
        (self.repo / 'package.json').write_text('{"name": "x"}\n')
        (self.repo / 'src.js').write_text('module.exports = 1\n')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-q', '-m', 'base')
        (self.repo / 'node_modules' / 'dep').mkdir(parents=True)
        (self.repo / 'node_modules' / 'dep' / 'index.js').write_text(INSTALLED)
        self.sha = self.head()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def head(self) -> str:
        return git(self.repo, 'rev-parse', 'HEAD').strip()

    def commit(self, path: str, text: str) -> str:
        (self.repo / path).write_text(text)
        git(self.repo, 'add', path)
        git(self.repo, 'commit', '-q', '-m', path)
        return self.head()

    def make(self, *names: str, sha: str | None = None, config: dict | None = None) -> dict[str, str]:
        self.lines = proof_dir.make(self.repo, sha or self.head(), list(names) or ['grade'], config or {},
                                    self.parent)
        return fields(self.lines)

    def installed(self) -> bool:
        return (self.repo / 'node_modules' / 'dep' / 'index.js').read_text() == INSTALLED


class CopyTest(Fixture):
    def test_each_name_gets_its_own_checkout_of_the_graded_commit(self) -> None:
        out = self.make('timing', 'reach')
        for name in ('timing', 'reach'):
            copy = Path(out[f'copy {name}'])
            self.assertEqual(git(copy, 'rev-parse', 'HEAD').strip(), self.sha)
            self.assertEqual((copy / 'src.js').read_text(), 'module.exports = 1\n')
            self.assertTrue(Path(out[f'scratch {name}']).is_dir())

    def test_a_file_written_in_one_copy_is_not_in_another(self) -> None:
        out = self.make('timing', 'reach')
        (Path(out['copy timing']) / 'proof.test.js').write_text('x\n')
        self.assertFalse((Path(out['copy reach']) / 'proof.test.js').exists())

    def test_the_copy_holds_the_commit_not_uncommitted_work(self) -> None:
        (self.repo / 'src.js').write_text('module.exports = 2\n')
        (self.repo / 'new.js').write_text('untracked\n')
        copy = Path(self.make(sha=self.sha)['copy grade'])
        self.assertEqual((copy / 'src.js').read_text(), 'module.exports = 1\n')
        self.assertFalse((copy / 'new.js').exists())

    def test_an_earlier_commit_can_be_graded(self) -> None:
        self.commit('src.js', 'module.exports = 2\n')
        copy = Path(self.make(sha=self.sha)['copy grade'])
        self.assertEqual((copy / 'src.js').read_text(), 'module.exports = 1\n')

    def test_a_copy_is_its_own_repository_and_nothing_is_registered_in_the_authors(self) -> None:
        # Code that finds its repository through git must find the copy, and a copy nobody removed must
        # leave no worktree entry behind.
        copy = Path(self.make()['copy grade'])
        self.assertEqual((copy / git(copy, 'rev-parse', '--git-common-dir').strip()).resolve(), copy / '.git')
        self.assertEqual(len(git(self.repo, 'worktree', 'list').splitlines()), 1)

    def test_a_copy_survives_a_reset_and_gc_in_the_authors_checkout(self) -> None:
        # A --shared clone borrows the author's objects, and this sequence left it unable to read its tree.
        self.commit('src.js', 'module.exports = 2\n')
        graded = self.head()
        copy = Path(self.make(sha=graded)['copy grade'])
        git(self.repo, 'reset', '-q', '--hard', 'HEAD~1')
        git(self.repo, 'reflog', 'expire', '--expire=now', '--all')
        git(self.repo, 'gc', '-q', '--prune=now')
        git(copy, 'cat-file', '-e', f'{graded}^{{tree}}')
        self.assertEqual(git(copy, 'show', f'{graded}:src.js'), 'module.exports = 2\n')

    def test_a_linked_worktree_is_copied_through_its_common_repository(self) -> None:
        # Authors often work in a linked worktree, whose .git is a file.
        tree = self.parent.parent / 'tree'
        git(self.repo, 'worktree', 'add', '-q', '-b', 'topic', str(tree))
        (tree / 'src.js').write_text('module.exports = 3\n')
        git(tree, 'commit', '-q', '-am', 'topic')
        lines = proof_dir.make(tree, git(tree, 'rev-parse', 'HEAD').strip(), ['grade'], {}, self.parent)
        self.assertEqual((Path(fields(lines)['copy grade']) / 'src.js').read_text(), 'module.exports = 3\n')

    def test_no_hook_of_the_authors_runs(self) -> None:
        ran = self.parent.parent / 'hook-ran'
        hook = self.repo / '.git' / 'hooks' / 'post-checkout'
        hook.write_text(f'#!/bin/sh\ntouch {ran}\n')
        hook.chmod(0o755)
        self.make()
        self.assertFalse(ran.exists())

    def test_a_name_that_is_not_one_plain_path_component_is_refused(self) -> None:
        for names in (['../escape'], ['a/b'], ['.hidden'], [''], ['reach', 'reach']):
            with self.subTest(names=names), self.assertRaises(SystemExit):
                proof_dir.make(self.repo, self.sha, names, {}, self.parent)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_a_failed_make_leaves_no_root(self) -> None:
        with self.assertRaises(subprocess.CalledProcessError):
            proof_dir.make(self.repo, '0' * 40, ['grade'], {}, self.parent)
        self.assertEqual(list(self.parent.iterdir()), [])


class LinkTest(Fixture):
    def test_node_modules_is_linked_when_its_pins_match_the_graded_commit(self) -> None:
        out = self.make()
        self.assertEqual((Path(out['copy grade']) / 'node_modules').resolve(), self.repo / 'node_modules')
        self.assertIn('linked node_modules', out)

    def test_a_lock_file_the_commit_lacks_leaves_it_unlinked(self) -> None:
        (self.repo / 'package-lock.json').write_text('{}\n')
        out = self.make()
        self.assertFalse((Path(out['copy grade']) / 'node_modules').exists())
        self.assertIn('(package-lock.json)', out['not linked node_modules'])

    def test_grading_a_commit_whose_manifest_differs_leaves_it_unlinked(self) -> None:
        # The change edits package.json, so the installed node_modules belongs to another manifest.
        self.commit('package.json', '{"name": "x", "dependencies": {"y": "1"}}\n')
        out = self.make(sha=self.sha)
        self.assertIn('(package.json)', out['not linked node_modules'])

    def test_a_branch_that_changed_its_pins_gets_no_link_even_when_committed(self) -> None:
        # The checkout matches the commit, but the node_modules installed before the change may not.
        fork = self.sha
        head = self.commit('package.json', '{"name": "x", "dependencies": {"y": "2"}}\n')
        out = self.make(sha=head)
        self.assertIn('linked node_modules', out)
        out = fields(proof_dir.make(self.repo, head, ['grade'], {}, self.parent, since=fork))
        self.assertIn('(package.json)', out['not linked node_modules'])

    def test_the_install_named_follows_the_lock_file_at_the_graded_commit(self) -> None:
        self.commit('pnpm-lock.yaml', 'lockfileVersion: 9\n')
        (self.repo / 'pnpm-lock.yaml').write_text('lockfileVersion: 9\n# edited\n')
        self.assertTrue(self.make()['not linked node_modules'].endswith('in each copy run: pnpm install --frozen-lockfile'))

    def test_a_configured_list_replaces_the_defaults_and_links_an_unpinned_file(self) -> None:
        (self.repo / '.env.test').write_text('KEY=1\n')
        copy = Path(self.make(config={'proofDir': [{'path': '.env.test'}]})['copy grade'])
        self.assertEqual((copy / '.env.test').read_text(), 'KEY=1\n')
        self.assertFalse((copy / 'node_modules').exists())

    def test_a_missing_default_is_quiet_and_a_missing_configured_entry_is_named(self) -> None:
        self.make()
        self.assertFalse(any('.venv' in line for line in self.lines))
        self.assertIn('missing .venv', self.make(config={'proofDir': [{'path': '.venv'}]}))

    def test_a_malformed_proof_dir_stops_before_anything_is_made(self) -> None:
        for config in ({'proofDir': {'path': 'x'}}, {'proofDir': [{'path': '../x'}]},
                       {'proofDir': [{'path': 'x', 'pins': []}]}, {'proofDir': [{'path': 'x', 'pinnedBy': 'x'}]}):
            with self.subTest(config=config), self.assertRaises(SystemExit):
                self.make(config=config)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_an_author_checkout_off_the_graded_commit_is_warned_about(self) -> None:
        self.assertNotIn('warning', self.make())
        (self.repo / 'src.js').write_text('module.exports = 2\n')
        self.assertIn('warning', self.make())


class RemoveTest(Fixture):
    def test_remove_deletes_the_root_and_never_what_a_copy_links_to(self) -> None:
        root = Path(self.make()['root'])
        proof_dir.remove(root)
        self.assertFalse(root.exists())
        self.assertTrue(self.installed())

    def test_remove_refuses_a_directory_make_did_not_make(self) -> None:
        with self.assertRaises(SystemExit):
            proof_dir.remove(self.repo)
        self.assertTrue((self.repo / 'package.json').exists())

    def age(self, path: Path) -> None:
        stale = time.time() - proof_dir.STALE_AFTER - 60
        os.utime(path, (stale, stale))

    def test_make_sweeps_this_repositorys_idle_roots_and_keeps_the_rest(self) -> None:
        old, recent = Path(self.make()['root']), Path(self.make()['root'])
        self.age(old / proof_dir.MARKER)
        self.make()
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(self.installed())

    def test_a_report_written_since_keeps_an_old_root(self) -> None:
        # Graders reported and the coordinator has not merged yet: the round is not over.
        root = Path(self.make()['root'])
        self.age(root / proof_dir.MARKER)
        (root / 'grade' / 'report.md').write_text('Score: 5/5\n')
        self.make()
        self.assertTrue((root / 'grade' / 'report.md').exists())

    def test_another_repositorys_root_is_left_to_it(self) -> None:
        other = self.parent.parent / 'other'
        other.mkdir()
        git(other, 'init', '-q', '-b', 'main')
        (other / 'x.txt').write_text('x\n')
        git(other, 'add', '-A')
        git(other, 'commit', '-q', '-m', 'x')
        theirs = Path(fields(proof_dir.make(other, git(other, 'rev-parse', 'HEAD').strip(), ['grade'], {},
                                            self.parent))['root'])
        self.age(theirs / proof_dir.MARKER)
        self.make()
        self.assertTrue(theirs.exists())

    def test_the_printed_remove_line_undoes_the_printed_make(self) -> None:
        env = {**os.environ, 'TMPDIR': str(self.parent)}
        made = subprocess.run([sys.executable, str(SCRIPTS / 'proof_dir.py'), 'make', 'reach'], cwd=self.repo,
                              env=env, capture_output=True, text=True, check=True).stdout
        out = fields(made.splitlines())
        self.assertTrue(Path(out['copy reach']).is_dir())
        subprocess.run(shlex.split(out['remove']), check=True, capture_output=True)
        self.assertFalse(Path(out['root']).exists())
        self.assertTrue(self.installed())


if __name__ == '__main__':
    unittest.main()

class TemplateTest(unittest.TestCase):
    def test_the_config_templates_proof_dir_is_valid(self) -> None:
        template = json.loads((SCRIPTS.parent / 'templates/pr-grade.json').read_text())
        self.assertEqual(proof_dir.entries(template), template['proofDir'])
