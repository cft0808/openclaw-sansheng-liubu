#!/bin/bash
# 三省六部 · OpenCode 一键启动

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

DASHBOARD_HOST="${EDICT_DASHBOARD_HOST:-127.0.0.1}"
DASHBOARD_PORT="${EDICT_DASHBOARD_PORT:-7891}"
OPENCODE_HOST="${OPENCODE_HOST:-127.0.0.1}"
OPENCODE_PORT="${OPENCODE_PORT:-4096}"
OPENCODE_SERVER_URL="${OPENCODE_SERVER_URL:-http://${OPENCODE_HOST}:${OPENCODE_PORT}}"
OPENCODE_MODEL="${OPENCODE_MODEL:-opencode/deepseek-v4-flash-free}"
PIDDIR="$REPO_DIR/.pids"
LOGDIR="$REPO_DIR/logs"
SERVER_PIDFILE="$PIDDIR/server.pid"
LOOP_PIDFILE="$PIDDIR/loop.pid"
OPENCODE_PIDFILE="$PIDDIR/opencode.pid"
SERVER_LOG="$LOGDIR/server.log"
LOOP_LOG="$LOGDIR/loop.log"
OPENCODE_LOG="$LOGDIR/opencode.log"

resolve_python() {
  local candidate version major minor
  for candidate in "${EDICT_PYTHON:-}" python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
    [ -n "$candidate" ] || continue
    command -v "$candidate" &>/dev/null || continue
    version=$("$candidate" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null) || continue
    major=${version%%.*}
    minor=${version#*.}
    if [ "$major" -gt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -ge 10 ]; }; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

resolve_opencode() {
  if [[ -n "${OPENCODE_BIN:-}" && -x "${OPENCODE_BIN:-}" ]]; then
    echo "$OPENCODE_BIN"
    return 0
  fi
  if command -v opencode &>/dev/null; then
    command -v opencode
    return 0
  fi
  if [[ -x "$HOME/.opencode/bin/opencode" ]]; then
    echo "$HOME/.opencode/bin/opencode"
    return 0
  fi
  return 1
}

http_ok() {
  "$PYTHON_BIN" - "$1" <<'PY' >/dev/null 2>&1
import sys
import urllib.request
try:
    r = urllib.request.urlopen(sys.argv[1], timeout=3)
    raise SystemExit(0 if 200 <= r.status < 500 else 1)
except Exception:
    raise SystemExit(1)
PY
}

opencode_agents_ok() {
  "$PYTHON_BIN" - "$OPENCODE_SERVER_URL" <<'PY' >/dev/null 2>&1
import json
import sys
import urllib.request
try:
    url = sys.argv[1].rstrip("/") + "/agent"
    data = json.loads(urllib.request.urlopen(url, timeout=5).read().decode("utf-8"))
    names = {item.get("name") for item in data if isinstance(item, dict)}
    raise SystemExit(0 if {"taizi", "zhongshu", "shangshu"}.issubset(names) else 1)
except Exception:
    raise SystemExit(1)
PY
}

write_opencode_pid_from_port() {
  command -v lsof &>/dev/null || return 0
  local pid
  pid=$(lsof -tiTCP:"$OPENCODE_PORT" -sTCP:LISTEN 2>/dev/null | head -n 1 || true)
  if [[ "$pid" =~ ^[0-9]+$ ]]; then
    echo "$pid" > "$OPENCODE_PIDFILE"
  fi
  return 0
}

stop_pidfile() {
  local pidfile="$1"
  if [[ -f "$pidfile" ]]; then
    local pid
    pid=$(cat "$pidfile" 2>/dev/null || true)
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      for _ in $(seq 1 10); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.3
      done
    fi
    rm -f "$pidfile"
  fi
}

stop_opencode_on_port() {
  command -v lsof &>/dev/null || return 0
  local pids cmd
  pids=$(lsof -tiTCP:"$OPENCODE_PORT" -sTCP:LISTEN 2>/dev/null || true)
  for pid in $pids; do
    cmd=$(ps -p "$pid" -o command= 2>/dev/null || true)
    if [[ "$cmd" == *"opencode"* ]]; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  return 0
}

stop_screen_session() {
  local name="$1"
  if command -v screen &>/dev/null && screen -ls 2>/dev/null | grep -q "[.]${name}[[:space:]]"; then
    screen -S "$name" -X quit >/dev/null 2>&1 || true
  fi
}

start_opencode_server() {
  if command -v screen &>/dev/null; then
    screen -dmS edict-opencode bash -lc "cd \"$REPO_DIR\" && echo \$\$ > \"$OPENCODE_PIDFILE\" && exec \"$OPENCODE_BIN_RESOLVED\" serve --hostname \"$OPENCODE_HOST\" --port \"$OPENCODE_PORT\" >> \"$OPENCODE_LOG\" 2>&1"
  else
    nohup "$OPENCODE_BIN_RESOLVED" serve --hostname "$OPENCODE_HOST" --port "$OPENCODE_PORT" \
      >> "$OPENCODE_LOG" 2>&1 &
    echo $! > "$OPENCODE_PIDFILE"
  fi
}

start_dashboard_server() {
  if command -v screen &>/dev/null; then
    screen -dmS edict-server bash -lc "cd \"$REPO_DIR\" && echo \$\$ > \"$SERVER_PIDFILE\" && exec env EDICT_RUNTIME=opencode EDICT_AGENT_RUNTIME=opencode OPENCODE_BIN=\"$OPENCODE_BIN_RESOLVED\" OPENCODE_SERVER_URL=\"$OPENCODE_SERVER_URL\" OPENCODE_MODEL=\"$OPENCODE_MODEL\" \"$PYTHON_BIN\" \"$REPO_DIR/dashboard/server.py\" --host \"$DASHBOARD_HOST\" --port \"$DASHBOARD_PORT\" >> \"$SERVER_LOG\" 2>&1"
  else
    nohup env EDICT_RUNTIME=opencode EDICT_AGENT_RUNTIME=opencode \
      OPENCODE_BIN="$OPENCODE_BIN_RESOLVED" OPENCODE_SERVER_URL="$OPENCODE_SERVER_URL" OPENCODE_MODEL="$OPENCODE_MODEL" \
      "$PYTHON_BIN" "$REPO_DIR/dashboard/server.py" --host "$DASHBOARD_HOST" --port "$DASHBOARD_PORT" \
      >> "$SERVER_LOG" 2>&1 &
    echo $! > "$SERVER_PIDFILE"
  fi
}

start_refresh_loop() {
  if command -v screen &>/dev/null; then
    screen -dmS edict-loop bash -lc "cd \"$REPO_DIR\" && echo \$\$ > \"$LOOP_PIDFILE\" && exec env EDICT_RUNTIME=opencode EDICT_AGENT_RUNTIME=opencode EDICT_PYTHON=\"$PYTHON_BIN\" OPENCODE_BIN=\"$OPENCODE_BIN_RESOLVED\" OPENCODE_SERVER_URL=\"$OPENCODE_SERVER_URL\" OPENCODE_MODEL=\"$OPENCODE_MODEL\" bash \"$REPO_DIR/scripts/run_loop_opencode.sh\" >> \"$LOOP_LOG\" 2>&1"
  else
    nohup env EDICT_RUNTIME=opencode EDICT_AGENT_RUNTIME=opencode EDICT_PYTHON="$PYTHON_BIN" \
      OPENCODE_BIN="$OPENCODE_BIN_RESOLVED" OPENCODE_SERVER_URL="$OPENCODE_SERVER_URL" OPENCODE_MODEL="$OPENCODE_MODEL" \
      bash "$REPO_DIR/scripts/run_loop_opencode.sh" \
      >> "$LOOP_LOG" 2>&1 &
    echo $! > "$LOOP_PIDFILE"
  fi
}

stop_repo_dashboard_on_port() {
  command -v lsof &>/dev/null || return 0
  local pids cmd
  pids=$(lsof -tiTCP:"$DASHBOARD_PORT" -sTCP:LISTEN 2>/dev/null || true)
  for pid in $pids; do
    cmd=$(ps -p "$pid" -o command= 2>/dev/null || true)
    if [[ "$cmd" == *"$REPO_DIR/dashboard/server.py"* || "$cmd" == *"dashboard/server.py"* ]]; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  return 0
}

stop_repo_loops() {
  command -v lsof &>/dev/null || return 0
  local pid cmd cwd
  while read -r pid cmd; do
    [[ -n "${pid:-}" ]] || continue
    if [[ "$cmd" != *"scripts/run_loop.sh"* && "$cmd" != *"scripts/run_loop_opencode.sh"* ]]; then
      continue
    fi
    cwd=$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1 || true)
    if [[ "$cwd" == "$REPO_DIR" ]]; then
      kill "$pid" 2>/dev/null || true
    fi
  done < <(ps ax -o pid=,command=)
  return 0
}

