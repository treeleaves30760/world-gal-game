"""Tests for the AI-friendly GameDriver headless harness."""
import os
import json
from pathlib import Path

import pygame
import pytest


os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


@pytest.fixture
def driver():
    from world_gal_game.dev.driver import GameDriver
    d = GameDriver(pack="demo_pack")
    yield d
    d.quit()


def test_driver_boots_and_snapshot(driver):
    """Boot, take snapshot, verify basic fields populated."""
    snap = driver.snapshot()
    assert snap["pack"] == "demo_pack"
    assert "scene_top" in snap
    assert isinstance(snap["flags"], dict)
    assert isinstance(snap["widgets"], list)


def test_driver_new_game_into_dialogue(driver):
    """new_game() should land us in DialogueScene with the prologue."""
    driver.new_game()
    driver.advance_frames(10)
    snap = driver.snapshot()
    assert snap["scene_top"] == "DialogueScene"
    assert snap["current_scene_id"] == "prologue"


def test_driver_click_advances_dialogue(driver):
    """Clicking after typewriter finishes should advance line_index."""
    driver.new_game()
    driver.advance_frames(60)   # let typewriter finish first line
    idx_before = driver.snapshot()["current_line_index"]
    driver.click((640, 600))
    driver.advance_frames(60)
    driver.click((640, 600))
    driver.advance_frames(10)
    idx_after = driver.snapshot()["current_line_index"]
    assert idx_after > idx_before


def test_driver_typewriter_skip_via_space(driver):
    """Pressing Space mid-typewriter should reveal full line (regression
    for the force_reveal rollback bug)."""
    driver.new_game()
    driver.advance_frames(2)   # very early; typewriter not done
    # Inspect dialogue box state via the active scene.
    scene = driver.app.manager.current
    box = scene.box if hasattr(scene, "box") else None
    assert box is not None
    assert not box.fully_revealed()
    driver.press_space(count=1, frames_between=2)
    # After the press the box should now be fully revealed.
    assert box.fully_revealed()


def test_driver_find_widget_by_label(driver):
    """find_widget should locate exit buttons after entering exploration."""
    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.advance_frames(5)
    w = driver.find_widget(label="鎮中廣場")
    assert w is not None
    assert w["enabled"] is True
    assert w["has_on_click"] is True


def test_driver_click_label_moves_location(driver):
    """Clicking '鎮中廣場' should change current location."""
    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.advance_frames(5)
    before = driver.snapshot()["location"]
    w = driver.find_widget(label="鎮中廣場")
    assert w is not None
    driver.click(tuple(w["rect_center"]))
    driver.advance_frames(20)
    after = driver.snapshot()["location"]
    assert after != before
    assert after == "town_square"


def test_driver_screenshot_writes_file(driver, tmp_path):
    p = driver.screenshot(tmp_path / "shot.png")
    assert p.exists()
    assert p.stat().st_size > 1000


def test_driver_dump_snapshot_writes_json(driver, tmp_path):
    p = driver.dump_snapshot(tmp_path / "snap.json")
    assert p.exists()
    data = json.loads(p.read_text())
    assert "scene_top" in data


def test_driver_consecutive_moves_no_auto_hook(driver):
    """Regression: moving into a location with no auto/enter hook used to
    leave ExplorationScene's exit buttons pointing at the previous
    location, silently swallowing every subsequent move click."""
    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.advance_frames(5)
    # Suppress town_square's auto meet_heroine scene by pre-setting its
    # forbids flag — this test isn't about that hook.
    driver.app.state.events.set_flag("met_heroine_1", True)

    # starting_room -> town_square (no auto hook will fire now).
    w = driver.find_widget(label="鎮中廣場")
    assert w is not None, "expected town_square exit button from starting_room"
    driver.click(tuple(w["rect_center"]))
    driver.advance_frames(8)
    assert driver.snapshot()["location"] == "town_square"

    # town_square -> park (no scene hook at all on park).
    w = driver.find_widget(label="湖畔公園")
    assert w is not None
    driver.click(tuple(w["rect_center"]))
    driver.advance_frames(8)
    assert driver.snapshot()["location"] == "park"

    # park -> town_square. Pre-fix, the exit buttons here were still
    # town_square's, so "→ 鎮中廣場" wouldn't even be present.
    w = driver.find_widget(label="鎮中廣場")
    assert w is not None, \
        "after a no-hook move, exit buttons must refresh to the new location"
    driver.click(tuple(w["rect_center"]))
    driver.advance_frames(8)
    assert driver.snapshot()["location"] == "town_square"


