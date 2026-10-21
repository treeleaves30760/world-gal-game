"""Dialogue saves restore presentation without replaying narrative work."""
from __future__ import annotations

import copy
import json
import os
import types

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from world_gal_game.core.portrait_spec import PortraitSpec
from world_gal_game.core.map_system import Location
from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import (
    Choice,
    ChoiceCardSpec,
    Effect,
    Line,
    Scene,
    SoundCaptionSpec,
)
from world_gal_game.dialogue.dialogue_engine import (
    LinePresentation,
    ScenePresentation,
    presentation_from_dict,
    presentation_to_dict,
    resume_presentations_for_state,
)


@pytest.fixture
def driver(tmp_path):
    from world_gal_game.dev.driver import GameDriver

    drv = GameDriver(pack="demo_pack")
    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    drv.app.config.save_dir = types.MethodType(
        lambda self, _path=save_dir: _path,
        drv.app.config,
    )
    bridge = drv.app.state.meta.get("__autosave_config__")
    if isinstance(bridge, dict):
        bridge["save_dir"] = save_dir
    drv.app.config.autosave_enabled = False
    drv._resume_save_dir = save_dir
    yield drv
    drv.quit()


def _open(driver, scene: Scene):
    app = driver.app
    app.state.story.add_scene(scene)
    # Test-authored runtime scenes must join the same trusted catalogue a pack
    # or registered runtime authoring API would supply in production. Saves
    # themselves are never allowed to extend this authority.
    app._authored_story_catalog.add_scene(scene.model_copy(deep=True))
    app._start_dialogue(scene.id)
    app.manager.commit_pending()
    driver.advance_frames(1)
    dialogue = app.manager.current
    assert type(dialogue).__name__ == "DialogueScene"
    return dialogue


class _ResumeLocale:
    source = "zh-Hant"
    default = "zh-Hant"
    source_digest = "a" * 64

    def __init__(self, effective: str, messages: dict[str, str] | None = None):
        self.effective = effective
        self.messages = messages or {}

    def story(self, key: str, source: str, **_) -> str:
        return self.messages.get(key, source)

    def t(self, key: str, default: str | None = None, **_) -> str:
        return default if default is not None else key


def _bind_resume_locale(app, localization: _ResumeLocale) -> None:
    app.localization = localization
    app.ctx.localization = localization
    app.dialogue.localization = localization


@pytest.mark.parametrize("nvl_mode", [False, True], ids=["adv", "nvl"])
def test_localized_quicksave_restores_and_cross_locale_rebuilds_without_effects(
    driver,
    nvl_mode,
) -> None:
    app = driver.app
    app.config.nvl_mode = nvl_mode
    scene_id = f"localized_resume_{'nvl' if nvl_mode else 'adv'}"
    english = _ResumeLocale("en", {
        f"{scene_id}.title": "Localized Resume",
        f"{scene_id}.line.greeting": "Hello, {player_name}.",
        f"{scene_id}.line.greeting.speaker": "Guide",
        f"{scene_id}.line.greeting.sound_caption.0": "Pages rustle.",
    })
    source = _ResumeLocale("zh-Hant")
    _bind_resume_locale(app, english)
    scene = Scene(
        id=scene_id,
        title="語系存檔",
        lines=[
            Line(
                loc_id="greeting",
                speaker="嚮導",
                text="你好，{player_name}。",
                sound_captions=[
                    SoundCaptionSpec(kind="ambient", text="紙頁翻動。"),
                ],
                effects=[Effect(kind="advance_time", value=1)],
            ),
            Line(text="下一句"),
        ],
    )
    dialogue = _open(driver, scene)
    assert dialogue._current_line.speaker == "Guide"
    assert dialogue._current_line.speaker_id == "嚮導"
    assert dialogue._current_line.text == "Hello, 玩家."
    assert dialogue._current_line.sound_captions[0].text == "Pages rustle."
    saved_time = app.state.time.model_dump(mode="json")
    saved_history = app.state.dialogue_history.model_dump(mode="json")

    resume = dialogue.export_resume_context()
    assert resume is not None
    assert resume["locale"] == "en"
    assert resume["locale_source_digest"] == "a" * 64
    assert app._resume_matches_state(
        resume,
        app.state,
        authored_story=app._authored_story_catalog,
        authored_npcs=app.npcs,
        authored_map=app._authored_map_catalog,
        localization=english,
    ) is True

    app._quicksave()
    dialogue.box.force_reveal()
    dialogue._advance()
    app._quickload()
    driver.advance_frames(2)
    restored = app.manager.current
    assert restored._current_line.speaker == "Guide"
    assert restored._current_line.speaker_id == "嚮導"
    assert restored._current_line.text == "Hello, 玩家."
    assert restored._current_line.sound_captions[0].text == "Pages rustle."
    assert app.state.time.model_dump(mode="json") == saved_time
    assert app.state.dialogue_history.model_dump(mode="json") == saved_history

    # A title-safe locale switch rebuilds only renderer copy. It does not call
    # start_scene/next_line, re-run the line effect, or retain the old-language
    # NVL/typewriter timeline.
    _bind_resume_locale(app, source)
    restored.box.force_reveal()
    restored._advance()
    app._quickload()
    driver.advance_frames(2)
    rebuilt = app.manager.current
    assert rebuilt._current_line.speaker == "嚮導"
    assert rebuilt._current_line.speaker_id == "嚮導"
    assert rebuilt._current_line.text == "你好，玩家。"
    assert rebuilt._current_line.sound_captions[0].text == "紙頁翻動。"
    assert app.state.time.model_dump(mode="json") == saved_time
    history = app.state.dialogue_history.lines[-1]
    assert history["speaker"] == "嚮導"
    assert history["speaker_id"] == "嚮導"
    assert history["text"] == "你好，玩家。"
    assert history["sound_captions"][0]["text"] == "紙頁翻動。"
    assert history["locale"] == "zh-Hant"
    if nvl_mode:
        assert rebuilt._nvl_transcript == [{
            "speaker": "嚮導",
            "speaker_id": "嚮導",
            "text": "你好，玩家。",
        }]


@pytest.mark.parametrize("sentinel", ("none", "silence", "-"))
def test_line_ambient_silence_sentinel_stops_normal_playback(
    driver, sentinel,
):
    app = driver.app
    played: list[str] = []
    stopped: list[dict] = []
    app.assets.play_ambient = lambda path, **_: played.append(path)
    app.assets.stop_ambient = lambda **kwargs: stopped.append(kwargs)

    dialogue = _open(
        driver,
        Scene(
            id=f"line_ambient_silence_{sentinel}",
            lines=[Line(text="the room tone stops here", ambient=sentinel)],
        ),
    )

    assert dialogue._current_line.ambient == sentinel
    assert app.state.meta["current_ambient"] == sentinel
    assert played == []
    assert stopped == [{}]


def test_scene_ambient_silence_sentinel_stops_on_first_line(driver):
    app = driver.app
    played: list[str] = []
    stopped: list[dict] = []
    app.assets.play_ambient = lambda path, **_: played.append(path)
    app.assets.stop_ambient = lambda **kwargs: stopped.append(kwargs)

    dialogue = _open(
        driver,
        Scene(
            id="scene_ambient_silence",
            ambient="silence",
            lines=[Line(text="the scene opens without an ambient bed")],
        ),
    )

    assert dialogue._current_line.ambient == "silence"
    assert app.state.meta["current_ambient"] == "silence"
    assert played == []
    assert stopped == [{}]


def test_location_ambient_is_trusted_by_resume_authority(driver):
    app = driver.app
    location = Location(
        id="resume_location_ambient",
        name="Resume room",
        ambient="assets/sfx/location-room.ogg",
    )
    app.state.map.add_location(location)
    app._authored_map_catalog.add_location(location.model_copy(deep=True))
    dialogue = _open(
        driver,
        Scene(
            id="resume_location_ambient_scene",
            location=location.id,
            lines=[Line(text="This line inherits its room tone.")],
        ),
    )

    resume = dialogue.export_resume_context()

    assert resume is not None
    assert resume["current_ambient"] == location.ambient
    assert resume["presentation"]["line"]["ambient"] == location.ambient
    assert app._resume_matches_loaded_state(resume) is True


