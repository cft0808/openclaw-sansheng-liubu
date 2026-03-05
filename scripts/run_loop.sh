#!/bin/bash
# 三省六部 · 数据刷新循环
# 用法: ./run_loop.sh [间隔秒数]  (默认 15)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INTERVAL="${1:-15}"
LOG="/tmp/sansheng_liubu_refresh.log"
PIDFILE="/tmp/sansheng_liubu_refresh.pid"
MAX_LOG_SIZE=$((10 * 1024 * 1024))  # 10MB

# ── 单实例保护 ──
if [[ -f "$PIDFILE" ]]; then
  OLD_PID=$(cat "$PIDFILE" 2>/dev/null)
  if kill -0 "$OLD_PID" 2>/dev/null; then
    echo "❌ 已有实例运行中 (PID=$OLD_PID)，退出"
    exit 1
  fi
  rm -f "$PIDFILE"
fi
echo $$ > "$PIDFILE"

# ── 优雅退出 ──
cleanup() {
  echo "$(date '+%H:%M:%S') [loop] 收到退出信号，清理中..." >> "$LOG"
  rm -f "$PIDFILE"
  exit 0
}
trap cleanup SIGINT SIGTERM EXIT

# ── 日志轮转 ──
rotate_log() {
  if [[ -f "$LOG" ]] && (( $(stat -f%z "$LOG" 2>/dev/null || stat -c%s "$LOG" 2>/dev/null || echo 0) > MAX_LOG_SIZE )); then
    mv "$LOG" "${LOG}.1"
    echo "$(date '+%H:%M:%S') [loop] 日志已轮转" > "$LOG"
  fi
}

SCAN_INTERVAL="${2:-120}"  # 巡检间隔(秒), 默认 120
SCAN_COUNTER=0

echo "🏛️  三省六部数据刷新循环启动 (PID=$$)"
echo "   脚本目录: $SCRIPT_DIR"
echo "   间隔: ${INTERVAL}s"
echo "   巡检间隔: ${SCAN_INTERVAL}s"
echo "   日志: $LOG"
echo "   PID文件: $PIDFILE"
echo "   按 Ctrl+C 停止"

while true; do
  rotate_log
  python3 "$SCRIPT_DIR/sync_from_openclaw_runtime.py" >> "$LOG" 2>&1 || true
  python3 "$SCRIPT_DIR/sync_agent_config.py"          >> "$LOG" 2>&1 || true
  python3 "$SCRIPT_DIR/apply_model_changes.py"        >> "$LOG" 2>&1 || true
  python3 "$SCRIPT_DIR/sync_officials_stats.py"       >> "$LOG" 2>&1 || true
  python3 "$SCRIPT_DIR/refresh_live_data.py"          >> "$LOG" 2>&1 || true

  # 太子通讯督办：确保关键任务每20分钟至少回报一次（写入看板 progress）。
  # 注意：这里只对少数旗舰任务启用，避免刷屏。
  python3 "$SCRIPT_DIR/taizi_20min_ping.py" --task JJC-20260305-002 --max-minutes 20 \
    --now "强制20分钟同步：正在督办RouteA vNext交付与验收" \
    --plan "收集各部回填🔄|催办未回填项🔄|汇总回奏皇上" >> "$LOG" 2>&1 || true
  python3 "$SCRIPT_DIR/taizi_20min_ping.py" --task JJC-20260305-003 --max-minutes 20 \
    --now "强制20分钟同步：正在复盘同步失效根因并落地机制" \
    --plan "收集事实与时间线🔄|定位根因与对策🔄|落地机制并验收" >> "$LOG" 2>&1 || true

  # 定期巡检：检测卡住的任务并自动重试
  SCAN_COUNTER=$((SCAN_COUNTER + INTERVAL))
  if (( SCAN_COUNTER >= SCAN_INTERVAL )); then
    SCAN_COUNTER=0
    curl -s -X POST http://127.0.0.1:7891/api/scheduler-scan \
      -H 'Content-Type: application/json' -d '{"thresholdSec":180}' >> "$LOG" 2>&1 || true
  fi

  sleep "$INTERVAL"
done
