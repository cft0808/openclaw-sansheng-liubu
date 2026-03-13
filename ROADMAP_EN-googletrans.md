# 🗺️ Three provinces and six departments · Roadmap

> This roadmap is public. Welcome to claim unfinished projects and submit PRs to participate in the construction.
>
> How to claim: Reply "I'll take this" under the corresponding issue, or submit a PR directly and indicate it in the description.

---

## Phase 1 — Core Architecture ✅

> The skeleton of three provinces and six departments: twelve departments + prince sorting + real-time billboard + complete workflow.

- [x] Twelve-department Agent structure (Prince + Zhongshu·Menxia·Shangshu + Huli Bingxinggong + Officials + Early Dynasty Officials)
- [x] Prince Sorting Layer - Automatically identify chats/commands, reply directly to chats, extract the title of the command and transfer it to Zhongshu Province
- [x] Strict permission matrix - who can send messages to whom, in black and white
- [x] Military Aircraft Department real-time dashboard (10 function panels)
- [x] Task life cycle management (create → sort → plan → review → dispatch → execute → echo)
- [x] Task pause/cancel/resume
- [x] Memorial system (automatic archiving of completed decrees + five-stage timeline)
- [x] Imperial edict template library (9 preset templates + parameter form + estimated time/cost)
- [x] A sense of ritual for going to court (the opening animation will be played for the first time every day + today’s statistics)
- [x] World News (daily automatic collection of technology/financial information + Feishu push + subscription management)
- [x] Model hot switching (one-click switching of each Agent's LLM in the dashboard)
- [x] Skill management (check installed Skills in each province + add new skills)
- [x] Official overview (Token consumption ranking + activity + completion statistics)
- [x] Small tasks/session monitoring (OC-* session real-time tracking)
- [x] Purpose data cleaning - titles/notes are automatically purified, and dirty data is refused to be stored in the database
- [x] Duplicate mission protection - Completed/cancelled missions cannot be overwritten
- [x] E2E Kanban test (all 9 scenarios and 17 assertions passed)
- [x] React 18 front-end refactoring - TypeScript + Vite + Zustand, 13 functional components
- [x] Visualization of Agent's thinking process - real-time display of thinking / tool_result / user messages
- [x] Integrated front-end and back-end deployment - server.py also provides API + static file services

---

## Phase 2 — System deepening 🚧

> Upgrade "easy to use" to "irreplaceable": Decentralization and checks and balances are not just a concept, but a complete system with performance evaluation, manual approval, and knowledge accumulation.

### 🏅 Royal approval mode (manual approval node)
- [ ] The results of the deliberation of the province are submitted to "Yulan", which can be accurately read/rejected manually with one click.
- [ ] Approval panel within the Kanban board (pending approval list + historical approval instructions)
- [ ] Feishu/Telegram push approval notification
- **Difficulty**: ⭐⭐ | **Suitable for first time contributors**

### 📊 Record of Merits and Demerits (Agent Performance Rating System)
- [ ] Completion rate, rework rate, and time-consuming statistics of each Agent
- [ ] Kanban panel display ranking list + trend chart
- [ ] Automatically mark "competent ministers" and "Agents who need training"
- **Difficulty**: ⭐⭐

### 🚀 Express Shop (visualization of real-time message flow between agents)
- [ ] Real-time connection animation in the billboard: Zhongshu → Menxia → Shangshu → Liubu
- [ ] Message type coloring (Dispatch/Consideration/Echo/Reject)
- [ ] Timeline playback mode
- **Difficulty**: ⭐⭐⭐

### 📚 National History Museum (knowledge base + citation tracing)
- [ ] The experience of historical will is automatically precipitated
- [ ] Similar intention search + recommendation
- [ ] Traceability chain of memorial citations
- **Difficulty**: ⭐⭐⭐

---

## Phase 3 — Ecological Expansion

> From stand-alone tools to ecosystem: more integrations, more users, and more scenarios.

### 🐳 Docker Compose + Demo Image
- [ ] `docker run` One-line command to experience the complete dashboard (preset simulation data)
- [ ] Docker Compose orchestration (kanban + data synchronization + OpenClaw Gateway)
- [ ] CI/CD automatically builds push images
- **Difficulty**: ⭐⭐ | **Suitable for first time contributors**

### 🔗 Kanban Adapter
- [ ] Notion Adapter - Turn the Notion database into a military aircraft department billboard
- [ ] Linear adapter - Linear projects are synchronized to three provinces and six departments
- [ ] GitHub Issues two-way synchronization
- **Difficulty**: ⭐⭐⭐

### 📱 Mobile + PWA
- [ ] Responsive layout adapted to mobile phones/tablets
- [ ] PWA offline support + push notifications
- **Difficulty**: ⭐⭐

### 🏪 ClawHub is available
- [ ] Core Skills submitted to OpenClaw official Skill Market
- [ ] Install three provinces and six Skill Packs with one click
- **Difficulty**: ⭐

### 📈 Annual exam
- [ ] Agent annual performance report (total Token consumption, completion rate, most complex intention)
- [ ] Visual annual review screen
- **Difficulty**: ⭐⭐

---

## How to participate

1. **Look at Phase 2** - These are the directions that need the most help right now
2. **Look for projects marked with ⭐⭐ or "Suitable for first time contribution"** to get started
3. **Open an Issue** to say what you want to do to avoid duplication of work
4. **Publish PR** - see [CONTRIBUTING.md](CONTRIBUTING.md) for details

> 💡 Didn’t find the direction you want to go? You are welcome to open an Issue to propose new features, and good ideas will be added to the Roadmap.
