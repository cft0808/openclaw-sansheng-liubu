"""Scheduler commit gate tests for dashboard/server.py."""

import json
import pathlib
import sys

# Add project paths
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'dashboard'))
sys.path.insert(0, str(ROOT / 'scripts'))

import server as srv


def _write_tasks(data_dir, tasks):
    (data_dir / 'tasks_source.json').write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )


def _base_task():
    now = srv.now_iso()
    return {
        'id': 'JJC-TEST-SCHED-001',
        'title': '测试提交闸门',
        'state': 'Doing',
        'org': '兵部',
        'now': '执行中',
        'block': '无',
        'flow_log': [],
        'progress_log': [],
        '_scheduler': {
            'stateVersion': 5,
            'controlState': 'Doing',
            'lease': {
                'stage': 'Doing',
                'role': 'bingbu',
                'ownerRunId': 'run-owner-a',
                'acquiredAt': now,
                'heartbeatAt': now,
                'ttlSec': 86400,
            },
            'writeback': {
                'status': 'idle',
                'retryCount': 0,
                'maxRetry': 2,
            },
        },
    }


def test_scheduler_commit_blocks_stale_owner(tmp_path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    srv.DATA = data_dir

    task = _base_task()
    _write_tasks(data_dir, [task])

    result = srv.handle_scheduler_commit({
        'taskId': task['id'],
        'action': 'retry',
        'ownerRunId': 'run-owner-b',
        'expectedVersion': 5,
        'reasonCode': 'test_retry',
    })

    assert result['ok'] is False
    assert result['committed'] is False
    assert result['blockedBy'] == 'staleOwner'

    tasks = srv.load_tasks()
    latest = next(t for t in tasks if t.get('id') == task['id'])
    assert latest.get('_scheduler', {}).get('stateVersion') == 5


def test_scheduler_commit_blocks_version_conflict(tmp_path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    srv.DATA = data_dir

    task = _base_task()
    _write_tasks(data_dir, [task])

    result = srv.handle_scheduler_commit({
        'taskId': task['id'],
        'action': 'retry',
        'ownerRunId': 'run-owner-a',
        'expectedVersion': 3,
        'reasonCode': 'test_retry',
    })

    assert result['ok'] is False
    assert result['committed'] is False
    assert result['blockedBy'] == 'versionConflict'

    tasks = srv.load_tasks()
    latest = next(t for t in tasks if t.get('id') == task['id'])
    assert latest.get('_scheduler', {}).get('stateVersion') == 5


def test_action_allowlist_blocks_escalate_in_doing(tmp_path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    srv.DATA = data_dir

    task = _base_task()
    task['_scheduler']['lease']['ownerRunId'] = ''
    _write_tasks(data_dir, [task])

    result = srv.handle_scheduler_action(task['id'], 'escalate', '测试禁止')

    assert result['ok'] is False
    assert '被拒绝' in (result.get('error') or '')


def test_action_allowlist_blocks_escalate_in_waiting_decision(tmp_path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    srv.DATA = data_dir

    task = _base_task()
    task['state'] = 'Menxia'
    task['org'] = '门下省'
    task['_scheduler']['controlState'] = 'WaitingDecision'
    task['_scheduler']['lease']['ownerRunId'] = ''
    _write_tasks(data_dir, [task])

    result = srv.handle_scheduler_action(task['id'], 'escalate', '等待裁决时不允许升级')

    assert result['ok'] is False
    assert '被拒绝' in (result.get('error') or '')


def test_scheduler_scan_single_action_per_tick(tmp_path, monkeypatch):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    srv.DATA = data_dir

    task = _base_task()
    task['id'] = 'JJC-TEST-SCHED-002'
    task['state'] = 'Assigned'
    task['org'] = '尚书省'
    task['updatedAt'] = '2026-03-13T00:00:00Z'
    task['_scheduler']['controlState'] = 'Assigned'
    task['_scheduler']['retryCount'] = 0
    task['_scheduler']['maxRetry'] = 1
    task['_scheduler']['lastProgressAt'] = '2026-03-13T00:00:00Z'
    task['_scheduler']['stateSince'] = '2026-03-13T00:00:00Z'
    task['_scheduler']['lease']['ownerRunId'] = ''
    _write_tasks(data_dir, [task])

    monkeypatch.setattr(srv, 'dispatch_for_state', lambda *args, **kwargs: None)
    monkeypatch.setattr(srv, 'wake_agent', lambda *args, **kwargs: {'ok': True})

    result = srv.handle_scheduler_scan(threshold_sec=30)

    assert result['ok'] is True
    assert result['count'] == 1
    assert len(result['actions']) == 1
    assert result['actions'][0]['action'] == 'retry'


def test_writeback_retry_scan_only_retries_commit_chain(tmp_path, monkeypatch):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    srv.DATA = data_dir

    task = _base_task()
    task['id'] = 'JJC-TEST-SCHED-003'
    task['state'] = 'Doing'
    task['_scheduler']['lease']['ownerRunId'] = ''
    task['_scheduler']['writeback']['status'] = 'WritebackPending'
    task['_scheduler']['writeback']['retryCount'] = 0
    task['_scheduler']['writeback']['maxRetry'] = 2
    _write_tasks(data_dir, [task])

    dispatch_calls = []
    writeback_calls = []
    monkeypatch.setattr(srv, 'dispatch_for_state', lambda *args, **kwargs: dispatch_calls.append((args, kwargs)))
    monkeypatch.setattr(srv, '_retry_writeback_for_task', lambda task_id, owner_run_id='': writeback_calls.append((task_id, owner_run_id)) or {'ok': True})

    result = srv.handle_scheduler_scan(threshold_sec=30)

    assert result['ok'] is True
    assert result['count'] == 1
    assert result['actions'][0]['action'] == 'writeback_retry'
    assert dispatch_calls == []
    assert len(writeback_calls) == 1
    assert writeback_calls[0][0] == 'JJC-TEST-SCHED-003'


def test_manual_decision_reassign_has_explicit_recovery_target(tmp_path, monkeypatch):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    srv.DATA = data_dir

    task = _base_task()
    task['id'] = 'JJC-TEST-SCHED-004'
    task['state'] = 'Menxia'
    task['org'] = '门下省'
    task['_scheduler']['lease']['ownerRunId'] = ''
    _write_tasks(data_dir, [task])

    dispatch_calls = []
    monkeypatch.setattr(srv, 'dispatch_for_state', lambda *args, **kwargs: dispatch_calls.append((args, kwargs)))

    result = srv.handle_scheduler_action(
        task['id'],
        'manual_decide',
        '皇上要求改派',
        expected_version=5,
        recovery_target='reassign',
    )

    assert result['ok'] is True
    assert result.get('recoveryTarget') == 'reassign'
    assert len(dispatch_calls) == 1

    latest = next(t for t in srv.load_tasks() if t.get('id') == task['id'])
    assert latest.get('state') == 'Assigned'
    assert latest.get('org') == '尚书省'
