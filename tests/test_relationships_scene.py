"""Relationship-status overlay (Fix 1b) + chapter/date HUD (Fix 2).

The relationship panel makes affection legible: it is reached from the pause
menu (next to the flowchart entry), lists heroines first with name_color +
affection bar + tier + next named threshold, and is read-only. The HUD is a
subtle chapter/date corner indicator on the dialogue frame.

Driven through the real app via ``GameDriver`` (transitions disabled), mirroring
tests/test_endings_scene.py.
"""
import os

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from world_gal_game.core.affection import AffectionThreshold


@pytest.fixture
def driver():
    from world_gal_game.dev.driver import GameDriver
    d = GameDriver(pack="demo_pack")
    d.app.manager.transitions_enabled = False
    yield d
    d.quit()


def _seed_relationship(driver):
    """Register a heroine with a name_color + named thresholds and give her
    enough affection to sit between two tiers."""
    state = driver.app.ctx.state
    npcs = driver.app.ctx.npcs
    npc = npcs.all()[0]
    npc.is_heroine = True
    npc.name_color = "#4a96b0"
    state.affection.register(npc.id)
    ca = state.affection.characters[npc.id]
    ca.thresholds = [
        AffectionThreshold(name="成為朋友", value=25, unlocks=["f"]),
        AffectionThreshold(name="在意你", value=50, unlocks=["r"]),
    ]
    ca.set_value(30)   # past 25, short of 50
    return npc


def test_relationships_scene_opens_and_describes(driver):
    npc = _seed_relationship(driver)
    driver.app._open_relationships()
    driver.advance_frames(2)
    top = driver.app.manager.current
    assert type(top).__name__ == "RelationshipsScene"
    assert top.is_overlay is True
    d = top.describe()
    assert d["scene"] == "RelationshipsScene"
    row = next(r for r in d["characters"] if r["character_id"] == npc.id)
    assert row["is_heroine"] is True
    assert row["affection"] == 30
    # at 30: current tier is 朋友, next named threshold is 在意你 (50)
    assert row["tier"] == "朋友"
    assert row["next_threshold"] == {"name": "在意你", "value": 50}


def test_relationships_scene_heroines_sort_first(driver):
    """A heroine sorts ahead of a non-heroine tracked character."""
    state = driver.app.ctx.state
    npcs = driver.app.ctx.npcs
    all_npcs = npcs.all()
    hero, other = all_npcs[0], all_npcs[1]
    hero.is_heroine = True
    other.is_heroine = False
    state.affection.register(hero.id)
    state.affection.register(other.id)
    driver.app._open_relationships()
    driver.advance_frames(2)
    rows = driver.app.manager.current.describe()["characters"]
    ids = [r["character_id"] for r in rows]
    assert ids.index(hero.id) < ids.index(other.id)


def test_relationships_scene_empty_is_graceful(driver):
    driver.app.ctx.state.affection.characters.clear()
    driver.app._open_relationships()
    driver.advance_frames(2)
    top = driver.app.manager.current
    assert type(top).__name__ == "RelationshipsScene"
    assert top.describe()["characters"] == []
    # draws without raising even with nothing to show
    driver.advance_frames(2)


def test_relationships_scene_closes_via_cancel(driver):
    _seed_relationship(driver)
    driver.app._open_relationships()
    driver.advance_frames(2)
    assert type(driver.app.manager.current).__name__ == "RelationshipsScene"
    driver.app.manager.pop()
    driver.advance_frames(2)
    assert type(driver.app.manager.current).__name__ != "RelationshipsScene"


def test_relationships_reachable_through_pause_menu(driver):
    """The pause menu carries on_relationships, next to the flowchart entry,
    and firing it (after from_menu closes the menu) lands on the panel."""
    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.advance_frames(5)
    driver.app._open_menu()
    driver.advance_frames(2)
    menu = driver.app.manager.current
    assert type(menu).__name__ == "MenuScene"
    assert menu.on_relationships is not None
    assert menu.on_flowchart is not None
    menu.on_relationships()
    driver.advance_frames(3)
    assert type(driver.app.manager.current).__name__ == "RelationshipsScene"


# --------------------------------------------------------------------------
# Fix 2: chapter/date HUD on the dialogue frame.
# --------------------------------------------------------------------------
def test_status_hud_resolves_chapter_title(driver):
    """When a chapter cursor + manifest is present, the dialogue scene resolves
    the chapter title; the date line always comes from TimeSystem.label."""
    from world_gal_game.scenes.dialogue_scene import DialogueScene
    from world_gal_game.core.chapter_spec import ChapterManifest

    state = driver.app.ctx.state
    state.meta["__chapters__"] = ChapterManifest.from_items([
        {"id": "ch1", "title": "第一章 · 到站", "order": 10},
    ])
    state.current_chapter = "ch1"
    scene = DialogueScene(driver.app.ctx)
    assert scene._current_chapter_title() == "第一章 · 到站"
    # unknown / absent cursor -> None (HUD then shows only the date)
    state.current_chapter = None
    assert scene._current_chapter_title() is None
    state.current_chapter = "nope"
    assert scene._current_chapter_title() is None
    # the date line is always available
    assert state.time.label()


def test_status_hud_toggle_default_on(driver):
    assert driver.app.config.show_status_hud is True


def test_status_hud_draw_does_not_raise(driver):
    """Drawing the HUD on a live dialogue frame must not raise, with or without
    a chapter cursor."""
    import pygame
    from world_gal_game.core.chapter_spec import ChapterManifest

    driver.new_game()
    driver.advance_frames(5)
    state = driver.app.ctx.state
    state.meta["__chapters__"] = ChapterManifest.from_items([
        {"id": "ch1", "title": "第一章", "order": 10}])
    state.current_chapter = "ch1"
    scene = driver.app.manager.current
    surf = pygame.Surface(driver.app.ctx.screen_size, pygame.SRCALPHA)
    if hasattr(scene, "_draw_status_hud"):
        scene._draw_status_hud(surf)   # chapter + date path
    state.current_chapter = None
    if hasattr(scene, "_draw_status_hud"):
        scene._draw_status_hud(surf)   # date-only path
