#!/usr/bin/env python3
"""
三省六部 · 看板本地 API 服务器
Port: 7891 (可通过 --port 修改)

Endpoints:
  GET  /                       → dashboard.html
  GET  /api/live-status        → data/live_status.json
  GET  /api/agent-config       → data/agent_config.json
  POST /api/set-model          → {agentId, model}
  GET  /api/model-change-log   → data/model_change_log.json
  GET  /api/last-result        → data/last_model_change_result.json
"""
import json, pathlib, subprocess, sys, threading, argparse, datetime, logging, re, os, shlex, uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse
from urllib.request import Request, urlopen

# 引入文件锁工具，确保与其他脚本并发安全
scripts_dir = str(pathlib.Path(__file__).parent.parent / 'scripts')
sys.path.insert(0, scripts_dir)
from file_lock import atomic_json_read, atomic_json_write, atomic_json_update
from utils import validate_url

log = logging.getLogger('server')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s', datefmt='%H:%M:%S')

OCLAW_HOME = pathlib.Path.home() / '.openclaw'
MAX_REQUEST_BODY = 1 * 1024 * 1024  # 1 MB
ALLOWED_ORIGIN = None  # Set via --cors; None means restrict to localhost
_DEFAULT_ORIGINS = {
    'http://127.0.0.1:7891', 'http://localhost:7891',
    'http://127.0.0.1:5173', 'http://localhost:5173',  # Vite dev server
}
_SAFE_NAME_RE = re.compile(r'^[a-zA-Z0-9_\-\u4e00-\u9fff]+$')

BASE = pathlib.Path(__file__).parent
DIST = BASE / 'dist'          # React 构建产物 (npm run build)
DATA = BASE.parent / "data"
SCRIPTS = BASE.parent / 'scripts'

# 静态资源 MIME 类型
_MIME_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.js':   'application/javascript; charset=utf-8',
    '.css':  'text/css; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.png':  'image/png',
    '.jpg':  'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif':  'image/gif',
    '.svg':  'image/svg+xml',
    '.ico':  'image/x-icon',
    '.woff': 'font/woff',
    '.woff2': 'font/woff2',
    '.ttf':  'font/ttf',
    '.map':  'application/json',
}


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default if default is not None else {}


def cors_headers(h):
    req_origin = h.headers.get('Origin', '')
    if ALLOWED_ORIGIN:
        origin = ALLOWED_ORIGIN
    elif req_origin in _DEFAULT_ORIGINS:
        origin = req_origin
    else:
        origin = 'http://127.0.0.1:7891'
    h.send_header('Access-Control-Allow-Origin', origin)
    h.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
    h.send_header('Access-Control-Allow-Headers', 'Content-Type')


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')


def load_tasks():
    return atomic_json_read(DATA / 'tasks_source.json', [])


def save_tasks(tasks):
    atomic_json_write(DATA / 'tasks_source.json', tasks)
    # Trigger refresh (异步，不阻塞，避免僵尸进程)
    def _refresh():
        try:
            subprocess.run(['python3', str(SCRIPTS / 'refresh_live_data.py')], timeout=30)
        except Exception as e:
            log.warning(f'refresh_live_data.py 触发失败: {e}')
    threading.Thread(target=_refresh, daemon=True).start()


def handle_task_action(task_id, action, reason):
    """Stop/cancel/resume a task from the dashboard."""
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}

    old_state = task.get('state', '')
    _ensure_scheduler(task)
    _scheduler_snapshot(task, f'task-action-before-{action}')
    run_id = _new_run_id()
    _acquire_lease(task, stage=old_state, role='manual', owner_run_id=run_id, ttl_sec=180, force_takeover=True)

    if action == 'stop':
        commit = commit_state_change(
            task,
            action='manual_decide',
            reason_code='manual_stop',
            owner_run_id=run_id,
            expected_version=task.get('_scheduler', {}).get('stateVersion'),
            to_state='Blocked',
            now_text=f'⏸️ 已暂停：{reason}',
            block_text=reason or '皇上叫停',
            flow_from='皇上',
            flow_remark=f'⏸️ 叫停：{reason or "无"}',
            force=True,
        )
        if not commit.get('committed'):
            save_tasks(tasks)
            return {'ok': False, 'error': f'{task_id} 叫停失败: {commit.get("blockedBy")}'}
        task['_prev_state'] = old_state
    elif action == 'cancel':
        commit = commit_state_change(
            task,
            action='manual_decide',
            reason_code='manual_cancel',
            owner_run_id=run_id,
            expected_version=task.get('_scheduler', {}).get('stateVersion'),
            to_state='Cancelled',
            now_text=f'🚫 已取消：{reason}',
            block_text=reason or '皇上取消',
            flow_from='皇上',
            flow_remark=f'🚫 取消：{reason or "无"}',
            force=True,
        )
        if not commit.get('committed'):
            save_tasks(tasks)
            return {'ok': False, 'error': f'{task_id} 取消失败: {commit.get("blockedBy")}'}
        task['_prev_state'] = old_state
    elif action == 'resume':
        resume_state = task.get('_prev_state', 'Doing')
        commit = commit_state_change(
            task,
            action='manual_decide',
            reason_code='manual_resume',
            owner_run_id=run_id,
            expected_version=task.get('_scheduler', {}).get('stateVersion'),
            to_state=resume_state,
            now_text='▶️ 已恢复执行',
            block_text='无',
            flow_from='皇上',
            flow_remark=f'▶️ 恢复：{reason or "无"}',
            force=True,
        )
        if not commit.get('committed'):
            save_tasks(tasks)
            return {'ok': False, 'error': f'{task_id} 恢复失败: {commit.get("blockedBy")}'}
        _scheduler_mark_progress(task, f'恢复到 {task.get("state", "Doing")}', reason_code='manual_resume')
        _set_cooldown(task, 'noReassignUntil', _COOLDOWN_SECONDS['post_human_decision_reassign'])

    save_tasks(tasks)
    if action == 'resume' and task.get('state') not in _TERMINAL_STATES:
        dispatch_for_state(task_id, task, task.get('state'), trigger='resume', owner_run_id=run_id)
    label = {'stop': '已叫停', 'cancel': '已取消', 'resume': '已恢复'}[action]
    return {'ok': True, 'message': f'{task_id} {label}'}


def handle_archive_task(task_id, archived, archive_all_done=False):
    """Archive or unarchive a task, or batch-archive all Done/Cancelled tasks."""
    tasks = load_tasks()
    if archive_all_done:
        count = 0
        for t in tasks:
            if t.get('state') in ('Done', 'Cancelled') and not t.get('archived'):
                t['archived'] = True
                t['archivedAt'] = now_iso()
                count += 1
        save_tasks(tasks)
        return {'ok': True, 'message': f'{count} 道旨意已归档', 'count': count}
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    task['archived'] = archived
    if archived:
        task['archivedAt'] = now_iso()
    else:
        task.pop('archivedAt', None)
    task['updatedAt'] = now_iso()
    save_tasks(tasks)
    label = '已归档' if archived else '已取消归档'
    return {'ok': True, 'message': f'{task_id} {label}'}


def update_task_todos(task_id, todos):
    """Update the todos list for a task."""
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}

    task['todos'] = todos
    task['updatedAt'] = now_iso()
    save_tasks(tasks)
    return {'ok': True, 'message': f'{task_id} todos 已更新'}


def read_skill_content(agent_id, skill_name):
    """Read SKILL.md content for a specific skill."""
    # 输入校验：防止路径遍历
    if not _SAFE_NAME_RE.match(agent_id) or not _SAFE_NAME_RE.match(skill_name):
        return {'ok': False, 'error': '参数含非法字符'}
    cfg = read_json(DATA / 'agent_config.json', {})
    agents = cfg.get('agents', [])
    ag = next((a for a in agents if a.get('id') == agent_id), None)
    if not ag:
        return {'ok': False, 'error': f'Agent {agent_id} 不存在'}
    sk = next((s for s in ag.get('skills', []) if s.get('name') == skill_name), None)
    if not sk:
        return {'ok': False, 'error': f'技能 {skill_name} 不存在'}
    skill_path = pathlib.Path(sk.get('path', '')).resolve()
    # 路径遍历保护：确保路径在 OCLAW_HOME 或项目目录下
    allowed_roots = (OCLAW_HOME.resolve(), BASE.parent.resolve())
    if not any(str(skill_path).startswith(str(root)) for root in allowed_roots):
        return {'ok': False, 'error': '路径不在允许的目录范围内'}
    if not skill_path.exists():
        return {'ok': True, 'name': skill_name, 'agent': agent_id, 'content': '(SKILL.md 文件不存在)', 'path': str(skill_path)}
    try:
        content = skill_path.read_text()
        return {'ok': True, 'name': skill_name, 'agent': agent_id, 'content': content, 'path': str(skill_path)}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def add_skill_to_agent(agent_id, skill_name, description, trigger=''):
    """Create a new skill for an agent with a standardised SKILL.md template."""
    if not _SAFE_NAME_RE.match(skill_name):
        return {'ok': False, 'error': f'skill_name 含非法字符: {skill_name}'}
    if not _SAFE_NAME_RE.match(agent_id):
        return {'ok': False, 'error': f'agentId 含非法字符: {agent_id}'}
    workspace = OCLAW_HOME / f'workspace-{agent_id}' / 'skills' / skill_name
    workspace.mkdir(parents=True, exist_ok=True)
    skill_md = workspace / 'SKILL.md'
    desc_line = description or skill_name
    trigger_section = f'\n## 触发条件\n{trigger}\n' if trigger else ''
    template = (f'---\n'
                f'name: {skill_name}\n'
                f'description: {desc_line}\n'
                f'---\n\n'
                f'# {skill_name}\n\n'
                f'{desc_line}\n'
                f'{trigger_section}\n'
                f'## 输入\n\n'
                f'<!-- 说明此技能接收什么输入 -->\n\n'
                f'## 处理流程\n\n'
                f'1. 步骤一\n'
                f'2. 步骤二\n\n'
                f'## 输出规范\n\n'
                f'<!-- 说明产出物格式与交付要求 -->\n\n'
                f'## 注意事项\n\n'
                f'- (在此补充约束、限制或特殊规则)\n')
    skill_md.write_text(template)
    # Re-sync agent config
    try:
        subprocess.run(['python3', str(SCRIPTS / 'sync_agent_config.py')], timeout=10)
    except Exception:
        pass
    return {'ok': True, 'message': f'技能 {skill_name} 已添加到 {agent_id}', 'path': str(skill_md)}


def add_remote_skill(agent_id, skill_name, source_url, description=''):
    """从远程 URL 或本地路径为 Agent 添加 skill SKILL.md 文件。
    
    支持的源：
    - HTTPS URLs: https://raw.githubusercontent.com/...
    - 本地路径: /path/to/SKILL.md 或 file:///path/to/SKILL.md
    """
    # 输入校验
    if not _SAFE_NAME_RE.match(agent_id):
        return {'ok': False, 'error': f'agentId 含非法字符: {agent_id}'}
    if not _SAFE_NAME_RE.match(skill_name):
        return {'ok': False, 'error': f'skillName 含非法字符: {skill_name}'}
    if not source_url or not isinstance(source_url, str):
        return {'ok': False, 'error': 'sourceUrl 必须是有效的字符串'}
    
    source_url = source_url.strip()
    
    # 检查 Agent 是否存在
    cfg = read_json(DATA / 'agent_config.json', {})
    agents = cfg.get('agents', [])
    if not any(a.get('id') == agent_id for a in agents):
        return {'ok': False, 'error': f'Agent {agent_id} 不存在'}
    
    # 下载或读取文件内容
    try:
        if source_url.startswith('http://') or source_url.startswith('https://'):
            # HTTPS URL 校验
            if not validate_url(source_url, allowed_schemes=('https',)):
                return {'ok': False, 'error': 'URL 无效或不安全（仅支持 HTTPS）'}
            
            # 从 URL 下载，带超时保护
            req = Request(source_url, headers={'User-Agent': 'OpenClaw-SkillManager/1.0'})
            try:
                resp = urlopen(req, timeout=10)
                content = resp.read(10 * 1024 * 1024).decode('utf-8')  # 最多 10MB
                if len(content) > 10 * 1024 * 1024:
                    return {'ok': False, 'error': '文件过大（最大 10MB）'}
            except Exception as e:
                return {'ok': False, 'error': f'URL 无法访问: {str(e)[:100]}'}
        
        elif source_url.startswith('file://'):
            # file:// URL 格式
            local_path = pathlib.Path(source_url[7:])
            if not local_path.exists():
                return {'ok': False, 'error': f'本地文件不存在: {local_path}'}
            content = local_path.read_text()
        
        elif source_url.startswith('/') or source_url.startswith('.'):
            # 本地绝对或相对路径
            local_path = pathlib.Path(source_url).resolve()
            if not local_path.exists():
                return {'ok': False, 'error': f'本地文件不存在: {local_path}'}
            # 路径遍历防护
            allowed_roots = (OCLAW_HOME.resolve(), BASE.parent.resolve())
            if not any(str(local_path).startswith(str(root)) for root in allowed_roots):
                return {'ok': False, 'error': '路径不在允许的目录范围内'}
            content = local_path.read_text()
        
        else:
            return {'ok': False, 'error': '不支持的 URL 格式（仅支持 https://, file://, 或本地路径）'}
    except Exception as e:
        return {'ok': False, 'error': f'文件读取失败: {str(e)[:100]}'}
    
    # 基础验证：检查是否为 Markdown 且包含 YAML frontmatter
    if not content.startswith('---'):
        return {'ok': False, 'error': '文件格式无效（缺少 YAML frontmatter）'}
    
    # 尝试解析 frontmatter
    try:
        import yaml
        parts = content.split('---', 2)
        if len(parts) < 3:
            return {'ok': False, 'error': '文件格式无效（YAML frontmatter 结构错误）'}
        frontmatter_str = parts[1]
        yaml.safe_load(frontmatter_str)  # 验证 YAML 格式
    except Exception as e:
        # 不要求完全的 YAML 解析，但要检查基本结构
        if 'name:' not in content[:500]:
            return {'ok': False, 'error': f'文件格式无效: {str(e)[:100]}'}
    
    # 创建本地目录
    workspace = OCLAW_HOME / f'workspace-{agent_id}' / 'skills' / skill_name
    workspace.mkdir(parents=True, exist_ok=True)
    skill_md = workspace / 'SKILL.md'
    
    # 写入 SKILL.md
    skill_md.write_text(content)
    
    # 保存源信息到 .source.json
    source_info = {
        'skillName': skill_name,
        'sourceUrl': source_url,
        'description': description,
        'addedAt': now_iso(),
        'lastUpdated': now_iso(),
        'checksum': _compute_checksum(content),
        'status': 'valid',
    }
    source_json = workspace / '.source.json'
    source_json.write_text(json.dumps(source_info, ensure_ascii=False, indent=2))
    
    # Re-sync agent config
    try:
        subprocess.run(['python3', str(SCRIPTS / 'sync_agent_config.py')], timeout=10)
    except Exception:
        pass
    
    return {
        'ok': True,
        'message': f'技能 {skill_name} 已从远程源添加到 {agent_id}',
        'skillName': skill_name,
        'agentId': agent_id,
        'source': source_url,
        'localPath': str(skill_md),
        'size': len(content),
        'addedAt': now_iso(),
    }


def get_remote_skills_list():
    """列表所有已添加的远程 skills 及其源信息"""
    remote_skills = []
    
    # 遍历所有 workspace
    for ws_dir in OCLAW_HOME.glob('workspace-*'):
        agent_id = ws_dir.name.replace('workspace-', '')
        skills_dir = ws_dir / 'skills'
        if not skills_dir.exists():
            continue
        
        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_name = skill_dir.name
            source_json = skill_dir / '.source.json'
            skill_md = skill_dir / 'SKILL.md'
            
            if not source_json.exists():
                # 本地创建的 skill，跳过
                continue
            
            try:
                source_info = json.loads(source_json.read_text())
                # 检查 SKILL.md 是否存在
                status = 'valid' if skill_md.exists() else 'not-found'
                remote_skills.append({
                    'skillName': skill_name,
                    'agentId': agent_id,
                    'sourceUrl': source_info.get('sourceUrl', ''),
                    'description': source_info.get('description', ''),
                    'localPath': str(skill_md),
                    'addedAt': source_info.get('addedAt', ''),
                    'lastUpdated': source_info.get('lastUpdated', ''),
                    'status': status,
                })
            except Exception:
                pass
    
    return {
        'ok': True,
        'remoteSkills': remote_skills,
        'count': len(remote_skills),
        'listedAt': now_iso(),
    }


def update_remote_skill(agent_id, skill_name):
    """更新已添加的远程 skill 为最新版本（重新从源 URL 下载）"""
    if not _SAFE_NAME_RE.match(agent_id):
        return {'ok': False, 'error': f'agentId 含非法字符: {agent_id}'}
    if not _SAFE_NAME_RE.match(skill_name):
        return {'ok': False, 'error': f'skillName 含非法字符: {skill_name}'}
    
    workspace = OCLAW_HOME / f'workspace-{agent_id}' / 'skills' / skill_name
    source_json = workspace / '.source.json'
    skill_md = workspace / 'SKILL.md'
    
    if not source_json.exists():
        return {'ok': False, 'error': f'技能 {skill_name} 不是远程 skill（无 .source.json）'}
    
    try:
        source_info = json.loads(source_json.read_text())
        source_url = source_info.get('sourceUrl', '')
        if not source_url:
            return {'ok': False, 'error': '源 URL 不存在'}
        
        # 重新下载
        result = add_remote_skill(agent_id, skill_name, source_url, 
                                  source_info.get('description', ''))
        if result['ok']:
            result['message'] = f'技能已更新'
            source_info_updated = json.loads(source_json.read_text())
            result['newVersion'] = source_info_updated.get('checksum', 'unknown')
        return result
    except Exception as e:
        return {'ok': False, 'error': f'更新失败: {str(e)[:100]}'}


def remove_remote_skill(agent_id, skill_name):
    """移除已添加的远程 skill"""
    if not _SAFE_NAME_RE.match(agent_id):
        return {'ok': False, 'error': f'agentId 含非法字符: {agent_id}'}
    if not _SAFE_NAME_RE.match(skill_name):
        return {'ok': False, 'error': f'skillName 含非法字符: {skill_name}'}
    
    workspace = OCLAW_HOME / f'workspace-{agent_id}' / 'skills' / skill_name
    if not workspace.exists():
        return {'ok': False, 'error': f'技能不存在: {skill_name}'}
    
    # 检查是否为远程 skill
    source_json = workspace / '.source.json'
    if not source_json.exists():
        return {'ok': False, 'error': f'技能 {skill_name} 不是远程 skill，无法通过此 API 移除'}
    
    try:
        # 删除整个 skill 目录
        import shutil
        shutil.rmtree(workspace)
        
        # Re-sync agent config
        try:
            subprocess.run(['python3', str(SCRIPTS / 'sync_agent_config.py')], timeout=10)
        except Exception:
            pass
        
        return {'ok': True, 'message': f'技能 {skill_name} 已从 {agent_id} 移除'}
    except Exception as e:
        return {'ok': False, 'error': f'移除失败: {str(e)[:100]}'}


def _compute_checksum(content: str) -> str:
    """计算内容的简单校验和（SHA256 的前16字符）"""
    import hashlib
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def push_to_feishu():
    """Push morning brief link to Feishu via webhook."""
    cfg = read_json(DATA / 'morning_brief_config.json', {})
    webhook = cfg.get('feishu_webhook', '').strip()
    if not webhook:
        return
    if not validate_url(webhook, allowed_schemes=('https',), allowed_domains=('open.feishu.cn', 'open.larksuite.com')):
        log.warning(f'飞书 Webhook URL 不合法: {webhook}')
        return
    brief = read_json(DATA / 'morning_brief.json', {})
    date_str = brief.get('date', '')
    total = sum(len(v) for v in (brief.get('categories') or {}).values())
    if not total:
        return
    cat_lines = []
    for cat, items in (brief.get('categories') or {}).items():
        if items:
            cat_lines.append(f'  {cat}: {len(items)} 条')
    summary = '\n'.join(cat_lines)
    date_fmt = date_str[:4] + '年' + date_str[4:6] + '月' + date_str[6:] + '日' if len(date_str) == 8 else date_str
    payload = json.dumps({
        'msg_type': 'interactive',
        'card': {
            'header': {'title': {'tag': 'plain_text', 'content': f'📰 天下要闻 · {date_fmt}'}, 'template': 'blue'},
            'elements': [
                {'tag': 'div', 'text': {'tag': 'lark_md', 'content': f'共 **{total}** 条要闻已更新\n{summary}'}},
                {'tag': 'action', 'actions': [{'tag': 'button', 'text': {'tag': 'plain_text', 'content': '🔗 查看完整简报'}, 'url': 'http://127.0.0.1:7891', 'type': 'primary'}]},
                {'tag': 'note', 'elements': [{'tag': 'plain_text', 'content': f"采集于 {brief.get('generated_at', '')}"}]}
            ]
        }
    }).encode()
    try:
        req = Request(webhook, data=payload, headers={'Content-Type': 'application/json'})
        resp = urlopen(req, timeout=10)
        print(f'[飞书] 推送成功 ({resp.status})')
    except Exception as e:
        print(f'[飞书] 推送失败: {e}', file=sys.stderr)


