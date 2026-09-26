#!/usr/bin/env python3
"""Per-subagent turns and tokens from a Claude Code session transcript, to measure a grading run.

    python3 tools/tally_run.py <session.jsonl> [--agent-type pr-grade:grade] [--json]

Pass the session's own transcript, `~/.claude/projects/<project>/<session id>.jsonl`; its subagents are
read from the `<session id>/subagents/` directory beside it. This is a maintainer's tool: it reads Claude
Code's transcript files, whose shape is not a published interface, and it ships nowhere.

A turn is one assistant message, however many tool calls it holds, so parallel calls count once. The
transcript splits one message across several records by content block, and repeats its usage on each;
this counts each message id once, with its last usage. The transcript records output tokens as the
stream began, so they are not reported: model seconds, the time from each prompt or tool result to the
message that answers it, stands in for output and thinking. `capped` is read from the session's own
notice that the subagent stopped at its turn limit.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

COLUMNS = ('turns', 'toolCalls', 'parallelTurns', 'cacheRead', 'input', 'contextAtEnd', 'modelSeconds')


def when(record: dict) -> datetime:
    return datetime.fromisoformat(record['timestamp'].replace('Z', '+00:00'))


def tally(transcript: Path) -> dict:
    """One subagent's numbers from its transcript."""
    usage: dict[str, dict] = {}
    calls: dict[str, set] = {}
    seconds, previous = 0.0, None
    for line in transcript.read_text().splitlines():
        record = json.loads(line)
        kind = record.get('type')
        if kind == 'assistant':
            message = record['message']
            if message['id'] not in usage and previous is not None:
                seconds += (when(record) - when(previous)).total_seconds()
            usage[message['id']] = message.get('usage') or {}
            calls.setdefault(message['id'], set()).update(
                block['id'] for block in message['content'] if block.get('type') == 'tool_use')
        if kind in ('assistant', 'user'):
            previous = record if kind == 'user' else None
    last = list(usage.values())[-1] if usage else {}
    return {
        'turns': len(usage),
        'toolCalls': sum(len(ids) for ids in calls.values()),
        'parallelTurns': sum(1 for ids in calls.values() if len(ids) > 1),
        'cacheRead': sum(u.get('cache_read_input_tokens') or 0 for u in usage.values()),
        'input': sum((u.get('input_tokens') or 0) + (u.get('cache_creation_input_tokens') or 0) for u in usage.values()),
        'contextAtEnd': sum(last.get(k) or 0 for k in ('input_tokens', 'cache_creation_input_tokens',
                                                        'cache_read_input_tokens')),
        'modelSeconds': round(seconds),
    }


def run(session: Path, agent_type: str | None) -> list[dict]:
    notices = session.read_text() if session.is_file() else ''
    rows = []
    for transcript in sorted((session.with_suffix('') / 'subagents').glob('agent-*.jsonl')):
        agent = transcript.stem[len('agent-'):]
        meta_path = transcript.with_suffix('.meta.json')
        meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
        if agent_type and meta.get('agentType') != agent_type:
            continue
        notice = next((part for part in notices.split('<task-notification>') if f'<task-id>{agent}</task-id>' in part), '')
        rows.append({'agent': agent, 'type': meta.get('agentType', '?'), 'description': meta.get('description', ''),
                     **tally(transcript), 'capped': 'turn limit' in notice})
    return rows


def render(rows: list[dict]) -> str:
    head = f"{'description':32} {'type':18} " + ' '.join(f'{c:>13}' for c in COLUMNS) + '  capped'
    lines = [head]
    for row in rows:
        lines.append(f"{row['description'][:32]:32} {row['type'][:18]:18} "
                     + ' '.join(f'{row[c]:>13,}' for c in COLUMNS) + ('  yes' if row['capped'] else ''))
    if rows:
        lines.append(f"{'total':32} {'':18} " + ' '.join(f'{sum(r[c] for r in rows):>13,}' for c in COLUMNS)
                     + f"  {sum(r['capped'] for r in rows)} of {len(rows)}")
    return '\n'.join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('session', type=Path)
    ap.add_argument('--agent-type', help='only subagents of this type, for example pr-grade:grade')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    if not (args.session.with_suffix('') / 'subagents').is_dir():
        sys.exit(f"{args.session}: no subagents directory beside it at {args.session.with_suffix('') / 'subagents'}")
    rows = run(args.session, args.agent_type)
    print(json.dumps(rows, indent=1) if args.json else render(rows))


if __name__ == '__main__':
    main()
