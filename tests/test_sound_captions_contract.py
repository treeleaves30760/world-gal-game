"""Core sound-caption data, persistence, and replay trust boundaries."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from world_gal_game.config import EngineConfig
from world_gal_game.core.event_log import DialogueHistory
from world_gal_game.core.game_state import GameState
from world_gal_game.core.map_system import Location
from world_gal_game.core.read_log import ReadLog, ReplayArchiveEntry
from world_gal_game.core.story_graph import (
    Condition,
    Line,
    Scene,
    SoundCaptionSpec,
)
from world_gal_game.dialogue.dialogue_engine import (
    DialogueEngine,
    LinePresentation,
    ScenePresentation,
    presentation_from_dict,
    presentation_to_dict,
    rebind_resume_choice_presentations,
    replay_presentation_to_dict,
    resume_matches_state,
    resume_presentations_for_state,
)
from world_gal_game.dialogue.replay_engine import (
    PresentationTraceDialogueEngine,
)
from world_gal_game.dev.session_server import SessionServer
from world_gal_game.headless import HeadlessSession
from world_gal_game.npc.llm_brain import EchoBrain
from world_gal_game.npc.npc_base import NPC, NPCRegistry


def _caption(kind: str = "sfx", text: str = "門外傳來三下敲門聲") -> dict:
    return {"kind": kind, "text": text}


def _line_payload(
    *,
    captions: list[dict[str, str]] | None = None,
) -> dict:
    return replay_presentation_to_dict(
        ScenePresentation(
            kind="line",
            scene_id="memory",
            line=LinePresentation(
                speaker=None,
                text="原文",
                plain_text="原文",
                sound_captions=[
                    SoundCaptionSpec.model_validate(item)
                    for item in (captions or [])
                ],
                scene_id="memory",
                line_index=0,
                total_lines=1,
            ),
        )
    )


def _read_log_payload(presentation: dict) -> dict:
    return {
        "replay_archive": {
            "memory": {
                "scene_id": "memory",
                "presentations": [presentation],
                "complete": True,
            }
        }
    }


def test_sound_caption_schema_normalises_copy_and_bounds_line_count():
    captions = [
        SoundCaptionSpec(kind=kind, text="  遠方　傳來  鐘聲  ")
        for kind in ("ambient", "sfx", "music", "silence")
    ]

    assert [caption.text for caption in captions] == ["遠方 傳來 鐘聲"] * 4
    assert Line(text="正文", sound_captions=captions).sound_captions == captions

    with pytest.raises(ValidationError):
        Line(
            text="正文",
            sound_captions=[_caption(text=str(index)) for index in range(5)],
        )


@pytest.mark.parametrize(
    "caption",
    [
        {"kind": "voice", "text": "不支援的類型"},
        {"kind": "sfx", "text": "   "},
        {"kind": "sfx", "text": "x" * 241},
        {"kind": "sfx", "text": "敲門\x00聲"},
        {"kind": "sfx", "text": "敲門\n聲"},
        {"kind": "sfx", "text": "敲門聲", "unknown": True},
    ],
)
def test_sound_caption_schema_rejects_invalid_or_unsafe_copy(caption):
    with pytest.raises(ValidationError):
        SoundCaptionSpec.model_validate(caption)


def test_presented_caption_is_not_interpolated_and_history_is_json_native():
    state = GameState()
    state.player.name = "不應代入的名字"
    state.story.add_scene(
        Scene(
            id="captioned",
            lines=[
                Line(
                    text="正文會正常顯示",
                    sound_captions=[
                        _caption(text="{player_name} 身後傳來腳步聲")
                    ],
                )
            ],
        )
    )

    presentation = DialogueEngine(state).start_scene("captioned")

    assert presentation.line.sound_captions == [
        SoundCaptionSpec(
            kind="sfx",
            text="{player_name} 身後傳來腳步聲",
        )
    ]
    assert state.dialogue_history.lines[-1]["sound_captions"] == [
        _caption(text="{player_name} 身後傳來腳步聲")
    ]
    json.dumps(state.dialogue_history.model_dump(mode="json"), allow_nan=False)
    restored = GameState.model_validate_json(state.model_dump_json())
    assert restored.dialogue_history.lines[-1]["sound_captions"] == [
        _caption(text="{player_name} 身後傳來腳步聲")
    ]
    assert restored.story.scenes["captioned"].lines[0].sound_captions == (
        presentation.line.sound_captions
    )


def test_sound_captions_roundtrip_resume_and_compact_replay():
    original = ScenePresentation(
        kind="line",
        scene_id="memory",
        line=LinePresentation(
            speaker="旁白",
            text="燈熄滅了。",
            plain_text="燈熄滅了。",
            sound_captions=[
                SoundCaptionSpec(kind="silence", text="電流聲驟然停止")
            ],
            scene_id="memory",
            line_index=0,
            total_lines=1,
        ),
    )

    encoded = presentation_to_dict(original)
    compact = replay_presentation_to_dict(original)

    json.dumps(encoded, allow_nan=False)
    assert encoded["line"]["sound_captions"] == [
        _caption(kind="silence", text="電流聲驟然停止")
    ]
    assert compact["line"]["sound_captions"] == [
        _caption(kind="silence", text="電流聲驟然停止")
    ]
    assert presentation_from_dict(encoded) == original
    assert presentation_from_dict(compact) == original

    legacy_resume = presentation_to_dict(original)
    legacy_resume["line"].pop("sound_captions")
    assert presentation_from_dict(legacy_resume).line.sound_captions == []


def _headless_caption_session() -> HeadlessSession:
    state = GameState()
    state.story.add_scene(
        Scene(
            id="headless_captions",
            lines=[
                Line(
                    text="聲音先於人影出現。",
                    sound_captions=[
                        _caption(text="{player_name} 左側傳來敲門聲"),
                        _caption(kind="silence", text="敲門聲驟然停止"),
                    ],
                )
            ],
        )
    )
    return HeadlessSession(
        config=EngineConfig(),
        state=state,
        npcs=NPCRegistry(),
        brain=EchoBrain(),
        dialogue=DialogueEngine(state),
        meta={},
    )


def _expected_headless_captions() -> list[dict[str, str]]:
    return [
        _caption(text="{player_name} 左側傳來敲門聲"),
        _caption(kind="silence", text="敲門聲驟然停止"),
    ]


def test_headless_python_and_cli_script_emit_json_native_sound_captions():
    direct = _headless_caption_session().start_scene("headless_captions")
    scripted = _headless_caption_session().run_script([
        {"op": "start_scene", "scene": "headless_captions"},
    ])[0]
    preference_off = _headless_caption_session()
    preference_off.config.show_sound_captions = False
    still_semantic = preference_off.start_scene("headless_captions")

    for result in (direct, scripted, still_semantic):
        captions = result["presentation"]["line"]["sound_captions"]
        assert captions == _expected_headless_captions()
        json.dumps(result, allow_nan=False)


def test_headless_ndjson_emits_exact_sound_captions():
    server = SessionServer(_headless_caption_session())

    response = json.loads(server.handle(json.dumps({
        "op": "start_scene",
        "scene": "headless_captions",
    })))

    assert response["ok"] is True
    assert response["result"]["presentation"]["line"][
        "sound_captions"
    ] == _expected_headless_captions()


def _captioned_resume() -> tuple[GameState, dict, list[dict[str, str]]]:
    authored = [
        _caption(text="左側傳來敲門聲"),
        _caption(kind="silence", text="敲門聲驟然停止"),
    ]
    state = GameState()
    state.story.add_scene(
        Scene(
            id="captioned_resume",
            lines=[Line(text="門外沒有人。", sound_captions=authored)],
        )
    )
    presentation = DialogueEngine(state).start_scene("captioned_resume")
    resume = {
        "version": 1,
        "screen": "dialogue",
        "scene_id": "captioned_resume",
        "story_scene": "captioned_resume",
        "story_line_index": 1,
        "presentation": presentation_to_dict(presentation),
    }
    return state, resume, authored


@pytest.mark.parametrize(
    "mutation",
    [
        lambda captions: captions[0].__setitem__("text", "偽造的槍聲"),
        lambda captions: captions[0].__setitem__("kind", "music"),
        lambda captions: captions.clear(),
        lambda captions: captions.pop(),
        lambda captions: captions.reverse(),
        lambda captions: captions[0].__setitem__(
            "text", "  左側   傳來敲門聲  "
        ),
    ],
)
def test_resume_rejects_tampered_sound_captions(mutation):
    state, resume, _ = _captioned_resume()
    mutation(resume["presentation"]["line"]["sound_captions"])

    assert resume_matches_state(resume, state) is False


def test_legacy_v1_resume_without_caption_key_is_hydrated_and_accepted():
    state, resume, authored = _captioned_resume()
    resume["presentation"]["line"].pop("sound_captions")

    assert resume_matches_state(resume, state) is True
    current, _ = resume_presentations_for_state(resume, state)
    assert [
        caption.model_dump(mode="json")
        for caption in current.line.sound_captions
    ] == authored

    rebound = rebind_resume_choice_presentations(resume, state)
    assert "sound_captions" not in resume["presentation"]["line"]
    assert rebound["presentation"]["line"]["sound_captions"] == authored


def test_resume_rejects_tampered_last_line_sound_captions():
    state = GameState()
    state.story.add_scene(
        Scene(
            id="captioned_last_line",
            lines=[
                Line(text="第一句", sound_captions=[_caption()]),
                Line(text="第二句"),
            ],
        )
    )
    engine = DialogueEngine(state)
    first = engine.start_scene("captioned_last_line")
    current = engine.next_line()
    resume = {
        "version": 1,
        "screen": "dialogue",
        "scene_id": "captioned_last_line",
        "story_scene": "captioned_last_line",
        "story_line_index": 2,
        "presentation": presentation_to_dict(current),
        "last_line": presentation_to_dict(first),
    }
    assert resume_matches_state(resume, state) is True

    forged = deepcopy(resume)
    forged["last_line"]["line"]["sound_captions"][0]["text"] = (
        "偽造的爆炸聲"
    )
    assert resume_matches_state(forged, state) is False


@pytest.mark.parametrize(
    "mutation",
    [
        lambda captions: captions.extend([_caption()] * 4),
        lambda captions: captions[0].__setitem__("kind", "voice"),
        lambda captions: captions[0].__setitem__("text", "x" * 241),
        lambda captions: captions[0].__setitem__("text", "敲門\x00聲"),
        lambda captions: captions[0].__setitem__("unknown", True),
        lambda captions: captions[0].pop("text"),
    ],
)
def test_hostile_replay_sound_caption_shape_fails_closed(mutation):
    presentation = _line_payload(captions=[_caption()])
    mutation(presentation["line"]["sound_captions"])

    with pytest.raises(ValueError, match="sound caption"):
        ReadLog.model_validate(_read_log_payload(presentation))


@pytest.mark.parametrize(
    "captions",
    [
        _caption(),
        None,
        [{"kind": "sfx", "text": "  未正規化文字  "}],
    ],
)
def test_hostile_replay_sound_caption_container_fails_closed(captions):
    presentation = _line_payload()
    presentation["line"]["sound_captions"] = captions

    with pytest.raises(ValueError, match="sound caption"):
        ReadLog.model_validate(_read_log_payload(presentation))


def test_replay_sound_caption_must_exactly_match_authored_line():
    state = GameState()
    state.story.add_scene(
        Scene(
            id="memory",
            lines=[Line(text="原文", sound_captions=[_caption()])],
        )
    )
    engine = DialogueEngine(state)
    engine.start_scene("memory")
    engine.next_line()
    original_entry = state.read_log.replay_entry("memory")
    PresentationTraceDialogueEngine(state, original_entry)
    entry = original_entry.model_copy(deep=True)
    entry.presentations[0]["line"]["sound_captions"][0]["text"] = (
        "偽造的玻璃破裂聲"
    )

    with pytest.raises(ValueError, match="not authored"):
        PresentationTraceDialogueEngine(state, entry)


def test_legacy_replay_and_history_without_sound_captions_remain_valid():
    presentation = _line_payload()
    presentation["line"].pop("sound_captions", None)
    entry = ReplayArchiveEntry.model_validate(
        _read_log_payload(presentation)["replay_archive"]["memory"]
    )
    state = GameState()
    state.story.add_scene(Scene(id="memory", lines=[Line(text="原文")]))

    replay = PresentationTraceDialogueEngine(state, entry)
    assert replay.start_scene("memory").line.sound_captions == []

    history = DialogueHistory.model_validate(
        {"lines": [{"speaker": None, "text": "舊歷史紀錄"}]}
    )
    assert history.lines[0].get("sound_captions", []) == []


@pytest.mark.parametrize(
    "sentinel",
    ["none", "silence", "-", "  NONE  ", "\tSilence\n"],
)
def test_scene_and_line_bgm_sentinels_never_unlock_music_room(sentinel):
    for scene in (
        Scene(id="scene_level", bgm=sentinel, lines=[Line(text="正文")]),
        Scene(
            id="line_level",
            lines=[Line(text="正文", bgm=sentinel)],
        ),
    ):
        state = GameState()
        state.story.add_scene(scene)
        DialogueEngine(state).start_scene(scene.id)
        assert state.music_room.unlocked == set()


def test_real_bgm_still_unlocks_music_room():
    state = GameState()
    state.story.add_scene(
        Scene(
            id="real_music",
            bgm="assets/bgm/scene.ogg",
            lines=[Line(text="正文", bgm="assets/bgm/line.ogg")],
        )
    )

    DialogueEngine(state).start_scene("real_music")

    assert state.music_room.unlocked == {
        "assets/bgm/scene.ogg",
        "assets/bgm/line.ogg",
    }


def _completed_audio_entry(
    scene: Scene,
    *,
    carry_bgm: str | None = None,
    carry_ambient: str | None = None,
) -> tuple[GameState, ReplayArchiveEntry]:
    state = GameState()
    if carry_bgm is not None:
        state.meta["current_bgm"] = carry_bgm
    if carry_ambient is not None:
        state.meta["current_ambient"] = carry_ambient
    if carry_bgm is not None or carry_ambient is not None:
        # Historical carry remains valid only when the currently installed
        # authored catalogue can still produce that decoder path.
        state.story.add_scene(Scene(
            id=f"{scene.id}_audio_authority",
            bgm=carry_bgm,
            ambient=carry_ambient,
        ))
    state.story.add_scene(scene)
    engine = DialogueEngine(state)
    presentation = engine.start_scene(scene.id)
    while presentation.kind == "line":
        presentation = engine.next_line()
    entry = state.read_log.replay_entry(scene.id)
    assert entry is not None
    return state, entry


@pytest.mark.parametrize(
    ("case", "field", "scene_audio", "line_audio", "forged", "error"),
    [
        pytest.param(
            "scene real track",
            "bgm",
            {"bgm": "assets/bgm/scene_theme.ogg"},
            {},
            "assets/bgm/forged.ogg",
            "BGM is not authored",
            id="scene-real-bgm",
        ),
        pytest.param(
            "scene real bed",
            "ambient",
            {"ambient": "assets/sfx/scene_rain.ogg"},
            {},
            "assets/sfx/forged_wind.ogg",
            "ambient is not authored",
            id="scene-real-ambient",
        ),
        pytest.param(
            "sakuraoka scene silence",
            "bgm",
            {"bgm": "silence"},
            {},
            "assets/bgm/forged.ogg",
            "BGM is not authored",
            id="scene-silence-bgm",
        ),
        pytest.param(
            "sakuraoka scene none",
            "bgm",
            {"bgm": "none"},
            {},
            "assets/bgm/forged.ogg",
            "BGM is not authored",
            id="scene-none-bgm",
        ),
        pytest.param(
            "sakuraoka scene dash silence",
            "bgm",
            {"bgm": "-"},
            {},
            "assets/bgm/forged.ogg",
            "BGM is not authored",
            id="scene-dash-bgm",
        ),
        pytest.param(
            "scene sentinel remains exact",
            "bgm",
            {"bgm": "  Silence  "},
            {},
            "silence",
            "BGM is not authored",
            id="scene-normalized-sentinel-mutation-bgm",
        ),
        pytest.param(
            "sakuraoka scene silence",
            "ambient",
            {"ambient": "none"},
            {},
            "assets/sfx/forged_wind.ogg",
            "ambient is not authored",
            id="scene-silence-ambient",
        ),
        pytest.param(
            "sakuraoka scene silence",
            "ambient",
            {"ambient": "silence"},
            {},
            "assets/sfx/forged_wind.ogg",
            "ambient is not authored",
            id="scene-word-silence-ambient",
        ),
        pytest.param(
            "sakuraoka scene dash silence",
            "ambient",
            {"ambient": "-"},
            {},
            "assets/sfx/forged_wind.ogg",
            "ambient is not authored",
            id="scene-dash-ambient",
        ),
        pytest.param(
            "line override",
            "bgm",
            {"bgm": "assets/bgm/scene_theme.ogg"},
            {"bgm": "assets/bgm/line_sting.ogg"},
            "assets/bgm/scene_theme.ogg",
            "BGM is not authored",
            id="line-override-bgm",
        ),
        pytest.param(
            "line override",
            "ambient",
            {"ambient": "assets/sfx/scene_rain.ogg"},
            {"ambient": "assets/sfx/line_hum.ogg"},
            "assets/sfx/scene_rain.ogg",
            "ambient is not authored",
            id="line-override-ambient",
        ),
    ],
)
def test_replay_effective_audio_rejects_structurally_valid_hostile_mutation(
    case,
    field,
    scene_audio,
    line_audio,
    forged,
    error,
):
    state, entry = _completed_audio_entry(
        Scene(
            id=f"audio_provenance_{field}",
            lines=[Line(text=case, **line_audio)],
            **scene_audio,
        )
    )
    hostile_payload = entry.model_dump(mode="json")
    hostile_payload["presentations"][0]["line"][field] = forged
    hostile_entry = ReplayArchiveEntry.model_validate(hostile_payload)

    with pytest.raises(ValueError, match=error):
        PresentationTraceDialogueEngine(state, hostile_entry)


@pytest.mark.parametrize(
    ("field", "carry", "forged"),
    [
        (
            "bgm",
            "assets/bgm/previous_scene.ogg",
            "assets/bgm/other_previous_scene.ogg",
        ),
        (
            "ambient",
            "assets/sfx/previous_room.ogg",
            "assets/sfx/other_previous_room.ogg",
        ),
    ],
)
def test_replay_rejects_unowned_historical_audio_carry(
    field,
    carry,
    forged,
):
    carry_kwargs = {
        "carry_bgm": carry if field == "bgm" else None,
        "carry_ambient": carry if field == "ambient" else None,
    }
    scene_id = f"pure_carry_{field}"
    state, entry = _completed_audio_entry(
        Scene(id=scene_id, lines=[Line(text="沿用前景音")]),
        **carry_kwargs,
    )
    replay = PresentationTraceDialogueEngine(state, entry)
    line = replay.start_scene(scene_id).line
    assert getattr(line, field) == carry

    hostile_payload = entry.model_dump(mode="json")
    hostile_payload["presentations"][0]["line"][field] = forged
    historical_entry = ReplayArchiveEntry.model_validate(hostile_payload)

    with pytest.raises(ValueError, match="inherited .* is not authored"):
        PresentationTraceDialogueEngine(state, historical_entry)


def test_replay_accepts_location_authored_inherited_ambient_only():
    ambient = "assets/sfx/campus-courtyard.ogg"
    state = GameState()
    state.map.add_location(Location(
        id="courtyard",
        name="中庭",
        ambient=ambient,
    ))
    state.story.add_scene(Scene(
        id="location_ambient_memory",
        location="courtyard",
        lines=[Line(text="風穿過樹葉。")],
    ))
    engine = DialogueEngine(state)
    engine.start_scene("location_ambient_memory")
    engine.next_line()
    entry = state.read_log.replay_entry("location_ambient_memory")
    assert entry is not None

    replay = PresentationTraceDialogueEngine(
        state,
        entry,
        authored_map=state.map,
    )
    assert replay.start_scene("location_ambient_memory").line.ambient == ambient

    hostile = entry.model_copy(deep=True)
    hostile.presentations[0]["line"]["ambient"] = (
        "assets/sfx/save-only-ambient.ogg"
    )
    with pytest.raises(ValueError, match="inherited ambient is not authored"):
        PresentationTraceDialogueEngine(
            state,
            hostile,
            authored_map=state.map,
        )


@pytest.mark.parametrize(
    ("authored_speaker", "archived_speaker"),
    [
        (None, "Heroine A"),
        ("Heroine A", "Heroine B"),
    ],
)
def test_replay_speaker_cannot_select_a_different_authored_npc(
    authored_speaker,
    archived_speaker,
):
    state = GameState()
    npcs = NPCRegistry()
    npcs.add(NPC(
        id="heroine_a",
        name="Heroine A",
        portrait_backend="hostile_backend_a",
    ))
    npcs.add(NPC(
        id="heroine_b",
        name="Heroine B",
        portrait_backend="hostile_backend_b",
    ))
    state.story.add_scene(Scene(
        id="speaker_provenance",
        lines=[Line(speaker=authored_speaker, text="原本的台詞")],
    ))
    engine = DialogueEngine(state)
    engine.start_scene("speaker_provenance")
    engine.next_line()
    entry = state.read_log.replay_entry("speaker_provenance")
    assert entry is not None
    hostile = entry.model_copy(deep=True)
    hostile.presentations[0]["line"]["speaker"] = archived_speaker

    with pytest.raises(ValueError, match="unauthorised NPC"):
        PresentationTraceDialogueEngine(
            state,
            hostile,
            authored_npcs=npcs,
        )


def test_replay_speaker_id_is_authorized_and_legacy_archive_is_hydrated():
    state = GameState()
    npcs = NPCRegistry()
    npcs.add(NPC(id="heroine_a", name="Heroine A"))
    npcs.add(NPC(id="heroine_b", name="Heroine B"))
    state.story.add_scene(Scene(
        id="stable_replay_speaker",
        lines=[Line(speaker="Heroine A", text="Archived line")],
    ))
    engine = DialogueEngine(
        state,
        speaker_id_resolver=lambda speaker: {
            "Heroine A": "heroine_a",
            "Heroine B": "heroine_b",
        }.get(speaker),
    )
    engine.start_scene("stable_replay_speaker")
    engine.next_line()
    entry = state.read_log.replay_entry("stable_replay_speaker")
    assert entry is not None
    assert entry.presentations[0]["line"]["speaker_id"] == "heroine_a"

    hostile = entry.model_copy(deep=True)
    hostile.presentations[0]["line"]["speaker_id"] = "heroine_b"
    with pytest.raises(ValueError, match="speaker_id"):
        PresentationTraceDialogueEngine(
            state,
            hostile,
            authored_npcs=npcs,
        )

    legacy = entry.model_copy(deep=True)
    legacy.presentations[0]["line"].pop("speaker_id")
    replay = PresentationTraceDialogueEngine(
        state,
        legacy,
        authored_npcs=npcs,
    )
    presentation = replay.start_scene("stable_replay_speaker")
    assert presentation.line.speaker == "Heroine A"
    assert presentation.line.speaker_id == "heroine_a"


def test_replay_retains_historical_non_npc_speaker_copy():
    state = GameState()
    state.player.name = "存檔時的玩家名"
    state.story.add_scene(Scene(
        id="historical_player_name",
        lines=[Line(speaker="{player_name}", text="我來了。")],
    ))
    engine = DialogueEngine(state)
    engine.start_scene("historical_player_name")
    engine.next_line()
    entry = state.read_log.replay_entry("historical_player_name")
    assert entry is not None

    state.player.name = "現在的玩家名"
    replay = PresentationTraceDialogueEngine(
        state,
        entry,
        authored_npcs=NPCRegistry(),
    )
    assert (
        replay.start_scene("historical_player_name").line.speaker
        == "存檔時的玩家名"
    )


@pytest.mark.parametrize(
    ("field", "scene_value", "override", "forged"),
    [
        (
            "bgm",
            "assets/bgm/scene_theme.ogg",
            "assets/bgm/line_override.ogg",
            "assets/bgm/forged.ogg",
        ),
        (
            "ambient",
            "assets/sfx/scene_rain.ogg",
            "assets/sfx/line_hum.ogg",
            "assets/sfx/forged_wind.ogg",
        ),
    ],
)
def test_replay_statefully_carries_a_presented_line_audio_override(
    field,
    scene_value,
    override,
    forged,
):
    scene_id = f"stateful_override_{field}"
    state, entry = _completed_audio_entry(
        Scene(
            id=scene_id,
            lines=[
                Line(text="scene directive"),
                Line(text="line override", **{field: override}),
                Line(text="legal carry"),
            ],
            **{field: scene_value},
        )
    )

    replay = PresentationTraceDialogueEngine(state, entry)
    observed = [getattr(replay.start_scene(scene_id).line, field)]
    observed.append(getattr(replay.next_line().line, field))
    observed.append(getattr(replay.next_line().line, field))
    assert observed == [scene_value, override, override]

    hostile_payload = entry.model_dump(mode="json")
    hostile_payload["presentations"][-1]["line"][field] = forged
    hostile_entry = ReplayArchiveEntry.model_validate(hostile_payload)
    with pytest.raises(ValueError, match="not authored"):
        PresentationTraceDialogueEngine(state, hostile_entry)


@pytest.mark.parametrize(
    ("field", "scene_value", "skipped_override"),
    [
        (
            "bgm",
            "assets/bgm/scene_theme.ogg",
            "assets/bgm/skipped_override.ogg",
        ),
        (
            "ambient",
            "assets/sfx/scene_rain.ogg",
            "assets/sfx/skipped_override.ogg",
        ),
    ],
)
def test_replay_conditional_skipped_audio_override_never_updates_effective_state(
    field,
    scene_value,
    skipped_override,
):
    scene_id = f"skipped_override_{field}"
    state, entry = _completed_audio_entry(
        Scene(
            id=scene_id,
            lines=[
                Line(
                    text="hidden audio directive",
                    requires=[Condition(kind="flag", target="never_set")],
                    **{field: skipped_override},
                ),
                Line(text="visible scene carry"),
            ],
            **{field: scene_value},
        )
    )
    assert len(entry.presentations) == 1
    assert entry.presentations[0]["line"]["line_index"] == 1
    PresentationTraceDialogueEngine(state, entry)

    hostile_payload = entry.model_dump(mode="json")
    hostile_payload["presentations"][0]["line"][field] = skipped_override
    hostile_entry = ReplayArchiveEntry.model_validate(hostile_payload)
    with pytest.raises(ValueError, match="not authored"):
        PresentationTraceDialogueEngine(state, hostile_entry)


@pytest.mark.parametrize(
    ("field", "carry", "forged"),
    [
        (
            "bgm",
            "assets/bgm/previous_scene.ogg",
            "assets/bgm/forged_after_anchor.ogg",
        ),
        (
            "ambient",
            "assets/sfx/previous_room.ogg",
            "assets/sfx/forged_after_anchor.ogg",
        ),
    ],
)
def test_replay_unknown_historical_carry_is_anchored_after_first_visible_line(
    field,
    carry,
    forged,
):
    scene_id = f"anchored_carry_{field}"
    carry_kwargs = {
        "carry_bgm": carry if field == "bgm" else None,
        "carry_ambient": carry if field == "ambient" else None,
    }
    state, entry = _completed_audio_entry(
        Scene(
            id=scene_id,
            lines=[Line(text="anchor carry"), Line(text="continue carry")],
        ),
        **carry_kwargs,
    )
    PresentationTraceDialogueEngine(state, entry)

    hostile_payload = entry.model_dump(mode="json")
    hostile_payload["presentations"][1]["line"][field] = forged
    hostile_entry = ReplayArchiveEntry.model_validate(hostile_payload)
    with pytest.raises(ValueError, match="not authored"):
        PresentationTraceDialogueEngine(state, hostile_entry)
