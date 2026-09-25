#!/usr/bin/env python3
"""The CI template users copy pins each action to the same commit as this repository's own workflow.

Dependabot bumps only .github/workflows/, so without this a bump would leave the template behind.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / '.github/workflows/test.yml'
WORKFLOWS = sorted((REPO / '.github/workflows').glob('*.yml'))
TEMPLATE = REPO / 'skills/pr-grade/templates/pr-grade-check.yml'
USES = re.compile(r'uses:\s*([\w.-]+/[\w.-]+)@(\S+)')


def pins(path: Path) -> dict[str, str]:
    return dict(USES.findall(path.read_text()))


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


if __name__ == '__main__':
    unittest.main()
