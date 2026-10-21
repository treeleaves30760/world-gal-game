"""Display-only pseudo expansion never contaminates canonical localization."""
from __future__ import annotations

import copy
import os
from types import SimpleNamespace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from world_gal_game.core.localization import Localization
from world_gal_game.core.story_graph import (
    Choice,
    ChoiceCardDetail,
    ChoiceCardSpec,
    Condition,
    Effect,
    Line,
    Scene,
)
from world_gal_game.scenes.base import SceneContext
from world_gal_game.scenes.dialogue_scene import DialogueScene
from world_gal_game.ui.presentation_text import PresentationTextPolicy


def test_pseudo_expansion_is_deterministic_and_preserves_control_syntax() -> None:
    policy = PresentationTextPolicy.pseudo_expand(1.4)
    source = (
        "[b]Hello {player_name}[/b][w=0.5][[portrait:heroine]]\n"
        r"Escaped \[ bracket"
    )

    first = policy.apply(source)
    second = policy.apply(source)

    assert first == second
    assert "[b]" in first and "[/b]" in first
    assert "{player_name}" in first
    assert "[w=0.5]" in first
    assert "[[portrait:heroine]]" in first
    assert r"\[" in first
    assert first.count("\n") == source.count("\n")
    assert len(first) > len(source)


def test_identity_policy_is_exact_and_factor_is_bounded() -> None:
    source = "Exact [b]copy[/b]\n"
    assert PresentationTextPolicy().apply(source) == source

    for invalid in (0.99, 2.01, float("inf"), float("nan")):
        try:
            PresentationTextPolicy.pseudo_expand(invalid)
        except ValueError:
            pass
        else:  # pragma: no cover - assertion message is more useful than raises
            raise AssertionError(f"accepted invalid factor {invalid!r}")


def test_scene_context_expands_only_the_display_result() -> None:
    localization = Localization(ui={"probe": "Canonical {value}"})
    ctx = SceneContext(
        config=SimpleNamespace(reduce_motion=False),
        state=SimpleNamespace(),
        npcs=SimpleNamespace(),
        brain=SimpleNamespace(),
        dialogue=SimpleNamespace(),
        assets=SimpleNamespace(),
        fonts=SimpleNamespace(),
        theme=SimpleNamespace(),
        localization=localization,
        presentation_text=PresentationTextPolicy.pseudo_expand(1.35),
    )

    displayed = ctx.t("probe", value="State")

    assert localization.t("probe", value="State") == "Canonical State"
    assert displayed != "Canonical State"
    assert "Ståtë" in displayed
    assert ctx.presentation_text.describe() == {
        "profile": "pseudo-expand-v1",
        "factor": 1.35,
    }


def _open_runtime_scene(driver, scene: Scene) -> DialogueScene:
    app = driver.app
    app.state.story.add_scene(scene)
    app._authored_story_catalog.add_scene(scene.model_copy(deep=True))
    app._start_dialogue(scene.id)
    app.manager.commit_pending()
    driver.advance_frames(1)
    dialogue = app.manager.current
    assert isinstance(dialogue, DialogueScene)
    return dialogue


def _capture_line_boundary(
    policy: PresentationTextPolicy,
    *,
    scene_id: str,
) -> dict[str, object]:
    from world_gal_game.dev.driver import GameDriver

    driver = GameDriver(pack="demo_pack")
    try:
        app = driver.app
        app.presentation_text = policy
        app.ctx.presentation_text = policy
        canonical_markup = "[b]Canonical Alpha[/b]"
        canonical_speaker = "林清雪"
        effect_target = "presentation_policy_effect"
        dialogue = _open_runtime_scene(
            driver,
            Scene(
                id=scene_id,
                lines=[
                    Line(
                        speaker=canonical_speaker,
                        text=canonical_markup,
                        effects=[
                            Effect(
                                kind="set_flag",
                                target=effect_target,
                                value="canonical-value",
                            ),
                        ],
                    ),
                ],
            ),
        )
        assert dialogue.box is not None
        line = dialogue._current_line
        rollback = dialogue._history._stack[-1][1]
        display = {
            "speaker": dialogue.box.speaker,
            "text": dialogue.box.body.plain_text(),
        }

        # A completed expanded document must still produce a valid save. Its
        # renderer cursor is projected back to authored coordinates.
        dialogue.box.force_reveal()
        resume = dialogue.export_resume_context()
        assert resume is not None
        history = copy.deepcopy(app.state.dialogue_history.lines[-1])
        effect_value = app.state.events.get_flag(effect_target)

        dialogue._advance()
        replay_entry = app.state.read_log.replay_entry(scene_id)
        assert replay_entry is not None
        return {
            "display": display,
            "line": copy.deepcopy(line),
            "rollback": copy.deepcopy(rollback),
            "resume": resume,
            "history": history,
            "replay": replay_entry.model_dump(mode="json"),
            "effect_value": effect_value,
        }
    finally:
        driver.quit()
        # GameDriver owns pygame globally. Leave it usable for this module's
        # second capture and for whatever module pytest schedules next.
        pygame.display.init()
        pygame.font.init()
        pygame.display.set_mode((10, 10))