# 旨意标题最低要求
_MIN_TITLE_LEN = 10
_JUNK_TITLES = {
    '?', '？', '好', '好的', '是', '否', '不', '不是', '对', '了解', '收到',
    '嗯', '哦', '知道了', '开启了么', '可以', '不行', '行', 'ok', 'yes', 'no',
    '你去开启', '测试', '试试', '看看',
}


def handle_create_task(title, org='中书省', official='中书令', priority='normal', template_id='', params=None, target_dept=''):
    """从看板创建新任务（圣旨模板下旨）。"""
    if not title or not title.strip():
        return {'ok': False, 'error': '任务标题不能为空'}
    title = title.strip()
    # 剥离 Conversation info 元数据
    title = re.split(r'\n*Conversation info\s*\(', title, maxsplit=1)[0].strip()
    title = re.split(r'\n*```', title, maxsplit=1)[0].strip()
    # 清理常见前缀: "传旨:" "下旨:" 等
    title = re.sub(r'^(传旨|下旨)[：:\uff1a]\s*', '', title)
    # 标题质量校验：防止闲聊被误建为旨意
    if len(title) < _MIN_TITLE_LEN:
        return {'ok': False, 'error': f'标题过短（{len(title)}<{_MIN_TITLE_LEN}字），不像是旨意'}
    if title.lower() in _JUNK_TITLES:
        return {'ok': False, 'error': f'「{title}」不是有效旨意，请输入具体工作指令'}
    # 生成 task id: JJC-YYYYMMDD-NNN
    today = datetime.datetime.now().strftime('%Y%m%d')
    tasks = load_tasks()
    today_ids = [t['id'] for t in tasks if t.get('id', '').startswith(f'JJC-{today}-')]
    seq = 1
    if today_ids:
        nums = [int(tid.split('-')[-1]) for tid in today_ids if tid.split('-')[-1].isdigit()]
        seq = max(nums) + 1 if nums else 1
    task_id = f'JJC-{today}-{seq:03d}'
    # 正确流程起点：皇上 -> 太子分拣
    # target_dept 记录模板建议的最终执行部门（仅供尚书省派发参考）
    initial_org = '太子'
    new_task = {
        'id': task_id,
        'title': title,
        'official': official,
        'org': initial_org,
        'state': 'Taizi',
        'now': '等待太子接旨分拣',
        'eta': '-',
        'block': '无',
        'output': '',
        'ac': '',
        'priority': priority,
        'templateId': template_id,
        'templateParams': params or {},
        'flow_log': [{
            'at': now_iso(),
            'from': '皇上',
            'to': initial_org,
            'remark': f'下旨：{title}'
        }],
        'updatedAt': now_iso(),
    }
    if target_dept:
        new_task['targetDept'] = target_dept

    _ensure_scheduler(new_task)
    _scheduler_snapshot(new_task, 'create-task-initial')
    _scheduler_mark_progress(new_task, '任务创建')

    tasks.insert(0, new_task)
    save_tasks(tasks)
    log.info(f'创建任务: {task_id} | {title[:40]}')

    dispatch_for_state(task_id, new_task, 'Taizi', trigger='imperial-edict')

    return {'ok': True, 'taskId': task_id, 'message': f'旨意 {task_id} 已下达，正在派发给太子'}


def handle_review_action(task_id, action, comment=''):
    """门下省御批：准奏/封驳。"""
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    if task.get('state') not in ('Review', 'Menxia'):
        return {'ok': False, 'error': f'任务 {task_id} 当前状态为 {task.get("state")}，无法御批'}

    _ensure_scheduler(task)
    _scheduler_snapshot(task, f'review-before-{action}')
    run_id = _new_run_id()
    _acquire_lease(task, stage=task.get('state', ''), role='manual-review', owner_run_id=run_id, ttl_sec=180, force_takeover=True)
    version = task.get('_scheduler', {}).get('stateVersion')

    if action == 'approve':
        if task['state'] == 'Menxia':
            next_state = 'Assigned'
            next_now = '门下省准奏，移交尚书省派发'
            remark = f'✅ 准奏：{comment or "门下省审议通过"}'
            to_dept = '尚书省'
        else:  # Review
            next_state = 'Done'
            next_now = '御批通过，任务完成'
            remark = f'✅ 御批准奏：{comment or "审查通过"}'
            to_dept = '皇上'
    elif action == 'reject':
        round_num = (task.get('review_round') or 0) + 1
        task['review_round'] = round_num
        next_state = 'Zhongshu'
        next_now = f'封驳退回中书省修订（第{round_num}轮）'
        remark = f'🚫 封驳：{comment or "需要修改"}'
        to_dept = '中书省'
    else:
        return {'ok': False, 'error': f'未知操作: {action}'}

    commit = commit_state_change(
        task,
        action='manual_decide',
        reason_code='manual_review_decision',
        owner_run_id=run_id,
        expected_version=version,
        to_state=next_state,
        to_org=_derive_org_for_state(task, next_state, task.get('org', '')),
        now_text=next_now,
        block_text='无',
        flow_from='门下省' if next_state != 'Done' else '皇上',
        flow_to=to_dept,
        flow_remark=remark,
        force=True,
    )
    if not commit.get('committed'):
        save_tasks(tasks)
        return {'ok': False, 'error': f'{task_id} 审批提交失败: {commit.get("blockedBy")}'}
    _scheduler_mark_progress(task, f'审议动作 {action} -> {task.get("state")}', reason_code='manual_review_decision')
    _set_cooldown(task, 'noReassignUntil', _COOLDOWN_SECONDS['post_human_decision_reassign'])
    save_tasks(tasks)

    # 🚀 审批后自动派发对应 Agent
    new_state = task['state']
    if new_state not in ('Done',):
        dispatch_for_state(task_id, task, new_state, owner_run_id=run_id)

    label = '已准奏' if action == 'approve' else '已封驳'
    dispatched = ' (已自动派发 Agent)' if new_state != 'Done' else ''
    return {'ok': True, 'message': f'{task_id} {label}{dispatched}'}


# ══ Agent 在线状态检测 ══

_AGENT_DEPTS = [
    {'id':'taizi',   'label':'太子',  'emoji':'🤴', 'role':'太子',     'rank':'储君'},
    {'id':'zhongshu','label':'中书省','emoji':'📜', 'role':'中书令',   'rank':'正一品'},
    {'id':'menxia',  'label':'门下省','emoji':'🔍', 'role':'侍中',     'rank':'正一品'},
    {'id':'shangshu','label':'尚书省','emoji':'📮', 'role':'尚书令',   'rank':'正一品'},
    {'id':'hubu',    'label':'户部',  'emoji':'💰', 'role':'户部尚书', 'rank':'正二品'},
    {'id':'libu',    'label':'礼部',  'emoji':'📝', 'role':'礼部尚书', 'rank':'正二品'},
    {'id':'bingbu',  'label':'兵部',  'emoji':'⚔️', 'role':'兵部尚书', 'rank':'正二品'},
    {'id':'xingbu',  'label':'刑部',  'emoji':'⚖️', 'role':'刑部尚书', 'rank':'正二品'},
    {'id':'gongbu',  'label':'工部',  'emoji':'🔧', 'role':'工部尚书', 'rank':'正二品'},
    {'id':'libu_hr', 'label':'吏部',  'emoji':'👔', 'role':'吏部尚书', 'rank':'正二品'},
    {'id':'zaochao', 'label':'钦天监','emoji':'📰', 'role':'朝报官',   'rank':'正三品'},
]


def _check_gateway_alive():
    """检测 Gateway 进程是否在运行。"""
    try:
        result = subprocess.run(['pgrep', '-f', 'openclaw-gateway'],
                                capture_output=True, text=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def _check_gateway_probe():
    """通过 HTTP probe 检测 Gateway 是否响应。"""
    try:
        from urllib.request import urlopen
        resp = urlopen('http://127.0.0.1:18789/', timeout=3)
        return resp.status == 200
    except Exception:
        return False


def _get_agent_session_status(agent_id):
    """读取 Agent 的 sessions.json 获取活跃状态。
    返回: (last_active_ts_ms, session_count, is_busy)
    """
    sessions_file = OCLAW_HOME / 'agents' / agent_id / 'sessions' / 'sessions.json'
    if not sessions_file.exists():
        return 0, 0, False
    try:
        data = json.loads(sessions_file.read_text())
        if not isinstance(data, dict):
            return 0, 0, False
        session_count = len(data)
        last_ts = 0
        for v in data.values():
            ts = v.get('updatedAt', 0)
            if isinstance(ts, (int, float)) and ts > last_ts:
                last_ts = ts
        now_ms = int(datetime.datetime.now().timestamp() * 1000)
        age_ms = now_ms - last_ts if last_ts else 9999999999
        is_busy = age_ms <= 2 * 60 * 1000  # 2分钟内视为正在工作
        return last_ts, session_count, is_busy
    except Exception:
        return 0, 0, False


def _check_agent_process(agent_id):
    """检测是否有该 Agent 的 openclaw-agent 进程正在运行。"""
    try:
        result = subprocess.run(
            ['pgrep', '-f', f'openclaw.*--agent.*{agent_id}'],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def _check_agent_workspace(agent_id):
    """检查 Agent 工作空间是否存在。"""
    ws = OCLAW_HOME / f'workspace-{agent_id}'
    return ws.is_dir()


def get_agents_status():
    """获取所有 Agent 的在线状态。
    返回各 Agent 的:
    - status: 'running' | 'idle' | 'offline' | 'unconfigured'
    - lastActive: 最后活跃时间
    - sessions: 会话数
    - hasWorkspace: 工作空间是否存在
    - processAlive: 是否有进程在运行
    """
    gateway_alive = _check_gateway_alive()
    gateway_probe = _check_gateway_probe() if gateway_alive else False

    agents = []
    seen_ids = set()
    for dept in _AGENT_DEPTS:
        aid = dept['id']
        if aid in seen_ids:
            continue
        seen_ids.add(aid)

        has_workspace = _check_agent_workspace(aid)
        last_ts, sess_count, is_busy = _get_agent_session_status(aid)
        process_alive = _check_agent_process(aid)

        # 状态判定
        if not has_workspace:
            status = 'unconfigured'
            status_label = '❌ 未配置'
        elif not gateway_alive:
            status = 'offline'
            status_label = '🔴 Gateway 离线'
        elif process_alive or is_busy:
            status = 'running'
            status_label = '🟢 运行中'
        elif last_ts > 0:
            now_ms = int(datetime.datetime.now().timestamp() * 1000)
            age_ms = now_ms - last_ts
            if age_ms <= 10 * 60 * 1000:  # 10分钟内
                status = 'idle'
                status_label = '🟡 待命'
            elif age_ms <= 3600 * 1000:  # 1小时内
                status = 'idle'
                status_label = '⚪ 空闲'
            else:
                status = 'idle'
                status_label = '⚪ 休眠'
        else:
            status = 'idle'
            status_label = '⚪ 无记录'

        # 格式化最后活跃时间
        last_active_str = None
        if last_ts > 0:
            try:
                last_active_str = datetime.datetime.fromtimestamp(
                    last_ts / 1000
                ).strftime('%m-%d %H:%M')
            except Exception:
                pass

        agents.append({
            'id': aid,
            'label': dept['label'],
            'emoji': dept['emoji'],
            'role': dept['role'],
            'status': status,
            'statusLabel': status_label,
            'lastActive': last_active_str,
            'lastActiveTs': last_ts,
            'sessions': sess_count,
            'hasWorkspace': has_workspace,
            'processAlive': process_alive,
        })

    return {
        'ok': True,
        'gateway': {
            'alive': gateway_alive,
            'probe': gateway_probe,
            'status': '🟢 运行中' if gateway_probe else ('🟡 进程在但无响应' if gateway_alive else '🔴 未启动'),
        },
        'agents': agents,
        'checkedAt': now_iso(),
    }


def wake_agent(agent_id, message=''):
    """唤醒指定 Agent，发送一条心跳/唤醒消息。"""
    if not _SAFE_NAME_RE.match(agent_id):
        return {'ok': False, 'error': f'agent_id 非法: {agent_id}'}
    if not _check_agent_workspace(agent_id):
        return {'ok': False, 'error': f'{agent_id} 工作空间不存在，请先配置'}
    if not _check_gateway_alive():
        return {'ok': False, 'error': 'Gateway 未启动，请先运行 openclaw gateway start'}

    # agent_id 直接作为 runtime_id（openclaw agents list 中的注册名）
    runtime_id = agent_id
    msg = message or f'🔔 系统心跳检测 — 请回复 OK 确认在线。当前时间: {now_iso()}'

    def do_wake():
        try:
            cmd = ['openclaw', 'agent', '--agent', runtime_id, '-m', msg, '--timeout', '120']
            log.info(f'🔔 唤醒 {agent_id}...')
            # 带重试（最多2次）
            for attempt in range(1, 3):
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=130)
                if result.returncode == 0:
                    log.info(f'✅ {agent_id} 已唤醒')
                    return
                err_msg = result.stderr[:200] if result.stderr else result.stdout[:200]
                log.warning(f'⚠️ {agent_id} 唤醒失败(第{attempt}次): {err_msg}')
                if attempt < 2:
                    import time
                    time.sleep(5)
            log.error(f'❌ {agent_id} 唤醒最终失败')
        except subprocess.TimeoutExpired:
            log.error(f'❌ {agent_id} 唤醒超时(130s)')
        except Exception as e:
            log.warning(f'⚠️ {agent_id} 唤醒异常: {e}')
    threading.Thread(target=do_wake, daemon=True).start()

    return {'ok': True, 'message': f'{agent_id} 唤醒指令已发出，约10-30秒后生效'}


def _agent_label(agent_id):
    for dept in _AGENT_DEPTS:
        if dept.get('id') == agent_id:
            return dept.get('label') or agent_id
    return agent_id


def _extract_json_obj(text):
    if not text:
        return None
    s = text.strip()
    if s.startswith('```'):
        s = re.sub(r'^```[a-zA-Z]*\s*', '', s)
        s = re.sub(r'\s*```$', '', s)
    start = s.find('{')
    end = s.rfind('}')
    if start < 0 or end <= start:
        return None
    candidate = s[start:end + 1]
    try:
        obj = json.loads(candidate)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _run_agent_sync(agent_id, message, timeout_sec=120):
    if not _SAFE_NAME_RE.match(agent_id):
        raise ValueError(f'agent_id 非法: {agent_id}')
    cmd = ['openclaw', 'agent', '--agent', agent_id, '-m', message, '--timeout', str(timeout_sec)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec + 15)
    output = (result.stdout or '').strip()
    err = (result.stderr or '').strip()
    if result.returncode != 0:
        raise RuntimeError(err[:400] or output[:400] or f'agent {agent_id} failed')
    text = output or err
    return text[:12000]


def _load_court_session(session_id):
    items = atomic_json_read(DATA / 'court_discussions.json', [])
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict) and item.get('id') == session_id:
            return item
    return None


def _upsert_court_session(session):
    target = DATA / 'court_discussions.json'
    sid = session.get('id')
    if not sid:
        return

    def updater(items):
        if not isinstance(items, list):
            items = []
        out = []
        replaced = False
        for item in items:
            if isinstance(item, dict) and item.get('id') == sid:
                out.append(session)
                replaced = True
            else:
                out.append(item)
        if not replaced:
            out.insert(0, session)
        out.sort(key=lambda x: x.get('updatedAt', '') if isinstance(x, dict) else '', reverse=True)
        return out[:120]

    atomic_json_update(target, updater, [])


def _pick_moderator(selected):
    if 'menxia' in selected:
        return 'menxia'
    if 'taizi' in selected:
        return 'taizi'
    return selected[0]


def _friendly_agent_error(err_text):
    text = (err_text or '').strip()
    if 'patternProperties' in text and 'Invalid JSON payload' in text:
        return (
            '模型工具协议不兼容（patternProperties）。'
            '建议切换该大臣模型，或先排除此大臣后继续议政。'
        )
    if not text:
        return '未知错误'
    return text[:240]


def _append_emperor_note(session, note):
    n = (note or '').strip()
    if not n:
        return
    notes = session.setdefault('emperorNotes', [])
    notes.append({'at': now_iso(), 'text': n[:600]})
    if len(notes) > 30:
        session['emperorNotes'] = notes[-30:]


def _build_court_response(session, message=''):
    assessment = None
    assessments = session.get('assessments') or []
    if assessments:
        assessment = assessments[-1]
    return {
        'ok': True,
        'sessionId': session.get('id'),
        'status': session.get('status', 'ongoing'),
        'topic': session.get('topic', ''),
        'participants': session.get('participants', []),
        'rounds': int(session.get('rounds') or 0),
        'moderator': {
            'id': session.get('moderatorId', ''),
            'label': _agent_label(session.get('moderatorId', '')),
        },
        'assessment': assessment,
        'suggestedAction': session.get('suggestedAction', 'next'),
        'linkedTaskId': session.get('linkedTaskId', ''),
        'emperorNotes': session.get('emperorNotes', [])[-10:],
        'discussion': (session.get('discussion') or [])[-80:],
        'final': session.get('final'),
        'message': message or session.get('message', ''),
    }


def _run_court_round(session):
    topic = session.get('topic', '')
    selected = session.get('participants', [])
    round_no = int(session.get('rounds') or 0) + 1
    transcript = session.setdefault('discussion', [])
    round_entries = []
    emperor_notes = session.get('emperorNotes') or []
    latest_note = emperor_notes[-1].get('text', '') if emperor_notes else ''

    for idx, aid in enumerate(selected):
        label = _agent_label(aid)
        recent = transcript[-6:]
        recent_text = '\n\n'.join([
            f'[{x.get("agentLabel", "")}] {(x.get("reply", "") or "")[:400]}'
            for x in recent
        ]) if recent else '暂无'
        prompt = (
            f'你正在参与御前议政讨论，角色是「{label}」。\n'
            f'议题：{topic}\n'
            f'当前第 {round_no} 轮，你是本轮第 {idx + 1}/{len(selected)} 位发言。\n\n'
            f'最近讨论摘要：\n{recent_text}\n\n'
            f'皇上最新批示：{latest_note or "暂无"}\n\n'
            f'请输出四段（中文、简洁）：\n'
            f'【你认为最关键的澄清点】\n'
            f'【你看到的主要风险】\n'
            f'【你建议皇上现在做的决定】\n'
            f'【可直接执行的修改建议】'
        )
        reply = ''
        error_text = ''
        try:
            reply = _run_agent_sync(aid, prompt, timeout_sec=120)
        except Exception as e:
            error_text = _friendly_agent_error(str(e))
            reply = (
                f'【系统降级】{label} 本轮发言失败：{error_text}\n'
                f'【建议】请皇上选择“继续一轮”或调整参与大臣后再议。'
            )
        round_entries.append({
            'round': round_no,
            'turn': idx + 1,
            'totalTurns': len(selected),
            'agentId': aid,
            'agentLabel': label,
            'reply': reply[:4000],
            'error': bool(error_text),
            'at': now_iso(),
        })

    transcript.extend(round_entries)
    session['rounds'] = round_no

    moderator_id = session.get('moderatorId') or _pick_moderator(selected)
    moderator_label = _agent_label(moderator_id)
    recent_round_text = '\n\n'.join([
        f'[{x["agentLabel"]}] {x["reply"][:1200]}'
        for x in round_entries
    ])
    assess_prompt = (
        f'你现在是本轮议政主持人（{moderator_label}）。\n'
        f'议题：{topic}\n'
        f'第 {round_no} 轮各方意见如下：\n{recent_round_text}\n\n'
        f'请只输出 JSON（不要代码块）：\n'
        f'{{\n'
        f'  "recommend_stop": true,\n'
        f'  "reason": "为何建议结束/继续",\n'
        f'  "question_to_emperor": "请皇上拍板的问题",\n'
        f'  "focus_next_round": ["若继续，下一轮重点1", "重点2"],\n'
        f'  "draft_direction": "若现在结束，旨意草案应强调什么"\n'
        f'}}'
    )
    assess_raw = ''
    assess = {}
    try:
        assess_raw = _run_agent_sync(moderator_id, assess_prompt, timeout_sec=120)
        assess = _extract_json_obj(assess_raw) or {}
    except Exception as e:
        err_msg = _friendly_agent_error(str(e))
        has_error_entry = any(bool(x.get('error')) for x in round_entries)
        assess = {
            'recommend_stop': bool(has_error_entry),
            'reason': f'主持评估降级：{err_msg}',
            'question_to_emperor': '是否继续下一轮讨论，或直接终止该话题？',
            'focus_next_round': ['先排查失败大臣模型兼容性', '收敛为可执行目标'],
            'draft_direction': '若无共识建议先终止，若有可执行路径则交由太子办理',
        }
        assess_raw = str(e)[:2000]
    assessment = {
        'round': round_no,
        'moderatorId': moderator_id,
        'moderatorLabel': moderator_label,
        'recommend_stop': bool(assess.get('recommend_stop', False)),
        'reason': str(assess.get('reason') or '').strip(),
        'question_to_emperor': str(assess.get('question_to_emperor') or '').strip(),
        'focus_next_round': assess.get('focus_next_round') if isinstance(assess.get('focus_next_round'), list) else [],
        'draft_direction': str(assess.get('draft_direction') or '').strip(),
        'raw': assess_raw[:2000],
        'at': now_iso(),
    }
    session.setdefault('assessments', []).append(assessment)
    session['suggestedAction'] = 'finalize' if assessment['recommend_stop'] else 'next'
    session['status'] = 'ongoing'
    session['updatedAt'] = now_iso()
    session['message'] = (
        f'第 {round_no} 轮结束，{moderator_label}建议'
        f'{"可请皇上决定结束讨论" if assessment["recommend_stop"] else "继续讨论一轮"}'
    )
    return round_entries, assessment


