#!/bin/bash
# 三省六部 · 修复已安装实例的数据同步问题

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" \&\& pwd)"
OC_HOME="$HOME/.openclaw"

echo "开始修复三省六部..."

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

echo ""
echo "修复完成！请刷新看板："
echo "  python3 $REPO_DIR/scripts/refresh_live_data.py"
