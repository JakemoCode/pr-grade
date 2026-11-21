#!/usr/bin/env python3
"""Unit tests for the skill's files: a core SKILL.md that every grade loads, and the files beside it that a
coordinator reads at the step that needs one. Each numbered section lives in exactly one file and keeps its number,
since ForgeSmith-OS and agents/grade.md cite sections by number.

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / 'skills/pr-grade'
sys.path.insert(0, str(SKILL_DIR / 'scripts'))
import grade_prep  # noqa: E402

# Where each section lives. The core holds what every subagent or fan-out grade needs before its graders report.
PLACES = {'SKILL.md': ['1', '2', '4', '6'], 'lenses.md': ['3'], 'scoring.md': ['5'], 'fix-and-regrade.md': ['7'],
          'setup-and-enforcement.md': ['0', '8'], 'surface-grades.md': ['9']}
SECTION = re.compile(r'(?m)^## (\d+)\. ')
LINK = re.compile(r'\]\(([\w-]+\.md)\)')


def files() -> dict[str, str]:
    return {path.name: path.read_text() for path in sorted(SKILL_DIR.glob('*.md'))}


def cut(text: str) -> tuple[str, dict[str, str]]:
    """The text before its first numbered section, and each numbered section up to the next one."""
    marks = [(match.start(), match.group(1)) for match in SECTION.finditer(text)]
    ends = [start for start, _ in marks[1:]] + [len(text)]
    return text[:marks[0][0]], {number: text[start:end] for (start, number), end in zip(marks, ends)}


class SplitTest(unittest.TestCase):
    def test_each_section_lives_in_the_one_file_named_for_it(self) -> None:
        found = {name: list(cut(text)[1]) for name, text in files().items()}
        self.assertEqual(found, PLACES)

    def test_the_core_names_each_file_beside_it_and_names_only_files_that_exist(self) -> None:
        core = files()['SKILL.md']
        named = set(LINK.findall(core))
        self.assertEqual(named, set(PLACES) - {'SKILL.md'})
        for name in named:
            with self.subTest(file=name):
                self.assertTrue((SKILL_DIR / name).is_file())

    def test_an_in_thread_grade_is_sent_to_the_lenses_and_scoring_before_it_grades(self) -> None:
        pointer = next(paragraph for paragraph in files()['SKILL.md'].split('\n\n') if 'In-thread, read' in paragraph)
        self.assertIn('(lenses.md)', pointer)
        self.assertIn('(scoring.md)', pointer)
        self.assertIn('before you grade', pointer)

    def test_section_2_says_what_the_prep_slices_now(self) -> None:
        two = cut(files()['SKILL.md'])[1]['2']
        self.assertNotIn('section 3 up to its first lens', two)
        self.assertNotIn('slices the `SKILL.md` beside', two)


class SkillTextTest(unittest.TestCase):
    """grade_prep.py reads sections 3 to 5 from whichever of the skill's files holds each."""

    def test_the_shipped_files_give_every_lens_and_sections_4_and_5(self) -> None:
        lenses, rules = grade_prep.skill_lens_text(grade_prep.skill_texts(SKILL_DIR)[0])
        sections = {lens: piece for lens, piece in grade_prep.lens_pieces(lenses) if lens}
        self.assertEqual(list(sections), grade_prep.grade_block.DEFAULT_LENSES)
        self.assertTrue(lenses.startswith('## 3. The fourteen lenses\n'))
        self.assertIn('*Examples, from EngOS:* PR #900', sections['L9'])
        self.assertTrue(rules.startswith('## 4. What a finding must carry\n'))
        self.assertIn('\n## 5. Score it\n', rules)
        self.assertNotIn('## 6. Report it', rules)
        # No pointer for the coordinator reaches a grader.
        self.assertNotIn('.md)', lenses + rules)

    def test_a_section_runs_to_the_end_of_its_own_file(self) -> None:
        texts = ['## 4. F\n\nf\n', '## 3. Lenses\n\nopen\n\n### L1. A\n\na\n\n## Ranking\n\nranked\n',
                 '## 5. S\n\ns\n\n## Tail\n\nt\n']
        lenses, rules = grade_prep.skill_lens_text(texts)
        self.assertEqual(lenses, texts[1])
        self.assertEqual(rules, '## 4. F\n\nf\n## 5. S\n\ns\n\n## Tail\n\nt\n')

    def test_a_section_held_by_two_files_gives_none(self) -> None:
        # Two copies, such as a 0.17 SKILL.md left beside a split one: which one a grader should get is unknown.
        one = '## 3. Lenses\n\n### L1. A\n\na\n\n## 4. F\n\nf\n\n## 5. S\n\ns\n'
        self.assertIsNotNone(grade_prep.skill_lens_text([one]))
        self.assertIsNone(grade_prep.skill_lens_text([one, '## 5. S again\n\ns\n']))

    def test_a_directory_gives_its_markdown_files_and_a_file_gives_itself(self) -> None:
        self.assertEqual(len(grade_prep.skill_texts(SKILL_DIR)[0]), len(PLACES))
        self.assertEqual(grade_prep.skill_texts(SKILL_DIR / 'lenses.md'), ([(SKILL_DIR / 'lenses.md').read_text()], []))
        self.assertEqual(grade_prep.skill_texts(SKILL_DIR / 'absent'), ([], []))

    def test_a_dotfile_or_a_directory_named_like_markdown_is_not_skill_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / 'skill'
            shutil.copytree(SKILL_DIR, copy, ignore=shutil.ignore_patterns('scripts', 'templates'))
            # macOS writes an AppleDouble `._<name>` beside a file on some disks, and it is not UTF-8.
            (copy / '._lenses.md').write_bytes(b'\x00\x05\x16\x07\xff\xfe')
            (copy / 'notes.md').mkdir()
            self.assertEqual(grade_prep.skill_texts(copy), grade_prep.skill_texts(SKILL_DIR))

    def test_a_stray_file_that_is_not_text_is_named_and_the_others_are_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / 'skill'
            shutil.copytree(SKILL_DIR, copy, ignore=shutil.ignore_patterns('scripts', 'templates'))
            (copy / 'README.md').write_bytes('# Notes, caf\xe9\n'.encode('latin-1'))
            texts, unread = grade_prep.skill_texts(copy)
            self.assertEqual(unread, [copy / 'README.md'])
            self.assertEqual(texts, grade_prep.skill_texts(SKILL_DIR)[0])
            self.assertIsNotNone(grade_prep.skill_lens_text(texts))

    def test_a_section_file_that_cannot_be_read_is_named_and_gives_no_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / 'skill'
            shutil.copytree(SKILL_DIR, copy, ignore=shutil.ignore_patterns('scripts', 'templates'))
            (copy / 'scoring.md').write_bytes(b'## 5. Score\n\xff\xfe\n')
            texts, unread = grade_prep.skill_texts(copy)
            self.assertEqual(unread, [copy / 'scoring.md'])
            self.assertEqual(len(texts), len(PLACES) - 1)
            # Section 5 lived there, so skipping the file can never hand a grader sections 3 and 4 alone.
            self.assertIsNone(grade_prep.skill_lens_text(texts))


if __name__ == '__main__':
    unittest.main()
