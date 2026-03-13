# Remote Skills Quick Start

## 5 minutes experience

### 1. Start the server

```bash
# Make sure you are in the project root directory
python3 dashboard/server.py
# Output: Three provinces and six departments signboard started → http://127.0.0.1:7891
```

### 2. Add official Skill (CLI)

```bash
# Add code review skill to Zhongshu Province
python3 scripts/skill_manager.py add-remote \
  --agent zhongshu \
  --name code_review \
  --source https://raw.githubusercontent.com/openclaw-ai/skills-hub/main/code_review/SKILL.md \
  --description "代码审查能力"

# Output:
# ⏳ Downloading from https://raw.githubusercontent.com/...
# ✅ Skill code_review has been added to zhongshu
# Path: /Users/xxx/.openclaw/workspace-zhongshu/skills/code_review/SKILL.md
# Size: 2048 bytes
```

### 3. List all remote Skills

```bash
python3 scripts/skill_manager.py list-remote

# Output:
# 📋 Total 1 remote skills:
#
# Agent | Skill name | Description | Add time
# ------------|-----------------------|--------------------------------|----------
# zhongshu | code_review | Code review capabilities | 2026-03-02
```

### 4. View API response

```bash
curl http://localhost:7891/api/remote-skills-list | jq .

# Output:
# {
# "ok": true,
# "remoteSkills": [
# {
# "skillName": "code_review",
# "agentId": "zhongshu",
# "sourceUrl": "https://raw.githubusercontent.com/...",
# "description": "Code review capability",
# "localPath": "/Users/xxx/.openclaw/workspace-zhongshu/skills/code_review/SKILL.md",
# "addedAt": "2026-03-02T14:30:00Z",
# "lastUpdated": "2026-03-02T14:30:00Z",
# "status": "valid"
# }
# ],
# "count": 1,
# "listedAt": "2026-03-02T14:35:00Z"
# }
```

---

## Common operations

### Import all skills in the official library with one click

```bash
python3 scripts/skill_manager.py import-official-hub \
  --agents zhongshu,menxia,shangshu,bingbu,xingbu
```

This automatically adds for each agent:
- **zhongshu**: code_review, api_design, doc_generation
- **menxia**: code_review, api_design, security_audit, data_analysis, doc_generation, test_framework
- **shangshu**: Same as menxia (coordinator)
- **bingbu**: code_review, api_design, test_framework
- **xingbu**: code_review, security_audit, test_framework

### Update a Skill to the latest version

```bash
python3 scripts/skill_manager.py update-remote \
  --agent zhongshu \
  --name code_review

# Output:
# ⏳ Downloading from https://raw.githubusercontent.com/...
# ✅ Skill code_review has been added to zhongshu
# ✅ Skills have been updated
# Path: /Users/xxx/.openclaw/workspace-zhongshu/skills/code_review/SKILL.md
# Size: 2156 bytes
```

### Remove a Skill

```bash
python3 scripts/skill_manager.py remove-remote \
  --agent zhongshu \
  --name code_review

# Output:
# ✅ Skill code_review has been removed from zhongshu
```

---

## Kanban UI operation

### Add Remote Skill to the dashboard

1. Open http://localhost:7891
2. Enter the 🔧 **Skill Configuration** panel
3. Click the **➕Add Remote Skill** button
4. Fill out the form:
- **Agent**: Select from the drop-down list (such as zhongshu)
- **Skill Name**: Enter an internal ID such as `code_review`
- **Remote URL**: Paste the GitHub URL such as `https://raw.githubusercontent.com/openclaw-ai/skills-hub/main/code_review/SKILL.md`
- **Chinese description**: optional, such as `code review ability`
5. Click the **Import** button
6. Wait 1-2 seconds to see the ✅ success prompt

### Manage added Skills

On the dashboard → 🔧 Skill configuration → **Remote Skills** tab:

- **View**: Click on the Skill name to view the SKILL.md content
- **UPDATE**: Click 🔄 to re-download the latest version from the source URL
- **DELETE**: Click ✕ to remove the local copy
- **Copy URL**: Quickly share with others