def test_quickload_restores_ambient_silence_without_restarting_bed(driver):
    app = driver.app
    played: list[str] = []
    stopped: list[dict] = []
    app.assets.play_ambient = lambda path, **_: played.append(path)
    app.assets.stop_ambient = lambda **kwargs: stopped.append(kwargs)

    dialogue = _open(
        driver,
        Scene(
            id="resume_ambient_silence",
            ambient="none",
            lines=[
                Line(text="save this silent room"),
                Line(
                    text="disturb the live screen with room tone",
                    ambient="assets/sfx/room.ogg",
                ),
            ],
        ),
    )
    assert stopped == [{}]
    app._quicksave()
    raw = json.loads(
        (
            driver._resume_save_dir
            / f"{app.config.quicksave_slot}.json"
        ).read_text(encoding="utf-8")
    )
    assert raw["_resume"]["current_ambient"] == "none"
    assert raw["_resume"]["presentation"]["line"]["ambient"] == "none"

    dialogue.box.force_reveal()
    dialogue._advance()
    assert played == ["assets/sfx/room.ogg"]
    played.clear()
    stopped.clear()

    app._quickload()
    driver.advance_frames(2)

    restored = app.manager.current
    assert type(restored).__name__ == "DialogueScene"
    assert restored._current_line.text == "save this silent room"
    assert played == []
    assert stopped == [{"fade_ms": 0}]


def test_presentation_serializer_preserves_portrait_specs():
    spec = PortraitSpec(
        character="heroine",
        expression="smile",
        slot="left",
        offset=(12, -4),
        backend="breath",
        backend_args={"period": 3.2},
    )
    original = ScenePresentation(
        kind="line",
        scene_id="serialization_probe",
        background="assets/backgrounds/room.png",
        line=LinePresentation(
            speaker="Heroine",
            speaker_id="heroine",
            text="[b]Hello[/b]",
            plain_text="Hello",
            portrait=spec,
            portraits=[spec],
            line_index=3,
            total_lines=9,
        ),
    )

    encoded = presentation_to_dict(original)
    assert encoded["line"]["speaker_id"] == "heroine"
    # This is the same strict property SaveManager requires of _resume.
    json.dumps(encoded, allow_nan=False)
    restored = presentation_from_dict(encoded)

    assert restored == original
    assert isinstance(restored.line.portrait, PortraitSpec)
    assert isinstance(restored.line.portraits[0], PortraitSpec)


def test_quickload_returns_to_same_line_without_replaying_effect(driver):
    app = driver.app
    scene = Scene(
        id="resume_line_probe",
        background="assets/backgrounds/resume_probe.png",
        lines=[
            Line(
                speaker="Tester",
                text="save this exact line",
                portrait="assets/characters/resume_probe.png",
                portrait_pos="left",
                cg="assets/cg/resume_probe.png",
                effects=[Effect(kind="advance_time", value=1)],
            ),
            Line(text="the following line"),
        ],
    )
    dialogue = _open(driver, scene)
    dialogue.box.force_reveal()

    saved_time = app.state.time.model_dump(mode="json")
    saved_history = app.state.dialogue_history.model_dump(mode="json")
    assert app.state.story.current_line_index == 1
    assert dialogue._current_line.text == "save this exact line"
    assert dialogue.background_path == scene.background
    assert dialogue.cg_surface_path == "assets/cg/resume_probe.png"
    assert dialogue._slot_resume["left"]["kind"] == "legacy"

    app._quicksave()
    slot = app.config.quicksave_slot
    raw = json.loads(
        (driver._resume_save_dir / f"{slot}.json").read_text(
            encoding="utf-8")
    )
    assert raw["_resume"]["screen"] == "dialogue"
    assert raw["_resume"]["story_line_index"] == 1

    # Move the live scene on, then load the earlier presentation.
    dialogue._advance()
    assert dialogue._current_line.text == "the following line"
    app._quickload()
    driver.advance_frames(2)

    restored = app.manager.current
    assert type(restored).__name__ == "DialogueScene"
    assert restored._current_line.text == "save this exact line"
    assert restored.box.fully_revealed() is True
    assert restored.background_path == scene.background
    assert restored.cg_surface_path == "assets/cg/resume_probe.png"
    assert restored._slot_resume["left"]["kind"] == "legacy"
    assert app.state.story.current_scene == scene.id
    assert app.state.story.current_line_index == 1
    # Loading redraws only. advance_time, dialogue-history push, hooks, and
    # read-log work from the source line were not executed a second time.
    assert app.state.time.model_dump(mode="json") == saved_time
    assert app.state.dialogue_history.model_dump(mode="json") == saved_history

    restored._advance()
    assert restored._current_line.text == "the following line"


@pytest.mark.parametrize("nvl_mode", [False, True], ids=["adv", "nvl"])
def test_active_inline_wait_resumes_with_exact_remaining_time(
    driver,
    nvl_mode,
):
    from world_gal_game.ui.nvl_box import NVLBox

    driver.app.config.nvl_mode = nvl_mode
    scene = Scene(
        id=f"resume_active_wait_{'nvl' if nvl_mode else 'adv'}",
        lines=[Line(text="序[w=2]續"), Line(text="下一句")],
    )
    dialogue = _open(driver, scene)
    body = (
        dialogue.box.entries[-1].body
        if isinstance(dialogue.box, NVLBox)
        else dialogue.box.body
    )
    wait_plain = next(iter(body._doc.waits()))
    wait_reveal = body.reveal_cursor_for_plain(wait_plain)
    body.set_reveal(wait_reveal)
    body.update(0.25)
    assert body._wait_left == pytest.approx(1.75)

    resume = dialogue.export_resume_context()
    assert resume is not None
    assert resume["text_wait_timeline"] == {
        "consumed_plain_positions": [wait_plain],
        "active": {
            "plain_position": wait_plain,
            "remaining_seconds": pytest.approx(1.75),
        },
    }
    driver.app._quicksave()
    raw = json.loads(
        (
            driver._resume_save_dir
            / f"{driver.app.config.quicksave_slot}.json"
        ).read_text(encoding="utf-8")
    )
    assert (
        raw["_resume"]["text_wait_timeline"]
        == resume["text_wait_timeline"]
    )

    body.force_reveal()
    dialogue._advance()
    assert dialogue._current_line.text == "下一句"
    driver.app._quickload()
    driver.app.manager.commit_pending()
    restored_dialogue = driver.app.manager.current
    assert type(restored_dialogue).__name__ == "DialogueScene"
    restored_body = (
        restored_dialogue.box.entries[-1].body
        if isinstance(restored_dialogue.box, NVLBox)
        else restored_dialogue.box.body
    )
    assert restored_body.reveal_chars == wait_reveal
    assert restored_body._wait_left == pytest.approx(1.75)

    restored_body.update(1.74)
    assert restored_body.reveal_chars == wait_reveal
    assert restored_body._wait_left == pytest.approx(0.01)
    restored_body.update(0.02)
    assert restored_body._wait_left == 0.0
    restored_body.update(0.1)
    assert restored_body.reveal_chars > wait_reveal


@pytest.mark.parametrize(
    ("source_nvl", "target_nvl"),
    [(False, True), (True, False)],
    ids=["adv-to-nvl", "nvl-to-adv"],
)
def test_active_wait_survives_presentation_mode_change_on_quickload(
    driver,
    source_nvl,
    target_nvl,
):
    from world_gal_game.ui.nvl_box import NVLBox

    app = driver.app
    app.config.nvl_mode = source_nvl
    dialogue = _open(
        driver,
        Scene(
            id=f"resume_wait_mode_{source_nvl}_{target_nvl}",
            lines=[Line(speaker="測試者", text="甲[w=2]乙")],
        ),
    )
    source_body = (
        dialogue.box.entries[-1].body
        if isinstance(dialogue.box, NVLBox)
        else dialogue.box.body
    )
    wait_plain = next(iter(source_body._doc.waits()))
    source_body.set_reveal(
        source_body.reveal_cursor_for_plain(wait_plain),
    )
    source_body.update(0.25)
    app._quicksave()

    app.config.nvl_mode = target_nvl
    source_body.force_reveal()
    app._quickload()
    app.manager.commit_pending()
    restored = app.manager.current
    assert type(restored).__name__ == "DialogueScene"
    assert isinstance(restored.box, NVLBox) is target_nvl
    target_body = (
        restored.box.entries[-1].body
        if isinstance(restored.box, NVLBox)
        else restored.box.body
    )

    assert target_body.plain_cursor_for_reveal() == wait_plain
    assert target_body._wait_left == pytest.approx(1.75)
    if target_nvl:
        assert restored.box.line_count == 1
        assert restored._nvl_transcript == [{
            "speaker": "測試者",
            "speaker_id": "測試者",
            "text": "甲[w=2]乙",
        }]


