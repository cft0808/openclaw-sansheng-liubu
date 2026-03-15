#!/bin/bash
# 三省六部 · 修复已安装实例的数据同步问题

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OC_HOME="$HOME/.openclaw"

echo "开始修复三省六部..."

# 修复所有 agent workspace
AGENTS="taizi zhongshu menxia shangshu hubu libu bingbu xingbu gongbu libu_hr zaochao"

for agent in $AGENTS; do
  ws="$OC_HOME/workspace-$agent"
  if [ -d "$ws" ]; then
    # 软链接 scripts
    rm -rf "$ws/scripts" 2>/dev/null
    ln -sf "$REPO_DIR/scripts" "$ws/scripts"
    # 软链接 data 文件
    mkdir -p "$ws/data"
    for f in tasks_source.json live_status.json agent_config.json officials_stats.json; do
      if [ -f "$REPO_DIR/data/$f" ]; then
        rm -f "$ws/data/$f"
        ln -sf "$REPO_DIR/data/$f" "$ws/data/$f"
      fi
    done
    echo "✅ 已修复: $agent"
  fi
done

# 修复 main workspace（如果存在）
ws_main="$OC_HOME/workspace"
if [ -d "$ws_main" ]; then
  rm -rf "$ws_main/scripts" 2>/dev/null
  ln -sf "$REPO_DIR/scripts" "$ws_main/scripts"
  mkdir -p "$ws_main/data"
  for f in tasks_source.json live_status.json agent_config.json officials_stats.json; do
    if [ -f "$REPO_DIR/data/$f" ]; then
      rm -f "$ws_main/data/$f"
      ln -sf "$REPO_DIR/data/$f" "$ws_main/data/$f"
    fi
  done
  echo "✅ 已修复: main"
fi

echo ""
echo "修复完成！请刷新看板："
echo "  python3 $REPO_DIR/scripts/refresh_live_data.py"
