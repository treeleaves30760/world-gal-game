"""Endings & completion overlay (WP-C).

Covers the module-level completion helper (pure, no pygame) and the
scene itself driven through the real app via ``GameDriver``: route
grouping, locked/hidden rendering paths, empty-pack graceful handling,
and a JSON-able ``describe()``.
"""
import os

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from world_gal_game.core.endings import Ending
from world_gal_game.core.cg_gallery_spec import CGGalleryManifest
from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import Scene as StoryScene
from world_gal_game.scenes.endings_scene import compute_completion


# --------------------------------------------------------------------------
# compute_completion — pure unit tests (no pygame, no driver)
# --------------------------------------------------------------------------
def test_completion_empty_state_no_divide_by_zero():
    comp = compute_completion(GameState(), total_cgs=None)
    assert comp["scenes"] == {"done": 0, "total": 0, "pct": 0.0}
    assert comp["endings"] == {"done": 0, "total": 0, "pct": 0.0}
    assert comp["cgs"] is None
    assert comp["overall_pct"] == 0.0


def test_completion_scenes_and_endings_percentages():
    s = GameState()
    # 4 scenes in the story, 2 read.
    for i in range(4):
        s.story.add_scene(StoryScene(id=f"sc_{i}", lines=[]))
    s.read_log.mark_scene_done("sc_0")
    s.read_log.mark_scene_done("sc_1")
    # 2 endings registered, 1 unlocked.
    s.endings.register(Ending(id="e_a", title="A"))
    s.endings.register(Ending(id="e_b", title="B"))
    s.endings.unlocked["e_a"] = "2026-01-01T00:00:00"

    comp = compute_completion(s, total_cgs=None)
    assert comp["scenes"] == {"done": 2, "total": 4, "pct": 50.0}
    assert comp["endings"] == {"done": 1, "total": 2, "pct": 50.0}
    assert comp["cgs"] is None
    # Overall = mean of the two countable categories.
    assert comp["overall_pct"] == 50.0


def test_completion_includes_cgs_when_total_known():
    s = GameState()
    s.story.add_scene(StoryScene(id="only", lines=[]))
    s.read_log.mark_scene_done("only")          # 1/1 -> 100%
    s.endings.register(Ending(id="e", title="E"))  # 0/1 -> 0%
    s.cg_gallery.unlock("assets/cgs/a.png")
    s.cg_gallery.unlock("assets/cgs/b.png")        # 2/4 -> 50%

    comp = compute_completion(s, total_cgs=4)
    assert comp["cgs"] == {"done": 2, "total": 4, "pct": 50.0}
    # Mean of 100, 0, 50 = 50.
    assert comp["overall_pct"] == 50.0


def test_completion_cgs_zero_total_omitted_from_overall():
    s = GameState()
    s.story.add_scene(StoryScene(id="only", lines=[]))
    s.read_log.mark_scene_done("only")          # 1/1 -> 100%
    # total_cgs known but zero: category present, pct 0, excluded from mean.
    comp = compute_completion(s, total_cgs=0)
    assert comp["cgs"] == {"done": 0, "total": 0, "pct": 0.0}
    # Only scenes is countable -> overall == scenes pct.
    assert comp["overall_pct"] == 100.0


def test_completion_rounds_to_one_decimal():
    s = GameState()
    for i in range(3):
        s.story.add_scene(StoryScene(id=f"sc_{i}", lines=[]))
    s.read_log.mark_scene_done("sc_0")          # 1/3 -> 33.3%
    comp = compute_completion(s, total_cgs=None)
    assert comp["scenes"]["pct"] == 33.3
    assert comp["overall_pct"] == 33.3


def test_completion_ignores_retired_clear_data_ids_in_current_roster():
    from world_gal_game.core.clear_data import ClearData

    state = GameState()
    state.story.add_scene(StoryScene(id="current", lines=[]))
    state.endings.register(Ending(id="current_ending", title="Current"))
    ClearData(
        scenes_seen={"current", "retired_a", "retired_b"},
        endings_seen={"retired_ending"},
    ).apply_to_state(
        state,
        registered_endings={"current_ending"},
    )
    # Simulate a stale save key as well: completion must intersect definitions
    # rather than merely clamping an impossible percentage.
    state.endings.unlocked["retired_ending"] = ""

    comp = compute_completion(state, total_cgs=None)

    assert state.read_log.scenes == {"current", "retired_a", "retired_b"}
    assert comp["scenes"] == {"done": 1, "total": 1, "pct": 100.0}
    assert comp["endings"] == {"done": 0, "total": 1, "pct": 0.0}
    assert comp["overall_pct"] == 50.0


# --------------------------------------------------------------------------
# Scene behaviour through the real app (GameDriver)
# --------------------------------------------------------------------------
@pytest.fixture
def driver():
    from world_gal_game.dev.driver import GameDriver
    d = GameDriver(pack="demo_pack")
    yield d
    d.quit()


def _seed_endings(driver):
    """Register a spread of endings on the live game state."""
    tracker = driver.app.ctx.state.endings
    # Start from a clean slate so these assertions are independent of whatever
    # endings the demo pack ships in content/endings.yaml.
    tracker.endings.clear()
    tracker.unlocked.clear()
    # Map heroine routes if the demo pack has any heroines.
    heroines = driver.app.ctx.npcs.heroines()
    route = heroines[0].route_id if heroines and heroines[0].route_id else "r1"
    tracker.register(Ending(id="end_lover", title="戀人結局",
                            description="走到了戀人結局。", route_id=route))
    tracker.register(Ending(id="end_friend", title="朋友結局",
                            description="停在朋友的距離。", route_id=route))
    tracker.register(Ending(id="end_secret", title="隱藏結局",
                            description="不該被看見的真相。",
                            route_id=None, hidden=True))
    tracker.register(Ending(id="end_misc", title="尋常結局",
                            description="沒有歸屬的結局。", route_id=None))
    # Unlock exactly one.
    tracker.unlocked["end_lover"] = "2026-05-22T12:00:00"
    return route


