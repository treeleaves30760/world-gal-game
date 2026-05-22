"""Voice channel: per-line voice playback on the reserved mixer channel.

These run under SDL_AUDIODRIVER=dummy so the mixer is real but silent. The
guarantees: a missing voice file is a silent no-op (never raises), and the
channel reports not-busy after stop_voice().
"""
from __future__ import annotations

import os

import pygame
import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import Scene, Line
from world_gal_game.dialogue.dialogue_engine import DialogueEngine
from world_gal_game.ui.assets import AssetManager


@pytest.fixture(autouse=True, scope="module")
def init_mixer():
    pygame.init()
    pygame.display.set_mode((1, 1))
    try:
        pygame.mixer.init()
        pygame.mixer.set_num_channels(8)
        pygame.mixer.set_reserved(1)
    except pygame.error:
        pytest.skip("mixer unavailable in this environment")
    yield
    pygame.quit()


def test_play_voice_missing_path_is_silent_noop(tmp_path):
    assets = AssetManager(pack_root=tmp_path)
    # A path that does not exist must not raise and must return None.
    result = assets.play_voice("assets/voice/does_not_exist.ogg")
    assert result is None
    # Nothing was loaded, so the channel stays unset / not busy.
    assert assets.voice_busy() is False


def test_play_voice_none_path_is_noop(tmp_path):
    assets = AssetManager(pack_root=tmp_path)
    assert assets.play_voice(None) is None
    assert assets.voice_busy() is False


def test_voice_busy_false_after_stop(tmp_path):
    assets = AssetManager(pack_root=tmp_path)
    # stop_voice on a never-played channel is a no-op.
    assets.stop_voice()
    assert assets.voice_busy() is False
    # Even after attempting to play a missing clip, stop + busy stay clean.
    assets.play_voice("assets/voice/missing.ogg")
    assets.stop_voice()
    assert assets.voice_busy() is False


def test_voice_volume_default_and_override(tmp_path):
    assets = AssetManager(pack_root=tmp_path)
    assert assets._voice_volume == 1.0
    assets._voice_volume = 0.5
    assert assets._voice_volume == 0.5


# ---------------------------------------------------------------------------
# Engine round-trip: Line.voice -> LinePresentation.voice
# ---------------------------------------------------------------------------


def test_line_voice_round_trips_to_presentation():
    sc = Scene(id="s", lines=[Line(text="hi", voice="assets/voice/a.ogg")])
    state = GameState()
    state.story.add_scene(sc)
    eng = DialogueEngine(state)
    pres = eng.start_scene("s")
    assert pres.kind == "line"
    assert pres.line.voice == "assets/voice/a.ogg"


def test_line_voice_defaults_none():
    sc = Scene(id="s", lines=[Line(text="hi")])
    state = GameState()
    state.story.add_scene(sc)
    eng = DialogueEngine(state)
    pres = eng.start_scene("s")
    assert pres.line.voice is None
