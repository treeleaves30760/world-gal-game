"""Dialogue save entry points preserve fail-closed voice checkpoints."""
from __future__ import annotations

import copy
import json
import os
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from world_gal_game.core.story_graph import Choice, Effect, Line, Scene


VOICE_PATH = "assets/voice/integration-line.ogg"
PCM_SHA256 = "1" * 64
MIXER_FORMAT = [48_000, -16, 2]


def _checkpoint(
    *,
    state: str = "playing",
    position: float = 1.25,
    duration: float = 4.0,
) -> dict[str, object]:
    return {
        "version": 2,
        "state": state,
        "position_seconds": position,
        "duration_seconds": duration,
        "pcm_sha256": PCM_SHA256,
        "mixer_format": list(MIXER_FORMAT),
        "fallback": "silence",
    }


@dataclass
class _VoiceHarness:
    """AssetManager-shaped voice double with an owned playback handle."""

    checkpoint: dict[str, object] = field(default_factory=_checkpoint)
    handle: object = field(default_factory=object)
    play_calls: list[dict[str, object]] = field(default_factory=list)
    capture_calls: list[dict[str, object]] = field(default_factory=list)
    resume_calls: list[dict[str, object]] = field(default_factory=list)
    stop_calls: int = 0

    def install(self, monkeypatch: pytest.MonkeyPatch, assets: Any) -> None:
        monkeypatch.setattr(assets, "play_voice", self.play_voice)
        monkeypatch.setattr(
            assets,
            "capture_voice_playback",
            self.capture_voice_playback,
        )
        monkeypatch.setattr(assets, "resume_voice", self.resume_voice)
        monkeypatch.setattr(assets, "stop_voice", self.stop_voice)

    def play_voice(
        self,
        path: str | None,
        *,
        volume: float = 1.0,
    ) -> object:
        self.play_calls.append({"path": path, "volume": volume})
        return self.handle

    def capture_voice_playback(
        self,
        expected_path: str | None = None,
        *,
        handle: object | None = None,
    ) -> dict[str, object] | None:
        # Overlay audio snapshots do not provide provenance. Only the
        # DialogueScene export, which supplies both authored path and handle,
        # is allowed to claim this checkpoint.
        if expected_path is None:
            return None
        self.capture_calls.append({
            "expected_path": expected_path,
            "handle": handle,
        })
        assert expected_path == VOICE_PATH
        assert handle is self.handle
        return copy.deepcopy(self.checkpoint)

    def resume_voice(
        self,
        path: str | None,
        *,
        position_seconds: object,
        duration_seconds: object,
        pcm_sha256: object,
        mixer_format: object,
        volume: float = 1.0,
    ) -> str:
        self.resume_calls.append({
            "path": path,
            "position_seconds": position_seconds,
            "duration_seconds": duration_seconds,
            "pcm_sha256": pcm_sha256,
            "mixer_format": mixer_format,
            "volume": volume,
        })
        return "exact"

    def stop_voice(self) -> None:
        self.stop_calls += 1

    def clear_runtime_calls(self) -> None:
        self.play_calls.clear()
        self.resume_calls.clear()
        self.stop_calls = 0


@pytest.fixture
def driver(tmp_path: Path):
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
    drv._voice_resume_save_dir = save_dir
    yield drv
    drv.quit()


def _open(driver, scene: Scene):
    app = driver.app
    app.state.story.add_scene(scene)
    app._authored_story_catalog.add_scene(scene.model_copy(deep=True))
    app._start_dialogue(scene.id)
    app.manager.commit_pending()
    driver.advance_frames(1)
    dialogue = app.manager.current
    assert type(dialogue).__name__ == "DialogueScene"
    return dialogue


def _save_path(driver, slot: str | None = None) -> Path:
    slot = slot or driver.app.config.quicksave_slot
    return driver._voice_resume_save_dir / f"{slot}.json"


def _read_save(driver, slot: str | None = None) -> dict[str, Any]:
    return json.loads(_save_path(driver, slot).read_text(encoding="utf-8"))


