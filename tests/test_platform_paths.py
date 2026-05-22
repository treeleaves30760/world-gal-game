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
    assert config.save_dir().name == config.save_subdir
    assert config.app_data_name == "BrandedGame"