def test_completed_wait_at_cursor_does_not_retrigger_after_quickload(driver):
    app = driver.app
    dialogue = _open(
        driver,
        Scene(
            id="resume_completed_wait_disk",
            lines=[Line(text="甲[w=2]乙"), Line(text="下一句")],
        ),
    )
    body = dialogue.box.body
    wait_plain = next(iter(body._doc.waits()))
    wait_reveal = body.reveal_cursor_for_plain(wait_plain)
    body.set_reveal(wait_reveal)
    body.update(0.25)
    body.update(1.75)
    assert body._wait_left == 0.0
    assert body.export_wait_timeline() == {
        "consumed_plain_positions": [wait_plain],
        "active": None,
    }
    app._quicksave()

    body.force_reveal()
    dialogue._advance()
    app._quickload()
    app.manager.commit_pending()
    restored = app.manager.current.box.body
    assert restored.reveal_chars == wait_reveal
    assert restored._wait_left == 0.0
    restored.update(0.1)

    assert restored._wait_left == 0.0
    assert restored.reveal_chars > wait_reveal


def test_legacy_resume_without_wait_timeline_remains_loadable(driver):
    scene = Scene(
        id="resume_legacy_wait_timeline",
        lines=[
            Line(
                text="甲[w=2]乙",
                effects=[Effect(kind="advance_time", value=1)],
            ),
            Line(text="下一句"),
        ],
    )
    dialogue = _open(driver, scene)
    app = driver.app
    body = dialogue.box.body
    wait_plain = next(iter(body._doc.waits()))
    wait_reveal = body.reveal_cursor_for_plain(wait_plain)
    body.set_reveal(wait_reveal)
    body.update(0.25)
    saved_time = app.state.time.model_dump(mode="json")
    app._quicksave()
    path = (
        driver._resume_save_dir
        / f"{app.config.quicksave_slot}.json"
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["_resume"].pop("text_wait_timeline")
    path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    body.force_reveal()
    dialogue._advance()
    assert dialogue._current_line.text == "下一句"
    app._quickload()
    app.manager.commit_pending()
    restored_dialogue = app.manager.current
    assert type(restored_dialogue).__name__ == "DialogueScene"
    restored_body = restored_dialogue.box.body
    assert restored_body._wait_left == 0.0
    restored_body.update(0.01)

    # A pre-field v1 save cannot know elapsed sub-wait time, so it retains the
    # historical safe fallback: replay the authored wait from its boundary.
    assert restored_body.reveal_chars == wait_reveal
    assert restored_body._wait_left == pytest.approx(1.99)
    assert app.state.time.model_dump(mode="json") == saved_time


def test_terminal_inline_wait_does_not_disable_dialogue_resume(driver):
    dialogue = _open(
        driver,
        Scene(
            id="resume_terminal_wait",
            lines=[Line(text="句末沒有下一個字[w=2]")],
        ),
    )
    dialogue.box.force_reveal()

    resume = dialogue.export_resume_context()

    assert resume is not None
    assert resume["text_fully_revealed"] is True
    assert resume["text_wait_timeline"] == {
        "consumed_plain_positions": [],
        "active": None,
    }
    assert driver.app._resume_matches_loaded_state(resume) is True


def test_quickload_restores_choice_prompt(driver):
    scene = Scene(
        id="resume_choice_probe",
        lines=[Line(text="choose now")],
        choices=[
            Choice(id="left", text="Left path"),
            Choice(id="right", text="Right path"),
        ],
    )
    dialogue = _open(driver, scene)
    dialogue.box.force_reveal()
    dialogue._advance()
    assert dialogue.choices.visible is True

    driver.app._quicksave()
    # Disturb only the UI; the saved state still points at the choice boundary.
    dialogue.choices.visible = False
    driver.app._quickload()
    driver.advance_frames(2)

    restored = driver.app.manager.current
    assert type(restored).__name__ == "DialogueScene"
    assert restored._current_line.text == "choose now"
    assert restored.choices.visible is True
    assert [button.label for button in restored.choices.buttons] == [
        "Left path", "Right path",
    ]
    assert driver.app.state.story.current_scene == scene.id
    assert driver.app.state.story.current_line_index == 1


def test_manual_save_and_load_use_same_resume_envelope(driver, monkeypatch):
    scene = Scene(
        id="manual_resume_probe",
        lines=[Line(text="manual save line"), Line(text="after manual save")],
    )
    dialogue = _open(driver, scene)
    dialogue.box.force_reveal()

    driver.app._open_save_menu()
    driver.app.manager.commit_pending()
    save_overlay = driver.app.manager.current
    assert type(save_overlay).__name__ == "SaveScene"
    assert save_overlay._get_resume is not None
    save_overlay._on_action(save_overlay._items[0])
    manual_slot = next(
        row["slot"] for row in save_overlay._items
        if row.get("slot") and not row["slot"].startswith("autosave_")
    )

    driver.app.manager.pop()
    driver.app.manager.commit_pending()
    dialogue = driver.app.manager.current
    dialogue._advance()
    assert dialogue._current_line.text == "after manual save"

    from world_gal_game.core import pack_migration

    original_migrate = pack_migration.check_and_migrate_pack
    migration_saw_resume: list[bool] = []

    def _migrate_with_resume(data, **kwargs):
        migration_saw_resume.append("_resume" in data)
        migrated_text = "manual save line after migration"
        resume = data["_resume"]
        resume["presentation"]["line"]["text"] = migrated_text
        resume["presentation"]["line"]["plain_text"] = migrated_text
        resume["text_plain_cursor"] = len(migrated_text)
        resume["text_reveal_cursor"] = len(migrated_text)
        return original_migrate(data, **kwargs)

    monkeypatch.setattr(
        pack_migration, "check_and_migrate_pack", _migrate_with_resume,
    )
    driver.app._open_load_menu()
    driver.app.manager.commit_pending()
    load_overlay = driver.app.manager.current
    row = next(item for item in load_overlay._items
               if item.get("slot") == manual_slot)
    load_overlay._on_action(row)
    driver.advance_frames(2)

    restored = driver.app.manager.current
    assert type(restored).__name__ == "DialogueScene"
    assert migration_saw_resume == [True]
    assert restored._current_line.text == "manual save line after migration"
    assert driver.app.state.story.current_line_index == 1


def test_nvl_resume_restores_accumulated_transcript(driver):
    from world_gal_game.ui.nvl_box import NVLBox

    driver.app.config.nvl_mode = True
    scene = Scene(
        id="nvl_resume_probe",
        lines=[
            Line(speaker="A", text="first nvl line"),
            Line(speaker="B", text="[b]second[/b] nvl line"),
        ],
    )
    dialogue = _open(driver, scene)
    dialogue.box.force_reveal()
    dialogue._advance()
    dialogue.box.force_reveal()
    assert isinstance(dialogue.box, NVLBox)
    assert dialogue.box.line_count == 2

    driver.app._quicksave()
    dialogue.box.reset()
    driver.app._quickload()
    driver.advance_frames(2)

    restored = driver.app.manager.current
    assert isinstance(restored.box, NVLBox)
    assert restored.box.line_count == 2
    assert restored.box.speakers() == ["A", "B"]
    assert [entry.body.plain_text() for entry in restored.box.entries] == [
        "first nvl line", "second nvl line",
    ]


def test_time_effect_autosave_is_flushed_after_presentation_commit(driver):
    driver.app.config.autosave_enabled = True
    driver.app.config.autosave_slot_count = 1
    scene = Scene(
        id="atomic_autosave_probe",
        lines=[
            Line(
                text="time moved exactly once",
                effects=[
                    Effect(
                        kind="screen_tint",
                        value={
                            "color": [90, 10, 10],
                            "duration": 0,
                            "max_alpha": 180,
                        },
                    ),
                    Effect(kind="advance_time", value=1),
                ],
            ),
            Line(text="next"),
        ],
    )
    dialogue = _open(driver, scene)
    dialogue.box.force_reveal()
    saved_time = driver.app.state.time.model_dump(mode="json")

    path = driver._resume_save_dir / "autosave_1.json"
    assert path.exists()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["story"]["current_line_index"] == 1
    assert raw["_resume"]["presentation"]["line"]["text"] == \
        "time moved exactly once"
    assert raw["_resume"]["story_line_index"] == 1
    assert raw["_resume"]["tint"] == {
        "color": [90, 10, 10],
        "alpha": 180,
    }

    dialogue._advance()
    assert driver.app._load_slot("autosave_1") is True
    driver.advance_frames(2)
    restored = driver.app.manager.current
    assert restored._current_line.text == "time moved exactly once"
    assert restored._tint is not None
    assert restored._tint.color == (90, 10, 10)
    assert restored._tint.alpha() == 180
    assert driver.app.state.time.model_dump(mode="json") == saved_time


def test_skip_to_end_still_flushes_deferred_autosave(driver):
    app = driver.app
    app.config.autosave_enabled = True
    app.config.autosave_slot_count = 1
    app.config.skip_unread_only = False
    scene = Scene(
        id="skip_end_autosave_probe",
        lines=[
            Line(text="start"),
            Line(
                text="skipped effect",
                effects=[Effect(kind="advance_time", value=1)],
            ),
        ],
    )
    dialogue = _open(driver, scene)
    dialogue.box.force_reveal()

    dialogue._trigger_skip()

    path = driver._resume_save_dir / "autosave_1.json"
    assert path.exists()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["story"]["current_scene"] is None
    assert "_resume" not in raw


def test_legacy_save_without_resume_falls_back_to_exploration(driver):
    from world_gal_game.core.save_manager import SaveManager

    scene = Scene(
        id="legacy_resume_probe",
        lines=[Line(text="old save has no presentation envelope")],
    )
    _open(driver, scene)
    state = driver.app.state.model_dump(mode="json")
    SaveManager(driver._resume_save_dir).save(
        "legacy_dialogue",
        state,
        pack_meta=driver.app.state.meta.get("__pack_meta__", {}),
    )

    assert driver.app._load_slot("legacy_dialogue") is True
    driver.advance_frames(2)
    assert type(driver.app.manager.current).__name__ == "ExplorationScene"
    # The story cursor remains available to future migration/tooling, but an
    # old save is never re-rendered by replaying the already-consumed line.
    assert driver.app.state.story.current_scene == scene.id
    assert driver.app.state.story.current_line_index == 1


def test_load_rebinds_current_pack_map_catalog_but_keeps_map_progress(driver):
    from world_gal_game.core.map_system import Location
    from world_gal_game.core.save_manager import SaveManager

    app = driver.app
    state = app.state.model_dump(mode="json")
    state["map"]["current_location_id"] = "town_square"
    state["map"]["visited"] = [
        "starting_room",
        "town_square",
        "retired_location",
    ]
    state["map"]["locations"]["town_square"]["name"] = "Obsolete square"
    del state["map"]["locations"]["park"]
    state["map"]["locations"]["retired_location"] = Location(
        id="retired_location",
        name="Removed from current pack",
    ).model_dump(mode="json")
    SaveManager(driver._resume_save_dir).save(
        "stale_map_catalog",
        state,
        pack_meta=app.state.meta.get("__pack_meta__", {}),
    )

    assert app._load_slot("stale_map_catalog") is True
    driver.advance_frames(2)

    assert app.state.map.current_location_id == "town_square"
    assert app.state.map.visited == {"starting_room", "town_square"}
    assert app.state.map.locations["town_square"].name == "鎮中廣場"
    assert "park" in app.state.map.locations
    assert "retired_location" not in app.state.map.locations


def test_resume_failure_after_preflight_never_restarts_scene(driver):
    app = driver.app
    scene = Scene(
        id="resume_fail_closed_probe",
        lines=[
            Line(
                text="effect must not replay",
                effects=[Effect(kind="advance_time", value=1)],
            ),
            Line(text="next"),
        ],
    )
    dialogue = _open(driver, scene)
    resume = dialogue.export_resume_context()
    assert resume is not None
    assert app._resume_matches_loaded_state(resume) is True
    saved_time = app.state.time.model_dump(mode="json")

    # Queue the validated view replacement, then model a lifecycle hook changing
    # the cursor before DialogueScene.enter performs the actual restore.
    app._restore_loaded_view(resume)
    app.state.story.current_line_index = 0
    ambient_checks: list[bool] = []
    app._check_ambient_hooks = lambda: ambient_checks.append(True)
    app.manager.commit_pending()

    assert type(app.manager.current).__name__ == "ExplorationScene"
    assert ambient_checks == []
    assert app.state.time.model_dump(mode="json") == saved_time
    assert app.state.story.current_line_index == 0


def test_malformed_resume_slot_fails_closed_without_async_crash_or_replay(
    driver,
):
    from world_gal_game.scenes.dialogue_scene import DialogueScene
    from world_gal_game.scenes.exploration import ExplorationScene

    app = driver.app
    scene = Scene(
        id="resume_malformed_slot_probe",
        lines=[
            Line(
                text="effect must remain committed exactly once",
                effects=[Effect(kind="advance_time", value=1)],
            ),
            Line(text="next"),
        ],
    )
    dialogue = _open(driver, scene)
    resume = dialogue.export_resume_context()
    assert resume is not None
    saved_time = app.state.time.model_dump(mode="json")

    malformed = copy.deepcopy(resume)
    malformed["slots"]["center"] = {
        "kind": "legacy",
        "portrait": None,
        "speaker": {"not": "a string"},
        "expression": None,
        "slot": "center",
    }
    assert app._resume_matches_loaded_state(malformed) is False

    # Exercise DialogueScene.enter directly through the manager too: callers
    # that bypass app pre-flight still fail closed on commit_pending rather
    # than raising on the next frame or falling through to _start().
    app.manager.clear_to(
        ExplorationScene(app.ctx), **app._exploration_callbacks(),
    )
    app.manager.push(
        DialogueScene(app.ctx),
        scene_id=scene.id,
        resume_context=malformed,
        **app._dialogue_enter_callbacks(),
    )
    app.manager.commit_pending()

    assert [type(item).__name__ for item in app.manager.stack()] == [
        "ExplorationScene",
    ]
    assert app.state.time.model_dump(mode="json") == saved_time
    assert app.state.story.current_line_index == 1


def test_resume_validation_rejects_structural_and_scene_mismatches(driver):
    app = driver.app
    scene = Scene(
        id="resume_validation_probe",
        lines=[Line(speaker="林清雪", text="strict resume")],
    )
    dialogue = _open(driver, scene)
    resume = dialogue.export_resume_context()
    assert resume is not None

    # v1 UI fields are additive: an older/minimal v1 envelope that predates
    # slots/camera/tint/transcript metadata still restores its core line.
    minimal_v1 = {
        key: copy.deepcopy(resume[key])
        for key in (
            "version",
            "screen",
            "scene_id",
            "story_scene",
            "story_line_index",
            "presentation",
            "last_line",
        )
    }
    minimal_v1["presentation"]["line"].pop("speaker_id", None)
    minimal_v1["last_line"]["line"].pop("speaker_id", None)
    assert app._resume_matches_loaded_state(minimal_v1) is True
    restored_presentation, restored_last_line = resume_presentations_for_state(
        minimal_v1,
        app.state,
        authored_story=app._authored_story_catalog,
        authored_npcs=app.npcs,
        authored_map=app._authored_map_catalog,
        localization=app.localization,
    )
    assert restored_presentation.line.speaker_id == "heroine_1"
    assert restored_last_line.line.speaker_id == "heroine_1"

    missing_line = copy.deepcopy(resume)
    missing_line["presentation"]["line"] = None
    assert app._resume_matches_loaded_state(missing_line) is False

    wrong_line_scene = copy.deepcopy(resume)
    wrong_line_scene["presentation"]["line"]["scene_id"] = "other_scene"
    assert app._resume_matches_loaded_state(wrong_line_scene) is False

    wrong_last_line_scene = copy.deepcopy(resume)
    wrong_last_line_scene["last_line"]["scene_id"] = "other_scene"
    assert app._resume_matches_loaded_state(wrong_last_line_scene) is False

    invalid_last_line = copy.deepcopy(resume)
    invalid_last_line["last_line"] = {
        "kind": "end",
        "line": None,
        "choices": [],
        "next_scene": None,
        "scene_id": scene.id,
        "title": None,
        "background": None,
    }
    assert app._resume_matches_loaded_state(invalid_last_line) is False


@pytest.mark.parametrize(
    "case",
    [
        "camera_huge_integer",
        "camera_oversized_finite",
        "camera_pan",
        "background_blur",
        "portrait_scale",
        "portrait_offset",
        "text_reveal_cursor",
        "text_plain_cursor",
        "text_page_index",
    ],
)
def test_resume_validation_bounds_renderer_facing_numbers(driver, case):
    app = driver.app
    scene = Scene(
        id=f"resume_numeric_bounds_{case}",
        lines=[Line(text="bounded presentation")],
    )
    dialogue = _open(driver, scene)
    resume = dialogue.export_resume_context()
    assert resume is not None

    malformed = copy.deepcopy(resume)
    if case == "camera_huge_integer":
        # Large enough to overflow float(), but still below Python's JSON
        # integer-string safety limit so it models a real user-edited save.
        malformed["camera"]["zoom"] = 10 ** 1000
    elif case == "camera_oversized_finite":
        malformed["camera"]["zoom"] = 1e308
    elif case == "camera_pan":
        malformed["camera"]["pan_x"] = 1e308
    elif case == "background_blur":
        malformed["background_blur"] = 1e308
    elif case == "portrait_scale":
        malformed["slots"]["center"] = {
            "spec": PortraitSpec(
                character="numeric_probe",
                slot="center",
                scale=1e308,
            ).model_dump(mode="json"),
        }
    elif case == "portrait_offset":
        malformed["slots"]["center"] = {
            "spec": PortraitSpec(
                character="numeric_probe",
                slot="center",
                offset=(10 ** 1000, 0),
            ).model_dump(mode="json"),
        }
    elif case == "text_reveal_cursor":
        malformed["text_reveal_cursor"] = 10 ** 1000
    elif case == "text_plain_cursor":
        malformed["text_plain_cursor"] = 10 ** 1000
    else:
        malformed["text_page_index"] = 10 ** 1000

    # This public preflight is a no-throw boundary for user-writable save JSON.
    assert app._resume_matches_loaded_state(malformed) is False
    assert dialogue.restore_resume_context(malformed) is False


@pytest.mark.parametrize(
    "case",
    [
        "timeline_shape",
        "duplicate_consumed",
        "unknown_consumed",
        "future_consumed",
        "active_position",
        "active_not_consumed",
        "remaining_negative",
        "remaining_nonfinite",
        "remaining_over_authored",
        "page_complete_during_active_wait",
    ],
)
def test_resume_validation_rejects_forged_wait_timeline(driver, case):
    app = driver.app
    scene = Scene(
        id=f"resume_wait_validation_{case}",
        lines=[Line(text="甲[w=2]乙[w=1]丙")],
    )
    dialogue = _open(driver, scene)
    body = dialogue.box.body
    first_wait, second_wait = body._doc.waits()
    body.set_reveal(body.reveal_cursor_for_plain(first_wait))
    body.update(0.25)
    resume = dialogue.export_resume_context()
    assert resume is not None

    malformed = copy.deepcopy(resume)
    timeline = malformed["text_wait_timeline"]
    if case == "timeline_shape":
        malformed["text_wait_timeline"] = []
    elif case == "duplicate_consumed":
        timeline["consumed_plain_positions"] = [first_wait, first_wait]
    elif case == "unknown_consumed":
        timeline["consumed_plain_positions"] = [0, first_wait]
    elif case == "future_consumed":
        timeline["consumed_plain_positions"] = [first_wait, second_wait]
    elif case == "active_position":
        timeline["active"]["plain_position"] = second_wait
    elif case == "active_not_consumed":
        timeline["consumed_plain_positions"] = []
    elif case == "remaining_negative":
        timeline["active"]["remaining_seconds"] = -0.1
    elif case == "remaining_nonfinite":
        timeline["active"]["remaining_seconds"] = float("inf")
    elif case == "page_complete_during_active_wait":
        malformed["text_page_fully_revealed"] = True
    else:
        timeline["active"]["remaining_seconds"] = 2.1

    assert app._resume_matches_loaded_state(malformed) is False
    assert dialogue.restore_resume_context(malformed) is False


@pytest.mark.parametrize("forge_complete", [True, False])
def test_resume_completion_flag_must_match_plain_cursor(
    driver,
    forge_complete,
):
    dialogue = _open(
        driver,
        Scene(
            id=f"resume_completion_flag_{forge_complete}",
            lines=[Line(text="甲乙丙")],
        ),
    )
    if forge_complete:
        dialogue.box.body.set_reveal(0)
    else:
        dialogue.box.force_reveal()
    resume = dialogue.export_resume_context()
    assert resume is not None
    assert resume["text_fully_revealed"] is (not forge_complete)

    forged = copy.deepcopy(resume)
    forged["text_fully_revealed"] = forge_complete
    forged["text_page_fully_revealed"] = forge_complete

    assert driver.app._resume_matches_loaded_state(forged) is False
    assert dialogue.restore_resume_context(forged) is False


def test_choice_resume_cannot_forge_an_active_line_wait(driver):
    scene = Scene(
        id="resume_choice_wait_forgery",
        lines=[Line(text="甲[w=1]乙")],
        choices=[Choice(id="continue", text="Continue")],
    )
    dialogue = _open(driver, scene)
    dialogue.box.force_reveal()
    dialogue._advance()
    resume = dialogue.export_resume_context()
    assert resume is not None
    assert resume["text_wait_timeline"] is None

    forged = copy.deepcopy(resume)
    forged["text_wait_timeline"] = {
        "consumed_plain_positions": [1],
        "active": {"plain_position": 1, "remaining_seconds": 0.5},
    }

    assert driver.app._resume_matches_loaded_state(forged) is False
    assert dialogue.restore_resume_context(forged) is False


def test_nvl_transcript_wait_mismatch_is_rejected_before_state_swap(driver):
    from world_gal_game.ui.nvl_box import NVLBox

    app = driver.app
    app.config.nvl_mode = True
    dialogue = _open(
        driver,
        Scene(
            id="resume_nvl_wait_tail_provenance",
            lines=[Line(text="甲[w=2]乙"), Line(text="live state")],
        ),
    )
    assert isinstance(dialogue.box, NVLBox)
    body = dialogue.box.entries[-1].body
    wait_plain = next(iter(body._doc.waits()))
    body.set_reveal(body.reveal_cursor_for_plain(wait_plain))
    body.update(0.25)
    app._quicksave()
    path = (
        driver._resume_save_dir
        / f"{app.config.quicksave_slot}.json"
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["_resume"]["nvl_transcript"][-1]["text"] = "甲乙"
    assert app._resume_matches_loaded_state(raw["_resume"]) is False
    path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    body.force_reveal()
    dialogue._advance()
    live_state = app.state.model_dump(mode="json")
    live_scene = app.manager.current
    assert app._load_slot(app.config.quicksave_slot) is False

    assert app.manager.current is live_scene
    assert app.state.model_dump(mode="json") == live_state
    assert dialogue._current_line.text == "live state"


def test_live_direct_restore_is_atomic_when_audio_backend_fails(
    driver,
    monkeypatch,
):
    app = driver.app
    dialogue = _open(
        driver,
        Scene(
            id="resume_live_backend_failure",
            lines=[Line(text="甲[w=2]乙", voice="assets/voice/probe.ogg")],
        ),
    )
    body = dialogue.box.body
    wait_plain = next(iter(body._doc.waits()))
    body.set_reveal(body.reveal_cursor_for_plain(wait_plain))
    body.update(0.25)
    resume = dialogue.export_resume_context()
    assert resume is not None
    # Exercise the legacy compatibility branch, whose missing checkpoint must
    # still restart a voiced line. The authored probe asset is intentionally
    # absent, so a new envelope correctly records explicit silence instead.
    resume.pop("voice_playback", None)
    body.force_reveal()
    old_body = dialogue.box.body
    old_cursor = old_body.reveal_chars
    old_state = app.state.model_dump(mode="json")

    def _raise_voice(*_args, **_kwargs):
        raise RuntimeError("voice backend failed")

    monkeypatch.setattr(app.assets, "play_voice", _raise_voice)
    assert dialogue.restore_resume_context(resume) is False

    assert dialogue.box.body is old_body
    assert dialogue.box.body.reveal_chars == old_cursor
    assert dialogue.box.body._wait_left == 0.0
    assert app.state.model_dump(mode="json") == old_state


def test_live_direct_restore_stops_before_mutation_when_audio_snapshot_fails(
    driver,
    monkeypatch,
):
    app = driver.app
    dialogue = _open(
        driver,
        Scene(
            id="resume_live_snapshot_failure",
            lines=[Line(text="A failed snapshot has no rollback point.")],
        ),
    )
    resume = dialogue.export_resume_context()
    assert resume is not None
    old_dict = dialogue.__dict__
    old_line = dialogue._current_line
    stop_calls: list[bool] = []

    def _raise_snapshot():
        raise RuntimeError("mixer snapshot unavailable")

    monkeypatch.setattr(app.assets, "capture_audio_playback", _raise_snapshot)
    monkeypatch.setattr(
        app.assets,
        "stop_voice",
        lambda: stop_calls.append(True),
    )

    assert dialogue.restore_resume_context(copy.deepcopy(resume)) is False
    assert dialogue.__dict__ is old_dict
    assert dialogue._current_line is old_line
    assert stop_calls == []


def test_unsafe_live_geometry_never_writes_a_poisoned_resume(driver):
    scene = Scene(
        id="resume_unsafe_export_probe",
        lines=[Line(text="state save must remain loadable")],
    )
    dialogue = _open(driver, scene)
    dialogue._camera.zoom = 1e308

    assert dialogue.export_resume_context() is None
    driver.app._quicksave()
    raw = json.loads(
        (driver._resume_save_dir
         / f"{driver.app.config.quicksave_slot}.json").read_text(
             encoding="utf-8",
         )
    )
    assert "_resume" not in raw


def test_nonfinite_authored_wait_degrades_to_bounded_resumable_pause(driver):
    dialogue = _open(
        driver,
        Scene(
            id="resume_nonfinite_live_wait",
            lines=[Line(text="甲[w=inf]乙")],
        ),
    )
    body = dialogue.box.body
    wait_plain = next(iter(body._doc.waits()))
    body.set_reveal(body.reveal_cursor_for_plain(wait_plain))
    body.update(0.1)
    assert body._wait_left == pytest.approx(0.3)

    assert dialogue.export_resume_context() is not None
    driver.app._quicksave()
    raw = json.loads(
        (
            driver._resume_save_dir
            / f"{driver.app.config.quicksave_slot}.json"
        ).read_text(encoding="utf-8")
    )
    assert raw["_resume"]["text_wait_timeline"]["active"] == {
        "plain_position": wait_plain,
        "remaining_seconds": pytest.approx(0.3),
    }


def _write_pathological_resume(path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["_resume"]["camera"]["zoom"] = 10 ** 1000
    path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def test_quickload_invalid_resume_is_atomic_for_state_and_stack(driver):
    app = driver.app
    scene = Scene(
        id="resume_atomic_quickload_probe",
        lines=[Line(text="saved"), Line(text="live state must survive")],
    )
    dialogue = _open(driver, scene)
    app._quicksave()
    slot = app.config.quicksave_slot
    _write_pathological_resume(driver._resume_save_dir / f"{slot}.json")

    dialogue.box.force_reveal()
    dialogue._advance()
    state_before = app.state.model_dump(mode="json")
    stack_before = app.manager.stack()

    assert app._load_slot(slot) is False
    assert app.state.model_dump(mode="json") == state_before
    assert app.manager.stack() == stack_before
    assert app.manager.current is dialogue
    assert dialogue._current_line.text == "live state must survive"


def test_manual_load_invalid_resume_keeps_overlay_state_and_stack(driver):
    app = driver.app
    scene = Scene(
        id="resume_atomic_manual_load_probe",
        lines=[Line(text="saved"), Line(text="live state must survive")],
    )
    dialogue = _open(driver, scene)
    app._quicksave()
    slot = app.config.quicksave_slot
    _write_pathological_resume(driver._resume_save_dir / f"{slot}.json")

    dialogue.box.force_reveal()
    dialogue._advance()
    app._open_load_menu()
    app.manager.commit_pending()
    overlay = app.manager.current
    row = next(item for item in overlay._items if item.get("slot") == slot)
    state_before = app.state.model_dump(mode="json")
    stack_before = app.manager.stack()

    overlay._on_action(row)

    assert app.state.model_dump(mode="json") == state_before
    assert app.manager.stack() == stack_before
    assert app.manager.current is overlay
    assert overlay._error_msg == "存檔顯示狀態錯誤，未載入。"


def test_minimal_v1_preserves_presentation_and_state_derived_audio(driver):
    app = driver.app
    scene = Scene(
        id="resume_minimal_v1_presentation_probe",
        background="assets/backgrounds/minimal_v1.png",
        lines=[
            Line(
                text="minimal v1",
                cg="assets/cg/minimal_v1.png",
                bgm="assets/bgm/minimal_v1.ogg",
                ambient="assets/sfx/minimal_v1.ogg",
            ),
        ],
    )
    dialogue = _open(driver, scene)
    resume = dialogue.export_resume_context()
    assert resume is not None
    minimal_v1 = {
        key: copy.deepcopy(resume[key])
        for key in (
            "version",
            "screen",
            "scene_id",
            "story_scene",
            "story_line_index",
            "presentation",
            "last_line",
        )
    }
    assert app._resume_matches_loaded_state(minimal_v1) is True

    played_music: list[str] = []
    played_ambient: list[str] = []
    stopped_music: list[bool] = []
    stopped_ambient: list[bool] = []
    app.assets.play_music = lambda path, **_: played_music.append(path)
    app.assets.play_ambient = lambda path, **_: played_ambient.append(path)
    app.assets.stop_music = lambda **_: stopped_music.append(True)
    app.assets.stop_ambient = lambda **_: stopped_ambient.append(True)

    app._restore_loaded_view(minimal_v1)
    app.manager.commit_pending()
    restored = app.manager.current

    assert type(restored).__name__ == "DialogueScene"
    assert restored.background_path == scene.background
    assert restored.cg_surface_path == "assets/cg/minimal_v1.png"
    assert played_music == ["assets/bgm/minimal_v1.ogg"]
    assert played_ambient == ["assets/sfx/minimal_v1.ogg"]
    # Load is a replacement boundary. Even with no tracked current path, an
    # outgoing backend channel may still be in its fade tail, so legacy audio
    # is hard-cut before the state-derived authored tracks are attempted.
    assert stopped_music == [True]
    assert stopped_ambient == [True]


def test_choice_resume_requires_the_real_end_of_lines_boundary(driver):
    app = driver.app
    scene = Scene(
        id="resume_choice_boundary_probe",
        lines=[Line(text="this line cannot be skipped")],
        choices=[Choice(id="continue", text="Continue")],
    )
    dialogue = _open(driver, scene)
    dialogue.box.force_reveal()
    dialogue._advance()
    resume = dialogue.export_resume_context()
    assert resume is not None
    assert resume["presentation"]["kind"] == "choice"

    # Keep envelope and state cursor mutually consistent but move both before
    # the authored line boundary. Structural validation must still reject it.
    forged = copy.deepcopy(resume)
    forged["story_line_index"] = 0
    app.state.story.current_line_index = 0
    try:
        assert app._resume_matches_loaded_state(forged) is False
        assert dialogue.restore_resume_context(forged) is False
    finally:
        app.state.story.current_line_index = 1


def test_direct_restore_rejects_tampered_sound_caption_provenance(driver):
    scene = Scene(
        id="resume_sound_caption_provenance",
        lines=[
            Line(
                text="門外沒有人。",
                sound_captions=[{
                    "kind": "sfx",
                    "text": "左側傳來三下敲門聲",
                }],
            )
        ],
    )
    dialogue = _open(driver, scene)
    resume = dialogue.export_resume_context()
    assert resume is not None
    assert dialogue.restore_resume_context(copy.deepcopy(resume)) is True

    legacy = copy.deepcopy(resume)
    legacy["presentation"]["line"].pop("sound_captions")
    assert dialogue.restore_resume_context(legacy) is True
    assert dialogue._current_line.sound_captions[0].text == (
        "左側傳來三下敲門聲"
    )

    forged = copy.deepcopy(resume)
    forged["presentation"]["line"]["sound_captions"][0]["text"] = (
        "偽造的爆炸聲"
    )

    assert dialogue.restore_resume_context(forged) is False
    assert dialogue._current_line.sound_captions[0].text == (
        "左側傳來三下敲門聲"
    )


def test_quickload_pack_migration_can_update_resume_envelope(
    driver, monkeypatch,
):
    from world_gal_game.core import pack_migration

    app = driver.app
    scene = Scene(
        id="resume_quickload_migration_probe",
        lines=[Line(text="before migration"), Line(text="after save")],
    )
    dialogue = _open(driver, scene)
    app._quicksave()
    dialogue._advance()

    original_migrate = pack_migration.check_and_migrate_pack
    migration_saw_resume: list[bool] = []

    def _migrate_with_resume(data, **kwargs):
        migration_saw_resume.append("_resume" in data)
        data["_resume"]["presentation"]["line"]["text"] = \
            "presentation migrated with state"
        return original_migrate(data, **kwargs)

    monkeypatch.setattr(
        pack_migration, "check_and_migrate_pack", _migrate_with_resume,
    )

    assert app._load_slot(app.config.quicksave_slot) is True
    driver.advance_frames(2)

    assert migration_saw_resume == [True]
    assert type(app.manager.current).__name__ == "DialogueScene"
    assert app.manager.current._current_line.text == \
        "presentation migrated with state"


def test_chained_scene_updates_all_resume_scene_ids(driver):
    app = driver.app
    second = Scene(
        id="resume_chain_second",
        lines=[Line(text="second scene line")],
    )
    first = Scene(
        id="resume_chain_first",
        lines=[Line(text="first scene line")],
        on_end=[Effect(kind="play_scene", target=second.id)],
    )
    app.state.story.add_scene(second)
    app._authored_story_catalog.add_scene(second.model_copy(deep=True))
    dialogue = _open(driver, first)
    dialogue.box.force_reveal()
    dialogue._advance()

    resume = dialogue.export_resume_context()
    assert resume is not None
    assert resume["scene_id"] == second.id
    assert resume["story_scene"] == second.id
    assert resume["presentation"]["scene_id"] == second.id
    assert resume["presentation"]["line"]["scene_id"] == second.id
    assert resume["last_line"]["scene_id"] == second.id
    assert app._resume_matches_loaded_state(resume) is True


def test_no_location_resume_keeps_exploration_base_after_dialogue(driver):
    app = driver.app
    app.state.map.current_location_id = None
    scene = Scene(
        id="resume_without_location_probe",
        lines=[Line(text="dialogue before the first location")],
    )
    dialogue = _open(driver, scene)
    dialogue.box.force_reveal()
    app._quicksave()

    app._quickload()
    driver.advance_frames(2)
    assert [type(item).__name__ for item in app.manager.stack()] == [
        "ExplorationScene",
        "DialogueScene",
    ]

    restored = app.manager.current
    restored.box.force_reveal()
    restored._advance()
    driver.advance_frames(2)
    assert [type(item).__name__ for item in app.manager.stack()] == [
        "ExplorationScene",
    ]


def test_silent_resume_stops_voice_from_abandoned_screen(driver):
    app = driver.app
    scene = Scene(
        id="resume_silent_voice_probe",
        lines=[Line(text="saved silent line"), Line(text="disturbed line")],
    )
    dialogue = _open(driver, scene)
    app._quicksave()
    dialogue._advance()

    stop_calls: list[bool] = []
    app.assets.stop_voice = lambda: stop_calls.append(True)
    app._quickload()
    driver.advance_frames(2)

    assert stop_calls
    assert app.manager.current._current_line.text == "saved silent line"


# Every path below came from user-writable save JSON.  It must be proven
# against the immutable story catalogue before any asset backend sees it.
_FORGED_RESUME_ASSET = "/etc/passwd"
_RESUME_ASSET_PROVENANCE_CASES = (
    "top_level_cg",
    "top_level_current_bgm",
    "top_level_current_ambient",
    "slot_legacy_portrait",
    "presentation_cg",
    "presentation_bgm",
    "presentation_ambient",
    "presentation_sfx",
    "presentation_portrait",
    "presentation_expression",
    "presentation_speaker",
    "presentation_voice_without_checkpoint",
    "last_line_cg",
    "last_line_bgm",
    "last_line_ambient",
    "last_line_sfx",
    "last_line_portrait",
    "last_line_expression",
    "last_line_speaker",
    "last_line_voice",
    "slot_spec_image",
    "slot_spec_character",
    "presentation_spec_image",
    "presentation_spec_character",
    "presentation_multi_spec_image",
    "presentation_multi_spec_character",
    "choice_card_image",
)


def _open_asset_provenance_resume(driver, case: str):
    """Open an authored line/choice carrying every resumable asset kind."""
    spec = PortraitSpec(
        character="resume_asset_probe",
        expression="smile",
        slot="left",
        image="assets/characters/resume_asset_probe/smile.png",
    )
    if case in {
        "slot_spec_image",
        "slot_spec_character",
        "presentation_spec_image",
        "presentation_spec_character",
    }:
        portrait = spec
        portraits = []
        portrait_pos = None
    elif case in {
        "presentation_multi_spec_image",
        "presentation_multi_spec_character",
    }:
        portrait = None
        portraits = [spec]
        portrait_pos = None
    else:
        portrait = "assets/characters/resume_asset_probe.png"
        portraits = []
        portrait_pos = "left"

    safe_case = case.replace(".", "_")
    scene = Scene(
        id=f"resume_asset_provenance_{safe_case}",
        background="assets/backgrounds/resume_asset_probe.png",
        lines=[
            Line(
                speaker="Asset Probe",
                text="All presentation paths must remain authored.",
                portrait=portrait,
                portraits=portraits,
                portrait_pos=portrait_pos,
                cg="assets/cg/resume_asset_probe.png",
                bgm="assets/bgm/resume_asset_probe.ogg",
                ambient="assets/sfx/resume_asset_probe.ogg",
                voice="assets/voice/resume_asset_probe.ogg",
            ),
        ],
        choices=[
            Choice(
                id="continue",
                text="Continue",
                card=ChoiceCardSpec(
                    title="Continue",
                    image="assets/ui/resume_asset_probe_card.png",
                    image_alt="A safe authored choice card",
                ),
            ),
        ],
    )
    dialogue = _open(driver, scene)
    if case.startswith("last_line_") or case == "choice_card_image":
        dialogue.box.force_reveal()
        dialogue._advance()
        assert dialogue._current_presentation.kind == "choice"
    resume = dialogue.export_resume_context()
    assert resume is not None
    assert driver.app._authored_story_catalog.get(scene.id) is not None
    assert (
        driver.app.state.meta["__pack_story_catalog__"]
        is driver.app._authored_story_catalog
    )
    return dialogue, resume


def _forge_resume_asset(resume: dict, case: str) -> None:
    if case == "top_level_cg":
        resume["cg"] = _FORGED_RESUME_ASSET
    elif case == "top_level_current_bgm":
        resume["current_bgm"] = _FORGED_RESUME_ASSET
    elif case == "top_level_current_ambient":
        resume["current_ambient"] = _FORGED_RESUME_ASSET
    elif case == "slot_legacy_portrait":
        resume["slots"]["left"]["portrait"] = _FORGED_RESUME_ASSET
    elif case.startswith("presentation_"):
        line = resume["presentation"]["line"]
        field = case.removeprefix("presentation_")
        if field == "voice_without_checkpoint":
            line["voice"] = _FORGED_RESUME_ASSET
            # A missing checkpoint is the supported outer-v1 compatibility
            # shape. It must not let an attacker turn arbitrary speech into a
            # trusted legacy restart.
            resume.pop("voice_playback", None)
        elif field == "spec_image":
            line["portrait"]["value"]["image"] = _FORGED_RESUME_ASSET
        elif field == "spec_character":
            line["portrait"]["value"]["character"] = "../../../etc"
        elif field == "multi_spec_image":
            line["portraits"][0]["value"]["image"] = _FORGED_RESUME_ASSET
        elif field == "multi_spec_character":
            line["portraits"][0]["value"]["character"] = "../../../etc"
        else:
            line[field] = _FORGED_RESUME_ASSET
    elif case.startswith("last_line_"):
        field = case.removeprefix("last_line_")
        resume["last_line"]["line"][field] = _FORGED_RESUME_ASSET
    elif case in {"slot_spec_image", "slot_spec_character"}:
        field = case.removeprefix("slot_spec_")
        resume["slots"]["left"]["spec"][field] = (
            _FORGED_RESUME_ASSET
            if field == "image" else "../../../etc"
        )
    elif case == "choice_card_image":
        resume["presentation"]["choices"][0]["card"]["image"] = (
            _FORGED_RESUME_ASSET
        )
    else:  # pragma: no cover - keeps future matrix additions honest
        raise AssertionError(f"unknown asset provenance case: {case}")


def _contains_forged_resume_asset(value) -> bool:
    if isinstance(value, str) and value in {
        _FORGED_RESUME_ASSET,
        "../../../etc",
    }:
        return True
    if isinstance(value, PortraitSpec):
        return (
            value.image == _FORGED_RESUME_ASSET
            or value.character == "../../../etc"
        )
    if isinstance(value, dict):
        return any(_contains_forged_resume_asset(item)
                   for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_forged_resume_asset(item) for item in value)
    return False


@pytest.mark.parametrize("case", _RESUME_ASSET_PROVENANCE_CASES)
def test_resume_asset_provenance_rejects_before_direct_backend_access(
    driver, monkeypatch, case,
):
    app = driver.app
    dialogue, resume = _open_asset_provenance_resume(driver, case)
    forged = copy.deepcopy(resume)
    _forge_resume_asset(forged, case)

    backend_attempts: list[tuple[str, object]] = []
    for method_name in (
        "image",
        "scaled",
        "resolve_portrait",
        "play_music",
        "play_ambient",
        "play_voice",
    ):
        original = getattr(app.assets, method_name)

        def _record_backend(*args, _name=method_name, _original=original,
                            **kwargs):
            if _contains_forged_resume_asset((args, kwargs)):
                backend_attempts.append((_name, (args, kwargs)))
            return _original(*args, **kwargs)

        monkeypatch.setattr(app.assets, method_name, _record_backend)

    state_before = app.state.model_dump(mode="json")
    stack_before = app.manager.stack()
    line_before = copy.deepcopy(dialogue._current_line)

    preflight_result = app._resume_matches_loaded_state(forged)
    direct_result = dialogue.restore_resume_context(forged)

    assert app.state.model_dump(mode="json") == state_before
    assert app.manager.stack() == stack_before
    assert app.manager.current is dialogue
    assert dialogue._current_line == line_before
    assert (
        preflight_result,
        direct_result,
        [name for name, _args in backend_attempts],
    ) == (False, False, [])


def test_candidate_saved_story_cannot_expand_resume_asset_authority(driver):
    app = driver.app
    _dialogue, resume = _open_asset_provenance_resume(
        driver, "presentation_voice_without_checkpoint",
    )
    forged = copy.deepcopy(resume)
    _forge_resume_asset(forged, "presentation_voice_without_checkpoint")
    candidate = GameState.model_validate(app.state.model_dump(mode="json"))
    scene = candidate.story.get(candidate.story.current_scene)
    assert scene is not None
    scene.lines[0].voice = _FORGED_RESUME_ASSET

    assert app._resume_matches_state(
        forged,
        candidate,
        authored_story=app._authored_story_catalog,
    ) is False


@pytest.mark.parametrize("field", ("current_bgm", "current_ambient"))
def test_legacy_resume_audio_fallback_uses_authored_not_saved_authority(
    driver, field,
):
    app = driver.app
    _dialogue, resume = _open_asset_provenance_resume(
        driver, f"top_level_{field}",
    )
    legacy = copy.deepcopy(resume)
    legacy.pop(field)
    candidate = GameState.model_validate(app.state.model_dump(mode="json"))
    candidate.meta[field] = _FORGED_RESUME_ASSET

    assert app._resume_matches_state(
        legacy,
        candidate,
        authored_story=app._authored_story_catalog,
    ) is False


@pytest.mark.parametrize(
    "case",
    (
        "top_level_cg",
        "top_level_current_bgm",
        "slot_legacy_portrait",
        "presentation_voice_without_checkpoint",
        "last_line_ambient",
        "presentation_multi_spec_image",
    ),
)
def test_quickload_forged_resume_asset_is_atomic_before_state_swap(
    driver, case,
):
    app = driver.app
    dialogue, _resume = _open_asset_provenance_resume(driver, case)
    app._quicksave()
    slot = app.config.quicksave_slot
    path = driver._resume_save_dir / f"{slot}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    _forge_resume_asset(raw["_resume"], case)
    path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Make the live state observably different from the saved candidate. A
    # rejected envelope must leave both narrative state and scene stack intact.
    app.state.player.name = "live state must survive"
    state_before = app.state.model_dump(mode="json")
    stack_before = app.manager.stack()

    assert app._load_slot(slot) is False
    assert app.state.model_dump(mode="json") == state_before
    assert app.manager.stack() == stack_before
    assert app.manager.current is dialogue


def test_quickload_saved_story_cannot_expand_resume_asset_authority(driver):
    app = driver.app
    dialogue, _resume = _open_asset_provenance_resume(
        driver, "presentation_voice_without_checkpoint",
    )
    app._quicksave()
    slot = app.config.quicksave_slot
    path = driver._resume_save_dir / f"{slot}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    _forge_resume_asset(
        raw["_resume"], "presentation_voice_without_checkpoint",
    )
    scene_id = raw["story"]["current_scene"]
    raw["story"]["scenes"][scene_id]["lines"][0]["voice"] = (
        _FORGED_RESUME_ASSET
    )
    path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    app.state.player.name = "live story authority must survive"
    state_before = app.state.model_dump(mode="json")
    stack_before = app.manager.stack()

    assert app._load_slot(slot) is False
    assert app.state.model_dump(mode="json") == state_before
    assert app.manager.stack() == stack_before
    assert app.manager.current is dialogue


@pytest.mark.parametrize("field", ("current_bgm", "current_ambient"))
def test_quickload_legacy_audio_fallback_is_atomic_before_state_swap(
    driver, field,
):
    app = driver.app
    dialogue, _resume = _open_asset_provenance_resume(
        driver, f"top_level_{field}",
    )
    app._quicksave()
    slot = app.config.quicksave_slot
    path = driver._resume_save_dir / f"{slot}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["_resume"].pop(field)
    raw["meta"][field] = _FORGED_RESUME_ASSET
    path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    app.state.player.name = "legacy audio fallback must not swap state"
    state_before = app.state.model_dump(mode="json")
    stack_before = app.manager.stack()

    assert app._load_slot(slot) is False
    assert app.state.model_dump(mode="json") == state_before
    assert app.manager.stack() == stack_before
    assert app.manager.current is dialogue


def test_quickload_rebinds_choice_card_image_before_asset_validation(driver):
    app = driver.app
    _dialogue, _resume = _open_asset_provenance_resume(
        driver, "choice_card_image",
    )
    app._quicksave()
    slot = app.config.quicksave_slot
    path = driver._resume_save_dir / f"{slot}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    _forge_resume_asset(raw["_resume"], "choice_card_image")
    path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    assert app._load_slot(slot) is True
    driver.advance_frames(2)

    restored = app.manager.current
    assert type(restored).__name__ == "DialogueScene"
    choice = restored._current_presentation.choices[0]
    assert choice.card is not None
    assert choice.card.image == "assets/ui/resume_asset_probe_card.png"


def test_manual_load_forged_resume_asset_is_atomic_before_state_swap(driver):
    app = driver.app
    dialogue, _resume = _open_asset_provenance_resume(
        driver, "top_level_current_ambient",
    )
    app._quicksave()
    slot = app.config.quicksave_slot
    path = driver._resume_save_dir / f"{slot}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    _forge_resume_asset(raw["_resume"], "top_level_current_ambient")
    path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    app.state.player.name = "manual-load live state must survive"
    app._open_load_menu()
    app.manager.commit_pending()
    overlay = app.manager.current
    row = next(item for item in overlay._items if item.get("slot") == slot)
    state_before = app.state.model_dump(mode="json")
    stack_before = app.manager.stack()

    overlay._on_action(row)

    assert app.state.model_dump(mode="json") == state_before
    assert app.manager.stack() == stack_before
    assert app.manager.current is overlay
    assert overlay._error_msg == "存檔顯示狀態錯誤，未載入。"
