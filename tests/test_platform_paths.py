"""Tests for writable_root() platform handling + app_data_name (B0).

The web (Emscripten) branch and the app_data_name plumbing support Steam
Auto-Cloud path-pinning and IndexedDB-backed saves.
"""
from __future__ import annotations

from pathlib import Path

from world_gal_game import config as cfg
from world_gal_game.config import EngineConfig, writable_root


def test_dev_writable_root_is_project_dir():
    # Non-frozen, non-web -> project root (unchanged legacy behavior).
    root = writable_root("Whatever")
    assert root.exists()
    # app_name is ignored in dev mode (writes next to the project).
    assert root == Path(cfg.__file__).resolve().parent.parent


def test_emscripten_branch_uses_data_mount(monkeypatch):
    created = {}

    def fake_mkdir(self, *a, **k):
        created["path"] = self
        return None

    monkeypatch.setattr(cfg.sys, "platform", "emscripten")
    monkeypatch.setattr(Path, "mkdir", fake_mkdir)
    root = writable_root("MyGame")
    assert root == Path("/data/MyGame")
    assert created["path"] == Path("/data/MyGame")


def test_save_dir_uses_app_data_name():
    config = EngineConfig(app_data_name="BrandedGame")
    # In dev the base is the project dir; the save subdir is appended.
    # With no pack_id set, the layout stays flat (legacy / backward compatible).
    assert config.save_dir().name == config.save_subdir
    assert config.app_data_name == "BrandedGame"


def test_save_dir_namespaced_by_pack_id():
    """Each pack gets its own save namespace: ``saves/<pack_id>``."""
    config = EngineConfig(pack_id="demo_pack")
    sd = config.save_dir()
    assert sd.name == "demo_pack"
    assert sd.parent.name == config.save_subdir
    # A different pack lands in a sibling directory, never colliding.
    other = EngineConfig(pack_id="ghost_pack").save_dir()
    assert other.name == "ghost_pack"
    assert other != sd


def test_save_dir_pack_id_is_sanitized():
    """A path-like or unsafe pack id collapses to one safe component, so the
    save namespace can't escape the saves/ root via separators / traversal."""
    from world_gal_game.config import _sanitize_pack_id
    assert _sanitize_pack_id("../../etc/passwd") == "passwd"
    assert _sanitize_pack_id("../Tsing-Hua-Strange-Tales") == "Tsing-Hua-Strange-Tales"
    assert _sanitize_pack_id("my pack!") == "my_pack"
    assert _sanitize_pack_id("") == ""
    # The resolved dir is always a direct child of saves/ (single component).
    sd = EngineConfig(pack_id="../../escape").save_dir()
    assert sd.name == "escape"
    assert sd.parent.name == "saves"
