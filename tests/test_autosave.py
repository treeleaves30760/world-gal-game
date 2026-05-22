"""Autosave bundled plugin (WP-F3).

The ``autosave`` plugin (``world_gal_game/plugins_user/autosave/``) hooks
``dialogue.choice_made`` (and ``time.advance``) and writes a rotating
autosave to ``autosave_1..N``. It reads the live config + save dir from a
private ``state.meta["__autosave_config__"]`` bridge that the app parks at
boot (the hook PluginContext from content_loader carries no config).

These tests construct a GameState + PluginManager directly (no display)
and fire the hook event through ``fire_event`` to assert:

- enabled  -> a slot is written, rotating across N slots;
- disabled -> nothing is written;
- a save failure never propagates (engine-safety contract);
- the private bridge + rotation cursor are stripped from a normal save.
"""
import os
from pathlib import Path

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


def _build(save_dir: Path, *, enabled: bool, count: int = 3):
    """Build a state with the autosave plugin active + its app bridge."""
    from world_gal_game.core.game_state import GameState
    from world_gal_game.config import EngineConfig
    from world_gal_game.plugins.manager import PluginManager
    from world_gal_game.plugins.context import PluginContext

    state = GameState()
    cfg = EngineConfig()
    cfg.autosave_enabled = enabled
    cfg.autosave_slot_count = count
    state.meta["__autosave_config__"] = {
        "config": cfg, "save_dir": save_dir, "get_screen": None,
    }
    pm = PluginManager()
    pm.discover()
    ctx = PluginContext(state=state, meta={}, manager=pm)
    pm.activate(context=ctx)
    state.meta["__plugin_manager__"] = pm
    return state, cfg, pm


def _fire_choice(state, n: int = 1):
    from world_gal_game.plugins import fire_event
    from world_gal_game.plugins.context import HookEvent
    for i in range(n):
        fire_event(state, HookEvent.DIALOGUE_CHOICE_MADE,
                   scene_id="scene", choice_id=f"c{i}")


def test_autosave_plugin_loads():
    from world_gal_game.plugins.manager import PluginManager
    pm = PluginManager()
    pm.discover()
    pm.activate()
    loaded = {r.id: r for r in pm.loaded()}
    assert "autosave" in loaded
    assert "dialogue.choice_made" in loaded["autosave"].hook_events


def test_autosave_writes_slot_when_enabled(tmp_path):
    state, _cfg, _pm = _build(tmp_path, enabled=True)
    _fire_choice(state, 1)
    assert (tmp_path / "autosave_1.json").exists()


def test_autosave_skips_when_disabled(tmp_path):
    state, _cfg, _pm = _build(tmp_path, enabled=False)
    _fire_choice(state, 3)
    assert list(tmp_path.glob("*.json")) == []


def test_autosave_rotates_across_slots(tmp_path):
    state, _cfg, _pm = _build(tmp_path, enabled=True, count=3)
    # Four choices over three slots: 1, 2, 3, then wraps back to 1.
    _fire_choice(state, 4)
    names = sorted(p.name for p in tmp_path.glob("*.json"))
    assert names == ["autosave_1.json", "autosave_2.json", "autosave_3.json"]


def test_autosave_no_bridge_is_noop(tmp_path):
    """Without the app bridge the hook must do nothing (bare headless)."""
    from world_gal_game.core.game_state import GameState
    from world_gal_game.plugins.manager import PluginManager
    from world_gal_game.plugins.context import PluginContext

    state = GameState()   # no __autosave_config__ bridge
    pm = PluginManager()
    pm.discover()
    ctx = PluginContext(state=state, meta={}, manager=pm)
    pm.activate(context=ctx)
    state.meta["__plugin_manager__"] = pm
    _fire_choice(state, 2)
    assert list(tmp_path.glob("*.json")) == []


def test_autosave_failure_never_propagates(tmp_path, monkeypatch):
    """A save error inside the hook must be swallowed, not raised."""
    state, _cfg, _pm = _build(tmp_path, enabled=True)

    import world_gal_game.core.save_manager as sm_mod

    def _boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(sm_mod.SaveManager, "save", _boom)
    # Must not raise even though the underlying save explodes.
    _fire_choice(state, 1)
    # And nothing got written.
    assert list(tmp_path.glob("*.json")) == []


def test_autosave_private_keys_stripped_from_normal_save(tmp_path):
    """The bridge + rotation cursor are ``__``-keys → filtered on save."""
    from world_gal_game.core.save_manager import SaveManager

    state, _cfg, _pm = _build(tmp_path, enabled=True)
    _fire_choice(state, 1)  # creates the rotation cursor + bridge usage

    # Serialise the way the manual save path does and persist it.
    sm = SaveManager(tmp_path)
    payload = state.model_dump(mode="json")
    sm.save("manual", payload)
    loaded = sm.load("manual")
    meta = loaded.get("meta", {})
    assert "__autosave_config__" not in meta
    assert "__plugin:autosave__" not in meta
    assert "__plugin_manager__" not in meta