@pytest.mark.parametrize("nvl_mode", [False, True], ids=["adv", "nvl"])
def test_quickload_resumes_voiced_line_at_checkpoint_in_adv_and_nvl(
    driver,
    monkeypatch: pytest.MonkeyPatch,
    nvl_mode: bool,
) -> None:
    app = driver.app
    app.config.nvl_mode = nvl_mode
    voice = _VoiceHarness()
    voice.install(monkeypatch, app.assets)
    dialogue = _open(
        driver,
        Scene(
            id=f"voice_checkpoint_{'nvl' if nvl_mode else 'adv'}",
            lines=[
                Line(speaker="A", text="save mid-voice", voice=VOICE_PATH),
                Line(text="disturbed live line"),
            ],
        ),
    )

    app._quicksave()
    raw = _read_save(driver)
    assert raw["_resume"]["voice_playback"] == _checkpoint()

    dialogue.box.force_reveal()
    dialogue._advance()
    voice.clear_runtime_calls()

    assert app._load_slot(app.config.quicksave_slot) is True
    driver.advance_frames(2)

    restored = app.manager.current
    assert restored._current_line.text == "save mid-voice"
    assert voice.play_calls == []
    assert voice.resume_calls == [{
        "path": VOICE_PATH,
        "position_seconds": pytest.approx(1.25),
        "duration_seconds": pytest.approx(4.0),
        "pcm_sha256": PCM_SHA256,
        "mixer_format": MIXER_FORMAT,
        "volume": pytest.approx(app.config.voice_volume),
    }]


