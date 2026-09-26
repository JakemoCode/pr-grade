#!/usr/bin/env python3
"""Unit tests for check_then_act.py's core, on hand-built syntax facts: no Node needed.

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'skills/pr-grade/scripts'))
import check_then_act as cta  # noqa: E402

CFG = dict(cta.DEFAULTS)


def fn(**facts) -> dict:
    base = {'name': 'f', 'span': [[1, 0], [30, 1]], 'isConstructor': False, 'inlineCallee': None, 'calls': [],
            'awaits': [], 'assigns': [], 'conds': [], 'memberWrites': [], 'scopes': []}
    base.update(facts)
    return base


def call(id: int, callee: str, line: int, *, start: int = 4, end: int = 40, **extra) -> dict:
    return {'id': id, 'callee': callee, 'span': [[line, start], [line, end]], 'line': line, 'text': callee,
            'awaited': False, 'bare': '.' not in callee, 'inHandler': False, 'inline': False, 'chainedReceiver': False,
            'sql': None, 'scopeIds': [], **extra}


def assign(name: str, line: int, call_id: int) -> dict:
    return {'names': [name], 'span': [[line, 2], [line, 40]], 'line': line,
            'refs': [{'t': 'call', 'id': call_id}], 'collection': False, 'inline': False}


def guard(line: int, name: str) -> dict:
    return {'kind': 'if', 'span': [[line, 2], [line, 40]], 'condSpan': [[line, 6], [line, 20]], 'line': line,
            'guard': True, 'inline': False, 'inHandler': False, 'refs': [{'t': 'name', 'name': name, 'via': []}],
            'text': f'if ({name}) return'}


def waited(line: int, *, start: int = 4, end: int = 40, reach_end=None) -> dict:
    return {'span': [[line, start], [line, end]], 'line': line, 'kind': 'await', 'reachEnd': reach_end, 'text': 'await'}


def window(**overrides) -> dict:
    """read at 2 into x, guard on x at 3, await at 4, write at 5: one candidate."""
    facts = dict(calls=[call(0, 'repo.get', 2), call(1, 'repo.update', 5)], assigns=[assign('x', 2, 0)],
                 conds=[guard(3, 'x')], awaits=[waited(4)])
    facts.update(overrides)
    return fn(**facts)


class AnalyzeTest(unittest.TestCase):
    def test_a_read_then_await_then_write_is_a_candidate(self) -> None:
        [candidate] = cta.analyze(window(), CFG)
        self.assertEqual(([c['line'] for c in candidate['checks']], [(g['line'], g['kind']) for g in candidate['gaps']],
                          [w['line'] for w in candidate['writes']]), ([3], [(4, 'await')], [5]))

    def test_the_read_behind_the_check_is_reported_with_its_store(self) -> None:
        [candidate] = cta.analyze(window(), CFG)
        self.assertEqual(candidate['checks'][0]['reads'], [{'line': 2, 'callee': 'repo.get'}])
        self.assertEqual(candidate['checks'][0]['sameStoreWrites'], [5])

    def test_an_await_that_wraps_the_write_is_no_gap(self) -> None:
        # await repo.update(...) awaits the write itself.
        self.assertEqual(cta.analyze(window(awaits=[waited(5, start=2, end=60)]), CFG), [])

    def test_an_await_in_a_block_that_always_exits_cannot_reach_the_write(self) -> None:
        self.assertEqual(cta.analyze(window(awaits=[waited(4, reach_end=[4, 60])]), CFG), [])

    def test_awaits_inside_a_lock_callback_do_not_count(self) -> None:
        self.assertEqual(cta.analyze(window(inlineCallee='mutex.runExclusive'), CFG), [])

    def test_a_write_in_a_catch_block_is_not_the_guarded_write(self) -> None:
        facts = window()
        facts['calls'][1]['inHandler'] = True
        self.assertEqual(cta.analyze(facts, CFG), [])

    def test_a_write_to_a_local_collection_is_not_shared_state(self) -> None:
        local = {'names': ['out'], 'span': [[1, 2], [1, 20]], 'line': 1, 'refs': [], 'collection': True, 'inline': False}
        facts = window(calls=[call(0, 'repo.get', 2), call(1, 'out.push', 5)])
        facts['assigns'].append(local)
        self.assertEqual(cta.analyze(facts, CFG), [])

    def test_a_recheck_after_the_await_clears_the_window(self) -> None:
        facts = window(calls=[call(0, 'repo.get', 2), call(2, 'repo.get', 6), call(1, 'repo.update', 8)],
                       assigns=[assign('x', 2, 0), assign('y', 6, 2)], conds=[guard(3, 'x'), guard(7, 'y')])
        self.assertEqual(cta.analyze(facts, CFG), [])

    def test_a_constructor_writes_nothing_shared(self) -> None:
        self.assertEqual(cta.analyze(window(isConstructor=True), CFG), [])


class WordMatchTest(unittest.TestCase):
    def test_a_word_matches_a_method_that_starts_with_it_at_a_boundary(self) -> None:
        for callee, entry, expected in (('store.setStatus', 'set', True), ('store.set_status', 'set', True),
                                        ('store.set', 'set', True), ('store.settle', 'set', False),
                                        ('store.setdefault', 'set', False)):
            with self.subTest(callee=callee, entry=entry):
                self.assertEqual(cta.word_match(callee, entry), expected)

    def test_a_glob_matches_the_method_and_a_dotted_entry_the_whole_callee(self) -> None:
        self.assertTrue(cta.word_match('self.write_lock', '*_lock'))
        self.assertTrue(cta.word_match('requests.post', 'requests.*'))
        self.assertFalse(cta.word_match('client.requests.post', 'requests.post'))


class ConfigTest(unittest.TestCase):
    def test_a_set_key_replaces_its_default_list(self) -> None:
        cfg = cta.cta_config({'checkThenAct': {'secondReads': ['callBilling']}})
        self.assertEqual((cfg['secondReads'], cfg['writes']), (['callBilling'], cta.DEFAULTS['writes']))

    def test_an_unknown_key_stops_the_run(self) -> None:
        with self.assertRaises(SystemExit) as stopped:
            cta.cta_config({'checkThenAct': {'secondRead': ['x']}})
        self.assertIn('secondRead', str(stopped.exception.code))

    def test_a_path_counted_as_code_is_scanned(self) -> None:
        config = cta.load_config(Path('.'), '{"countAsCode": ["docs/tool.py"]}')
        self.assertEqual([cta.is_code(p, config) for p in ('docs/tool.py', 'docs/other.py')], [True, False])

    def test_a_config_without_count_as_code_still_reads(self) -> None:
        config = {k: v for k, v in cta.load_config(Path('.'), '{}').items() if k != 'countAsCode'}
        self.assertTrue(cta.is_code('src/a.py', config))

    def test_a_value_that_is_not_a_list_of_strings_stops_the_run(self) -> None:
        with self.assertRaises(SystemExit):
            cta.cta_config({'checkThenAct': {'writes': 'append'}})


if __name__ == '__main__':
    unittest.main()


class RenderTest(unittest.TestCase):
    def result(self, scope: str, in_diff: list[str]) -> dict:
        candidate = {'file': 'store.py', 'span': [10, 30], 'function': 'Store.claim', 'inDiff': in_diff,
                     'checks': [{'line': 12, 'text': 'if row.free:', 'reads': [], 'sameStoreWrites': []}],
                     'gaps': [{'line': 14, 'kind': 'await', 'text': 'await slow()'}],
                     'writes': [{'line': 16, 'callee': 'store.claim'}]}
        return {'scope': scope, 'scanned': {'functions': 1}, 'skipped': [], 'candidates': [candidate]}

    def test_a_window_the_diff_left_alone_is_marked(self) -> None:
        self.assertIn('Store.claim  (check, gap, and writes unchanged by this diff)', cta.render(self.result('diff', [])))

    def test_a_window_the_diff_touched_is_not_marked(self) -> None:
        self.assertNotIn('unchanged by this diff', cta.render(self.result('diff', ['gap'])))

    def test_a_whole_file_scan_marks_nothing(self) -> None:
        # --whole lists functions the branch never touched on purpose; the mark is for the diff scope.
        self.assertNotIn('unchanged by this diff', cta.render(self.result('whole', [])))
