"""Per-character voice volume (WP-3A).

Two layers are covered:

1. The pure lookup contract: ``config.per_character_voice_volume.get(speaker,
   config.voice_volume)`` returns a per-speaker override when present and falls
   back to the global ``voice_volume`` otherwise.
2. The dialogue scene wiring: when a voiced line is rendered, the volume passed
   to ``assets.play_voice`` is the per-character value for that speaker, or the
   global default for a speaker with no override. ``assets.play_voice`` is
   stubbed so no real audio device is needed and the volume is captured.

The settings-scene helper ``_adjust_char_voice`` / ``_char_voice_volume`` is
also exercised to confirm it seeds from the global default and clamps.
"""
from __future__ import annotations

import os

import pygame
import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from world_gal_game.config import EngineConfig
from world_gal_game.core.story_graph import Scene, Line


@pytest.fixture(autouse=True, scope="module")
def _restore_pygame_after_module():
    """Re-init a tiny display on teardown so this module (which quits pygame
    via GameDriver) leaves the global pygame state usable for whatever test
    module pytest runs next, regardless of collection order."""
    yield
    if not pygame.display.get_init():
        pygame.init()
        pygame.display.set_mode((10, 10))


# ---------------------------------------------------------------------------
# Layer 1: pure config lookup
# ---------------------------------------------------------------------------


def test_lookup_falls_back_to_global_voice_volume():
    cfg = EngineConfig()
    cfg.voice_volume = 0.7
    # No per-character entry -> global fallback.
    assert cfg.per_character_voice_volume.get("akari", cfg.voice_volume) == 0.7


def test_lookup_uses_override_when_present():
    cfg = EngineConfig()
    cfg.voice_volume = 0.7
    cfg.per_character_voice_volume["akari"] = 0.2
    assert cfg.per_character_voice_volume.get("akari", cfg.voice_volume) == 0.2
    # An unlisted speaker still falls back.
    assert cfg.per_character_voice_volume.get("ren", cfg.voice_volume) == 0.7


# ---------------------------------------------------------------------------
# Layer 2: dialogue scene passes the right volume to play_voice
# ---------------------------------------------------------------------------


@pytest.fixture
def driver():
    from world_gal_game.dev.driver import GameDriver
    d = GameDriver(pack="demo_pack")
    yield d
    d.quit()


class _VoiceSpy:
    """Records the volume play_voice was called with; drop-in for the methods
    DialogueScene._render_presentation touches on the asset manager."""

    def __init__(self, real):
        self._real = real
        self.last_volume = None
        self.calls = []

    def play_voice(self, path, *, volume=1.0):
        self.last_volume = volume
        self.calls.append((path, volume))

    # Delegate everything else (image/scaled/play_music/...) to the real one.
    def __getattr__(self, name):
        return getattr(self._real, name)


def _dialogue_scene_with_line(driver, *, speaker, voice="assets/voice/x.ogg"):
    """Open a DialogueScene over a one-line scene that has a voice clip and a
    known speaker, with the asset manager's play_voice spied."""
    app = driver.app
    spy = _VoiceSpy(app.assets)
    app.assets = spy
    app.ctx.assets = spy
    sc = Scene(id="voice_probe", lines=[
        Line(speaker=speaker, text="hi", voice=voice),
    ])
    app.state.story.add_scene(sc)
    app._start_dialogue("voice_probe")
    app.manager.commit_pending()
    driver.advance_frames(1)
    scene = app.manager.current
    assert type(scene).__name__ == "DialogueScene"
    return scene, spy


def test_render_uses_per_character_override(driver):
    driver.app.config.voice_volume = 0.8
    driver.app.config.per_character_voice_volume["akari"] = 0.25
    _scene, spy = _dialogue_scene_with_line(driver, speaker="akari")
    assert spy.last_volume == pytest.approx(0.25)


def test_render_falls_back_to_global_for_unlisted_speaker(driver):
    driver.app.config.voice_volume = 0.6
    driver.app.config.per_character_voice_volume.clear()
    _scene, spy = _dialogue_scene_with_line(driver, speaker="nobody_special")
    assert spy.last_volume == pytest.approx(0.6)


def test_render_no_voice_path_does_not_call_play_voice(driver):
    driver.app.config.voice_volume = 0.6
    _scene, spy = _dialogue_scene_with_line(driver, speaker="akari", voice=None)
    assert spy.last_volume is None
    assert spy.calls == []


# ---------------------------------------------------------------------------
# Settings-scene helper: seed-from-global + clamp + persist key
# ---------------------------------------------------------------------------


def test_settings_helper_seeds_and_clamps(driver, tmp_path, monkeypatch):
    # Redirect persistence to a temp file so save_to_disk() is harmless.
    monkeypatch.setattr(driver.app.config, "settings_path",
                        lambda: tmp_path / "settings.json")
    from world_gal_game.scenes.settings_scene import SettingsScene
    scene = SettingsScene(driver.app.ctx)
    scene.enter(on_close=lambda: None)
    driver.app.config.voice_volume = 0.5
    driver.app.config.per_character_voice_volume.clear()
    # First nudge seeds from the global 0.5 then subtracts 0.1 -> 0.4.
    scene._adjust_char_voice("akari", -0.1)
    assert driver.app.config.per_character_voice_volume["akari"] == pytest.approx(0.4)
    # Clamp at the floor.
    for _ in range(10):
        scene._adjust_char_voice("akari", -0.1)
    assert driver.app.config.per_character_voice_volume["akari"] == 0.0
    # Clamp at the ceiling.
    for _ in range(20):
        scene._adjust_char_voice("akari", 0.1)
    assert driver.app.config.per_character_voice_volume["akari"] == 1.0