def test_completed_voice_checkpoint_quickloads_silently(
    driver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = driver.app
    voice = _VoiceHarness(
        checkpoint=_checkpoint(
            state="completed",
            position=4.0,
            duration=4.0,
        ),
    )
    voice.install(monkeypatch, app.assets)
    dialogue = _open(
        driver,
        Scene(
            id="completed_voice_checkpoint",
            lines=[
                Line(text="already heard", voice=VOICE_PATH),
                Line(text="disturbed live line"),
            ],
        ),
    )

    app._quicksave()
    assert _read_save(driver)["_resume"]["voice_playback"] == _checkpoint(
        state="completed",
        position=4.0,
        duration=4.0,
    )
    dialogue._advance()
    voice.clear_runtime_calls()

    assert app._load_slot(app.config.quicksave_slot) is True
    driver.advance_frames(2)

    assert app.manager.current._current_line.text == "already heard"
    assert voice.stop_calls >= 1
    assert voice.resume_calls == []
    assert voice.play_calls == []


def test_legacy_resume_without_voice_field_restarts_from_zero(
    driver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = driver.app
    voice = _VoiceHarness()
    voice.install(monkeypatch, app.assets)
    dialogue = _open(
        driver,
        Scene(
            id="legacy_voice_checkpoint",
            lines=[
                Line(text="legacy voiced line", voice=VOICE_PATH),
                Line(text="disturbed live line"),
            ],
        ),
    )
    app._quicksave()
    raw = _read_save(driver)
    assert raw["_resume"].pop("voice_playback") == _checkpoint()
    _save_path(driver).write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    dialogue._advance()
    voice.clear_runtime_calls()
    assert app._load_slot(app.config.quicksave_slot) is True
    driver.advance_frames(2)

    assert voice.resume_calls == []
    assert voice.play_calls == [{
        "path": VOICE_PATH,
        "volume": pytest.approx(app.config.voice_volume),
    }]


def test_v1_voice_checkpoint_loads_with_declared_silent_fallback(
    driver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = driver.app
    voice = _VoiceHarness()
    voice.install(monkeypatch, app.assets)
    dialogue = _open(
        driver,
        Scene(
            id="v1_voice_checkpoint_silent_fallback",
            lines=[
                Line(text="old checkpoint", voice=VOICE_PATH),
                Line(text="disturbed live line"),
            ],
        ),
    )
    app._quicksave()
    raw = _read_save(driver)
    checkpoint = raw["_resume"]["voice_playback"]
    checkpoint["version"] = 1
    checkpoint.pop("pcm_sha256")
    checkpoint.pop("mixer_format")
    _save_path(driver).write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    dialogue._advance()
    voice.clear_runtime_calls()
    assert app._load_slot(app.config.quicksave_slot) is True
    driver.advance_frames(2)

    assert app.manager.current._current_line.text == "old checkpoint"
    assert voice.play_calls == []
    assert voice.resume_calls == []


@pytest.mark.parametrize(
    ("field_name", "hostile_value"),
    [
        pytest.param("position_seconds", True, id="bool-position"),
        pytest.param("duration_seconds", False, id="bool-duration"),
        pytest.param("position_seconds", float("nan"), id="nan"),
        pytest.param("position_seconds", -0.01, id="negative"),
        pytest.param("position_seconds", 4.01, id="past-duration"),
    ],
)
def test_hostile_voice_numbers_are_rejected_before_restore(
    driver,
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
    hostile_value: object,
) -> None:
    app = driver.app
    voice = _VoiceHarness()
    voice.install(monkeypatch, app.assets)
    dialogue = _open(
        driver,
        Scene(
            id=f"hostile_voice_number_{field_name}",
            lines=[Line(text="trusted authored line", voice=VOICE_PATH)],
        ),
    )
    resume = dialogue.export_resume_context()
    assert resume is not None
    assert app._resume_matches_loaded_state(resume) is True

    forged = copy.deepcopy(resume)
    forged["voice_playback"][field_name] = hostile_value
    voice.clear_runtime_calls()

    assert app._resume_matches_loaded_state(forged) is False
    assert dialogue.restore_resume_context(forged) is False
    assert voice.play_calls == []
    assert voice.resume_calls == []


@pytest.mark.parametrize(
    "presentation_kind",
    ["silent-line", "choice"],
)
def test_voice_checkpoint_is_rejected_on_silent_line_or_choice(
    driver,
    monkeypatch: pytest.MonkeyPatch,
    presentation_kind: str,
) -> None:
    app = driver.app
    voice = _VoiceHarness()
    voice.install(monkeypatch, app.assets)
    if presentation_kind == "silent-line":
        dialogue = _open(
            driver,
            Scene(
                id="voice_checkpoint_on_silent_line",
                lines=[Line(text="silence is authoritative")],
            ),
        )
    else:
        dialogue = _open(
            driver,
            Scene(
                id="voice_checkpoint_on_choice",
                lines=[Line(text="line before choice", voice=VOICE_PATH)],
                choices=[Choice(id="only", text="Choose")],
            ),
        )
        dialogue.box.force_reveal()
        dialogue._advance()
        assert dialogue._current_presentation.kind == "choice"

    resume = dialogue.export_resume_context()
    assert resume is not None
    assert resume["voice_playback"] is None
    forged = copy.deepcopy(resume)
    forged["voice_playback"] = _checkpoint()

    assert app._resume_matches_loaded_state(forged) is False
    assert dialogue.restore_resume_context(forged) is False


@pytest.mark.parametrize(
    "attack",
    ["presentation-path-swap", "checkpoint-path-injection"],
)
def test_voice_checkpoint_cannot_replace_or_embed_asset_provenance(
    driver,
    monkeypatch: pytest.MonkeyPatch,
    attack: str,
) -> None:
    app = driver.app
    voice = _VoiceHarness()
    voice.install(monkeypatch, app.assets)
    dialogue = _open(
        driver,
        Scene(
            id=f"voice_path_provenance_{attack}",
            lines=[Line(text="authored source wins", voice=VOICE_PATH)],
        ),
    )
    resume = dialogue.export_resume_context()
    assert resume is not None
    forged = copy.deepcopy(resume)
    if attack == "presentation-path-swap":
        forged["presentation"]["line"]["voice"] = (
            "assets/voice/attacker-controlled.ogg"
        )
    else:
        forged["voice_playback"]["path"] = (
            "assets/voice/attacker-controlled.ogg"
        )

    assert app._resume_matches_loaded_state(forged) is False
    assert dialogue.restore_resume_context(forged) is False


def test_manual_save_carries_the_same_voice_checkpoint(
    driver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = driver.app
    voice = _VoiceHarness()
    voice.install(monkeypatch, app.assets)
    _open(
        driver,
        Scene(
            id="manual_voice_checkpoint",
            lines=[Line(text="manual save", voice=VOICE_PATH)],
        ),
    )

    app._open_save_menu()
    app.manager.commit_pending()
    save_overlay = app.manager.current
    assert type(save_overlay).__name__ == "SaveScene"
    save_overlay._on_action(save_overlay._items[0])

    manual_paths = list(driver._voice_resume_save_dir.glob("slot_*.json"))
    assert len(manual_paths) == 1
    raw = json.loads(manual_paths[0].read_text(encoding="utf-8"))
    assert raw["_resume"]["voice_playback"] == _checkpoint()


def test_autosave_carries_the_same_voice_checkpoint_after_commit(
    driver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = driver.app
    app.config.autosave_enabled = True
    app.config.autosave_slot_count = 1
    voice = _VoiceHarness()
    voice.install(monkeypatch, app.assets)
    _open(
        driver,
        Scene(
            id="autosave_voice_checkpoint",
            lines=[
                Line(
                    text="autosave after presentation commit",
                    voice=VOICE_PATH,
                    effects=[Effect(kind="advance_time", value=1)],
                ),
            ],
        ),
    )

    raw = _read_save(driver, "autosave_1")
    assert raw["_resume"]["presentation"]["line"]["text"] == (
        "autosave after presentation commit"
    )
    assert raw["_resume"]["voice_playback"] == _checkpoint()
