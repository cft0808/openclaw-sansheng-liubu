"""Tests for symlink-based script sync (fix for issue #56).

Verifies that ``sync_scripts_to_workspaces()`` creates symlinks instead of
physical copies, so ``__file__`` in workspace scripts resolves back to the
project ``scripts/`` directory.
"""
import os
import pathlib
import sys
import types

import pytest

# ── Bootstrap: make scripts/ importable ──────────────────────────
SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / 'scripts'
sys.path.insert(0, str(SCRIPTS))

import sync_agent_config as sac  # noqa: E402


# ────────────────────────────────────────────────────────────────
# Helper: patch BASE, _SOUL_DEPLOY_MAP, HOME to isolate tests
# ────────────────────────────────────────────────────────────────

@pytest.fixture
def project(tmp_path, monkeypatch):
    """Set up a minimal fake project tree and home dir."""
    proj = tmp_path / 'project'
    scripts = proj / 'scripts'
    scripts.mkdir(parents=True)
    data = proj / 'data'
    data.mkdir()
    (data / 'tasks_source.json').write_text('[]')

    home = tmp_path / 'home'
    home.mkdir()

    # Create a couple of dummy scripts
    (scripts / 'kanban_update.py').write_text('# kanban\n')
    (scripts / 'refresh_live_data.py').write_text('# refresh\n')
    (scripts / '__init__.py').write_text('')  # should be skipped

    # Patch module-level state
    monkeypatch.setattr(sac, 'BASE', proj)
    monkeypatch.setattr(sac, '_SOUL_DEPLOY_MAP', {'agent-a': 'aaa'})
    monkeypatch.setattr(pathlib.Path, 'home', staticmethod(lambda: home))

    return types.SimpleNamespace(root=proj, scripts=scripts, data=data, home=home)


# ── Tests ────────────────────────────────────────────────────────

class TestSyncScriptSymlink:
    """Unit tests for the helper ``_sync_script_symlink``."""

    def test_creates_symlink(self, tmp_path):
        src = tmp_path / 'src.py'
        src.write_text('hello')
        dst = tmp_path / 'dst.py'

        created = sac._sync_script_symlink(src, dst)

        assert created is True
        assert dst.is_symlink()
        assert dst.resolve() == src.resolve()
        assert dst.read_text() == 'hello'

    def test_idempotent_when_up_to_date(self, tmp_path):
        src = tmp_path / 'src.py'
        src.write_text('hello')
        dst = tmp_path / 'dst.py'

        sac._sync_script_symlink(src, dst)
        created = sac._sync_script_symlink(src, dst)

        assert created is False  # already correct

    def test_replaces_physical_copy(self, tmp_path):
        """A pre-existing physical copy should be replaced with a symlink."""
        src = tmp_path / 'src.py'
        src.write_text('v2')
        dst = tmp_path / 'dst.py'
        dst.write_text('v1')  # physical copy, not a symlink

        created = sac._sync_script_symlink(src, dst)

        assert created is True
        assert dst.is_symlink()
        assert dst.resolve() == src.resolve()

    def test_replaces_broken_symlink(self, tmp_path):
        src = tmp_path / 'src.py'
        src.write_text('ok')
        dst = tmp_path / 'dst.py'
        os.symlink('/no/such/file', dst)  # broken link

        created = sac._sync_script_symlink(src, dst)

        assert created is True
        assert dst.is_symlink()
        assert dst.resolve() == src.resolve()


class TestSyncScriptsToWorkspaces:
    """Integration tests for the full ``sync_scripts_to_workspaces`` flow."""

    def test_creates_symlinks_in_workspace(self, project):
        sac.sync_scripts_to_workspaces()

        ws = project.home / '.openclaw' / 'workspace-aaa' / 'scripts'
        assert ws.is_dir()

        kb = ws / 'kanban_update.py'
        assert kb.is_symlink(), 'expected symlink, got physical file'
        assert kb.resolve() == (project.scripts / 'kanban_update.py').resolve()

        rf = ws / 'refresh_live_data.py'
        assert rf.is_symlink()

    def test_skips_dunder_files(self, project):
        sac.sync_scripts_to_workspaces()

        ws = project.home / '.openclaw' / 'workspace-aaa' / 'scripts'
        assert not (ws / '__init__.py').exists()

    def test_legacy_workspace_main(self, project):
        sac.sync_scripts_to_workspaces()

        ws_main = project.home / '.openclaw' / 'workspace-main' / 'scripts'
        assert ws_main.is_dir()

        kb = ws_main / 'kanban_update.py'
        assert kb.is_symlink()
        assert kb.resolve() == (project.scripts / 'kanban_update.py').resolve()

    def test_idempotent_rerun(self, project):
        sac.sync_scripts_to_workspaces()
        sac.sync_scripts_to_workspaces()  # second run should be a no-op

        ws = project.home / '.openclaw' / 'workspace-aaa' / 'scripts'
        kb = ws / 'kanban_update.py'
        assert kb.is_symlink()

    def test_replaces_old_physical_copies(self, project):
        """Simulate pre-existing physical copies (old behaviour) and verify
        they get replaced by symlinks on the next sync run."""
        ws = project.home / '.openclaw' / 'workspace-aaa' / 'scripts'
        ws.mkdir(parents=True, exist_ok=True)
        old_copy = ws / 'kanban_update.py'
        old_copy.write_text('# stale physical copy')

        sac.sync_scripts_to_workspaces()

        assert old_copy.is_symlink(), 'old physical copy should be replaced'
        assert old_copy.resolve() == (project.scripts / 'kanban_update.py').resolve()

    def test_file_resolves_to_project_root(self, project):
        """The whole point of #56: __file__ should resolve to project scripts/,
        so Path(__file__).resolve().parent.parent == project root."""
        sac.sync_scripts_to_workspaces()

        ws = project.home / '.openclaw' / 'workspace-aaa' / 'scripts'
        ws_script = ws / 'kanban_update.py'

        # Simulate what kanban_update.py does at import time
        resolved = ws_script.resolve()
        computed_base = resolved.parent.parent
        assert computed_base == project.root, (
            f'Expected {project.root}, got {computed_base}; '
            'symlink should resolve __file__ back to project root'
        )
