#!/usr/bin/env python3
"""Generate project-local OpenCode agent config from 三省六部 SOUL files."""
import argparse
import datetime
import json
import os
import pathlib
import shutil
import tempfile


BASE = pathlib.Path(__file__).resolve().parent.parent
DATA = BASE / 'data'
OPENCODE_CFG = BASE / 'opencode.json'
OPENCODE_DIR = BASE / '.opencode'
PROMPTS_DIR = BASE / '.opencode' / 'prompts'

AGENT_ORDER = [
    'taizi', 'zhongshu', 'menxia', 'shangshu',
    'hubu', 'libu', 'bingbu', 'xingbu', 'gongbu', 'libu_hr',
    'zaochao', 'qintianjian',
]

ID_LABEL = {
    'taizi': {'label': '太子', 'role': '太子', 'duty': '飞书消息分拣与回奏', 'emoji': '🤴'},
    'zhongshu': {'label': '中书省', 'role': '中书令', 'duty': '起草任务令与优先级', 'emoji': '📜'},
    'menxia': {'label': '门下省', 'role': '侍中', 'duty': '审议与退回机制', 'emoji': '🔍'},
    'shangshu': {'label': '尚书省', 'role': '尚书令', 'duty': '派单与升级裁决', 'emoji': '📮'},
    'libu': {'label': '礼部', 'role': '礼部尚书', 'duty': '文档/UI/对外沟通', 'emoji': '📝'},
    'hubu': {'label': '户部', 'role': '户部尚书', 'duty': '数据/资源/成本', 'emoji': '💰'},
    'bingbu': {'label': '兵部', 'role': '兵部尚书', 'duty': '工程实现与架构设计', 'emoji': '⚔️'},
    'xingbu': {'label': '刑部', 'role': '刑部尚书', 'duty': '质量保障与合规审计', 'emoji': '⚖️'},
    'gongbu': {'label': '工部', 'role': '工部尚书', 'duty': '基础设施与部署运维', 'emoji': '🔧'},
    'libu_hr': {'label': '吏部', 'role': '吏部尚书', 'duty': '人事/培训/Agent管理', 'emoji': '👔'},
    'zaochao': {'label': '钦天监', 'role': '朝报官', 'duty': '每日新闻采集与简报', 'emoji': '📰'},
    'qintianjian': {'label': '钦天监', 'role': '监正', 'duty': '数据分析与趋势预测', 'emoji': '🔭'},
}

GROUPS = {
    'taizi': 'sansheng',
    'zhongshu': 'sansheng',
    'menxia': 'sansheng',
    'shangshu': 'sansheng',
    'hubu': 'liubu',
    'libu': 'liubu',
    'bingbu': 'liubu',
    'xingbu': 'liubu',
    'gongbu': 'liubu',
    'libu_hr': 'liubu',
    'qintianjian': 'liubu',
}

ALLOW_AGENTS = {
    'taizi': ['zhongshu'],
    'zhongshu': ['menxia', 'shangshu'],
    'menxia': ['zhongshu'],
    'shangshu': ['hubu', 'libu', 'bingbu', 'xingbu', 'gongbu', 'libu_hr', 'qintianjian'],
}

DEFAULT_PERMISSION = {
    'read': 'allow',
    'edit': 'allow',
    'glob': 'allow',
    'grep': 'allow',
    'list': 'allow',
    'bash': 'allow',
    'task': 'allow',
    'todowrite': 'allow',
    'webfetch': 'allow',
    'websearch': 'allow',
    'external_directory': 'ask',
}


def read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding='utf-8') if path.exists() else ''


def read_json(path: pathlib.Path, fallback):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return fallback