---

## Create your own Skill library

### Directory structure

```
my-skills-hub/
├── code_review/
│ └── SKILL.md # Code review capability
├── api_design/
│ └── SKILL.md #API Design Review
├── data_analysis/
│ └── SKILL.md # Data Analysis
└── README.md
```

### SKILL.md template

```markdown
---
name: my_custom_skill
description: short description
version: 1.0.0
tags: [tag1, tag2]
---

# Skill full name

Detailed description...

## Input

Indicates what parameters are received

## Processing process

Specific steps...

## Output specifications

Output format description
```

### Upload to GitHub

```bash
git init
git add .
git commit -m "Initial commit: my-skills-hub"
git remote add origin https://github.com/yourname/my-skills-hub
git push -u origin main
```

### Import your own Skill

```bash
python3 scripts/skill_manager.py add-remote \
  --agent zhongshu \
  --name my_skill \
  --source https://raw.githubusercontent.com/yourname/my-skills-hub/main/my_skill/SKILL.md \
  --description "我的定制技能"
```

---

## API complete reference

### POST /api/add-remote-skill

Add a remote skill.

**ask:**
```bash
curl -X POST http://localhost:7891/api/add-remote-skill \
  -H "Content-Type: application/json" \
  -d '{
    "agentId": "zhongshu",
    "skillName": "code_review",
    "sourceUrl": "https://raw.githubusercontent.com/...",
    "description": "代码审查"
  }'
```

**Response (200):**
```json
{
  "ok": true,
  "message": "技能 code_review 已从远程源添加到 zhongshu",
  "skillName": "code_review",
  "agentId": "zhongshu",
  "source": "https://raw.githubusercontent.com/...",
  "localPath": "/Users/xxx/.openclaw/workspace-zhongshu/skills/code_review/SKILL.md",
  "size": 2048,
  "addedAt": "2026-03-02T14:30:00Z"
}
```

### GET /api/remote-skills-list

List all remote skills.

```bash
curl http://localhost:7891/api/remote-skills-list
```

**response:**
```json
{
  "ok": true,
  "remoteSkills": [
    {
      "skillName": "code_review",
      "agentId": "zhongshu",
      "sourceUrl": "https://raw.githubusercontent.com/...",
      "description": "代码审查能力",
      "localPath": "/Users/xxx/.openclaw/workspace-zhongshu/skills/code_review/SKILL.md",
      "addedAt": "2026-03-02T14:30:00Z",
      "lastUpdated": "2026-03-02T14:30:00Z",
      "status": "valid"
    }
  ],
  "count": 1,
  "listedAt": "2026-03-02T14:35:00Z"
}
```

### POST /api/update-remote-skill

Update the remote skill to the latest version.

```bash
curl -X POST http://localhost:7891/api/update-remote-skill \
  -H "Content-Type: application/json" \
  -d '{
    "agentId": "zhongshu",
    "skillName": "code_review"
  }'
```

### DELETE /api/remove-remote-skill

Remove remote skills.

```bash
curl -X POST http://localhost:7891/api/remove-remote-skill \
  -H "Content-Type: application/json" \
  -d '{
    "agentId": "zhongshu",
    "skillName": "code_review"
  }'
```

---

## Troubleshooting

### Q: Download failed, prompting "Connection timeout"

**A:** Check network connection and URL validity

```bash
curl -I https://raw.githubusercontent.com/...
# Should return HTTP/1.1 200 OK
```

### Q: The file format is invalid

**A:** Make sure SKILL.md starts with YAML frontmatter

```markdown
---
name: skill_name
description: description
---

# The text begins...
```

### Q: I can’t see Skill after importing

**A:** Refresh the dashboard or check whether the Agent is configured correctly

```bash
# Check if Agent exists
python3 scripts/skill_manager.py list-remote

# Check local files
ls -la ~/.openclaw/workspace-zhongshu/skills/
```

---

## More information

- 📚 [Complete Guide](remote-skills-guide.md)
- 🏛️[Architecture Document](task-dispatch-architecture.md)
- 🤝 [Project Contribution](../CONTRIBUTING.md)

