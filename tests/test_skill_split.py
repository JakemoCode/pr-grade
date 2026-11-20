#!/usr/bin/env python3
"""Unit tests for the skill's files: a core SKILL.md that every grade loads, and the files beside it that a
coordinator reads at the step that needs one. Each numbered section lives in exactly one file and keeps its number,
since ForgeSmith-OS and agents/grade.md cite sections by number.

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import hashlib
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
# git's blob id for SKILL.md at adbdd3c, the last commit before the split, which held every section in one file.
BEFORE_SPLIT = '4f08d711a45bb300b78fd3b4310e849294947b0a'
# Each sentence the split reworded, as it reads now and as it read before. Section 2 described the one file the prep
# sliced and the cut it made, and both changed with the split and issue #81.
REWORDED = {
    ('section 3 less the lens sections of lenses the grader does not hold, sections 4 and 5 whole, and the lens file '
     'less the lens sections of lenses it does not hold'):
    ('section 3 up to its first lens and each lens the grader holds, sections 4 and 5 whole, and the lens file less '
     'the lens sections of lenses it does not hold'),
    ("The prep slices the skill's files beside its `scripts/` directory, or the copy `--skill <path>` names, a "
     'directory or one file holding sections 3 to 5;'):
    'The prep slices the `SKILL.md` beside its `scripts/` directory, or the copy `--skill <path>` names;',
}
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

    def test_the_split_moved_every_line_and_added_only_the_pointers(self) -> None:
        # A one-time proof for the 0.18.0 split: the core less its pointer paragraphs, with every file's sections put
        # back in number order, is byte for byte the SKILL.md before it. The first change that rewords a moved section
        # deletes this test.
        core = files()['SKILL.md']
        for now, before in REWORDED.items():
            self.assertIn(now, core)
            core = core.replace(now, before)
        pointers = [paragraph for paragraph in core.split('\n\n') if LINK.search(paragraph)
                    and set(LINK.findall(paragraph)) <= set(PLACES)]
        self.assertTrue(pointers)
        for paragraph in pointers:
            core = core.replace(paragraph + '\n\n', '', 1) if paragraph + '\n\n' in core else core.replace(
                '\n\n' + paragraph, '\n', 1)
        sections: dict[str, str] = {}
        intro = ''
        for name, text in files().items():
            head, found = cut(core if name == 'SKILL.md' else text)
            intro = head if name == 'SKILL.md' else intro
            sections.update({number: body.rstrip('\n') + '\n\n' for number, body in found.items()})
        joined = (intro + ''.join(sections[number] for number in sorted(sections, key=int))).rstrip('\n') + '\n'
        data = joined.encode()
        self.assertEqual(hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest(), BEFORE_SPLIT)


class SkillTextTest(unittest.TestCase):
    """grade_prep.py reads sections 3 to 5 from whichever of the skill's files holds each."""

    def test_the_shipped_files_give_every_lens_and_sections_4_and_5(self) -> None:
        lenses, rules = grade_prep.skill_lens_text(grade_prep.skill_texts(SKILL_DIR))
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
        self.assertEqual(len(grade_prep.skill_texts(SKILL_DIR)), len(PLACES))
        self.assertEqual(grade_prep.skill_texts(SKILL_DIR / 'lenses.md'), [(SKILL_DIR / 'lenses.md').read_text()])
        self.assertEqual(grade_prep.skill_texts(SKILL_DIR / 'absent'), [])

    def test_a_dotfile_or_a_directory_named_like_markdown_is_not_skill_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / 'skill'
            shutil.copytree(SKILL_DIR, copy, ignore=shutil.ignore_patterns('scripts', 'templates'))
            # macOS writes an AppleDouble `._<name>` beside a file on some disks, and it is not UTF-8.
            (copy / '._lenses.md').write_bytes(b'\x00\x05\x16\x07\xff\xfe')
            (copy / 'notes.md').mkdir()
            self.assertEqual(grade_prep.skill_texts(copy), grade_prep.skill_texts(SKILL_DIR))

    def test_a_skill_file_that_is_not_text_gives_no_skill_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / 'skill'
            shutil.copytree(SKILL_DIR, copy, ignore=shutil.ignore_patterns('scripts', 'templates'))
            (copy / 'scoring.md').write_bytes(b'## 5. Score\n\xff\xfe\n')
            self.assertEqual(grade_prep.skill_texts(copy), [])


if __name__ == '__main__':
    unittest.main()