def _finalize_court_session(session, force=False):
    topic = session.get('topic', '')
    transcript = session.get('discussion') or []
    if not transcript:
        return {'ok': False, 'error': '暂无讨论内容，无法生成结论'}

    moderator_id = session.get('moderatorId') or _pick_moderator(session.get('participants') or ['taizi'])
    discuss_pack = '\n\n'.join([
        f'[{x.get("agentLabel", "")}] {(x.get("reply", "") or "")[:1200]}'
        for x in transcript[-24:]
    ])
    assess_pack = '\n'.join([
        f'第{a.get("round")}轮建议: {a.get("reason", "")}'
        for a in (session.get('assessments') or [])[-6:]
    ])
    emperor_notes = session.get('emperorNotes') or []
    emperor_pack = '\n'.join([f'- {(x.get("text") or "")[:200]}' for x in emperor_notes[-6:]]) or '暂无'
    synth_prompt = (
        f'请作为太子秘书处，基于御前讨论输出最终可执行结论。\n'
        f'议题：{topic}\n'
        f'主持审议摘要：\n{assess_pack or "暂无"}\n\n'
        f'皇上批示：\n{emperor_pack}\n\n'
        f'讨论记录：\n{discuss_pack}\n\n'
        f'请只输出 JSON（不要代码块）：\n'
        f'{{\n'
        f'  "ready_for_edict": true,\n'
        f'  "clarified_goal": "一句话目标",\n'
        f'  "risks": ["风险1","风险2"],\n'
        f'  "questions_to_emperor": ["若仍有未定项，在此列出"],\n'
        f'  "recommended_edict": "可直接下旨的完整文本",\n'
        f'  "recommended_target_dept": "中书省",\n'
        f'  "recommended_priority": "normal"\n'
        f'}}'
    )
    synth_raw = ''
    synth = {}
    try:
        synth_raw = _run_agent_sync(moderator_id, synth_prompt, timeout_sec=120)
        synth = _extract_json_obj(synth_raw) or {}
    except Exception as e:
        err_msg = _friendly_agent_error(str(e))
        synth_raw = str(e)[:4000]
        has_error_entry = any(bool(x.get('error')) for x in transcript[-24:])
        synth = {
            'ready_for_edict': False if has_error_entry else True,
            'clarified_goal': topic[:80],
            'risks': [f'总结阶段降级：{err_msg}'],
            'questions_to_emperor': ['是否允许在降级结论下交由太子办理？'],
            'recommended_edict': f'请太子先组织可行性评估：{topic}',
            'recommended_target_dept': '中书省',
            'recommended_priority': 'normal',
        }
    final = {
        'ready_for_edict': bool(synth.get('ready_for_edict', False)),
        'clarified_goal': str(synth.get('clarified_goal') or '').strip(),
        'risks': synth.get('risks') if isinstance(synth.get('risks'), list) else [],
        'questions_to_emperor': (
            synth.get('questions_to_emperor') if isinstance(synth.get('questions_to_emperor'), list) else []
        ),
        'recommended_edict': str(synth.get('recommended_edict') or topic).strip(),
        'recommended_target_dept': str(synth.get('recommended_target_dept') or '中书省').strip(),
        'recommended_priority': str(synth.get('recommended_priority') or 'normal').strip(),
        'forceFinalized': bool(force),
        'raw': synth_raw[:4000],
    }
    if final['recommended_priority'] not in ('low', 'normal', 'high', 'critical'):
        final['recommended_priority'] = 'normal'
    if final['recommended_target_dept'] not in ('中书省', '尚书省', '礼部', '户部', '兵部', '刑部', '工部', '吏部'):
        final['recommended_target_dept'] = '中书省'

    session['final'] = final
    session['status'] = 'done'
    session['suggestedAction'] = 'finalize'
    session['updatedAt'] = now_iso()
    session['finalizedAt'] = now_iso()
    session['message'] = '议政讨论已结束，可直接下旨'
    return {'ok': True, 'final': final}


def handle_court_discuss(action='start', topic='', participants=None, session_id='', force=False, emperor_note=''):
    # emperor_note 用于皇上在每轮拍板前补充要求
    action = (action or 'start').strip().lower()
    if action in ('start', 'next', 'finalize', 'handoff') and not _check_gateway_alive():
        return {'ok': False, 'error': 'Gateway 未启动，请先运行 openclaw gateway start'}
    if action == 'start':
        topic = (topic or '').strip()
        if len(topic) < 10:
            return {'ok': False, 'error': '议题至少 10 个字'}

        allowed = {dept.get('id') for dept in _AGENT_DEPTS}
        selected = []
        for aid in (participants or []):
            if isinstance(aid, str):
                x = aid.strip()
                if x and x in allowed and x not in selected:
                    selected.append(x)
        if not selected:
            selected = ['taizi', 'zhongshu', 'menxia']
        if len(selected) > 6:
            selected = selected[:6]
        if len(selected) < 2:
            return {'ok': False, 'error': '至少选择 2 位大臣参与讨论'}
        for aid in selected:
            if not _check_agent_workspace(aid):
                return {'ok': False, 'error': f'Agent {aid} 工作空间不存在，请先配置'}

        session = {
            'id': f"CD-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}",
            'topic': topic,
            'participants': selected,
            'moderatorId': _pick_moderator(selected),
            'status': 'ongoing',
            'rounds': 0,
            'discussion': [],
            'assessments': [],
            'final': None,
            'emperorNotes': [],
            'createdAt': now_iso(),
            'updatedAt': now_iso(),
            'message': '议政会话已创建',
        }
        _append_emperor_note(session, emperor_note)
        try:
            _run_court_round(session)
        except Exception as e:
            return {'ok': False, 'error': f'首轮讨论失败: {str(e)[:240]}'}
        _upsert_court_session(session)
        return _build_court_response(session, session.get('message', ''))

    if not session_id:
        return {'ok': False, 'error': 'sessionId required'}
    session = _load_court_session(session_id)
    if not session:
        return {'ok': False, 'error': f'讨论会话 {session_id} 不存在'}
    _append_emperor_note(session, emperor_note)

    if action == 'status':
        session['updatedAt'] = now_iso()
        _upsert_court_session(session)
        return _build_court_response(session, '会话状态已返回')

    if action == 'next':
        if session.get('status') == 'done':
            return _build_court_response(session, '会话已结束，无需继续')
        try:
            _run_court_round(session)
        except Exception as e:
            return {'ok': False, 'error': f'继续讨论失败: {str(e)[:240]}'}
        _upsert_court_session(session)
        return _build_court_response(session, session.get('message', ''))

    if action == 'finalize':
        if session.get('status') == 'done':
            return _build_court_response(session, '会话已结束')
        finalized = _finalize_court_session(session, force=bool(force))
        if not finalized.get('ok'):
            return finalized
        _upsert_court_session(session)
        return _build_court_response(session, session.get('message', ''))

    if action == 'handoff':
        if session.get('status') == 'terminated':
            return _build_court_response(session, '话题已终止，无法交办')
        if session.get('linkedTaskId'):
            return _build_court_response(session, f'该话题已交办：{session.get("linkedTaskId")}')
        if not session.get('final'):
            finalized = _finalize_court_session(session, force=bool(force))
            if not finalized.get('ok'):
                return finalized
        final = session.get('final') or {}
        if not bool(final.get('ready_for_edict')) and not force:
            return {'ok': False, 'error': '当前结论未达到可下旨状态，如需强制交办请传 force=true'}

        title = str(final.get('recommended_edict') or session.get('topic') or '').strip()
        if not title:
            return {'ok': False, 'error': '结论缺少可交办内容'}
        target_dept = str(final.get('recommended_target_dept') or '').strip()
        priority = str(final.get('recommended_priority') or 'normal').strip()
        create = handle_create_task(
            title=title,
            org='中书省',
            official='中书令',
            priority=priority,
            template_id='court-discuss',
            params={'source': 'court-discuss', 'sessionId': session.get('id', '')},
            target_dept=target_dept,
        )
        if not create.get('ok'):
            return {'ok': False, 'error': create.get('error') or '交办失败'}

        session['status'] = 'handoffed'
        session['linkedTaskId'] = create.get('taskId', '')
        session['handoffAt'] = now_iso()
        session['updatedAt'] = now_iso()
        session['message'] = f'已交由太子办理：{session["linkedTaskId"]}'
        _upsert_court_session(session)
        return _build_court_response(session, session.get('message', ''))

    if action == 'terminate':
        if session.get('linkedTaskId'):
            return _build_court_response(session, f'该话题已交办：{session.get("linkedTaskId")}，不可终止')
        session['status'] = 'terminated'
        session['terminatedAt'] = now_iso()
        session['updatedAt'] = now_iso()
        session['suggestedAction'] = 'terminate'
        session['message'] = '皇上裁决：该话题不进入办理流程，已终止'
        _upsert_court_session(session)
        return _build_court_response(session, session.get('message', ''))

    return {'ok': False, 'error': f'unsupported action: {action}'}


# ══ Agent 实时活动读取 ══

# 状态 → agent_id 映射
_STATE_AGENT_MAP = {
    'Taizi': 'taizi',
    'Zhongshu': 'zhongshu',
    'Menxia': 'menxia',
    'Assigned': 'shangshu',
    'Doing': None,         # 六部，需从 org 推断
    'Review': 'shangshu',
    'Next': None,          # 待执行，从 org 推断
    'Pending': 'zhongshu', # 待处理，默认中书省
}
_ORG_AGENT_MAP = {
    '礼部': 'libu', '户部': 'hubu', '兵部': 'bingbu',
    '刑部': 'xingbu', '工部': 'gongbu', '吏部': 'libu_hr',
    '中书省': 'zhongshu', '门下省': 'menxia', '尚书省': 'shangshu',
}
_EXECUTION_DEPTS = {'礼部', '户部', '兵部', '刑部', '工部', '吏部'}

_TERMINAL_STATES = {'Done', 'Cancelled'}
_DIAG_MAX_LOG = 300
_FLOW_DEDUPE_WINDOW_SEC = 60

_CONTROL_STATE_BY_STATE = {
    'Pending': 'Pending',
    'Taizi': 'Taizi',
    'Zhongshu': 'Zhongshu',
    'Menxia': 'WaitingDecision',
    'Assigned': 'Assigned',
    'Next': 'Assigned',
    'Doing': 'Doing',
    'Review': 'WaitingDecision',
    'Done': 'Completed',
    'Cancelled': 'Cancelled',
    'Blocked': 'Blocked',
}

_CONTROL_ACTION_ALLOWLIST = {
    'Pending': {'dispatch', 'advance', 'noop'},
    'Taizi': {'dispatch', 'advance', 'retry', 'noop'},
    'Zhongshu': {'dispatch', 'advance', 'retry', 'escalate', 'noop'},
    'Assigned': {'dispatch', 'retry', 'escalate', 'advance', 'rollback', 'wait_human', 'noop'},
    'Doing': {'retry', 'writeback_retry', 'advance', 'wait_human', 'noop'},
    'ExecutionOutputReady': {'writeback_retry', 'wait_human', 'noop'},
    'WritebackPending': {'writeback_retry', 'wait_human', 'noop'},
    'RetryableFailure': {'retry', 'wait_human', 'rollback', 'noop'},
    'WaitingDecision': {'wait_human', 'manual_decide', 'noop'},
    'EscalationCandidate': {'escalate', 'wait_human', 'noop'},
    'Blocked': {'manual_decide', 'noop'},
    'Completed': {'manual_decide', 'noop'},
    'Cancelled': {'manual_decide', 'noop'},
}

_COOLDOWN_SECONDS = {
    'post_dispatch_escalate': 90,
    'post_dispatch_dispatch': 90,
    'post_doing_retry': 120,
    'post_human_decision_reassign': 180,
}


def _parse_iso(ts):
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except Exception:
        return None


def _new_run_id():
    return f'run-{uuid.uuid4().hex[:10]}'


def _lease_expired(lease, now_dt=None):
    if not isinstance(lease, dict):
        return True
    ttl_sec = int(lease.get('ttlSec') or 0)
    hb = _parse_iso(lease.get('heartbeatAt') or lease.get('acquiredAt'))
    if ttl_sec <= 0 or not hb:
        return True
    now_dt = now_dt or datetime.datetime.now(datetime.timezone.utc)
    return (now_dt - hb).total_seconds() > ttl_sec


def _sync_control_state(task):
    sched = task.setdefault('_scheduler', {})
    if not isinstance(sched, dict):
        sched = {}
        task['_scheduler'] = sched
    state = task.get('state', '')
    writeback = sched.get('writeback') or {}
    wb_status = writeback.get('status', '')
    if wb_status == 'WritebackPending':
        control_state = 'WritebackPending'
    elif wb_status == 'ExecutionOutputReady':
        control_state = 'ExecutionOutputReady'
    else:
        control_state = _CONTROL_STATE_BY_STATE.get(state, 'Assigned')
    sched['controlState'] = control_state
    return control_state


def _append_diagnostic(
    task,
    event_type,
    reason_code,
    details='',
    action='',
    dedupe_key='',
    suppress_window_sec=60,
):
    diag = task.setdefault('diagnostic_log', [])
    if dedupe_key:
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        for item in reversed(diag[-30:]):
            if item.get('dedupeKey') != dedupe_key:
                continue
            prev_dt = _parse_iso(item.get('at'))
            if prev_dt and (now_dt - prev_dt).total_seconds() <= suppress_window_sec:
                return False
            break
    diag.append({
        'at': now_iso(),
        'eventType': event_type,
        'action': action,
        'reasonCode': reason_code,
        'details': details,
        'dedupeKey': dedupe_key,
    })
    if len(diag) > _DIAG_MAX_LOG:
        task['diagnostic_log'] = diag[-_DIAG_MAX_LOG:]
    return True


def _set_cooldown(task, key, seconds):
    if seconds <= 0:
        return
    sched = _ensure_scheduler(task)
    cds = sched.setdefault('cooldowns', {})
    until_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=int(seconds))
    cds[key] = until_dt.isoformat().replace('+00:00', 'Z')


