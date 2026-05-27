"""Tests for dashboard auto-dispatch error handling."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'dashboard'))
sys.path.insert(0, str(ROOT / 'scripts'))


def test_dispatch_records_missing_openclaw_cli(monkeypatch, tmp_path):
    """Missing OpenClaw CLI should become an actionable dispatch status."""
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    task_id = 'JJC-20260415-004'
    task = {
        'id': task_id,
        'title': '小任务',
        'state': 'Taizi',
        'org': '太子',
        'updatedAt': '2026-04-15T15:34:16Z',
    }
    tasks_path = data_dir / 'tasks_source.json'
    tasks_path.write_text(json.dumps([task], ensure_ascii=False), encoding='utf-8')
    (data_dir / 'agent_config.json').write_text('{}', encoding='utf-8')

    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)
    monkeypatch.setattr(srv, '_check_gateway_alive', lambda: True)
    monkeypatch.setattr(srv, '_resolve_openclaw_bin', lambda: None)
    monkeypatch.setattr(
        srv,
        'save_tasks',
        lambda tasks: tasks_path.write_text(
            json.dumps(tasks, ensure_ascii=False),
            encoding='utf-8',
        ),
    )

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self.target = target

        def start(self):
            if self.target:
                self.target()

    monkeypatch.setattr(srv.threading, 'Thread', ImmediateThread)

    srv.dispatch_for_state(task_id, task, 'Taizi', trigger='test')

    updated = json.loads(tasks_path.read_text(encoding='utf-8'))[0]
    sched = updated['_scheduler']
    assert sched['lastDispatchStatus'] == 'openclaw-missing'
    assert 'OpenClaw CLI 未找到' in sched['lastDispatchError']
    assert '[WinError 2]' not in sched['lastDispatchError']
    assert any('OpenClaw CLI 未找到' in item['remark'] for item in updated['flow_log'])


def test_dispatch_records_missing_opencode_cli(monkeypatch, tmp_path):
    """OpenCode mode should report a missing opencode CLI distinctly."""
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    task_id = 'JJC-20260526-001'
    task = {
        'id': task_id,
        'title': '切换 OpenCode',
        'state': 'Taizi',
        'org': '太子',
        'updatedAt': '2026-05-26T15:34:16Z',
    }
    tasks_path = data_dir / 'tasks_source.json'
    tasks_path.write_text(json.dumps([task], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setenv('EDICT_RUNTIME', 'opencode')
    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)
    monkeypatch.setattr(srv, '_check_gateway_alive', lambda: True)
    monkeypatch.setattr(srv, '_resolve_opencode_bin', lambda: None)
    monkeypatch.setattr(
        srv,
        'save_tasks',
        lambda tasks: tasks_path.write_text(
            json.dumps(tasks, ensure_ascii=False),
            encoding='utf-8',
        ),
    )

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self.target = target

        def start(self):
            if self.target:
                self.target()

    monkeypatch.setattr(srv.threading, 'Thread', ImmediateThread)

    srv.dispatch_for_state(task_id, task, 'Taizi', trigger='test')

    updated = json.loads(tasks_path.read_text(encoding='utf-8'))[0]
    sched = updated['_scheduler']
    assert sched['lastDispatchStatus'] == 'opencode-missing'
    assert 'OpenCode CLI 未找到' in sched['lastDispatchError']
    assert any('OpenCode CLI 未找到' in item['remark'] for item in updated['flow_log'])


def test_dispatch_uses_opencode_run_attach(monkeypatch, tmp_path):
    """OpenCode mode should dispatch through `opencode run --attach --dir --agent`."""
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    task_id = 'JJC-20260526-002'
    task = {
        'id': task_id,
        'title': '启动 OpenCode 适配',
        'state': 'Taizi',
        'org': '太子',
        'updatedAt': '2026-05-26T16:00:00Z',
    }
    tasks_path = data_dir / 'tasks_source.json'
    tasks_path.write_text(json.dumps([task], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setenv('EDICT_RUNTIME', 'opencode')
    monkeypatch.setenv('OPENCODE_SERVER_URL', 'http://127.0.0.1:4096')
    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)
    monkeypatch.setattr(srv, '_check_gateway_alive', lambda: True)
    monkeypatch.setattr(srv, '_resolve_opencode_bin', lambda: '/usr/local/bin/opencode')
    monkeypatch.setattr(
        srv,
        'save_tasks',
        lambda tasks: tasks_path.write_text(
            json.dumps(tasks, ensure_ascii=False),
            encoding='utf-8',
        ),
    )

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self.target = target

        def start(self):
            if self.target:
                self.target()

    class Completed:
        returncode = 0
        stdout = ''
        stderr = ''

    captured = {'cmds': []}

    def fake_run(cmd, **kwargs):
        captured['cmds'].append(cmd)
        return Completed()

    monkeypatch.setattr(srv.threading, 'Thread', ImmediateThread)
    monkeypatch.setattr(srv.subprocess, 'run', fake_run)

    srv.dispatch_for_state(task_id, task, 'Taizi', trigger='test')

    opencode_cmd = next(cmd for cmd in captured['cmds'] if cmd[:2] == ['/usr/local/bin/opencode', 'run'])
    assert opencode_cmd[opencode_cmd.index('--attach') + 1] == 'http://127.0.0.1:4096'
    assert opencode_cmd[opencode_cmd.index('--dir') + 1] == str(ROOT)
    assert opencode_cmd[opencode_cmd.index('--agent') + 1] == 'taizi'

    updated = json.loads(tasks_path.read_text(encoding='utf-8'))[0]
    assert updated['_scheduler']['lastDispatchStatus'] == 'success'


def test_opencode_agents_are_idle_without_recent_session(monkeypatch):
    """OpenCode server availability should not make every agent look busy."""
    import server as srv

    monkeypatch.setenv('EDICT_RUNTIME', 'opencode')
    monkeypatch.setattr(srv, '_check_gateway_alive', lambda: True)
    monkeypatch.setattr(srv, '_check_gateway_probe', lambda: True)
    monkeypatch.setattr(srv, '_opencode_agent_names', lambda: {d['id'] for d in srv._AGENT_DEPTS})
    monkeypatch.setattr(srv, '_opencode_config_has_agent', lambda agent_id: True)
    monkeypatch.setattr(srv, '_get_opencode_agent_session_status', lambda agent_id: (0, 0, False))

    data = srv.get_agents_status()

    assert data['gateway']['runtime'] == 'opencode'
    assert data['gateway']['label'] == 'OpenCode'
    taizi = next(a for a in data['agents'] if a['id'] == 'taizi')
    assert taizi['status'] == 'idle'
    assert taizi['statusLabel'] == '🟡 待命'
    assert taizi['processAlive'] is False


def test_opencode_recent_session_marks_agent_running(monkeypatch):
    """Recent OpenCode session activity should still surface as running."""
    import server as srv

    now_ms = int(srv.datetime.datetime.now().timestamp() * 1000)
    monkeypatch.setenv('EDICT_RUNTIME', 'opencode')
    monkeypatch.setattr(srv, '_check_gateway_alive', lambda: True)
    monkeypatch.setattr(srv, '_check_gateway_probe', lambda: True)
    monkeypatch.setattr(srv, '_opencode_agent_names', lambda: {d['id'] for d in srv._AGENT_DEPTS})
    monkeypatch.setattr(srv, '_opencode_config_has_agent', lambda agent_id: True)
    monkeypatch.setattr(
        srv,
        '_get_opencode_agent_session_status',
        lambda agent_id: (now_ms, 1, agent_id == 'taizi'),
    )

    data = srv.get_agents_status()
    taizi = next(a for a in data['agents'] if a['id'] == 'taizi')
    zhongshu = next(a for a in data['agents'] if a['id'] == 'zhongshu')

    assert taizi['status'] == 'running'
    assert taizi['statusLabel'] == '🟢 运行中'
    assert zhongshu['status'] == 'idle'
