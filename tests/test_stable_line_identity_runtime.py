"""Stable authored-line identity across runtime persistence surfaces."""

from __future__ import annotations

import copy

import pytest

from world_gal_game.config import EngineConfig
from world_gal_game.core.clear_data import ClearData
from world_gal_game.core.event_log import DialogueHistory
from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import (
    Line,
    Scene,
    StoryGraph,
    rebind_authored_dialogue_history_voices,
    rebind_authored_story_catalog,
)
from world_gal_game.dev.hot_reload import _restore_progress, _snapshot_progress
from world_gal_game.dialogue.dialogue_engine import (
    DialogueEngine,
    presentation_to_dict,
    rebind_resume_choice_presentations,
    resume_presentations_for_state,
)
from world_gal_game.dialogue.replay_engine import (
    PresentationTraceDialogueEngine,
    rebind_authored_replay_visual_fx,
)
from world_gal_game.headless import HeadlessSession
from world_gal_game.npc.llm_brain import EchoBrain
from world_gal_game.npc.npc_base import NPCRegistry


def _story(*lines: Line, cursor: int = 0) -> StoryGraph:
    return StoryGraph(
        scenes={"scene": Scene(id="scene", lines=list(lines))},
        current_scene="scene",
        current_line_index=cursor,
    )


def test_story_cursor_rebinds_next_line_and_preserves_end_boundary() -> None:
    saved = _story(
        Line(loc_id="a", text="A"),
        Line(loc_id="b", text="B"),
        cursor=1,
    )
    reordered = _story(
        Line(loc_id="b", text="B"),
        Line(loc_id="inserted", text="new"),
        Line(loc_id="a", text="A"),
    )

    rebound = rebind_authored_story_catalog(reordered, saved)
    assert rebound.current_line_index == 0
    assert rebound.scenes == reordered.scenes

    saved.current_line_index = 2
    at_end = rebind_authored_story_catalog(reordered, saved)
    assert at_end.current_line_index == 3

    deleted = _story(Line(loc_id="a", text="A"))
    saved.current_line_index = 1
    with pytest.raises(ValueError, match="loc_id"):
        rebind_authored_story_catalog(deleted, saved)


def test_dialogue_read_and_presentation_use_stable_identity_only() -> None:
    state = GameState()
    state.story = _story(Line(loc_id="opening", text="hello"))

    presented = DialogueEngine(state).start_scene("scene")

    assert presented.line is not None
    assert presented.line.loc_id == "opening"
    assert state.read_log.lines == {"scene:@opening"}
    assert state.read_log.is_read("scene", 0, loc_id="opening")
    assert not state.read_log.is_read("scene", 0)
    assert state.read_log.line_key("scene", 0, loc_id="opening") == (
        "scene:@opening"
    )

    headless_state = GameState()
    headless_state.story = _story(Line(loc_id="headless", text="hello"))
    session = HeadlessSession(
        config=EngineConfig(),
        state=headless_state,
        npcs=NPCRegistry(),
        brain=EchoBrain(),
        dialogue=DialogueEngine(headless_state),
        meta={},
    )
    result = session.start_scene("scene")
    assert result["presentation"]["line"]["loc_id"] == "headless"


def test_resume_crossing_reorder_uses_presentation_and_next_line_ids() -> None:
    old_state = GameState()
    old_state.story = _story(
        Line(loc_id="a", text="A"),
        Line(loc_id="b", text="B"),
    )
    shown = DialogueEngine(old_state).start_scene("scene")
    resume = {
        "version": 1,
        "screen": "dialogue",
        "scene_id": "scene",
        "story_scene": "scene",
        "story_line_index": 1,
        "story_cursor_loc_id": "b",
        "story_cursor_at_end": False,
        "presentation": presentation_to_dict(shown),
        "last_line": None,
    }

    current = GameState()
    current.story = _story(
        Line(loc_id="b", text="B"),
        Line(loc_id="a", text="A"),
        cursor=0,
    )
    rebound = rebind_resume_choice_presentations(resume, current)

    assert rebound["story_line_index"] == 0
    assert rebound["story_cursor_loc_id"] == "b"
    raw_line = rebound["presentation"]["line"]
    assert raw_line["loc_id"] == "a"
    assert raw_line["line_index"] == 1
    assert raw_line["total_lines"] == 2
    restored, _ = resume_presentations_for_state(rebound, current)
    assert restored.line is not None
    assert restored.line.loc_id == "a"

    hostile = copy.deepcopy(resume)
    hostile["presentation"]["line"]["loc_id"] = "deleted"
    with pytest.raises(ValueError, match="loc_id"):
        rebind_resume_choice_presentations(hostile, current)


