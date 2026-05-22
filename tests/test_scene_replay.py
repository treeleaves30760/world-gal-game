"""Scene replay (WP-3C).

Two layers of coverage:

* sandbox isolation at the helper level — replaying a scene that fires
  ``set_flag`` / ``affection`` effects must leave the *live* GameState
  byte-for-byte unchanged, and the transient ``__`` meta bridges (autosave,
  plugin manager) must not survive into the sandbox so a replay can never
  trigger a real autosave;
* the overlay scene driven through the real app (GameDriver) — the list
  reflects ``read_log.scenes``, the empty case is graceful, and clicking a
  row starts a replay without mutating the live save.
"""
import os

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import dataclasses

from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import Scene, Line, Effect
from world_gal_game.dialogue.dialogue_engine import DialogueEngine
from world_gal_game.scenes.scene_replay_scene import build_sandbox_context


@pytest.fixture
def driver():
    from world_gal_game.dev.driver import GameDriver
    d = GameDriver(pack="demo_pack")
    yield d
    d.quit()


def _ctx_with_state(driver, state: GameState):
    """A real, fully-formed SceneContext whose state/dialogue we swap.

    Reuses the booted app's context (assets / fonts / theme / npcs / config /
    localization) so the helper tests run against the same plumbing the live
    game uses, with only ``state`` + ``dialogue`` pointed at ``state``.
    """
    return dataclasses.replace(
        driver.app.ctx, state=state, dialogue=DialogueEngine(state))


# ---------------------------------------------------------------------------
# Sandbox helper: build a state that mirrors the live one with effects on it.
# ---------------------------------------------------------------------------

def _state_with_replayable_scene() -> GameState:
    """A GameState with one completed scene whose lines mutate state."""
    s = GameState()
    sc = Scene(
        id="reunion",
        title="重逢",
        lines=[
            Line(text="第一句",
                 effects=[Effect(kind="set_flag", target="replay_touched",
                                 value=True)]),
            Line(text="第二句",
                 effects=[Effect(kind="affection", target="mei", value=9)]),
        ],
    )
    s.story.add_scene(sc)
    # The scene has been read to completion (this is what the list shows).
    s.read_log.mark_scene_done("reunion")
    return s


def _drain_engine(engine) -> None:
    """Walk a DialogueEngine to the end of its current scene."""
    engine.start_scene("reunion")
    for _ in range(50):
        pres = engine.next_line()
        if pres.kind in ("end", "transition"):
            break


def test_build_sandbox_shares_services_but_isolates_state(driver):
    s = _state_with_replayable_scene()
    ctx = _ctx_with_state(driver, s)
    sandbox = build_sandbox_context(ctx)
    # State + dialogue are fresh; everything else is the same object.
    assert sandbox.state is not ctx.state
    assert sandbox.dialogue is not ctx.dialogue
    assert sandbox.dialogue.state is sandbox.state
    assert sandbox.assets is ctx.assets
    assert sandbox.fonts is ctx.fonts
    assert sandbox.theme is ctx.theme
    assert sandbox.npcs is ctx.npcs
    assert sandbox.config is ctx.config


def test_sandbox_drops_transient_meta_bridges(driver):
    """The autosave / plugin-manager bridges must not reach the sandbox.

    If they did, a replay choice could fire the autosave hook against the
    live save dir. The JSON round-trip strips every ``__`` key.
    """
    s = _state_with_replayable_scene()
    # Simulate the live bridges the App parks on meta.
    s.meta["__autosave_config__"] = {"config": object(), "save_dir": "/tmp"}
    s.meta["__plugin_manager__"] = object()
    s.meta["public_note"] = "kept"
    ctx = _ctx_with_state(driver, s)

    sandbox = build_sandbox_context(ctx)
    assert "__autosave_config__" not in sandbox.state.meta
    assert "__plugin_manager__" not in sandbox.state.meta
    # Non-private meta survives the round-trip.
    assert sandbox.state.meta.get("public_note") == "kept"
    # ...and the live state keeps its bridges (round-trip is non-destructive).
    assert "__autosave_config__" in ctx.state.meta
    assert "__plugin_manager__" in ctx.state.meta