def test_dialogue_line_policy_changes_only_widget_copy() -> None:
    identity = _capture_line_boundary(
        PresentationTextPolicy(),
        scene_id="presentation_identity_line",
    )
    expanded = _capture_line_boundary(
        PresentationTextPolicy.pseudo_expand(1.35),
        scene_id="presentation_expanded_line",
    )
    canonical_markup = "[b]Canonical Alpha[/b]"
    canonical_plain = "Canonical Alpha"
    canonical_speaker = "林清雪"

    identity_display = identity["display"]
    expanded_display = expanded["display"]
    assert isinstance(identity_display, dict)
    assert isinstance(expanded_display, dict)
    assert identity_display["speaker"].encode("utf-8") == (
        canonical_speaker.encode("utf-8")
    )
    assert identity_display["text"].encode("utf-8") == (
        canonical_plain.encode("utf-8")
    )
    assert expanded_display["speaker"] != canonical_speaker
    assert expanded_display["text"] != canonical_plain
    assert len(expanded_display["speaker"]) > len(canonical_speaker)
    assert len(expanded_display["text"]) > len(canonical_plain)

    for capture in (identity, expanded):
        line = capture["line"]
        rollback = capture["rollback"]
        resume_line = capture["resume"]["presentation"]["line"]
        history = capture["history"]
        replay_line = capture["replay"]["presentations"][0]["line"]

        assert line.text == canonical_markup
        assert line.plain_text == canonical_plain
        assert line.speaker == canonical_speaker
        assert line.speaker_id == "heroine_1"
        assert rollback.line.text == canonical_markup
        assert rollback.line.speaker_id == "heroine_1"
        assert resume_line["text"] == canonical_markup
        assert resume_line["plain_text"] == canonical_plain
        assert resume_line["speaker"] == canonical_speaker
        assert resume_line["speaker_id"] == "heroine_1"
        assert capture["resume"]["text_plain_cursor"] == len(canonical_plain)
        assert history["text"] == canonical_plain
        assert history["speaker"] == canonical_speaker
        assert history["speaker_id"] == "heroine_1"
        assert replay_line["text"] == canonical_markup
        assert replay_line["plain_text"] == canonical_plain
        assert replay_line["speaker"] == canonical_speaker
        assert replay_line["speaker_id"] == "heroine_1"
        assert capture["effect_value"] == "canonical-value"


