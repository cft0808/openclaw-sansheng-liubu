#!/usr/bin/env python3
import json
import pathlib
import time
import datetime
import traceback
import logging
from file_lock import atomic_json_write, atomic_json_read

log = logging.getLogger('sync_runtime')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s', datefmt='%H:%M:%S')

BASE = pathlib.Path(__file__).resolve().parent.parent
DATA = BASE / 'data'
DATA.mkdir(exist_ok=True)
SYNC_STATUS = DATA / 'sync_status.json'
SESSIONS_ROOT = pathlib.Path.home() / '.openclaw' / 'agents'


def write_status(**kwargs):
    atomic_json_write(SYNC_STATUS, kwargs)


def ms_to_str(ts_ms):
    if not ts_ms:
        return '-'
    try:
        return datetime.datetime.fromtimestamp(ts_ms / 1000).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return '-'


def state_from_session(age_ms, aborted):
    if aborted:
        return 'Blocked'
    if age_ms <= 2 * 60 * 1000:
        return 'Doing'
    if age_ms <= 60 * 60 * 1000:
        return 'Review'
    return 'Next'


def detect_official(agent_id):
    mapping = {
        'main':    ('储君', '太子'),        # legacy id for taizi
        'taizi':   ('储君', '太子'),
        'zhongshu': ('中书令', '中书省'),
        'menxia':  ('侍中', '门下省'),
        'shangshu': ('尚书令', '尚书省'),
        'hubu':    ('户部尚书', '户部'),
        'libu':    ('礼部尚书', '礼部'),
        'bingbu':  ('兵部尚书', '兵部'),
        'xingbu':  ('刑部尚书', '刑部'),
        'gongbu':  ('工部尚书', '工部'),
        'libu_hr': ('吏部尚书', '吏部'),
        'zaochao': ('钦天监', '钦天监'),
    }
    return mapping.get(agent_id, ('尚书令', '尚书省'))


def load_activity(session_file, limit=12):
    p = pathlib.Path(session_file or '')
    if not p.exists():
        return []
    rows = []
    try:
        lines = p.read_text(errors='ignore').splitlines()
    except Exception:
        return []

    # Read all valid JSON lines first
    events = []
    for ln in lines:
        try:
            item = json.loads(ln)
            events.append(item)
        except:
            continue

    # Process events to extract meaningful activity
    # We want to show what the agent is *thinking* or *doing*
    for item in reversed(events):
        msg = item.get('message') or {}
        role = msg.get('role')
        ts = item.get('timestamp') or ''

        if role == 'toolResult':
            tool = msg.get('toolName', '-')
            details = msg.get('details') or {}
            # If tool output is short, show it
            content = msg.get('content', [{'text': ''}])[0].get('text', '')
            if len(content) < 50:
                text = f"Tool '{tool}' returned: {content}"
            else:
                text = f"Tool '{tool}' finished"
            rows.append({'at': ts, 'kind': 'tool', 'text': text})

        elif role == 'assistant':
            text = ''
            for c in msg.get('content', []):
                if c.get('type') == 'text' and c.get('text'):
                    raw_text = c.get('text').strip()
                    # Clean up common prefixes
                    clean_text = raw_text.replace('[[reply_to_current]]', '').strip()
                    if clean_text:
                        text = clean_text
                    break
            if text:
                # Prioritize showing the "thought" - usually the first few sentences
                summary = text.split('\n')[0]
                if len(summary) > 200:
                    summary = summary[:200] + '...'
                rows.append({'at': ts, 'kind': 'assistant', 'text': summary})
                
        elif role == 'user':
             # Also show what user asked, can be context relevant
             text = ''
             for c in msg.get('content', []):
                if c.get('type') == 'text':
                     text = c.get('text', '')[:100]
             if text:
                 rows.append({'at': ts, 'kind': 'user', 'text': f"User: {text}..."})

        if len(rows) >= limit:
            break

    # Re-order to chronological for display if needed, but the caller usually takes the first (latest)
    return rows