def _cooldown_remaining(task, key):
    sched = _ensure_scheduler(task)
    cds = sched.get('cooldowns') or {}
    until_dt = _parse_iso(cds.get(key))
    if not until_dt:
        return 0
    left = (until_dt - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
    return max(0, int(left))


def _cooldown_block_for_action(task, action):
    blocks = []
    if action in ('dispatch', 'retry'):
        left = _cooldown_remaining(task, 'noDispatchUntil')
        if left > 0:
            blocks.append(('dispatchCooldown', left))
    if action == 'retry' and task.get('state') == 'Doing':
        left = _cooldown_remaining(task, 'noRetryUntil')
        if left > 0:
            blocks.append(('retryCooldown', left))
    if action == 'escalate':
        left = _cooldown_remaining(task, 'noEscalateUntil')
        if left > 0:
            blocks.append(('escalateCooldown', left))
        left2 = _cooldown_remaining(task, 'noReassignUntil')
        if left2 > 0:
            blocks.append(('reassignCooldown', left2))
    return blocks


def _action_allowed(task, action):
    sched = _ensure_scheduler(task)
    control_state = _sync_control_state(task)
    allowed = _CONTROL_ACTION_ALLOWLIST.get(control_state, {'noop'})
    if action not in allowed:
        return {
            'ok': False,
            'blockedBy': 'stateGuard',
            'controlState': control_state,
            'allowedActions': sorted(allowed),
        }
    cd_blocks = _cooldown_block_for_action(task, action)
    if cd_blocks:
        code, left = cd_blocks[0]
        return {
            'ok': False,
            'blockedBy': code,
            'cooldownSec': left,
            'controlState': control_state,
            'allowedActions': sorted(allowed),
        }
    return {'ok': True, 'controlState': control_state, 'allowedActions': sorted(allowed)}


def _acquire_lease(task, stage, role, owner_run_id, ttl_sec=180, force_takeover=False):
    sched = _ensure_scheduler(task)
    lease = sched.setdefault('lease', {})
    now_ts = now_iso()
    current_owner = lease.get('ownerRunId', '')
    expired = _lease_expired(lease)
    if force_takeover or not current_owner or expired:
        lease.update({
            'stage': stage,
            'role': role,
            'ownerRunId': owner_run_id,
            'acquiredAt': now_ts,
            'heartbeatAt': now_ts,
            'ttlSec': int(ttl_sec),
        })
        return {'ok': True, 'takenOver': bool(current_owner and current_owner != owner_run_id)}
    if current_owner == owner_run_id:
        lease['heartbeatAt'] = now_ts
        lease['ttlSec'] = int(ttl_sec)
        return {'ok': True, 'takenOver': False}
    return {'ok': False, 'blockedBy': 'leaseBusy', 'ownerRunId': current_owner}


def _renew_lease(task, owner_run_id, ttl_sec=None):
    sched = _ensure_scheduler(task)
    lease = sched.setdefault('lease', {})
    if lease.get('ownerRunId') != owner_run_id:
        return False
    lease['heartbeatAt'] = now_iso()
    if ttl_sec:
        lease['ttlSec'] = int(ttl_sec)
    return True


def _release_lease(task, owner_run_id):
    sched = _ensure_scheduler(task)
    lease = sched.get('lease') or {}
    if lease.get('ownerRunId') != owner_run_id:
        return False
    lease['ownerRunId'] = ''
    lease['releasedAt'] = now_iso()
    return True


def _ensure_scheduler(task):
    sched = task.setdefault('_scheduler', {})
    if not isinstance(sched, dict):
        sched = {}
        task['_scheduler'] = sched
    sched.setdefault('enabled', True)
    sched.setdefault('stallThresholdSec', 180)
    sched.setdefault('maxRetry', 1)
    sched.setdefault('retryCount', 0)
    sched.setdefault('escalationLevel', 0)
    sched.setdefault('maxStateAgeSec', 900)
    sched.setdefault('autoRollback', True)
    sched.setdefault('autoAdvance', True)
    if not sched.get('lastProgressAt'):
        sched['lastProgressAt'] = task.get('updatedAt') or now_iso()
    cur_state = task.get('state', '')
    if not sched.get('stateSince'):
        sched['stateSince'] = task.get('updatedAt') or now_iso()
    if sched.get('stateName') != cur_state:
        sched['stateName'] = cur_state
        sched['stateSince'] = task.get('updatedAt') or now_iso()
    if 'stallSince' not in sched:
        sched['stallSince'] = None
    if 'awaitingEmperorDecision' not in sched:
        sched['awaitingEmperorDecision'] = False
    if 'lastDispatchStatus' not in sched:
        sched['lastDispatchStatus'] = 'idle'
    sched.setdefault('stateVersion', 0)
    sched.setdefault('lastCommit', {})
    sched.setdefault('lastAction', {})
    sched.setdefault('cooldowns', {})
    lease = sched.setdefault('lease', {})
    if not isinstance(lease, dict):
        lease = {}
        sched['lease'] = lease
    lease.setdefault('stage', '')
    lease.setdefault('role', '')
    lease.setdefault('ownerRunId', '')
    lease.setdefault('acquiredAt', '')
    lease.setdefault('heartbeatAt', '')
    lease.setdefault('ttlSec', 180)
    writeback = sched.setdefault('writeback', {})
    if not isinstance(writeback, dict):
        writeback = {}
        sched['writeback'] = writeback
    writeback.setdefault('status', 'idle')
    writeback.setdefault('retryCount', 0)
    writeback.setdefault('maxRetry', 2)
    writeback.setdefault('firstOutputAt', '')
    writeback.setdefault('lastCommittedAt', '')
    writeback.setdefault('lastError', '')
    writeback.setdefault('lastDispatchOutput', '')
    if 'snapshot' not in sched:
        sched['snapshot'] = {
            'state': task.get('state', ''),
            'org': task.get('org', ''),
            'now': task.get('now', ''),
            'savedAt': now_iso(),
            'note': 'init',
        }
    _sync_control_state(task)
    return sched


def _scheduler_add_flow(task, remark, to='', reason_code=''):
    flow_log = task.setdefault('flow_log', [])
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    entry = {
        'at': now_iso(),
        'from': '太子调度',
        'to': to or task.get('org', ''),
        'remark': f'🧭 {remark}'
    }
    if reason_code:
        entry['reasonCode'] = reason_code

    if flow_log:
        last = flow_log[-1]
        last_dt = _parse_iso(last.get('at'))
        if (
            last.get('from') == entry['from']
            and last.get('to') == entry['to']
            and last.get('remark') == entry['remark']
            and ((now_dt - last_dt).total_seconds() <= _FLOW_DEDUPE_WINDOW_SEC if last_dt else False)
        ):
            return False
    flow_log.append(entry)
    return True


def _scheduler_snapshot(task, note=''):
    sched = _ensure_scheduler(task)
    sched['snapshot'] = {
        'state': task.get('state', ''),
        'org': task.get('org', ''),
        'now': task.get('now', ''),
        'savedAt': now_iso(),
        'note': note or 'snapshot',
    }


def _scheduler_mark_progress(task, note='', reason_code='progress_update'):
    sched = _ensure_scheduler(task)
    sched['lastProgressAt'] = now_iso()
    sched['stallSince'] = None
    sched['awaitingEmperorDecision'] = False
    sched['decisionPacket'] = None
    sched['retryCount'] = 0
    sched['escalationLevel'] = 0
    sched['lastEscalatedAt'] = None
    if note:
        _scheduler_add_flow(task, f'进展确认：{note}', reason_code=reason_code)


def _scheduler_mark_state_change(task, new_state, reason_code='state_change'):
    sched = _ensure_scheduler(task)
    sched['stateName'] = new_state
    sched['stateSince'] = now_iso()
    sched['awaitingEmperorDecision'] = False
    sched['decisionPacket'] = None
    if new_state == 'Doing':
        _set_cooldown(task, 'noRetryUntil', _COOLDOWN_SECONDS['post_doing_retry'])
    _sync_control_state(task)
    sched['lastAction'] = {
        'action': 'state_change',
        'reasonCode': reason_code,
        'at': now_iso(),
    }


def commit_state_change(
    task,
    action,
    reason_code,
    owner_run_id='',
    expected_version=None,
    to_state=None,
    to_org=None,
    now_text=None,
    block_text=None,
    flow_remark='',
    flow_from='太子调度',
    flow_to='',
    force=False,
):
    sched = _ensure_scheduler(task)
    current_state = task.get('state', '')
    current_version = int(sched.get('stateVersion') or 0)

    if expected_version is not None and int(expected_version) != current_version:
        _append_diagnostic(
            task,
            event_type='state_commit_blocked',
            action=action,
            reason_code='version_conflict',
            details=f'expected={expected_version}, current={current_version}',
            dedupe_key=f'{task.get("id")}:version:{action}:{expected_version}:{current_version}',
        )
        sched['lastCommit'] = {
            'at': now_iso(),
            'action': action,
            'reasonCode': reason_code,
            'result': 'blocked',
            'blockedBy': 'versionConflict',
            'currentVersion': current_version,
            'expectedVersion': expected_version,
        }
        return {'ok': False, 'committed': False, 'blockedBy': 'versionConflict', 'currentVersion': current_version}

    if owner_run_id and not force:
        lease = sched.get('lease') or {}
        lease_owner = lease.get('ownerRunId') or ''
        if not lease_owner or lease_owner != owner_run_id or _lease_expired(lease):
            _append_diagnostic(
                task,
                event_type='state_commit_blocked',
                action=action,
                reason_code='stale_owner',
                details=f'owner={owner_run_id}, leaseOwner={lease_owner}',
                dedupe_key=f'{task.get("id")}:owner:{action}:{owner_run_id}:{lease_owner}',
            )
            sched['lastCommit'] = {
                'at': now_iso(),
                'action': action,
                'reasonCode': reason_code,
                'result': 'blocked',
                'blockedBy': 'staleOwner',
                'ownerRunId': owner_run_id,
                'leaseOwner': lease_owner,
            }
            return {'ok': False, 'committed': False, 'blockedBy': 'staleOwner', 'leaseOwner': lease_owner}
        _renew_lease(task, owner_run_id)

    if not force:
        allowed = _action_allowed(task, action)
        if not allowed.get('ok'):
            _append_diagnostic(
                task,
                event_type='state_commit_blocked',
                action=action,
                reason_code='action_blocked',
                details=str(allowed),
                dedupe_key=f'{task.get("id")}:blocked:{action}:{allowed.get("blockedBy")}',
            )
            sched['lastCommit'] = {
                'at': now_iso(),
                'action': action,
                'reasonCode': reason_code,
                'result': 'blocked',
                'blockedBy': allowed.get('blockedBy'),
                'controlState': allowed.get('controlState'),
            }
            return {'ok': False, 'committed': False, 'blockedBy': allowed.get('blockedBy')}

    state_changed = False
    prev_state = current_state
    if to_state is not None and to_state != current_state:
        task['state'] = to_state
        state_changed = True
        if to_org is not None:
            task['org'] = to_org
        else:
            task['org'] = _derive_org_for_state(task, to_state, task.get('org', ''))
        _scheduler_mark_state_change(task, to_state, reason_code=reason_code)
    elif to_org is not None:
        task['org'] = to_org

    if now_text is not None:
        task['now'] = now_text
    if block_text is not None:
        task['block'] = block_text

    if flow_remark:
        task.setdefault('flow_log', []).append({
            'at': now_iso(),
            'from': flow_from,
            'to': flow_to or task.get('org', ''),
            'remark': flow_remark,
            'reasonCode': reason_code,
        })

    sched['stateVersion'] = current_version + 1
    sched['lastAction'] = {'action': action, 'reasonCode': reason_code, 'at': now_iso()}
    sched['lastCommit'] = {
        'at': now_iso(),
        'action': action,
        'reasonCode': reason_code,
        'result': 'committed',
        'ownerRunId': owner_run_id,
        'fromState': prev_state,
        'toState': task.get('state', ''),
        'stateChanged': state_changed,
        'version': sched['stateVersion'],
    }
    _sync_control_state(task)
    task['updatedAt'] = now_iso()
    return {'ok': True, 'committed': True, 'stateChanged': state_changed, 'stateVersion': sched['stateVersion']}


def _build_decision_packet(task, state, stalled_sec=0, state_age_sec=0):
    title = (task.get('title') or '').strip()
    now_text = (task.get('now') or '').strip()
    progress_log = task.get('progress_log') or []
    flow_log = task.get('flow_log') or []
    latest_progress = (progress_log[-1].get('text') if progress_log else '') or now_text
    latest_flow = (flow_log[-1].get('remark') if flow_log else '')

    if state == 'Menxia':
        question = '请拍板：是否准奏并移交尚书省执行？'
        options = [
            {
                'id': 'approve',
                'label': '准奏推进',
                'impact': '状态变更为 Assigned，尚书省开始派发六部执行（推荐）',
            },
            {
                'id': 'reject',
                'label': '封驳退回',
                'impact': '状态回到 Zhongshu，要求中书省补充/修改方案后再审',
            },
        ]
        recommended = 'approve'
    elif state == 'Review':
        question = '请拍板：是否验收通过并结案？'
        options = [
            {
                'id': 'approve',
                'label': '验收通过',
                'impact': '状态变更为 Done，任务归档结束（推荐）',
            },
            {
                'id': 'reject',
                'label': '退回整改',
                'impact': '状态回到 Zhongshu，按封驳意见继续修订',
            },
        ]
        recommended = 'approve'
    else:
        question = '请拍板：是否继续当前推进策略？'
        options = [
            {'id': 'approve', 'label': '继续推进', 'impact': '按当前流程继续自动调度（推荐）'},
            {'id': 'reject', 'label': '人工干预', 'impact': '改为人工指定下一步或回滚'},
        ]
        recommended = 'approve'

    evidence = [
        f'任务: {task.get("id", "")}',
        f'状态: {_STATE_LABELS.get(state, state)}',
        f'停滞: {int(stalled_sec)}秒',
        f'驻留: {int(state_age_sec)}秒',
    ]
    if title:
        evidence.append(f'旨意: {title[:120]}')
    if latest_progress:
        evidence.append(f'最近进展: {latest_progress[:200]}')
    if latest_flow:
        evidence.append(f'最近流转: {latest_flow[:200]}')

    return {
        'state': state,
        'question': question,
        'options': options,
        'recommended': recommended,
        'evidence': evidence,
        'generatedAt': now_iso(),
    }


def _update_task_scheduler(task_id, updater):
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return False
    sched = _ensure_scheduler(task)
    updater(task, sched)
    _sync_control_state(task)
    task['updatedAt'] = now_iso()
    save_tasks(tasks)
    return True


def _retry_writeback_for_task(task_id, owner_run_id=''):
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}

    sched = _ensure_scheduler(task)
    wb = sched.setdefault('writeback', {})
    lease = sched.get('lease') or {}
    lease_owner = lease.get('ownerRunId') or ''
    if owner_run_id and lease_owner and owner_run_id != lease_owner:
        _append_diagnostic(
            task,
            event_type='writeback_retry_blocked',
            action='writeback_retry',
            reason_code='stale_owner',
            details=f'owner={owner_run_id}, leaseOwner={lease_owner}',
            dedupe_key=f'{task_id}:writeback_retry:stale_owner',
        )
        save_tasks(tasks)
        return {'ok': False, 'blockedBy': 'staleOwner'}

    output = wb.get('lastDispatchOutput', '')
    if not output:
        wb['status'] = 'ExecutionOutputReady'
        wb['lastError'] = 'missing_dispatch_output'
        _append_diagnostic(
            task,
            event_type='writeback_retry_blocked',
            action='writeback_retry',
            reason_code='missing_dispatch_output',
            details=f'task={task_id}',
            dedupe_key=f'{task_id}:writeback_retry:missing_output',
        )
        save_tasks(tasks)
        return {'ok': False, 'blockedBy': 'missingDispatchOutput'}

    bridge = _bridge_apply_kanban_commands(task_id, output)
    if bridge.get('attempted', 0) > 0 and bridge.get('applied', 0) >= bridge.get('attempted', 0):
        wb['status'] = 'idle'
        wb['retryCount'] = 0
        wb['lastCommittedAt'] = now_iso()
        wb['lastError'] = ''
        _scheduler_add_flow(task, '写回重试成功，提交已落板', reason_code='writeback_retry_success')
        _release_lease(task, owner_run_id or lease_owner)
        save_tasks(tasks)
        return {'ok': True, 'committed': True, 'attempted': bridge.get('attempted', 0), 'applied': bridge.get('applied', 0)}

    wb['status'] = 'WritebackPending'
    wb['retryCount'] = int(wb.get('retryCount') or 0) + 1
    wb['lastError'] = '; '.join((bridge.get('errors') or [])[:2]) or 'writeback_retry_failed'
    _append_diagnostic(
        task,
        event_type='writeback_retry_failed',
        action='writeback_retry',
        reason_code='writeback_retry_failed',
        details=wb['lastError'],
        dedupe_key=f'{task_id}:writeback_retry:failed',
    )
    _scheduler_add_flow(task, '写回重试失败，等待下一次提交重试', reason_code='writeback_retry_failed')
    save_tasks(tasks)
    return {
        'ok': False,
        'committed': False,
        'attempted': bridge.get('attempted', 0),
        'applied': bridge.get('applied', 0),
        'error': wb.get('lastError', ''),
    }


def get_scheduler_state(task_id):
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    sched = _ensure_scheduler(task)
    last_progress = _parse_iso(sched.get('lastProgressAt') or task.get('updatedAt'))
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    stalled_sec = 0
    if last_progress:
        stalled_sec = max(0, int((now_dt - last_progress).total_seconds()))
    state_since = _parse_iso(sched.get('stateSince') or task.get('updatedAt'))
    state_age_sec = 0
    if state_since:
        state_age_sec = max(0, int((now_dt - state_since).total_seconds()))
    state_age_limit = int(sched.get('maxStateAgeSec') or 0)
    return {
        'ok': True,
        'taskId': task_id,
        'state': task.get('state', ''),
        'org': task.get('org', ''),
        'scheduler': sched,
        'controlState': sched.get('controlState'),
        'lease': sched.get('lease'),
        'lastAction': sched.get('lastAction'),
        'writeback': sched.get('writeback'),
        'decision': sched.get('decisionPacket'),
        'stalledSec': stalled_sec,
        'stateAgeSec': state_age_sec,
        'stateAgeLimitSec': state_age_limit,
        'checkedAt': now_iso(),
    }


def get_scheduler_metrics(task_id=''):
    tasks = load_tasks()
    if task_id:
        tasks = [t for t in tasks if t.get('id') == task_id]
        if not tasks:
            return {'ok': False, 'error': f'任务 {task_id} 不存在'}

    task_metrics = []
    total_dispatch_attempts = 0
    total_unique_steps = 0
    total_invalid_control = 0
    total_control_actions = 0
    writeback_lags = []

    now_dt = datetime.datetime.now(datetime.timezone.utc)
    for task in tasks:
        sched = _ensure_scheduler(task)
        progress_log = task.get('progress_log') or []
        diagnostic_log = task.get('diagnostic_log') or []
        flow_log = task.get('flow_log') or []

        dispatch_attempts = int(sched.get('dispatchAttempts') or 0)
        unique_execution_steps = len({
            (p.get('agent', ''), p.get('text', ''), p.get('state', ''))
            for p in progress_log if p.get('text')
        })
        unique_execution_steps = max(unique_execution_steps, 1 if dispatch_attempts > 0 else 0)
        amplification_ratio = round(dispatch_attempts / unique_execution_steps, 2) if unique_execution_steps else 0.0

        control_actions = sum(
            1 for f in flow_log
            if isinstance(f, dict) and str(f.get('from', '')).startswith('太子调度')
        )
        invalid_control = sum(
            1 for d in diagnostic_log
            if d.get('eventType') in ('state_commit_blocked', 'control_blocked')
        )
        invalid_ratio = round(invalid_control / control_actions, 3) if control_actions else 0.0

        wb = sched.get('writeback') or {}
        first_output = _parse_iso(wb.get('firstOutputAt'))
        committed_at = _parse_iso(wb.get('lastCommittedAt'))
        writeback_lag_sec = None
        if first_output:
            end_dt = committed_at or now_dt
            writeback_lag_sec = max(0, int((end_dt - first_output).total_seconds()))
            writeback_lags.append(writeback_lag_sec)

        task_metrics.append({
            'taskId': task.get('id', ''),
            'state': task.get('state', ''),
            'dispatchAttempts': dispatch_attempts,
            'uniqueExecutionSteps': unique_execution_steps,
            'dispatchAmplificationRatio': amplification_ratio,
            'controlActions': control_actions,
            'invalidControlActions': invalid_control,
            'invalidControlRatio': invalid_ratio,
            'writebackLagSec': writeback_lag_sec,
            'writebackStatus': wb.get('status', 'idle'),
        })

        total_dispatch_attempts += dispatch_attempts
        total_unique_steps += unique_execution_steps
        total_invalid_control += invalid_control
        total_control_actions += control_actions

    global_amp = round(total_dispatch_attempts / total_unique_steps, 2) if total_unique_steps else 0.0
    global_invalid = round(total_invalid_control / total_control_actions, 3) if total_control_actions else 0.0
    avg_writeback_lag = (
        round(sum(writeback_lags) / len(writeback_lags), 2) if writeback_lags else None
    )

    return {
        'ok': True,
        'taskId': task_id or '',
        'metrics': task_metrics,
        'summary': {
            'taskCount': len(task_metrics),
            'dispatchAttempts': total_dispatch_attempts,
            'uniqueExecutionSteps': total_unique_steps,
            'dispatchAmplificationRatio': global_amp,
            'controlActions': total_control_actions,
            'invalidControlActions': total_invalid_control,
            'invalidControlRatio': global_invalid,
            'avgWritebackLagSec': avg_writeback_lag,
        },
        'checkedAt': now_iso(),
    }


def handle_scheduler_action(task_id, action, reason='', expected_version=None, owner_run_id='', recovery_target=''):
    action = (action or '').strip()
    if action == 'retry':
        return handle_scheduler_retry(task_id, reason)
    if action == 'escalate':
        return handle_scheduler_escalate(task_id, reason)
    if action == 'rollback':
        return handle_scheduler_rollback(task_id, reason)
    if action in ('wait_human', 'manual_decide'):
        tasks = load_tasks()
        task = next((t for t in tasks if t.get('id') == task_id), None)
        if not task:
            return {'ok': False, 'error': f'任务 {task_id} 不存在'}
        _ensure_scheduler(task)
        sched = task.get('_scheduler') or {}
        run_id = owner_run_id or _new_run_id()
        _acquire_lease(task, stage=task.get('state', ''), role='manual', owner_run_id=run_id, ttl_sec=180, force_takeover=True)
        target = (recovery_target or '').strip()
        reason_code = 'manual_human_decision'
        to_state = None
        to_org = None
        now_text = task.get('now', '')
        block_text = task.get('block', '')
        trigger_dispatch_state = ''
        trigger_writeback_retry = False

        if target == 'continue_execution':
            reason_code = 'human_continue_execution'
            to_state = 'Doing'
            to_org = _derive_org_for_state(task, 'Doing', task.get('org', ''))
            now_text = '👑 皇上裁决：继续执行'
            block_text = '无'
            trigger_dispatch_state = 'Doing'
        elif target == 'continue_writeback':
            reason_code = 'human_continue_writeback'
            now_text = '👑 皇上裁决：继续提交写回'
            block_text = '无'
            trigger_writeback_retry = True
        elif target == 'reassign':
            reason_code = 'human_reassign'
            to_state = 'Assigned'
            to_org = '尚书省'
            now_text = '👑 皇上裁决：改派尚书省重新派发'
            block_text = '无'
            trigger_dispatch_state = 'Assigned'
        elif target == 'terminate':
            reason_code = 'human_terminate'
            to_state = 'Cancelled'
            to_org = task.get('org', '')
            now_text = f'👑 皇上裁决：终止任务（{reason or "人工终止"}）'
            block_text = reason or '皇上终止'

        commit = commit_state_change(
            task,
            action='manual_decide',
            reason_code=reason_code,
            owner_run_id=run_id,
            expected_version=expected_version if expected_version is not None else sched.get('stateVersion'),
            to_state=to_state,
            to_org=to_org,
            now_text=now_text,
            block_text=block_text,
            flow_from='皇上',
            flow_remark=f'👑 人工裁决：{reason or "无"}（目标：{target or "仅记录"}）',
            force=True,
        )
        if not commit.get('committed'):
            save_tasks(tasks)
            return {'ok': False, 'error': f'提交失败: {commit.get("blockedBy")}'}
        if trigger_writeback_retry:
            wb = sched.setdefault('writeback', {})
            wb['status'] = 'WritebackPending'
            wb['lastError'] = wb.get('lastError') or 'human_resume_writeback'
        _set_cooldown(task, 'noReassignUntil', _COOLDOWN_SECONDS['post_human_decision_reassign'])
        save_tasks(tasks)
        if trigger_dispatch_state:
            dispatch_for_state(task_id, task, trigger_dispatch_state, trigger='human-decision', owner_run_id=run_id)
        if trigger_writeback_retry:
            _retry_writeback_for_task(task_id, owner_run_id=run_id)
        return {'ok': True, 'message': f'{task_id} 已记录人工裁决', 'recoveryTarget': target or 'record_only'}
    return {'ok': False, 'error': f'不支持的 action: {action}'}


def handle_scheduler_commit(payload):
    task_id = (payload.get('taskId') or '').strip()
    action = (payload.get('action') or '').strip()
    if not task_id or not action:
        return {'ok': False, 'error': 'taskId/action required'}
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    _ensure_scheduler(task)
    sched = task.get('_scheduler') or {}
    owner_run_id = (payload.get('ownerRunId') or '').strip() or _new_run_id()
    expected_version = payload.get('expectedVersion')
    to_state = payload.get('toState')
    to_org = payload.get('toOrg')
    reason_code = (payload.get('reasonCode') or '').strip() or f'manual_commit_{action}'
    context = payload.get('context') if isinstance(payload.get('context'), dict) else {}
    flow_remark = context.get('flowRemark', f'🔒 统一提交：{action}')
    flow_from = context.get('flowFrom', '太子调度')
    flow_to = context.get('flowTo', '')
    force = bool(payload.get('force', False))
    lease = sched.get('lease') or {}
    lease_owner = lease.get('ownerRunId') or ''
    if force:
        _acquire_lease(
            task,
            stage=task.get('state', ''),
            role='manual-commit',
            owner_run_id=owner_run_id,
            ttl_sec=180,
            force_takeover=True,
        )
    elif not lease_owner:
        _acquire_lease(
            task,
            stage=task.get('state', ''),
            role='manual-commit',
            owner_run_id=owner_run_id,
            ttl_sec=180,
            force_takeover=False,
        )
    elif lease_owner == owner_run_id:
        _renew_lease(task, owner_run_id, ttl_sec=180)

    commit = commit_state_change(
        task,
        action=action,
        reason_code=reason_code,
        owner_run_id=owner_run_id,
        expected_version=expected_version if expected_version is not None else sched.get('stateVersion'),
        to_state=to_state,
        to_org=to_org,
        now_text=context.get('nowText'),
        block_text=context.get('blockText'),
        flow_remark=flow_remark,
        flow_from=flow_from,
        flow_to=flow_to,
        force=force,
    )
    save_tasks(tasks)
    return {
        'ok': bool(commit.get('ok')),
        'committed': bool(commit.get('committed')),
        'blockedBy': commit.get('blockedBy'),
        'currentVersion': task.get('_scheduler', {}).get('stateVersion'),
        'taskId': task_id,
    }