def test_dialogue_choice_policy_expands_label_reason_and_card_only() -> None:
    from world_gal_game.dev.driver import GameDriver

    driver = GameDriver(pack="demo_pack")
    try:
        app = driver.app
        policy = PresentationTextPolicy.pseudo_expand(1.35)
        app.presentation_text = policy
        app.ctx.presentation_text = policy
        scene_id = "presentation_expanded_choice"
        card = ChoiceCardSpec(
            title="Library Route",
            subtitle="Read together",
            badge="Quiet",
            image_alt="Library courtyard",
            details=[
                ChoiceCardDetail(label="Mood", text="Calm afternoon"),
            ],
            tags=["Study"],
            action="Go now",
        )
        locked_card = card.model_copy(update={"title": "Locked Route"})
        dialogue = _open_runtime_scene(
            driver,
            Scene(
                id=scene_id,
                lines=[Line(text="Choose a route")],
                choices=[
                    Choice(
                        id="library",
                        text="Visit Library",
                        card=card,
                        effects=[
                            Effect(
                                kind="set_flag",
                                target="choice_policy_effect",
                                value="chosen",
                            ),
                        ],
                    ),
                    Choice(
                        id="locked",
                        text="Locked Path",
                        requires=[
                            Condition(kind="flag", target="missing_gate"),
                        ],
                        card=locked_card,
                    ),
                ],
            ),
        )
        assert dialogue.box is not None
        dialogue.box.force_reveal()
        dialogue._advance()
        assert dialogue.choices is not None
        assert dialogue._current_presentation is not None
        canonical = dialogue._current_presentation
        rows = dialogue.choices._rows

        assert rows[0][0] == canonical.choices[0].id == "library"
        assert rows[0][1] != canonical.choices[0].text
        assert rows[1][3] != canonical.choices[1].reason
        assert rows[0][4] is not canonical.choices[0].card
        assert rows[0][4].title != canonical.choices[0].card.title
        assert rows[0][4].subtitle != canonical.choices[0].card.subtitle
        assert rows[0][4].badge != canonical.choices[0].card.badge
        assert rows[0][4].image_alt != canonical.choices[0].card.image_alt
        assert rows[0][4].details[0].label != (
            canonical.choices[0].card.details[0].label
        )
        assert rows[0][4].details[0].text != (
            canonical.choices[0].card.details[0].text
        )
        assert rows[0][4].tags[0] != canonical.choices[0].card.tags[0]
        assert rows[0][4].action != canonical.choices[0].card.action

        resume = dialogue.export_resume_context()
        assert resume is not None
        saved_choices = resume["presentation"]["choices"]
        assert saved_choices[0]["text"] == "Visit Library"
        assert saved_choices[0]["card"] == card.model_dump(mode="json")
        assert saved_choices[1]["text"] == "Locked Path"
        assert saved_choices[1]["card"] == locked_card.model_dump(mode="json")
        assert saved_choices[1]["reason"] == canonical.choices[1].reason
        rollback = dialogue._history._stack[-1][1]
        assert rollback.choices[0].text == "Visit Library"
        assert rollback.choices[0].card == card

        dialogue._on_choose("library")
        replay = app.state.read_log.replay_entry(scene_id)
        assert replay is not None
        replay_choice = replay.presentations[-1]["choices"]
        assert replay_choice[0]["text"] == "Visit Library"
        assert replay_choice[0]["card"] == card.model_dump(mode="json")
        assert replay_choice[1]["text"] == "Locked Path"
        assert replay_choice[1]["card"] == locked_card.model_dump(mode="json")
        assert replay.selected_choices == ["library"]
        assert app.state.events.get_flag("choice_policy_effect") == "chosen"
    finally:
        driver.quit()
        pygame.display.init()
        pygame.font.init()
        pygame.display.set_mode((10, 10))


def test_expanded_multiline_wait_resume_uses_canonical_save_coordinates() -> None:
    from world_gal_game.dev.driver import GameDriver

    driver = GameDriver(pack="demo_pack")
    try:
        app = driver.app
        policy = PresentationTextPolicy.pseudo_expand(1.35)
        app.presentation_text = policy
        app.ctx.presentation_text = policy
        scene_id = "presentation_expanded_resume"
        canonical_markup = "First\nSec[w=0.2]ond"
        canonical_plain = "First\nSecond"
        dialogue = _open_runtime_scene(
            driver,
            Scene(
                id=scene_id,
                lines=[
                    Line(
                        text=canonical_markup,
                        effects=[
                            Effect(
                                kind="increment_flag",
                                target="presentation_resume_effect_count",
                                value=1,
                            ),
                        ],
                    ),
                ],
            ),
        )
        assert dialogue.box is not None
        dialogue.box.force_reveal()
        displayed = dialogue.box.body.plain_text()
        resume = dialogue.export_resume_context()

        assert resume is not None
        assert resume["presentation"]["line"]["text"] == canonical_markup
        assert resume["presentation"]["line"]["plain_text"] == canonical_plain
        assert resume["text_plain_cursor"] == len(canonical_plain)
        assert resume["text_wait_timeline"] == {
            "consumed_plain_positions": [9],
            "active": None,
        }
        assert displayed == policy.apply(canonical_plain)
        assert app.state.events.get_flag(
            "presentation_resume_effect_count",
        ) == 1
        assert len(app.state.dialogue_history.lines) == 1
        assert len(app.state.read_log.replay_archive[scene_id].presentations) == 1

        assert dialogue.restore_resume_context(resume) is True
        assert dialogue.box.body.plain_text() == displayed
        assert dialogue.box.body.fully_revealed() is True
        assert app.state.events.get_flag(
            "presentation_resume_effect_count",
        ) == 1
        assert len(app.state.dialogue_history.lines) == 1
        assert len(app.state.read_log.replay_archive[scene_id].presentations) == 1
        restored = dialogue.export_resume_context()
        assert restored is not None
        assert restored["presentation"]["line"]["text"] == canonical_markup
        assert restored["text_plain_cursor"] == len(canonical_plain)
        assert restored["text_wait_timeline"] == resume["text_wait_timeline"]
    finally:
        driver.quit()
        pygame.display.init()
        pygame.font.init()
        pygame.display.set_mode((10, 10))
