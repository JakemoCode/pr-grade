#!/usr/bin/env python3
"""Unit tests for the first read: when grade_mode.py says to scan, how grade_prep.py numbers the scan's report, and
how the attach step hands it to one grader before that grader is dispatched.

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / 'skills/pr-grade/scripts'
sys.path.insert(0, str(SCRIPTS))
import grade_mode  # noqa: E402
import grade_prep  # noqa: E402

FIXTURES = ROOT / 'tests/fixtures/first_read'
# Counted by hand from each saved report: its numbered items, in order from 1. pr1520 numbers nothing.
ITEMS = {'pr1210-309be13': 4, 'pr1280-a7f7919': 3, 'pr1355-f66320e': 5, 'pr1420-71e2151': 5, 'pr1435-8f171dd': 4,
         'pr1520-8924c6f': 0, 'pr1580-9e42d59': 7, 'pr1581-e535ffe': 6, 'pr1840-772c967': 5, 'pr1910-85a705d': 3,
         'pr825-9aee1d4': 5, 'pr840-3db50d9': 7, 'pr840-801c1a5': 6, 'pr900-efcac51': 2, 'pr905-abf355b': 2,
         'pr910-7c1513b': 4, 'pr935-3ac619c': 5, 'pr975-2ece01b': 5, 'pr990-f130971': 2}
PROMPT = ('Review the change between {base} and {head} in this repository. Use git to read it. Report every bug or '
          'defect you find in the changed code, giving the file path and line for each.')


def git(root: Path, *args: str) -> str:
    return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


class SplitTest(unittest.TestCase):
    def test_the_fixtures_are_the_nineteen_saved_reports(self) -> None:
        self.assertEqual(sorted(p.stem for p in FIXTURES.glob('*.txt')), sorted(ITEMS))

    def test_each_saved_report_splits_at_its_numbered_items(self) -> None:
        for name, count in ITEMS.items():
            with self.subTest(name=name):
                items, rest = grade_prep.suspect_items((FIXTURES / f'{name}.txt').read_text())
                self.assertEqual(len(items), count or 1)
                for number, item in enumerate(items, 1):
                    self.assertTrue(item.startswith(f'S{number} '), item[:40])

    def test_no_line_of_a_saved_report_is_lost(self) -> None:
        # An item's opening line loses its number, and keeps every word after it.
        opener = re.compile(r'^(#{1,6}[ \t]+)?(\*\*)?\d+[.)][ \t]+')
        for name in ITEMS:
            with self.subTest(name=name):
                text = (FIXTURES / f'{name}.txt').read_text()
                items, rest = grade_prep.suspect_items(text)
                out = '\n'.join([*items, rest])
                for line in text.splitlines():
                    if line.strip():
                        self.assertIn(opener.sub('', line).strip(), out)

    def test_a_report_that_numbers_nothing_is_one_suspect_whole(self) -> None:
        text = (FIXTURES / 'pr1520-8924c6f.txt').read_text()
        items, rest = grade_prep.suspect_items(text)
        self.assertEqual((items, rest), (['S1 ' + text.strip()], ''))

    def test_the_last_heading_item_ends_at_a_heading_of_its_level(self) -> None:
        items, rest = grade_prep.suspect_items((FIXTURES / 'pr1355-f66320e.txt').read_text())
        self.assertIn('\n### Minor\n', rest)
        self.assertNotIn('### Minor', items[-1])

    def test_a_paragraph_item_ends_at_the_next_heading(self) -> None:
        items, rest = grade_prep.suspect_items((FIXTURES / 'pr1280-a7f7919.txt').read_text())
        self.assertTrue(items[2].startswith('S3 **Minor — a lost execution record'))
        self.assertIn('## Findings reported through the findings tool', rest)
        self.assertNotIn('## Findings reported', items[2])

    def test_the_opening_before_the_first_item_is_kept_apart(self) -> None:
        items, rest = grade_prep.suspect_items((FIXTURES / 'pr1280-a7f7919.txt').read_text())
        self.assertTrue(rest.startswith('I found one real bug and two minor issues'), rest[:40])

    def test_a_heading_item_loses_its_marks_and_a_bold_item_keeps_its_bold(self) -> None:
        heading, _ = grade_prep.suspect_items('### 1. A title\nbody\n### 2. Another\n')
        bold, _ = grade_prep.suspect_items('**1. A title** (nit)\n\n**2. Another**\n')
        self.assertEqual(heading, ['S1 A title\nbody', 'S2 Another'])
        self.assertEqual(bold, ['S1 **A title** (nit)', 'S2 **Another**'])

    def test_a_bold_number_alone_opens_an_item(self) -> None:
        items, _ = grade_prep.suspect_items('**1.** a\n\n**2.** b\n')
        self.assertEqual(items, ['S1 a', 'S2 b'])

    def test_a_number_out_of_order_stays_in_the_item_above_it(self) -> None:
        items, _ = grade_prep.suspect_items('1. one\n3. three\n2. two\n')
        self.assertEqual(items, ['S1 one\n3. three', 'S2 two'])

    def test_an_indented_or_fenced_number_opens_no_item(self) -> None:
        items, _ = grade_prep.suspect_items('1. one\n   2. nested\n```\n2. in a fence\n```\n2. two\n')
        self.assertEqual(len(items), 2)
        self.assertIn('2. in a fence', items[0])

    def test_an_empty_report_is_no_suspect(self) -> None:
        self.assertEqual(grade_prep.suspect_items(' \n\n'), ([], ''))


class Repo(unittest.TestCase):
    """A Python repository whose branch adds `lines` lines to a silent file, so it grades in one subagent."""

    lines = 151

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.repo, self.temp = base / 'repo', base / 'temp'
        self.repo.mkdir()
        self.temp.mkdir()
        git(self.repo, 'init', '-q', '-b', 'main')
        # The config stays out of git, so a test can change it without an uncommitted edit or a changed line.
        (self.repo / '.git/info/exclude').write_text('.claude/\n')
        (self.repo / '.claude').mkdir()
        self.configure()
        (self.repo / 'store.py').write_text('def claim(x):\n    return x\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'base')
        git(self.repo, 'checkout', '-q', '-b', 'topic')
        (self.repo / 'store.py').write_text('def claim(x):\n    return x\n' + 'X = 1\n' * self.lines)
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'change')

    def configure(self, **keys) -> None:
        (self.repo / '.claude/pr-grade.json').write_text(json.dumps({'silent': ['store.py'], **keys}))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_script(self, name: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPTS / name), *args], cwd=self.repo,
                              env={**os.environ, 'TMPDIR': str(self.temp)}, capture_output=True, text=True)

    def ok(self, name: str, *args: str) -> str:
        run = self.run_script(name, *args)
        self.assertEqual(run.returncode, 0, run.stderr)
        return run.stdout

    def scan(self, *args: str) -> str:
        return self.ok('grade_mode.py', '--base', 'main', '--scan', *args)

    def decision(self, *args: str) -> str:
        return re.search(r'^scan: .*$', self.scan(*args), re.M).group(0)


class DecisionTest(Repo):
    def test_a_change_over_the_default_150_lines_scans(self) -> None:
        self.assertEqual(self.decision(),
                         'scan: yes (151 changed lines, over scanAbove 150, in round 1 of a subagent grade)')

    def test_a_change_of_exactly_150_lines_does_not(self) -> None:
        git(self.repo, 'rm', '-q', '--cached', 'store.py')
        (self.repo / 'store.py').write_text('def claim(x):\n    return x\n' + 'X = 1\n' * 150)
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'one line fewer')
        self.assertEqual(self.decision(), 'scan: no (150 changed lines, not over scanAbove 150)')

    def test_deletions_count_as_changed_lines(self) -> None:
        # Against main the two lines of `claim` are deleted and written again, beside the 151 added below them.
        (self.repo / 'store.py').write_text('def claim(y):\n    return y\n' + 'X = 1\n' * 151)
        git(self.repo, 'commit', '-q', '-am', 'rename')
        self.assertIn('155 changed lines', self.decision())

    def test_scan_above_comes_from_the_config(self) -> None:
        self.configure(scanAbove=151)
        self.assertEqual(self.decision(), 'scan: no (151 changed lines, not over scanAbove 151)')
        self.configure(scanAbove=150)
        self.assertTrue(self.decision().startswith('scan: yes (151 changed lines, over scanAbove 150'))

    def test_scan_above_false_turns_it_off(self) -> None:
        self.configure(scanAbove=False)
        self.assertEqual(self.decision(), f'scan: no (scanAbove is false in {grade_mode.CONFIG})')

    def test_scan_above_zero_scans_every_change(self) -> None:
        self.configure(scanAbove=0)
        self.assertTrue(self.decision().startswith('scan: yes (151 changed lines, scanAbove 0 scans every change'))

    def test_a_scan_above_that_is_not_a_count_stops_the_run(self) -> None:
        for value in ('150', True, -1, 1.5, None):
            with self.subTest(value=value):
                self.configure(scanAbove=value)
                run = self.run_script('grade_mode.py', '--base', 'main', '--scan')
                self.assertNotEqual(run.returncode, 0)
                self.assertIn('scanAbove', run.stderr)

    def test_an_in_thread_grade_does_not_scan(self) -> None:
        self.configure(silent=[], ordinary=['store.py'])
        self.assertEqual(self.decision(), 'scan: no (an in-thread grade)')

    def test_without_prompt_files_it_does_not_scan(self) -> None:
        # The report reaches a grader only through its prompt file; a pasted block would make the coordinator type it.
        self.configure(promptFiles=False)
        self.assertEqual(self.decision(), 'scan: no (promptFiles is false, and the first read reaches a grader '
                                          'only through its prompt file)')

    def test_a_regrade_does_not_scan(self) -> None:
        graded = git(self.repo, 'rev-parse', 'HEAD').strip()
        (self.repo / 'store.py').write_text((self.repo / 'store.py').read_text() + 'Y = 2\n' * 200)
        git(self.repo, 'commit', '-q', '-am', 'fix')
        out = self.ok('grade_mode.py', '--base', graded, '--branch-base', 'main', '--scan')
        self.assertIn(f'scan: no (a re-grade: {graded} is after where the branch left main)', out)

    def test_a_base_behind_the_branch_base_is_still_round_one(self) -> None:
        # A stale local main is an older commit of main, not a commit on the branch.
        git(self.repo, 'checkout', '-q', 'main')
        (self.repo / 'other.txt').write_text('later on main\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'main moves on')
        git(self.repo, 'checkout', '-q', 'topic')
        out = self.ok('grade_mode.py', '--base', 'main~1', '--branch-base', 'main', '--scan')
        self.assertIn('scan: yes (151 changed lines', out)

    def test_a_branch_base_git_cannot_read_stops_the_run(self) -> None:
        # `--is-ancestor` exits 128 for a ref it cannot read, which is no answer about the round.
        run = self.run_script('grade_mode.py', '--base', 'main', '--branch-base', 'nosuchref', '--scan')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('nosuchref', run.stderr)
        self.assertNotIn('re-grade', run.stdout)

    def test_a_local_main_ahead_of_a_stale_origin_main_is_round_one(self) -> None:
        # The branch left local main, which a fetch has not yet brought origin/main up to.
        git(self.repo, 'update-ref', 'refs/remotes/origin/main', 'main')
        (self.repo / 'store.py').write_text((self.repo / 'store.py').read_text() + 'Y = 2\n')
        git(self.repo, 'commit', '-q', '-am', 'second')
        git(self.repo, 'branch', '-f', 'main', 'HEAD~1')
        self.configure(scanAbove=0)
        self.assertIn('scan: yes (1 changed lines', self.decision())

    def test_lock_files_and_excluded_prefixes_do_not_count(self) -> None:
        (self.repo / 'package-lock.json').write_text('{}\n' * 500)
        (self.repo / 'kept').mkdir()
        (self.repo / 'kept/grade.txt').write_text('z\n' * 500)
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'more')
        self.assertIn('651 changed lines', self.decision())
        self.assertIn('151 changed lines', self.decision('--exclude-path', 'kept/'))

    def test_uncommitted_edits_stop_it(self) -> None:
        (self.repo / 'store.py').write_text('dirty\n')
        run = self.run_script('grade_mode.py', '--base', 'main', '--scan')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('uncommitted', run.stderr)

    def test_a_yes_prints_the_tested_prompt_with_the_report_path_and_the_prep_option(self) -> None:
        out = self.scan()
        head = git(self.repo, 'rev-parse', 'HEAD').strip()
        fork = git(self.repo, 'merge-base', 'main', 'HEAD').strip()
        found = re.search(r'^--suspects (\S+)$', out, re.M)
        self.assertIsNotNone(found, out)
        scan = Path(found.group(1))
        self.assertIn(PROMPT.format(base=fork, head=head)
                      + f' Write the report to {scan / "first-read.md"} with the Write tool.', out)
        self.assertEqual(json.loads((scan / 'scan.json').read_text()),
                         {'repository': str(self.repo), 'base': fork, 'head': head})
        # Claude Code refuses a subagent's Write to report*.md, summary*.md, findings*.md and analysis*.md.
        self.assertIsNone(re.match(r'(report|summary|findings|analysis).*\.md$', 'first-read.md', re.I))

    def test_a_no_prints_no_dispatch(self) -> None:
        self.configure(scanAbove=False)
        out = self.scan()
        self.assertNotIn('--suspects', out)
        self.assertEqual(list(self.temp.iterdir()), [])

    def test_without_the_option_it_prints_no_scan_line(self) -> None:
        self.assertNotIn('scan:', self.ok('grade_mode.py', '--base', 'main'))


class HandOffTest(Repo):
    REPORT = '1. **Bug** at `store.py:3`.\n\n2. **Minor** at `store.py:4`.\n   more on two\n\n3. Nit.\n'

    def prep_with_scan(self, *args: str) -> tuple[str, Path]:
        scan = Path(re.search(r'^--suspects (\S+)$', self.scan(), re.M).group(1))
        return scan, self.ok('grade_prep.py', '--base', 'main', '--suspects', str(scan), *args)

    @staticmethod
    def proof_root(out: str) -> Path:
        return Path(re.search(r'^merge: .* merge (\S+)$', out, re.M).group(1))

    def test_the_grader_the_first_read_goes_to_is_withheld_until_the_attach(self) -> None:
        scan, out = self.prep_with_scan()
        root = self.proof_root(out)
        self.assertFalse((root / 'grade/prompt.md').exists())
        self.assertNotIn('Read your pr-grade dispatch', out)
        attach = re.search(r'^attach: (.+)$', out, re.M)
        self.assertIsNotNone(attach, out)
        self.assertEqual(attach.group(1), f"python3 {SCRIPTS / 'grade_prep.py'} --attach {root}")

    def test_in_fan_out_only_the_correctness_grader_waits(self) -> None:
        _, out = self.prep_with_scan('--groups', 'timing=L1,L2,L7,L16',
                                     '--groups', 'correctness=L9,L10,L4,L11,L15',
                                     '--groups', 'lifetime=L3,L5,L6,L8,L14')
        root = self.proof_root(out)
        lines = re.findall(r'^Read your pr-grade dispatch from (\S+) ', out, re.M)
        self.assertEqual(lines, [str(root / 'timing/prompt.md'), str(root / 'lifetime/prompt.md')])
        self.assertIn('withheld until the first read is attached: correctness', out)

    def test_with_no_group_holding_l9_the_first_group_takes_the_first_read(self) -> None:
        _, out = self.prep_with_scan('--skip', 'L9')
        self.assertIn('withheld until the first read is attached: grade', out)

    def test_the_attach_numbers_the_report_into_the_prompt_file_before_the_lens_text(self) -> None:
        scan, out = self.prep_with_scan()
        (scan / 'first-read.md').write_text(self.REPORT)
        root = self.proof_root(out)
        attached = self.ok('grade_prep.py', '--attach', str(root))
        prompt = (root / 'grade/prompt.md').read_text()
        section = prompt.index('\nSuspects from a first read:\n')
        self.assertLess(prompt.index(f'- Write your report to: {root / "grade/grade.md"}'), section)
        self.assertLess(section, prompt.index('===== lens text ====='))
        self.assertIn('\nS1 **Bug** at `store.py:3`.\n', prompt)
        self.assertIn('\nS2 **Minor** at `store.py:4`.\n   more on two\n', prompt)
        self.assertIn('\nS3 Nit.\n', prompt)
        self.assertFalse((root / 'grade/prompt-pending.md').exists())
        meta = json.loads((root / 'grade.json').read_text())
        self.assertEqual(meta['suspects'], {'grade': 3})
        self.assertEqual(meta['firstRead'], '3 suspects attached to grade')
        self.assertIn(f'Read your pr-grade dispatch from {root / "grade/prompt.md"} ', attached)
        self.assertIn('first read: 3 suspects attached to grade', attached)
        # The report is kept with the grade's records, and the scan's own directory is gone.
        self.assertEqual((root / 'first-read.md').read_text(), self.REPORT)
        self.assertFalse(scan.exists())

    def test_a_report_that_numbers_nothing_goes_whole_with_a_notice(self) -> None:
        scan, out = self.prep_with_scan()
        (scan / 'first-read.md').write_text('Two things look off in store.py.\n')
        root = self.proof_root(out)
        attached = self.ok('grade_prep.py', '--attach', str(root))
        self.assertIn('notice: the first read numbers no items, so grade gets it whole as S1', attached)
        self.assertIn('\nS1 Two things look off in store.py.\n', (root / 'grade/prompt.md').read_text())
        self.assertEqual(json.loads((root / 'grade.json').read_text())['suspects'], {'grade': 1})

    def test_with_no_report_the_grader_goes_without_suspects_and_says_so(self) -> None:
        # The scan failed, ran out of turns, or a plugin older than 0.19.0 had no pr-grade:scan to dispatch.
        scan, out = self.prep_with_scan()
        root = self.proof_root(out)
        attached = self.ok('grade_prep.py', '--attach', str(root))
        prompt = (root / 'grade/prompt.md').read_text()
        self.assertNotIn('Suspects from a first read', prompt)
        meta = json.loads((root / 'grade.json').read_text())
        self.assertNotIn('suspects', meta)
        why = f'did not run: no report at {scan / "first-read.md"}, so grade grades with no suspects'
        self.assertEqual(meta['firstRead'], why)
        self.assertIn(f'notice: the first read {why}', attached)
        self.assertIn(f'Read your pr-grade dispatch from {root / "grade/prompt.md"} ', attached)
        self.assertFalse(scan.exists())

    def test_an_empty_report_is_no_report(self) -> None:
        scan, out = self.prep_with_scan()
        (scan / 'first-read.md').write_text('\n')
        root = self.proof_root(out)
        self.ok('grade_prep.py', '--attach', str(root))
        self.assertTrue(json.loads((root / 'grade.json').read_text())['firstRead'].startswith('did not run: the report '
                                                                                               'at '))
        self.assertNotIn('Suspects from a first read', (root / 'grade/prompt.md').read_text())

    def test_a_second_attach_prints_the_line_again_and_changes_nothing(self) -> None:
        scan, out = self.prep_with_scan()
        (scan / 'first-read.md').write_text(self.REPORT)
        root = self.proof_root(out)
        self.ok('grade_prep.py', '--attach', str(root))
        before = (root / 'grade/prompt.md').read_text()
        again = self.ok('grade_prep.py', '--attach', str(root))
        self.assertEqual((root / 'grade/prompt.md').read_text(), before)
        self.assertIn(f'Read your pr-grade dispatch from {root / "grade/prompt.md"} ', again)

    def test_a_scan_of_another_head_hands_nothing_and_its_directory_goes(self) -> None:
        # The scan may still be writing, so the attach, run once it returns, refuses the report and removes it.
        scan = Path(re.search(r'^--suspects (\S+)$', self.scan(), re.M).group(1))
        scanned = git(self.repo, 'rev-parse', 'HEAD').strip()
        (self.repo / 'store.py').write_text((self.repo / 'store.py').read_text() + 'Z = 3\n')
        git(self.repo, 'commit', '-q', '-am', 'later')
        out = self.ok('grade_prep.py', '--base', 'main', '--suspects', str(scan))
        (scan / 'first-read.md').write_text(self.REPORT)
        root = self.proof_root(out)
        attached = self.ok('grade_prep.py', '--attach', str(root))
        self.assertIn(f'notice: the first read did not run: it read {scanned}', attached)
        self.assertNotIn('Suspects from a first read', (root / 'grade/prompt.md').read_text())
        meta = json.loads((root / 'grade.json').read_text())
        self.assertNotIn('suspects', meta)
        self.assertTrue(meta['firstRead'].startswith(f'did not run: it read {scanned}'))
        self.assertFalse(scan.exists())

    def test_the_merge_prints_what_the_first_read_did(self) -> None:
        for report, expected in ((self.REPORT, 'first read: 3 suspects attached to grade'),
                                 (None, 'first read: did not run: no report at ')):
            with self.subTest(expected=expected):
                scan, out = self.prep_with_scan()
                if report:
                    (scan / 'first-read.md').write_text(report)
                root = self.proof_root(out)
                self.ok('grade_prep.py', '--attach', str(root))
                merged = self.ok('grade_block.py', 'merge', str(root))
                self.assertIn(expected, merged)

    def test_a_grade_with_no_first_read_merges_with_no_line_for_it(self) -> None:
        out = self.ok('grade_prep.py', '--base', 'main')
        self.assertNotIn('first read', self.ok('grade_block.py', 'merge', str(self.proof_root(out))))


class AgentTest(unittest.TestCase):
    AGENT = (ROOT / 'agents/scan.md').read_text() if (ROOT / 'agents/scan.md').is_file() else ''

    def frontmatter(self) -> dict:
        head = self.AGENT.split('---\n')[1]
        return dict(line.split(': ', 1) for line in head.splitlines() if ': ' in line)

    def test_the_scan_runs_on_opus_at_high_without_claude_md(self) -> None:
        meta = self.frontmatter()
        self.assertEqual((meta['name'], meta['model'], meta['effort'], meta['omitClaudeMd'], meta['maxTurns']),
                         ('scan', 'opus', 'high', 'true', '50'))

    def test_the_scan_cannot_edit(self) -> None:
        tools = [tool.strip() for tool in self.frontmatter()['tools'].split(',')]
        self.assertEqual(tools, ['Read', 'Glob', 'Grep', 'Bash', 'Write'])

    def test_the_scan_reads_no_lens_and_no_skill(self) -> None:
        body = self.AGENT.split('---\n', 2)[2]
        for word in ('lens', 'SKILL', 'L9', 'pr-grade-lenses'):
            with self.subTest(word=word):
                self.assertNotIn(word, body)

    def test_the_scan_runs_nothing_but_git(self) -> None:
        # The test's reader ran nothing. Given auto mode, the 0.19.0 smoke grade's scan ran the suites and probe scripts
        # in the author's checkout and took 453 s against the test's 150 s median.
        body = self.AGENT.split('---\n', 2)[2]
        self.assertIn('Run nothing but git', body)

    def test_the_prompt_the_scan_is_given_is_the_tested_one(self) -> None:
        self.assertEqual(grade_mode.SCAN_PROMPT, PROMPT)


class DocsTest(unittest.TestCase):
    def test_section_2_gives_the_scan_steps_in_the_order_the_scripts_print_them(self) -> None:
        skill = (ROOT / 'skills/pr-grade/SKILL.md').read_text()
        two = skill[skill.index('## 2.'):skill.index('## 4.')]
        order = [two.index(word) for word in ('grade_mode.py --scan', 'pr-grade:scan', '--suspects', 'attach:',
                                              'first read: did not run')]
        self.assertEqual(order, sorted(order))

    def test_the_preps_usage_names_suspects_and_attach(self) -> None:
        usage = grade_prep.__doc__.split('\n\n', 1)[1]
        for option in ('--suspects DIR', '--attach PROOF_ROOT'):
            with self.subTest(option=option):
                self.assertIn(option, usage)

    def test_the_template_and_readme_name_scan_above(self) -> None:
        template = json.loads((ROOT / 'skills/pr-grade/templates/pr-grade.json').read_text())
        self.assertEqual(template['scanAbove'], 150)
        self.assertIn('| `scanAbove` | `150` |', (ROOT / 'README.md').read_text())

    def test_the_scan_agent_is_a_silent_failure_file_here(self) -> None:
        config = json.loads((ROOT / '.claude/pr-grade.json').read_text())
        self.assertIn('agents/scan.md', config['silent'])


if __name__ == '__main__':
    unittest.main()
