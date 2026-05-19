"""Tests for the clue (journal) system: model + tracker + integration
into apply_all + scene wiring + loader."""
from __future__ import annotations

import os
from pathlib import Path

import pytest


os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


# ---------------------------------------------------------------------------
# Clue model + tracker (no engine boot needed)
# ---------------------------------------------------------------------------


def _make_state_with_clues(specs):
    """Build a bare GameState with the given clue specs registered."""
    from world_gal_game.core.game_state import GameState
    from world_gal_game.core.clue import Clue
    from world_gal_game.core.story_graph import Condition

    state = GameState()
    for spec in specs:
        reqs = [Condition(**c) for c in spec.get("requires", [])]
        forb = [Condition(**c) for c in spec.get("forbids", [])]
        state.clues.register(Clue(
            id=spec["id"], title=spec["title"],
            text=spec.get("text", ""),
            category=spec.get("category", "其他"),
            requires=reqs, forbids=forb,
            priority=spec.get("priority", 0),
        ))
    return state


def test_clue_unlocks_when_requires_satisfied():
    state = _make_state_with_clues([
        {"id": "a", "title": "A",
         "requires": [{"kind": "flag", "target": "f1"}]},
    ])
    # Before flag: not in journal.
    assert state.clues.refresh(state) == []
    assert "a" not in state.clues.seen

    state.events.set_flag("f1", True)
    newly = state.clues.refresh(state)
    assert [c.id for c in newly] == ["a"]
    assert "a" in state.clues.seen
    assert "a" in state.clues.unread


def test_clue_resolves_when_forbids_triggers():
    state = _make_state_with_clues([
        {"id": "a", "title": "A",
         "requires": [{"kind": "flag", "target": "open"}],
         "forbids":  [{"kind": "flag", "target": "done"}]},
    ])
    state.events.set_flag("open", True)
    state.clues.refresh(state)
    entries = state.clues.journal(state)
    assert len(entries) == 1 and entries[0][1] == "active"

    state.events.set_flag("done", True)
    entries = state.clues.journal(state)
    assert entries[0][1] == "resolved", \
        "forbids flag flipping must move the clue to resolved"


def test_clue_never_appears_if_window_was_missed():
    """If a clue's requires AND forbids both hold from the start
    (e.g. loading a save past the moment), it should not enter the
    journal."""
    state = _make_state_with_clues([
        {"id": "a", "title": "A",
         "requires": [{"kind": "flag", "target": "open"}],
         "forbids":  [{"kind": "flag", "target": "done"}]},
    ])
    state.events.set_flag("open", True)
    state.events.set_flag("done", True)
    assert state.clues.refresh(state) == []
    assert "a" not in state.clues.seen


def test_clue_unread_badge_clears_on_mark_read():
    state = _make_state_with_clues([
        {"id": "a", "title": "A",
         "requires": [{"kind": "flag", "target": "f"}]},
    ])
    state.events.set_flag("f", True)
    state.clues.refresh(state)
    assert state.clues.unread_count() == 1
    state.clues.mark_read("a")
    assert state.clues.unread_count() == 0
    assert "a" in state.clues.seen  # still in journal


def test_clue_journal_sort_active_first_by_priority():
    state = _make_state_with_clues([
        {"id": "low_active", "title": "L",
         "requires": [{"kind": "flag", "target": "f"}],
         "priority": 1},
        {"id": "high_active", "title": "H",
         "requires": [{"kind": "flag", "target": "f"}],
         "priority": 100},
        {"id": "resolved", "title": "R",
         "requires": [{"kind": "flag", "target": "f"}],
         "forbids":  [{"kind": "flag", "target": "done"}],
         "priority": 999},
    ])
    state.events.set_flag("f", True)
    state.clues.refresh(state)
    state.events.set_flag("done", True)
    order = [c.id for c, _ in state.clues.journal(state)]
    # Both actives come before the resolved one, and within active the
    # higher-priority shows first.
    assert order[0] == "high_active"
    assert order[1] == "low_active"
    assert order[2] == "resolved"


# ---------------------------------------------------------------------------
# apply_all integration
# ---------------------------------------------------------------------------


def test_apply_all_unlocks_clue_and_queues_toast():
    """Setting a flag via the standard apply_all path must unlock any
    newly-eligible clue AND push a toast onto the pending queue."""
    state = _make_state_with_clues([
        {"id": "a", "title": "Hello",
         "requires": [{"kind": "flag", "target": "f1"}]},
    ])
    from world_gal_game.core.story_graph import Effect
    state.apply_all([Effect(kind="set_flag", target="f1", value=True)])
    assert "a" in state.clues.seen
    queue = state.meta.get("__pending_toasts__", [])
    assert any(kind == "clue" and key == "a" for kind, key, _ in queue), \
        f"expected a clue toast in queue, got {queue}"


# ---------------------------------------------------------------------------
# Loader integration
# ---------------------------------------------------------------------------


def test_loader_reads_clues_yaml_for_demo_pack(tmp_path):
    from world_gal_game.content_loader import load_pack

    # demo_pack ships with content/clues.yaml since this PR.
    repo_root = Path(__file__).resolve().parent.parent
    content_root = repo_root / "games" / "demo_pack" / "content"
    state, _registry, _meta = load_pack(content_root)
    assert "cl_demo_intro" in state.clues.clues
    assert "cl_demo_quest" in state.clues.clues


# ---------------------------------------------------------------------------
# Driver-level: top-bar button + J hotkey open the clue panel
# ---------------------------------------------------------------------------


@pytest.fixture
def driver():
    from world_gal_game.dev.driver import GameDriver
    d = GameDriver(pack="demo_pack")
    yield d
    d.quit()


def test_driver_has_clue_button(driver):
    """ExplorationScene must show a 線索 button on the top bar after
    boot."""
    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.advance_frames(5)
    labels = [b.get("label") for b in driver._widget_catalogue()]
    # button label includes "(J)" — match by substring
    assert any("線索" in (lbl or "") for lbl in labels), \
        f"線索 button missing; visible: {labels}"


def test_driver_j_hotkey_opens_clue_scene(driver):
    import pygame
    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.advance_frames(5)
    # Press J
    driver.press_key(pygame.K_j)
    driver.advance_frames(6)
    assert driver.snapshot()["scene_top"] == "CluesScene"
    # Escape closes
    driver.press_key(pygame.K_ESCAPE)
    driver.advance_frames(6)
    assert driver.snapshot()["scene_top"] != "CluesScene"


def test_clue_unlocked_during_play_shows_in_journal(driver):
    import pygame
    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.advance_frames(5)
    # prologue_done is set by the prologue scene → cl_demo_intro should
    # already be in the journal.
    snap = driver._widget_catalogue()
    driver.press_key(pygame.K_j)
    driver.advance_frames(6)
    state = driver.app.state
    assert "cl_demo_intro" in state.clues.seen
    # Opening the clue panel marks everything read.
    assert state.clues.unread_count() == 0