def handle_scheduler_retry(task_id, reason=''):
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    state = task.get('state', '')
    if state in _TERMINAL_STATES or state == 'Blocked':
        return {'ok': False, 'error': f'任务 {task_id} 当前状态 {state} 不支持重试'}

    sched = _ensure_scheduler(task)
    run_id = _new_run_id()
    lease_result = _acquire_lease(task, stage=state, role='scheduler', owner_run_id=run_id, ttl_sec=180)
    if not lease_result.get('ok'):
        _append_diagnostic(
            task,
            event_type='control_blocked',
            action='retry',
            reason_code='lease_busy',
            details=str(lease_result),
            dedupe_key=f'{task_id}:retry:lease_busy',
        )
        save_tasks(tasks)
        return {'ok': False, 'error': f'任务 {task_id} 当前被其它流程持有租约'}

    commit = commit_state_change(
        task,
        action='retry',
        reason_code='manual_retry',
        owner_run_id=run_id,
        expected_version=sched.get('stateVersion'),
        flow_remark=f'🔁 手动重试：{reason or "人工触发"}',
        flow_from='皇上',
    )
    if not commit.get('committed'):
        save_tasks(tasks)
        return {'ok': False, 'error': f'任务 {task_id} 重试被拒绝: {commit.get("blockedBy")}'}

    sched['retryCount'] = int(sched.get('retryCount') or 0) + 1
    sched['lastRetryAt'] = now_iso()
    sched['lastDispatchTrigger'] = 'taizi-retry'
    _scheduler_add_flow(task, f'触发重试第{sched["retryCount"]}次：{reason or "超时未推进"}', reason_code='manual_retry')
    task['updatedAt'] = now_iso()
    save_tasks(tasks)

    dispatch_for_state(task_id, task, state, trigger='taizi-retry', owner_run_id=run_id)
    return {'ok': True, 'message': f'{task_id} 已触发重试派发', 'retryCount': sched['retryCount']}


def handle_scheduler_escalate(task_id, reason=''):
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    state = task.get('state', '')
    if state in _TERMINAL_STATES:
        return {'ok': False, 'error': f'任务 {task_id} 已结束，无需升级'}

    sched = _ensure_scheduler(task)
    run_id = _new_run_id()
    lease_result = _acquire_lease(task, stage=state, role='scheduler', owner_run_id=run_id, ttl_sec=180)
    if not lease_result.get('ok'):
        _append_diagnostic(
            task,
            event_type='control_blocked',
            action='escalate',
            reason_code='lease_busy',
            details=str(lease_result),
            dedupe_key=f'{task_id}:escalate:lease_busy',
        )
        save_tasks(tasks)
        return {'ok': False, 'error': f'任务 {task_id} 当前被其它流程持有租约'}

    commit = commit_state_change(
        task,
        action='escalate',
        reason_code='manual_escalate',
        owner_run_id=run_id,
        expected_version=sched.get('stateVersion'),
        flow_remark=f'⬆️ 手动升级：{reason or "人工触发"}',
        flow_from='皇上',
    )
    if not commit.get('committed'):
        save_tasks(tasks)
        return {'ok': False, 'error': f'任务 {task_id} 升级被拒绝: {commit.get("blockedBy")}'}

    current_level = int(sched.get('escalationLevel') or 0)
    next_level = min(current_level + 1, 2)
    target = 'menxia' if next_level == 1 else 'shangshu'
    target_label = '门下省' if next_level == 1 else '尚书省'

    sched['escalationLevel'] = next_level
    sched['lastEscalatedAt'] = now_iso()
    _scheduler_add_flow(task, f'升级到{target_label}协调：{reason or "任务停滞"}', to=target_label, reason_code='manual_escalate')
    task['updatedAt'] = now_iso()
    save_tasks(tasks)

    msg = (
        f'🧭 太子调度升级通知\n'
        f'任务ID: {task_id}\n'
        f'当前状态: {state}\n'
        f'停滞处理: 请你介入协调推进\n'
        f'原因: {reason or "任务超过阈值未推进"}\n'
        f'⚠️ 看板已有任务，请勿重复创建。'
    )
    wake_agent(target, msg)

    return {'ok': True, 'message': f'{task_id} 已升级至{target_label}', 'escalationLevel': next_level}


def handle_scheduler_rollback(task_id, reason=''):
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    sched = _ensure_scheduler(task)
    run_id = _new_run_id()
    lease_result = _acquire_lease(task, stage=task.get('state', ''), role='scheduler', owner_run_id=run_id, ttl_sec=180, force_takeover=True)
    if not lease_result.get('ok'):
        _append_diagnostic(
            task,
            event_type='control_blocked',
            action='rollback',
            reason_code='lease_busy',
            details=str(lease_result),
            dedupe_key=f'{task_id}:rollback:lease_busy',
        )
        save_tasks(tasks)
        return {'ok': False, 'error': f'任务 {task_id} 当前被其它流程持有租约'}

    snapshot = sched.get('snapshot') or {}
    snap_state = snapshot.get('state')
    if not snap_state:
        return {'ok': False, 'error': f'任务 {task_id} 无可用回滚快照'}

    old_state = task.get('state', '')
    commit = commit_state_change(
        task,
        action='rollback',
        reason_code='manual_rollback',
        owner_run_id=run_id,
        expected_version=sched.get('stateVersion'),
        to_state=snap_state,
        to_org=snapshot.get('org', task.get('org', '')),
        now_text=f'↩️ 太子调度自动回滚：{reason or "恢复到上个稳定节点"}',
        block_text='无',
        flow_remark=f'↩️ 手动回滚：{old_state} → {snap_state}，原因：{reason or "人工触发"}',
        flow_from='皇上',
    )
    if not commit.get('committed'):
        save_tasks(tasks)
        return {'ok': False, 'error': f'任务 {task_id} 回滚被拒绝: {commit.get("blockedBy")}'}

    sched['retryCount'] = 0
    sched['escalationLevel'] = 0
    sched['stallSince'] = None
    sched['lastProgressAt'] = now_iso()
    _scheduler_add_flow(task, f'执行回滚：{old_state} → {snap_state}，原因：{reason or "停滞恢复"}', reason_code='manual_rollback')
    task['updatedAt'] = now_iso()
    save_tasks(tasks)

    if snap_state not in _TERMINAL_STATES:
        dispatch_for_state(task_id, task, snap_state, trigger='taizi-rollback')

    return {'ok': True, 'message': f'{task_id} 已回滚到 {snap_state}'}


def decide_next_action(task, threshold_sec=180):
    sched = _ensure_scheduler(task)
    state = task.get('state', '')
    task_threshold = int(sched.get('stallThresholdSec') or threshold_sec)
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    last_progress = _parse_iso(sched.get('lastProgressAt') or task.get('updatedAt'))
    if not last_progress:
        last_progress = now_dt
    stalled_sec = max(0, int((now_dt - last_progress).total_seconds()))
    state_since = _parse_iso(sched.get('stateSince') or task.get('updatedAt'))
    state_age_sec = max(0, int((now_dt - state_since).total_seconds())) if state_since else 0
    state_age_limit = max(task_threshold, int(sched.get('maxStateAgeSec') or (task_threshold * 4)))
    age_overdue = (
        sched.get('autoAdvance', True)
        and state in _AUTO_ADVANCE_SAFE_STATES
        and state_age_sec >= state_age_limit
    )

    if state in _TERMINAL_STATES or task.get('archived') or state == 'Blocked':
        return {'action': 'noop', 'reasonCode': 'state_terminal_or_blocked'}

    writeback = sched.get('writeback') or {}
    wb_status = writeback.get('status')
    if wb_status == 'WritebackPending':
        retry_count = int(writeback.get('retryCount') or 0)
        max_retry = int(writeback.get('maxRetry') or 2)
        if retry_count < max_retry:
            allow = _action_allowed(task, 'writeback_retry')
            if allow.get('ok'):
                return {
                    'action': 'writeback_retry',
                    'reasonCode': 'writeback_retry_budget',
                    'stalledSec': stalled_sec,
                    'stateAgeSec': state_age_sec,
                }
        return {
            'action': 'await-decision',
            'reasonCode': 'writeback_pending_need_human',
            'stalledSec': stalled_sec,
            'stateAgeSec': state_age_sec,
        }

    if stalled_sec < task_threshold and not age_overdue:
        return {'action': 'noop', 'reasonCode': 'below_threshold'}

    if age_overdue and stalled_sec < task_threshold:
        flow = _STATE_FLOW.get(state)
        if flow:
            next_state, _, _, _ = flow
            return {
                'action': 'auto-advance',
                'reasonCode': 'state_age_overdue',
                'toState': next_state,
                'stalledSec': stalled_sec,
                'stateAgeSec': state_age_sec,
            }

    if state in _RISK_DECISION_STATES:
        return {
            'action': 'await-decision',
            'reasonCode': 'risk_state_stalled',
            'stalledSec': stalled_sec,
            'stateAgeSec': state_age_sec,
        }

    retry_count = int(sched.get('retryCount') or 0)
    max_retry = max(0, int(sched.get('maxRetry') or 1))
    if retry_count < max_retry:
        allow = _action_allowed(task, 'retry')
        if allow.get('ok'):
            return {
                'action': 'retry',
                'reasonCode': 'stall_retry_budget',
                'stalledSec': stalled_sec,
                'stateAgeSec': state_age_sec,
            }
        return {'action': 'noop', 'reasonCode': f'blocked_{allow.get("blockedBy", "retry")}'}

    level = int(sched.get('escalationLevel') or 0)
    if level < 2:
        allow = _action_allowed(task, 'escalate')
        if allow.get('ok'):
            next_level = level + 1
            target = 'menxia' if next_level == 1 else 'shangshu'
            target_label = '门下省' if next_level == 1 else '尚书省'
            return {
                'action': 'escalate',
                'reasonCode': 'stall_need_escalation',
                'to': target,
                'toLabel': target_label,
                'stalledSec': stalled_sec,
                'stateAgeSec': state_age_sec,
            }
        return {'action': 'noop', 'reasonCode': f'blocked_{allow.get("blockedBy", "escalate")}'}

    if sched.get('autoAdvance', True) and state in _AUTO_ADVANCE_SAFE_STATES:
        flow = _STATE_FLOW.get(state)
        if flow:
            next_state, _, _, _ = flow
            return {
                'action': 'auto-advance',
                'reasonCode': 'stall_auto_advance',
                'toState': next_state,
                'stalledSec': stalled_sec,
                'stateAgeSec': state_age_sec,
            }

    if sched.get('autoRollback', True):
        snapshot = sched.get('snapshot') or {}
        snap_state = snapshot.get('state')
        if snap_state and snap_state != state:
            return {
                'action': 'rollback',
                'reasonCode': 'stall_auto_rollback',
                'toState': snap_state,
                'stalledSec': stalled_sec,
                'stateAgeSec': state_age_sec,
            }

    return {'action': 'wait_human', 'reasonCode': 'no_safe_auto_action'}


def handle_scheduler_scan(threshold_sec=180):
    threshold_sec = max(30, int(threshold_sec or 180))
    tasks = load_tasks()
    pending_dispatches = []
    pending_escalates = []
    pending_auto_advances = []
    pending_writeback_retries = []
    actions = []
    changed = False

    for task in tasks:
        task_id = task.get('id', '')
        state = task.get('state', '')
        if not task_id or state in _TERMINAL_STATES or task.get('archived'):
            continue
        if state == 'Blocked':
            continue

        sched = _ensure_scheduler(task)
        decision = decide_next_action(task, threshold_sec)
        action = decision.get('action', 'noop')
        if action in ('noop', 'wait_human'):
            continue
        run_id = _new_run_id()
        version = sched.get('stateVersion')
        lease_result = _acquire_lease(
            task,
            stage=state,
            role='scheduler',
            owner_run_id=run_id,
            ttl_sec=180,
            force_takeover=(action in ('rollback',)),
        )
        if not lease_result.get('ok'):
            _append_diagnostic(
                task,
                event_type='control_blocked',
                action=action,
                reason_code='lease_busy',
                details=str(lease_result),
                dedupe_key=f'{task_id}:{action}:lease_busy',
            )
            changed = True
            continue

        if action == 'retry':
            commit = commit_state_change(
                task,
                action='retry',
                reason_code=decision.get('reasonCode', 'stall_retry_budget'),
                owner_run_id=run_id,
                expected_version=version,
                flow_remark=f'🔁 自动重试：停滞{decision.get("stalledSec", 0)}秒',
            )
            if not commit.get('committed'):
                changed = True
                continue
            sched['retryCount'] = int(sched.get('retryCount') or 0) + 1
            sched['lastRetryAt'] = now_iso()
            sched['lastDispatchTrigger'] = 'taizi-scan-retry'
            pending_dispatches.append((task_id, state, run_id, 'taizi-scan-retry'))
            actions.append({
                'taskId': task_id,
                'action': 'retry',
                'stalledSec': decision.get('stalledSec'),
                'reasonCode': decision.get('reasonCode'),
            })
            changed = True
            continue

        if action == 'escalate':
            next_level = min(int(sched.get('escalationLevel') or 0) + 1, 2)
            target = decision.get('to') or ('menxia' if next_level == 1 else 'shangshu')
            target_label = decision.get('toLabel') or ('门下省' if next_level == 1 else '尚书省')
            commit = commit_state_change(
                task,
                action='escalate',
                reason_code=decision.get('reasonCode', 'stall_need_escalation'),
                owner_run_id=run_id,
                expected_version=version,
                flow_remark=f'⬆️ 自动升级：停滞{decision.get("stalledSec", 0)}秒，升级至{target_label}协调',
            )
            if not commit.get('committed'):
                changed = True
                continue
            sched['escalationLevel'] = next_level
            sched['lastEscalatedAt'] = now_iso()
            pending_escalates.append((task_id, state, target, target_label, decision.get('stalledSec', 0)))
            actions.append({
                'taskId': task_id,
                'action': 'escalate',
                'to': target_label,
                'stalledSec': decision.get('stalledSec'),
                'reasonCode': decision.get('reasonCode'),
            })
            changed = True
            continue

        if action == 'auto-advance':
            flow = _STATE_FLOW.get(state)
            if flow:
                next_state, _, to_dept, _ = flow
                _scheduler_snapshot(task, f'auto-advance-before-{state}')
                commit = commit_state_change(
                    task,
                    action='advance',
                    reason_code=decision.get('reasonCode', 'auto_advance'),
                    owner_run_id=run_id,
                    expected_version=version,
                    to_state=next_state,
                    to_org=_derive_org_for_state(task, next_state, task.get('org', '')),
                    now_text=(
                        f'⏩ 太子调度自动推进：{_STATE_LABELS.get(state, state)}'
                        f' → {_STATE_LABELS.get(next_state, next_state)}'
                    ),
                    block_text='无',
                    flow_remark=(
                        f'⏩ 自动推进：{_STATE_LABELS.get(state, state)} → '
                        f'{_STATE_LABELS.get(next_state, next_state)}'
                    ),
                    flow_to=to_dept,
                )
                if not commit.get('committed'):
                    changed = True
                    continue
                _scheduler_mark_progress(task, f'自动推进 {state} -> {next_state}', reason_code='auto_advance')
                pending_auto_advances.append((task_id, next_state))
                actions.append({
                    'taskId': task_id,
                    'action': 'auto-advance',
                    'fromState': state,
                    'toState': next_state,
                    'reasonCode': decision.get('reasonCode'),
                    'stalledSec': decision.get('stalledSec'),
                    'stateAgeSec': decision.get('stateAgeSec'),
                })
                changed = True
                continue

        if action == 'writeback_retry':
            commit = commit_state_change(
                task,
                action='writeback_retry',
                reason_code=decision.get('reasonCode', 'writeback_retry_budget'),
                owner_run_id=run_id,
                expected_version=version,
                flow_remark=f'🧩 自动提交重试：停滞{decision.get("stalledSec", 0)}秒',
            )
            if not commit.get('committed'):
                changed = True
                continue
            wb = sched.setdefault('writeback', {})
            wb['retryCount'] = int(wb.get('retryCount') or 0) + 1
            wb['status'] = 'WritebackPending'
            wb['lastError'] = wb.get('lastError') or 'writeback_pending_retry'
            _scheduler_add_flow(
                task,
                f'写回失败，触发提交重试第{wb["retryCount"]}次',
                reason_code=decision.get('reasonCode', 'writeback_retry_budget'),
            )
            pending_writeback_retries.append((task_id, run_id))
            actions.append({
                'taskId': task_id,
                'action': 'writeback_retry',
                'stalledSec': decision.get('stalledSec'),
                'reasonCode': decision.get('reasonCode'),
            })
            changed = True
            continue

        if action == 'await-decision':
            stalled_sec = int(decision.get('stalledSec') or 0)
            state_age_sec = int(decision.get('stateAgeSec') or 0)
            sched['decisionPacket'] = _build_decision_packet(task, state, stalled_sec, state_age_sec)
            if not sched.get('awaitingEmperorDecision'):
                sched['awaitingEmperorDecision'] = True
                task['block'] = '风险节点停滞，等待皇上裁决'
                task['now'] = f'⚠️ 风险节点{_STATE_LABELS.get(state, state)}停滞，等待皇上裁决'
                _scheduler_add_flow(
                    task,
                    f'风险节点停滞{stalled_sec}秒，暂停自动推进并等待皇上裁决',
                    reason_code=decision.get('reasonCode', 'await_decision')
                )
                actions.append({
                    'taskId': task_id,
                    'action': 'await-decision',
                    'state': state,
                    'stalledSec': stalled_sec,
                    'question': sched['decisionPacket'].get('question'),
                    'reasonCode': decision.get('reasonCode'),
                })
                task['updatedAt'] = now_iso()
                changed = True
            continue

        if action == 'rollback' and sched.get('autoRollback', True):
            snapshot = sched.get('snapshot') or {}
            snap_state = snapshot.get('state')
            if snap_state and snap_state != state:
                old_state = state
                commit = commit_state_change(
                    task,
                    action='rollback',
                    reason_code=decision.get('reasonCode', 'auto_rollback'),
                    owner_run_id=run_id,
                    expected_version=version,
                    to_state=snap_state,
                    to_org=snapshot.get('org', task.get('org', '')),
                    now_text='↩️ 太子调度自动回滚到稳定节点',
                    block_text='无',
                    flow_remark=f'↩️ 连续停滞，自动回滚：{old_state} → {snap_state}',
                )
                if not commit.get('committed'):
                    changed = True
                    continue
                sched['retryCount'] = 0
                sched['escalationLevel'] = 0
                sched['stallSince'] = None
                sched['lastProgressAt'] = now_iso()
                pending_dispatches.append((task_id, snap_state, run_id, 'taizi-auto-rollback'))
                actions.append({'taskId': task_id, 'action': 'rollback', 'toState': snap_state})
                changed = True

    if changed:
        save_tasks(tasks)

    for task_id, state, owner_run_id, trigger in pending_dispatches:
        retry_task = next((t for t in tasks if t.get('id') == task_id), None)
        if retry_task:
            dispatch_for_state(task_id, retry_task, state, trigger=trigger, owner_run_id=owner_run_id)

    for task_id, owner_run_id in pending_writeback_retries:
        _retry_writeback_for_task(task_id, owner_run_id=owner_run_id)

    for task_id, state, target, target_label, stalled_sec in pending_escalates:
        msg = (
            f'🧭 太子调度升级通知\n'
            f'任务ID: {task_id}\n'
            f'当前状态: {state}\n'
            f'已停滞: {stalled_sec} 秒\n'
            f'请立即介入协调推进\n'
            f'⚠️ 看板已有任务，请勿重复创建。'
        )
        wake_agent(target, msg)

    for task_id, state in pending_auto_advances:
        adv_task = next((t for t in tasks if t.get('id') == task_id), None)
        if adv_task and state not in _TERMINAL_STATES:
            dispatch_for_state(task_id, adv_task, state, trigger='taizi-auto-advance')

    return {
        'ok': True,
        'thresholdSec': threshold_sec,
        'actions': actions,
        'count': len(actions),
        'checkedAt': now_iso(),
    }


def _startup_recover_queued_dispatches():
    """服务启动后扫描 lastDispatchStatus=queued 的任务，重新派发。
    解决：kill -9 重启导致派发线程中断、任务永久卡住的问题。"""
    tasks = load_tasks()
    recovered = 0
    for task in tasks:
        task_id = task.get('id', '')
        state = task.get('state', '')
        if not task_id or state in _TERMINAL_STATES or task.get('archived'):
            continue
        sched = task.get('_scheduler') or {}
        if sched.get('lastDispatchStatus') == 'queued':
            log.info(f'🔄 启动恢复: {task_id} 状态={state} 上次派发未完成，重新派发')
            sched['lastDispatchTrigger'] = 'startup-recovery'
            dispatch_for_state(task_id, task, state, trigger='startup-recovery')
            recovered += 1
    if recovered:
        log.info(f'✅ 启动恢复完成: 重新派发 {recovered} 个任务')
    else:
        log.info(f'✅ 启动恢复: 无需恢复')


