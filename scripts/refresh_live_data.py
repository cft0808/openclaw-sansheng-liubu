#!/usr/bin/env python3
import json
import pathlib
import datetime
import logging
from file_lock import atomic_json_write, atomic_json_read
from utils import read_json

log = logging.getLogger('refresh')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s', datefmt='%H:%M:%S')

BASE = pathlib.Path(__file__).parent.parent
DATA = BASE / 'data'


def output_meta(path):
    p = pathlib.Path(path)
    if not p.exists():
        return {"exists": False, "lastModified": None}
    ts = datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
    return {"exists": True, "lastModified": ts}


def clean_text(value):
    if not isinstance(value, str):
        return value
    t = value.replace('\ufeff', '').replace('\x00', '')
    # 常见残留乱码
    t = t.replace('锟斤拷', '�').replace('宸叉帴鏃', '已接旨')
    t = ''.join(ch for ch in t if ch >= ' ' or ch in '\n\t')
    return t.strip()


def clean_obj(obj):
    if isinstance(obj, dict):
        return {k: clean_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean_obj(v) for v in obj]
    if isinstance(obj, str):
        return clean_text(obj)
    return obj


def main():
    officials_data = read_json(DATA / 'officials_stats.json', {})
    officials = officials_data.get('officials', []) if isinstance(officials_data, dict) else officials_data
    tasks = atomic_json_read(DATA / 'tasks_source.json', [])
    if not tasks:
        tasks = read_json(DATA / 'tasks.json', [])

    sync_status = read_json(DATA / 'sync_status.json', {})

    org_map = {}
    for o in officials:
        label = o.get('label', o.get('name', ''))
        if label:
            org_map[label] = label

    now_ts = datetime.datetime.now(datetime.timezone.utc)
    for t in tasks:
        t['org'] = t.get('org') or org_map.get(t.get('official', ''), '')
        t['outputMeta'] = output_meta(t.get('output', ''))

        if t.get('state') in ('Doing', 'Assigned', 'Review'):
            updated_raw = t.get('updatedAt') or t.get('sourceMeta', {}).get('updatedAt')
            age_sec = None
            if updated_raw:
                try:
                    if isinstance(updated_raw, (int, float)):
                        updated_dt = datetime.datetime.fromtimestamp(updated_raw / 1000, tz=datetime.timezone.utc)
                    else:
                        updated_dt = datetime.datetime.fromisoformat(str(updated_raw).replace('Z', '+00:00'))
                    age_sec = (now_ts - updated_dt).total_seconds()
                except Exception:
                    pass
            if age_sec is None:
                t['heartbeat'] = {'status': 'unknown', 'label': '? 未知', 'ageSec': None}
            elif age_sec < 180:
                t['heartbeat'] = {'status': 'active', 'label': f'🟢 活跃 {int(age_sec//60)}分钟前', 'ageSec': int(age_sec)}
            elif age_sec < 600:
                t['heartbeat'] = {'status': 'warn', 'label': f'🟡 停滞 {int(age_sec//60)}分钟前', 'ageSec': int(age_sec)}
            else:
                t['heartbeat'] = {'status': 'stalled', 'label': f'🔴 停滞 {int(age_sec//60)}分钟', 'ageSec': int(age_sec)}
        else:
            t['heartbeat'] = None

    today_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')

    def _is_today_done(task):
        if task.get('state') != 'Done':
            return False
        ua = task.get('updatedAt', '')
        if isinstance(ua, str) and ua[:10] == today_str:
            return True
        lm = task.get('outputMeta', {}).get('lastModified', '')
        if isinstance(lm, str) and lm[:10] == today_str:
            return True
        return False

    today_done = sum(1 for t in tasks if _is_today_done(t))
    total_done = sum(1 for t in tasks if t.get('state') == 'Done')
    in_progress = sum(1 for t in tasks if t.get('state') in ['Doing', 'Review', 'Next', 'Blocked'])
    blocked = sum(1 for t in tasks if t.get('state') == 'Blocked')

    history = []
    for t in tasks:
        if t.get('state') == 'Done':
            lm = t.get('outputMeta', {}).get('lastModified')
            history.append({
                'at': lm or '未知',
                'official': t.get('official'),
                'task': t.get('title'),
                'out': t.get('output'),
                'qa': '通过' if t.get('outputMeta', {}).get('exists') else '未生成成果'
            })

    payload = {
        'generatedAt': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'taskSource': 'tasks_source.json' if (DATA / 'tasks_source.json').exists() else 'tasks.json',
        'officials': officials,
        'tasks': clean_obj(tasks),
        'history': clean_obj(history),
        'metrics': {
            'officialCount': len(officials),
            'todayDone': today_done,
            'totalDone': total_done,
            'inProgress': in_progress,
            'blocked': blocked
        },
        'syncStatus': clean_obj(sync_status),
        'health': {
            'syncOk': bool(sync_status.get('ok', False)),
            'syncLatencyMs': sync_status.get('durationMs'),
            'missingFieldCount': len(sync_status.get('missingFields', {})),
        }
    }

    atomic_json_write(DATA / 'live_status.json', payload)
    log.info(f'updated live_status.json ({len(tasks)} tasks)')


if __name__ == '__main__':
    main()
