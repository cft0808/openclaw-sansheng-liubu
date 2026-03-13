# I redesigned the AI ​​multi-agent collaboration architecture using the three provinces and six departments system

> The system design from 1,300 years ago understood decentralization better than the modern AI framework.

![Cover: Full view of the military aircraft department’s kanban board](screenshots/01-kanban-main.png)

---

## 1. A strange idea

At the end of last year, I started using AI Agent heavily for work - writing code, doing analysis, and generating documents. It uses some of the most popular multi-agent frameworks on the market.

After using it for a month, I discovered a fundamental problem:

**These frameworks do not have the concept of "audit". **

The model of CrewAI is: several Agents work on their own, and then hand over the work after completion. AutoGen is better, it has Human-in-the-loop, but essentially you are your own QA. MetaGPT has roles, but review is optional.

Just like a company without a QA department, engineers write the code and deploy it directly online.

Then you get the final result, and you don’t know what happened in the middle. You can’t reproduce it, audit it, or intervene. If something goes wrong, you can only run again.

I have been wondering: Is there a structure that naturally embeds auditing into the process, not as an optional plug-in, but as a level that must be passed?

Then one day, when I was reading "Zi Zhi Tong Jian", I suddenly thought -

**Three provinces and six ministries. **

Emperor Taizong of the Tang Dynasty designed this system 1,300 years ago: Zhongshu Province drafts decrees, Menxia Province reviews and approves them, and Shangshu Province executes them. The three departments check and balance each other, and any decree must be reviewed before being issued.

Isn't this the architecture I'm looking for?

![Court Ceremony: Easter Egg Animation Opened for the First Time Every Day](screenshots/11-ceremony.png)
*▲ When you open the billboard for the first time every day, there will be an opening animation of "Going to court" - full of sense of ritual*

---

## 2. Architectural design of the ancients

The system of three provinces and six ministries is not a metaphor, it is a system of decentralized checks and balances that has been tested in practice for 1,400 years.

To simplify, the information flow looks like this:

```
Your Majesty (you)
↓ Decree
Zhongshu Sheng (Planning) ← Break your sentence into executable subtasks
↓ Submit for review
Menxia Province (Deliberation) ← Review the quality of the plan, and reject it if it is not good
↓ Accurate performance
Shangshu Province (distribution) ← assigned to six ministries for execution
↓
Six Departments (Execution) ← The Ministry of Accounts is in charge of data, the Ministry of Rites is in charge of documents, the Ministry of War is in charge of development, the Ministry of Punishment is in charge of compliance, and the Ministry of Industry is in charge of infrastructure.
↓
Summary of responses from Shangshu Province ← The results will be reported to you
```

Pay attention to the most critical step here: **Provincial review**.

After the Zhongshu Ministry plans the plan, it is not thrown directly to the executive level - it must first be reviewed by the subordinate ministry. Provincial inspection under the door:

- Is the splitting of subtasks reasonable? Are there any missing requirements?
- Are department assignments accurate? Was the one assigned to the Military Department mistakenly assigned to the Ministry of Rites?
- Is the plan executable? Is there anything unrealistic?

If it is unqualified, the Ministry of Education can reject it - directly reject it and ask Zhongshu Province to make a new plan. It's not a warning, it's a forced rework.

This is why the Tang Dynasty functioned for 289 years. **Unrestrained power is bound to make mistakes**, Tang Taizong thought clearly.

---

## 3. I made it an open source project

I used OpenClaw to build a real system with three provinces and six departments. The 9 AI Agents each perform their own duties and communicate strictly in accordance with the permission matrix.

The project is called **Edict (three provinces and six departments)** and has been open source:

**GitHub: https://github.com/cft0808/edict**

The core architecture is simple:

- **Zhongshu Sheng**: receive orders (your instructions), plan plans, and dismantle subtasks
- **Menxiasheng**: Review the plan, check the quality, and reject it directly if it is not qualified.
- **Shang Shu Sheng**: After the report is accurate, it will be distributed to the six departments to coordinate the implementation and summarize the results.
- **Six departments**: Ministry of Finance (data analysis), Ministry of Rites (document writing), Ministry of War (code development), Ministry of Justice (security and compliance), Ministry of Industry (CI/CD deployment)
- **Morning Official**: Send you a news briefing every day

Each Agent has independent Workspace, independent Skills, and independent LLM model. Strict permission matrix - who can send messages to whom, written in black and white:

| Who ↓ To whom → | Zhongshu | Menxia | Shangshu | Liubu |
|:---:|:---:|:---:|:---:|:---:|
| **中书省** | — | ✅ | ✅ | ❌ |
| **MenxiaSheng** | ✅ | — | ✅ | ❌ |
| **Shang Shu Sheng** | ✅ | ✅ | — | ✅ |
| **六片** | ❌ | ❌ | ✅ | ❌ |

The Zhongshu Provincial Department cannot directly command the Sixth Department, and the Sixth Department cannot report to the Zhongshu Provincial Department directly. All cross-layer communications must be relayed through Shangshu Province.