def handle_repair_flow_order():
    """修复历史任务中首条流转为“皇上->中书省”的错序问题。"""
    tasks = load_tasks()
    fixed = 0
    fixed_ids = []

    for task in tasks:
        task_id = task.get('id', '')
        if not task_id.startswith('JJC-'):
            continue
        flow_log = task.get('flow_log') or []
        if not flow_log:
            continue

        first = flow_log[0]
        if first.get('from') != '皇上' or first.get('to') != '中书省':
            continue

        first['to'] = '太子'
        remark = first.get('remark', '')
        if isinstance(remark, str) and remark.startswith('下旨：'):
            first['remark'] = remark

        if task.get('state') == 'Zhongshu' and task.get('org') == '中书省' and len(flow_log) == 1:
            task['state'] = 'Taizi'
            task['org'] = '太子'
            task['now'] = '等待太子接旨分拣'

        task['updatedAt'] = now_iso()
        fixed += 1
        fixed_ids.append(task_id)

    if fixed:
        save_tasks(tasks)

    return {
        'ok': True,
        'count': fixed,
        'taskIds': fixed_ids[:80],
        'more': max(0, fixed - 80),
        'checkedAt': now_iso(),
    }


def _collect_message_text(msg):
    """收集消息中的可检索文本，用于 task_id/关键词过滤。"""
    parts = []
    for c in msg.get('content', []) or []:
        ctype = c.get('type')
        if ctype == 'text' and c.get('text'):
            parts.append(str(c.get('text', '')))
        elif ctype == 'thinking' and c.get('thinking'):
            parts.append(str(c.get('thinking', '')))
        elif ctype == 'tool_use':
            parts.append(json.dumps(c.get('input', {}), ensure_ascii=False))
    details = msg.get('details') or {}
    for key in ('output', 'stdout', 'stderr', 'message'):
        val = details.get(key)
        if isinstance(val, str) and val:
            parts.append(val)
    return ''.join(parts)


def _parse_activity_entry(item):
    """将 session jsonl 的 message 统一解析成看板活动条目。"""
    msg = item.get('message') or {}
    role = str(msg.get('role', '')).strip().lower()
    ts = item.get('timestamp', '')

    if role == 'assistant':
        text = ''
        thinking = ''
        tool_calls = []
        for c in msg.get('content', []) or []:
            if c.get('type') == 'text' and c.get('text') and not text:
                text = str(c.get('text', '')).strip()
            elif c.get('type') == 'thinking' and c.get('thinking') and not thinking:
                thinking = str(c.get('thinking', '')).strip()[:200]
            elif c.get('type') == 'tool_use':
                tool_calls.append({
                    'name': c.get('name', ''),
                    'input_preview': json.dumps(c.get('input', {}), ensure_ascii=False)[:100]
                })
        if not (text or thinking or tool_calls):
            return None
        entry = {'at': ts, 'kind': 'assistant'}
        if text:
            entry['text'] = text[:300]
        if thinking:
            entry['thinking'] = thinking
        if tool_calls:
            entry['tools'] = tool_calls
        return entry

    if role in ('toolresult', 'tool_result'):
        details = msg.get('details') or {}
        code = details.get('exitCode')
        if code is None:
            code = details.get('code', details.get('status'))
        output = ''
        for c in msg.get('content', []) or []:
            if c.get('type') == 'text' and c.get('text'):
                output = str(c.get('text', '')).strip()[:200]
                break
        if not output:
            for key in ('output', 'stdout', 'stderr', 'message'):
                val = details.get(key)
                if isinstance(val, str) and val.strip():
                    output = val.strip()[:200]
                    break

        entry = {
            'at': ts,
            'kind': 'tool_result',
            'tool': msg.get('toolName', msg.get('name', '')),
            'exitCode': code,
            'output': output,
        }
        duration_ms = details.get('durationMs')
        if isinstance(duration_ms, (int, float)):
            entry['durationMs'] = int(duration_ms)
        return entry

    if role == 'user':
        text = ''
        for c in msg.get('content', []) or []:
            if c.get('type') == 'text' and c.get('text'):
                text = str(c.get('text', '')).strip()
                break
        if not text:
            return None
        return {'at': ts, 'kind': 'user', 'text': text[:200]}

    return None


def get_agent_activity(agent_id, limit=30, task_id=None):
    """从 Agent 的 session jsonl 读取最近活动。
    如果 task_id 不为空，只返回提及该 task_id 的相关条目。
    """
    sessions_dir = OCLAW_HOME / 'agents' / agent_id / 'sessions'
    if not sessions_dir.exists():
        return []

    # 扫描所有 jsonl（按修改时间倒序），优先最新
    jsonl_files = sorted(sessions_dir.glob('*.jsonl'), key=lambda f: f.stat().st_mtime, reverse=True)
    if not jsonl_files:
        return []

    entries = []
    # 如果需要按 task_id 过滤，可能需要扫描多个文件
    files_to_scan = jsonl_files[:3] if task_id else jsonl_files[:1]

    for session_file in files_to_scan:
        try:
            lines = session_file.read_text(errors='ignore').splitlines()
        except Exception:
            continue

        # 正向扫描以保持时间顺序；如果有 task_id，收集提及 task_id 的条目
        for ln in lines:
            try:
                item = json.loads(ln)
            except Exception:
                continue
            msg = item.get('message') or {}
            all_text = _collect_message_text(msg)

            # task_id 过滤：只保留提及 task_id 的条目
            if task_id and task_id not in all_text:
                continue
            entry = _parse_activity_entry(item)
            if entry:
                entries.append(entry)

            if len(entries) >= limit:
                break
        if len(entries) >= limit:
            break

    # 只保留最后 limit 条
    return entries[-limit:]


def _extract_keywords(title):
    """从任务标题中提取有意义的关键词（用于 session 内容匹配）。"""
    stop = {'的', '了', '在', '是', '有', '和', '与', '或', '一个', '一篇', '关于', '进行',
            '写', '做', '请', '把', '给', '用', '要', '需要', '面向', '风格', '包含',
            '出', '个', '不', '可以', '应该', '如何', '怎么', '什么', '这个', '那个'}
    # 提取英文词
    en_words = re.findall(r'[a-zA-Z][\w.-]{1,}', title)
    # 提取 2-4 字中文词组（更短的颗粒度）
    cn_words = re.findall(r'[\u4e00-\u9fff]{2,4}', title)
    all_words = en_words + cn_words
    kws = [w for w in all_words if w not in stop and len(w) >= 2]
    # 去重保序
    seen = set()
    unique = []
    for w in kws:
        if w.lower() not in seen:
            seen.add(w.lower())
            unique.append(w)
    return unique[:8]  # 最多 8 个关键词


def get_agent_activity_by_keywords(agent_id, keywords, limit=20):
    """从 agent session 中按关键词匹配获取活动条目。
    找到包含关键词的 session 文件，只读该文件的活动。
    """
    sessions_dir = OCLAW_HOME / 'agents' / agent_id / 'sessions'
    if not sessions_dir.exists():
        return []

    jsonl_files = sorted(sessions_dir.glob('*.jsonl'), key=lambda f: f.stat().st_mtime, reverse=True)
    if not jsonl_files:
        return []

    # 找到包含关键词的 session 文件
    target_file = None
    for sf in jsonl_files[:5]:
        try:
            content = sf.read_text(errors='ignore')
        except Exception:
            continue
        hits = sum(1 for kw in keywords if kw.lower() in content.lower())
        if hits >= min(2, len(keywords)):
            target_file = sf
            break

    if not target_file:
        return []

    # 解析 session 文件，按 user 消息分割为对话段
    # 找到包含关键词的对话段，只返回该段的活动
    try:
        lines = target_file.read_text(errors='ignore').splitlines()
    except Exception:
        return []

    # 第一遍：找到关键词匹配的 user 消息位置
    user_msg_indices = []  # (line_index, user_text)
    for i, ln in enumerate(lines):
        try:
            item = json.loads(ln)
        except Exception:
            continue
        msg = item.get('message') or {}
        if msg.get('role') == 'user':
            text = ''
            for c in msg.get('content', []):
                if c.get('type') == 'text' and c.get('text'):
                    text += c['text']
            user_msg_indices.append((i, text))

    # 找到与关键词匹配度最高的 user 消息
    best_idx = -1
    best_hits = 0
    for line_idx, utext in user_msg_indices:
        hits = sum(1 for kw in keywords if kw.lower() in utext.lower())
        if hits > best_hits:
            best_hits = hits
            best_idx = line_idx

    # 确定对话段的行范围：从匹配的 user 消息到下一个 user 消息之前
    if best_idx >= 0 and best_hits >= min(2, len(keywords)):
        # 找下一个 user 消息的位置
        next_user_idx = len(lines)
        for line_idx, _ in user_msg_indices:
            if line_idx > best_idx:
                next_user_idx = line_idx
                break
        start_line = best_idx
        end_line = next_user_idx
    else:
        # 没找到匹配的对话段，返回空
        return []

    # 第二遍：只解析对话段内的行
    entries = []
    for ln in lines[start_line:end_line]:
        try:
            item = json.loads(ln)
        except Exception:
            continue
        entry = _parse_activity_entry(item)
        if entry:
            entries.append(entry)

    return entries[-limit:]


def get_agent_latest_segment(agent_id, limit=20):
    """获取 Agent 最新一轮对话段（最后一条 user 消息起的所有内容）。
    用于活跃任务没有精确匹配时，展示 Agent 的实时工作状态。
    """
    sessions_dir = OCLAW_HOME / 'agents' / agent_id / 'sessions'
    if not sessions_dir.exists():
        return []

    jsonl_files = sorted(sessions_dir.glob('*.jsonl'),
                         key=lambda f: f.stat().st_mtime, reverse=True)
    if not jsonl_files:
        return []

    # 读取最新的 session 文件
    target_file = jsonl_files[0]
    try:
        lines = target_file.read_text(errors='ignore').splitlines()
    except Exception:
        return []

    # 找到最后一条 user 消息的行号
    last_user_idx = -1
    for i, ln in enumerate(lines):
        try:
            item = json.loads(ln)
        except Exception:
            continue
        msg = item.get('message') or {}
        if msg.get('role') == 'user':
            last_user_idx = i

    if last_user_idx < 0:
        return []

    # 从最后一条 user 消息开始，解析到文件末尾
    entries = []
    for ln in lines[last_user_idx:]:
        try:
            item = json.loads(ln)
        except Exception:
            continue
        entry = _parse_activity_entry(item)
        if entry:
            entries.append(entry)

    return entries[-limit:]


def _compute_phase_durations(flow_log):
    """从 flow_log 计算每个阶段的停留时长。"""
    if not flow_log or len(flow_log) < 1:
        return []
    phases = []
    for i, fl in enumerate(flow_log):
        start_at = fl.get('at', '')
        to_dept = fl.get('to', '')
        remark = fl.get('remark', '')
        # 下一阶段的起始时间就是本阶段的结束时间
        if i + 1 < len(flow_log):
            end_at = flow_log[i + 1].get('at', '')
            ongoing = False
        else:
            end_at = now_iso()
            ongoing = True
        # 计算时长
        dur_sec = 0
        try:
            from_dt = datetime.datetime.fromisoformat(start_at.replace('Z', '+00:00'))
            to_dt = datetime.datetime.fromisoformat(end_at.replace('Z', '+00:00'))
            dur_sec = max(0, int((to_dt - from_dt).total_seconds()))
        except Exception:
            pass
        # 人类可读时长
        if dur_sec < 60:
            dur_text = f'{dur_sec}秒'
        elif dur_sec < 3600:
            dur_text = f'{dur_sec // 60}分{dur_sec % 60}秒'
        elif dur_sec < 86400:
            h, rem = divmod(dur_sec, 3600)
            dur_text = f'{h}小时{rem // 60}分'
        else:
            d, rem = divmod(dur_sec, 86400)
            dur_text = f'{d}天{rem // 3600}小时'
        phases.append({
            'phase': to_dept,
            'from': start_at,
            'to': end_at,
            'durationSec': dur_sec,
            'durationText': dur_text,
            'ongoing': ongoing,
            'remark': remark,
        })
    return phases


def _compute_todos_summary(todos):
    """计算 todos 完成率汇总。"""
    if not todos:
        return None
    total = len(todos)
    completed = sum(1 for t in todos if t.get('status') == 'completed')
    in_progress = sum(1 for t in todos if t.get('status') == 'in-progress')
    not_started = total - completed - in_progress
    percent = round(completed / total * 100) if total else 0
    return {
        'total': total,
        'completed': completed,
        'inProgress': in_progress,
        'notStarted': not_started,
        'percent': percent,
    }


def _compute_todos_diff(prev_todos, curr_todos):
    """计算两个 todos 快照之间的差异。"""
    prev_map = {str(t.get('id', '')): t for t in (prev_todos or [])}
    curr_map = {str(t.get('id', '')): t for t in (curr_todos or [])}
    changed, added, removed = [], [], []
    for tid, ct in curr_map.items():
        if tid in prev_map:
            pt = prev_map[tid]
            if pt.get('status') != ct.get('status'):
                changed.append({
                    'id': tid, 'title': ct.get('title', ''),
                    'from': pt.get('status', ''), 'to': ct.get('status', ''),
                })
        else:
            added.append({'id': tid, 'title': ct.get('title', '')})
    for tid, pt in prev_map.items():
        if tid not in curr_map:
            removed.append({'id': tid, 'title': pt.get('title', '')})
    if not changed and not added and not removed:
        return None
    return {'changed': changed, 'added': added, 'removed': removed}


def get_task_activity(task_id):
    """获取任务的实时进展数据。
    数据来源：
    1. 任务自身的 now / todos / flow_log 字段（由 Agent 通过 progress 命令主动上报）
    2. Agent session JSONL 中的对话日志（thinking / tool_result / user，用于展示思考过程）

    增强字段:
    - taskMeta: 任务元信息 (title/state/org/output/block/priority/reviewRound/archived)
    - phaseDurations: 各阶段停留时长
    - todosSummary: todos 完成率汇总
    - resourceSummary: Agent 资源消耗汇总 (tokens/cost/elapsed)
    - activity 条目中 progress/todos 保留 state/org 快照
    - activity 中 todos 条目含 diff 字段
    """
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}

    state = task.get('state', '')
    org = task.get('org', '')
    now_text = task.get('now', '')
    todos = task.get('todos', [])
    updated_at = task.get('updatedAt', '')

    # ── 任务元信息 ──
    task_meta = {
        'title': task.get('title', ''),
        'state': state,
        'org': org,
        'output': task.get('output', ''),
        'block': task.get('block', ''),
        'priority': task.get('priority', 'normal'),
        'reviewRound': task.get('review_round', 0),
        'archived': task.get('archived', False),
    }

    # 当前负责 Agent（兼容旧逻辑）
    agent_id = _STATE_AGENT_MAP.get(state)
    if agent_id is None and state in ('Doing', 'Next'):
        agent_id = _ORG_AGENT_MAP.get(org)

    # ── 构建活动条目列表（flow_log + progress_log）──
    activity = []
    flow_log = task.get('flow_log', [])

    # 1. flow_log 转为活动条目
    for fl in flow_log:
        activity.append({
            'at': fl.get('at', ''),
            'kind': 'flow',
            'from': fl.get('from', ''),
            'to': fl.get('to', ''),
            'remark': fl.get('remark', ''),
            'reasonCode': fl.get('reasonCode', ''),
        })

    diagnostic_log = task.get('diagnostic_log', [])
    for dg in diagnostic_log:
        activity.append({
            'at': dg.get('at', ''),
            'kind': 'diagnostic',
            'action': dg.get('action', ''),
            'reasonCode': dg.get('reasonCode', ''),
            'text': dg.get('details', ''),
            'eventType': dg.get('eventType', ''),
        })

    progress_log = task.get('progress_log', [])
    related_agents = set()

    # 资源消耗累加
    total_tokens = 0
    total_cost = 0.0
    total_elapsed = 0
    has_resource_data = False

    # 用于 todos diff 计算
    prev_todos_snapshot = None

    if progress_log:
        # 2. 多 Agent 实时进展日志（每条 progress 都保留自己的 todo 快照）
        for pl in progress_log:
            p_at = pl.get('at', '')
            p_agent = pl.get('agent', '')
            p_text = pl.get('text', '')
            p_todos = pl.get('todos', [])
            p_state = pl.get('state', '')
            p_org = pl.get('org', '')
            if p_agent:
                related_agents.add(p_agent)
            # 累加资源消耗
            if pl.get('tokens'):
                total_tokens += pl['tokens']
                has_resource_data = True
            if pl.get('cost'):
                total_cost += pl['cost']
                has_resource_data = True
            if pl.get('elapsed'):
                total_elapsed += pl['elapsed']
                has_resource_data = True
            if p_text:
                entry = {
                    'at': p_at,
                    'kind': 'progress',
                    'text': p_text,
                    'agent': p_agent,
                    'agentLabel': pl.get('agentLabel', ''),
                    'state': p_state,
                    'org': p_org,
                }
                # 单条资源数据
                if pl.get('tokens'):
                    entry['tokens'] = pl['tokens']
                if pl.get('cost'):
                    entry['cost'] = pl['cost']
                if pl.get('elapsed'):
                    entry['elapsed'] = pl['elapsed']
                activity.append(entry)
            if p_todos:
                todos_entry = {
                    'at': p_at,
                    'kind': 'todos',
                    'items': p_todos,
                    'agent': p_agent,
                    'agentLabel': pl.get('agentLabel', ''),
                    'state': p_state,
                    'org': p_org,
                }
                # 计算 diff
                diff = _compute_todos_diff(prev_todos_snapshot, p_todos)
                if diff:
                    todos_entry['diff'] = diff
                activity.append(todos_entry)
                prev_todos_snapshot = p_todos

        # 仅当无法通过状态确定 Agent 时，才回退到最后一次上报的 Agent
        if not agent_id:
            last_pl = progress_log[-1]
            if last_pl.get('agent'):
                agent_id = last_pl.get('agent')
    else:
        # 兼容旧数据：仅使用 now/todos
        if now_text:
            activity.append({
                'at': updated_at,
                'kind': 'progress',
                'text': now_text,
                'agent': agent_id or '',
                'state': state,
                'org': org,
            })
        if todos:
            activity.append({
                'at': updated_at,
                'kind': 'todos',
                'items': todos,
                'agent': agent_id or '',
                'state': state,
                'org': org,
            })

    # 按时间排序，保证流转/进展穿插正确
    activity.sort(key=lambda x: x.get('at', ''))

    if agent_id:
        related_agents.add(agent_id)

    # ── 融合 Agent Session 活动（thinking / tool_result / user）──
    # 从 session JSONL 中提取 Agent 的思考过程和工具调用记录
    try:
        session_entries = []
        # 活跃任务：尝试按 task_id 精确匹配
        if state not in ('Done', 'Cancelled'):
            if agent_id:
                entries = get_agent_activity(agent_id, limit=30, task_id=task_id)
                session_entries.extend(entries)
            # 也从其他相关 Agent 获取
            for ra in related_agents:
                if ra != agent_id:
                    entries = get_agent_activity(ra, limit=20, task_id=task_id)
                    session_entries.extend(entries)
        else:
            # 已完成任务：基于关键词匹配
            title = task.get('title', '')
            keywords = _extract_keywords(title)
            if keywords:
                agents_to_scan = list(related_agents) if related_agents else ([agent_id] if agent_id else [])
                for ra in agents_to_scan[:5]:
                    entries = get_agent_activity_by_keywords(ra, keywords, limit=15)
                    session_entries.extend(entries)
        # 去重（通过 at+kind 去重避免重复）
        existing_keys = {(a.get('at', ''), a.get('kind', '')) for a in activity}
        for se in session_entries:
            key = (se.get('at', ''), se.get('kind', ''))
            if key not in existing_keys:
                activity.append(se)
                existing_keys.add(key)
        # 重新排序
        activity.sort(key=lambda x: x.get('at', ''))
    except Exception as e:
        log.warning(f'Session JSONL 融合失败 (task={task_id}): {e}')

    # ── 阶段耗时统计 ──
    phase_durations = _compute_phase_durations(flow_log)

    # ── Todos 汇总 ──
    todos_summary = _compute_todos_summary(todos)

    # ── 总耗时（首条 flow_log 到最后一条/当前） ──
    total_duration = None
    if flow_log:
        try:
            first_at = datetime.datetime.fromisoformat(flow_log[0].get('at', '').replace('Z', '+00:00'))
            if state in ('Done', 'Cancelled') and len(flow_log) >= 2:
                last_at = datetime.datetime.fromisoformat(flow_log[-1].get('at', '').replace('Z', '+00:00'))
            else:
                last_at = datetime.datetime.now(datetime.timezone.utc)
            dur = max(0, int((last_at - first_at).total_seconds()))
            if dur < 60:
                total_duration = f'{dur}秒'
            elif dur < 3600:
                total_duration = f'{dur // 60}分{dur % 60}秒'
            elif dur < 86400:
                h, rem = divmod(dur, 3600)
                total_duration = f'{h}小时{rem // 60}分'
            else:
                d, rem = divmod(dur, 86400)
                total_duration = f'{d}天{rem // 3600}小时'
        except Exception:
            pass

    result = {
        'ok': True,
        'taskId': task_id,
        'taskMeta': task_meta,
        'agentId': agent_id,
        'agentLabel': _STATE_LABELS.get(state, state),
        'lastActive': updated_at[:19].replace('T', ' ') if updated_at else None,
        'activity': activity,
        'activitySource': 'progress+session',
        'relatedAgents': sorted(list(related_agents)),
        'phaseDurations': phase_durations,
        'totalDuration': total_duration,
    }
    if todos_summary:
        result['todosSummary'] = todos_summary
    if has_resource_data:
        result['resourceSummary'] = {
            'totalTokens': total_tokens,
            'totalCost': round(total_cost, 4),
            'totalElapsedSec': total_elapsed,
        }
    return result


