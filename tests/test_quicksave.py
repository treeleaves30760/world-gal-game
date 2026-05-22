"""Quicksave / quickload round-trip (WP-F3).

Boots the real app headlessly via GameDriver, redirects the save dir to a
temp path, then verifies F6=quicksave / F9=quickload behaviour:

- quicksave writes ``config.quicksave_slot`` to disk;
- quickload restores the saved flags + location and drops any mutations
  made after the save;
- the transient ``__``-prefixed meta bridges (plugin manager, autosave
  config) survive the state swap.
"""
import os
import types
from pathlib import Path

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


@pytest.fixture
def driver(tmp_path):
    from world_gal_game.dev.driver import GameDriver
    d = GameDriver(pack="demo_pack")
    # Redirect saves to a temp dir so the test never touches the repo.
    save_dir = tmp_path / "saves"
    save_dir.mkdir(parents=True, exist_ok=True)
    d.app.config.save_dir = types.MethodType(lambda self, _p=save_dir: _p,
                                             d.app.config)
    # Point the live autosave bridge at the same temp dir.
    bridge = d.app.state.meta.get("__autosave_config__")
    if bridge is not None:
        bridge["save_dir"] = save_dir
    d._save_dir = save_dir
    yield d
    d.quit()


def test_quicksave_writes_slot(driver):
    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.advance_frames(5)
    driver.app._quicksave()
    slot = driver.app.config.quicksave_slot
    assert (driver._save_dir / f"{slot}.json").exists()


def test_quicksave_then_quickload_round_trips_state(driver):
    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.advance_frames(5)

    loc_before = driver.app.state.map.current_location_id
    driver.app.state.events.set_flag("qs_marker", True)

    driver.app._quicksave()

    # Mutate after the save: flip the marker and add a brand-new flag.
    driver.app.state.events.set_flag("qs_marker", False)
    driver.app.state.events.set_flag("post_save_only", True)

    driver.app._quickload()
    driver.advance_frames(3)

    # Saved value restored; post-save mutation discarded.
    assert driver.app.state.events.flags.get("qs_marker") is True
    assert "post_save_only" not in driver.app.state.events.flags
    assert driver.app.state.map.current_location_id == loc_before


def test_quickload_preserves_transient_bridges(driver):
    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.advance_frames(5)
    driver.app._quicksave()
    driver.app._quickload()
    driver.advance_frames(2)
    # The plugin manager + autosave bridge are filtered out at save time
    # and must be re-attached after the state swap.
    assert "__plugin_manager__" in driver.app.state.meta
    assert "__autosave_config__" in driver.app.state.meta


def test_quickload_missing_slot_is_noop(driver):
    """Quickloading with no quicksave on disk must not crash or wipe state."""
    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.advance_frames(5)
    loc = driver.app.state.map.current_location_id
    # No _quicksave() has run, so the slot is absent.
    driver.app._quickload()
    driver.advance_frames(2)
    assert driver.app.state.map.current_location_id == loc


def test_save_menu_thumbnail_wired_and_listed(driver):
    """``_open_save_menu`` now passes a screen grabber, so a save through
    SaveScene produces a thumbnail PNG that ``list_saves()`` reports."""
    from world_gal_game.core.save_manager import SaveManager

    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.advance_frames(5)
    driver.app._open_save_menu()
    driver.advance_frames(2)
    scene = driver.app.manager.current
    assert type(scene).__name__ == "SaveScene"
    assert scene._get_screen is not None
    # The first row in save mode is the "new save" item.
    scene._on_action(scene._items[0])
    driver.advance_frames(2)
    rows = SaveManager(driver._save_dir).list_saves()
    assert rows, "expected at least one save written"
    assert any(r["thumbnail_path"] is not None for r in rows)


def test_f6_f9_keys_bound_in_main_loop(driver, monkeypatch):
    """The F6 / F9 key bindings should invoke quicksave / quickload."""
    import pygame
    from world_gal_game.ui.input import InputState

    calls = {"save": 0, "load": 0}
    monkeypatch.setattr(driver.app, "_quicksave",
                        lambda: calls.__setitem__("save", calls["save"] + 1))
    monkeypatch.setattr(driver.app, "_quickload",
                        lambda: calls.__setitem__("load", calls["load"] + 1))

    # Feed a synthetic F6 then F9 keydown through a real _step call by
    # pushing them onto the pygame event queue the loop reads.
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F6))
    driver.app._step(driver.frame_dt)
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F9))
    driver.app._step(driver.frame_dt)

    assert calls["save"] == 1
    assert calls["load"] == 1
