#!/usr/bin/env python3
"""Every workflow and the CI template pin actions by full commit SHA, the template to the same commits as
this repository's own workflow, and pr-grade.yml stays the template with its own header comment.

Dependabot bumps only .github/workflows/, so without this a bump would leave the template behind.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / '.github/workflows/test.yml'
WORKFLOWS = sorted(p for p in (REPO / '.github/workflows').iterdir() if p.suffix in ('.yml', '.yaml'))
DOGFOOD = REPO / '.github/workflows/pr-grade.yml'
TEMPLATE = REPO / 'skills/pr-grade/templates/pr-grade-check.yml'
USES = re.compile(r'uses:\s*([\w.-]+/[\w.-]+)@(\S+)')


def pins(path: Path) -> dict[str, str]:
    return dict(USES.findall(path.read_text()))


def without_header(path: Path) -> list[str]:
    """The file after its leading comment block."""
    lines = path.read_text().splitlines()
    return lines[next(i for i, line in enumerate(lines) if not line.startswith('#')):]


class PinTest(unittest.TestCase):
    def test_every_action_is_pinned_to_a_full_commit(self) -> None:
        for path in (*WORKFLOWS, TEMPLATE):
            for action, ref in pins(path).items():
                with self.subTest(file=path.name, action=action):
                    self.assertRegex(ref, r'^[0-9a-f]{40}$')

    def test_the_template_pins_what_the_workflow_pins(self) -> None:
        ours, template = pins(WORKFLOW), pins(TEMPLATE)
        for action, ref in template.items():
            with self.subTest(action=action):
                self.assertEqual(ref, ours.get(action), f'{TEMPLATE.name} pins {action} apart from {WORKFLOW.name}')

    def test_the_dogfood_workflow_is_the_template(self) -> None:
        self.assertEqual(without_header(DOGFOOD), without_header(TEMPLATE),
                         f'{DOGFOOD.name} runs something other than {TEMPLATE.name}')


if __name__ == '__main__':
    unittest.main()
