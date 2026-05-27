#!/bin/bash
# ══════════════════════════════════════════════════════════════
# 三省六部 · 统一服务管理脚本
# 用法: ./edict.sh {start|opencode|stop|status|restart|logs}
# ══════════════════════════════════════════════════════════════

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDDIR="$REPO_DIR/.pids"
LOGDIR="$REPO_DIR/logs"

SERVER_PIDFILE="$PIDDIR/server.pid"
LOOP_PIDFILE="$PIDDIR/loop.pid"
SERVER_LOG="$LOGDIR/server.log"
LOOP_LOG="$LOGDIR/loop.log"

# 可通过环境变量覆盖的配置
DASHBOARD_HOST="${EDICT_DASHBOARD_HOST:-127.0.0.1}"
DASHBOARD_PORT="${EDICT_DASHBOARD_PORT:-7891}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

# ── 工具函数 ──

_ensure_dirs() {
  mkdir -p "$PIDDIR" "$LOGDIR" "$REPO_DIR/data"
  # 初始化必需的数据文件
  for f in live_status.json agent_config.json model_change_log.json sync_status.json; do
    [[ -f "$REPO_DIR/data/$f" ]] || echo '{}' > "$REPO_DIR/data/$f"
  done
  [[ -f "$REPO_DIR/data/pending_model_changes.json" ]] || echo '[]' > "$REPO_DIR/data/pending_model_changes.json"
  [[ -f "$REPO_DIR/data/tasks_source.json" ]] || echo '[]' > "$REPO_DIR/data/tasks_source.json"
  [[ -f "$REPO_DIR/data/tasks.json" ]] || echo '[]' > "$REPO_DIR/data/tasks.json"
  [[ -f "$REPO_DIR/data/officials.json" ]] || echo '[]' > "$REPO_DIR/data/officials.json"
  [[ -f "$REPO_DIR/data/officials_stats.json" ]] || echo '{}' > "$REPO_DIR/data/officials_stats.json"
}

_is_running() {
  local pidfile="$1"
  if [[ -f "$pidfile" ]]; then
    local pid
    pid=$(cat "$pidfile" 2>/dev/null)
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
    # PID 文件存在但进程已死，清理
    rm -f "$pidfile"
  fi
  return 1
}

_get_pid() {
  local pidfile="$1"
  if [[ -f "$pidfile" ]]; then
    cat "$pidfile" 2>/dev/null
  fi
}

# ── 启动 ──

do_start() {
  _ensure_dirs

  if ! command -v python3 &>/dev/null; then
    echo -e "${RED}❌ 未找到 python3，请先安装 Python 3.9+${NC}"
    exit 1
  fi

  echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
  echo -e "${BLUE}║  🏛️  三省六部 · 服务启动中               ║${NC}"
  echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
  echo ""

  # 检查是否已在运行
  local already=0
  if _is_running "$SERVER_PIDFILE"; then
    echo -e "${YELLOW}⚠️  看板服务器已在运行 (PID=$(_get_pid "$SERVER_PIDFILE"))${NC}"
    already=$((already+1))
  fi
  if _is_running "$LOOP_PIDFILE"; then
    echo -e "${YELLOW}⚠️  数据刷新循环已在运行 (PID=$(_get_pid "$LOOP_PIDFILE"))${NC}"
    already=$((already+1))
  fi
  if [[ $already -eq 2 ]]; then
    echo -e "${YELLOW}所有服务均已运行，如需重启请用: $0 restart${NC}"
    return 0
  fi

  # 启动数据刷新循环（后台）
  if ! _is_running "$LOOP_PIDFILE"; then
    if command -v openclaw &>/dev/null; then
      echo -e "${GREEN}▶ 启动数据刷新循环...${NC}"
      nohup bash "$REPO_DIR/scripts/run_loop.sh" >> "$LOOP_LOG" 2>&1 &
      echo $! > "$LOOP_PIDFILE"
      echo -e "  PID=$(_get_pid "$LOOP_PIDFILE")  日志: ${BLUE}$LOOP_LOG${NC}"
    else
      echo -e "${YELLOW}⚠️  未检测到 OpenClaw CLI，跳过数据刷新循环${NC}"
      echo -e "${YELLOW}   看板将以只读模式运行（使用已有数据）${NC}"
    fi
  fi

  # 启动看板服务器（后台）
  if ! _is_running "$SERVER_PIDFILE"; then
    echo -e "${GREEN}▶ 启动看板服务器...${NC}"
    nohup python3 "$REPO_DIR/dashboard/server.py" \
      --host "$DASHBOARD_HOST" --port "$DASHBOARD_PORT" \
      >> "$SERVER_LOG" 2>&1 &
    echo $! > "$SERVER_PIDFILE"
    echo -e "  PID=$(_get_pid "$SERVER_PIDFILE")  日志: ${BLUE}$SERVER_LOG${NC}"
  fi

  sleep 1
  echo ""
  if _is_running "$SERVER_PIDFILE"; then
    echo -e "${GREEN}✅ 服务已启动！${NC}"
    echo -e "   看板地址: ${BLUE}http://${DASHBOARD_HOST}:${DASHBOARD_PORT}${NC}"
  else
    echo -e "${RED}❌ 看板服务器启动失败，请查看日志: $SERVER_LOG${NC}"
    exit 1
  fi
}

