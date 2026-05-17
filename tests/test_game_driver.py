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
    d = GameDriver(pack="tsing_hua_strange_tales")
    yield d
    d.quit()


def test_driver_boots_and_snapshot(driver):
    """Boot, take snapshot, verify basic fields populated."""
    snap = driver.snapshot()
    assert snap["pack"] == "tsing_hua_strange_tales"
    assert "scene_top" in snap
    assert isinstance(snap["flags"], dict)
    assert isinstance(snap["widgets"], list)


def test_driver_new_game_into_dialogue(driver):
    """new_game() should land us in DialogueScene with the prologue."""
    driver.new_game()
    driver.advance_frames(10)
    snap = driver.snapshot()
    assert snap["scene_top"] == "DialogueScene"
    assert snap["current_scene_id"] == "prologue_arrival"


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
    w = driver.find_widget(label="校門口")
    assert w is not None
    assert w["enabled"] is True
    assert w["has_on_click"] is True


def test_driver_click_label_moves_location(driver):
    """Clicking '校門口' should change current location."""
    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.advance_frames(5)
    before = driver.snapshot()["location"]
    w = driver.find_widget(label="校門口")
    assert w is not None
    driver.click(tuple(w["rect_center"]))
    driver.advance_frames(20)
    after = driver.snapshot()["location"]
    assert after != before
    assert after == "main_gate"


def test_driver_screenshot_writes_file(driver, tmp_path):
    p = driver.screenshot(tmp_path / "shot.png")
    assert p.exists()
    assert p.stat().st_size > 1000


def test_driver_dump_snapshot_writes_json(driver, tmp_path):
    p = driver.dump_snapshot(tmp_path / "snap.json")
    assert p.exists()
    data = json.loads(p.read_text())
    assert "scene_top" in data


def test_driver_cli_script(tmp_path):
    """End-to-end CLI: feed a JSON script, verify report.json is written."""
    from world_gal_game.dev.driver import _cli_main
    script = tmp_path / "script.json"
    script.write_text(json.dumps({
        "pack": "tsing_hua_strange_tales",
        "actions": [
            {"do": "new_game"},
            {"do": "skip_dialogue", "max_frames": 800},
            {"do": "click_label", "label": "校門口", "after": 20},
            {"do": "snapshot", "path": "snap.json"},
        ],
    }))
    out_dir = tmp_path / "out"
    rc = _cli_main([str(script), "--out-dir", str(out_dir)])
    assert rc == 0
    report = json.loads((out_dir / "report.json").read_text())
    last = next(r for r in report if r["do"] == "snapshot")
    assert last["snapshot"]["location"] == "main_gate"
