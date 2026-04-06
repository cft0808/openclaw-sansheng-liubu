import json
import importlib.util
from pathlib import Path


def _load_module(rel_path, name):
    root = Path(__file__).resolve().parents[1]
    script_path = root / rel_path
    spec = importlib.util.spec_from_file_location(name, script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sync_agent_config_discovers_sandbox_only_agents(tmp_path, monkeypatch):
    sync_agent_config = _load_module('scripts/sync_agent_config.py', 'sync_agent_config_sandbox')

    home = tmp_path / 'home'
    home.mkdir()
    sandbox_root = home / '.openclaw' / 'workspaces' / 'edict'
    (sandbox_root / 'agents' / 'zhongshu' / 'skills' / 'planner').mkdir(parents=True)
    (sandbox_root / 'agents' / 'zhongshu' / 'skills' / 'planner' / 'SKILL.md').write_text(
        '---\nname: planner\ndescription: sandbox planner\n---\n\nSandbox planner\n'
    )

    cfg = {
        'agents': {
            'defaults': {'model': 'openai/gpt-4o'},
            'list': [
                {'id': 'taizi', 'workspace': str(home / '.openclaw' / 'workspace-taizi')}
            ]
        }
    }
    cfg_path = tmp_path / 'openclaw.json'
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False))

    monkeypatch.setattr(sync_agent_config.pathlib.Path, 'home', staticmethod(lambda: home))
    monkeypatch.setattr(sync_agent_config, 'OPENCLAW_CFG', cfg_path)
    monkeypatch.setattr(sync_agent_config, 'DATA', tmp_path / 'data')
    monkeypatch.setattr(sync_agent_config, 'SANDBOX_ROOT', sandbox_root)

    sync_agent_config.main()

    out = json.loads((tmp_path / 'data' / 'agent_config.json').read_text())
    zhongshu = next(agent for agent in out['agents'] if agent['id'] == 'zhongshu')
    assert zhongshu['workspace'] == str(sandbox_root / 'agents' / 'zhongshu')
    assert zhongshu['isSandboxOnly'] is True
    assert zhongshu['skills'][0]['name'] == 'planner'


def test_server_iter_task_data_dirs_includes_sandbox(monkeypatch, tmp_path):
    server = _load_module('dashboard/server.py', 'server_sandbox')

    data_dir = tmp_path / 'repo-data'
    data_dir.mkdir()
    sandbox_data = tmp_path / '.openclaw' / 'workspaces' / 'edict' / 'data'
    sandbox_data.mkdir(parents=True)
    global_ws_data = tmp_path / '.openclaw' / 'workspace-taizi' / 'data'
    global_ws_data.mkdir(parents=True)

    monkeypatch.setattr(server, 'DATA', data_dir)
    monkeypatch.setattr(server, 'OCLAW_HOME', tmp_path / '.openclaw')

    dirs = server._iter_task_data_dirs()
    assert data_dir in dirs
    assert sandbox_data in dirs
    assert global_ws_data in dirs