# ── 停止 ──

do_stop() {
  echo -e "${YELLOW}正在关闭服务...${NC}"
  local stopped=0

  for label_pid in "看板服务器:$SERVER_PIDFILE" "数据刷新循环:$LOOP_PIDFILE"; do
    local label="${label_pid%%:*}"
    local pidfile="${label_pid#*:}"
    if _is_running "$pidfile"; then
      local pid
      pid=$(_get_pid "$pidfile")
      kill "$pid" 2>/dev/null
      # 等待最多 5 秒
      for _ in $(seq 1 10); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.5
      done
      # 如果还在运行，强制 kill
      if kill -0 "$pid" 2>/dev/null; then
        kill -9 "$pid" 2>/dev/null
      fi
      rm -f "$pidfile"
      echo -e "  ✅ ${label} (PID=$pid) 已停止"
      stopped=$((stopped+1))
    fi
  done

  if [[ $stopped -eq 0 ]]; then
    echo -e "${YELLOW}  没有正在运行的服务${NC}"
  else
    echo -e "${GREEN}✅ 所有服务已关闭${NC}"
  fi
}

# ── 状态 ──

do_status() {
  echo -e "${BLUE}🏛️  三省六部 · 服务状态${NC}"
  echo ""

  for label_pid in "看板服务器:$SERVER_PIDFILE" "数据刷新循环:$LOOP_PIDFILE"; do
    local label="${label_pid%%:*}"
    local pidfile="${label_pid#*:}"
    if _is_running "$pidfile"; then
      local pid
      pid=$(_get_pid "$pidfile")
      echo -e "  ${GREEN}●${NC} ${label}  PID=$pid  ${GREEN}运行中${NC}"
    else
      echo -e "  ${RED}○${NC} ${label}  ${RED}未运行${NC}"
    fi
  done

  echo ""
  # 如果看板在运行，尝试 healthz
  if _is_running "$SERVER_PIDFILE"; then
    local health
    if health=$(python3 -c "
import urllib.request, json, sys
try:
    r = urllib.request.urlopen('http://${DASHBOARD_HOST}:${DASHBOARD_PORT}/healthz', timeout=3)
    d = json.loads(r.read())
    print('healthy' if d.get('status')=='ok' else 'unhealthy')
except Exception:
    print('unreachable')
" 2>/dev/null); then
      case "$health" in
        healthy)    echo -e "  健康检查: ${GREEN}✅ 正常${NC}" ;;
        unhealthy)  echo -e "  健康检查: ${YELLOW}⚠️  异常${NC}" ;;
        *)          echo -e "  健康检查: ${RED}❌ 无法连接${NC}" ;;
      esac
    fi
    echo -e "  看板地址: ${BLUE}http://${DASHBOARD_HOST}:${DASHBOARD_PORT}${NC}"
  fi
}

# ── 日志 ──

do_logs() {
  local target="${1:-all}"
  case "$target" in
    server)  tail -f "$SERVER_LOG" ;;
    loop)    tail -f "$LOOP_LOG" ;;
    all)     tail -f "$SERVER_LOG" "$LOOP_LOG" ;;
    *)       echo "用法: $0 logs [server|loop|all]"; exit 1 ;;
  esac
}

# ── 主入口 ──

case "${1:-}" in
  start)   do_start ;;
  opencode) bash "$REPO_DIR/scripts/start_opencode.sh" ;;
  stop)    do_stop ;;
  restart) do_stop; sleep 1; do_start ;;
  status)  do_status ;;
  logs)    do_logs "${2:-all}" ;;
  *)
    echo "用法: $0 {start|opencode|stop|restart|status|logs}"
    echo ""
    echo "命令:"
    echo "  start    启动所有服务（看板 + 数据刷新）"
    echo "  opencode 切换并启动 OpenCode 模式"
    echo "  stop     停止所有服务"
    echo "  restart  重启所有服务"
    echo "  status   查看运行状态"
    echo "  logs     查看日志 (logs [server|loop|all])"
    echo ""
    echo "环境变量:"
    echo "  EDICT_DASHBOARD_HOST  监听地址 (默认: 127.0.0.1)"
    echo "  EDICT_DASHBOARD_PORT  监听端口 (默认: 7891)"
    exit 1
    ;;
esac
