"""Runtime story catalogs affect presentation, never story authority."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import (
    Choice,
    ChoiceCardDetail,
    ChoiceCardSpec,
    Line,
    Scene,
    SoundCaptionSpec,
)
from world_gal_game.dialogue.dialogue_engine import (
    DialogueEngine,
    presentation_to_dict,
    rebind_dialogue_history_localization,
    rebind_resume_choice_presentations,
)


class _Catalog:
    source = "zh-Hant"
    source_digest = "b" * 64

    def __init__(self, messages: dict[str, str], *, effective: str = "en"):
        self.messages = messages
        self.effective = effective

    def story(self, key: str, source: str, **_) -> str:
        return self.messages.get(key, source)


def test_line_loc_id_matches_the_extractor_key_segment_contract() -> None:
    assert Line(text="ok", loc_id="a" * 64).loc_id == "a" * 64
    for invalid in ("a" * 65, "has.dot", "中文", "_leading"):
        with pytest.raises(ValidationError):
            Line(text="bad", loc_id=invalid)


def test_story_catalog_localizes_every_explicit_presentation_field() -> None:
    state = GameState()
    scene = Scene(
        id="arrival",
        title="抵達",
        lines=[
            Line(
                loc_id="welcome",
                speaker="白若雪",
                text="[b]你好[/b]，{player_name}。",
                sound_captions=[
                    SoundCaptionSpec(kind="ambient", text="紙頁翻動。"),
                ],
            ),
        ],
        choices=[
            Choice(
                id="stay",
                text="留下",
                card=ChoiceCardSpec(
                    title="留在這裡",
                    subtitle="再聽一會",
                    badge="推薦",
                    image_alt="圖書館夜景",
                    details=[
                        ChoiceCardDetail(label="時間", text="傍晚"),
                    ],
                    tags=["安靜"],
                    action="確認留下",
                ),
            ),
        ],
    )
    state.story.add_scene(scene)
    state.player.name = "Alex"
    messages = {
        "arrival.title": "Arrival",
        "arrival.line.welcome": "[b]Hello[/b], {player_name}.",
        "arrival.line.welcome.speaker": "Lin Ruoxue",
        "arrival.line.welcome.sound_caption.0": "Pages rustle.",
        "arrival.choice.stay": "Stay",
        "arrival.choice.stay.card.title": "Stay Here",
        "arrival.choice.stay.card.subtitle": "Listen a little longer",
        "arrival.choice.stay.card.badge": "Recommended",
        "arrival.choice.stay.card.image_alt": "Library at night",
        "arrival.choice.stay.card.detail.0.label": "Time",
        "arrival.choice.stay.card.detail.0.text": "Evening",
        "arrival.choice.stay.card.tag.0": "Quiet",
        "arrival.choice.stay.card.action": "Confirm stay",
    }
    engine = DialogueEngine(
        state,
        localization=_Catalog(messages),
        speaker_id_resolver=lambda name: (
            "ruoxue" if name in {"白若雪", "Lin Ruoxue"} else None
        ),
    )

    line = engine.start_scene("arrival")
    assert line.title == "Arrival"
    assert line.line is not None
    assert line.line.speaker == "Lin Ruoxue"
    assert line.line.speaker_id == "ruoxue"
    assert line.line.text == "[b]Hello[/b], Alex."
    assert line.line.plain_text == "Hello, Alex."
    assert line.line.sound_captions[0].text == "Pages rustle."
    assert state.dialogue_history.lines[-1]["speaker_id"] == "ruoxue"
    assert state.dialogue_history.lines[-1]["text"] == "Hello, Alex."

    decision = engine.next_line()
    assert decision.title == "Arrival"
    assert [(choice.id, choice.text) for choice in decision.choices] == [
        ("stay", "Stay"),
    ]
    card = decision.choices[0].card
    assert card is not None
    assert card.title == "Stay Here"
    assert card.subtitle == "Listen a little longer"
    assert card.badge == "Recommended"
    assert card.image_alt == "Library at night"
    assert card.details[0].label == "Time"
    assert card.details[0].text == "Evening"
    assert card.tags == ["Quiet"]
    assert card.action == "Confirm stay"

    engine.choose("stay")
    assert state.events.filter(kind="choice")[-1].title == "Stay"
    # Canonical content is untouched; localized speaker copy cannot become an
    # identity, condition, transition or persistence authority.
    authored = state.story.get("arrival")
    assert authored is not None
    assert authored.title == "抵達"
    assert authored.lines[0].speaker == "白若雪"
    assert authored.lines[0].text == "[b]你好[/b]，{player_name}。"
    assert authored.choices[0].text == "留下"


def test_localized_display_collision_cannot_replace_authored_speaker_identity(
) -> None:
    state = GameState()
    state.story.add_scene(Scene(
        id="identity_collision",
        lines=[Line(speaker="Source Heroine", text="Hello.")],
    ))
    engine = DialogueEngine(
        state,
        localization=_Catalog({
            "identity_collision.line.0.speaker": "Other Heroine",
        }),
        speaker_id_resolver=lambda name: {
            "Source Heroine": "source_id",
            "Other Heroine": "other_id",
        }.get(name),
    )

    presentation = engine.start_scene("identity_collision")

    assert presentation.line is not None
    assert presentation.line.speaker == "Other Heroine"
    assert presentation.line.speaker_id == "source_id"
    assert state.dialogue_history.lines[-1]["speaker_id"] == "source_id"


def test_index_key_remains_backward_compatible_without_loc_id() -> None:
    state = GameState()
    state.story.add_scene(Scene(
        id="legacy",
        lines=[Line(text="來源")],
    ))
    engine = DialogueEngine(
        state,
        localization=_Catalog({"legacy.line.0": "Translation"}),
    )

    presentation = engine.start_scene("legacy")

    assert presentation.line is not None
    assert presentation.line.text == "Translation"


def test_history_rebind_restores_stable_id_from_authored_speaker() -> None:
    state = GameState()
    scene = Scene(
        id="history_identity",
        lines=[Line(speaker="Source Heroine", text="Source line")],
    )
    state.story.add_scene(scene)
    engine = DialogueEngine(
        state,
        speaker_id_resolver=lambda name: (
            "source_id" if name == "Source Heroine" else None
        ),
    )
    engine.start_scene("history_identity")
    saved = state.dialogue_history.model_copy(deep=True)
    saved.lines[-1]["speaker_id"] = "forged_or_stale_id"

    rebound = rebind_dialogue_history_localization(
        state.story,
        saved,
        state,
        localization=_Catalog({
            "history_identity.line.0.speaker": "Localized Heroine",
        }),
        speaker_id_resolver=lambda name: (
            "source_id" if name == "Source Heroine" else None
        ),
    )

    assert rebound.lines[-1]["speaker"] == "Localized Heroine"
    assert rebound.lines[-1]["speaker_id"] == "source_id"


def test_saved_choice_copy_rebinds_only_when_locale_identity_changes() -> None:
    state = GameState()
    scene = Scene(
        id="decision",
        title="抉擇",
        lines=[Line(text="請選擇。")],
        choices=[Choice(
            id="stay",
            text="留下",
            card=ChoiceCardSpec(title="留在這裡", action="確認"),
        )],
    )
    state.story.add_scene(scene)
    english = _Catalog({
        "decision.title": "Decision",
        "decision.line.0": "Choose.",
        "decision.choice.stay": "Stay",
        "decision.choice.stay.card.title": "Stay Here",
        "decision.choice.stay.card.action": "Confirm",
    })
    engine = DialogueEngine(state, localization=english)
    line = engine.start_scene("decision")
    choice = engine.next_line()
    resume = {
        "version": 1,
        "screen": "dialogue",
        "scene_id": "decision",
        "story_scene": "decision",
        "story_line_index": 1,
        "locale": "en",
        "locale_source_digest": "b" * 64,
        "presentation": presentation_to_dict(choice),
        "last_line": presentation_to_dict(line),
    }

    source = _Catalog({}, effective="zh-Hant")
    rebound_source = rebind_resume_choice_presentations(
        resume,
        state,
        localization=source,
    )
    row = rebound_source["presentation"]["choices"][0]
    assert rebound_source["presentation"]["title"] == "抉擇"
    assert row["text"] == "留下"
    assert row["card"]["title"] == "留在這裡"
    assert row["card"]["action"] == "確認"

    rebound_english = rebind_resume_choice_presentations(
        rebound_source,
        state,
        localization=english,
    )
    row = rebound_english["presentation"]["choices"][0]
    assert rebound_english["presentation"]["title"] == "Decision"
    assert row["text"] == "Stay"
    assert row["card"]["title"] == "Stay Here"
    assert row["card"]["action"] == "Confirm"