def test_replay_in_sandbox_leaves_live_state_unchanged(driver):
    """Running the scene's effects on the sandbox must not touch live state."""
    s = _state_with_replayable_scene()
    ctx = _ctx_with_state(driver, s)
    before = s.model_dump(mode="json")

    sandbox = build_sandbox_context(ctx)
    _drain_engine(sandbox.dialogue)

    # The sandbox actually ran the effects.
    assert sandbox.state.events.flags.get("replay_touched") is True
    assert sandbox.state.affection.get("mei") == 9
    # The live state is byte-for-byte identical to before the replay.
    after = s.model_dump(mode="json")
    assert after == before
    # And specifically: the replayed flag / affection never leaked to live.
    assert "replay_touched" not in s.events.flags
    assert s.affection.get("mei") == 0


# ---------------------------------------------------------------------------
# Overlay scene through the real app (GameDriver).
# ---------------------------------------------------------------------------

def _open_replay(driver):
    driver.app._open_scene_replay()
    driver.advance_frames(2)
    scene = driver.app.manager.current
    assert type(scene).__name__ == "SceneReplayScene"
    return scene


def test_replay_overlay_opens_and_is_overlay(driver):
    scene = _open_replay(driver)
    assert scene.is_overlay is True
    d = scene.describe()
    assert d["scene"] == "SceneReplayScene"
    assert isinstance(d["replayable"], list)
    assert d["replaying"] is False


def test_empty_read_log_renders_gracefully(driver):
    """No completed scenes -> the empty message branch, no crash."""
    driver.app.state.read_log.scenes.clear()
    scene = _open_replay(driver)
    assert scene.describe()["replayable"] == []
    driver.advance_frames(2)
    assert type(driver.app.manager.current).__name__ == "SceneReplayScene"


def test_list_reflects_read_log_scenes(driver):
    """The replayable list is exactly the read-log scenes the pack defines."""
    story = driver.app.state.story
    known = list(story.scenes.keys())
    assert known, "demo_pack should define scenes"
    driver.app.state.read_log.scenes.clear()
    driver.app.state.read_log.mark_scene_done(known[0])
    # An id the pack does not define must be filtered out of the list.
    driver.app.state.read_log.mark_scene_done("ghost_scene_not_in_pack")
    scene = _open_replay(driver)
    d = scene.describe()
    assert known[0] in d["replayable"]
    assert "ghost_scene_not_in_pack" not in d["replayable"]


def test_clicking_row_starts_replay_without_touching_live_state(driver):
    """Click a listed scene -> a replay starts; the live save is unchanged."""
    story = driver.app.state.story
    sid = next(iter(story.scenes.keys()))
    driver.app.state.read_log.scenes.clear()
    driver.app.state.read_log.mark_scene_done(sid)
    scene = _open_replay(driver)
    driver.advance_frames(1)  # populate _row_rects via a draw pass

    live_before = driver.app.state.model_dump(mode="json")
    row = next((rect for rect, rid in scene._row_rects if rid == sid), None)
    assert row is not None
    driver.click(row.center)
    driver.advance_frames(2)

    # We are now replaying inside a sandbox-backed child DialogueScene.
    d = scene.describe()
    assert d["replaying"] is True
    assert d["replay_scene_id"] == sid
    # The replay runs on the sandbox; the live state never moved.
    assert driver.app.state.model_dump(mode="json") == live_before
    # The child scene drives its own state, not the live one.
    assert scene._replay_ctx is not None
    assert scene._replay_ctx.state is not driver.app.state


def test_advancing_the_replay_does_not_mutate_live_state(driver):
    """Blast through the replayed scene; live flags / read-log stay put."""
    import pygame
    story = driver.app.state.story
    sid = next(iter(story.scenes.keys()))
    driver.app.state.read_log.scenes.clear()
    driver.app.state.read_log.mark_scene_done(sid)
    scene = _open_replay(driver)
    driver.advance_frames(1)
    live_before = driver.app.state.model_dump(mode="json")

    row = next((rect for rect, rid in scene._row_rects if rid == sid), None)
    assert row is not None
    driver.click(row.center)
    driver.advance_frames(2)
    assert scene.describe()["replaying"] is True

    # Hammer space to advance the replayed dialogue several lines.
    for _ in range(30):
        driver.press_key(pygame.K_SPACE)
        driver.advance_frames(2)
        if scene.describe()["replaying"] is False:
            break

    # However far the replay got, the live save is byte-for-byte unchanged.
    assert driver.app.state.model_dump(mode="json") == live_before
