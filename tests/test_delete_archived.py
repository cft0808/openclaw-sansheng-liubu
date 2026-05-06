"""tests for handle_delete_archived in dashboard/server.py"""
import json, pathlib, sys
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'dashboard'))
sys.path.insert(0, str(ROOT / 'scripts'))

import server as srv


SAMPLE_TASKS = [
    {"id": "T-1", "title": "active task", "state": "Doing"},
    {"id": "T-2", "title": "archived task", "state": "Done", "archived": True, "archivedAt": "2026-01-01T00:00:00+08:00"},
    {"id": "T-3", "title": "another archived", "state": "Cancelled", "archived": True, "archivedAt": "2026-01-02T00:00:00+08:00"},
]


def _run_delete(tasks, **kwargs):
    """Run handle_delete_archived with mocked load/save."""
    saved = {}
    with patch.object(srv, 'load_tasks', return_value=list(tasks)), \
         patch.object(srv, 'save_tasks', side_effect=lambda t: saved.update(tasks=t)):
        result = srv.handle_delete_archived(**kwargs)
    return result, saved.get('tasks')


def test_delete_single_archived():
    result, saved = _run_delete(SAMPLE_TASKS, task_id="T-2")
    assert result['ok'] is True
    assert 'T-2' in result['message']
    assert len(saved) == 2
    assert all(t['id'] != 'T-2' for t in saved)


def test_delete_non_archived_rejected():
    result, saved = _run_delete(SAMPLE_TASKS, task_id="T-1")
    assert result['ok'] is False
    assert '未归档' in result['error']
    assert saved is None  # save_tasks not called


def test_delete_nonexistent_rejected():
    result, saved = _run_delete(SAMPLE_TASKS, task_id="T-999")
    assert result['ok'] is False
    assert '不存在' in result['error']
    assert saved is None


def test_delete_all_archived():
    result, saved = _run_delete(SAMPLE_TASKS, delete_all=True)
    assert result['ok'] is True
    assert result['count'] == 2
    assert len(saved) == 1
    assert saved[0]['id'] == 'T-1'


def test_delete_all_when_none_archived():
    tasks = [{"id": "T-1", "title": "active", "state": "Doing"}]
    result, saved = _run_delete(tasks, delete_all=True)
    assert result['ok'] is True
    assert result['count'] == 0
    assert len(saved) == 1