def atomic_write_json(path: pathlib.Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name, suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write('\n')
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def cleanup_unmanaged_opencode_artifacts() -> None:
    """Keep only project-owned OpenCode prompt files under .opencode/."""
    for path in (
        OPENCODE_DIR / 'node_modules',
        OPENCODE_DIR / 'package.json',
        OPENCODE_DIR / 'package-lock.json',
        OPENCODE_DIR / '.gitignore',
    ):
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()


def build_prompt(agent_id: str) -> str:
    meta = ID_LABEL[agent_id]
    parts = [
        '# OpenCode 运行时适配\n'
        f'你正在 OpenCode 中担任「{meta["label"]} / {meta["role"]}」。\n\n'
        f'- 项目根目录：`{BASE}`。\n'
        '- 默认工作目录就是项目根目录；执行命令前确认在该目录下。\n'
        '- 看板状态必须通过 `python3 scripts/kanban_update.py ...` 更新，不要直接改 JSON。\n'
        '- 需要调用其他官员时，使用 OpenCode 的 subagent/task 能力，目标 agent id 使用本项目定义的英文 id。\n'
        '- 不要调用 `openclaw`、`sessions_send` 或写入 `~/.openclaw`；本项目当前由 OpenCode 接管。\n'
        '- 如原 SOUL 中出现 `__REPO_DIR__`，它指向上面的项目根目录。\n',
        read_text(BASE / 'agents' / 'GLOBAL.md'),
    ]
    group = GROUPS.get(agent_id)
    if group:
        parts.append(read_text(BASE / 'agents' / 'groups' / f'{group}.md'))
    parts.append(read_text(BASE / 'agents' / agent_id / 'SOUL.md'))
    return '\n\n---\n\n'.join(p.strip() for p in parts if p.strip()).replace('__REPO_DIR__', str(BASE))


def sync_prompts() -> list[str]:
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for agent_id in AGENT_ORDER:
        prompt_path = PROMPTS_DIR / f'{agent_id}.md'
        prompt_path.write_text(build_prompt(agent_id) + '\n', encoding='utf-8')
        written.append(str(prompt_path.relative_to(BASE)))
    return written


def sync_opencode_config() -> dict:
    cfg = read_json(OPENCODE_CFG, {})
    if not isinstance(cfg, dict):
        cfg = {}
    cfg['$schema'] = cfg.get('$schema') or 'https://opencode.ai/config.json'

    server = cfg.get('server') if isinstance(cfg.get('server'), dict) else {}
    server.setdefault('hostname', '127.0.0.1')
    server.setdefault('port', 4096)
    cors = list(server.get('cors') or [])
    for origin in ('http://127.0.0.1:7891', 'http://localhost:7891'):
        if origin not in cors:
            cors.append(origin)
    server['cors'] = cors
    cfg['server'] = server

    cfg.setdefault('default_agent', 'taizi')
    agents = cfg.get('agent') if isinstance(cfg.get('agent'), dict) else {}
    for agent_id in AGENT_ORDER:
        meta = ID_LABEL[agent_id]
        existing = agents.get(agent_id) if isinstance(agents.get(agent_id), dict) else {}
        entry = dict(existing)
        entry['description'] = entry.get('description') or f'{meta["label"]}：{meta["duty"]}'
        entry['mode'] = entry.get('mode') or 'all'
        entry['prompt'] = f'{{file:./.opencode/prompts/{agent_id}.md}}'
        entry.setdefault('temperature', 0.1)
        entry.setdefault('steps', 60)
        entry.setdefault('permission', DEFAULT_PERMISSION)
        agents[agent_id] = entry
    cfg['agent'] = agents

    atomic_write_json(OPENCODE_CFG, cfg)
    return cfg


def sync_dashboard_config(cfg: dict) -> None:
    existing = read_json(DATA / 'agent_config.json', {})
    if not isinstance(existing, dict):
        existing = {}
    default_model = (
        os.environ.get('OPENCODE_MODEL')
        or cfg.get('model')
        or existing.get('defaultModel')
        or 'configured-in-opencode'
    )
    known_models = existing.get('knownModels') or []
    agents = []
    cfg_agents = cfg.get('agent') or {}
    for agent_id in AGENT_ORDER:
        meta = ID_LABEL[agent_id]
        entry = cfg_agents.get(agent_id) or {}
        agents.append({
            'id': agent_id,
            'label': meta['label'],
            'role': meta['role'],
            'duty': meta['duty'],
            'emoji': meta['emoji'],
            'model': entry.get('model') or default_model,
            'defaultModel': default_model,
            'workspace': str(BASE),
            'prompt': str((PROMPTS_DIR / f'{agent_id}.md').relative_to(BASE)),
            'skills': [],
            'allowAgents': ALLOW_AGENTS.get(agent_id, []),
            'runtime': 'opencode',
        })
    payload = {
        'generatedAt': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'runtime': 'opencode',
        'defaultModel': default_model,
        'knownModels': known_models,
        'dispatchChannel': existing.get('dispatchChannel') or '',
        'agents': agents,
    }
    atomic_write_json(DATA / 'agent_config.json', payload)


def main() -> None:
    parser = argparse.ArgumentParser(description='Sync 三省六部 agents to project-local OpenCode config.')
    parser.add_argument('--no-dashboard-config', action='store_true', help='Skip data/agent_config.json update')
    args = parser.parse_args()

    cleanup_unmanaged_opencode_artifacts()
    prompts = sync_prompts()
    cfg = sync_opencode_config()
    if not args.no_dashboard_config:
        sync_dashboard_config(cfg)
    print(f'OpenCode config synced: {len(prompts)} prompts, {len(cfg.get("agent") or {})} agents')


if __name__ == '__main__':
    main()
