"""Tests for EngineConfig settings persistence (WP-F1).

``save_to_disk()`` / ``load_from_disk()`` round-trip only the user-tunable
settings to ``settings.json``. Loading must be robust: a missing file is a
no-op, a corrupt file is ignored (defaults kept), and unknown keys are
skipped — none of these may raise.

In dev mode ``writable_root()`` ignores ``app_data_name`` and returns the
project root, so we monkeypatch the module-level ``writable_root`` to point
at ``tmp_path``. ``settings_path`` / ``save_to_disk`` / ``load_from_disk``
all resolve through that one function, so this keeps the repo clean.
"""
from __future__ import annotations

import json

import pytest

from world_gal_game import config as cfg
from world_gal_game.config import EngineConfig


@pytest.fixture
def tmp_settings(monkeypatch, tmp_path):
    """Redirect writable_root() to a temp dir for the duration of a test."""
    monkeypatch.setattr(cfg, "writable_root", lambda *_a, **_k: tmp_path)
    return tmp_path


def test_settings_path_under_writable_root(tmp_settings):
    config = EngineConfig()
    assert config.settings_path() == tmp_settings / "settings.json"


def test_round_trip_preserves_values(tmp_settings):
    config = EngineConfig()
    # Touch a representative subset across types: float, bool, int, str, dict.
    config.bgm_volume = 0.25
    config.voice_volume = 0.5
    config.text_speed = 80.0
    config.auto_play_delay = 1.5
    config.touch_mode = True
    config.auto_play_speed = 1.5
    config.auto_play_wait_voice = False
    config.skip_unread_only = False
    config.nvl_mode = True
    config.per_character_voice_volume = {"yuki": 0.8, "rin": 0.3}
    config.autosave_enabled = False
    config.autosave_slot_count = 5
    config.quicksave_slot = "qs_main"
    config.save_to_disk()

    assert config.settings_path().exists()

    fresh = EngineConfig()
    fresh.load_from_disk()
    assert fresh.bgm_volume == 0.25
    assert fresh.voice_volume == 0.5
    assert fresh.text_speed == 80.0
    assert fresh.auto_play_delay == 1.5
    assert fresh.touch_mode is True
    assert fresh.auto_play_speed == 1.5
    assert fresh.auto_play_wait_voice is False
    assert fresh.skip_unread_only is False
    assert fresh.nvl_mode is True
    assert fresh.per_character_voice_volume == {"yuki": 0.8, "rin": 0.3}
    assert fresh.autosave_enabled is False
    assert fresh.autosave_slot_count == 5
    assert fresh.quicksave_slot == "qs_main"


def test_only_user_settings_are_serialized(tmp_settings):
    config = EngineConfig()
    config.save_to_disk()
    data = json.loads(config.settings_path().read_text(encoding="utf-8"))
    # User-tunable fields are present.
    assert "bgm_volume" in data
    assert "nvl_mode" in data
    assert "per_character_voice_volume" in data
    # Pack/path/dev fields are intentionally excluded.
    for excluded in ("default_pack", "save_subdir", "dev_mode",
                     "screen_size", "title", "app_data_name",
                     "extra_pack_dirs", "game_pack_dir"):
        assert excluded not in data


def test_missing_file_keeps_defaults(tmp_settings):
    # No settings.json written -> load is a silent no-op, defaults intact.
    assert not (tmp_settings / "settings.json").exists()
    config = EngineConfig()
    config.load_from_disk()
    defaults = EngineConfig()
    assert config.nvl_mode == defaults.nvl_mode is False
    assert config.auto_play_speed == defaults.auto_play_speed == 1.0
    assert config.quicksave_slot == defaults.quicksave_slot == "quicksave"


def test_corrupt_file_keeps_defaults(tmp_settings):
    # Unparseable JSON must be ignored without raising.
    (tmp_settings / "settings.json").write_text("{not valid json,,,",
                                                encoding="utf-8")
    config = EngineConfig()
    config.load_from_disk()  # must not raise
    assert config.auto_play_speed == 1.0
    assert config.nvl_mode is False
    assert config.autosave_slot_count == 3


def test_non_object_json_keeps_defaults(tmp_settings):
    # Valid JSON but not an object (e.g. a list) -> ignored, no crash.
    (tmp_settings / "settings.json").write_text("[1, 2, 3]", encoding="utf-8")
    config = EngineConfig()
    config.load_from_disk()
    assert config.text_speed == EngineConfig().text_speed


def test_unknown_key_is_ignored(tmp_settings):
    payload = {
        "bgm_volume": 0.42,
        "totally_unknown_setting": "ignore me",
        "default_pack": "should_not_be_applied",
    }
    (tmp_settings / "settings.json").write_text(json.dumps(payload),
                                                encoding="utf-8")
    config = EngineConfig()
    config.load_from_disk()
    assert config.bgm_volume == 0.42
    # Unknown key did not become an attribute.
    assert not hasattr(config, "totally_unknown_setting")
    # default_pack is not a persisted setting, so it stays at its default
    # even though the file contained it.
    assert config.default_pack == EngineConfig().default_pack


def test_partial_file_only_overwrites_present_keys(tmp_settings):
    # A file with a subset of keys leaves the rest at their defaults.
    (tmp_settings / "settings.json").write_text(
        json.dumps({"nvl_mode": True}), encoding="utf-8")
    config = EngineConfig()
    config.text_speed = 12.0  # locally tweaked, absent from file
    config.load_from_disk()
    assert config.nvl_mode is True            # came from file
    assert config.auto_play_speed == 1.0      # default, untouched
    # Key absent from file is left as-is on the instance (not reset).
    assert config.text_speed == 12.0