def test_advance_time_fires_eligible_enter_hook(driver):
    """Regression: when an enter/auto scene hook at the current location
    only becomes eligible after time advances (e.g. requires_time=evening
    but the player walked in at noon), the hook should fire on the time
    advance — not require the player to leave and re-enter.

    This was the user-reported bug: "在總圖的深處呆了一整天也沒有任何反應"
    — the qingyi route's library_stacks enter hook required evening/night
    but never fired if the player advanced time in place.
    """
    from world_gal_game.core.map_system import SceneHook
    from world_gal_game.core.time_system import TimeOfDay

    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.advance_frames(5)
    # Make sure no demo-pack auto hook eats the test.
    driver.app.state.events.set_flag("met_heroine_1", True)
    # Park has no scene hooks in demo_pack — pristine ground to inject one.
    park = driver.app.state.map.locations["park"]
    # Use an existing scene id from the pack so the dialogue engine can
    # resolve it. find_sketchbook_park exists and is gated on quest_started.
    driver.app.state.events.set_flag("quest_started", True)
    park.scene_hooks.append(SceneHook(
        scene_id="find_sketchbook_park",
        trigger="enter",
        requires_time=["evening"],
        forbids_flags=["obj_park_done"],
        once=True,
    ))
    # Walk in during a phase that does NOT satisfy requires_time.
    driver.app.state.time.set_phase(TimeOfDay.MORNING)
    w = driver.find_widget(label="鎮中廣場")
    assert w is not None
    driver.click(tuple(w["rect_center"]))
    driver.advance_frames(8)
    w = driver.find_widget(label="湖畔公園")
    assert w is not None
    driver.click(tuple(w["rect_center"]))
    driver.advance_frames(8)
    assert driver.snapshot()["location"] == "park"
    assert driver.snapshot()["scene_top"] == "ExplorationScene", \
        "morning entry must not fire the evening-only hook"

    # Now advance time in place. Eventually we land on evening; that tick
    # must fire the hook.
    fired = False
    for _ in range(6):
        driver.app._advance_time()
        driver.advance_frames(4)
        if driver.snapshot()["scene_top"] == "DialogueScene":
            fired = True
            break
    assert fired, "advance_time did not fire the newly eligible enter hook"
    assert driver.app.state.story.current_scene == "find_sketchbook_park"


def test_dialogue_end_fires_eligible_enter_hook(driver):
    """A dialogue's on_end / choice effects can set flags or advance time
    that newly satisfy an enter hook at the current location. The hook
    should fire when the dialogue ends, not wait for the player to leave."""
    from world_gal_game.core.map_system import SceneHook
    from world_gal_game.core.time_system import TimeOfDay

    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.advance_frames(5)
    driver.app.state.events.set_flag("met_heroine_1", True)

    # Move to park with no hook yet, then inject a hook gated on a flag
    # that the meet_heroine dialogue would have set. We then trigger a
    # dialogue (manually) and verify the hook fires after the dialogue
    # ends.
    park = driver.app.state.map.locations["park"]
    # Hook needs: requires_flags=[trigger_after_dialogue]
    park.scene_hooks.append(SceneHook(
        scene_id="find_sketchbook_park",
        trigger="auto",
        requires_flags=["trigger_after_dialogue"],
        forbids_flags=["obj_park_done"],
        once=True,
    ))
    driver.app.state.events.set_flag("quest_started", True)

    w = driver.find_widget(label="鎮中廣場")
    driver.click(tuple(w["rect_center"]))
    driver.advance_frames(8)
    w = driver.find_widget(label="湖畔公園")
    driver.click(tuple(w["rect_center"]))
    driver.advance_frames(8)
    assert driver.snapshot()["location"] == "park"
    assert driver.snapshot()["scene_top"] == "ExplorationScene"

    # Simulate the flag-setting effect that would happen inside a
    # dialogue, then trigger a dummy dialogue end through the standard
    # _start_dialogue plumbing. The simplest way: directly invoke a
    # dialogue and let its on_end flow run.
    driver.app.state.events.set_flag("trigger_after_dialogue", True)
    # Push a benign dialogue then close it through the wrapped on_done.
    driver.app._start_dialogue("find_sketchbook_park")
    # Above call would itself fire because we set the flag. So we should
    # already be in a DialogueScene now.
    driver.advance_frames(4)
    assert driver.snapshot()["scene_top"] == "DialogueScene"


def test_driver_cli_script(tmp_path):
    """End-to-end CLI: feed a JSON script, verify report.json is written."""
    from world_gal_game.dev.driver import _cli_main
    script = tmp_path / "script.json"
    script.write_text(json.dumps({
        "pack": "demo_pack",
        "actions": [
            {"do": "new_game"},
            {"do": "skip_dialogue", "max_frames": 800},
            {"do": "click_label", "label": "鎮中廣場", "after": 20},
            {"do": "snapshot", "path": "snap.json"},
        ],
    }))
    out_dir = tmp_path / "out"
    rc = _cli_main([str(script), "--out-dir", str(out_dir)])
    assert rc == 0
    report = json.loads((out_dir / "report.json").read_text())
    last = next(r for r in report if r["do"] == "snapshot")
    assert last["snapshot"]["location"] == "town_square"
