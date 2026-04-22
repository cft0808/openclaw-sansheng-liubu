"""Tests for task mutation atomicity — verifying that concurrent writers
(dispatch threads, periodic scanner, HTTP handlers) cannot clobber each
other's changes.

The core issue: the old ``load_tasks()`` + modify + ``save_tasks()`` pattern
allows two concurrent threads to both read the same snapshot, each modify
a different field, and the second ``save_tasks()`` overwrites the first's
changes — a classic TOCTOU (Time-of-Check-Time-of-Use) race.

The fix introduces ``modify_tasks()`` / ``modify_task()`` wrappers around
``atomic_json_update()`` which hold the file lock for the entire
read-modify-write cycle.
"""
import json
import pathlib
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'dashboard'))
sys.path.insert(0, str(ROOT / 'scripts'))


def _setup_server(monkeypatch, tmp_path, tasks=None):
    """Bootstrap server module with isolated data directory."""
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    tasks_path = data_dir / 'tasks_source.json'
    initial = tasks or []
    tasks_path.write_text(json.dumps(initial, ensure_ascii=False), encoding='utf-8')
    (data_dir / 'agent_config.json').write_text('{}', encoding='utf-8')

    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)
    monkeypatch.setattr(srv, 'SCRIPTS', tmp_path / 'scripts')  # avoid real scripts
    monkeypatch.setattr(srv, '_check_gateway_alive', lambda: False)  # no real dispatch
    # Suppress refresh subprocess
    monkeypatch.setattr(srv, '_trigger_refresh', lambda: None)

    return srv, data_dir, tasks_path


# ── Test: modify_tasks holds file lock ──


class TestModifyTasksAtomicity:
    """Verify that ``modify_tasks`` uses atomic_json_update under the hood."""

    def test_modify_tasks_exists_and_callable(self, monkeypatch, tmp_path):
        srv, _, _ = _setup_server(monkeypatch, tmp_path)
        assert callable(getattr(srv, 'modify_tasks', None)), \
            'modify_tasks must be a callable function on server module'

    def test_modify_task_exists_and_callable(self, monkeypatch, tmp_path):
        srv, _, _ = _setup_server(monkeypatch, tmp_path)
        assert callable(getattr(srv, 'modify_task', None)), \
            'modify_task must be a callable function on server module'

    def test_modify_task_updates_single_task(self, monkeypatch, tmp_path):
        task = {
            'id': 'T-001', 'title': '测试', 'state': 'Doing',
            'org': '兵部', 'updatedAt': '2026-04-22T00:00:00Z',
        }
        srv, _, tasks_path = _setup_server(monkeypatch, tmp_path, [task])

        found = srv.modify_task('T-001', lambda t: t.update({'state': 'Review'}))
        assert found is True

        data = json.loads(tasks_path.read_text(encoding='utf-8'))
        assert data[0]['state'] == 'Review'
        assert 'updatedAt' in data[0]  # auto-stamped

    def test_modify_task_returns_false_for_missing(self, monkeypatch, tmp_path):
        srv, _, _ = _setup_server(monkeypatch, tmp_path, [])
        found = srv.modify_task('NONEXISTENT', lambda t: t.update({'state': 'Done'}))
        assert found is False

    def test_modify_tasks_bulk_update(self, monkeypatch, tmp_path):
        tasks = [
            {'id': 'T-A', 'title': 'A', 'state': 'Doing', 'org': '', 'updatedAt': ''},
            {'id': 'T-B', 'title': 'B', 'state': 'Doing', 'org': '', 'updatedAt': ''},
        ]
        srv, _, tasks_path = _setup_server(monkeypatch, tmp_path, tasks)

        def _mark_all_done(tasks):
            for t in tasks:
                t['state'] = 'Done'
            return tasks

        srv.modify_tasks(_mark_all_done)

        data = json.loads(tasks_path.read_text(encoding='utf-8'))
        assert all(t['state'] == 'Done' for t in data)


# ── Test: _update_task_scheduler uses modify_task ──


