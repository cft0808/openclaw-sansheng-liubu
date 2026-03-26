#!/usr/bin/env python3
"""JSON → Postgres 数据迁移脚本。

读取旧版 data/tasks_source.json，导入到 Edict Postgres 数据库。

用法:
  # 确保 Postgres 已运行且 schema 已创建（alembic upgrade head）
  python3 migrate_json_to_pg.py

  # 指定数据文件
  python3 migrate_json_to_pg.py --file /path/to/tasks_source.json

  # Dry run（只分析不写入）
  python3 migrate_json_to_pg.py --dry-run
"""

import argparse
import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# 添加 backend 路径
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from sqlalchemy import text
from app.db import engine, async_session, Base
from app.models.task import Task, TaskState

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
log = logging.getLogger("migrate")

# 旧版状态 → Edict TaskState
STATE_MAP = {
    "Taizi": TaskState.Taizi,
    "Zhongshu": TaskState.Zhongshu,
    "Menxia": TaskState.Menxia,
    "Assigned": TaskState.Assigned,
    "Next": TaskState.Next,
    "Doing": TaskState.Doing,
    "Review": TaskState.Review,
    "Done": TaskState.Done,
    "Blocked": TaskState.Blocked,
    "Cancelled": TaskState.Cancelled,
    "Pending": TaskState.Pending,
    # Fallbacks
    "Inbox": TaskState.Taizi,
    "": TaskState.Taizi,
}


def parse_old_task(old: dict) -> dict:
    """将旧版 task JSON 转换为 Edict Task 参数。"""
    state_str = old.get("state", "Taizi")
    state = STATE_MAP.get(state_str, TaskState.Taizi)

    legacy_id = old.get("id", "")
    title = old.get("title", "未命名任务")

    # 解析时间
    updated_str = old.get("updatedAt", "")
    try:
        updated_at = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        updated_at = datetime.now(timezone.utc)

    return {
        "trace_id": str(uuid.uuid4()),
        "title": title,
        "description": old.get("now", ""),
        "priority": "中",
        "state": state,
        "assignee_org": old.get("org", None),
        "creator": old.get("official", "emperor"),
        "tags": [legacy_id] if legacy_id else [],
        "org": old.get("org", Task.org_for_state(state)),
        "official": old.get("official", ""),
        "now": old.get("now", ""),
        "eta": old.get("eta", "-"),
        "block": old.get("block", "无"),
        "output": old.get("output", ""),
        "archived": bool(old.get("archived", False)),
        "flow_log": old.get("flow_log", []),
        "progress_log": old.get("progress_log", []),
        "todos": old.get("todos", []),
        "scheduler": old.get("scheduler", {}),
        "template_id": old.get("templateId", ""),
        "template_params": old.get("templateParams", {}),
        "ac": old.get("ac", ""),
        "target_dept": old.get("targetDept", ""),
        "meta": {
            "legacy_id": legacy_id,
            "legacy_state": state_str,
            "legacy_output": old.get("output", ""),
            "legacy_ac": old.get("ac", ""),
            "legacy_eta": old.get("eta", ""),
            "legacy_block": old.get("block", ""),
        },
        "created_at": updated_at,  # 旧版没有 created_at，用 updated_at 近似
        "updated_at": updated_at,
    }


async def migrate(file_path: Path, dry_run: bool = False):
    """执行迁移。"""
    if not file_path.exists():
        log.error(f"数据文件不存在: {file_path}")
        return

    # 读取旧版数据
    raw = file_path.read_text(encoding="utf-8")
    old_tasks = json.loads(raw)
    log.info(f"读取到 {len(old_tasks)} 个旧版任务")

    # 统计
    stats = {"total": len(old_tasks), "migrated": 0, "skipped": 0, "errors": 0}
    by_state = {}

    for old in old_tasks:
        state_str = old.get("state", "?")
        by_state[state_str] = by_state.get(state_str, 0) + 1

    log.info(f"状态分布: {by_state}")

    if dry_run:
        log.info("=== DRY RUN 模式，不写入数据库 ===")
        for old in old_tasks:
            params = parse_old_task(old)
            log.info(f"  [{params['meta']['legacy_id']}] {params['title'][:40]} → {params['state'].value}")
        log.info(f"Dry run 完成: {stats['total']} 个任务待迁移")
        return

    # 写入 Postgres
    async with async_session() as db:
        for old in old_tasks:
            try:
                params = parse_old_task(old)
                legacy_id = params["meta"]["legacy_id"]

                # 检查是否已迁移
                from sqlalchemy import select
                existing = await db.execute(
                    select(Task).where(Task.tags.contains([legacy_id]))
                )
                if existing.scalars().first():
                    log.debug(f"跳过已存在: {legacy_id}")
                    stats["skipped"] += 1
                    continue

                task = Task(**params)
                db.add(task)
                stats["migrated"] += 1
                log.info(f"✅ 迁移: [{legacy_id}] {params['title'][:40]} → {params['state'].value}")

            except Exception as e:
                log.error(f"❌ 迁移失败: {old.get('id', '?')}: {e}")
                stats["errors"] += 1

        await db.commit()

    log.info(f"迁移完成: 总计 {stats['total']}, 成功 {stats['migrated']}, "
             f"跳过 {stats['skipped']}, 错误 {stats['errors']}")


def main():
    parser = argparse.ArgumentParser(description="Migrate JSON tasks to Postgres")
    parser.add_argument(
        "--file", "-f",
        default=str(Path(__file__).parent.parent.parent / "data" / "tasks_source.json"),
        help="Path to tasks_source.json",
    )
    parser.add_argument("--dry-run", action="store_true", help="Only analyze, don't write")
    args = parser.parse_args()

    asyncio.run(migrate(Path(args.file), dry_run=args.dry_run))


if __name__ == "__main__":
    main()
