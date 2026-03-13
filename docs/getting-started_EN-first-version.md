# 🚀 Quick Start Guide

> Start from zero — set up your Three Departments & Six Ministries AI collaboration system in 5 minutes

---

## Step 1: Install OpenClaw

Three Departments & Six Ministries runs on [OpenClaw](https://openclaw.ai). Install it first:

```bash
# macOS
brew install openclaw

# Or download the installer package
# https://openclaw.ai/download
```
After installation, initialize:

```bash
openclaw init
```

---

## Step 2: Clone and install Three Departments & Six Ministries

```bash
git clone https://github.com/cft0808/edict.git
cd edict
chmod +x install.sh && ./install.sh
```

The installer automatically:
- ✅ Creates 12 Agent Workspaces (`~/.openclaw/workspace-*`)
- ✅ Writes each department/office SOUL.md personality file
- ✅ Registers Agents and the permission matrix into `openclaw.json`
- ✅ Configures edict data sanitization rules
- ✅ Builds the React frontend into `dashboard/dist/` (requires Node.js 18+)
- ✅ Initializes the data directory
- ✅ Runs the first data sync
- ✅ Restarts the Gateway so the configuration takes effect

---

## Step 3: Configure messaging channels

Configure messaging channels in OpenClaw (Feishu / Telegram / Signal), and set the `taizi` (Crown Prince) agent as the edict entrypoint. The Crown Prince automatically separates casual chat from commands; command messages have their titles extracted and are forwarded to the Planning Department (Zhongshu).

```bash
# View current channels
openclaw channels list

# Add Feishu channel (set entrypoint to Crown Prince)
openclaw channels add --type feishu --agent taizi
```

Refer to OpenClaw docs: https://docs.openclaw.ai/channels

---

## Step 4: Start the services

```bash
# Terminal 1: data refresh loop (sync every 15 seconds)
bash scripts/run_loop.sh

# Terminal 2: dashboard server
python3 dashboard/server.py

# Open browser
open http://127.0.0.1:7891
```

> 💡 Tip: `run_loop.sh` automatically syncs data every 15 seconds. You can run it in the background using `&`.

> 💡 Dashboard is ready out-of-the-box: `server.py` embeds `dashboard/dashboard.html`, no extra build required. The Docker image includes a prebuilt React frontend.

---

## Step 5: Send your first edict

Send a task through your messaging channel (the Crown Prince will automatically detect it and forward it to Zhongshu):

```
Please help me write a text classifier in Python:
1. Use scikit-learn
2. Support multi-class classification
3. Output a confusion matrix
4. Write complete documentation
```

---

## Step 6: Observe the execution process

Open the dashboard: http://127.0.0.1:7891

1. **📋 Edicts Kanban** — watch tasks move across states
2. **🔭 Department Dispatch** — view workload distribution across departments
3. **📜 Memorial Archive** — after completion, tasks are automatically archived as memorials

Task flow path:
```
Inbox → Crown Prince triage → Zhongshu planning → Menxia review → Assigned → Doing → Done
```

---

## 🎯 Advanced usage

### Use edict templates

> Dashboard → 📜 Templates Library → select template → fill parameters → issue edict

9 preset templates: weekly report · code review · API design · competitive analysis · data report · blog post · deployment plan · email copy · standup summary

### Switch an agent’s model

> Dashboard → ⚙️ Model Config → choose a new model → apply changes

After ~5 seconds, the Gateway automatically restarts and the change takes effect.

### Manage skills

> Dashboard → 🛠️ Skills Config → view installed skills → click “add new skill”

### Stop / cancel a task

> In the Edicts Kanban or task detail, click **⏸ Stop** or **🚫 Cancel**

### Subscribe to “World News” (Daily Briefing)

> Dashboard → 📰 Daily Briefing → ⚙️ Subscription Management → choose categories / add sources / configure Feishu push

---

## ❓ Troubleshooting

### Dashboard shows “server not started”
```bash
# Confirm the server is running
python3 dashboard/server.py
```

### Agent does not respond
```bash
# Check Gateway status
openclaw gateway status

# Restart if needed
openclaw gateway restart
```

### Data does not update
```bash
# Check whether the refresh loop is running
ps aux | grep run_loop

# Manually run one sync
python3 scripts/refresh_live_data.py
```

### Heartbeat shows red / alert
```bash
# Check the corresponding Agent process
openclaw agent status <agent-id>

# Restart a specific Agent
openclaw agent restart <agent-id>
```

### Model switch does not take effect
Wait about 5 seconds for the Gateway restart to complete. If it still does not take effect:

```bash
python3 scripts/apply_model_changes.py
openclaw gateway restart
```

---

## 📚 More resources

- [🏠 Project homepage](https://github.com/cft0808/edict)
- [📖 README](../README.md)
- [🤝 Contributing guide](../CONTRIBUTING.md)
- [💬 OpenClaw docs](https://docs.openclaw.ai)
- [📮 WeChat · cft0808](wechat.md) — architecture breakdown / postmortems / token-saving tricks
