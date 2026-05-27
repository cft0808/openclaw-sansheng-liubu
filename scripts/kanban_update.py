#!/usr/bin/env python3
"""
看板任务更新工具 - 供各省部 Agent 调用

本工具操作 data/tasks_source.json（JSON 看板模式）。
如果您已部署 edict/backend（Postgres + Redis 事件总线模式），
请使用 edict/backend API 端点代替本脚本，或运行迁移脚本：
  python3 edict/migration/migrate_json_to_pg.py

两种模式互相独立，数据不会自动同步。

用法:
  # 查询任务
  python3 kanban_update.py show JJC-20260223-012

  # 新建任务（收旨时）
  python3 kanban_update.py create JJC-20260223-012 "任务标题" Zhongshu 中书省 中书令

  # 更新状态
  python3 kanban_update.py state JJC-20260223-012 Menxia "规划方案已提交门下省"

  # 添加流转记录
  python3 kanban_update.py flow JJC-20260223-012 "中书省" "门下省" "规划方案提交审核"

  # 完成任务
  python3 kanban_update.py done JJC-20260223-012 "/path/to/output" "任务完成摘要"

  # 添加/更新子任务 todo
  python3 kanban_update.py todo JJC-20260223-012 1 "实现API接口" in-progress
  python3 kanban_update.py todo JJC-20260223-012 1 "" completed

  # 🔥 实时进展汇报（Agent 主动调用，频率不限）
  python3 kanban_update.py progress JJC-20260223-012 "正在分析需求，拟定3个子方案" "1.调研技术选型|2.撰写设计文档|3.实现原型"
"""
import datetime
import json, pathlib, sys, subprocess, logging, os, re
from utils import python_bin

_BASE = pathlib.Path(os.environ['EDICT_HOME']) if 'EDICT_HOME' in os.environ else pathlib.Path(__file__).resolve().parent.parent
TASKS_FILE = _BASE / 'data' / 'tasks_source.json'
REFRESH_SCRIPT = _BASE / 'scripts' / 'refresh_live_data.py'

log = logging.getLogger('kanban')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s', datefmt='%H:%M:%S')

# 文件锁 —— 防止多 Agent 同时读写 tasks_source.json
from file_lock import atomic_json_read, atomic_json_update  # noqa: E402
from utils import now_iso  # noqa: E402

try:
    from event_log import append_event as _ledger_append_event  # noqa: E402
except Exception:  # pragma: no cover - event ledger must not break kanban writes
    _ledger_append_event = None


