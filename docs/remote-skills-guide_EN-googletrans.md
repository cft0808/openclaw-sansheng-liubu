# Remote Skills Resource Management Guide

## Overview

Three provinces and six ministries now support connecting and adding skills resources from the Internet without the need to manually copy files. Support is obtained from the following sources:

- **GitHub Repository** (raw.githubusercontent.com)
- **Any HTTPS URL** (needs to return a valid skill file)
- **Local file path**
- **Built-in repository** (official skills repository)

---

## Functional architecture

### 1. API endpoint

#### `POST /api/add-remote-skill`

Adds a skill for the specified Agent from a remote URL or local path.

**Request body:**
```json
{
  "agentId": "zhongshu",
  "skillName": "code_review",
  "sourceUrl": "https://raw.githubusercontent.com/org/skills-repo/main/code_review/SKILL.md",
  "description": "代码审查专项技能"
}
```

**Parameter description:**
- `agentId` (string, required): Target Agent ID (verify validity)
- `skillName` (string, required): internal name of skill (only letters/numbers/underscores/Chinese characters allowed)
- `sourceUrl` (string, required): remote URL or local file path
- GitHub: `https://raw.githubusercontent.com/user/repo/branch/path/SKILL.md`
- Any HTTPS: `https://example.com/skills/my_skill.md`
- Local: `file:///Users/bingsen/skills/code_review.md` or `/Users/bingsen/skills/code_review.md`
- `description` (string, optional): Chinese description of skill

**Response successful (200):**
```json
{
  "ok": true,
  "message": "技能 code_review 已添加到 zhongshu",
  "skillName": "code_review",
  "agentId": "zhongshu",
  "source": "https://raw.githubusercontent.com/...",
  "localPath": "/Users/bingsen/.openclaw/workspace-zhongshu/skills/code_review/SKILL.md",
  "size": 2048,
  "addedAt": "2026-03-02T14:30:00Z"
}
```

**Response failed (400):**
```json
{
  "ok": false,
  "error": "URL 无效或无法访问",
  "details": "Connection timeout after 10s"
}
```

#### `GET /api/remote-skills-list`

Lists all added remote skills and their source information.

**response:**
```json
{
  "ok": true,
  "remoteSkills": [
    {
      "skillName": "code_review",
      "agentId": "zhongshu",
      "sourceUrl": "https://raw.githubusercontent.com/org/skills-repo/main/code_review/SKILL.md",
      "description": "代码审查专项技能",
      "localPath": "/Users/bingsen/.openclaw/workspace-zhongshu/skills/code_review/SKILL.md",
      "lastUpdated": "2026-03-02T14:30:00Z",
      "status": "valid"  // valid | invalid | not-found
    }
  ],
  "count": 5
}
```

#### `POST /api/update-remote-skill`

Update the added remote skill to the latest version.

**Request body:**
```json
{
  "agentId": "zhongshu",
  "skillName": "code_review"
}
```

**response:**
```json
{
  "ok": true,
  "message": "技能已更新",
  "skillName": "code_review",
  "newVersion": "2.1.0",
  "updatedAt": "2026-03-02T15:00:00Z"
}
```

#### `DELETE /api/remove-remote-skill`

Remove an added remote skill.

**Request body:**
```json
{
  "agentId": "zhongshu",
  "skillName": "code_review"
}
```

---

## CLI commands

### Add remote skill

```bash
python3 scripts/skill_manager.py add-remote \
  --agent zhongshu \
  --name code_review \
  --source https://raw.githubusercontent.com/org/skills-repo/main/code_review/SKILL.md \
  --description "代码审查专项技能"
```

### List remote Skills

```bash
python3 scripts/skill_manager.py list-remote
```

### Update remote Skill

```bash
python3 scripts/skill_manager.py update-remote \
  --agent zhongshu \
  --name code_review
```

### Remove remote Skill

