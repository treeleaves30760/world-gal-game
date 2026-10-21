"""One-shot SFX ownership contracts for the scene replay overlay."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from world_gal_game.core.story_graph import Line, Scene as StoryScene
from world_gal_game.dev.driver import GameDriver
from world_gal_game.dialogue.dialogue_engine import DialogueEngine
from world_gal_game.scenes import scene_replay_scene as replay_module
from world_gal_game.scenes.scene_replay_scene import SceneReplayScene
from world_gal_game.ui.assets import AssetManager, AudioPlaybackSnapshot
from world_gal_game.ui.input import InputState
from world_gal_game.ui.input_bindings import InputAction


class _Channel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.current_sound = None
        self.busy = False
        self.stop_calls = 0

    def get_busy(self) -> bool:
        return self.busy

    def get_sound(self):
        return self.current_sound

    def stop(self) -> None:
        self.stop_calls += 1
        self.busy = False
        self.current_sound = None


class _Sound:
    def __init__(self, channel: _Channel) -> None:
        self.channel = channel
        self.volume = 1.0

    def set_volume(self, volume: float) -> None:
        self.volume = volume

    def play(self):
        self.channel.current_sound = self
        self.channel.busy = True
        return self.channel


@pytest.fixture
def driver():
    game = GameDriver(pack="demo_pack")
    yield game
    game.quit()


def _install_sfx_trace(driver: GameDriver) -> tuple[str, str]:
    scene_id = "replay_sfx_ownership"
    sfx_path = "assets/audio/replay_owned.wav"
    authored_scene = StoryScene(
        id=scene_id,
        title="SFX ownership regression",
        lines=[Line(text="replay line", sfx=sfx_path)],
    )
    driver.app.state.story.add_scene(authored_scene)
    # Replay validation resolves immutable authored content from this catalog.
    catalog = driver.app.state.meta.get("__pack_story_catalog__")
    if catalog is not None:
        catalog.add_scene(authored_scene.model_copy(deep=True))
    first_view = DialogueEngine(driver.app.state)
    assert first_view.start_scene(scene_id).kind == "line"
    assert first_view.next_line().kind == "end"
    return scene_id, sfx_path


def _wire_fake_sounds(monkeypatch, assets, replay_path: str):
    live_channel = _Channel(4)
    replay_channel = _Channel(5)
    live_sound = _Sound(live_channel)
    replay_sound = _Sound(replay_channel)
    sounds = {
        "live-before-replay.wav": live_sound,
        replay_path: replay_sound,
    }
    monkeypatch.setattr(assets, "sound", sounds.get)
    return live_channel, replay_channel


def _snapshot() -> AudioPlaybackSnapshot:
    return AudioPlaybackSnapshot(
        music="live-bgm.ogg",
        music_volume=0.4,
        ambient="live-ambient.ogg",
        ambient_volume=0.3,
        voice="live-voice.ogg",
        voice_volume=0.8,
        voice_was_busy=True,
    )


def test_sfx_ownership_stops_only_owned_current_playbacks(monkeypatch) -> None:
    assets = AssetManager()
    live_channel, replay_channel = _wire_fake_sounds(
        monkeypatch,
        assets,
        "owned.wav",
    )

    assets.play_sound("live-before-replay.wav")
    ownership = assets.begin_sfx_ownership()
    assets.play_sound("owned.wav")
    assets.end_sfx_ownership(ownership)

    assert live_channel.busy is True
    assert live_channel.stop_calls == 0
    assert replay_channel.busy is False
    assert replay_channel.stop_calls == 1


def test_sfx_ownership_does_not_stop_a_reused_channel(monkeypatch) -> None:
    assets = AssetManager()
    channel = _Channel(4)
    owned_sound = _Sound(channel)
    replacement_sound = _Sound(channel)
    monkeypatch.setattr(assets, "sound", lambda _path: owned_sound)

    ownership = assets.begin_sfx_ownership()
    assets.play_sound("owned.wav")
    # Simulate an integration reusing the mixer channel outside AssetManager.
    replacement_sound.play()
    assets.end_sfx_ownership(ownership)

    assert channel.busy is True
    assert channel.current_sound is replacement_sound
    assert channel.stop_calls == 0


@pytest.mark.parametrize("teardown", ["back", "complete", "overlay_exit"])
def test_replay_line_sfx_is_stopped_on_every_exit_path(
    driver,
    monkeypatch,
    teardown: str,
) -> None:
    scene_id, replay_path = _install_sfx_trace(driver)
    assets = driver.app.assets
    live_channel, replay_channel = _wire_fake_sounds(
        monkeypatch,
        assets,
        replay_path,
    )
    snapshot = _snapshot()
    restored: list[AudioPlaybackSnapshot] = []
    monkeypatch.setattr(assets, "capture_audio_playback", lambda: snapshot)
    monkeypatch.setattr(assets, "restore_audio_playback", restored.append)

    assets.play_sound("live-before-replay.wav")
    scene = SceneReplayScene(driver.app.ctx)
    scene.enter()
    assert scene._start_replay(scene_id) is True
    assert replay_channel.busy is True

    if teardown == "back":
        scene.update(
            0.0,
            InputState(actions_pressed={InputAction.BACK}),
        )
    elif teardown == "complete":
        scene._replay_scene._advance()
    else:
        scene.exit()

    assert scene._replay_scene is None
    assert scene._sfx_ownership is None
    assert live_channel.busy is True
    assert live_channel.stop_calls == 0
    assert replay_channel.busy is False
    assert replay_channel.stop_calls == 1
    assert restored == [snapshot]


def test_replay_enter_exception_releases_owned_sfx_and_restores_audio(
    driver,
    monkeypatch,
) -> None:
    scene_id, replay_path = _install_sfx_trace(driver)
    assets = driver.app.assets
    live_channel, replay_channel = _wire_fake_sounds(
        monkeypatch,
        assets,
        replay_path,
    )
    snapshot = _snapshot()
    restored: list[AudioPlaybackSnapshot] = []
    monkeypatch.setattr(assets, "capture_audio_playback", lambda: snapshot)
    monkeypatch.setattr(assets, "restore_audio_playback", restored.append)

    class _ExplodingDialogueScene:
        def __init__(self, ctx) -> None:
            self.ctx = ctx
            self.on_done = None

        def enter(self, **_) -> None:
            self.ctx.assets.play_sound(replay_path)
            raise RuntimeError("replay enter failed")

        def exit(self) -> None:
            pass

    monkeypatch.setattr(replay_module, "DialogueScene", _ExplodingDialogueScene)
    assets.play_sound("live-before-replay.wav")
    scene = SceneReplayScene(driver.app.ctx)
    scene.enter()

    try:
        started = scene._start_replay(scene_id)
    except RuntimeError:
        # Both fail-closed and propagating callers share the cleanup contract.
        started = False

    assert started is False
    assert scene._replay_scene is None
    assert scene._sfx_ownership is None
    assert live_channel.busy is True
    assert live_channel.stop_calls == 0
    assert replay_channel.busy is False
    assert replay_channel.stop_calls == 1
    assert restored == [snapshot]
