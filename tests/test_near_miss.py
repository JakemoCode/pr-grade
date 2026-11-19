#!/usr/bin/env python3
"""Unit tests for near_miss.py: the near-miss table and the second-reader list behind L9's boundaries.

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / 'skills/pr-grade/scripts'
sys.path.insert(0, str(SCRIPTS))
import grade_mode  # noqa: E402
import near_miss  # noqa: E402

CONFIG = dict(grade_mode.DEFAULTS)


def git(root: Path, *args: str) -> str:
    return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout


class GeneratorTest(unittest.TestCase):
    def test_a_heading_gets_every_near_miss_the_issue_names(self) -> None:
        variants = dict(near_miss.near_misses('## Loose ends'))
        self.assertEqual(variants, {
            'as given': '## Loose ends',
            'with a prefix': 'x ## Loose ends',
            'with a suffix': '## Loose ends x',
            'in upper case': '## LOOSE ENDS',
            'in lower case': '## loose ends',
            'with its first letter in the other case': '## loose ends',
            'with surrounding whitespace': ' ## Loose ends ',
            'with CRLF line endings': '## Loose ends\r\n',
            'with a Unicode lookalike': '## L\u043eose ends',
            'empty': '',
            'embedded in a longer word': 'x## Loose endsx',
        })

    def test_a_seed_with_lines_gets_crlf_inside_and_no_letters_gets_a_fullwidth_lookalike(self) -> None:
        variants = dict(near_miss.near_misses('#\n#'))
        self.assertEqual(variants['with CRLF line endings'], '#\r\n#')
        self.assertEqual(variants['with a Unicode lookalike'], '\uff03\n#')

    def test_the_empty_seed_gets_no_casing_or_lookalike_row(self) -> None:
        labels = [label for label, _ in near_miss.near_misses('')]
        self.assertNotIn('in upper case', labels)
        self.assertNotIn('with a Unicode lookalike', labels)
        self.assertIn('with CRLF line endings', labels)


class TableTest(unittest.TestCase):
    """A repository whose branch changes a predicate, with the tests it touches."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / 'tests').mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def table(self, files: dict[str, str], lines: dict | None = None, config: dict | None = None) -> list[str]:
        for path, text in files.items():
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_text(text)
        changed = sorted(files)
        return near_miss.near_miss_table(self.root, lines or {path: None for path in changed}, changed,
                                         config or CONFIG)

    def block(self, out: list[str], header: str) -> list[str]:
        """The rows under the first predicate line holding `header`."""
        start = next(i for i, line in enumerate(out) if line.startswith('- ') and header in line)
        rows = []
        for line in out[start + 1:]:
            if not line.startswith('  '):
                break
            rows.append(line.strip())
        return rows

    def test_the_withdrawn_marker_regex_accepts_the_withdrawn_rows_heading(self) -> None:
        # EngOS PR #900: the marker regex settled a row headed `**Withdrawn rows**`.
        out = self.table({
            'status.py': "import re\n\nWITHDRAWN = re.compile(r'\\*\\*\\s*withdrawn\\b', re.I)\n\n\n"
                         "def status(row):\n    return 'withdrawn' if WITHDRAWN.search(row) else 'open'\n",
            'tests/test_status.py': "from status import status\n\nassert status('**withdrawn** by the author') == 'withdrawn'\n"
                                    "HEADING = '**Withdrawn rows**'\n",
        })
        rows = self.block(out, 'regex')
        self.assertIn("accepted: '**Withdrawn rows**', a literal at tests/test_status.py:4", rows)
        self.assertIn("refused: '', empty", rows)
        header = next(line for line in out if line.startswith('- ') and 'regex' in line)
        self.assertIn('status.py:3', header)
        self.assertIn("seed '**withdrawn** by the author', a literal at tests/test_status.py:3", header)

    def test_a_heading_compared_whole_refuses_its_crlf_line(self) -> None:
        # EngOS PR #990: the hand parser read a CRLF body as having no `## Loose ends` section.
        out = self.table({'wave_report.py': "def loose(body):\n    for line in body.split('\\n'):\n"
                                            "        if line == '## Loose ends':\n            return True\n"})
        rows = self.block(out, "'## Loose ends'")
        self.assertIn("accepted: '## Loose ends', as given", rows)
        self.assertIn("refused: '## Loose ends\\r\\n', with CRLF line endings", rows)

    def test_a_transform_on_the_input_is_applied_before_the_predicate(self) -> None:
        out = self.table({'a.py': "def f(line):\n    return line.strip().startswith('## Loose ends')\n"})
        header = next(line for line in out if line.startswith('- a.py:2'))
        self.assertIn('.strip()', header)
        self.assertIn("accepted: '## Loose ends\\r\\n', with CRLF line endings", self.block(out, 'a.py:2'))

    def test_a_predicate_on_an_unchanged_line_is_left_out(self) -> None:
        out = self.table({'a.py': "def f(s):\n    if s.startswith('old:'):\n        return 1\n"
                                  "    return s.endswith(':new')\n"}, lines={'a.py': [(4, 4)]})
        self.assertFalse(any("'old:'" in line for line in out))
        self.assertTrue(any(line.startswith('- a.py:4') and "':new'" in line for line in out))

    def test_a_regex_no_literal_matches_is_printed_with_no_seed_found(self) -> None:
        out = self.table({'a.py': "import re\nPAT = re.compile(r'^zz\\d{4}$')\n"})
        line = next(line for line in out if line.startswith('- a.py:2'))
        self.assertIn('no seed found', line)

    def test_a_regex_python_cannot_compile_gets_a_row_that_says_why(self) -> None:
        out = self.table({'a.py': "import re\nPAT = re.compile('(unclosed')\n"})
        line = next(line for line in out if line.startswith('- a.py:2'))
        self.assertIn('not run', line)
        self.assertIn('missing ), unterminated subpattern', line)

    def test_a_regex_that_backtracks_forever_times_out_and_the_scan_goes_on(self) -> None:
        with mock.patch.object(near_miss, 'RUN_TIMEOUT', 1):
            out = self.table({'a.py': "import re\nBAD = re.compile(r'^(a+)+$')\nOK = '" + 'a' * 40 + "!'\n"
                                      "def f(s):\n    return s.startswith('ok:')\n"})
        self.assertIn('timed out', next(line for line in out if line.startswith('- a.py:2')))
        self.assertIn("accepted: 'ok:', as given", self.block(out, 'a.py:5'))

    def test_a_typescript_regex_without_node_is_listed_as_found_and_not_run(self) -> None:
        with mock.patch.object(near_miss.shutil, 'which', return_value=None):
            out = self.table({'report.ts': "export const loose = (line: string) => /^## Loose ends$/m.test(line);\n"})
        line = next(line for line in out if line.startswith('- report.ts:1'))
        self.assertIn('not run: node is not on PATH', line)

    @unittest.skipUnless(shutil.which('node'), 'needs node')
    def test_a_typescript_regex_runs_under_node_where_dollar_matches_before_cr(self) -> None:
        # JavaScript counts \r as a line terminator under the m flag, and Python does not: the same pattern
        # accepts a CRLF heading in one and refuses it in the other.
        out = self.table({'report.ts': "export const loose = (line: string) => /^## Loose ends$/m.test(line);\n"
                                       "const HEADING = '## Loose ends';\n"})
        self.assertIn("accepted: '## Loose ends\\r\\n', with CRLF line endings", self.block(out, 'report.ts:1'))

    def test_a_cap_on_predicates_prints_an_overflow_line(self) -> None:
        body = ''.join(f"    if s.startswith('marker {i}:'):\n        return {i}\n" for i in range(near_miss.MAX_PREDICATES + 3))
        out = self.table({'a.py': 'def f(s):\n' + body})
        self.assertEqual(sum(1 for line in out if line.startswith('- a.py:')), near_miss.MAX_PREDICATES)
        self.assertIn('- and 3 more predicates, cut by the cap of', '\n'.join(out))

    def test_a_cap_on_rows_prints_an_overflow_row(self) -> None:
        literals = ''.join(f"L{i} = '## Loose ends {i}'\n" for i in range(near_miss.MAX_ROWS))
        out = self.table({'a.py': f"{literals}def f(s):\n    return s.startswith('## Loose ends')\n"})
        rows = self.block(out, "startswith '## Loose ends'")
        self.assertEqual(len(rows), near_miss.MAX_ROWS + 1)
        self.assertRegex(rows[-1], r'^and \d+ more rows, cut by the cap of \d+$')

    def test_a_changed_file_in_another_language_is_named(self) -> None:
        out = self.table({'scan.rb': "puts 'x' if line.start_with?('## Loose ends')\n"})
        self.assertIn('- not scanned scan.rb: no scanner reads this language', out)


class SecondReaderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        git(self.root, 'init', '-q', '-b', 'main')
        (self.root / 'docs').mkdir()
        (self.root / 'tests').mkdir()
        (self.root / 'wave_report.py').write_text("def loose(body):\n    return body\n")
        (self.root / 'check_pr.py').write_text("import re\n\nLOOSE = re.compile(r'^## Loose ends$', re.M)\n")
        (self.root / 'docs/format.md').write_text('## Loose ends\n')
        (self.root / 'tests/test_check.py').write_text("BODY = '## Loose ends\\n- one'\n")
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-q', '-m', 'base')
        (self.root / 'wave_report.py').write_text("def loose(body):\n    for line in body.split('\\n'):\n"
                                                  "        if line == '## Loose ends':\n            return True\n")
        git(self.root, 'commit', '-q', '-am', 'a second reader')
        self.head = git(self.root, 'rev-parse', 'HEAD').strip()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def readers(self, lines: dict) -> list[str]:
        return near_miss.second_readers(self.root, self.head, lines, CONFIG)

    def test_a_change_to_a_heading_reader_lists_the_other_file_that_reads_it(self) -> None:
        # EngOS PR #990: wave_report.py split `## Loose ends` by hand while check-pr had its own reader.
        out = self.readers({'wave_report.py': [(2, 4)]})
        line = next(line for line in out if "'## Loose ends'" in line)
        self.assertIn('wave_report.py:3', line)
        self.assertIn('check_pr.py:3', line)
        self.assertNotIn('docs/format.md', line)
        self.assertIn('and 1 test file', line)

    def test_a_regex_anchor_is_searched_by_its_longest_literal_run(self) -> None:
        (self.root / 'status.py').write_text("import re\nW = re.compile(r'\\*\\*\\s*withdrawn\\b', re.I)\n")
        (self.root / 'ledger.py').write_text("if '**Withdrawn**' in row:\n    pass\n")
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-q', '-m', 'withdrawn')
        self.head = git(self.root, 'rev-parse', 'HEAD').strip()
        line = next(line for line in self.readers({'status.py': [(2, 2)]}) if 'status.py:2' in line)
        self.assertIn("'withdrawn'", line)
        self.assertIn('ledger.py:1', line)

    def test_an_anchor_with_no_other_reader_says_none_found(self) -> None:
        (self.root / 'solo.py').write_text("def f(s):\n    return s.startswith('Unique-marker:')\n")
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-q', '-m', 'solo')
        self.head = git(self.root, 'rev-parse', 'HEAD').strip()
        line = next(line for line in self.readers({'solo.py': [(2, 2)]}) if 'Unique-marker:' in line)
        self.assertIn('none found', line)


class ReportTest(unittest.TestCase):
    def test_a_scan_that_raises_is_printed_as_a_note(self) -> None:
        with mock.patch.object(near_miss, 'near_miss_table', side_effect=RuntimeError('boom')):
            out = near_miss.report(Path('/nonexistent'), 'HEAD', {}, [], CONFIG)
        self.assertIn('### Near misses', out)
        self.assertTrue(any('boom' in line and 'stopped' in line for line in out))
        self.assertIn('### Second readers', out)


if __name__ == '__main__':
    unittest.main()