```bash
python3 scripts/skill_manager.py remove-remote \
  --agent zhongshu \
  --name code_review
```

---

## Official Skills Library

### OpenClaw Skills Hub

> **Official skills library address**: https://github.com/openclaw-ai/skills-hub

List of available skills:

| Skill Name | Description | Applicable Agent | Source URL |
|-----------|------|----------|--------|
| `code_review` | Code review (supports Python/JS/Go) | Ministry of War/Ministry of Justice | https://raw.githubusercontent.com/openclaw-ai/skills-hub/main/code_review/SKILL.md |
| `api_design` | API design review | Ministry of War/Ministry of Industry | https://raw.githubusercontent.com/openclaw-ai/skills-hub/main/api_design/SKILL.md |
| `security_audit` | Security Audit | Ministry of Justice | https://raw.githubusercontent.com/openclaw-ai/skills-hub/main/security_audit/SKILL.md |
| `data_analysis` | Data Analysis | Household Department | https://raw.githubusercontent.com/openclaw-ai/skills-hub/main/data_analysis/SKILL.md |
| `doc_generation` | Document generation | Ministry of Rites | https://raw.githubusercontent.com/openclaw-ai/skills-hub/main/doc_generation/SKILL.md |
| `test_framework` | Test framework design | Ministry of Industry/Ministry of Punishment | https://raw.githubusercontent.com/openclaw-ai/skills-hub/main/test_framework/SKILL.md |

**Import official skills with one click**

```bash
python3 scripts/skill_manager.py import-official-hub \
  --agents zhongshu,menxia,shangshu,bingbu,xingbu,libu
```

---

## Kanban UI operation

### Quickly add Skill

1. Open the dashboard → 🔧 **Skill Configuration** panel
2. Click the **➕Add Remote Skill** button
3. Fill out the form:
- **Agent**: Select the target Agent
- **Skill Name**: Enter the internal ID of the skill
- **Remote URL**: Paste the GitHub/HTTPS URL
- **Chinese description**: Optional, briefly describe the skill function
4. Click the **Confirm** button

### Manage added Skills

1. Kanban → 🔧 **Skill Configuration** → **Remote Skills** Tag
2. View all added skills and their source addresses
3. Operation:
- **View**: Display SKILL.md content
- **UPDATE**: Re-download the latest version from the source URL
- **DELETE**: Remove the local copy (does not affect the source)
- **Copy Source URL**: Quickly share with others

---

## Skill file specification

Remote skills must follow standard Markdown format:

### Minimum required structure

```markdown
---
name: skill_internal_name
description: Short description
version: 1.0.0
tags: [tag1, tag2]
---

# Skill name

Detailed description...

## Input

Indicates what parameters are received

## Processing process

Specific steps...

## Output specifications

Output format description
```

### 完整示例

```markdown
---
name: code_review
description: Structural review and optimization recommendations for Python/JavaScript code
version: 2.1.0
author: openclaw-ai
tags: [code-quality, security, performance]
Compatible agents: [Not, Xingbu, Menxia]
---

# Code review skills

This skill is specifically designed to conduct multi-dimensional reviews of production code...

## Input

- `code`: the source code to be reviewed
- `language`: programming language (python, javascript, go, rust)
- `focusAreas`: review focus (security, performance, style, structure)

## Processing process

1. Language recognition and grammar verification
2. Security vulnerability scanning
3. Performance bottleneck identification
4. Code style check
5. Best practice recommendations

## Output specifications

```json
{
"issues": [
{
"type": "security|performance|style|structure",
"severity": "critical|high|medium|low",
"location": "line:column",
"message": "Problem description",
"suggestion": "Repair suggestion"
}
],
"summary": {
"totalIssues": 3,
"criticalCount": 1,
"highCount": 2
}
}
```

## Applicable scenarios

- Code output review by Ministry of War (code implementation)
- Security inspection by the Ministry of Justice (Compliance Audit)
- Quality assessment of the province (review and control)

## Dependencies and limitations

- Requires Python 3.9+
- Supported file size: up to 50KB
- Execution timeout: 30 seconds
```