def build_task(agent_id, session_key, row, now_ms):
    session_id = row.get('sessionId') or session_key
    updated_at = row.get('updatedAt') or 0
    age_ms = max(0, now_ms - updated_at) if updated_at else 99 * 24 * 3600 * 1000
    aborted = bool(row.get('abortedLastRun'))
    state = state_from_session(age_ms, aborted)

    official, org = detect_official(agent_id)
    channel = row.get('lastChannel') or (row.get('origin') or {}).get('channel') or '-'
    session_file = row.get('sessionFile', '')
    
    # 尝试从 activity 获取更有意义的当前状态描述
    latest_act = '等待指令'
    acts = load_activity(session_file, limit=5)
    
    # If the absolute latest is a tool result, look for the preceding assistant thought
    # because that explains *why* the tool was called.
    if acts:
        first_act = acts[0]
        if first_act['kind'] == 'tool' and len(acts) > 1:
            # Look for next assistant message (which is actually previous in time)
            for next_act in acts[1:]:
                if next_act['kind'] == 'assistant':
                    latest_act = f"正在执行: {next_act['text'][:80]}"
                    break
            else:
                latest_act = first_act['text'][:60]
        elif first_act['kind'] == 'assistant':
             latest_act = f"思考中: {first_act['text'][:80]}"
        else:
             latest_act = acts[0]['text'][:60]
    
    title_label = (row.get('origin') or {}).get('label') or session_key
    # 清洗会话标题：agent:xxx:cron:uuid → 定时任务, agent:xxx:subagent:uuid → 子任务
    import re
    if re.match(r'agent:\w+:cron:', title_label):
        title = f"{org}定时任务"
    elif re.match(r'agent:\w+:subagent:', title_label):
        title = f"{org}子任务"
    elif title_label == session_key or len(title_label) > 40:
        title = f"{org}会话"
    else:
        title = f"{title_label}"
    
    return {
        'id': f"OC-{agent_id}-{str(session_id)[:8]}",
        'title': title,
        'official': official,
        'org': org,
        'state': state,
        'now': latest_act,
        'eta': ms_to_str(updated_at),
        'block': '上次运行中断' if aborted else '无',
        'output': session_file,
        'flow': {
            'draft': f"agent={agent_id}",
            'review': f"updatedAt={ms_to_str(updated_at)}",
            'dispatch': f"sessionKey={session_key}",
        },
        'ac': '来自 OpenClaw runtime sessions 的实时映射',
        'activity': load_activity(session_file, limit=10),
        'sourceMeta': {
            'agentId': agent_id,
            'sessionKey': session_key,
            'sessionId': session_id,
            'updatedAt': updated_at,
            'ageMs': age_ms,
            'systemSent': bool(row.get('systemSent')),
            'abortedLastRun': aborted,
            'inputTokens': row.get('inputTokens'),
            'outputTokens': row.get('outputTokens'),
            'totalTokens': row.get('totalTokens'),
        }
    }


