"""Faithful recollection archive: evaluated first-view paths, not live reruns."""

import pytest

from world_gal_game.core.game_state import GameState
from world_gal_game.core.history import StateHistory
from world_gal_game.core.read_log import ReplayArchiveEntry
from world_gal_game.core.story_graph import (
    Choice,
    Condition,
    Effect,
    Line,
    Scene,
)
from world_gal_game.core.time_system import TimeOfDay
from world_gal_game.dialogue.dialogue_engine import (
    DialogueEngine,
    REPLAY_FX_PAYLOAD_KEY,
    REPLAY_HUD_META_KEY,
    presentation_from_dict,
)
from world_gal_game.dialogue.replay_engine import (
    PresentationTraceDialogueEngine,
    rebind_authored_replay_visual_fx,
)
from world_gal_game.plugins.builtin_effects import VISUAL_FX_QUEUE
from world_gal_game.scenes.dialogue_scene import DialogueScene


def _engine_with_branch() -> tuple[DialogueEngine, GameState]:
    state = GameState()
    state.player.name = "第一次的名字"
    state.events.set_flag("secret_open", True)
    state.story.add_scene(
        Scene(
            id="memory",
            title="那一天",
            lines=[
                Line(text="共同台詞"),
                Line(
                    text="只有當時看見的祕密",
                    requires=[Condition(kind="flag", target="secret_open")],
                ),
            ],
            choices=[
                Choice(id="left", text="走左邊", next_scene="left_result"),
                Choice(id="right", text="走右邊", next_scene="right_result"),
            ],
        )
    )
    state.story.add_scene(
        Scene(id="left_result", title="左邊", lines=[Line(text="左線正文")])
    )
    state.story.add_scene(
        Scene(id="right_result", title="右邊", lines=[Line(text="右線正文")])
    )
    return DialogueEngine(state), state


def test_engine_archives_only_the_evaluated_scene_and_selected_path():
    engine, state = _engine_with_branch()

    first = engine.start_scene("memory")
    second = engine.next_line()
    decision = engine.next_line()
    assert first.line.text == "共同台詞"
    assert second.line.text == "只有當時看見的祕密"
    assert decision.kind == "choice"

    chained = engine.choose("left")
    assert chained.scene_id == "left_result"

    entry = state.read_log.replay_entry("memory")
    assert entry is not None
    assert entry.selected_choices == ["left"]
    decoded = [presentation_from_dict(item) for item in entry.presentations]
    assert [item.kind for item in decoded] == ["line", "line", "choice"]
    assert [item.line.text for item in decoded[:2]] == [
        "共同台詞",
        "只有當時看見的祕密",
    ]
    # The chained result is a separate archive episode.  Merely showing its
    # first line does not make it a completed/replayable entry.
    assert state.read_log.replay_entry("left_result") is None
    assert "right_result" not in state.read_log.replay_archive


def test_later_flags_and_revisit_cannot_rewrite_first_view_archive():
    engine, state = _engine_with_branch()
    engine.start_scene("memory")
    engine.next_line()
    engine.next_line()
    engine.choose("left")
    original = state.read_log.replay_entry("memory").model_dump(mode="json")

    # A later playthrough has a different branch state.  Starting the scene
    # naturally may render different source content, but the completed memory
    # remains the first evaluated path.
    state.events.set_flag("secret_open", False)
    state.player.name = "後來的名字"
    revisit = DialogueEngine(state)
    revisit.start_scene("memory")
    changed_boundary = revisit.next_line()
    assert changed_boundary.kind == "choice"
    revisit.choose("right")

    assert (
        state.read_log.replay_entry("memory").model_dump(mode="json")
        == original
    )


def test_invalid_choice_does_not_duplicate_archived_choice_screen():
    engine, state = _engine_with_branch()
    engine.start_scene("memory")
    engine.next_line()
    engine.next_line()
    repeated = engine.choose("not_an_authored_choice")
    assert repeated.kind == "choice"
    engine.choose("left")

    entry = state.read_log.replay_entry("memory")
    assert entry is not None
    assert [item["kind"] for item in entry.presentations].count("choice") == 1
    assert entry.selected_choices == ["left"]


