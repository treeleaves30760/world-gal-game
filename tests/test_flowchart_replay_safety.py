"""Flowchart replay must never become a live narrative branch jump."""
from __future__ import annotations

import copy
import os

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from world_gal_game.core.chapter_spec import ChapterSpec
from world_gal_game.core.story_graph import Effect, Line, Scene
from world_gal_game.dialogue.dialogue_engine import DialogueEngine
from world_gal_game.dialogue.replay_engine import (
    PresentationTraceDialogueEngine,
)
from world_gal_game.scenes.flowchart_scene import FlowchartScene
from world_gal_game.scenes.scene_replay_scene import SceneReplayScene
from world_gal_game.ui.assets import AudioPlaybackSnapshot
from world_gal_game.ui.input import InputState
from world_gal_game.ui.input_bindings import InputAction


@pytest.fixture
def driver():
    from world_gal_game.dev.driver import GameDriver

    game = GameDriver(pack="demo_pack")
    yield game
    game.quit()


def _install_mutating_trace_scene(driver) -> str:
    """Add and first-view a scene whose authored effects mutate most state."""
    state = driver.app.state
    scene_id = "flowchart_sandbox_regression"
    scene = Scene(
        id=scene_id,
        title="安全重溫回歸",
        lines=[
            Line(
                text="這些效果只會在第一次觀看時發生。",
                effects=[
                    Effect(
                        kind="set_flag",
                        target="replay_only_flag",
                        value=True,
                    ),
                    Effect(kind="advance_time", value=2),
                    Effect(
                        kind="give_item",
                        target="replay_only_item",
                        value=3,
                    ),
                    Effect(
                        kind="log_event",
                        target="重溫內事件",
                        value="不得寫回 live history",
                    ),
                ],
            ),
        ],
    )
    state.story.add_scene(scene)
    authored_story = state.meta.get("__pack_story_catalog__")
    if authored_story is not None and authored_story is not state.story:
        authored_story.add_scene(copy.deepcopy(scene))
    state.route = "live_route"
    state.inventory.add("live_keepsake", 2)
    state.events.set_flag("live_flag", "kept")
    state.events.record(kind="custom", title="live event")
    state.dialogue_history.push(
        speaker=None,
        text="live history",
        scene_id="live_scene",
    )
    state.read_log.lines.add("live_scene:0")
    first_view = DialogueEngine(state)
    assert first_view.start_scene(scene_id).kind == "line"
    assert first_view.next_line().kind == "end"
    assert state.read_log.replay_entry(scene_id) is not None
    return scene_id


def _persistent_projection(state) -> dict:
    """The P0 surfaces a replay is specifically forbidden to mutate."""
    return {
        "route": state.route,
        "time": state.time.model_dump(mode="json"),
        "inventory": state.inventory.model_dump(mode="json"),
        "flags": copy.deepcopy(state.events.flags),
        "event_history": state.events.model_dump(mode="json"),
        "dialogue_history": state.dialogue_history.model_dump(mode="json"),
        "read_state": state.read_log.model_dump(mode="json"),
        "story_cursor": {
            "scene": state.story.current_scene,
            "line": state.story.current_line_index,
            "played": sorted(state.story.played),
        },
    }


def _open_flowchart_replay(driver, scene_id: str):
    driver.app._open_flowchart()
    driver.app.manager.commit_pending()
    flowchart = driver.app.manager.current
    assert isinstance(flowchart, FlowchartScene)

    flowchart.on_jump(scene_id)
    driver.app.manager.commit_pending()
    replay = driver.app.manager.current
    assert isinstance(replay, SceneReplayScene)
    assert driver.app.manager.stack()[-2] is flowchart
    assert replay.describe()["replay_scene_id"] == scene_id
    return flowchart, replay


def test_flowchart_trace_replay_never_reapplies_archived_effects(driver):
    scene_id = _install_mutating_trace_scene(driver)
    live = driver.app.state
    before = _persistent_projection(live)

    flowchart, replay = _open_flowchart_replay(driver, scene_id)
    sandbox = replay._replay_ctx.state
    assert isinstance(
        replay._replay_ctx.dialogue,
        PresentationTraceDialogueEngine,
    )

    # First viewing already applied each authored effect exactly once. Trace
    # playback redraws that evaluated presentation without dispatching any of
    # them again, even against the throwaway copy.
    assert sandbox.events.flags["replay_only_flag"] is True
    assert sandbox.inventory.count("replay_only_item") == 3
    sandbox_projection = _persistent_projection(sandbox)
    assert {
        key: value
        for key, value in sandbox_projection.items()
        if key != "story_cursor"
    } == {
        key: value
        for key, value in before.items()
        if key != "story_cursor"
    }
    assert f"{scene_id}:0" in sandbox.read_log.lines
    assert _persistent_projection(live) == before

    # Complete the one-line scene. One-shot chart replay closes back to the
    # same chart rather than advancing/replacing the live route.
    replay._replay_scene._advance()
    driver.app.manager.commit_pending()
    assert driver.app.manager.current is flowchart
    assert _persistent_projection(live) == before


