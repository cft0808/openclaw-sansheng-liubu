#!/bin/bash
# ══════════════════════════════════════════════════════════════
# 三省六部 · OpenClaw Multi-Agent System 一键卸载脚本
# ══════════════════════════════════════════════════════════════
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OC_HOME="$HOME/.openclaw"
OC_CFG="$OC_HOME/openclaw.json"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

banner() {
  echo ""
  echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
  echo -e "${BLUE}║  🏛️  三省六部 · 卸载向导                  ║${NC}"
  echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
  echo ""
}

log()   { echo -e "${GREEN}✅ $1${NC}"; }
warn()  { echo -e "${YELLOW}⚠️  $1${NC}"; }
info()  { echo -e "${BLUE}ℹ️  $1${NC}"; }

# ── Step 0: 确认 ────────────────────────────────────────────
check_env() {
  info "检查环境..."
  if ! command -v python3 &>/dev/null; then
    warn "未找到 python3，跳过清理配置项"
  fi

  echo ""
  echo -e "${YELLOW}确定要卸载「三省六部」系统并清理相关 Agent 数据吗？${NC}"
  read -p "(y/N) " -r
  echo
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    info "已取消卸载"
    exit 0
  fi
}

# ── Step 1: 停止正在运行的服务 ────────────────────────────────
stop_services() {
  info "尝试停止相关进程..."

  if pgrep -f "scripts/run_loop.sh" > /dev/null 2>&1; then
    pkill -f "scripts/run_loop.sh" || warn "无法自动停止 run_loop.sh"
    log "已尝试停止 run_loop.sh"
  fi

  if pgrep -f "python.*dashboard/server.py" > /dev/null 2>&1; then
    pkill -f "python.*dashboard/server.py" || warn "无法自动停止 dashboard/server.py"
    log "已尝试停止 dashboard/server.py"
  fi
}

# ── Step 2: 清理 OpenClaw 注册配置 ──────────────────────────────
unregister_agents() {
  info "从 OpenClaw 移除三省六部 Agents 注册信息..."

  if [ ! -f "$OC_CFG" ]; then
    warn "未找到 openclaw.json，跳过配置清理"
    return
  fi

  cp "$OC_CFG" "$OC_CFG.bak.pre-uninstall-$(date +%Y%m%d-%H%M%S)"
  log "已备份当前配置"

  python3 << 'PYEOF'
import json, pathlib

cfg_path = pathlib.Path.home() / '.openclaw' / 'openclaw.json'
if not cfg_path.exists():
    print("  openclaw.json 不存在。")
    exit(0)

try:
    cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
except Exception as e:
    print(f"  解析 openclaw.json 失败: {e}")
    exit(1)

AGENTS_TO_REMOVE = {
    "taizi", "zhongshu", "menxia", "shangshu",
    "hubu", "libu", "bingbu", "xingbu", "gongbu",
    "libu_hr", "zaochao"
}

agents_list = cfg.get('agents', {}).get('list', [])
new_list = [a for a in agents_list if a.get('id') not in AGENTS_TO_REMOVE]
removed_count = len(agents_list) - len(new_list)

if 'agents' in cfg:
    cfg['agents']['list'] = new_list
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"  成功移除了 {removed_count} 个 Agent 的注册信息")
PYEOF

  log "注册信息清理完成"
}

# ── Step 3: 清除 Workspace 目录 ─────────────────────────────────
remove_workspaces() {
  info "清除 Agent Workspace 目录..."

  AGENTS=(taizi zhongshu menxia shangshu hubu libu bingbu xingbu gongbu libu_hr zaochao)
  removed=0
  for agent in "${AGENTS[@]}"; do
    ws="$OC_HOME/workspace-$agent"
    if [ -d "$ws" ]; then
      rm -rf "$ws"
      removed=$((removed+1))
    fi
  done

  log "成功清理了 $removed 个 Workspace 目录"
}

# ── Step 4: 清除本地数据缓存 ────────────────────────────────────
remove_data() {
  info "清除本地 data 缓存..."

  echo -e "${YELLOW}是否需要删除项目内的 data 目录及已生成的数据？${NC}"
  read -p "(y/N) " -r
  echo
  if [[ $REPLY =~ ^[Yy]$ ]]; then
    if [ -d "$REPO_DIR/data" ]; then
      rm -rf "$REPO_DIR/data"
      log "已删除 $REPO_DIR/data"
    else
      warn "$REPO_DIR/data 不存在"
    fi
  else
    info "保留原有 data 目录"
  fi
}

# ── Step 5: 重启 Gateway ────────────────────────────────────────
restart_gateway() {
  info "重启 OpenClaw Gateway 以应用配置..."
  if command -v openclaw &>/dev/null; then
    if openclaw gateway restart 2>/dev/null; then
      log "Gateway 重启成功"
    else
      warn "Gateway 重启失败，请手动重启：openclaw gateway restart"
    fi
  else
    warn "未找到 openclaw 命令行工具，跳过重启 Gateway"
  fi
}

# ── Main ────────────────────────────────────────────────────
banner
check_env
stop_services
unregister_agents
remove_workspaces
remove_data
restart_gateway

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  ✅  三省六部卸载完成！                          ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════╝${NC}"
echo ""