PYTHON_BIN=$(resolve_python) || {
  echo -e "${RED}未找到 Python 3.10+${NC}"
  exit 1
}
OPENCODE_BIN_RESOLVED=$(resolve_opencode) || {
  echo -e "${RED}未找到 OpenCode CLI。请先安装 opencode，或设置 OPENCODE_BIN。${NC}"
  exit 1
}

export EDICT_PYTHON="$PYTHON_BIN"
export EDICT_RUNTIME=opencode
export EDICT_AGENT_RUNTIME=opencode
export OPENCODE_BIN="$OPENCODE_BIN_RESOLVED"
export OPENCODE_SERVER_URL
export OPENCODE_MODEL

mkdir -p "$PIDDIR" "$LOGDIR" "$REPO_DIR/data"
for f in live_status.json agent_config.json model_change_log.json sync_status.json; do
  [[ -f "$REPO_DIR/data/$f" ]] || echo '{}' > "$REPO_DIR/data/$f"
done
[[ -f "$REPO_DIR/data/pending_model_changes.json" ]] || echo '[]' > "$REPO_DIR/data/pending_model_changes.json"
[[ -f "$REPO_DIR/data/tasks_source.json" ]] || echo '[]' > "$REPO_DIR/data/tasks_source.json"
[[ -f "$REPO_DIR/data/tasks.json" ]] || echo '[]' > "$REPO_DIR/data/tasks.json"
[[ -f "$REPO_DIR/data/officials.json" ]] || echo '[]' > "$REPO_DIR/data/officials.json"
[[ -f "$REPO_DIR/data/officials_stats.json" ]] || echo '{}' > "$REPO_DIR/data/officials_stats.json"

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  三省六部 · OpenCode 模式启动中          ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "Python:  ${GREEN}${PYTHON_BIN}${NC}"
echo -e "OpenCode: ${GREEN}${OPENCODE_BIN_RESOLVED}${NC}"
echo -e "Model:    ${GREEN}${OPENCODE_MODEL}${NC}"