class TestUpdateTaskSchedulerAtomicity:
    """Verify that ``_update_task_scheduler`` no longer uses the racy
    ``load_tasks()`` + ``save_tasks()`` pattern."""

    def test_scheduler_update_persists_atomically(self, monkeypatch, tmp_path):
        task = {
            'id': 'T-002', 'title': '派发测试', 'state': 'Taizi',
            'org': '太子', 'updatedAt': '2026-04-22T01:00:00Z',
        }
        srv, _, tasks_path = _setup_server(monkeypatch, tmp_path, [task])

        srv._update_task_scheduler('T-002', lambda t, s: s.update({
            'lastDispatchStatus': 'success',
            'lastDispatchAgent': 'taizi',
        }))

        data = json.loads(tasks_path.read_text(encoding='utf-8'))
        sched = data[0].get('_scheduler', {})
        assert sched['lastDispatchStatus'] == 'success'
        assert sched['lastDispatchAgent'] == 'taizi'

    def test_scheduler_update_missing_task(self, monkeypatch, tmp_path):
        srv, _, _ = _setup_server(monkeypatch, tmp_path, [])
        result = srv._update_task_scheduler('MISSING', lambda t, s: None)
        assert result is False


# ── Test: handle_scheduler_scan uses modify_tasks ──


class TestSchedulerScanAtomicity:
    """Verify that the periodic scanner mutates tasks under the file lock."""

    def test_scan_stalled_task_triggers_retry(self, monkeypatch, tmp_path):
        """A task stalled past threshold should get retryCount incremented
        and the change should be persisted atomically."""
        import datetime

        old_ts = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(seconds=700)
        ).isoformat()

        task = {
            'id': 'T-003', 'title': '停滞任务', 'state': 'Zhongshu',
            'org': '中书省', 'updatedAt': old_ts,
            '_scheduler': {
                'enabled': True, 'stallThresholdSec': 600, 'maxRetry': 2,
                'retryCount': 0, 'escalationLevel': 0, 'autoRollback': True,
                'lastProgressAt': old_ts, 'stallSince': None,
                'lastDispatchStatus': 'idle', 'rollbackCount': 0,
                'snapshot': {'state': 'Taizi', 'org': '太子', 'now': '', 'savedAt': old_ts, 'note': 'init'},
            },
        }
        srv, _, tasks_path = _setup_server(monkeypatch, tmp_path, [task])

        # Suppress dispatch side-effects
        monkeypatch.setattr(srv, 'dispatch_for_state', lambda *a, **kw: None)
        monkeypatch.setattr(srv, 'wake_agent', lambda *a, **kw: None)

        result = srv.handle_scheduler_scan(threshold_sec=600)
        assert result['ok'] is True
        assert result['count'] >= 1

        data = json.loads(tasks_path.read_text(encoding='utf-8'))
        sched = data[0].get('_scheduler', {})
        assert sched['retryCount'] == 1
        assert sched['lastDispatchTrigger'] == 'taizi-scan-retry'


# ── Test: concurrent modify_task calls don't clobber ──


class TestConcurrentModifyTask:
    """Simulate the race that existed before the fix: two threads
    concurrently modifying different fields of the same task."""

    def test_concurrent_writes_both_persist(self, monkeypatch, tmp_path):
        """Two threads updating different scheduler fields should both
        be visible in the final state (no lost updates)."""
        task = {
            'id': 'T-RACE', 'title': '竞争测试', 'state': 'Doing',
            'org': '兵部', 'updatedAt': '2026-04-22T02:00:00Z',
            '_scheduler': {
                'enabled': True, 'stallThresholdSec': 600, 'maxRetry': 2,
                'retryCount': 0, 'escalationLevel': 0, 'autoRollback': True,
                'lastProgressAt': '2026-04-22T02:00:00Z', 'stallSince': None,
                'lastDispatchStatus': 'idle', 'rollbackCount': 0,
                'field_a': 'initial_a', 'field_b': 'initial_b',
                'snapshot': {'state': 'Assigned', 'org': '尚书省', 'now': '', 'savedAt': '', 'note': 'init'},
            },
        }
        srv, _, tasks_path = _setup_server(monkeypatch, tmp_path, [task])
        monkeypatch.setattr(srv, '_trigger_refresh', lambda: None)

        barrier = threading.Barrier(2, timeout=5)
        errors = []

        def update_field_a():
            try:
                barrier.wait()
                srv.modify_task('T-RACE', lambda t: t.setdefault('_scheduler', {}).update({'field_a': 'updated_a'}))
            except Exception as e:
                errors.append(e)

        def update_field_b():
            try:
                barrier.wait()
                srv.modify_task('T-RACE', lambda t: t.setdefault('_scheduler', {}).update({'field_b': 'updated_b'}))
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=update_field_a)
        t2 = threading.Thread(target=update_field_b)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f'Thread errors: {errors}'

        data = json.loads(tasks_path.read_text(encoding='utf-8'))
        sched = data[0].get('_scheduler', {})

        # With atomic modify_task, BOTH updates must be visible.
        # The old load_tasks/save_tasks pattern would lose one.
        assert sched['field_a'] == 'updated_a', \
            f'field_a lost: {sched.get("field_a")}'
        assert sched['field_b'] == 'updated_b', \
            f'field_b lost: {sched.get("field_b")}'


