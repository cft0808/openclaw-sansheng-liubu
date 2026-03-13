# 🚀 Quick Start Guide

> Start from scratch and build your AI collaboration system for three provinces and six departments in 5 minutes

---

## Step 1: Install OpenClaw

Three Provinces and Six Departments run based on [OpenClaw](https://openclaw.ai), please install it first:

```bash
#macOS
brew install openclaw

# Or download the installation package
# HTTPS://open claw.love/download
```

Initialize after installation is complete:

```bash
openclaw init
```

## Step 2: Clone and install Sanshengliubu

```bash
git clone https://github.com/cft0808/edict.git
cd edict
chmod +x install.sh && ./install.sh
```

The installation script will be completed automatically:
- ✅ Create 12 Agent Workspaces (`~/.openclaw/workspace-*`)
- ✅ Write to the SOUL.md personality files of each province and department
- ✅ Register Agent and permission matrix to `openclaw.json`
- ✅ Configure decree data cleaning rules
- ✅ Build React frontend to `dashboard/dist/` (requires Node.js 18+)
- ✅Initialize data directory
- ✅ Perform first data synchronization
- ✅ Restart Gateway to make the configuration take effect

## Step 3: Configure message channels

Configure the message channel (Feishu/Telegram/Signal) in OpenClaw, and set the `taizi` (Prince) Agent as the will entrance. Prince will automatically sort chats and instructions. Instruction messages will be forwarded to Zhongshu Province after refining their titles.

```bash
# View current channel
openclaw channels list

# Add Feishu channel (the entrance is set to Prince)
openclaw channels add --type feishu --agent taizi
```

Refer to the OpenClaw documentation: https://docs.openclaw.ai/channels

## Step 4: Start the service

```bash
# Terminal 1: Data refresh cycle (synchronized every 15 seconds)
bash scripts/run_loop.sh

# Terminal 2: Kanban server
python3 dashboard/server.py

# Open browser
open http://127.0.0.1:7891
```

> 💡 **Tip**: `run_loop.sh` automatically synchronizes data every 15 seconds. Can be run in the background using `&`.

> 💡 **Kanban works out of the box**: `server.py` embeds `dashboard/dashboard.html`, no additional build required. The Docker image includes a pre-built React frontend.

## Step 5: Send the first message

Send tasks through message channels (Prince will automatically recognize and forward to Zhongshu Province):

```
Please help me write a text classifier in Python:
1. Using scikit-learn
2. Support multiple categories
3. Output confusion matrix
4. Write complete documentation
```

## Step 6: Observe the execution process

Open the Kanban board http://127.0.0.1:7891

1. **📋 Will Board** — Observe the flow of tasks between states
2. **🔭 Provincial and Department Scheduling** — Check the work distribution of each department
3. **📜 Memorial Pavilion** — After the task is completed, it will be automatically archived as a memorial

Task flow path:
```
Receipt → Sorting by Prince → Zhongshu Planning → Review by the door → Distributed → Under implementation → Completed
```

---

## 🎯 Advanced usage

### Use the edict template

> Kanban → 📜 Purpose Library → Select Template → Fill in Parameters → Issue Purpose

9 preset templates: Weekly report generation · Code review · API design · Competitive product analysis · Data report · Blog article · Deployment plan · Email copy · Station meeting summary

### Switch Agent model

> Kanban → ⚙️ Model Configuration → Select New Model → Apply Changes

The Gateway will automatically restart and take effect after about 5 seconds.

### Management skills

> Dashboard → 🛠️ Skill configuration → View installed skills → Click to add new skills

### Stop/Cancel task

> In the Purpose Board or task details, click the **⏸ Stop** or **🚫 Cancel** button

### Subscribe to world news

> Bulletin → 📰 World News → ⚙️ Subscription Management → Select Category / Add Source / Equip Feishu Push

---

## ❓ Troubleshooting

### The dashboard displays "Server not started"
```bash
# Confirm that the server is running
python3 dashboard/server.py
```

### Agent does not respond
```bash
# Check Gateway status
openclaw gateway status

# Restart if necessary
openclaw gateway restart
```

### Data is not updated
```bash
# Check if refresh loop is running
ps aux | grep run_loop

# Manually perform a synchronization
python3 scripts/refresh_live_data.py
```

### Heartbeat shows red/alarm
```bash
# Check the process of the corresponding Agent
openclaw agent status <agent-id>

# Restart the specified Agent
openclaw agent restart <agent-id>
```

### Does not take effect after model switching
Wait approximately 5 seconds for the Gateway restart to complete. Rules still not valid:
```bash
python3 scripts/apply_model_changes.py
openclaw gateway restart
```

---

## 📚 More resources

- [🏠Project homepage](https://github.com/cft0808/edict)
- [📖 README](../README.md)
- [🤝 Contribution Guide](../CONTRIBUTING.md)
- [💬 OpenClaw Documentation](https://docs.openclaw.ai)
- [📮 Official account · cft0808](wechat.md) — Architecture dismantling / Pitfall review / Token money-saving technique


---

 ### Comando recomendado:                                                                                                            
                                                                                                                                     
 ```bash
   /home/cartine/.openclaw/workspace/skills/fidelity-md-translation/.venv/bin/python \
     /home/cartine/.openclaw/workspace/skills/fidelity-md-translation/scripts/translate_md_fidelity.py \
     translate-google \
     /home/cartine/.openclaw/edict-BR/docs/task-dispatch-architecture.md \
     --out /home/cartine/.openclaw/edict-BR/docs/task-dispatch-architecture_EN-googletrans.md \
     --src-lang zh-cn --dest en --max-concurrency 1 --retry 3 --chunk-size 12000
 ```