# ── 从 task.py 动态加载权威状态转换表（Single Source of Truth）──
def _load_canonical_transitions() -> dict:
    """从 edict/backend 源码解析状态转换表，无需 import（避免 SQLAlchemy 依赖）。"""
    task_py = _BASE / "edict" / "backend" / "app" / "models" / "task.py"
    source = task_py.read_text(encoding="utf-8")

    m = re.search(r"STATE_TRANSITIONS\s*=\s*\{", source)
    if not m:
        return None
    start = m.start()
    depth = 0
    end = start
    for i, ch in enumerate(source[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    block = source[start:end]
    cleaned = re.sub(r"TaskState\.(\w+)", r'"\1"', block)
    cleaned = cleaned.replace("STATE_TRANSITIONS =", "_result =")
    local_ns = {}
    exec(cleaned, {}, local_ns)  # noqa: S102
    return local_ns["_result"]


STATE_ORG_MAP = {
    'Taizi': '太子', 'Zhongshu': '中书省', 'Menxia': '门下省',
    'Assigned': '尚书省', 'Next': '尚书省',
    'Doing': '执行中', 'Review': '尚书省', 'Done': '完成', 'Blocked': '阻塞',
    'PendingConfirm': '尚书省', 'Pending': '中书省',
}

_STATE_AGENT_MAP = {
    'Taizi': 'taizi',
    'Zhongshu': 'zhongshu',
    'Menxia': 'menxia',
    'Assigned': 'shangshu',
    'Review': 'shangshu',
    'Pending': 'zhongshu',
    'PendingConfirm': 'shangshu',
}

_ORG_AGENT_MAP = {
    '礼部': 'libu', '户部': 'hubu', '兵部': 'bingbu',
    '刑部': 'xingbu', '工部': 'gongbu', '吏部': 'libu_hr',
    '中书省': 'zhongshu', '门下省': 'menxia', '尚书省': 'shangshu',
}

_AGENT_LABELS = {
    'main': '太子', 'taizi': '太子',
    'zhongshu': '中书省', 'menxia': '门下省', 'shangshu': '尚书省',
    'libu': '礼部', 'hubu': '户部', 'bingbu': '兵部', 'xingbu': '刑部',
    'gongbu': '工部', 'libu_hr': '吏部', 'zaochao': '钦天监',
}

MAX_PROGRESS_LOG = 100  # 单任务最大进展日志条数

def load():
    return atomic_json_read(TASKS_FILE, [])


def _append_runtime_event(kind, task_id='', agent_id='', payload=None, evidence=None, confidence='high', at=None):
    """Best-effort append to the runtime event ledger."""
    if _ledger_append_event is None:
        return None
    try:
        return _ledger_append_event(
            kind,
            task_id=task_id,
            agent_id=agent_id or _infer_agent_id_from_runtime(),
            runtime=os.environ.get('EDICT_RUNTIME', ''),
            source=agent_id or _infer_agent_id_from_runtime() or 'kanban_update',
            payload=payload or {},
            evidence=evidence or {},
            confidence=confidence,
            at=at,
        )
    except Exception as exc:
        log.debug(f'事件账本写入失败 ({kind}/{task_id}): {exc}')
        return None


def _touch_scheduler_progress(task, at, agent_id='', reset_retry=True):
    """同步调度器心跳，避免 Agent 已上报但巡检仍按旧时间误判停滞。"""
    sched = task.setdefault('_scheduler', {})
    if not isinstance(sched, dict):
        return
    sched['lastProgressAt'] = at
    if agent_id:
        sched['lastProgressAgent'] = agent_id
    sched['stallSince'] = None
    if reset_retry:
        sched['retryCount'] = 0
        sched['escalationLevel'] = 0
    sched.pop('activeDispatchId', None)
    sched.pop('activeDispatchState', None)
    sched.pop('activeDispatchStartedAt', None)
    if sched.get('lastDispatchStatus') in {
        'queued', 'timeout', 'failed', 'error', 'gateway-offline',
        'openclaw-missing', 'opencode-missing',
    }:
        sched['lastDispatchStatus'] = 'progress'
        sched['lastDispatchError'] = ''

_REFRESH_SIGNAL_FILE = _BASE / 'data' / '.refresh_pending'

def _trigger_refresh():
    """Debounced refresh — touch 信号文件，由独立 watcher 合并执行。
    
    替代原来每次 fork subprocess 的方式，避免多 Agent 并发时产生数百个进程。
    如果 refresh_watcher 未运行，会 fallback 到直接 fork（保持向后兼容）。
    """
    try:
        _REFRESH_SIGNAL_FILE.touch(exist_ok=True)
    except Exception:
        pass
    # Fallback: 如果信号文件 3 秒后仍存在（watcher 没在运行），直接 fork
    # 注意：这个 fallback 只在非 watcher 部署场景触发
    if not (_BASE / 'data' / '.refresh_watcher_pid').exists():
        try:
            subprocess.Popen([python_bin(), str(REFRESH_SCRIPT)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


# ── 审计日志 ──
AUDIT_FILE = _BASE / 'data' / 'audit_log.json'
MAX_AUDIT_LOG = 5000  # 审计日志最大条数

def _append_audit(task_id, agent, action, old_val=None, new_val=None, reason=""):
    """追加一条审计记录到 data/audit_log.json（原子操作）。"""
    entry = {
        "ts": now_iso(),
        "task": task_id or "",
        "agent": agent or "",
        "action": action,
        "from": old_val,
        "to": new_val,
        "reason": reason,
    }
    try:
        def modifier(logs):
            if logs is None:
                logs = []
            logs.append(entry)
            if len(logs) > MAX_AUDIT_LOG:
                logs = logs[-MAX_AUDIT_LOG:]
            return logs
        atomic_json_update(AUDIT_FILE, modifier, [])
    except Exception as e:
        log.warning(f"审计日志写入失败: {e}")


# ── 越权检测（Agent 权限策略）──
AGENT_POLICY = {
    "taizi":    {"role": "coordination", "commands": {"show", "create", "state", "flow", "progress", "todo", "memory", "task-memo"}},
    "zhongshu": {"role": "coordination", "commands": {"show", "state", "flow", "progress", "todo", "memory", "task-memo", "delegate"}},
    "menxia":   {"role": "coordination", "commands": {"show", "state", "flow", "progress", "todo", "confirm", "memory", "task-memo"}},
    "shangshu": {"role": "coordination", "commands": {"show", "state", "flow", "progress", "todo", "confirm", "delegate", "memory", "task-memo", "shared-memo"}},
    "zaochao":  {"role": "coordination", "commands": {"show", "progress", "todo", "memory"}},
    "hubu":     {"role": "execution", "commands": {"show", "progress", "todo", "done", "block", "memory", "task-memo", "delegate-result"}},
    "libu":     {"role": "execution", "commands": {"show", "progress", "todo", "done", "block", "memory", "task-memo", "delegate-result"}},
    "bingbu":   {"role": "execution", "commands": {"show", "progress", "todo", "done", "block", "memory", "task-memo", "delegate-result"}},
    "xingbu":   {"role": "execution", "commands": {"show", "progress", "todo", "done", "block", "memory", "task-memo", "delegate-result"}},
    "gongbu":   {"role": "execution", "commands": {"show", "progress", "todo", "done", "block", "memory", "task-memo", "delegate-result"}},
    "libu_hr":  {"role": "execution", "commands": {"show", "progress", "todo", "done", "block", "memory", "task-memo", "delegate-result"}},
}

def _check_permission(agent_id, cmd):
    """检查 Agent 是否有权执行该命令。未知 Agent 不拦截（向前兼容）。"""
    if not agent_id:
        return  # 无法推断 Agent 身份时不拦截
    policy = AGENT_POLICY.get(agent_id)
    if policy is None:
        return  # 未注册的 Agent 不拦截
    if cmd not in policy["commands"]:
        _append_audit(None, agent_id, "permission_denied", cmd, None, f"{agent_id} 越权执行 {cmd}")
        log.warning(f"⛔ {agent_id} 无权执行 {cmd}（允许: {policy['commands']}）")
        print(f"[看板] 越权拒绝: {agent_id} 不可执行 {cmd}", flush=True)
        sys.exit(1)


def find_task(tasks, task_id):
    return next((t for t in tasks if t.get('id') == task_id), None)


def cmd_show(task_id):
    """打印单个任务的当前看板记录，供 Agent 查询上下文。"""
    tasks = atomic_json_read(TASKS_FILE, [])
    task = find_task(tasks, task_id)
    if not task:
        print(json.dumps({'ok': False, 'error': f'任务 {task_id} 不存在'}, ensure_ascii=False))
        sys.exit(1)
    print(json.dumps({'ok': True, 'task': task}, ensure_ascii=False, indent=2))


# 旨意标题最低要求
_MIN_TITLE_LEN = 6
_JUNK_TITLES = {
    '?', '？', '好', '好的', '是', '否', '不', '不是', '对', '了解', '收到',
    '嗯', '哦', '知道了', '开启了么', '可以', '不行', '行', 'ok', 'yes', 'no',
    '你去开启', '测试', '试试', '看看',
}

def _sanitize_text(raw, max_len=80):
    """清洗文本：剥离文件路径、URL、Conversation 元数据、传旨前缀、截断过长内容。"""
    t = (raw or '').strip()
    # 1) 剥离 Conversation info / Conversation 后面的所有内容
    t = re.split(r'\n*Conversation\b', t, maxsplit=1)[0].strip()
    # 2) 剥离 ```json 代码块
    t = re.split(r'\n*```', t, maxsplit=1)[0].strip()
    # 3) 剥离 Unix/Mac 文件路径 (/Users/xxx, /home/xxx, /opt/xxx, ./xxx)
    t = re.sub(r'[/\\.~][A-Za-z0-9_\-./]+(?:\.(?:py|js|ts|json|md|sh|yaml|yml|txt|csv|html|css|log))?', '', t)
    # 4) 剥离 URL
    t = re.sub(r'https?://\S+', '', t)
    # 5) 清理常见前缀: "传旨:" "下旨:" "下旨（xxx）:" 等
    t = re.sub(r'^(传旨|下旨)([（(][^)）]*[)）])?[：:\uff1a]\s*', '', t)
    # 6) 剥离系统元数据关键词
    t = re.sub(r'(message_id|session_id|chat_id|open_id|user_id|tenant_key)\s*[:=]\s*\S+', '', t)
    # 7) 合并多余空白
    t = re.sub(r'\s+', ' ', t).strip()
    # 8) 截断过长内容
    if len(t) > max_len:
        t = t[:max_len] + '…'
    return t


def _sanitize_title(raw):
    """清洗标题（最长 80 字符）。"""
    return _sanitize_text(raw, 80)


def _sanitize_remark(raw):
    """清洗流转备注（最长 120 字符）。"""
    return _sanitize_text(raw, 120)


def _todo_counts(task):
    """返回 (completed, total) 便于完成态校验。"""
    todos = task.get('todos') or []
    total = len(todos)
    completed = sum(1 for td in todos if td.get('status') == 'completed')
    return completed, total


def _infer_agent_id_from_runtime(task=None):
    """尽量推断当前执行该命令的 Agent。"""
    for k in ('OPENCLAW_AGENT_ID', 'OPENCLAW_AGENT', 'AGENT_ID'):
        v = (os.environ.get(k) or '').strip()
        if v:
            return v

    cwd = str(pathlib.Path.cwd())
    m = re.search(r'workspace-([a-zA-Z0-9_\-]+)', cwd)
    if m:
        return m.group(1)

    fpath = str(pathlib.Path(__file__).resolve())
    m2 = re.search(r'workspace-([a-zA-Z0-9_\-]+)', fpath)
    if m2:
        return m2.group(1)

    if task:
        state = task.get('state', '')
        org = task.get('org', '')
        aid = _STATE_AGENT_MAP.get(state)
        if aid is None and state in ('Doing', 'Next'):
            aid = _ORG_AGENT_MAP.get(org)
        if aid:
            return aid
    return ''


def _is_valid_task_title(title):
    """校验标题是否足够作为一个旨意任务。"""
    t = (title or '').strip()
    if len(t) < _MIN_TITLE_LEN:
        return False, f'标题过短（{len(t)}<{_MIN_TITLE_LEN}字），疑似非旨意'
    if t.lower() in _JUNK_TITLES:
        return False, f'标题 "{t}" 不是有效旨意'
    # 纯标点或问号
    if re.fullmatch(r'[\s?？!！.。,，…·\-—~]+', t):
        return False, '标题只有标点符号'
    # 看起来像文件路径
    if re.match(r'^[/\\~.]', t) or re.search(r'/[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+', t):
        return False, f'标题看起来像文件路径，请用中文概括任务'
    # 只剩标点和空白（清洗后可能变空）
    if re.fullmatch(r'[\s\W]*', t):
        return False, '标题清洗后为空'
    return True, ''


def cmd_create(task_id, title, state, org, official, remark=None):
    """新建任务（收旨时立即调用）"""
    # 清洗标题（剥离元数据）
    title = _sanitize_title(title)
    # 旨意标题校验
    valid, reason = _is_valid_task_title(title)
    if not valid:
        log.warning(f'⚠️ 拒绝创建 {task_id}：{reason}')
        print(f'[看板] 拒绝创建：{reason}', flush=True)
        return
    actual_org = STATE_ORG_MAP.get(state, org)
    clean_remark = _sanitize_remark(remark) if remark else f"下旨：{title}"
    def modifier(tasks):
        existing = next((t for t in tasks if t.get('id') == task_id), None)
        if existing:
            ex_state = existing.get('state')
            if ex_state in ('Done', 'Cancelled', 'Review', 'Doing', 'Next',
                            'Zhongshu', 'Menxia', 'Assigned', 'Blocked'):
                log.warning(f'⚠️ 任务 {task_id} 已在流转中 (state={ex_state})，不可覆盖')
                return tasks
            if ex_state not in (None, '', 'Inbox', 'Pending'):
                log.warning(f'任务 {task_id} 已存在 (state={ex_state})，将被覆盖')
        tasks = [t for t in tasks if t.get('id') != task_id]
        tasks.insert(0, {
            "id": task_id, "title": title, "official": official,
            "org": actual_org, "state": state,
            "now": clean_remark[:60] if remark else f"已下旨，等待{actual_org}接旨",
            "eta": "-", "block": "无", "output": "", "ac": "",
            "flow_log": [{"at": now_iso(), "from": "皇上", "to": actual_org, "remark": clean_remark}],
            "updatedAt": now_iso()
        })
        return tasks
    atomic_json_update(TASKS_FILE, modifier, [])
    _trigger_refresh()
    log.info(f'✅ 创建 {task_id} | {title[:30]} | state={state}')
    agent_id = _infer_agent_id_from_runtime()
    _append_audit(task_id, agent_id, 'create', None, state, title)
    _append_runtime_event('task_created', task_id, agent_id, {
        'title': title,
        'newState': state,
        'to': actual_org,
        'remark': clean_remark,
    })


# ── 状态流转合法性校验 ──
# 从 task.py 动态加载（如果 edict 目录存在），否则使用内置 fallback
_edict_task_path = _BASE / "edict" / "backend" / "app" / "models" / "task.py"
if _edict_task_path.exists():
    _VALID_TRANSITIONS = _load_canonical_transitions()
else:
    # Fallback：当 edict 目录不存在时使用内置定义（必须与 task.py 保持一致）
    _VALID_TRANSITIONS = {
        'Pending':        {'Taizi', 'Cancelled'},
        'Taizi':          {'Zhongshu', 'Cancelled'},
        'Zhongshu':       {'Menxia', 'Cancelled', 'Blocked'},
        'Menxia':         {'Assigned', 'Zhongshu', 'Cancelled'},
        'Assigned':       {'Doing', 'Next', 'Blocked', 'Cancelled'},
        'Next':           {'Doing', 'Blocked', 'Cancelled'},
        'Doing':          {'Review', 'Done', 'Blocked', 'Cancelled'},
        'Review':         {'Done', 'Menxia', 'Doing', 'Cancelled', 'PendingConfirm'},
        'PendingConfirm': {'Done', 'Review', 'Cancelled'},
        'Blocked':        {'Taizi', 'Zhongshu', 'Menxia', 'Assigned', 'Next', 'Doing', 'Review', 'Cancelled'},
        'Done':           set(),
        'Cancelled':      set(),
    }


# ── 高风险操作确认机制 ──

# 需要进入 PendingConfirm 中间状态的高风险转换
HIGH_RISK_TRANSITIONS = {
    ('Review', 'Done'),       # 完结任务 — 需门下省确认
    ('Doing', 'Cancelled'),   # 执行中取消 — 需尚书省确认
    ('Menxia', 'Cancelled'),  # 审核中取消 — 需中书省确认
}

# 各状态的确认权限方
CONFIRM_AUTHORITY = {
    'Review': 'menxia',
    'Doing': 'shangshu',
    'Menxia': 'zhongshu',
}


def cmd_state(task_id, new_state, now_text=None):
    """更新任务状态（原子操作，含流转合法性校验 + 高风险拦截）"""
    old_state = [None]
    rejected = [False]
    pending_confirm = [False]
    idempotent = [False]
    agent_id = _infer_agent_id_from_runtime()
    def modifier(tasks):
        t = find_task(tasks, task_id)
        if not t:
            log.error(f'任务 {task_id} 不存在')
            return tasks
        old_state[0] = t['state']
        if old_state[0] == new_state:
            idempotent[0] = True
            if now_text:
                t['now'] = now_text
            at = now_iso()
            t['updatedAt'] = at
            _touch_scheduler_progress(t, at, agent_id, reset_retry=False)
            return tasks
        allowed = _VALID_TRANSITIONS.get(old_state[0])
        if allowed is not None and new_state not in allowed:
            log.warning(f'⚠️ 非法状态转换 {task_id}: {old_state[0]} → {new_state}（允许: {allowed}）')
            rejected[0] = True
            return tasks
        # 高风险操作拦截 → 进入 PendingConfirm
        if (old_state[0], new_state) in HIGH_RISK_TRANSITIONS:
            t['state'] = 'PendingConfirm'
            t['org'] = STATE_ORG_MAP.get('PendingConfirm', t.get('org', ''))
            t['pending_confirm'] = {
                'target_state': new_state,
                'requested_by': _infer_agent_id_from_runtime(t),
                'requested_at': now_iso(),
                'confirm_by': CONFIRM_AUTHORITY.get(old_state[0], 'shangshu'),
            }
            t['now'] = f'待确认: {old_state[0]}→{new_state}'
            at = now_iso()
            t['updatedAt'] = at
            _touch_scheduler_progress(t, at, agent_id)
            pending_confirm[0] = True
            return tasks
        t['state'] = new_state
        if new_state in STATE_ORG_MAP:
            t['org'] = STATE_ORG_MAP[new_state]
        if now_text:
            t['now'] = now_text
        at = now_iso()
        t['updatedAt'] = at
        _touch_scheduler_progress(t, at, agent_id)
        return tasks
    atomic_json_update(TASKS_FILE, modifier, [])
    _trigger_refresh()
    if rejected[0]:
        log.info(f'❌ {task_id} 状态转换被拒: {old_state[0]} → {new_state}')
        agent_id = _infer_agent_id_from_runtime()
        _append_audit(task_id, agent_id, 'state_rejected', old_state[0], new_state, '非法状态转换')
        _append_runtime_event('state_rejected', task_id, agent_id, {
            'oldState': old_state[0],
            'newState': new_state,
            'reason': '非法状态转换',
        }, confidence='high')
    elif idempotent[0]:
        log.info(f'ℹ️ {task_id} 状态已是 {new_state}，按心跳处理')
        agent_id = _infer_agent_id_from_runtime()
        _append_audit(task_id, agent_id, 'state_noop', old_state[0], new_state, now_text or '')
        _append_runtime_event('state_noop', task_id, agent_id, {
            'oldState': old_state[0],
            'newState': new_state,
            'remark': now_text or '',
        })
    elif pending_confirm[0]:
        log.info(f'⏳ {task_id} 高风险操作 {old_state[0]}→{new_state}，进入 PendingConfirm 待确认')
        agent_id = _infer_agent_id_from_runtime()
        confirm_by = CONFIRM_AUTHORITY.get(old_state[0], "shangshu")
        _append_audit(task_id, agent_id, 'pending_confirm', old_state[0], new_state, f'需 {confirm_by} 确认')
        _append_runtime_event('pending_confirm', task_id, agent_id, {
            'oldState': old_state[0],
            'newState': 'PendingConfirm',
            'targetState': new_state,
            'to': confirm_by,
            'remark': f'高风险转换待确认: {old_state[0]} -> {new_state}',
        })
    else:
        log.info(f'✅ {task_id} 状态更新: {old_state[0]} → {new_state}')
        agent_id = _infer_agent_id_from_runtime()
        _append_audit(task_id, agent_id, 'state', old_state[0], new_state, now_text or '')
        _append_runtime_event('state_changed', task_id, agent_id, {
            'oldState': old_state[0],
            'newState': new_state,
            'remark': now_text or '',
        })


def cmd_flow(task_id, from_dept, to_dept, remark):
    """添加流转记录（原子操作）"""
    clean_remark = _sanitize_remark(remark)
    agent_id = _infer_agent_id_from_runtime()
    agent_label = _AGENT_LABELS.get(agent_id, agent_id)
    start_only = clean_remark.startswith('▶️ 开始执行') and from_dept == to_dept
    flow_at = [None]
    def modifier(tasks):
        t = find_task(tasks, task_id)
        if not t:
            log.error(f'任务 {task_id} 不存在')
            return tasks
        at = now_iso()
        flow_at[0] = at
        t.setdefault('flow_log', []).append({
            "at": at, "from": from_dept, "to": to_dept, "remark": clean_remark,
            "agent": agent_id, "agentLabel": agent_label,
        })
        # 同步更新 org，使看板能正确显示当前所属部门
        t['org'] = to_dept
        t['updatedAt'] = now_iso()
        _touch_scheduler_progress(t, t['updatedAt'], agent_id, reset_retry=not start_only)
        return tasks
    atomic_json_update(TASKS_FILE, modifier, [])
    _trigger_refresh()
    log.info(f'✅ {task_id} 流转记录: {from_dept} → {to_dept}')
    _append_audit(task_id, _infer_agent_id_from_runtime(), 'flow', from_dept, to_dept, clean_remark)
    _append_runtime_event('flow_recorded', task_id, agent_id, {
        'from': from_dept,
        'to': to_dept,
        'remark': clean_remark,
    }, at=flow_at[0])


def cmd_done(task_id, output_path='', summary=''):
    """执行部门回报完成，任务进入 Review 待尚书省汇总审查。"""
    rejected = [False]
    reject_reason = ['']
    done_payload = [{}]
    def modifier(tasks):
        t = find_task(tasks, task_id)
        if not t:
            log.error(f'任务 {task_id} 不存在')
            return tasks
        old_state = t.get('state')
        if old_state not in ('Doing', 'Next'):
            rejected[0] = True
            reject_reason[0] = f'当前状态 {old_state} 不允许直接上报完成'
            done_payload[0] = {'oldState': old_state, 'newState': 'Review'}
            return tasks
        completed, total = _todo_counts(t)
        if total > 0 and completed < total:
            rejected[0] = True
            reject_reason[0] = f'todos 未完成（{completed}/{total}），禁止直接收口'
            done_payload[0] = {'oldState': old_state, 'newState': 'Review'}
            return tasks

        from_org = t.get('org', '执行部门')
        at = now_iso()
        t['state'] = 'Review'
        t['org'] = STATE_ORG_MAP.get('Review', t.get('org', ''))
        t['output'] = output_path
        t['now'] = summary or '执行已完成，提交尚书省汇总审查'
        t.setdefault('flow_log', []).append({
            "at": at, "from": from_org,
            "to": "尚书省", "remark": f"✅ 执行完成，提交审查：{summary or '待尚书省汇总'}"
        })
        done_payload[0] = {
            'oldState': old_state,
            'newState': 'Review',
            'from': from_org,
            'to': '尚书省',
            'summary': summary or '',
            'outputPath': output_path,
            'at': at,
        }
        # 同步设置 outputMeta，避免依赖 refresh_live_data.py 异步补充
        if output_path:
            p = pathlib.Path(output_path)
            if p.exists():
                ts = datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                t['outputMeta'] = {"exists": True, "lastModified": ts}
            else:
                t['outputMeta'] = {"exists": False, "lastModified": None}
        t['updatedAt'] = now_iso()
        _touch_scheduler_progress(t, t['updatedAt'], _infer_agent_id_from_runtime(t))
        return tasks
    atomic_json_update(TASKS_FILE, modifier, [])
    _trigger_refresh()
    if rejected[0]:
        log.warning(f'⚠️ {task_id} done 被拒绝：{reject_reason[0]}')
        agent_id = _infer_agent_id_from_runtime()
        _append_audit(task_id, agent_id, 'done_rejected', None, 'Review', reject_reason[0])
        _append_runtime_event('state_rejected', task_id, agent_id, {
            **done_payload[0],
            'reason': reject_reason[0],
        }, confidence='high')
        return
    log.info(f'✅ {task_id} 执行完成，已提交尚书省审查')
    agent_id = _infer_agent_id_from_runtime()
    _append_audit(task_id, agent_id, 'done', None, 'Review', summary or '')
    _append_runtime_event('task_done', task_id, agent_id, done_payload[0], at=done_payload[0].get('at'))


def cmd_block(task_id, reason):
    """标记阻塞（原子操作）"""
    old_state = ['']
    def modifier(tasks):
        t = find_task(tasks, task_id)
        if not t:
            log.error(f'任务 {task_id} 不存在')
            return tasks
        old_state[0] = t.get('state', '')
        at = now_iso()
        t['state'] = 'Blocked'
        t['block'] = reason
        t['updatedAt'] = at
        _touch_scheduler_progress(t, at, _infer_agent_id_from_runtime(t))
        return tasks
    atomic_json_update(TASKS_FILE, modifier, [])
    _trigger_refresh()
    log.warning(f'⚠️ {task_id} 已阻塞: {reason}')
    agent_id = _infer_agent_id_from_runtime()
    _append_audit(task_id, agent_id, 'block', None, 'Blocked', reason)
    _append_runtime_event('task_blocked', task_id, agent_id, {
        'oldState': old_state[0],
        'newState': 'Blocked',
        'reason': reason,
    }, confidence='high')


def cmd_confirm(task_id, action, reason=''):
    """确认或驳回 PendingConfirm 状态的高风险操作。

    action: approve / reject
    """
    result_state = [None]
    rejected = [False]
    def modifier(tasks):
        t = find_task(tasks, task_id)
        if not t:
            log.error(f'任务 {task_id} 不存在')
            return tasks
        if t.get('state') != 'PendingConfirm':
            log.warning(f'⚠️ {task_id} 不在 PendingConfirm 状态 (当前: {t.get("state")})')
            rejected[0] = True
            return tasks
        pending = t.get('pending_confirm', {})
        if action == 'approve':
            target = pending.get('target_state', 'Done')
            t['state'] = target
            if target in STATE_ORG_MAP:
                t['org'] = STATE_ORG_MAP[target]
            t['now'] = reason or f'确认通过 → {target}'
            result_state[0] = target
        elif action == 'reject':
            # 驳回 → 回到 Review
            t['state'] = 'Review'
            t['org'] = STATE_ORG_MAP.get('Review', t.get('org', ''))
            t['now'] = reason or '确认被驳回，退回复审'
            result_state[0] = 'Review'
        else:
            log.error(f'未知 confirm 操作: {action}')
            rejected[0] = True
            return tasks
        t.pop('pending_confirm', None)
        at = now_iso()
        t['updatedAt'] = at
        _touch_scheduler_progress(t, at, _infer_agent_id_from_runtime(t))
        t.setdefault('flow_log', []).append({
            'at': now_iso(), 'from': 'PendingConfirm', 'to': result_state[0],
            'remark': f'{"✅ 批准" if action == "approve" else "❌ 驳回"}: {reason}',
        })
        return tasks
    atomic_json_update(TASKS_FILE, modifier, [])
    _trigger_refresh()
    if rejected[0]:
        log.info(f'❌ {task_id} confirm 操作失败')
    else:
        log.info(f'✅ {task_id} confirm {action} → {result_state[0]}')
    agent_id = _infer_agent_id_from_runtime()
    _append_audit(task_id, agent_id, f'confirm_{action}', 'PendingConfirm', result_state[0], reason)
    _append_runtime_event('confirm_applied' if not rejected[0] else 'state_rejected', task_id, agent_id, {
        'oldState': 'PendingConfirm',
        'newState': result_state[0] or '',
        'action': action,
        'reason': reason,
    }, confidence='high' if not rejected[0] else 'low')


def cmd_progress(task_id, now_text, todos_pipe='', tokens=0, cost=0.0, elapsed=0):
    """🔥 实时进展汇报 — Agent 主动调用，不改变状态，只更新 now + todos

    now_text: 当前正在做什么的一句话描述（必填）
    todos_pipe: 可选，用 | 分隔的 todo 列表，格式：
        "已完成的事项✅|正在做的事项🔄|计划做的事项"
        - 以 ✅ 结尾 → completed
        - 以 🔄 结尾 → in-progress
        - 其他 → not-started
    tokens: 可选，本次消耗的 token 数
    cost: 可选，本次成本（美元）
    elapsed: 可选，本次耗时（秒）
    """
    clean = _sanitize_remark(now_text)
    # 解析 todos_pipe
    parsed_todos = None
    if todos_pipe:
        new_todos = []
        for i, item in enumerate(todos_pipe.split('|'), 1):
            item = item.strip()
            if not item:
                continue
            if item.endswith('✅'):
                status = 'completed'
                title = item[:-1].strip()
            elif item.endswith('🔄'):
                status = 'in-progress'
                title = item[:-1].strip()
            else:
                status = 'not-started'
                title = item
            new_todos.append({'id': str(i), 'title': title, 'status': status})
        if new_todos:
            parsed_todos = new_todos

    # 解析资源消耗参数
    try:
        tokens = int(tokens) if tokens else 0
    except (ValueError, TypeError):
        tokens = 0
    try:
        cost = float(cost) if cost else 0.0
    except (ValueError, TypeError):
        cost = 0.0
    try:
        elapsed = int(elapsed) if elapsed else 0
    except (ValueError, TypeError):
        elapsed = 0

    done_cnt = [0]
    total_cnt = [0]
    event_payload = [{}]
    def modifier(tasks):
        t = find_task(tasks, task_id)
        if not t:
            log.error(f'任务 {task_id} 不存在')
            return tasks
        t['now'] = clean
        if parsed_todos is not None:
            t['todos'] = parsed_todos
        # 多 Agent 并行进展日志
        at = now_iso()
        agent_id = _infer_agent_id_from_runtime(t)
        agent_label = _AGENT_LABELS.get(agent_id, agent_id)
        log_todos = parsed_todos if parsed_todos is not None else t.get('todos', [])
        log_entry = {
            'at': at, 'agent': agent_id, 'agentLabel': agent_label,
            'text': clean, 'todos': log_todos,
            'state': t.get('state', ''), 'org': t.get('org', ''),
        }
        # 资源消耗（可选字段，有值才写入）
        if tokens > 0:
            log_entry['tokens'] = tokens
        if cost > 0:
            log_entry['cost'] = cost
        if elapsed > 0:
            log_entry['elapsed'] = elapsed
        t.setdefault('progress_log', []).append(log_entry)
        event_payload[0] = dict(log_entry)
        _touch_scheduler_progress(t, at, agent_id)
        # 限制 progress_log 大小，防止无限增长
        if len(t['progress_log']) > MAX_PROGRESS_LOG:
            t['progress_log'] = t['progress_log'][-MAX_PROGRESS_LOG:]
        t['updatedAt'] = at
        done_cnt[0] = sum(1 for td in t.get('todos', []) if td.get('status') == 'completed')
        total_cnt[0] = len(t.get('todos', []))
        return tasks
    atomic_json_update(TASKS_FILE, modifier, [])
    _trigger_refresh()
    res_info = ''
    if tokens or cost or elapsed:
        res_info = f' [res: {tokens}tok/${cost:.4f}/{elapsed}s]'
    log.info(f'📡 {task_id} 进展: {clean[:40]}... [{done_cnt[0]}/{total_cnt[0]}]{res_info}')
    agent_id = _infer_agent_id_from_runtime()
    _append_audit(task_id, agent_id, 'progress', None, None, clean)
    _append_runtime_event('progress_reported', task_id, event_payload[0].get('agent') or agent_id, event_payload[0], at=event_payload[0].get('at'))

def cmd_todo(task_id, todo_id, title, status='not-started', detail=''):
    """添加或更新子任务 todo（原子操作）

    status: not-started / in-progress / completed
    detail: 可选，该子任务的详细产出/说明（Markdown 格式）

    约束：同一时刻最多只有 1 个 in-progress 状态的 todo。
    """
    # 校验 status 值
    if status not in ('not-started', 'in-progress', 'completed'):
        status = 'not-started'
    result_info = [0, 0]
    rejected = [False]
    ready_to_close = [False]
    todo_event = [{}]
    def modifier(tasks):
        t = find_task(tasks, task_id)
        if not t:
            log.error(f'任务 {task_id} 不存在')
            return tasks
        if 'todos' not in t:
            t['todos'] = []

        # 单一 in-progress 约束
        if status == 'in-progress':
            existing_ip = [td for td in t['todos']
                           if td.get('status') == 'in-progress' and str(td.get('id')) != str(todo_id)]
            if existing_ip:
                log.warning(
                    f'⚠️ todo #{existing_ip[0]["id"]} 正在执行中，'
                    f'请先完成或取消后再开始 #{todo_id}'
                )
                rejected[0] = True
                todo_event[0] = {
                    'todoId': todo_id,
                    'status': status,
                    'title': title,
                    'reason': 'single in-progress constraint',
                }
                return tasks

        existing = next((td for td in t['todos'] if str(td.get('id')) == str(todo_id)), None)
        previous_status = existing.get('status') if existing else ''
        if existing:
            existing['status'] = status
            if title:
                existing['title'] = title
            if detail:
                existing['detail'] = detail
        else:
            item = {'id': todo_id, 'title': title, 'status': status}
            if detail:
                item['detail'] = detail
            t['todos'].append(item)
        todo_event[0] = {
            'todoId': todo_id,
            'status': status,
            'previousStatus': previous_status,
            'title': title,
            'detail': detail,
            'completed': result_info[0],
            'total': result_info[1],
        }
        at = now_iso()
        t['updatedAt'] = at
        _touch_scheduler_progress(t, at, _infer_agent_id_from_runtime(t))
        result_info[0] = sum(1 for td in t['todos'] if td.get('status') == 'completed')
        result_info[1] = len(t['todos'])
        todo_event[0]['completed'] = result_info[0]
        todo_event[0]['total'] = result_info[1]
        # 所有 todo 完成 → 标记 ready_to_close
        if result_info[1] > 0 and result_info[0] == result_info[1]:
            t['ready_to_close'] = True
            ready_to_close[0] = True
        return tasks
    atomic_json_update(TASKS_FILE, modifier, [])
    _trigger_refresh()
    if rejected[0]:
        log.info(f'❌ {task_id} todo #{todo_id} → in-progress 被拒（已有进行中的 todo）')
        agent_id = _infer_agent_id_from_runtime()
        _append_audit(task_id, agent_id, 'todo_rejected', todo_id, 'in-progress', 'single in-progress constraint')
        _append_runtime_event('todo_rejected', task_id, agent_id, todo_event[0], confidence='high')
        return
    log.info(f'✅ {task_id} todo [{result_info[0]}/{result_info[1]}]: {todo_id} → {status}')
    if ready_to_close[0]:
        log.info(f'🎯 {task_id} 所有子任务完成，ready_to_close=true')
    agent_id = _infer_agent_id_from_runtime()
    _append_audit(task_id, agent_id, 'todo', todo_id, status, title)
    _append_runtime_event('todo_updated', task_id, agent_id, todo_event[0])


# ── 三级记忆系统 ──

MEMORY_DIR = _BASE / 'data' / 'agent_memory'
TASK_MEMORY_DIR = _BASE / 'data' / 'task_memory'
SHARED_MEMORY_FILE = _BASE / 'data' / 'shared_memory.json'
MAX_AGENT_MEMORIES = 200


def cmd_memory(agent_id, mem_type, content, source_task='', tags=''):
    """写入 Agent 永久记忆。

    mem_type: feedback | experience | preference
    tags: 逗号分隔的相关性标签
    """
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    mem_file = MEMORY_DIR / f'{agent_id}.json'

    tag_list = [t.strip() for t in tags.split(',') if t.strip()] if tags else []
    entry = {
        'id': f'mem_{now_iso().replace(":", "").replace("-", "")[:15]}',
        'type': mem_type if mem_type in ('feedback', 'experience', 'preference') else 'experience',
        'content': content,
        'source_task': source_task,
        'created_at': now_iso(),
        'relevance_tags': tag_list,
        'pinned': False,
    }

    def modifier(data):
        if not data:
            data = {'agent_id': agent_id, 'memories': [], 'stats': {'tasks_handled': 0}}
        memories = data.get('memories', [])
        memories.append(entry)
        # FIFO 淘汰（pinned 除外）
        if len(memories) > MAX_AGENT_MEMORIES:
            unpinned = [m for m in memories if not m.get('pinned')]
            pinned = [m for m in memories if m.get('pinned')]
            # 淘汰最旧的 unpinned experience 类记忆
            unpinned.sort(key=lambda m: (m.get('type') == 'feedback', m.get('created_at', '')))
            memories = pinned + unpinned[-(MAX_AGENT_MEMORIES - len(pinned)):]
        data['memories'] = memories
        return data

    atomic_json_update(mem_file, modifier, {})
    log.info(f'🧠 {agent_id} 记忆写入: [{mem_type}] {content[:40]}...')
    _append_audit(source_task or 'system', agent_id, 'memory', None, mem_type, content)


def cmd_task_memo(task_id, agent_id, decisions, warnings=''):
    """写入任务上下文记忆（跨 Agent 传递决策链）。

    decisions: 逗号分隔的关键决策
    warnings: 逗号分隔的风险提示
    """
    TASK_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    memo_file = TASK_MEMORY_DIR / f'{task_id}.json'

    decision_list = [d.strip() for d in decisions.split(',') if d.strip()]
    warning_list = [w.strip() for w in warnings.split(',') if w.strip()] if warnings else []

    # 从 tasks_source.json 获取当前状态
    tasks = atomic_json_read(TASKS_FILE, [])
    task = next((t for t in tasks if t.get('id') == task_id), None)
    phase = task.get('state', '') if task else ''

    chain_entry = {
        'agent': agent_id,
        'phase': phase,
        'key_decisions': decision_list,
        'warnings': warning_list,
        'at': now_iso(),
    }

    def modifier(data):
        if not data:
            data = {'task_id': task_id, 'context_chain': []}
        data.setdefault('context_chain', []).append(chain_entry)
        return data

    atomic_json_update(memo_file, modifier, {})
    log.info(f'📝 {task_id} 任务记忆: {agent_id} → {len(decision_list)} 决策')
    _append_audit(task_id, agent_id, 'task_memo', None, None, f'{len(decision_list)} decisions')
    _append_runtime_event('task_memo_recorded', task_id, agent_id, {
        'decisions': decision_list,
        'warnings': warning_list,
        'phase': phase,
        'summary': f'{len(decision_list)} decisions',
    })


def cmd_shared_memo(content, added_by):
    """写入全局共享记忆（所有 Agent 可读的规则）。"""
    entry = {
        'content': content,
        'added_by': added_by,
        'at': now_iso(),
    }

    def modifier(data):
        if not data:
            data = {'rules': []}
        data.setdefault('rules', []).append(entry)
        return data

    atomic_json_update(SHARED_MEMORY_FILE, modifier, {})
    log.info(f'🌐 全局记忆写入: {content[:40]}... (by {added_by})')
    _append_audit('system', added_by, 'shared_memo', None, None, content)
    _append_runtime_event('shared_memo_recorded', 'system', added_by, {
        'summary': content,
        'addedBy': added_by,
    })


# ── 子 Agent 无状态委派 ──

MAX_DELEGATION_DEPTH = 3


def _short_uuid():
    """生成短 UUID 后缀。"""
    import uuid as _uuid
    return _uuid.uuid4().hex[:8]


def cmd_delegate(task_id, from_agent, to_agent, instruction, return_spec=''):
    """创建委派子任务，由目标 Agent 独立执行。

    防死锁：记录 delegation_depth 和 delegation_path，超过 3 层或循环委派时拒绝。
    """
    # 检查父任务，获取委派链信息
    tasks = atomic_json_read(TASKS_FILE, [])
    parent = next((t for t in tasks if t.get('id') == task_id), None)
    if not parent:
        log.error(f'父任务 {task_id} 不存在')
        return

    # 计算委派深度和路径
    parent_delegation = parent.get('delegation', {})
    depth = parent_delegation.get('delegation_depth', 0) + 1 if parent_delegation else 1
    path = parent_delegation.get('delegation_path', [from_agent]) if parent_delegation else [from_agent]
    path = list(path) + [to_agent]

    # 防死锁检查
    if depth > MAX_DELEGATION_DEPTH:
        log.error(f'❌ 委派深度超限 ({depth} > {MAX_DELEGATION_DEPTH})，拒绝委派')
        _append_audit(task_id, from_agent, 'delegate_rejected', None, None, f'depth={depth} exceeds limit')
        _append_runtime_event('agent_message_failed', task_id, from_agent, {
            'from': from_agent,
            'to': to_agent,
            'error': f'delegation depth {depth} exceeds limit',
        }, confidence='high')
        return
    if to_agent in path[:-1]:
        log.error(f'❌ 检测到循环委派 ({" → ".join(path)})，拒绝')
        _append_audit(task_id, from_agent, 'delegate_rejected', None, None, f'circular: {" → ".join(path)}')
        _append_runtime_event('agent_message_failed', task_id, from_agent, {
            'from': from_agent,
            'to': to_agent,
            'error': f'circular delegation: {" -> ".join(path)}',
        }, confidence='high')
        return

    sub_task_id = f'{task_id}-sub-{_short_uuid()}'
    org = STATE_ORG_MAP.get('Doing', to_agent)

    def modifier(tasks):
        sub_task = {
            'id': sub_task_id,
            'parent_task': task_id,
            'type': 'delegation',
            'title': f'[委派] {instruction[:40]}',
            'state': 'Doing',
            'org': org,
            'official': from_agent,
            'now': instruction[:60],
            'delegation': {
                'from': from_agent,
                'to': to_agent,
                'instruction': instruction,
                'return_spec': return_spec,
                'created_at': now_iso(),
                'timeout_minutes': 30,
                'delegation_bypass': True,
                'review_required': True,
                'delegation_depth': depth,
                'delegation_path': path,
            },
            'flow_log': [{'at': now_iso(), 'from': from_agent, 'to': to_agent, 'remark': f'委派: {instruction[:40]}'}],
            'todos': [],
            'updatedAt': now_iso(),
        }
        tasks.insert(0, sub_task)
        return tasks

    atomic_json_update(TASKS_FILE, modifier, [])
    _trigger_refresh()
    log.info(f'📋 委派 {sub_task_id}: {from_agent} → {to_agent} (depth={depth})')
    _append_audit(task_id, from_agent, 'delegate', to_agent, sub_task_id, instruction)
    _append_runtime_event('agent_message_sent', task_id, to_agent, {
        'from': from_agent,
        'to': to_agent,
        'message': instruction,
        'summary': f'委派子任务 {sub_task_id}',
        'subTaskId': sub_task_id,
        'returnSpec': return_spec,
        'delegationDepth': depth,
        'delegationPath': path,
    })


def cmd_delegate_result(sub_task_id, result_json):
    """提交委派子任务结果，回写到父任务的 task_memory。"""
    tasks = atomic_json_read(TASKS_FILE, [])
    sub = next((t for t in tasks if t.get('id') == sub_task_id), None)
    if not sub:
        log.error(f'子任务 {sub_task_id} 不存在')
        return
    parent_id = sub.get('parent_task', '')
    delegation = sub.get('delegation', {})
    from_agent = delegation.get('from', '')
    to_agent = delegation.get('to', '')

    # 标记子任务完成
    def modifier(tasks):
        t = find_task(tasks, sub_task_id)
        if t:
            t['state'] = 'Done'
            t['now'] = f'委派结果已提交'
            t['updatedAt'] = now_iso()
            t['delegation_result'] = result_json
        return tasks
    atomic_json_update(TASKS_FILE, modifier, [])

    # 写入父任务的 task_memory
    if parent_id:
        TASK_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        memo_file = TASK_MEMORY_DIR / f'{parent_id}.json'
        chain_entry = {
            'agent': to_agent,
            'phase': 'delegation_result',
            'key_decisions': [f'委派结果: {result_json[:200]}'],
            'warnings': [],
            'at': now_iso(),
            'delegation_from': sub_task_id,
        }
        def memo_modifier(data):
            if not data:
                data = {'task_id': parent_id, 'context_chain': []}
            data.setdefault('context_chain', []).append(chain_entry)
            return data
        atomic_json_update(memo_file, memo_modifier, {})

    _trigger_refresh()
    log.info(f'✅ 委派结果 {sub_task_id} → 父任务 {parent_id}')
    _append_audit(parent_id, to_agent, 'delegate_result', sub_task_id, None, result_json[:100])
    _append_runtime_event('agent_message_done', parent_id, to_agent, {
        'from': to_agent,
        'to': from_agent,
        'summary': result_json[:300],
        'subTaskId': sub_task_id,
    })

_CMD_MIN_ARGS = {
    'show': 2,
    'create': 6, 'state': 3, 'flow': 5, 'done': 2, 'block': 3, 'confirm': 3,
    'todo': 4, 'progress': 3,
    'memory': 4, 'task-memo': 4, 'shared-memo': 3,
    'delegate': 5, 'delegate-result': 3,
}

if __name__ == '__main__':
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)
    cmd = args[0]
    if cmd in _CMD_MIN_ARGS and len(args) < _CMD_MIN_ARGS[cmd]:
        print(f'错误："{cmd}" 命令至少需要 {_CMD_MIN_ARGS[cmd]} 个参数，实际 {len(args)} 个')
        print(__doc__)
        sys.exit(1)
    # 越权检测：推断当前 Agent 身份，校验是否有权执行该命令
    _check_permission(_infer_agent_id_from_runtime(), cmd)
    if cmd == 'show':
        cmd_show(args[1])
    elif cmd == 'create':
        cmd_create(args[1], args[2], args[3], args[4], args[5], args[6] if len(args)>6 else None)
    elif cmd == 'state':
        cmd_state(args[1], args[2], args[3] if len(args)>3 else None)
    elif cmd == 'flow':
        cmd_flow(args[1], args[2], args[3], args[4])
    elif cmd == 'done':
        cmd_done(args[1], args[2] if len(args)>2 else '', args[3] if len(args)>3 else '')
    elif cmd == 'block':
        cmd_block(args[1], args[2])
    elif cmd == 'todo':
        # 解析可选 --detail 参数
        todo_pos = []
        todo_detail = ''
        ti = 1
        while ti < len(args):
            if args[ti] == '--detail' and ti + 1 < len(args):
                todo_detail = args[ti + 1]; ti += 2
            else:
                todo_pos.append(args[ti]); ti += 1
        cmd_todo(
            todo_pos[0] if len(todo_pos) > 0 else '',
            todo_pos[1] if len(todo_pos) > 1 else '',
            todo_pos[2] if len(todo_pos) > 2 else '',
            todo_pos[3] if len(todo_pos) > 3 else 'not-started',
            detail=todo_detail,
        )
    elif cmd == 'progress':
        # 解析可选 --tokens/--cost/--elapsed 参数
        pos_args = []
        kw = {}
        i = 1
        while i < len(args):
            if args[i] == '--tokens' and i + 1 < len(args):
                kw['tokens'] = args[i + 1]; i += 2
            elif args[i] == '--cost' and i + 1 < len(args):
                kw['cost'] = args[i + 1]; i += 2
            elif args[i] == '--elapsed' and i + 1 < len(args):
                kw['elapsed'] = args[i + 1]; i += 2
            else:
                pos_args.append(args[i]); i += 1
        cmd_progress(
            pos_args[0] if len(pos_args) > 0 else '',
            pos_args[1] if len(pos_args) > 1 else '',
            pos_args[2] if len(pos_args) > 2 else '',
            tokens=kw.get('tokens', 0),
            cost=kw.get('cost', 0.0),
            elapsed=kw.get('elapsed', 0),
        )
    elif cmd == 'memory':
        cmd_memory(args[1], args[2], args[3],
                   args[4] if len(args) > 4 else '',
                   args[5] if len(args) > 5 else '')
    elif cmd == 'task-memo':
        cmd_task_memo(args[1], args[2], args[3],
                      args[4] if len(args) > 4 else '')
    elif cmd == 'shared-memo':
        cmd_shared_memo(args[1], args[2])
    elif cmd == 'confirm':
        cmd_confirm(args[1], args[2], args[3] if len(args) > 3 else '')
    elif cmd == 'delegate':
        cmd_delegate(args[1], args[2], args[3], args[4],
                     args[5] if len(args) > 5 else '')
    elif cmd == 'delegate-result':
        cmd_delegate_result(args[1], args[2])
    else:
        print(__doc__)
        sys.exit(1)