---

## Data storage

### Local storage structure

```
~/.openclaw/
├── workspace-zhongshu/
│ └── skills/
│ ├── code_review/
│ │ ├── SKILL.md
│ │ └── .source.json # Store source URL and metadata
│ └── api_design/
│ ├── SKILL.md
│ └── .source.json
├──...
```

### .source.json format

```json
{
  "skillName": "code_review",
  "sourceUrl": "https://raw.githubusercontent.com/...",
  "description": "代码审查专项技能",
  "version": "2.1.0",
  "addedAt": "2026-03-02T14:30:00Z",
  "lastUpdated": "2026-03-02T14:30:00Z",
  "lastUpdateCheck": "2026-03-02T15:00:00Z",
  "checksum": "sha256:abc123...",
  "status": "valid"
}
```

---

## Security considerations

### URL verification

✅ **Allowed URL types:**
- HTTPS URLs: `https://`
- Local file: `file://` or absolute path
- Relative path: `./skills/`

❌ **BANNED URL TYPES:**
- HTTP (not HTTPS): `http://` rejected
- Local mode HTTP: `http://localhost/` (avoid loopback attacks)
- FTP/SSH: `ftp://`, `ssh://`

### Content verification

1. **Format Verification**: Ensure it is a valid Markdown YAML frontmatter
2. **Size Limit**: Maximum 10 MB
3. **Timeout Protection**: Automatically abort downloading if it exceeds 30 seconds
4. **Path Traversal Protection**: Check the parsed skill name and disable `../` mode
5. **checksum verification**: Optional GPG signature verification (official library only)

### Isolated execution

- Remote skills are executed in a sandbox (provided by the OpenClaw runtime)
- Unable to access sensitive files such as `~/.openclaw/config.json`
- Only the assigned workspace directory can be accessed

---

## Troubleshooting

### FAQ

**Q: Download failed, prompting "Connection timeout"**

A: Check network connection and URL validity:
```bash
curl -I https://raw.githubusercontent.com/...
```

**Q: Skill shows "invalid" status**

A: Check the file format:
```bash
python3 -m json.tool ~/.openclaw/workspace-zhongshu/skills/xxx/SKILL.md
```

**Q: Can I import from a private GitHub repository? **

A: Not supported (for security reasons). Can:
1. Make the repository public
2. Add directly after downloading locally
3. Public link via GitHub Gist

**Q: How to create your own skills library? **

A: Create your own warehouse by referring to the structure of [OpenClaw Skills Hub](https://github.com/openclaw-ai/skills-hub), and then:

```bash
git clone https://github.com/yourname/my-skills-hub.git
cd my-skills-hub
#Create skill file structure
# Commit & push to GitHub
```

Then add it through the URL or official library import function.

---

## Best Practices

### 1. Version management

Always mark the version number in the frontmatter of SKILL.md:
```yaml
---
version: 2.1.0
---
```

### 2. Backward compatibility

Keep input/output formats compatible when updating skills to avoid breaking existing processes.

### 3. Complete documentation

Contains detailed:
- Function description
- Applicable scenarios
- Dependency description
- Output example

### 4. Regular updates

Set up regular checks for updates (the cycle can be configured in the dashboard):
```bash
python3 scripts/skill_manager.py check-updates --interval weekly
```

### 5. Contribute to the community

Mature skills can be contributed to [OpenClaw Skills Hub](https://github.com/openclaw-ai/skills-hub).

---

## API complete reference

For details, see Part 3 (API and Tools) of [Task Dispatch Architecture Document](task-dispatch-architecture.md).

---

<p align="center">
<sub>Use the <strong>open</strong> ecosystem to empower <strong>institutionalized</strong> AI collaboration</sub>
</p>