# 状态推进顺序（手动推进用）
_STATE_FLOW = {
    'Pending':  ('Taizi', '皇上', '太子', '待处理旨意转交太子分拣'),
    'Taizi':    ('Zhongshu', '太子', '中书省', '太子分拣完毕，转中书省起草'),
    'Zhongshu': ('Menxia', '中书省', '门下省', '中书省方案提交门下省审议'),
    'Menxia':   ('Assigned', '门下省', '尚书省', '门下省准奏，转尚书省派发'),
    'Assigned': ('Doing', '尚书省', '六部', '尚书省开始派发执行'),
    'Next':     ('Doing', '尚书省', '六部', '待执行任务开始执行'),
    'Doing':    ('Review', '六部', '尚书省', '各部完成，进入汇总'),
    'Review':   ('Done', '尚书省', '太子', '全流程完成，回奏太子转报皇上'),
}
_STATE_LABELS = {
    'Pending': '待处理', 'Taizi': '太子', 'Zhongshu': '中书省', 'Menxia': '门下省',
    'Assigned': '尚书省', 'Next': '待执行', 'Doing': '执行中', 'Review': '审查', 'Done': '完成',
}
_AUTO_ADVANCE_SAFE_STATES = {'Taizi', 'Zhongshu', 'Assigned', 'Next'}
_RISK_DECISION_STATES = {'Menxia', 'Review'}


def _derive_org_for_state(task, state, current_org=''):
    """根据目标状态推导 org，避免 state/org 不一致导致后续派发失败。"""
    fixed = {
        'Taizi': '太子',
        'Zhongshu': '中书省',
        'Menxia': '门下省',
        'Assigned': '尚书省',
        'Review': '尚书省',
    }
    if state in fixed:
        return fixed[state]

    if state in ('Doing', 'Next'):
        target_dept = (task.get('targetDept') or '').strip()
        if target_dept in _ORG_AGENT_MAP:
            return target_dept
        if current_org in _ORG_AGENT_MAP and current_org not in ('中书省', '门下省', '尚书省', '太子'):
            return current_org
        return current_org or target_dept or '尚书省'

    return current_org or task.get('org', '')


def _extract_kanban_commands_from_text(text):
    """从 agent 文本中提取 kanban_update.py 命令参数（仅白名单子命令）。"""
    if not text:
        return []
    allowed = {'progress', 'flow', 'state', 'todo', 'done', 'block'}
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or 'kanban_update.py' not in line:
            continue
        line = line.strip('`').lstrip('-').strip()
        if not line.startswith('python3'):
            continue
        try:
            parts = shlex.split(line)
        except Exception:
            continue
        script_idx = -1
        for i, tok in enumerate(parts):
            if tok.endswith('kanban_update.py'):
                script_idx = i
                break
        if script_idx < 0:
            continue
        args = parts[script_idx + 1:]
        if not args:
            continue
        subcmd = args[0]
        if subcmd not in allowed:
            continue
        out.append(args)
    return out


def _bridge_apply_kanban_commands(task_id, text):
    """在本机代执行 agent 输出中的 kanban_update.py 命令，解决 agent 沙箱无法写看板。"""
    commands = _extract_kanban_commands_from_text(text)
    if not commands:
        return {'applied': 0, 'attempted': 0, 'errors': [], 'deptDispatches': []}

    script_path = str(SCRIPTS / 'kanban_update.py')
    applied = 0
    errors = []
    attempted = 0
    dept_dispatches = []
    dept_seen = set()

    for args in commands[:6]:
        subcmd = args[0]
        # 所有允许子命令都要求第二个参数是 task_id，避免误执行到其它任务
        if len(args) < 2 or args[1] != task_id:
            continue
        if subcmd == 'flow' and len(args) >= 4:
            from_dept = (args[2] or '').strip()
            to_dept = (args[3] or '').strip()
            remark = (args[4] or '').strip() if len(args) >= 5 else ''
            if from_dept == '尚书省' and to_dept in _EXECUTION_DEPTS:
                standby = ('待命' in remark) or ('排障' in remark and '派发' not in remark)
                if not standby and to_dept not in dept_seen:
                    dept_seen.add(to_dept)
                    dept_dispatches.append(to_dept)
        attempted += 1
        cmd = ['python3', script_path] + args
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if result.returncode == 0:
                applied += 1
            else:
                stderr = (result.stderr or result.stdout or '').strip()
                errors.append(f'{subcmd}: {stderr[:160]}')
        except Exception as e:
            errors.append(f'{subcmd}: {str(e)[:160]}')

    return {
        'applied': applied,
        'attempted': attempted,
        'errors': errors,
        'deptDispatches': dept_dispatches,
    }


def _pick_execution_dept(task, dept_dispatches):
    target_dept = (task.get('targetDept') or '').strip()
    if target_dept in _EXECUTION_DEPTS:
        return target_dept
    for dept in dept_dispatches or []:
        if dept in _EXECUTION_DEPTS:
            return dept
    return ''


def _auto_handoff_to_execution(task_id, preferred_dept='', trigger='shangshu-auto-handoff'):
    """尚书省派发后自动切换到六部执行态，并自动派发对应执行 Agent。"""
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'reason': 'task_not_found'}

    cur_state = task.get('state', '')
    if cur_state not in ('Assigned', 'Next'):
        return {'ok': False, 'reason': f'state={cur_state}'}

    dept = (preferred_dept or '').strip()
    if dept not in _EXECUTION_DEPTS:
        return {'ok': False, 'reason': f'dept={dept}'}

    _ensure_scheduler(task)
    _scheduler_snapshot(task, f'{trigger}-before-{cur_state}')
    run_id = _new_run_id()
    _acquire_lease(task, stage=cur_state, role='auto-handoff', owner_run_id=run_id, ttl_sec=180, force_takeover=True)
    version = task.get('_scheduler', {}).get('stateVersion')
    task['targetDept'] = dept
    commit = commit_state_change(
        task,
        action='advance',
        reason_code='shangshu_auto_handoff',
        owner_run_id=run_id,
        expected_version=version,
        to_state='Doing',
        to_org=dept,
        now_text=f'尚书省已派发，{dept}执行中',
        block_text='无',
        flow_remark=f'尚书省派发完成，自动切换到{dept}执行',
        flow_to=dept,
    )
    if not commit.get('committed'):
        save_tasks(tasks)
        return {'ok': False, 'reason': f'commit_blocked:{commit.get("blockedBy")}'}
    _scheduler_mark_progress(task, f'自动切换执行：{cur_state} -> Doing ({dept})', reason_code='shangshu_auto_handoff')
    save_tasks(tasks)

    dispatch_for_state(task_id, task, 'Doing', trigger=trigger, owner_run_id=run_id)
    return {'ok': True, 'dept': dept}


def dispatch_for_state(task_id, task, new_state, trigger='state-transition', owner_run_id=''):
    """推进/审批后自动派发对应 Agent（后台异步，不阻塞响应）。"""
    tasks = load_tasks()
    persisted = next((t for t in tasks if t.get('id') == task_id), None)
    if persisted:
        task = persisted

    agent_id = _STATE_AGENT_MAP.get(new_state)
    if agent_id is None and new_state in ('Doing', 'Next'):
        org = task.get('org', '')
        agent_id = _ORG_AGENT_MAP.get(org)
    if not agent_id:
        log.info(f'ℹ️ {task_id} 新状态 {new_state} 无对应 Agent，跳过自动派发')
        return

    sched = _ensure_scheduler(task)
    action_ok = _action_allowed(task, 'dispatch')
    if not action_ok.get('ok'):
        _append_diagnostic(
            task,
            event_type='control_blocked',
            action='dispatch',
            reason_code='dispatch_blocked',
            details=str(action_ok),
            dedupe_key=f'{task_id}:dispatch:blocked:{action_ok.get("blockedBy")}',
        )
        if persisted:
            task['updatedAt'] = now_iso()
            save_tasks(tasks)
        return

    run_id = owner_run_id or _new_run_id()
    lease_result = _acquire_lease(
        task,
        stage=new_state,
        role=agent_id,
        owner_run_id=run_id,
        ttl_sec=300,
        force_takeover=False,
    )
    if not lease_result.get('ok'):
        _append_diagnostic(
            task,
            event_type='control_blocked',
            action='dispatch',
            reason_code='lease_busy',
            details=str(lease_result),
            dedupe_key=f'{task_id}:dispatch:lease_busy',
        )
        if persisted:
            task['updatedAt'] = now_iso()
            save_tasks(tasks)
        return

    commit = commit_state_change(
        task,
        action='dispatch',
        reason_code='dispatch_queued',
        owner_run_id=run_id,
        expected_version=sched.get('stateVersion'),
        flow_remark=f'🚀 已入队派发：{new_state} → {agent_id}（{trigger}）',
        flow_to=_STATE_LABELS.get(new_state, new_state),
    )
    if not commit.get('committed'):
        _append_diagnostic(
            task,
            event_type='control_blocked',
            action='dispatch',
            reason_code='commit_blocked',
            details=str(commit),
            dedupe_key=f'{task_id}:dispatch:commit:{commit.get("blockedBy")}',
        )
        if persisted:
            task['updatedAt'] = now_iso()
            save_tasks(tasks)
        return

    sched.update({
        'lastDispatchAt': now_iso(),
        'lastDispatchStatus': 'queued',
        'lastDispatchAgent': agent_id,
        'lastDispatchTrigger': trigger,
        'dispatchRunId': run_id,
        'dispatchAttempts': int(sched.get('dispatchAttempts') or 0) + 1,
    })
    _set_cooldown(task, 'noEscalateUntil', _COOLDOWN_SECONDS['post_dispatch_escalate'])
    _set_cooldown(task, 'noDispatchUntil', _COOLDOWN_SECONDS['post_dispatch_dispatch'])
    task['updatedAt'] = now_iso()
    if persisted:
        save_tasks(tasks)

    title = task.get('title', '(无标题)')
    target_dept = task.get('targetDept', '')
    kanban_cmd = 'python3 "scripts/kanban_update.py"'
    kanban_cmd_fallback = f'python3 "{OCLAW_HOME / "workspace-main" / "scripts" / "kanban_update.py"}"'

    # 根据 agent_id 构造针对性消息
    _msgs = {
        'taizi': (
            f'📜 皇上旨意需要你处理\n'
            f'任务ID: {task_id}\n'
            f'旨意: {title}\n'
            f'⚠️ 看板已有此任务，请勿重复创建。优先用：{kanban_cmd}（若失败再用：{kanban_cmd_fallback}）。\n'
            f'请立即转交中书省起草执行方案。'
        ),
        'zhongshu': (
            f'📜 旨意已到中书省，请起草方案\n'
            f'任务ID: {task_id}\n'
            f'旨意: {title}\n'
            f'⚠️ 看板已有此任务记录，请勿重复创建。优先用：{kanban_cmd}（若失败再用：{kanban_cmd_fallback}）。\n'
            f'请立即起草执行方案，走完完整三省流程（中书起草→门下审议→尚书派发→六部执行）。'
        ),
        'menxia': (
            f'📋 中书省方案提交审议\n'
            f'任务ID: {task_id}\n'
            f'旨意: {title}\n'
            f'⚠️ 看板已有此任务，请勿重复创建。优先用：{kanban_cmd}（若失败再用：{kanban_cmd_fallback}）。\n'
            f'请审议中书省方案，给出准奏或封驳意见。'
        ),
        'shangshu': (
            f'📮 门下省已准奏，请派发执行\n'
            f'任务ID: {task_id}\n'
            f'旨意: {title}\n'
            f'{"建议派发部门: " + target_dept if target_dept else ""}\n'
            f'⚠️ 看板已有此任务，请勿重复创建。优先用：{kanban_cmd}（若失败再用：{kanban_cmd_fallback}）。\n'
            f'请分析方案并派发给六部执行。'
        ),
        'gongbu': (
            f'🔧 六部执行任务\n'
            f'任务ID: {task_id}\n'
            f'旨意: {title}\n'
            f'⚠️ 看板已有此任务，请勿重复创建。优先用：{kanban_cmd}（若失败再用：{kanban_cmd_fallback}）。\n'
            f'请开始执行并持续回写 progress/todo，完成后回传尚书省。'
        ),
        'xingbu': (
            f'⚖️ 六部执行任务\n'
            f'任务ID: {task_id}\n'
            f'旨意: {title}\n'
            f'⚠️ 看板已有此任务，请勿重复创建。优先用：{kanban_cmd}（若失败再用：{kanban_cmd_fallback}）。\n'
            f'请开始执行并持续回写 progress/todo，完成后回传尚书省。'
        ),
        'libu': (
            f'📝 六部执行任务\n'
            f'任务ID: {task_id}\n'
            f'旨意: {title}\n'
            f'⚠️ 看板已有此任务，请勿重复创建。优先用：{kanban_cmd}（若失败再用：{kanban_cmd_fallback}）。\n'
            f'请开始执行并持续回写 progress/todo，完成后回传尚书省。'
        ),
        'hubu': (
            f'💰 六部执行任务\n'
            f'任务ID: {task_id}\n'
            f'旨意: {title}\n'
            f'⚠️ 看板已有此任务，请勿重复创建。优先用：{kanban_cmd}（若失败再用：{kanban_cmd_fallback}）。\n'
            f'请开始执行并持续回写 progress/todo，完成后回传尚书省。'
        ),
        'bingbu': (
            f'⚔️ 六部执行任务\n'
            f'任务ID: {task_id}\n'
            f'旨意: {title}\n'
            f'⚠️ 看板已有此任务，请勿重复创建。优先用：{kanban_cmd}（若失败再用：{kanban_cmd_fallback}）。\n'
            f'请开始执行并持续回写 progress/todo，完成后回传尚书省。'
        ),
        'libu_hr': (
            f'👔 六部执行任务\n'
            f'任务ID: {task_id}\n'
            f'旨意: {title}\n'
            f'⚠️ 看板已有此任务，请勿重复创建。优先用：{kanban_cmd}（若失败再用：{kanban_cmd_fallback}）。\n'
            f'请开始执行并持续回写 progress/todo，完成后回传尚书省。'
        ),
    }
    msg = _msgs.get(agent_id, (
        f'📌 请处理任务\n'
        f'任务ID: {task_id}\n'
        f'旨意: {title}\n'
        f'⚠️ 看板已有此任务，请勿重复创建。优先用：{kanban_cmd}（若失败再用：{kanban_cmd_fallback}）。'
    ))

    def _do_dispatch():
        try:
            if not _check_gateway_alive():
                log.warning(f'⚠️ {task_id} 自动派发跳过: Gateway 未启动')
                def _mark_gateway_offline(t, s):
                    s.update({
                        'lastDispatchAt': now_iso(),
                        'lastDispatchStatus': 'gateway-offline',
                        'lastDispatchAgent': agent_id,
                        'lastDispatchTrigger': trigger,
                    })
                    s['controlState'] = 'RetryableFailure'
                    _release_lease(t, run_id)
                    _append_diagnostic(
                        t,
                        event_type='dispatch_failed',
                        action='dispatch',
                        reason_code='gateway_offline',
                        details=f'task={task_id}, agent={agent_id}',
                        dedupe_key=f'{task_id}:dispatch:gateway_offline',
                    )
                _update_task_scheduler(task_id, _mark_gateway_offline)
                return
            # 默认走本地 direct 调用，避免硬编码 feishu 导致无渠道环境派发失败。
            # 如需强制渠道投递，可设置 EDICT_DISPATCH_CHANNEL=feishu|telegram|signal...
            cmd = ['openclaw', 'agent', '--agent', agent_id, '-m', msg, '--timeout', '300']
            dispatch_channel = (os.environ.get('EDICT_DISPATCH_CHANNEL') or '').strip()
            if dispatch_channel:
                cmd.extend(['--deliver', '--channel', dispatch_channel])
            max_retries = 2
            err = ''
            for attempt in range(1, max_retries + 1):
                log.info(f'🔄 自动派发 {task_id} → {agent_id} (第{attempt}次)...')
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=310)
                if result.returncode == 0:
                    log.info(f'✅ {task_id} 自动派发成功 → {agent_id}')
                    dispatch_text = ((result.stdout or '') + '\n' + (result.stderr or '')).strip()
                    bridge = _bridge_apply_kanban_commands(task_id, dispatch_text)
                    handoff = {'ok': False}
                    handoff_dept = ''
                    if agent_id == 'shangshu' and new_state in ('Assigned', 'Next'):
                        handoff_dept = _pick_execution_dept(task, bridge.get('deptDispatches', []))
                        if handoff_dept:
                            handoff = _auto_handoff_to_execution(
                                task_id,
                                preferred_dept=handoff_dept,
                                trigger='shangshu-auto-handoff'
                            )
                            if handoff.get('ok'):
                                log.info(f'🚦 {task_id} 尚书省派发完成，自动切换到 {handoff_dept} 执行')
                    if bridge.get('applied', 0) > 0:
                        log.info(
                            f'🧩 {task_id} 桥接执行看板命令: '
                            f'{bridge.get("applied", 0)}/{bridge.get("attempted", 0)}'
                        )
                    elif bridge.get('attempted', 0) > 0 and bridge.get('errors'):
                        log.warning(f'⚠️ {task_id} 桥接执行失败: {" | ".join(bridge.get("errors", [])[:2])}')
                    def _mark_dispatch_success(t, s):
                        s.update({
                            'lastDispatchAt': now_iso(),
                            'lastDispatchStatus': 'success',
                            'lastDispatchAgent': agent_id,
                            'lastDispatchTrigger': trigger,
                            'lastDispatchError': '',
                        })
                        wb = s.setdefault('writeback', {})
                        wb.setdefault('retryCount', 0)
                        wb.setdefault('maxRetry', 2)
                        wb['lastDispatchOutput'] = dispatch_text[:12000]
                        if not wb.get('firstOutputAt'):
                            wb['firstOutputAt'] = now_iso()
                        if bridge.get('attempted', 0) <= 0:
                            wb['status'] = 'ExecutionOutputReady'
                            wb['lastError'] = 'no_bridge_command_detected'
                        elif bridge.get('applied', 0) >= bridge.get('attempted', 0):
                            wb['status'] = 'idle'
                            wb['lastCommittedAt'] = now_iso()
                            wb['lastError'] = ''
                            wb['retryCount'] = 0
                        else:
                            wb['status'] = 'WritebackPending'
                            wb['retryCount'] = int(wb.get('retryCount') or 0) + 1
                            wb['lastError'] = '; '.join((bridge.get('errors') or [])[:2]) or 'writeback_failed'
                            _append_diagnostic(
                                t,
                                event_type='writeback_pending',
                                action='writeback_retry',
                                reason_code='writeback_failed',
                                details=wb['lastError'],
                                dedupe_key=f'{task_id}:writeback_pending',
                            )
                        _sync_control_state(t)
                        _set_cooldown(t, 'noEscalateUntil', _COOLDOWN_SECONDS['post_dispatch_escalate'])
                        _set_cooldown(t, 'noDispatchUntil', _COOLDOWN_SECONDS['post_dispatch_dispatch'])
                        _release_lease(t, run_id)
                        _scheduler_add_flow(
                            t,
                            (
                                f'派发成功：{agent_id}（{trigger}）'
                                + (
                                    f'；桥接执行 {bridge.get("applied", 0)}/{bridge.get("attempted", 0)}'
                                    if bridge.get('attempted', 0) > 0 else ''
                                )
                                + (
                                    f'；自动切换执行:{handoff_dept}'
                                    if handoff.get('ok') and handoff_dept else ''
                                )
                            ),
                            to=t.get('org', ''),
                            reason_code='dispatch_success',
                        )
                    _update_task_scheduler(task_id, _mark_dispatch_success)
                    return
                err = result.stderr[:200] if result.stderr else result.stdout[:200]
                log.warning(f'⚠️ {task_id} 自动派发失败(第{attempt}次): {err}')
                if attempt < max_retries:
                    import time
                    time.sleep(5)
            log.error(f'❌ {task_id} 自动派发最终失败 → {agent_id}')
            def _mark_dispatch_failed(t, s):
                s.update({
                    'lastDispatchAt': now_iso(),
                    'lastDispatchStatus': 'failed',
                    'lastDispatchAgent': agent_id,
                    'lastDispatchTrigger': trigger,
                    'lastDispatchError': err,
                    'controlState': 'RetryableFailure',
                })
                _release_lease(t, run_id)
                _append_diagnostic(
                    t,
                    event_type='dispatch_failed',
                    action='dispatch',
                    reason_code='dispatch_failed',
                    details=err,
                    dedupe_key=f'{task_id}:dispatch_failed',
                )
                _scheduler_add_flow(t, f'派发失败：{agent_id}（{trigger}）', to=t.get('org', ''), reason_code='dispatch_failed')
            _update_task_scheduler(task_id, _mark_dispatch_failed)
        except subprocess.TimeoutExpired:
            log.error(f'❌ {task_id} 自动派发超时 → {agent_id}')
            def _mark_dispatch_timeout(t, s):
                s.update({
                    'lastDispatchAt': now_iso(),
                    'lastDispatchStatus': 'timeout',
                    'lastDispatchAgent': agent_id,
                    'lastDispatchTrigger': trigger,
                    'lastDispatchError': 'timeout',
                    'controlState': 'RetryableFailure',
                })
                _release_lease(t, run_id)
                _append_diagnostic(
                    t,
                    event_type='dispatch_failed',
                    action='dispatch',
                    reason_code='dispatch_timeout',
                    details=f'task={task_id}, agent={agent_id}',
                    dedupe_key=f'{task_id}:dispatch_timeout',
                )
                _scheduler_add_flow(t, f'派发超时：{agent_id}（{trigger}）', to=t.get('org', ''), reason_code='dispatch_timeout')
            _update_task_scheduler(task_id, _mark_dispatch_timeout)
        except Exception as e:
            log.warning(f'⚠️ {task_id} 自动派发异常: {e}')
            def _mark_dispatch_error(t, s):
                s.update({
                    'lastDispatchAt': now_iso(),
                    'lastDispatchStatus': 'error',
                    'lastDispatchAgent': agent_id,
                    'lastDispatchTrigger': trigger,
                    'lastDispatchError': str(e)[:200],
                    'controlState': 'RetryableFailure',
                })
                _release_lease(t, run_id)
                _append_diagnostic(
                    t,
                    event_type='dispatch_failed',
                    action='dispatch',
                    reason_code='dispatch_error',
                    details=str(e)[:200],
                    dedupe_key=f'{task_id}:dispatch_error',
                )
                _scheduler_add_flow(t, f'派发异常：{agent_id}（{trigger}）', to=t.get('org', ''), reason_code='dispatch_error')
            _update_task_scheduler(task_id, _mark_dispatch_error)

    threading.Thread(target=_do_dispatch, daemon=True).start()
    log.info(f'🚀 {task_id} 推进后自动派发 → {agent_id}')