def test_replay_keeps_historical_positions_but_resolves_assets_by_loc_id() -> None:
    old = GameState()
    old.story = _story(
        Line(loc_id="a", text="A", voice="assets/voice/a.ogg"),
        Line(loc_id="b", text="B", voice="assets/voice/b.ogg"),
    )
    engine = DialogueEngine(old)
    engine.start_scene("scene")
    engine.next_line()
    engine.next_line()
    entry = old.read_log.replay_entry("scene")
    assert entry is not None

    current_story = _story(
        Line(loc_id="b", text="B", voice="assets/voice/b.ogg"),
        Line(loc_id="inserted", text="new"),
        Line(loc_id="a", text="A", voice="assets/voice/a.ogg"),
    )
    rebound_log = rebind_authored_replay_visual_fx(
        old.read_log,
        current_story,
    )
    rebound = rebound_log.replay_entry("scene")
    assert rebound is not None
    assert [row["line"]["line_index"] for row in rebound.presentations] == [0, 1]
    assert [row["line"]["total_lines"] for row in rebound.presentations] == [2, 2]
    assert [row["line"]["loc_id"] for row in rebound.presentations] == ["a", "b"]

    sandbox = GameState()
    sandbox.story = current_story
    replay = PresentationTraceDialogueEngine(
        sandbox,
        rebound,
        authored_story=current_story,
    )
    assert replay.start_scene("scene").line.loc_id == "a"
    assert sandbox.story.current_line_index == 3
    assert replay.next_line().line.loc_id == "b"
    assert sandbox.story.current_line_index == 1

    deleted_story = _story(Line(loc_id="b", text="B"))
    filtered = rebind_authored_replay_visual_fx(old.read_log, deleted_story)
    assert "scene" not in filtered.replay_archive


def test_history_voice_follows_loc_id_and_deleted_line_is_silent() -> None:
    history = DialogueHistory(lines=[
        {
            "scene_id": "scene",
            "line_index": 0,
            "loc_id": "a",
            "text": "historical A",
            "voice": "assets/voice/wrong.ogg",
        },
        {
            "scene_id": "scene",
            "line_index": 1,
            "loc_id": "deleted",
            "text": "keep deleted copy",
            "voice": "assets/voice/b.ogg",
        },
    ])
    authored = _story(
        Line(loc_id="b", text="B", voice="assets/voice/b.ogg"),
        Line(loc_id="a", text="A", voice="assets/voice/a.ogg"),
    )

    rebound = rebind_authored_dialogue_history_voices(authored, history)
    assert rebound.lines[0]["line_index"] == 1
    assert rebound.lines[0]["voice"] == "assets/voice/a.ogg"
    assert rebound.lines[1]["text"] == "keep deleted copy"
    assert rebound.lines[1]["voice"] is None


def test_hot_reload_and_clear_data_rebind_stable_cursor_and_replay() -> None:
    old = GameState()
    old.story = _story(
        Line(loc_id="a", text="A"),
        Line(loc_id="b", text="B"),
        cursor=1,
    )
    snapshot = _snapshot_progress(old)
    fresh = GameState()
    fresh.story = _story(
        Line(loc_id="b", text="B"),
        Line(loc_id="a", text="A"),
    )
    _restore_progress(fresh, snapshot)
    assert fresh.story.current_line_index == 0

    old.story.current_line_index = 2
    boundary_snapshot = _snapshot_progress(old)
    appended = GameState()
    appended.story = _story(
        Line(loc_id="a", text="A"),
        Line(loc_id="b", text="B"),
        Line(loc_id="new_tail", text="new tail"),
    )
    _restore_progress(appended, boundary_snapshot)
    assert appended.story.current_line_index == 3

    archived_state = GameState()
    archived_state.story = _story(Line(loc_id="a", text="A"))
    archive_engine = DialogueEngine(archived_state)
    archive_engine.start_scene("scene")
    archive_engine.next_line()
    archive = archived_state.read_log.replay_archive
    legacy_archive = copy.deepcopy(archive)
    legacy_archive["scene"].presentations[0]["line"].pop("loc_id")
    clear_data = ClearData(replay_archive=legacy_archive)
    target = GameState()
    target.story = _story(Line(loc_id="a", text="A"))
    clear_data.apply_to_state(target)
    assert (
        target.read_log.replay_archive["scene"].presentations[0]["line"]["loc_id"]
        == "a"
    )