**This is not a decorative setting, it is a mandatory constraint at the architectural level. **

![Demo: Watch the complete flow in 30 seconds](demo.gif)
*▲ 30-second Demo: a complete tour from the court ceremony to the decree board, memorial archives, and model configuration*

---

## 4. Comparison with existing frameworks

You may ask: Compared with CrewAI and AutoGen, what is the difference?

| | CrewAI | AutoGen | **Three provinces and six departments** |
|---|:---:|:---:|:---:|
| Audit mechanism | ❌ | ⚠️ Optional | ✅ Mandatory audit in Menha Province |
| Live dashboard | ❌ | ❌ | ✅ 10 panels |
| Task Intervention | ❌ | ❌ | ✅ Stop/Cancel/Resume |
| Transfer audit | ⚠️ | ❌ | ✅ Complete memorial archive |
| Agent Health Monitoring | ❌ | ❌ | ✅ Heartbeat Detection |
| Hot Switch LLM | ❌ | ❌ | ✅ One-click switching within the board |

The core difference is **the provincial review mechanism**.

This is not Human-in-the-loop (that’s asking yourself to be the QA), this is a dedicated AI Agent responsible for reviewing the output of another AI Agent. Institutional, mandatory, architectural level.

An AI collaboration system without review is like a team without code review - it runs fast and overturns quickly.

---

## 5. Bulletin Board at the Military Aircraft Department - Make everything observable

It’s not enough to have a structure, you also have to be able to see it.

So I made a **Military Aircraft Department Dashboard** - a web panel that monitors the flow of all tasks in real time. Zero dependencies, single file HTML, Python standard library backend, just open the browser and use it.

10 function panels:

**📋 Purpose Board**: All tasks are displayed in the form of cards, sorted by status, and filter search is supported. Each card has a heartbeat badge - 🟢 Active, 🟡 Stalled, 🔴 Alarm. Click to see the complete circulation timeline, and you can stop or cancel at any time.

![Kanban](screenshots/01-kanban-main.png)
*▲ Purpose board: task cards are sorted by status, and heartbeat badges are clear at a glance*

**🔭 Provincial and Department Scheduling**: Visualize the number of tasks, department distribution, and Agent health cards in each status. See at a glance who is busy, who is idle, and who is down.

![Provincial dispatch](screenshots/02-monitor.png)
*▲ Provincial dispatch: status distribution + department load + Agent health card*

**📜 Memorial Pavilion**: All completed edicts are automatically archived as "Memorials", showing a complete five-stage timeline - Imperial edict → Zhongshu planning → Subordinate review → Six-part execution → Echo. Copy to Markdown with one click.

![Memorial Archive](screenshots/08-memorials.png)
*▲ Memorial Pavilion: Complete five-stage timeline, one-click export to Markdown*

**📜 Edict Library**: 9 preset edict templates. Choose one, fill in the parameters, preview, and make an order with one click. Covers: common scenarios such as weekly report generation, code review, API design, competitive product analysis, etc.

![Imperial edict template library](screenshots/09-templates.png)
*▲Purpose library: 9 preset templates, fill in the parameters and make a decree with one click*

**⚙️ Model configuration**: Each Agent can switch LLM models independently. The Ministry of Education uses Claude for planning, the Ministry of War uses GPT-4o to write code, and the Ministry of Household Affairs uses DeepSeek to calculate data - each draws on its strengths.

![Model configuration](screenshots/04-model-config.png)
*▲ Model configuration: Each Agent switches LLM independently, each taking advantage of their own strengths*

There are also official overview (Token consumption ranking list), skill management, world news (automatic news aggregation), session monitoring, and court ceremony (the Easter egg animation that is opened for the first time every day).

**All zero dependencies**, no React or Vue, pure HTML + CSS + JavaScript, completed in 2200 lines.

![Official Overview](screenshots/06-official-overview.png)
*▲ Official Overview: Token Consumption Ranking + Activity Statistics*

![World News](screenshots/10-morning-briefing.png)
*▲World News: Daily automatic aggregation of technology/financial information*

---

## 6. Let me show you a real case

Just talking without practicing is not enough. Let’s take a look at a real operating record - let three provinces and six departments analyze competing products.

**Purpose**: Analyze the differences between the three frameworks CrewAI, AutoGen and LangGraph and output a comparison report.

![Task flow details](screenshots/03-task-detail.png)
*▲ Click on any task card to see the complete flow chain and real-time status*

### Zhongshu Provincial Planning (45 seconds)

After receiving the order, Zhongshu Province split it into four sub-tasks:
1. Ministry of War → Research on architecture and communication mechanism
2. Account Department → Data collection and quantitative comparison (GitHub Stars, Contributors, etc.)
3. Ministry of War → In-depth evaluation of developer experience
4. Department of Etiquette → Summarize and write a comparison report

### Menxia Provincial Review (32 seconds) - rejected!