def test_mid_scene_archive_roundtrip_stays_incomplete_until_scene_end():
    engine, state = _engine_with_branch()
    engine.start_scene("memory")

    restored = GameState.model_validate_json(state.model_dump_json())
    assert restored.read_log.replay_entry("memory") is None
    in_progress = restored.read_log.replay_archive["memory"]
    assert in_progress.complete is False
    assert len(in_progress.presentations) == 1

    resumed = DialogueEngine(restored)
    resumed.next_line()
    resumed.next_line()
    resumed.choose("left")
    assert restored.read_log.replay_entry("memory") is not None


def test_archive_envelope_never_prevents_an_authored_scene_from_playing():
    state = GameState()
    authored = (
        ("oversized_archive_title", "x" * 4097),
        ("s" * 513, "超長場景 ID"),
    )
    for scene_id, title in authored:
        state.story.add_scene(
            Scene(
                id=scene_id,
                title=title,
                lines=[Line(text="正文仍然必須顯示")],
            )
        )
        presentation = DialogueEngine(state).start_scene(scene_id)
        assert presentation.kind == "line"
        assert presentation.line.text == "正文仍然必須顯示"
        assert scene_id not in state.read_log.replay_archive


def _completed_memory() -> tuple[GameState, object]:
    engine, state = _engine_with_branch()
    engine.start_scene("memory")
    engine.next_line()
    engine.next_line()
    engine.choose("left")
    return state, state.read_log.replay_entry("memory")


def test_trace_player_never_re_evaluates_later_state_or_follows_chain():
    live, entry = _completed_memory()
    # Model the later save state that made the old live-rerun implementation
    # unsafe: the original conditional line is now hidden and another branch
    # is otherwise available.
    live.events.set_flag("secret_open", False)
    sandbox = GameState.model_validate_json(live.model_dump_json())
    replay = PresentationTraceDialogueEngine(sandbox, entry)

    assert replay.start_scene("memory").line.text == "共同台詞"
    assert replay.next_line().line.text == "只有當時看見的祕密"
    decision = replay.next_line()
    assert decision.kind == "choice"
    assert next(c for c in decision.choices if c.id == "left").enabled is True
    right = next(c for c in decision.choices if c.id == "right")
    assert right.enabled is False
    assert right.reason == "回想會維持當時的選擇"

    # A different option cannot move the cursor.  The recorded option ends the
    # one-scene episode instead of following left_result.
    assert replay.choose("right").kind == "choice"
    assert replay.choose("left").kind == "end"
    assert sandbox.story.current_scene is None
    assert "右線正文" not in [
        item["line"].get("text", "")
        for item in entry.presentations
        if item.get("line")
    ]


def test_trace_player_does_not_write_any_persistent_state():
    live, entry = _completed_memory()
    sandbox = GameState.model_validate_json(live.model_dump_json())
    before = sandbox.model_dump(mode="json")
    replay = PresentationTraceDialogueEngine(sandbox, entry)
    replay.start_scene("memory")
    replay.next_line()
    replay.next_line()
    replay.choose("left")

    after = sandbox.model_dump(mode="json")
    # Only the transient story cursor is used by DialogueScene's renderer.
    before["story"]["current_scene"] = None
    before["story"]["current_line_index"] = 0
    assert after == before