def handle_advance_state(task_id, comment=''):
    """手动推进任务到下一阶段（解卡用），推进后自动派发对应 Agent。"""
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    cur = task.get('state', '')
    if cur not in _STATE_FLOW:
        return {'ok': False, 'error': f'任务 {task_id} 状态为 {cur}，无法推进'}
    _ensure_scheduler(task)
    _scheduler_snapshot(task, f'advance-before-{cur}')
    run_id = _new_run_id()
    _acquire_lease(task, stage=cur, role='manual-advance', owner_run_id=run_id, ttl_sec=180, force_takeover=True)
    version = task.get('_scheduler', {}).get('stateVersion')
    next_state, from_dept, to_dept, default_remark = _STATE_FLOW[cur]
    remark = comment or default_remark

    commit = commit_state_change(
        task,
        action='manual_decide',
        reason_code='manual_advance',
        owner_run_id=run_id,
        expected_version=version,
        to_state=next_state,
        to_org=_derive_org_for_state(task, next_state, task.get('org', '')),
        now_text=f'⬇️ 手动推进：{remark}',
        flow_from=from_dept,
        flow_to=to_dept,
        flow_remark=f'⬇️ 手动推进：{remark}',
        force=True,
    )
    if not commit.get('committed'):
        save_tasks(tasks)
        return {'ok': False, 'error': f'任务 {task_id} 推进失败: {commit.get("blockedBy")}'}
    _scheduler_mark_progress(task, f'手动推进 {cur} -> {next_state}', reason_code='manual_advance')
    save_tasks(tasks)

    # 🚀 推进后自动派发对应 Agent（Done 状态无需派发）
    if next_state != 'Done':
        dispatch_for_state(task_id, task, next_state, owner_run_id=run_id)

    from_label = _STATE_LABELS.get(cur, cur)
    to_label = _STATE_LABELS.get(next_state, next_state)
    dispatched = ' (已自动派发 Agent)' if next_state != 'Done' else ''
    return {'ok': True, 'message': f'{task_id} {from_label} → {to_label}{dispatched}'}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # 只记录 4xx/5xx 错误请求
        if args and len(args) >= 1:
            status = str(args[0]) if args else ''
            if status.startswith('4') or status.startswith('5'):
                log.warning(f'{self.client_address[0]} {fmt % args}')

    def handle_error(self):
        pass  # 静默处理连接错误，避免 BrokenPipe 崩溃

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass  # 客户端断开连接，忽略

    def do_OPTIONS(self):
        self.send_response(200)
        cors_headers(self)
        self.end_headers()

    def send_json(self, data, code=200):
        try:
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            cors_headers(self)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_file(self, path: pathlib.Path, mime='text/html; charset=utf-8'):
        if not path.exists():
            self.send_error(404)
            return
        try:
            body = path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            cors_headers(self)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _serve_static(self, rel_path):
        """从 dist/ 目录提供静态文件。"""
        safe = rel_path.replace('\\', '/').lstrip('/')
        if '..' in safe:
            self.send_error(403)
            return True
        fp = DIST / safe
        if fp.is_file():
            mime = _MIME_TYPES.get(fp.suffix.lower(), 'application/octet-stream')
            self.send_file(fp, mime)
            return True
        return False

    def do_GET(self):
        p = urlparse(self.path).path.rstrip('/')
        if p in ('', '/dashboard', '/dashboard.html'):
            self.send_file(DIST / 'index.html')
        elif p == '/healthz':
            checks = {'dataDir': DATA.is_dir(), 'tasksReadable': (DATA / 'tasks_source.json').exists()}
            checks['dataWritable'] = os.access(str(DATA), os.W_OK)
            all_ok = all(checks.values())
            self.send_json({'status': 'ok' if all_ok else 'degraded', 'ts': now_iso(), 'checks': checks})
        elif p == '/api/live-status':
            self.send_json(read_json(DATA / 'live_status.json'))
        elif p == '/api/agent-config':
            self.send_json(read_json(DATA / 'agent_config.json'))
        elif p == '/api/model-change-log':
            self.send_json(read_json(DATA / 'model_change_log.json', []))
        elif p == '/api/last-result':
            self.send_json(read_json(DATA / 'last_model_change_result.json', {}))
        elif p == '/api/officials-stats':
            self.send_json(read_json(DATA / 'officials_stats.json', {}))
        elif p == '/api/morning-brief':
            self.send_json(read_json(DATA / 'morning_brief.json', {}))
        elif p == '/api/morning-config':
            self.send_json(read_json(DATA / 'morning_brief_config.json', {
                'categories': [
                    {'name': '政治', 'enabled': True},
                    {'name': '军事', 'enabled': True},
                    {'name': '经济', 'enabled': True},
                    {'name': 'AI大模型', 'enabled': True},
                ],
                'keywords': [], 'custom_feeds': [], 'feishu_webhook': '',
            }))
        elif p.startswith('/api/morning-brief/'):
            date = p.split('/')[-1]
            # 标准化日期格式为 YYYYMMDD（兼容 YYYY-MM-DD 输入）
            date_clean = date.replace('-', '')
            if not date_clean.isdigit() or len(date_clean) != 8:
                self.send_json({'ok': False, 'error': f'日期格式无效: {date}，请使用 YYYYMMDD'}, 400)
                return
            self.send_json(read_json(DATA / f'morning_brief_{date_clean}.json', {}))
        elif p == '/api/remote-skills-list':
            self.send_json(get_remote_skills_list())
        elif p.startswith('/api/skill-content/'):
            # /api/skill-content/{agentId}/{skillName}
            parts = p.replace('/api/skill-content/', '').split('/', 1)
            if len(parts) == 2:
                self.send_json(read_skill_content(parts[0], parts[1]))
            else:
                self.send_json({'ok': False, 'error': 'Usage: /api/skill-content/{agentId}/{skillName}'}, 400)
        elif p.startswith('/api/task-activity/'):
            task_id = p.replace('/api/task-activity/', '')
            if not task_id:
                self.send_json({'ok': False, 'error': 'task_id required'}, 400)
            else:
                self.send_json(get_task_activity(task_id))
        elif p.startswith('/api/scheduler-state/'):
            task_id = p.replace('/api/scheduler-state/', '')
            if not task_id:
                self.send_json({'ok': False, 'error': 'task_id required'}, 400)
            else:
                self.send_json(get_scheduler_state(task_id))
        elif p == '/api/scheduler-metrics':
            self.send_json(get_scheduler_metrics())
        elif p.startswith('/api/scheduler-metrics/'):
            task_id = p.replace('/api/scheduler-metrics/', '')
            if not task_id:
                self.send_json({'ok': False, 'error': 'task_id required'}, 400)
            else:
                self.send_json(get_scheduler_metrics(task_id))
        elif p == '/api/agents-status':
            self.send_json(get_agents_status())
        elif p.startswith('/api/agent-activity/'):
            agent_id = p.replace('/api/agent-activity/', '')
            if not agent_id or not _SAFE_NAME_RE.match(agent_id):
                self.send_json({'ok': False, 'error': 'invalid agent_id'}, 400)
            else:
                self.send_json({'ok': True, 'agentId': agent_id, 'activity': get_agent_activity(agent_id)})
        elif self._serve_static(p):
            pass  # 已由 _serve_static 处理 (JS/CSS/图片等)
        else:
            # SPA fallback：非 /api/ 路径返回 index.html
            if not p.startswith('/api/'):
                idx = DIST / 'index.html'
                if idx.exists():
                    self.send_file(idx)
                    return
            self.send_error(404)

    def do_POST(self):
        p = urlparse(self.path).path.rstrip('/')
        length = int(self.headers.get('Content-Length', 0))
        if length > MAX_REQUEST_BODY:
            self.send_json({'ok': False, 'error': f'Request body too large (max {MAX_REQUEST_BODY} bytes)'}, 413)
            return
        raw = self.rfile.read(length) if length else b''
        try:
            body = json.loads(raw) if raw else {}
        except Exception:
            self.send_json({'ok': False, 'error': 'invalid JSON'}, 400)
            return

        if p == '/api/morning-config':
            # 字段校验
            if not isinstance(body, dict):
                self.send_json({'ok': False, 'error': '请求体必须是 JSON 对象'}, 400)
                return
            allowed_keys = {'categories', 'keywords', 'custom_feeds', 'feishu_webhook'}
            unknown = set(body.keys()) - allowed_keys
            if unknown:
                self.send_json({'ok': False, 'error': f'未知字段: {", ".join(unknown)}'}, 400)
                return
            if 'categories' in body and not isinstance(body['categories'], list):
                self.send_json({'ok': False, 'error': 'categories 必须是数组'}, 400)
                return
            if 'keywords' in body and not isinstance(body['keywords'], list):
                self.send_json({'ok': False, 'error': 'keywords 必须是数组'}, 400)
                return
            # 飞书 Webhook 校验
            webhook = body.get('feishu_webhook', '').strip()
            if webhook and not validate_url(webhook, allowed_schemes=('https',), allowed_domains=('open.feishu.cn', 'open.larksuite.com')):
                self.send_json({'ok': False, 'error': '飞书 Webhook URL 无效，仅支持 https://open.feishu.cn 或 open.larksuite.com 域名'}, 400)
                return
            cfg_path = DATA / 'morning_brief_config.json'
            cfg_path.write_text(json.dumps(body, ensure_ascii=False, indent=2))
            self.send_json({'ok': True, 'message': '订阅配置已保存'})
            return

        if p == '/api/scheduler-scan':
            threshold_sec = body.get('thresholdSec', 180)
            try:
                result = handle_scheduler_scan(threshold_sec)
                self.send_json(result)
            except Exception as e:
                self.send_json({'ok': False, 'error': f'scheduler scan failed: {e}'}, 500)
            return

        if p == '/api/repair-flow-order':
            try:
                self.send_json(handle_repair_flow_order())
            except Exception as e:
                self.send_json({'ok': False, 'error': f'repair flow order failed: {e}'}, 500)
            return

        if p == '/api/scheduler-action':
            task_id = body.get('taskId', '').strip()
            action = body.get('action', '').strip()
            reason = body.get('reason', '').strip()
            expected_version = body.get('expectedVersion')
            owner_run_id = body.get('ownerRunId', '').strip()
            recovery_target = body.get('recoveryTarget', '').strip()
            if not task_id or not action:
                self.send_json({'ok': False, 'error': 'taskId/action required'}, 400)
                return
            self.send_json(handle_scheduler_action(task_id, action, reason, expected_version, owner_run_id, recovery_target))
            return

        if p == '/api/scheduler-commit':
            self.send_json(handle_scheduler_commit(body))
            return

        if p == '/api/scheduler-retry':
            task_id = body.get('taskId', '').strip()
            reason = body.get('reason', '').strip()
            if not task_id:
                self.send_json({'ok': False, 'error': 'taskId required'}, 400)
                return
            self.send_json(handle_scheduler_action(task_id, 'retry', reason))
            return

        if p == '/api/scheduler-escalate':
            task_id = body.get('taskId', '').strip()
            reason = body.get('reason', '').strip()
            if not task_id:
                self.send_json({'ok': False, 'error': 'taskId required'}, 400)
                return
            self.send_json(handle_scheduler_action(task_id, 'escalate', reason))
            return

        if p == '/api/scheduler-rollback':
            task_id = body.get('taskId', '').strip()
            reason = body.get('reason', '').strip()
            if not task_id:
                self.send_json({'ok': False, 'error': 'taskId required'}, 400)
                return
            self.send_json(handle_scheduler_action(task_id, 'rollback', reason))
            return

        if p == '/api/morning-brief/refresh':
            force = body.get('force', True)  # 从看板手动触发默认强制
            def do_refresh():
                try:
                    cmd = ['python3', str(SCRIPTS / 'fetch_morning_news.py')]
                    if force:
                        cmd.append('--force')
                    subprocess.run(cmd, timeout=120)
                    push_to_feishu()
                except Exception as e:
                    print(f'[refresh error] {e}', file=sys.stderr)
            threading.Thread(target=do_refresh, daemon=True).start()
            self.send_json({'ok': True, 'message': '采集已触发，约30-60秒后刷新'})
            return

        if p == '/api/add-skill':
            agent_id = body.get('agentId', '').strip()
            skill_name = body.get('skillName', body.get('name', '')).strip()
            desc = body.get('description', '').strip() or skill_name
            trigger = body.get('trigger', '').strip()
            if not agent_id or not skill_name:
                self.send_json({'ok': False, 'error': 'agentId and skillName required'}, 400)
                return
            result = add_skill_to_agent(agent_id, skill_name, desc, trigger)
            self.send_json(result)
            return

        if p == '/api/add-remote-skill':
            agent_id = body.get('agentId', '').strip()
            skill_name = body.get('skillName', '').strip()
            source_url = body.get('sourceUrl', '').strip()
            description = body.get('description', '').strip()
            if not agent_id or not skill_name or not source_url:
                self.send_json({'ok': False, 'error': 'agentId, skillName, and sourceUrl required'}, 400)
                return
            result = add_remote_skill(agent_id, skill_name, source_url, description)
            self.send_json(result)
            return

        if p == '/api/remote-skills-list':
            result = get_remote_skills_list()
            self.send_json(result)
            return

        if p == '/api/update-remote-skill':
            agent_id = body.get('agentId', '').strip()
            skill_name = body.get('skillName', '').strip()
            if not agent_id or not skill_name:
                self.send_json({'ok': False, 'error': 'agentId and skillName required'}, 400)
                return
            result = update_remote_skill(agent_id, skill_name)
            self.send_json(result)
            return

        if p == '/api/remove-remote-skill':
            agent_id = body.get('agentId', '').strip()
            skill_name = body.get('skillName', '').strip()
            if not agent_id or not skill_name:
                self.send_json({'ok': False, 'error': 'agentId and skillName required'}, 400)
                return
            result = remove_remote_skill(agent_id, skill_name)
            self.send_json(result)
            return

        if p == '/api/task-action':
            task_id = body.get('taskId', '').strip()
            action = body.get('action', '').strip()  # stop, cancel, resume
            reason = body.get('reason', '').strip() or f'皇上从看板{action}'
            if not task_id or action not in ('stop', 'cancel', 'resume'):
                self.send_json({'ok': False, 'error': 'taskId and action(stop/cancel/resume) required'}, 400)
                return
            result = handle_task_action(task_id, action, reason)
            self.send_json(result)
            return

        if p == '/api/archive-task':
            task_id = body.get('taskId', '').strip() if body.get('taskId') else ''
            archived = body.get('archived', True)
            archive_all = body.get('archiveAllDone', False)
            if not task_id and not archive_all:
                self.send_json({'ok': False, 'error': 'taskId or archiveAllDone required'}, 400)
                return
            result = handle_archive_task(task_id, archived, archive_all)
            self.send_json(result)
            return

        if p == '/api/task-todos':
            task_id = body.get('taskId', '').strip()
            todos = body.get('todos', [])  # [{id, title, status}]
            if not task_id:
                self.send_json({'ok': False, 'error': 'taskId required'}, 400)
                return
            # todos 输入校验
            if not isinstance(todos, list) or len(todos) > 200:
                self.send_json({'ok': False, 'error': 'todos must be a list (max 200 items)'}, 400)
                return
            valid_statuses = {'not-started', 'in-progress', 'completed'}
            for td in todos:
                if not isinstance(td, dict) or 'id' not in td or 'title' not in td:
                    self.send_json({'ok': False, 'error': 'each todo must have id and title'}, 400)
                    return
                if td.get('status', 'not-started') not in valid_statuses:
                    td['status'] = 'not-started'
            result = update_task_todos(task_id, todos)
            self.send_json(result)
            return

        if p == '/api/create-task':
            title = body.get('title', '').strip()
            org = body.get('org', '中书省').strip()
            official = body.get('official', '中书令').strip()
            priority = body.get('priority', 'normal').strip()
            template_id = body.get('templateId', '')
            params = body.get('params', {})
            if not title:
                self.send_json({'ok': False, 'error': 'title required'}, 400)
                return
            target_dept = body.get('targetDept', '').strip()
            result = handle_create_task(title, org, official, priority, template_id, params, target_dept)
            self.send_json(result)
            return

        if p == '/api/court-discuss':
            action = body.get('action', 'start')
            topic = body.get('topic', '').strip()
            participants = body.get('participants', [])
            session_id = body.get('sessionId', '').strip()
            force = bool(body.get('force', False))
            emperor_note = body.get('emperorNote', '').strip()
            if action == 'start' and not topic:
                self.send_json({'ok': False, 'error': 'topic required'}, 400)
                return
            if action in ('next', 'status', 'finalize', 'handoff', 'terminate') and not session_id:
                self.send_json({'ok': False, 'error': 'sessionId required'}, 400)
                return
            result = handle_court_discuss(
                action=action,
                topic=topic,
                participants=participants,
                session_id=session_id,
                force=force,
                emperor_note=emperor_note,
            )
            self.send_json(result)
            return

        if p == '/api/review-action':
            task_id = body.get('taskId', '').strip()
            action = body.get('action', '').strip()  # approve, reject
            comment = body.get('comment', '').strip()
            if not task_id or action not in ('approve', 'reject'):
                self.send_json({'ok': False, 'error': 'taskId and action(approve/reject) required'}, 400)
                return
            result = handle_review_action(task_id, action, comment)
            self.send_json(result)
            return

        if p == '/api/advance-state':
            task_id = body.get('taskId', '').strip()
            comment = body.get('comment', '').strip()
            if not task_id:
                self.send_json({'ok': False, 'error': 'taskId required'}, 400)
                return
            result = handle_advance_state(task_id, comment)
            self.send_json(result)
            return

        if p == '/api/agent-wake':
            agent_id = body.get('agentId', '').strip()
            message = body.get('message', '').strip()
            if not agent_id:
                self.send_json({'ok': False, 'error': 'agentId required'}, 400)
                return
            result = wake_agent(agent_id, message)
            self.send_json(result)
            return

        if p == '/api/set-model':
            agent_id = body.get('agentId', '').strip()
            model = body.get('model', '').strip()
            if not agent_id or not model:
                self.send_json({'ok': False, 'error': 'agentId and model required'}, 400)
                return

            # Write to pending (atomic)
            pending_path = DATA / 'pending_model_changes.json'
            def update_pending(current):
                current = [x for x in current if x.get('agentId') != agent_id]
                current.append({'agentId': agent_id, 'model': model})
                return current
            atomic_json_update(pending_path, update_pending, [])

            # Async apply
            def apply_async():
                try:
                    subprocess.run(['python3', str(SCRIPTS / 'apply_model_changes.py')], timeout=30)
                    subprocess.run(['python3', str(SCRIPTS / 'sync_agent_config.py')], timeout=10)
                except Exception as e:
                    print(f'[apply error] {e}', file=sys.stderr)

            threading.Thread(target=apply_async, daemon=True).start()
            self.send_json({'ok': True, 'message': f'Queued: {agent_id} → {model}'})
        else:
            self.send_error(404)


def main():
    parser = argparse.ArgumentParser(description='三省六部看板服务器')
    parser.add_argument('--port', type=int, default=7891)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--cors', default=None, help='Allowed CORS origin (default: reflect request Origin header)')
    args = parser.parse_args()

    global ALLOWED_ORIGIN
    ALLOWED_ORIGIN = args.cors

    server = HTTPServer((args.host, args.port), Handler)
    log.info(f'三省六部看板启动 → http://{args.host}:{args.port}')
    print(f'   按 Ctrl+C 停止')

    # 启动恢复：重新派发上次被 kill 中断的 queued 任务
    threading.Timer(3.0, _startup_recover_queued_dispatches).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止')


if __name__ == '__main__':
    main()
