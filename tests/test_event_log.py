import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'dashboard'))


def test_event_log_append_list_and_activity(monkeypatch, tmp_path):
    import event_log

    monkeypatch.setattr(event_log, 'EVENTS_DIR', tmp_path / 'events')

    event = event_log.append_event(
        'progress_reported',
        task_id='T-100',
        agent_id='gongbu',
        payload={
            'text': '正在执行集成测试',
            'todos': [{'id': '1', 'title': '跑测试', 'status': 'in-progress'}],
            'state': 'Doing',
            'org': '工部',
        },
        at='2026-05-27T01:02:03Z',
    )

    events = event_log.list_events(task_id='T-100')
    assert [e['eventId'] for e in events] == [event['eventId']]

    activity = event_log.event_to_activity_entries(events[0])
    assert activity[0]['kind'] == 'progress'
    assert activity[0]['text'] == '正在执行集成测试'
    assert activity[1]['kind'] == 'todos'
    assert activity[1]['items'][0]['title'] == '跑测试'


def test_agent_comm_message_lifecycle(monkeypatch, tmp_path):
    import agent_comm
    import event_log

    monkeypatch.setattr(event_log, 'EVENTS_DIR', tmp_path / 'events')
    monkeypatch.setattr(agent_comm, 'MESSAGES_FILE', tmp_path / 'agent_messages.json')

    sent = agent_comm.send_message(
        'T-200',
        'shangshu',
        'gongbu',
        '请核对 Windows 启动脚本',
        message_type='delegate',
    )
    message_id = sent['message']['messageId']
    assert sent['ok'] is True

    ack = agent_comm.mark_message(message_id, 'gongbu', 'ack', note='收到')
    done = agent_comm.mark_message(message_id, 'gongbu', 'done', summary='脚本已核对')
    assert ack['message']['status'] == 'acknowledged'
    assert done['message']['status'] == 'done'

    data = json.loads((tmp_path / 'agent_messages.json').read_text(encoding='utf-8'))
    assert data['messages'][0]['completedAt']

    events = event_log.list_events(task_id='T-200')
    assert [e['kind'] for e in events] == [
        'agent_message_sent',
        'agent_message_ack',
        'agent_message_done',
    ]


def test_task_activity_merges_event_ledger(monkeypatch, tmp_path):
    import event_log
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    task = {
        'id': 'T-300',
        'title': '运行证据测试',
        'state': 'Doing',
        'org': '工部',
        'now': '等待事件',
        'updatedAt': '2026-05-27T01:00:00Z',
        'flow_log': [],
        'progress_log': [],
    }
    (data_dir / 'tasks_source.json').write_text(json.dumps([task], ensure_ascii=False), encoding='utf-8')

    event = {
        'eventId': 'evt_test',
        'kind': 'dispatch_started',
        'at': '2026-05-27T01:05:00Z',
        'taskId': 'T-300',
        'agentId': 'gongbu',
        'runtime': 'opencode',
        'payload': {
            'from': 'OpenCode',
            'to': 'gongbu',
            'status': 'started',
            'remark': '开始派发: gongbu',
        },
        'confidence': 'high',
    }

    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)
    monkeypatch.setattr(srv, '_ledger_list_events', lambda task_id='', limit=200: [event])
    monkeypatch.setattr(srv, '_ledger_event_to_activity_entries', event_log.event_to_activity_entries)
    monkeypatch.setattr(srv, 'get_agent_activity', lambda *args, **kwargs: [])

    result = srv.get_task_activity('T-300')
    assert result['ok'] is True
    assert result['activitySource'] == 'progress+session+event-ledger'
    assert result['stateEvidence']['eventCount'] == 1
    assert any(a.get('eventId') == 'evt_test' for a in result['activity'])


def test_opencode_storage_activity_parser(monkeypatch, tmp_path):
    import server as srv

    storage = tmp_path / 'opencode' / 'storage'
    (storage / 'session' / 'global').mkdir(parents=True)
    (storage / 'message' / 'ses_task').mkdir(parents=True)
    (storage / 'part' / 'msg_assistant').mkdir(parents=True)

    session = {
        'id': 'ses_task',
        'title': 'T-400 OpenCode runtime trace',
        'directory': str(ROOT),
        'time': {'created': 1769835000000, 'updated': 1769835003000},
    }
    message = {
        'id': 'msg_assistant',
        'sessionID': 'ses_task',
        'role': 'assistant',
        'agent': 'gongbu',
        'time': {'created': 1769835001000},
    }
    text_part = {
        'id': 'prt_text',
        'messageID': 'msg_assistant',
        'type': 'text',
        'text': '我正在检查 Windows 启动路径。',
        'time': {'start': 1769835001000},
    }
    tool_part = {
        'id': 'prt_tool',
        'messageID': 'msg_assistant',
        'type': 'tool',
        'tool': 'bash',
        'state': {
            'status': 'completed',
            'input': {'command': 'npm run build', 'description': 'Build frontend'},
            'output': 'built',
            'metadata': {'exit': 0},
            'time': {'start': 1769835002000, 'end': 1769835003000},
        },
    }

    for path, data in [
        (storage / 'session' / 'global' / 'ses_task.json', session),
        (storage / 'message' / 'ses_task' / 'msg_assistant.json', message),
        (storage / 'part' / 'msg_assistant' / 'prt_text.json', text_part),
        (storage / 'part' / 'msg_assistant' / 'prt_tool.json', tool_part),
    ]:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    monkeypatch.setattr(srv, 'OPENCODE_HOME', tmp_path / 'opencode')
    monkeypatch.setattr(srv, '_agent_runtime', lambda: 'opencode')

    activity = srv.get_agent_activity('gongbu', task_id='T-400', limit=10)

    assert any(a['kind'] == 'assistant' and 'Windows' in a.get('text', '') for a in activity)
    assert any(a['kind'] == 'assistant' and a.get('tools', [{}])[0].get('name') == 'bash' for a in activity if a.get('tools'))
    assert any(a['kind'] == 'tool_result' and a.get('output') == 'built' for a in activity)
