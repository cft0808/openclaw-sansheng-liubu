"""tests for scripts/kanban_update.py"""
import json
import pathlib
import sys

# Ensure scripts/ is importable
SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import kanban_update as kb


def test_create_and_get(tmp_path):
    """kanban create + get round-trip."""
    tasks_file = tmp_path / "tasks_source.json"
    tasks_file.write_text("[]", encoding="utf-8")

    original = kb.TASKS_FILE
    kb.TASKS_FILE = tasks_file
    try:
        kb.cmd_create("TEST-001", "测试任务创建和查询功能验证", "Inbox", "工部", "工部尚书")
        tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
        assert any(t.get("id") == "TEST-001" for t in tasks)
        task = next(t for t in tasks if t["id"] == "TEST-001")
        assert task["title"] == "测试任务创建和查询功能验证"
        assert task["state"] == "Inbox"
        assert task["org"] == "工部"
    finally:
        kb.TASKS_FILE = original


def test_move_state(tmp_path):
    """kanban move changes task state."""
    tasks_file = tmp_path / "tasks_source.json"
    tasks_file.write_text(json.dumps([
        {"id": "T-1", "title": "test", "state": "Inbox"}
    ], ensure_ascii=False), encoding="utf-8")

    original = kb.TASKS_FILE
    kb.TASKS_FILE = tasks_file
    try:
        kb.cmd_state("T-1", "Doing")
        tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
        assert tasks[0]["state"] == "Doing"
    finally:
        kb.TASKS_FILE = original


def test_block_and_unblock(tmp_path):
    """kanban block round-trip."""
    tasks_file = tmp_path / "tasks_source.json"
    tasks_file.write_text(json.dumps([
        {"id": "T-2", "title": "blocker test", "state": "Doing"}
    ], ensure_ascii=False), encoding="utf-8")

    original = kb.TASKS_FILE
    kb.TASKS_FILE = tasks_file
    try:
        kb.cmd_block("T-2", "等待依赖")
        tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
        assert tasks[0]["state"] == "Blocked"
        assert tasks[0]["block"] == "等待依赖"
    finally:
        kb.TASKS_FILE = original


def test_flow_log(tmp_path):
    """cmd_flow appends a flow_log entry."""
    tasks_file = tmp_path / "tasks_source.json"
    tasks_file.write_text(json.dumps([
        {"id": "T-3", "title": "flow test", "state": "Zhongshu", "flow_log": []}
    ], ensure_ascii=False), encoding="utf-8")

    original = kb.TASKS_FILE
    kb.TASKS_FILE = tasks_file
    try:
        kb.cmd_flow("T-3", "中书省", "门下省", "规划方案提交审议")
        tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
        task = tasks[0]
        assert len(task["flow_log"]) == 1
        assert task["flow_log"][0]["from"] == "中书省"
        assert task["flow_log"][0]["to"] == "门下省"
    finally:
        kb.TASKS_FILE = original


def test_done_routes_to_review(tmp_path):
    """cmd_done should route execution output back to Review instead of direct Done."""
    tasks_file = tmp_path / "tasks_source.json"
    tasks_file.write_text(json.dumps([
        {
            "id": "T-4",
            "title": "done test",
            "state": "Doing",
            "org": "兵部",
            "flow_log": [],
            "todos": [{"id": "1", "title": "收尾", "status": "completed"}],
        }
    ], ensure_ascii=False), encoding="utf-8")

    original = kb.TASKS_FILE
    kb.TASKS_FILE = tasks_file
    try:
        kb.cmd_done("T-4", "/tmp/output.md", "功能已全部实现")
        tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
        task = tasks[0]
        assert task["state"] == "Review"
        assert task["org"] == "尚书省"
        assert task["output"] == "/tmp/output.md"
        assert task["now"] == "功能已全部实现"
        assert any("提交审查" in entry.get("remark", "") for entry in task["flow_log"])
    finally:
        kb.TASKS_FILE = original


def test_done_rejects_incomplete_todos(tmp_path):
    """cmd_done should be rejected when todos are still incomplete."""
    tasks_file = tmp_path / "tasks_source.json"
    tasks_file.write_text(json.dumps([
        {
            "id": "T-4B",
            "title": "done gate test",
            "state": "Doing",
            "org": "工部",
            "flow_log": [],
            "todos": [
                {"id": "1", "title": "已完成", "status": "completed"},
                {"id": "2", "title": "未完成", "status": "in-progress"},
            ],
        }
    ], ensure_ascii=False), encoding="utf-8")

    original = kb.TASKS_FILE
    kb.TASKS_FILE = tasks_file
    try:
        kb.cmd_done("T-4B", "/tmp/output.md", "试图提前收口")
        tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
        task = tasks[0]
        assert task["state"] == "Doing"
        assert task.get("output", "") in ("", None)
        assert task.get("flow_log", []) == []
    finally:
        kb.TASKS_FILE = original


