# 三省六部 Windows 安装说明（简明版 + 详细版）

> 适用于 Windows 用户。本文默认你下载的是**已经包含 Windows 兼容修复**的版本，因此不需要再手动改 Python 文件。

---

# 一、最简单版本：照着做就能装

## 1. 下载项目
把项目放到你自己的 OpenClaw workspace 里，例如：

```text
C:\Users\<YOUR_USER>\.openclaw\workspace\skills\edict
```

> 你实际目录名可以不是 `edict`，但后面命令里的路径要对应修改。

---

## 2. 如果以前装过旧版本，先删除旧链接
如果你之前已经安装过旧版三省六部，请先检查并删除这些目录里的旧 `data` / `scripts` 链接：

```text
C:\Users\<YOUR_USER>\.openclaw\workspace-taizi
C:\Users\<YOUR_USER>\.openclaw\workspace-zhongshu
C:\Users\<YOUR_USER>\.openclaw\workspace-menxia
C:\Users\<YOUR_USER>\.openclaw\workspace-shangshu
C:\Users\<YOUR_USER>\.openclaw\workspace-hubu
C:\Users\<YOUR_USER>\.openclaw\workspace-libu
C:\Users\<YOUR_USER>\.openclaw\workspace-bingbu
C:\Users\<YOUR_USER>\.openclaw\workspace-xingbu
C:\Users\<YOUR_USER>\.openclaw\workspace-gongbu
C:\Users\<YOUR_USER>\.openclaw\workspace-libu_hr
C:\Users\<YOUR_USER>\.openclaw\workspace-zaochao
```

重点删除里面已有的：

- `data`
- `scripts`

如果不清理旧链接，第一次运行安装脚本时，可能会因为“链接已经存在”而失败。

---

## 3. 运行安装脚本
在 PowerShell 里进入项目目录：

```powershell
cd C:\Users\<YOUR_USER>\.openclaw\workspace\skills\edict
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

---

## 4. 安装后检查两件事

### A. 检查 agent / subagent 配置是否写进 `openclaw.json`
安装脚本正常情况下会写入，但建议你安装后自己确认一次。

如果没有正确写进去，可以参考本仓库附带的 `agents.json` 脱敏模板。使用时请先把其中的 `<YOUR_USER>` 替换成你自己的系统用户名，再复制到对应配置中。

### B. 检查 `tools.sessions.visibility = all`
安装脚本会尝试设置，但建议你手动确认一次。

如果没有生效，执行：

```powershell
openclaw config set tools.sessions.visibility all
```

---

## 5. 启动后台刷新循环
在 Git Bash / MINGW64 里运行：

```bash
cd ~/.openclaw/workspace/skills/edict/scripts
bash run_loop.sh
```

> 这个脚本负责后台持续刷新数据。

---

## 6. 启动 dashboard
在 PowerShell 里运行：

```powershell
cd C:\Users\<YOUR_USER>\.openclaw\workspace\skills\edict
python dashboard\server.py
```

然后浏览器打开：

```text
http://127.0.0.1:7891
```

---

# 二、安装完成后你应该看到什么

正常情况下：

- 面板可以打开
- `官员总览` 能显示三省六部官员信息
- `模型配置` 能显示 agent 列表
- 右上角 Gateway 状态正常
- 倒计时会持续刷新页面数据

---

# 三、详细说明

## 1. 为什么要先删旧链接

如果你以前已经安装过旧版本，那么：

- `workspace-*\data`
- `workspace-*\scripts`

很可能还指向旧仓库。

这时你再运行新的 `install.ps1`，第一次可能出现：

- symlink / junction 创建失败
- 安装脚本看起来跑完了，但实际 workspace 仍然连着旧版本

所以最稳妥的做法是：

## 先删旧链接，再运行安装脚本

---

## 2. 为什么安装后还要核对 agent / subagent 配置

在部分环境里，安装脚本可能没有把三省六部的 agent 配置完整落进 `openclaw.json`。

因此建议你安装后主动确认：

- `taizi`
- `zhongshu`
- `menxia`
- `shangshu`
- `hubu`
- `libu`
- `bingbu`
- `xingbu`
- `gongbu`
- `libu_hr`
- `zaochao`

这些 agent 是否都存在，且 `subagents.allowAgents` 是否正确。

如果缺失，可以直接参考本仓库附带的 `agents.json` 脱敏模板；使用前请先把 `<YOUR_USER>` 替换成你自己的系统用户名。

---

## 3. `agents.json` 是干什么用的

本仓库附带了一个脱敏版的：
```text
agents.json```

它保留了三省六部 agent 的配置结构，包括：
- `id`
- `name`
- `workspace`
- `agentDir`
- `subagents.allowAgents`

其中路径部分已经用 `<YOUR_USER>` 做了脱敏处理。

使用时请先把：

```text
<YOUR_USER>```

替换成你自己的 Windows 用户名，再复制到对应配置中。

---

## 4. 为什么还要确认 `tools.sessions.visibility = all`

这个设置会影响 session 工具可见性，对多 agent 协同很重要。

虽然安装脚本会尝试设置，但建议安装后自己再确认一次。

如果没生效，手动执行：

```powershell
openclaw config set tools.sessions.visibility all
```

---

## 5. 为什么还要跑 `run_loop.sh`

dashboard 右上角虽然有一个 5 秒倒计时，但它只是：

- 每 5 秒重新读取一次现有 API 数据

它并不会自动帮你在后台持续生成数据。

真正负责后台数据刷新的是：

```bash
bash run_loop.sh
```

它会持续执行同步脚本，更新：

- `live_status.json`
- `officials_stats.json`
- `agent_config.json`

所以：

- dashboard 倒计时 = **读数据**
- `run_loop.sh` = **产数据 / 刷数据**

Windows 下也建议正常运行 `run_loop.sh`。

---

## 6. 如果 dashboard 提示“请先启动服务器”怎么办

这句文案有时是误导性的。它不一定表示 `dashboard/server.py` 真没启动。

更常见的真实原因是：

- API 返回了空对象
- 读取到了旧仓库的数据
- 当前启动的不是你想要的那个 dashboard server

排查时建议直接访问：

```text
http://127.0.0.1:7891/api/officials-stats
http://127.0.0.1:7891/api/agent-config
http://127.0.0.1:7891/api/live-status
```

如果这三个接口能正常返回 JSON，说明 server 没问题。

---

## 7. 如果 dashboard 提示 Gateway 没启动怎么办

如果你使用的是本修复版，这个问题应该已经被修好。

之前在 Windows 上误报的原因是 dashboard 的 Gateway 检测逻辑偏 Linux。修复后已改为优先使用端口 / probe 检测。

所以如果你仍然看到 Gateway 未启动：

- 先确认自己现在运行的是修复后的 `dashboard/server.py`
- 再确认浏览器访问的不是旧的本地 server 进程

---

# 四、推荐的完整使用顺序

## 第一步
清理旧 `workspace-*` 里的 `data` / `scripts`

## 第二步
运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

## 第三步
检查：

- agent / subagent 配置
- `tools.sessions.visibility = all`

必要时可参考：

```text
agents.json
```

## 第四步
启动后台刷新循环：

```bash
bash run_loop.sh
```

## 第五步
启动 dashboard：

```powershell
python dashboard\server.py
```

---

# 五、一句话总结

## Windows 用户最稳的做法就是：
先清旧链接，再运行安装脚本；安装后检查 agent 配置和 `tools.sessions.visibility = all`；如有需要可参考 `agents.json` 脱敏模板并替换 `<YOUR_USER>`；最后启动 `run_loop.sh` 和 `dashboard/server.py`。