def test_endings_scene_opens_and_describes(driver):
    route = _seed_endings(driver)
    driver.app._open_endings()
    driver.advance_frames(2)
    top = driver.app.manager.current
    assert type(top).__name__ == "EndingsScene"
    assert top.is_overlay is True

    d = top.describe()
    assert d["scene"] == "EndingsScene"
    assert d["unlocked"] == ["end_lover"]
    # describe() carries JSON-able completion counts + percentages.
    assert "completion" in d
    assert d["completion"]["endings"]["done"] == 1
    assert d["completion"]["endings"]["total"] == 4
    assert isinstance(d["completion"]["overall_pct"], float)

    # Locked, non-hidden endings appear in the groups; the hidden+locked
    # one is filtered out by visible_to_player().
    all_listed = {eid for g in d["groups"] for eid in g["endings"]}
    assert "end_lover" in all_listed
    assert "end_friend" in all_listed
    assert "end_misc" in all_listed
    assert "end_secret" not in all_listed
    _ = route


def test_endings_completion_uses_visible_authored_cg_roster(driver):
    state = driver.app.ctx.state
    state.meta["__cg_gallery_manifest__"] = CGGalleryManifest.from_items([
        {"asset": "assets/cgs/a.png", "title": "A", "order": 1},
        {"asset": "assets/cgs/b.png", "title": "B", "order": 2},
        {
            "asset": "assets/cgs/hidden_locked.png",
            "title": "Hidden locked",
            "hidden": True,
            "order": 3,
        },
        {
            "asset": "assets/cgs/hidden_seen.png",
            "title": "Hidden seen",
            "hidden": True,
            "order": 4,
        },
    ])
    state.cg_gallery.unlocked.clear()
    state.cg_gallery.unlock("assets/cgs/hidden_seen.png")
    state.cg_gallery.unlock("assets/cgs/old_save.png")

    driver.app._open_endings()
    driver.advance_frames(2)
    completion = driver.app.manager.current.describe()["completion"]

    # a + b + unlocked hidden + old-save fallback; hidden_locked is secret.
    assert completion["cgs"] == {
        "done": 2,
        "total": 4,
        "pct": 50.0,
    }


@pytest.mark.parametrize(
    ("size", "compact"),
    [
        ((640, 360), True),
        ((1280, 720), False),
    ],
)
def test_endings_summary_stays_inside_panel_at_responsive_sizes(
    size,
    compact,
):
    from world_gal_game.dev.driver import GameDriver

    d = GameDriver(pack="demo_pack", screen_size=size)
    try:
        state = d.app.ctx.state
        state.meta["__cg_gallery_manifest__"] = CGGalleryManifest.from_items([
            {"asset": "assets/cgs/a.png", "title": "A"},
        ])
        d.app._open_endings()
        d.advance_frames(2)
        scene = d.app.manager.current

        assert scene._summary_compact is compact
        assert scene._panel_rect.contains(scene._summary_rect)
        assert scene._panel_rect.contains(scene.close_btn.rect)
        assert scene.close_btn.rect.width >= 44
        assert scene.close_btn.rect.height >= 44
        assert scene._summary_rect.bottom < scene._scroll.rect.top
        assert all(
            scene._summary_rect.contains(rect)
            for rect in scene._summary_text_rects
        )
        assert scene._summary_texts[-1].startswith("完成度 ")
        category_labels = scene._summary_texts[:-1]
        assert {"劇情", "結局", "CG"} == {
            label.split(maxsplit=1)[0]
            for label in category_labels
        }
        if compact:
            assert all("(" not in label for label in category_labels)
        else:
            assert all(label.endswith("%)") for label in category_labels)
    finally:
        d.quit()


def test_endings_grouped_by_route(driver):
    route = _seed_endings(driver)
    driver.app._open_endings()
    driver.advance_frames(2)
    top = driver.app.manager.current
    groups = {g["label"]: g["endings"] for g in top.describe()["groups"]}

    # Route endings land under the heroine name (or route id) bucket;
    # the routeless ending lands under "其他".
    heroines = driver.app.ctx.npcs.heroines()
    expected_label = (heroines[0].name
                      if heroines and heroines[0].route_id == route
                      else route)
    assert expected_label in groups
    assert set(groups[expected_label]) == {"end_lover", "end_friend"}
    assert "其他" in groups
    assert groups["其他"] == ["end_misc"]


def test_endings_scene_empty_is_graceful(driver):
    # Clear any pack-provided endings to exercise the empty path explicitly.
    driver.app.ctx.state.endings.endings.clear()
    driver.app.ctx.state.endings.unlocked.clear()
    driver.app._open_endings()
    driver.advance_frames(2)
    top = driver.app.manager.current
    assert type(top).__name__ == "EndingsScene"
    d = top.describe()
    assert d["groups"] == []
    assert d["unlocked"] == []
    assert d["completion"]["endings"] == {"done": 0, "total": 0, "pct": 0.0}
    # Drawing the empty overlay must not raise.
    driver.advance_frames(2)


def test_endings_scene_closes_via_cancel(driver):
    _seed_endings(driver)
    driver.app._open_endings()
    driver.advance_frames(2)
    assert type(driver.app.manager.current).__name__ == "EndingsScene"
    driver.app.manager.pop()
    driver.advance_frames(2)
    assert type(driver.app.manager.current).__name__ != "EndingsScene"
