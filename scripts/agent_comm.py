#!/usr/bin/env python3
"""Agent-to-agent message projection backed by the runtime event ledger."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import uuid
from typing import Any

from event_log import append_event, list_events, now_iso
from file_lock import atomic_json_read, atomic_json_update

_BASE = pathlib.Path(os.environ['EDICT_HOME']) if 'EDICT_HOME' in os.environ else pathlib.Path(__file__).resolve().parent.parent
MESSAGES_FILE = pathlib.Path(os.environ.get('EDICT_AGENT_MESSAGES_FILE', str(_BASE / 'data' / 'agent_messages.json')))

TERMINAL_STATUSES = {'done', 'failed', 'cancelled'}


def _new_message_id() -> str:
    return f'msg_{uuid.uuid4().hex[:12]}'


def _load_messages() -> dict[str, Any]:
    data = atomic_json_read(MESSAGES_FILE, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault('messages', [])
    return data


def _update_message(message_id: str, modifier) -> dict[str, Any] | None:
    found: dict[str, Any] | None = None

    def update(data):
        nonlocal found
        if not isinstance(data, dict):
            data = {'messages': []}
        messages = data.setdefault('messages', [])
        for msg in messages:
            if msg.get('messageId') == message_id:
                modifier(msg)
                msg['updatedAt'] = now_iso()
                found = dict(msg)
                break
        return data

    atomic_json_update(MESSAGES_FILE, update, {'messages': []})
    return found


def send_message(
    task_id: str,
    from_agent: str,
    to_agent: str,
    message: str,
    *,
    message_type: str = 'request',
    priority: str = 'normal',
    evidence: str = '',
) -> dict[str, Any]:
    message_id = _new_message_id()
    at = now_iso()
    msg = {
        'messageId': message_id,
        'taskId': task_id,
        'from': from_agent,
        'to': to_agent,
        'type': message_type,
        'priority': priority,
        'message': message,
        'status': 'sent',
        'createdAt': at,
        'updatedAt': at,
        'evidence': evidence,
    }

    def update(data):
        if not isinstance(data, dict):
            data = {'messages': []}
        data.setdefault('messages', []).append(msg)
        return data

    atomic_json_update(MESSAGES_FILE, update, {'messages': []})
    event = append_event(
        'agent_message_sent',
        task_id=task_id,
        agent_id=to_agent,
        message_id=message_id,
        source=from_agent or 'agent_comm',
        payload=msg,
        evidence={'note': evidence} if evidence else {},
        confidence='high',
        at=at,
    )
    return {'ok': True, 'message': msg, 'event': event}


def mark_message(message_id: str, agent: str, status: str, *, note: str = '', summary: str = '', error: str = '') -> dict[str, Any]:
    if status not in {'ack', 'working', 'done', 'failed', 'cancelled'}:
        raise ValueError(f'unsupported status: {status}')

    event_kind = f'agent_message_{status}'
    projection_status = status
    if status == 'ack':
        projection_status = 'acknowledged'

    def apply(msg):
        msg['status'] = projection_status
        msg['handledBy'] = agent
        if note:
            msg['note'] = note
        if summary:
            msg['summary'] = summary
        if error:
            msg['error'] = error
        if projection_status in TERMINAL_STATUSES:
            msg['completedAt'] = now_iso()

    msg = _update_message(message_id, apply)
    if not msg:
        return {'ok': False, 'error': f'message not found: {message_id}'}

    payload = {
        'from': agent,
        'to': msg.get('from', ''),
        'message': msg.get('message', ''),
        'note': note,
        'summary': summary,
        'error': error,
        'status': projection_status,
    }
    event = append_event(
        event_kind,
        task_id=msg.get('taskId', ''),
        agent_id=agent,
        message_id=message_id,
        source=agent or 'agent_comm',
        payload=payload,
        confidence='high' if not error else 'low',
    )
    return {'ok': True, 'message': msg, 'event': event}


def list_messages(task_id: str = '', agent: str = '', include_events: bool = False) -> dict[str, Any]:
    data = _load_messages()
    messages = data.get('messages', [])
    if task_id:
        messages = [m for m in messages if m.get('taskId') == task_id]
    if agent:
        messages = [m for m in messages if agent in {m.get('from', ''), m.get('to', ''), m.get('handledBy', '')}]
    messages = sorted(messages, key=lambda m: m.get('updatedAt') or m.get('createdAt') or '')
    result = {'ok': True, 'messages': messages}
    if include_events:
        result['events'] = list_events(task_id=task_id, agent_id=agent, limit=200)
    return result


def _print(data: dict[str, Any]) -> int:
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0 if data.get('ok', False) else 1


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Send and track agent-to-agent messages.')
    sub = parser.add_subparsers(dest='cmd', required=True)

    p_send = sub.add_parser('send')
    p_send.add_argument('--task', required=True)
    p_send.add_argument('--from-agent', required=True)
    p_send.add_argument('--to-agent', required=True)
    p_send.add_argument('--type', default='request')
    p_send.add_argument('--priority', default='normal')
    p_send.add_argument('--evidence', default='')
    p_send.add_argument('message')

    for name in ('ack', 'working', 'done', 'failed', 'cancelled'):
        p = sub.add_parser(name)
        p.add_argument('message_id')
        p.add_argument('--agent', required=True)
        p.add_argument('--note', default='')
        p.add_argument('--summary', default='')
        p.add_argument('--error', default='')

    p_list = sub.add_parser('list')
    p_list.add_argument('--task', default='')
    p_list.add_argument('--agent', default='')
    p_list.add_argument('--events', action='store_true')

    args = parser.parse_args(argv)
    if args.cmd == 'send':
        return _print(send_message(
            args.task,
            args.from_agent,
            args.to_agent,
            args.message,
            message_type=args.type,
            priority=args.priority,
            evidence=args.evidence,
        ))
    if args.cmd == 'list':
        return _print(list_messages(args.task, args.agent, args.events))

    return _print(mark_message(
        args.message_id,
        args.agent,
        args.cmd,
        note=args.note,
        summary=args.summary,
        error=args.error,
    ))


if __name__ == '__main__':
    raise SystemExit(_main(sys.argv[1:]))
