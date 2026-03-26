import json
import importlib.util
from pathlib import Path


def _load_sync_agent_config():
    root = Path(__file__).resolve().parents[1]
    script_path = root / "scripts" / "sync_agent_config.py"
    spec = importlib.util.spec_from_file_location("sync_agent_config", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sync_agent_config_accepts_allow_agents_key(tmp_path, monkeypatch):
    sync_agent_config = _load_sync_agent_config()

    cfg = {
        "agents": {
            "defaults": {"model": "openai/gpt-4o"},
            "list": [
                {
                    "id": "taizi",
                    "workspace": str(tmp_path / "ws-taizi"),
                    "allowAgents": ["zhongshu"]
                }
            ]
        }
    }

    cfg_path = tmp_path / "openclaw.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False))

    monkeypatch.setattr(sync_agent_config, "OPENCLAW_CFG", cfg_path)
    monkeypatch.setattr(sync_agent_config, "DATA", tmp_path / "data")

    sync_agent_config.main()

    out = json.loads((tmp_path / "data" / "agent_config.json").read_text())
    taizi = next(agent for agent in out["agents"] if agent["id"] == "taizi")
    assert taizi["allowAgents"] == ["zhongshu"]
