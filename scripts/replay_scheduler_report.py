#!/usr/bin/env python3
"""Build replay report for one scheduler task."""

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


def _metrics(task):
    sched = task.get("_scheduler") or {}
    flow = task.get("flow_log") or []
    progress = task.get("progress_log") or []
    diagnostics = task.get("diagnostic_log") or []
    dispatch_attempts = int(sched.get("dispatchAttempts") or 0)
    unique_steps = len({
        (item.get("agent", ""), item.get("text", ""), item.get("state", ""))
        for item in progress
        if item.get("text")
    })
    control_actions = sum(1 for item in diagnostics if item.get("eventType", "").startswith("state_commit"))
    invalid_control = sum(1 for item in diagnostics if item.get("eventType") == "state_commit_blocked")
    wb = sched.get("writeback") or {}
    first_output = _parse_iso(wb.get("firstOutputAt"))
    committed = _parse_iso(wb.get("lastCommittedAt"))
    writeback_lag_sec = None
    if first_output:
        end = committed or dt.datetime.now(dt.timezone.utc)
        writeback_lag_sec = max(0, int((end - first_output).total_seconds()))
    return {
        "taskId": task.get("id", ""),
        "state": task.get("state", ""),
        "dispatchAttempts": dispatch_attempts,
        "uniqueExecutionSteps": unique_steps,
        "dispatchAmplificationRatio": round(dispatch_attempts / unique_steps, 3) if unique_steps else 0.0,
        "flowCount": len(flow),
        "progressCount": len(progress),
        "controlActions": control_actions,
        "invalidControlActions": invalid_control,
        "invalidControlRatio": round(invalid_control / control_actions, 3) if control_actions else 0.0,
        "writebackStatus": wb.get("status", "idle"),
        "writebackLagSec": writeback_lag_sec,
    }


def _find_baseline_row(baseline, task_id):
    rows = baseline.get("tasks") if isinstance(baseline, dict) else None
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and row.get("taskId") == task_id:
            return row
    return None


def _render_markdown(task_id, metrics, baseline_row, now_iso):
    lines = []
    lines.append(f"# 调度回放报告：{task_id}")
    lines.append("")
    lines.append(f"- 生成时间: {now_iso}")
    lines.append(f"- 当前状态: {metrics['state']}")
    lines.append("")
    lines.append("## 当前指标")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| dispatchAttempts | {metrics['dispatchAttempts']} |")
    lines.append(f"| uniqueExecutionSteps | {metrics['uniqueExecutionSteps']} |")
    lines.append(f"| dispatchAmplificationRatio | {metrics['dispatchAmplificationRatio']} |")
    lines.append(f"| flowCount | {metrics['flowCount']} |")
    lines.append(f"| progressCount | {metrics['progressCount']} |")
    lines.append(f"| invalidControlRatio | {metrics['invalidControlRatio']} |")
    lines.append(f"| writebackStatus | {metrics['writebackStatus']} |")
    lines.append(f"| writebackLagSec | {metrics['writebackLagSec']} |")
    lines.append("")
    lines.append("## 与基线对比")
    lines.append("")
    if not baseline_row:
        lines.append("- 未找到该任务基线记录（跳过对比）。")
    else:
        lines.append("| 指标 | 基线 | 当前 | 差值 |")
        lines.append("|---|---:|---:|---:|")
        for key in ("dispatchAttempts", "flowCount", "progressCount"):
            base = int(baseline_row.get(key) or 0)
            cur = int(metrics.get(key) or 0)
            lines.append(f"| {key} | {base} | {cur} | {cur - base:+d} |")
    lines.append("")
    lines.append("## 结论")
    lines.append("")
    if metrics["dispatchAmplificationRatio"] <= 2:
        lines.append("- 调度放大比处于可控区间。")
    else:
        lines.append("- 调度放大比偏高，建议继续收敛重试/升级触发条件。")
    if metrics["invalidControlRatio"] <= 0.3:
        lines.append("- 无效控制比处于可接受范围。")
    else:
        lines.append("- 无效控制比偏高，建议排查 blockedBy 分布。")
    if metrics["writebackStatus"] == "idle":
        lines.append("- 写回链路闭环完成。")
    else:
        lines.append("- 写回链路仍在处理中，需继续观察。")
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate scheduler replay report")
    parser.add_argument("--task-id", required=True, help="Task id to replay")
    parser.add_argument(
        "--baseline",
        default=str(DATA / "scheduler_baseline_latest.json"),
        help="Baseline snapshot json path",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output markdown path (default to .helloagents/plan/<latest>/replay_report_<task>.md)",
    )
    args = parser.parse_args()

    source = DATA / "tasks_source.json"
    tasks = json.loads(source.read_text(encoding="utf-8")) if source.exists() else []
    task = next((item for item in tasks if item.get("id") == args.task_id), None)
    if not task:
        raise SystemExit(f"task not found: {args.task_id}")

    baseline_path = pathlib.Path(args.baseline)
    baseline = {}
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))

    now_iso = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    metrics = _metrics(task)
    baseline_row = _find_baseline_row(baseline, args.task_id)
    content = _render_markdown(args.task_id, metrics, baseline_row, now_iso)

    if args.output:
        output = pathlib.Path(args.output)
    else:
        plan_root = ROOT / ".helloagents" / "plan"
        candidates = sorted([p for p in plan_root.iterdir() if p.is_dir()], reverse=True) if plan_root.exists() else []
        target_dir = candidates[0] if candidates else plan_root
        output = target_dir / f"replay_report_{args.task_id}.md"

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    print(f"replay report written: {output}")


if __name__ == "__main__":
    main()
