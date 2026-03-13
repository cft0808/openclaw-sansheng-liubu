∏# 🤝 Participate and contribute

<p align="center">
<strong>Three provinces and six ministries welcome heroes from all walks of life ⚔️</strong><br>
<sub>Whether it’s fixing a typo or designing a new Agent character, we’re all very grateful</sub>
</p>

---

## 📋 How to contribute

### 🐛 Report a bug

Please use the [Bug Report](.github/ISSUE_TEMPLATE/bug_report.md) template to submit an Issue, including:
- OpenClaw version (`openclaw --version`)
- Python version (`python3 --version`)
- operating system
- Steps to reproduce (the more detailed, the better)
- Desired behavior vs actual behavior
- Screenshots (if Kanban UI is involved)

### 💡 Feature Suggestions

Use the [Feature Request](.github/ISSUE_TEMPLATE/feature_request.md) template.

We recommend using the "will" format to describe your needs - just like writing a memorial to the emperor 😄

### 🔧 Submit Pull Request

```bash
# 1. Fork this warehouse
# 2. Clone your Fork
git clone https://github.com/<your-username>/edict.git
cd edict

# 3. Create a feature branch
git checkout -b feat/my-awesome-feature

# 4. Development & Testing
python3 dashboard/server.py  # 启动看板验证

# 5. Submit
git add .
git commit -m "feat: 添加了一个很酷的功能"

# 6. Push & create PR
git push origin feat/my-awesome-feature
```

---

## 🏗️ Development environment

### Preconditions
- [OpenClaw](https://openclaw.ai) installed
-Python 3.9+
- macOS/Linux

### Local startup

```bash
# Install
./install.sh

# Start data refresh (running in the background)
bash scripts/run_loop.sh &

# Start the kanban server
python3 dashboard/server.py

# Open browser
open http://127.0.0.1:7891
```

> 💡 **Kanbanboard out of the box**: `server.py` embeds `dashboard/dashboard.html`, Docker image includes pre-built React frontend

### Quick overview of project structure

| Directory/File | Description | Frequency of changes |
|----------|------|--------|
| `dashboard/dashboard.html` | Kanban front-end (single file, zero dependencies, ready to use out of the box) | 🔥 High |
| `dashboard/server.py` | API Server (stdlib, ~2200 lines) | 🔥 High |
| `agents/*/SOUL.md` | 12 Agent personality templates | 🔶 Medium |
| `scripts/kanban_update.py` | Kanban CLI + data cleaning (~300 lines) | 🔶 Medium |
| `scripts/*.py` | Data synchronization / automation script | 🔶 Medium |
| `tests/test_e2e_kanban.py` | E2E Kanban test (17 assertions) | 🔶 Medium |
| `install.sh` | Installation script | 🟢 Low |

---

## 📝 Commit specification

We use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: ✨ NEW FEATURES
fix: 🐛 Fix Bug
docs: 📝 Documentation update
style: 🎨 code format (does not affect logic)
refactor: ♻️ code refactoring
perf: ⚡ performance optimization
test: ✅ test
chore: 🔧 Miscellaneous maintenance
ci: 👷 CI/CD configuration
```

Example:
```
feat: Added the function of exporting memorials to PDF
fix: Fixed the problem of Gateway not restarting after model switching
docs: Updated README screenshots
```

---

## 🎯 Specially welcome contributions

### 🎨 Kanban UI
- Dark/light theme switching
- Responsive layout optimization
- Enhanced animation effects
- Accessibility (a11y) improvements

### 🤖 New Agent role
- Full-time Agent suitable for specific industries/scenarios
- New SOUL.md personality template
- Innovation in collaboration model between agents

### 📦 Skills Ecosystem
- Dedicated skill packages for each department
- MCP integration skills
- Special skills in data processing/code analysis/document generation

### 🔗 Third-party integration
- Notion / Jira / Linear synchronization
- GitHub Issues/PR linkage
- Slack / Discord messaging channels
- Webhook extension

### 🌐 Internationalization
- Japanese / Korean / Spanish translation
- Kanban UI multi-language support

### 📱 Mobile version
- Responsive adaptation
- PWA support
- Mobile operation optimization

---

## 🧪 Test

```bash
# Compilation check
python3 -m py_compile dashboard/server.py
python3 -m py_compile scripts/kanban_update.py

# E2E Kanban Test (9 Scenario 17 Assertion)
python3 tests/test_e2e_kanban.py

# Verify data synchronization
python3 scripts/refresh_live_data.py
python3 scripts/sync_agent_config.py

# Start server verification API
python3 dashboard/server.py &
curl -s http://localhost:7891/api/live-status | python3 -m json.tool | head -20
```

---

## 📏 Code style

- **Python**: PEP 8, use pathlib to handle paths
- **TypeScript/React**: Function components + Hooks, CSS variable names start with `--`
- **CSS**: Use CSS variables (`--bg`, `--text`, `--acc`, etc.), BEM-style class names
- **Markdown**: Use `#` for titles, `-` for lists, code block annotation language

---

## 🙏 Code of Conduct

- Be kind and constructive
- Respect different perspectives and experiences
- Accept constructive criticism
- Focus on what is best for the community
- Show empathy towards other community members

**We have zero tolerance for harassment. **

---

## 📬 Contact information

- GitHub Issues: [Submit an issue](https://github.com/cft0808/edict/issues)
- GitHub Discussions: [Community Discussions](https://github.com/cft0808/edict/discussions)

---

<p align="center">
<sub>Thank you to every contributor, you are the cornerstone of the three provinces and six departments ⚔️</sub>
</p>