def test_visual_fx_and_hud_labels_are_archived_and_reinjected():
    state = GameState()
    state.story.add_scene(
        Scene(
            id="staged_memory",
            title="演出回想",
            lines=[
                Line(
                    text="閃光發生在這一句",
                    effects=[
                        Effect(
                            kind="screen_flash",
                            value={"color": [10, 20, 30], "duration": 0.2},
                        )
                    ],
                )
            ],
        )
    )
    engine = DialogueEngine(state)
    engine.start_scene("staged_memory")
    engine.next_line()
    entry = state.read_log.replay_entry("staged_memory")
    payload = entry.presentations[0]
    assert payload[REPLAY_FX_PAYLOAD_KEY] == [
        {
            "fx": "screen_flash",
            "color": [10, 20, 30],
            "duration": 0.2,
            "max_alpha": 255,
            "easing": None,
        }
    ]
    assert payload["_replay_hud"]["date_label"] == state.time.label()
    assert payload["_replay_hud"]["time_of_day"] == "morning"

    state.time.set_phase(TimeOfDay.NIGHT)
    sandbox = GameState.model_validate_json(state.model_dump_json())
    # A real DialogueScene consumes this queue immediately.  Clear the live
    # headless queue first so this assertion proves the trace re-injected it.
    sandbox.meta.pop(VISUAL_FX_QUEUE, None)
    replay = PresentationTraceDialogueEngine(sandbox, entry)
    replay.start_scene("staged_memory")
    assert sandbox.meta[VISUAL_FX_QUEUE][0]["fx"] == "screen_flash"
    assert (
        sandbox.meta[REPLAY_HUD_META_KEY]["date_label"]
        != state.time.label()
    )
    dialogue_view = DialogueScene.__new__(DialogueScene)
    dialogue_view.ctx = type("Ctx", (), {"state": sandbox})()
    assert dialogue_view._presentation_time_of_day() == "morning"


@pytest.mark.parametrize(
    "directive",
    [
        {
            "fx": "set_background",
            "path": "assets/backgrounds/save-only.png",
        },
        {
            "fx": "set_weather",
            "backend": "save_only_weather_backend",
            "params": {
                "asset_path": "assets/weather/save-only-payload.bin",
            },
            "fade": 0.0,
        },
    ],
    ids=("decoder-path", "ambient-plugin"),
)
def test_replay_fx_requires_exact_current_authored_provenance(directive):
    state = GameState()
    state.story.add_scene(Scene(
        id="hostile_fx_memory",
        lines=[Line(text="這一行沒有任何演出效果。")],
    ))
    engine = DialogueEngine(state)
    engine.start_scene("hostile_fx_memory")
    engine.next_line()
    original = state.read_log.replay_entry("hostile_fx_memory")
    assert original is not None

    hostile_payload = original.model_dump(mode="json")
    hostile_payload["presentations"][0][REPLAY_FX_PAYLOAD_KEY] = [directive]
    # The archive envelope accepts this bounded relative path/plugin metadata;
    # current authored content is the separate execution-authority boundary.
    hostile = ReplayArchiveEntry.model_validate(hostile_payload)
    with pytest.raises(ValueError, match="visual effect is not authored"):
        PresentationTraceDialogueEngine(state, hostile)

    state.read_log.replay_archive["hostile_fx_memory"] = hostile
    safe_log = rebind_authored_replay_visual_fx(
        state.read_log,
        state.story,
    )
    safe = safe_log.replay_entry("hostile_fx_memory")
    assert safe is not None
    assert REPLAY_FX_PAYLOAD_KEY not in safe.presentations[0]

    state.meta.pop(VISUAL_FX_QUEUE, None)
    replay = PresentationTraceDialogueEngine(state, safe)
    replay.start_scene("hostile_fx_memory")
    assert VISUAL_FX_QUEUE not in state.meta


def test_choice_transition_fx_are_owned_by_the_chained_scene_first_line():
    state = GameState()
    state.story.add_scene(
        Scene(
            id="decision_fx",
            lines=[Line(text="選吧")],
            choices=[
                Choice(
                    id="go",
                    text="前進",
                    effects=[
                        Effect(
                            kind="screen_flash",
                            value={"color": [1, 2, 3], "duration": 0.1},
                        )
                    ],
                    next_scene="arrival_fx",
                )
            ],
        )
    )
    state.story.add_scene(
        Scene(
            id="arrival_fx",
            lines=[
                Line(
                    text="抵達",
                    effects=[
                        Effect(
                            kind="screen_tint",
                            value={"color": [4, 5, 6], "duration": 0.2},
                        )
                    ],
                )
            ],
        )
    )
    engine = DialogueEngine(state)
    engine.start_scene("decision_fx")
    engine.next_line()
    engine.choose("go")

    first = state.read_log.replay_archive["arrival_fx"].presentations[0]
    assert [item["fx"] for item in first[REPLAY_FX_PAYLOAD_KEY]] == [
        "screen_flash",
        "screen_tint",
    ]
    engine.next_line()
    arrival_entry = state.read_log.replay_entry("arrival_fx")
    assert arrival_entry is not None
    PresentationTraceDialogueEngine(state, arrival_entry)