# ── Test: source audit — no racy load/save in scheduler paths ──


class TestSourceAudit:
    """Verify that the critical concurrent paths no longer use the racy
    ``load_tasks()`` + ``save_tasks()`` pattern."""

    def test_update_task_scheduler_no_load_save(self):
        """_update_task_scheduler should not call load_tasks or save_tasks directly."""
        import inspect
        import server as srv

        source = inspect.getsource(srv._update_task_scheduler)
        assert 'load_tasks' not in source, \
            '_update_task_scheduler still calls load_tasks() — should use modify_task()'
        assert 'save_tasks' not in source, \
            '_update_task_scheduler still calls save_tasks() — should use modify_task()'

    def test_handle_scheduler_scan_no_save_tasks(self):
        """handle_scheduler_scan should use modify_tasks, not save_tasks()."""
        import ast
        import inspect
        import server as srv

        source = inspect.getsource(srv.handle_scheduler_scan)
        tree = ast.parse(source)
        # Check that save_tasks() is not called in the function body
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = ''
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                assert name != 'save_tasks', \
                    'handle_scheduler_scan still calls save_tasks() — should use modify_tasks()'

    def test_modify_tasks_uses_atomic_json_update(self):
        """modify_tasks must delegate to atomic_json_update for lock safety."""
        import inspect
        import server as srv

        source = inspect.getsource(srv.modify_tasks)
        assert 'atomic_json_update' in source, \
            'modify_tasks must use atomic_json_update for file-level locking'

    def test_modify_task_delegates_to_modify_tasks(self):
        """modify_task should use modify_tasks (or atomic_json_update) internally."""
        import inspect
        import server as srv

        source = inspect.getsource(srv.modify_task)
        assert 'modify_tasks' in source or 'atomic_json_update' in source, \
            'modify_task should delegate to modify_tasks or atomic_json_update'

    def test_handle_scheduler_retry_uses_modify_task(self):
        """handle_scheduler_retry should use modify_task instead of load/save."""
        import inspect
        import server as srv

        source = inspect.getsource(srv.handle_scheduler_retry)
        assert 'modify_task' in source, \
            'handle_scheduler_retry should use modify_task for atomic updates'

    def test_handle_scheduler_rollback_uses_modify_task(self):
        """handle_scheduler_rollback should use modify_task instead of load/save."""
        import inspect
        import server as srv

        source = inspect.getsource(srv.handle_scheduler_rollback)
        assert 'modify_task' in source, \
            'handle_scheduler_rollback should use modify_task for atomic updates'


# ── Test: backward compatibility — load_tasks/save_tasks still exist ──


class TestBackwardCompatibility:
    """HTTP handler paths still use load_tasks/save_tasks for now;
    ensure they remain functional."""

    def test_load_tasks_still_works(self, monkeypatch, tmp_path):
        tasks = [{'id': 'T-BC', 'title': '兼容性', 'state': 'Doing'}]
        srv, _, _ = _setup_server(monkeypatch, tmp_path, tasks)
        loaded = srv.load_tasks()
        assert len(loaded) == 1
        assert loaded[0]['id'] == 'T-BC'

    def test_save_tasks_still_works(self, monkeypatch, tmp_path):
        srv, _, tasks_path = _setup_server(monkeypatch, tmp_path, [])
        srv.save_tasks([{'id': 'T-NEW', 'title': '新', 'state': 'Pending'}])
        data = json.loads(tasks_path.read_text(encoding='utf-8'))
        assert data[0]['id'] == 'T-NEW'