def test_semantic_back_cancels_flowchart_replay_and_restores_audio(
    driver,
    monkeypatch,
):
    scene_id = _install_mutating_trace_scene(driver)
    live = driver.app.state
    before = _persistent_projection(live)
    token = AudioPlaybackSnapshot(
        music="live_bgm.ogg",
        music_volume=0.35,
        ambient="live_ambient.ogg",
        ambient_volume=0.25,
        voice="live_voice.ogg",
        voice_volume=0.8,
        voice_was_busy=True,
    )
    restored = []
    monkeypatch.setattr(
        driver.app.assets,
        "capture_audio_playback",
        lambda: token,
    )
    monkeypatch.setattr(
        driver.app.assets,
        "restore_audio_playback",
        restored.append,
    )

    flowchart, replay = _open_flowchart_replay(driver, scene_id)
    driver.app.manager.update(
        0.0,
        InputState(actions_pressed={InputAction.BACK}),
    )

    assert driver.app.manager.current is flowchart
    assert replay.describe()["replaying"] is False
    assert restored == [token]
    assert _persistent_projection(live) == before


def test_unread_chapter_entry_is_never_used_as_replay_permission(driver):
    state = driver.app.state
    state.read_log.scenes.clear()
    state.read_log.replay_archive.clear()
    state.story.add_scene(
        Scene(
            id="actually_read_scene",
            title="實際讀過的場景",
            lines=[Line(text="這才是有完整記錄的內容。")],
        ),
    )
    first_view = DialogueEngine(state)
    assert first_view.start_scene("actually_read_scene").kind == "line"
    assert first_view.next_line().kind == "end"
    chapter = ChapterSpec(
        id="legacy_partial_chapter",
        title="部分已讀",
        entry_scene="unread_entry_scene",
        scenes=["unread_entry_scene", "actually_read_scene"],
    )
    flowchart = FlowchartScene(driver.app.ctx)

    assert flowchart._chapter_read(chapter) is True
    assert flowchart._replay_scene(chapter) == "actually_read_scene"


def test_legacy_read_card_click_stays_on_chart_without_empty_replay(driver):
    state = driver.app.state
    chapter = state.meta["__chapters__"].ordered()[0]
    scene_id = chapter.entry_scene or chapter.scenes[0]
    state.read_log.scenes.clear()
    state.read_log.replay_archive.clear()
    state.read_log.mark_scene_done(scene_id)

    driver.app._open_flowchart()
    driver.app.manager.commit_pending()
    flowchart = driver.app.manager.current
    assert isinstance(flowchart, FlowchartScene)
    hit_index = flowchart._hit_chapter_ids.index(chapter.id)
    target = flowchart._hit_screen_rect(hit_index)
    stack_before = driver.app.manager.stack()

    assert flowchart._chapter_read(chapter) is True
    assert flowchart._replay_scene(chapter) is None
    assert flowchart._hits[hit_index][1] == ""
    assert flowchart._view_rect.contains(target)

    driver.app.manager.update(
        0.0,
        InputState(mouse_clicked=True, mouse_pos=target.center),
    )
    driver.app.manager.commit_pending()

    assert driver.app.manager.current is flowchart
    assert driver.app.manager.stack() == stack_before


def test_audio_playback_snapshot_restores_all_owned_channels(
    driver,
    monkeypatch,
):
    assets = driver.app.assets
    assets._current_music = "live_bgm.ogg"
    assets._bgm_volume = 0.35
    assets._current_ambient = "live_ambient.ogg"
    assets._ambient_volume = 0.25
    assets._current_voice = "live_voice.ogg"
    assets._current_voice_volume = 0.8
    monkeypatch.setattr(assets, "voice_busy", lambda: True)
    snapshot = assets.capture_audio_playback()

    assets._current_music = "replay_bgm.ogg"
    assets._current_ambient = "replay_ambient.ogg"
    assets._current_voice = "replay_voice.ogg"
    assets._music_revision += 1
    assets._ambient_revision += 1
    assets._voice_revision += 1
    calls: list[tuple] = []

    def stop_music(*, fade_ms=800):
        calls.append(("stop_music", fade_ms))
        assets._current_music = None

    def play_music(path, *, volume=0.6, loops=-1, fade_ms=800):
        calls.append(("play_music", path, volume, fade_ms))
        assets._current_music = path

    def stop_ambient(*, fade_ms=1000):
        calls.append(("stop_ambient", fade_ms))
        assets._current_ambient = None

    def play_ambient(path, *, volume=0.4, fade_ms=1000):
        calls.append(("play_ambient", path, volume, fade_ms))
        assets._current_ambient = path

    def stop_voice():
        calls.append(("stop_voice",))
        assets._current_voice = None

    def play_voice(path, *, volume=1.0):
        calls.append(("play_voice", path, volume))
        assets._current_voice = path

    monkeypatch.setattr(assets, "stop_music", stop_music)
    monkeypatch.setattr(assets, "play_music", play_music)
    monkeypatch.setattr(assets, "stop_ambient", stop_ambient)
    monkeypatch.setattr(assets, "play_ambient", play_ambient)
    monkeypatch.setattr(assets, "stop_voice", stop_voice)
    monkeypatch.setattr(assets, "play_voice", play_voice)

    assets.restore_audio_playback(snapshot)

    assert assets._current_music == "live_bgm.ogg"
    assert assets._current_ambient == "live_ambient.ogg"
    assert assets._current_voice == "live_voice.ogg"
    assert ("stop_music", 0) in calls
    assert ("play_music", "live_bgm.ogg", 0.35, 0) in calls
    assert ("stop_ambient", 0) in calls
    assert ("play_ambient", "live_ambient.ogg", 0.25, 0) in calls
    assert ("stop_voice",) in calls
    assert ("play_voice", "live_voice.ogg", 0.8) in calls
