"""Load-time BGM and ambient replacement must fail closed to silence."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from world_gal_game.core.story_graph import Line, Scene
from world_gal_game.dialogue.dialogue_engine import ScenePresentation


@pytest.fixture
def dialogue_driver():
    from world_gal_game.dev.driver import GameDriver

    driver = GameDriver(pack="demo_pack")
    scene = Scene(
        id="load_audio_replacement_probe",
        lines=[Line(text="audio replacement probe")],
    )
    driver.app.state.story.add_scene(scene)
    driver.app._start_dialogue(scene.id)
    driver.app.manager.commit_pending()
    driver.advance_frames(1)
    dialogue = driver.app.manager.current
    assert type(dialogue).__name__ == "DialogueScene"
    yield dialogue, driver.app.assets
    driver.quit()


@pytest.mark.parametrize("channel", ["music", "ambient"])
@pytest.mark.parametrize("failure", ["missing", "corrupt"])
def test_load_of_different_unavailable_audio_cuts_abandoned_track(
    dialogue_driver,
    monkeypatch,
    tmp_path,
    channel,
    failure,
):
    dialogue, assets = dialogue_driver
    live_path = f"assets/{channel}/live.ogg"
    saved_path = f"assets/{channel}/saved.ogg"
    broken_file = tmp_path / "saved.ogg"
    if failure == "corrupt":
        broken_file.write_bytes(b"this is not an audio stream")

    original_resolve = assets._resolve
    monkeypatch.setattr(
        assets,
        "_resolve",
        lambda path: broken_file if path == saved_path else original_resolve(path),
    )
    if channel == "music":
        assets._current_music = live_path
        resume = {"current_bgm": saved_path, "current_ambient": None}
    else:
        assets._current_ambient = live_path
        resume = {"current_bgm": None, "current_ambient": saved_path}

    dialogue._restore_audio_state(
        resume,
        ScenePresentation(kind="choice"),
    )

    current = getattr(assets, f"current_{channel}_path")
    assert current is None


@pytest.mark.parametrize("channel", ["music", "ambient"])
def test_load_of_same_audio_path_does_not_restart(channel, dialogue_driver):
    dialogue, assets = dialogue_driver
    saved_path = f"assets/{channel}/same.ogg"
    if channel == "music":
        assets._current_music = saved_path
        revision_before = assets._music_revision
        resume = {"current_bgm": saved_path, "current_ambient": None}
    else:
        assets._current_ambient = saved_path
        revision_before = assets._ambient_revision
        resume = {"current_bgm": None, "current_ambient": saved_path}

    dialogue._restore_audio_state(
        resume,
        ScenePresentation(kind="choice"),
    )

    assert getattr(assets, f"current_{channel}_path") == saved_path
    assert getattr(assets, f"_{channel}_revision") == revision_before


@pytest.mark.parametrize("channel", ["music", "ambient"])
def test_load_hard_cuts_untracked_outgoing_fade_before_missing_track(
    channel,
    dialogue_driver,
):
    dialogue, assets = dialogue_driver

    class _OutgoingChannel:
        def __init__(self) -> None:
            self.fadeouts: list[int] = []

        def get_busy(self) -> bool:
            return True

        def fadeout(self, milliseconds: int) -> None:
            self.fadeouts.append(milliseconds)

    outgoing = _OutgoingChannel()
    saved_path = f"assets/{channel}/missing-after-fade.ogg"
    if channel == "music":
        assets._bgm_channels = [outgoing]
        assets._current_music = None
        resume = {"current_bgm": saved_path, "current_ambient": None}
    else:
        assets._ambient_channel = outgoing
        assets._current_ambient = None
        resume = {"current_bgm": None, "current_ambient": saved_path}

    dialogue._restore_audio_state(
        resume,
        ScenePresentation(kind="choice"),
    )

    assert outgoing.fadeouts == [0]