def main():
    start = time.time()
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    now_ms = int(time.time() * 1000)

    try:
        tasks = []
        scan_files = 0

        if SESSIONS_ROOT.exists():
            for agent_dir in sorted(SESSIONS_ROOT.iterdir()):
                if not agent_dir.is_dir():
                    continue
                agent_id = agent_dir.name
                sessions_file = agent_dir / 'sessions' / 'sessions.json'
                if not sessions_file.exists():
                    continue
                scan_files += 1

                try:
                    raw = json.loads(sessions_file.read_text())
                except Exception:
                    continue

                if not isinstance(raw, dict):
                    continue

                for session_key, row in raw.items():
                    if not isinstance(row, dict):
                        continue
                    tasks.append(build_task(agent_id, session_key, row, now_ms))

        # merge mission control tasks (最小接入)
        mc_tasks_file = DATA / 'mission_control_tasks.json'
        if mc_tasks_file.exists():
            try:
                mc_tasks = json.loads(mc_tasks_file.read_text())
                if isinstance(mc_tasks, list):
                    tasks.extend(mc_tasks)
            except Exception:
                pass

        # merge manual parallel tasks (用于军机处并行看板展示)
        manual_tasks_file = DATA / 'manual_parallel_tasks.json'
        if manual_tasks_file.exists():
            try:
                manual_tasks = json.loads(manual_tasks_file.read_text())
                if isinstance(manual_tasks, list):
                    tasks.extend(manual_tasks)
            except Exception:
                pass

        tasks.sort(key=lambda x: x.get('sourceMeta', {}).get('updatedAt', 0), reverse=True)

        # 去重（同一 id 只保留第一个=最新的）
        seen_ids = set()
        deduped = []
        for t in tasks:
            if t['id'] not in seen_ids:
                seen_ids.add(t['id'])
                deduped.append(t)
        tasks = deduped

        # ── 过滤掉非 JJC 且非活跃的系统会话，防止看板噪音 ──
        # 规则: 仅保留 24小时内更新的活跃会话，且排除 cron/subagent 等纯后台任务
        filtered_tasks = []
        one_day_ago = now_ms - 24 * 3600 * 1000
        for t in tasks:
            # 始终保留 JJC 任务（如果有的话，虽然这里主要是 OC 任务，但以防万一）
            if str(t['id']).startswith('JJC'):
                filtered_tasks.append(t)
                continue
            
            # OC 任务过滤
            updated = t.get('sourceMeta', {}).get('updatedAt', 0)
            title = t.get('title', '')
            
            # 1. 排除太旧的 (超过24小时)
            if updated < one_day_ago:
                continue
            
            # 2. 排除纯后台 cron / subagent 任务，除非它们正在报错
            if '定时任务' in title or '子任务' in title:
                # 只有当它 block 或者 error 时才显示，否则视为噪音
                if t.get('state') != 'Blocked':
                    continue

            # 3. 排除已冷却的 OC 会话，避免污染看板
            # 保留 Doing（<2min）、Review（<60min）、Blocked（报错）
            # 仅过滤掉 Next（>60min 无响应）等已结束/闲置的会话
            state = t.get('state')
            if state not in ('Doing', 'Review', 'Blocked'):
                continue

            filtered_tasks.append(t)
        
        tasks = filtered_tasks
        
        # ── 保留已有的 JJC-* 旨意任务（不覆盖皇上下旨记录）──
        # JJC 任务的 now 字段由 Agent 自己通过 kanban_update.py progress 命令主动上报，
        # 不再从会话日志中被动抓取。这里只做合并，不做 activity 映射。
        existing_tasks_file = DATA / 'tasks_source.json'
        if existing_tasks_file.exists():
            try:
                existing = json.loads(existing_tasks_file.read_text())
                jjc_existing = [t for t in existing if str(t.get('id', '')).startswith('JJC')]
                
                # 去掉 tasks 里已有的 JJC（以防重复），再把旨意放到最前面
                tasks = [t for t in tasks if not str(t.get('id', '')).startswith('JJC')]
                tasks = jjc_existing + tasks
            except Exception as e:
                log.error(f'merge existing JJC tasks failed: {e}')
                pass

        atomic_json_write(DATA / 'tasks_source.json', tasks)

        duration_ms = int((time.time() - start) * 1000)
        write_status(
            ok=True,
            lastSyncAt=now,
            durationMs=duration_ms,
            source='openclaw_runtime_sessions',
            recordCount=len(tasks),
            scannedSessionFiles=scan_files,
            missingFields={},
            error=None,
        )
        log.info(f'synced {len(tasks)} tasks from openclaw runtime in {duration_ms}ms')

    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        write_status(
            ok=False,
            lastSyncAt=now,
            durationMs=duration_ms,
            source='openclaw_runtime_sessions',
            recordCount=0,
            missingFields={},
            error=f'{type(e).__name__}: {e}',
            traceback=traceback.format_exc(limit=3),
        )
        raise


if __name__ == '__main__':
    main()
