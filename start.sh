#!/bin/bash
# ══════════════════════════════════════════════════════════════
# 三省六部 · 一键启动脚本
# 同时启动看板服务器 + 数据刷新循环
# ══════════════════════════════════════════════════════════════

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

resolve_python() {
  local candidate version major minor
  for candidate in "${EDICT_PYTHON:-}" python3.12 python3.11 python3.10 python3; do
    [ -n "$candidate" ] || continue
    command -v "$candidate" &>/dev/null || continue
    version=$("$candidate" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null) || continue
    major=${version%%.*}
    minor=${version#*.}
    if [ "$major" -gt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -ge 10 ]; }; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

PYTHON_BIN=$(resolve_python) || {
  echo -e "${RED}❌ 未找到可用的 Python 3.10+（当前项目实际需要 3.10+）${NC}"
  exit 1
}
export EDICT_PYTHON="$PYTHON_BIN"

# 确保 data 目录存在
mkdir -p "$REPO_DIR/data"

# 初始化必需的数据文件（如果不存在）
for f in live_status.json agent_config.json model_change_log.json sync_status.json; do
  [ ! -f "$REPO_DIR/data/$f" ] && echo '{}' > "$REPO_DIR/data/$f"
done
[ ! -f "$REPO_DIR/data/pending_model_changes.json" ] && echo '[]' > "$REPO_DIR/data/pending_model_changes.json"
[ ! -f "$REPO_DIR/data/tasks_source.json" ] && echo '[]' > "$REPO_DIR/data/tasks_source.json"
[ ! -f "$REPO_DIR/data/tasks.json" ] && echo '[]' > "$REPO_DIR/data/tasks.json"
[ ! -f "$REPO_DIR/data/officials.json" ] && echo '[]' > "$REPO_DIR/data/officials.json"
[ ! -f "$REPO_DIR/data/officials_stats.json" ] && echo '{}' > "$REPO_DIR/data/officials_stats.json"

cleanup() {
  echo ""
  echo -e "${YELLOW}正在关闭服务...${NC}"
  kill $SERVER_PID $LOOP_PID 2>/dev/null
  wait $SERVER_PID $LOOP_PID 2>/dev/null
  echo -e "${GREEN}✅ 已关闭${NC}"
  exit 0
}
trap cleanup SIGINT SIGTERM

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  🏛️  三省六部 · 服务启动中               ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
echo ""

# 启动数据刷新循环（后台）
if command -v openclaw &>/dev/null; then
  echo -e "${GREEN}▶ 启动数据刷新循环...${NC}"
  bash scripts/run_loop.sh &
  LOOP_PID=$!
else
  echo -e "${YELLOW}⚠️  未检测到 OpenClaw CLI，跳过数据刷新循环${NC}"
  echo -e "${YELLOW}   看板将以只读模式运行（使用已有数据）${NC}"
  LOOP_PID=""
fi

# 启动看板服务器
echo -e "${GREEN}▶ 启动看板服务器...${NC}"
echo -e "${GREEN}   使用 Python: ${PYTHON_BIN} ($($PYTHON_BIN --version 2>&1))${NC}"
"$PYTHON_BIN" dashboard/server.py &
SERVER_PID=$!

sleep 1
echo ""
echo -e "${GREEN}✅ 服务已启动！${NC}"
echo -e "   看板地址: ${BLUE}http://127.0.0.1:7891${NC}"
echo -e "   按 ${YELLOW}Ctrl+C${NC} 关闭所有服务"
echo ""

# 尝试自动打开浏览器
if command -v open &>/dev/null; then
  open http://127.0.0.1:7891
elif command -v xdg-open &>/dev/null; then
  xdg-open http://127.0.0.1:7891
fi

# 等待任一进程退出
wait $SERVER_PID $LOOP_PID 2>/dev/null