def test_on_end_transition_fx_are_owned_by_the_next_scene_first_line():
    state = GameState()
    state.story.add_scene(
        Scene(
            id="departure_fx",
            lines=[Line(text="離開")],
            on_end=[
                Effect(
                    kind="screen_flash",
                    value={"color": [9, 8, 7], "duration": 0.1},
                ),
                Effect(kind="play_scene", target="arrival_fx"),
            ],
        )
    )
    state.story.add_scene(Scene(id="arrival_fx", lines=[Line(text="抵達")]))
    engine = DialogueEngine(state)
    engine.start_scene("departure_fx")
    transition = engine.next_line()
    assert transition.kind == "transition"
    engine.start_scene(transition.next_scene)

    first = state.read_log.replay_archive["arrival_fx"].presentations[0]
    assert [item["fx"] for item in first[REPLAY_FX_PAYLOAD_KEY]] == [
        "screen_flash"
    ]
    engine.next_line()
    arrival_entry = state.read_log.replay_entry("arrival_fx")
    assert arrival_entry is not None
    PresentationTraceDialogueEngine(state, arrival_entry)


def test_external_movie_and_chapter_overlays_are_not_embedded_in_scene_trace():
    state = GameState()
    state.story.add_scene(
        Scene(
            id="external_overlay",
            lines=[
                Line(
                    text="正文仍可回想",
                    effects=[
                        Effect(kind="play_movie", target="assets/video/op.mp4")
                    ],
                )
            ],
        )
    )
    engine = DialogueEngine(state)
    engine.start_scene("external_overlay")
    engine.next_line()
    entry = state.read_log.replay_entry("external_overlay")
    assert entry is not None
    assert REPLAY_FX_PAYLOAD_KEY not in entry.presentations[0]


def test_rollback_rebuilds_active_trace_without_duplicate_lines():
    state = GameState()
    state.story.add_scene(
        Scene(
            id="rollback_lines",
            lines=[Line(text="0"), Line(text="1"), Line(text="2")],
        )
    )
    engine = DialogueEngine(state)
    history = StateHistory()

    line0 = engine.start_scene("rollback_lines")
    history.record(state, line0)
    line1 = engine.next_line()
    history.record(state, line1)
    assert history.rewind(state).line.text == "0"

    engine.next_line()
    engine.next_line()
    engine.next_line()
    entry = state.read_log.replay_entry("rollback_lines")
    assert entry is not None
    assert [item["line"]["line_index"] for item in entry.presentations] == [
        0,
        1,
        2,
    ]


def test_rollback_choice_records_the_branch_finally_committed():
    state = GameState()
    state.story.add_scene(
        Scene(
            id="rollback_choice",
            lines=[Line(text="選擇")],
            choices=[
                Choice(id="a", text="A", next_scene="a_result"),
                Choice(id="b", text="B", next_scene="b_result"),
            ],
        )
    )
    state.story.add_scene(Scene(id="a_result", lines=[Line(text="A線")]))
    state.story.add_scene(Scene(id="b_result", lines=[Line(text="B線")]))
    engine = DialogueEngine(state)
    history = StateHistory()

    history.record(state, engine.start_scene("rollback_choice"))
    decision = engine.next_line()
    history.record(state, decision)
    branch_a = engine.choose("a")
    history.record(state, branch_a)
    assert history.rewind(state).kind == "choice"

    branch_b = engine.choose("b")
    assert branch_b.scene_id == "b_result"
    entry = state.read_log.replay_entry("rollback_choice")
    assert entry is not None
    assert entry.selected_choices == ["b"]
