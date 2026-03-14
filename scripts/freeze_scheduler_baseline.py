#!/usr/bin/env python3
"""Freeze scheduler baseline metrics for recent tasks."""

import argparse
import datetime as dt
import json
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def _parse_iso(value):
    if not value or not isinstance(value, str):
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _task_metrics(task):
    sched = task.get("_scheduler") or {}
    flow = task.get("flow_log") or []
    progress = task.get("progress_log") or []
    unique_steps = len({
        (item.get("agent", ""), item.get("text", ""), item.get("state", ""))
        for item in progress
        if item.get("text")
    })
    retries = sum(1 for item in flow if "重试" in (item.get("remark") or ""))
    escalates = sum(1 for item in flow if "升级" in (item.get("remark") or ""))
    rollbacks = sum(1 for item in flow if "回滚" in (item.get("remark") or ""))
    return {
        "taskId": task.get("id", ""),
        "state": task.get("state", ""),
        "dispatchAttempts": int(sched.get("dispatchAttempts") or 0),
        "retryEvents": retries,
        "escalateEvents": escalates,
        "rollbackEvents": rollbacks,
        "flowCount": len(flow),
        "progressCount": len(progress),
        "uniqueExecutionSteps": unique_steps,
        "updatedAt": task.get("updatedAt", ""),
    }


def _summary(rows):
    total_dispatch = sum(item["dispatchAttempts"] for item in rows)
    total_unique = sum(item["uniqueExecutionSteps"] for item in rows)
    return {
        "taskCount": len(rows),
        "dispatchAttempts": total_dispatch,
        "retryEvents": sum(item["retryEvents"] for item in rows),
        "escalateEvents": sum(item["escalateEvents"] for item in rows),
        "rollbackEvents": sum(item["rollbackEvents"] for item in rows),
        "flowCount": sum(item["flowCount"] for item in rows),
        "progressCount": sum(item["progressCount"] for item in rows),
        "dispatchAmplificationRatio": round(total_dispatch / total_unique, 3) if total_unique else 0.0,
    }


def main():
    parser = argparse.ArgumentParser(description="Freeze scheduler baseline snapshot")
    parser.add_argument("--days", type=int, default=7, help="Lookback days")
    parser.add_argument(
        "--output",
        default=str(DATA / "scheduler_baseline_latest.json"),
        help="Output json path",
    )
    args = parser.parse_args()

    source = DATA / "tasks_source.json"
    tasks = json.loads(source.read_text(encoding="utf-8")) if source.exists() else []
    now = dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(days=max(1, int(args.days)))

    selected = []
    for task in tasks:
        updated = _parse_iso(task.get("updatedAt"))
        if updated and updated < since:
            continue
        selected.append(_task_metrics(task))

    payload = {
        "generatedAt": now.isoformat().replace("+00:00", "Z"),
        "lookbackDays": int(args.days),
        "source": str(source),
        "summary": _summary(selected),
        "tasks": selected,
    }

    output = pathlib.Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"baseline written: {output}")
    print(json.dumps(payload["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
