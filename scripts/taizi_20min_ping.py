#!/usr/bin/env python3
"""taizi_20min_ping.py

A simple watchdog that ensures the taizi agent posts a minimal progress update
at least every N minutes for specific active tasks.

Design goals:
- Be safe: no messaging APIs; only writes to kanban via kanban_update.py progress.
- Be conservative: only ping tasks in non-terminal states.

Usage:
  python3 scripts/taizi_20min_ping.py --task JJC-... --max-minutes 20 --now "..." --plan "...|..."

Typical use: invoked by ops/edict run_loop.sh or cron.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TASKS = BASE / "data" / "tasks_source.json"
K_UPDATE = BASE / "scripts" / "kanban_update.py"

TERMINAL = {"Done", "Cancelled"}


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_iso(s: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def load_tasks() -> list[dict]:
    if not TASKS.exists():
        return []
    return json.loads(TASKS.read_text())


def find_task(tasks: list[dict], task_id: str) -> dict | None:
    for t in tasks:
        if t.get("id") == task_id:
            return t
    return None


def last_progress_at(task: dict) -> dt.datetime | None:
    pl = task.get("progress_log") or []
    if not pl:
        return parse_iso(task.get("updatedAt") or "")
    last = pl[-1]
    return parse_iso(last.get("at") or "") or parse_iso(task.get("updatedAt") or "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--max-minutes", type=int, default=20)
    ap.add_argument("--now", required=True)
    ap.add_argument("--plan", required=True)
    args = ap.parse_args()

    tasks = load_tasks()
    task = find_task(tasks, args.task)
    if not task:
        return 0

    if (task.get("state") in TERMINAL) or (task.get("org") in ("完成",)):
        return 0

    last = last_progress_at(task)
    if not last:
        # no timestamp: ping once
        should = True
    else:
        gap = (now_utc() - last).total_seconds() / 60
        should = gap >= args.max_minutes

    if not should:
        return 0

    cmd = [
        "python3",
        str(K_UPDATE),
        "progress",
        args.task,
        args.now,
        args.plan,
    ]
    subprocess.run(cmd, cwd=str(BASE), check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