def test_progress(tmp_path):
    """cmd_progress updates now text and appends to progress_log."""
    tasks_file = tmp_path / "tasks_source.json"
    tasks_file.write_text(json.dumps([
        {"id": "T-5", "title": "progress test", "state": "Doing", "org": "工部"}
    ], ensure_ascii=False), encoding="utf-8")

    original = kb.TASKS_FILE
    kb.TASKS_FILE = tasks_file
    try:
        kb.cmd_progress("T-5", "正在实现核心模块", "已完成需求分析✅|正在写代码🔄|待测试")
        tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
        task = tasks[0]
        assert task["now"] == "正在实现核心模块"
        assert len(task.get("progress_log", [])) == 1
        todos = task.get("todos", [])
        assert len(todos) == 3
        statuses = {td["title"]: td["status"] for td in todos}
        assert statuses["已完成需求分析"] == "completed"
        assert statuses["正在写代码"] == "in-progress"
        assert statuses["待测试"] == "not-started"
    finally:
        kb.TASKS_FILE = original


def test_progress_clears_active_dispatch(tmp_path):
    """Any real progress should stop stale queued dispatch recovery."""
    tasks_file = tmp_path / "tasks_source.json"
    tasks_file.write_text(json.dumps([
        {
            "id": "T-5B",
            "title": "progress scheduler test",
            "state": "Taizi",
            "org": "太子",
            "_scheduler": {
                "lastDispatchStatus": "queued",
                "activeDispatchId": "old",
                "activeDispatchState": "Taizi",
                "activeDispatchStartedAt": "2026-05-27T12:00:00Z",
                "retryCount": 2,
                "escalationLevel": 1,
            },
        }
    ], ensure_ascii=False), encoding="utf-8")

    original = kb.TASKS_FILE
    kb.TASKS_FILE = tasks_file
    try:
        kb.cmd_progress("T-5B", "太子已经接旨并开始转交", "转交中书省🔄")
        task = json.loads(tasks_file.read_text(encoding="utf-8"))[0]
        sched = task["_scheduler"]
        assert sched["lastDispatchStatus"] == "progress"
        assert sched["retryCount"] == 0
        assert sched["escalationLevel"] == 0
        assert "activeDispatchId" not in sched
    finally:
        kb.TASKS_FILE = original


def test_show_outputs_task_json(tmp_path, capsys):
    """cmd_show gives agents a safe way to inspect a task."""
    tasks_file = tmp_path / "tasks_source.json"
    tasks_file.write_text(json.dumps([
        {"id": "T-SHOW", "title": "show test", "state": "Doing"}
    ], ensure_ascii=False), encoding="utf-8")

    original = kb.TASKS_FILE
    kb.TASKS_FILE = tasks_file
    try:
        kb.cmd_show("T-SHOW")
        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is True
        assert out["task"]["id"] == "T-SHOW"
    finally:
        kb.TASKS_FILE = original


def test_todo(tmp_path):
    """cmd_todo adds and updates sub-tasks."""
    tasks_file = tmp_path / "tasks_source.json"
    tasks_file.write_text(json.dumps([
        {"id": "T-6", "title": "todo test", "state": "Doing"}
    ], ensure_ascii=False), encoding="utf-8")

    original = kb.TASKS_FILE
    kb.TASKS_FILE = tasks_file
    try:
        kb.cmd_todo("T-6", "1", "实现登录接口", "in-progress")
        kb.cmd_todo("T-6", "2", "编写测试", "not-started")
        kb.cmd_todo("T-6", "1", "", "completed")
        tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
        task = tasks[0]
        todos = {td["id"]: td for td in task.get("todos", [])}
        assert todos["1"]["status"] == "completed"
        assert todos["2"]["status"] == "not-started"
    finally:
        kb.TASKS_FILE = original


def test_progress_log_capped(tmp_path):
    """progress_log should not exceed MAX_PROGRESS_LOG entries."""
    tasks_file = tmp_path / "tasks_source.json"
    tasks_file.write_text(json.dumps([
        {"id": "T-7", "title": "日志上限测试", "state": "Doing", "org": "礼部"}
    ], ensure_ascii=False), encoding="utf-8")

    original = kb.TASKS_FILE
    kb.TASKS_FILE = tasks_file
    try:
        for i in range(kb.MAX_PROGRESS_LOG + 5):
            kb.cmd_progress("T-7", f"第{i}次进展汇报内容，描述当前执行情况")
        tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
        task = tasks[0]
        assert len(task.get("progress_log", [])) == kb.MAX_PROGRESS_LOG
    finally:
        kb.TASKS_FILE = original