**The first round of Menxia Province was directly returned:**

> *"The plan has three problems: 1) The purpose clearly requires the evaluation of 'observability', but there is no corresponding sub-task in the plan; 2) Sub-tasks 1 and 3 are both Ministry of War research, there is overlap, and it is recommended to merge; 3) There is a lack of conclusive sub-task of recommended scenarios - analysis without conclusion means no analysis. Rejected."*

After the Zhongshu Province revised the plan, the second round of the Menxia Province was accurate.

**This is the value of door-to-door savings. ** Without this step, the Ministry of War will conduct two surveys, and there will be no recommended scenarios in the final report - because there is no requirement in the original plan.

### Execution of each department (17 minutes)

- **Ministry of War**: In-depth technical comparison, covering the three dimensions of architecture, communication, and observability
- **Household Department**: Quantitative data table - Stars, Contributors, Issue response time, Hello World construction time
- **Ministry of Rites**: Integrate data from the Ministry of War + Ministry of Household Affairs and write the final report

### Echo

22 minutes, 15800 Tokens, a structured comparison report. The conclusion is very interesting:

| Scenario | Recommendation | Reason |
|------|------|------|
| Rapid Prototyping | CrewAI | The fastest to get started |
| Conversational collaboration | AutoGen | Naturally suitable for multi-round discussions |
| Complex workflow | LangGraph | State machine is the most flexible |
| **Reliability first** | **Three provinces and six departments** | The only built-in mandatory audit |

---

## 7. Some technical choices

When working on this project, I made several deliberate technical decisions:

**1. Zero dependencies**

The Kanban front-end is an HTML file, 2200 lines long, without any framework. The backend is `http.server` of the Python standard library, without Flask or FastAPI.

Why? Because I don't want people to `pip install` a bunch of things before running. The target users of this project may just want to quickly experience the circulation effect of three provinces and six movies, and do not want to set up the environment.

**2. One SOUL.md for each Agent**

Each Agent's personality, responsibilities, and workflow rules are written in a Markdown file. Want to modify the review standards of Phnom Penh Province? Edit `agents/menxia/SOUL.md` and it will automatically take effect next time you start it.

This means that you can customize your own three provinces and six departments - maybe your "Ministry of War" is not responsible for engineering, but for market analysis. Just change SOUL.md.

**3. Permission matrix is ​​mandatory**

It is not "recommended" not to communicate across levels between agents, it is a mandatory restriction at the architectural level. The Sixth Ministry cannot send messages to the Zhongshu Province, and the Zhongshu Province cannot bypass the Menxia Province and directly ask the Shangshu Province to implement it. OpenClaw's configuration file says in black and white who can talk to whom.

---

## 8. Now you can try

The project is open source and licensed under the MIT license.

**GitHub: https://github.com/cft0808/edict**

The fastest way to experience:

```bash
# Docker starts with one line
docker run -p 7891:7891 cft0808/edict

# Open browser
open http://localhost:7891
```

If you have OpenClaw installed, you can install it completely:

```bash
git clone https://github.com/cft0808/edict.git
cd edict
chmod +x install.sh && ./install.sh
```

The installation script automatically creates 9 Agent Workspaces, writes personality files, registers the permission matrix, and restarts the Gateway.

![Skills configuration](screenshots/05-skills-config.png)
*▲Skills management: List of installed Skills in each province and department, you can view details and add new skills*

---

## 9. Next step

Phase 1 (core architecture) has been completed. Next few things to do:

- **Imperial Approval Mode**: The review results of Menxiasheng can be pushed to your Feishu/Telegram, and you can decide whether to approve or reject it yourself.
- **Accomplishment and Failure Book**: Each Agent's performance score - completion rate, rework rate, time-consuming statistics
- **Express Delivery Shop**: Add a real-time Agent communication flow diagram to the bulletin board - when Zhongshu Province sends a message to Menxia Province, the connection will light up
- **National History Museum**: historical decrees and memorials are accumulated into a knowledge base, and new decrees can refer to historical experience

The complete Roadmap is on GitHub. Each sub-item of Phase 2 and Phase 3 is marked with difficulty. You are welcome to claim it.

---

## at last

The core issue of AI Agent collaboration is not "making the Agent smarter", but "making the Agent's collaboration have rules."

CrewAI solves the problem of "multiple Agents working together". AutoGen solves the problem of "agents being able to talk to each other".

But who will solve the problem of "Agent's output quality is guaranteed"?

Emperor Taizong of the Tang Dynasty gave the answer 1,300 years ago: **separation of powers, checks and balances**. What is planned is not reviewed, what is reviewed is not implemented, and what is implemented is not planned. Someone is watching every step, and every decision must be reviewed.

This is probably the most elegant "AI governance" solution I've ever seen - because it wasn't designed for AI at all.

It is designed for governance itself.

---

**GitHub: https://github.com/cft0808/edict**

Open Source · MIT · Welcome Star ⚔️
