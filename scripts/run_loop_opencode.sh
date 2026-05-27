#!/bin/bash
# 三省六部 · OpenCode 数据刷新循环
# 用法: ./run_loop_opencode.sh [间隔秒数 [巡检间隔秒数]]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export EDICT_HOME="${EDICT_HOME:-$(dirname "$SCRIPT_DIR")}"
export EDICT_RUNTIME="${EDICT_RUNTIME:-opencode}"
export EDICT_AGENT_RUNTIME="${EDICT_AGENT_RUNTIME:-opencode}"
export OPENCODE_MODEL="${OPENCODE_MODEL:-github-copilot/gpt-4o}"
PYTHON_BIN="${EDICT_PYTHON:-python3}"
INTERVAL="${1:-15}"
SCAN_INTERVAL="${2:-120}"
LOG="/tmp/sansheng_liubu_opencode_refresh.log"
PIDFILE="/tmp/sansheng_liubu_opencode_refresh.pid"
MAX_LOG_SIZE=$((10 * 1024 * 1024))
SCRIPT_TIMEOUT=30
DASHBOARD_PORT="${EDICT_DASHBOARD_PORT:-7891}"

if [[ -f "$PIDFILE" ]]; then
  OLD_PID=$(cat "$PIDFILE" 2>/dev/null || true)
  if [[ -n "$OLD_PID" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "已有 OpenCode 刷新循环运行中 (PID=$OLD_PID)，退出"
    exit 1
  fi
  rm -f "$PIDFILE"
fi
echo $$ > "$PIDFILE"

cleanup() {
  echo "$(date '+%H:%M:%S') [opencode-loop] 收到退出信号，清理中..." >> "$LOG"
  rm -f "$PIDFILE"
  exit 0
}
trap cleanup SIGINT SIGTERM EXIT

rotate_log() {
  if [[ -f "$LOG" ]] && (( $(stat -f%z "$LOG" 2>/dev/null || stat -c%s "$LOG" 2>/dev/null || echo 0) > MAX_LOG_SIZE )); then
    mv "$LOG" "${LOG}.1"
    echo "$(date '+%H:%M:%S') [opencode-loop] 日志已轮转" > "$LOG"
  fi
}

safe_run() {
  local script="$1"
  if command -v timeout &>/dev/null; then
    timeout "$SCRIPT_TIMEOUT" "$PYTHON_BIN" "$script" >> "$LOG" 2>&1 || {
      local rc=$?
      if [[ $rc -eq 124 ]]; then
        echo "$(date '+%H:%M:%S') [opencode-loop] 脚本超时(${SCRIPT_TIMEOUT}s): $script" >> "$LOG"
      fi
    }
  else
    "$PYTHON_BIN" "$script" >> "$LOG" 2>&1 || true
  fi
}

scan_scheduler() {
  "$PYTHON_BIN" - <<PY >> "$LOG" 2>&1 || true
import urllib.request
req = urllib.request.Request(
    "http://127.0.0.1:${DASHBOARD_PORT}/api/scheduler-scan",
    data=b'{"thresholdSec":180}',
    headers={"Content-Type": "application/json"},
    method="POST",
)
urllib.request.urlopen(req, timeout=5).read()
PY
}

echo "三省六部 OpenCode 数据刷新循环启动 (PID=$$)"
echo "   脚本目录: $SCRIPT_DIR"
echo "   间隔: ${INTERVAL}s"
echo "   巡检间隔: ${SCAN_INTERVAL}s"
echo "   日志: $LOG"

SCAN_COUNTER=0
while true; do
  rotate_log
  safe_run "$SCRIPT_DIR/sync_opencode_agents.py"
  safe_run "$SCRIPT_DIR/refresh_live_data.py"

  SCAN_COUNTER=$((SCAN_COUNTER + INTERVAL))
  if (( SCAN_COUNTER >= SCAN_INTERVAL )); then
    SCAN_COUNTER=0
    scan_scheduler
  fi

  sleep "$INTERVAL"
done
