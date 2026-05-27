#!/usr/bin/env python3
"""Append-only runtime event ledger for task and agent activity."""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import pathlib
import uuid
from typing import Any, Iterable

_BASE = pathlib.Path(os.environ['EDICT_HOME']) if 'EDICT_HOME' in os.environ else pathlib.Path(__file__).resolve().parent.parent
EVENTS_DIR = pathlib.Path(os.environ.get('EDICT_EVENTS_DIR', str(_BASE / 'data' / 'events')))

_IS_WINDOWS = os.name == 'nt'
if _IS_WINDOWS:
    import msvcrt
else:
    import fcntl


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _lock_exclusive(fd: int) -> None:
    if _IS_WINDOWS:
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX)


def _unlock(fd: int) -> None:
    if _IS_WINDOWS:
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _event_file(at: str | None = None) -> pathlib.Path:
    stamp = at or now_iso()
    day = stamp[:10].replace('-', '')
    return EVENTS_DIR / f'events-{day}.jsonl'


def _json_default(value: Any) -> str:
    if isinstance(value, pathlib.Path):
        return str(value)
    return str(value)


def append_event(
    kind: str,
    *,
    task_id: str = '',
    agent_id: str = '',
    runtime: str = '',
    session_id: str = '',
    message_id: str = '',
    parent_event_id: str = '',
    source: str = 'system',
    payload: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    confidence: str | None = None,
    at: str | None = None,
) -> dict[str, Any]:
    """Append one event and return the normalized event object."""
    event_at = at or now_iso()
    event = {
        'eventId': f'evt_{uuid.uuid4().hex[:16]}',
        'kind': kind,
        'at': event_at,
        'taskId': task_id or '',
        'agentId': agent_id or '',
        'runtime': runtime or '',
        'sessionId': session_id or '',
        'messageId': message_id or '',
        'parentEventId': parent_event_id or '',
        'source': source or 'system',
        'payload': payload or {},
        'evidence': evidence or {},
    }
    if confidence:
        event['confidence'] = confidence

    path = _event_file(event_at)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + '.lock')
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        _lock_exclusive(fd)
        with path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(event, ensure_ascii=False, default=_json_default) + '\n')
    finally:
        _unlock(fd)
        os.close(fd)
    return event


def _iter_event_files() -> Iterable[pathlib.Path]:
    if not EVENTS_DIR.exists():
        return []
    return sorted(EVENTS_DIR.glob('events-*.jsonl'))


def _matches_agent(event: dict[str, Any], agent_id: str) -> bool:
    if not agent_id:
        return True
    payload = event.get('payload') or {}
    return agent_id in {
        event.get('agentId', ''),
        payload.get('from', ''),
        payload.get('to', ''),
        payload.get('agent', ''),
        payload.get('requestedBy', ''),
    }


def list_events(
    *,
    task_id: str = '',
    agent_id: str = '',
    message_id: str = '',
    since: str = '',
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Read events in chronological order, filtered by common fields."""
    events: list[dict[str, Any]] = []
    for path in _iter_event_files():
        try:
            lines = path.read_text(encoding='utf-8').splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if task_id and event.get('taskId') != task_id:
                continue
            if message_id and event.get('messageId') != message_id:
                continue
            if since and event.get('at', '') < since:
                continue
            if agent_id and not _matches_agent(event, agent_id):
                continue
            events.append(event)

    events.sort(key=lambda e: e.get('at', ''))
    if limit and len(events) > limit:
        events = events[-limit:]
    return events


def event_to_activity_entries(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Map ledger events to the dashboard's existing activity-entry shapes."""
    kind = event.get('kind', '')
    payload = event.get('payload') or {}
    at = event.get('at', '')
    agent = event.get('agentId') or payload.get('agent') or payload.get('to') or ''
    base = {
        'at': at,
        'agent': agent,
        'eventId': event.get('eventId', ''),
        'eventKind': kind,
        'source': 'event-ledger',
        'confidence': event.get('confidence', ''),
    }

    if kind == 'progress_reported':
        entries = []
        text = payload.get('text') or payload.get('summary') or ''
        if text:
            entry = {**base, 'kind': 'progress', 'text': text}
            for field in ('tokens', 'cost', 'elapsed', 'state', 'org'):
                if field in payload:
                    entry[field] = payload[field]
            entries.append(entry)
        todos = payload.get('todos') or []
        if todos:
            entries.append({**base, 'kind': 'todos', 'items': todos, 'state': payload.get('state', ''), 'org': payload.get('org', '')})
        return entries

    if kind in {'tool_call_started', 'tool_call_finished'}:
        tool_name = payload.get('tool') or payload.get('name') or 'tool'
        if kind == 'tool_call_started':
            return [{**base, 'kind': 'assistant', 'tools': [{'name': tool_name, 'input_preview': payload.get('inputPreview', '')}]}]
        return [{
            **base,
            'kind': 'tool_result',
            'tool': tool_name,
            'output': payload.get('outputPreview', '') or payload.get('summary', ''),
            'exitCode': payload.get('exitCode'),
        }]

    if kind.startswith('dispatch_'):
        status = payload.get('status') or kind.removeprefix('dispatch_')
        remark = payload.get('remark') or f'Dispatch {status}'
        if payload.get('error'):
            remark = f'{remark}: {payload.get("error")}'
        return [{
            **base,
            'kind': 'flow',
            'from': payload.get('from') or event.get('runtime') or 'scheduler',
            'to': payload.get('to') or event.get('agentId') or '',
            'remark': remark,
        }]

    if kind.startswith('agent_message_'):
        action = kind.removeprefix('agent_message_')
        text = payload.get('message') or payload.get('summary') or payload.get('note') or payload.get('error') or action
        return [{
            **base,
            'kind': 'flow',
            'from': payload.get('from') or event.get('agentId') or '',
            'to': payload.get('to') or '',
            'remark': f'{action}: {text}',
        }]

    if kind in {'task_created', 'state_changed', 'state_rejected', 'pending_confirm', 'confirm_applied', 'task_done', 'task_blocked', 'flow_recorded'}:
        return [{
            **base,
            'kind': 'flow',
            'from': payload.get('from') or payload.get('oldState') or ('皇上' if kind == 'task_created' else ''),
            'to': payload.get('to') or payload.get('newState') or '',
            'remark': payload.get('remark') or payload.get('reason') or payload.get('summary') or kind,
        }]

    if kind in {'todo_updated', 'todo_rejected'}:
        text = payload.get('text') or f'todo {payload.get("todoId", "")} -> {payload.get("status", "")}'.strip()
        if payload.get('title'):
            text = f'{text}: {payload.get("title")}'
        return [{**base, 'kind': 'progress', 'text': text}]

    text = payload.get('text') or payload.get('summary') or payload.get('remark') or kind
    return [{**base, 'kind': 'progress', 'text': text}]


def event_to_activity(event: dict[str, Any]) -> dict[str, Any] | None:
    entries = event_to_activity_entries(event)
    return entries[0] if entries else None


def _main() -> int:
    parser = argparse.ArgumentParser(description='Inspect the runtime event ledger.')
    parser.add_argument('--task', default='')
    parser.add_argument('--agent', default='')
    parser.add_argument('--message', default='')
    parser.add_argument('--limit', type=int, default=50)
    args = parser.parse_args()
    print(json.dumps(
        list_events(task_id=args.task, agent_id=args.agent, message_id=args.message, limit=args.limit),
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == '__main__':
    raise SystemExit(_main())