echo -e "${GREEN}▶ 同步 OpenCode agent 配置...${NC}"
"$PYTHON_BIN" "$REPO_DIR/scripts/sync_opencode_agents.py"

if http_ok "${OPENCODE_SERVER_URL}/doc" && opencode_agents_ok; then
  write_opencode_pid_from_port
  echo -e "${GREEN}▶ 复用已运行的 OpenCode server: ${OPENCODE_SERVER_URL}${NC}"
else
  if http_ok "${OPENCODE_SERVER_URL}/doc"; then
    echo -e "${YELLOW}⚠️  当前 ${OPENCODE_SERVER_URL} 不是本项目的 OpenCode server，正在切换...${NC}"
    stop_screen_session edict-opencode
    stop_opencode_on_port
    stop_pidfile "$OPENCODE_PIDFILE"
    sleep 0.5
  fi
  echo -e "${GREEN}▶ 启动 OpenCode server...${NC}"
  stop_screen_session edict-opencode
  start_opencode_server
  for _ in $(seq 1 20); do
    http_ok "${OPENCODE_SERVER_URL}/doc" && break
    sleep 0.5
  done
  if ! http_ok "${OPENCODE_SERVER_URL}/doc" || ! opencode_agents_ok; then
    echo -e "${RED}OpenCode server 启动失败，请查看日志: ${OPENCODE_LOG}${NC}"
    exit 1
  fi
  write_opencode_pid_from_port
fi

echo -e "${GREEN}▶ 切换看板到 OpenCode 运行时...${NC}"
stop_pidfile "$SERVER_PIDFILE"
stop_pidfile "$LOOP_PIDFILE"
stop_screen_session edict-server
stop_screen_session edict-loop
stop_repo_dashboard_on_port
stop_repo_loops
rm -f /tmp/sansheng_liubu_opencode_refresh.pid
sleep 0.5

start_dashboard_server
start_refresh_loop

for _ in $(seq 1 20); do
  http_ok "http://${DASHBOARD_HOST}:${DASHBOARD_PORT}/healthz" && break
  sleep 0.5
done

if ! http_ok "http://${DASHBOARD_HOST}:${DASHBOARD_PORT}/healthz"; then
  echo -e "${RED}看板启动失败，请查看日志: ${SERVER_LOG}${NC}"
  exit 1
fi

echo ""
echo -e "${GREEN}✅ OpenCode 模式已启动${NC}"
echo -e "   看板:     ${BLUE}http://${DASHBOARD_HOST}:${DASHBOARD_PORT}${NC}"
echo -e "   OpenCode: ${BLUE}${OPENCODE_SERVER_URL}${NC}"
echo -e "   日志:     ${BLUE}${LOGDIR}${NC}"

if command -v open &>/dev/null; then
  open "http://${DASHBOARD_HOST}:${DASHBOARD_PORT}" >/dev/null 2>&1 || true
elif command -v xdg-open &>/dev/null; then
  xdg-open "http://${DASHBOARD_HOST}:${DASHBOARD_PORT}" >/dev/null 2>&1 || true
fi
