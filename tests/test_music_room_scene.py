"""WP-B — music-room overlay.

Covers BGM enumeration from a pack's ``assets/bgm/`` directory, the
unlocked-vs-locked playability gate, play/stop wiring through
``AssetManager.play_music``, the now-playing indicator, the empty case,
``describe()``'s JSON-able shape, and the stop-on-close contract (a preview
must not hijack the underlying scene's BGM).

The scene is exercised directly against a hand-built ``SceneContext`` so the
test stays fast and deterministic: a *real* ``AssetManager`` (real
``_resolve`` + real directory enumeration), real ``Theme`` / ``FontRegistry``
/ ``EngineConfig`` / ``GameState`` / ``Localization``. Only ``play_music`` is
swapped for a recorder, because pygame's mixer cannot stream audio under the
dummy SDL driver — the swap keeps the test about the scene's logic, not the
mixer.
"""
from __future__ import annotations

import os
from pathlib import Path

import pygame
import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _pygame_init():
    pygame.display.init()
    pygame.font.init()
    yield
    pygame.quit()


class _RecordingAssets:
    """A real AssetManager wrapper that records play_music calls.

    Subclasses nothing; we just instantiate the real manager and monkeypatch
    its ``play_music`` so ``_current_music`` updates deterministically without
    a working mixer.
    """


def _make_assets(pack_root: Path):
    from world_gal_game.ui.assets import AssetManager
    assets = AssetManager(pack_root=pack_root)
    calls: list = []

    def fake_play_music(path, *, volume=0.6, loops=-1, fade_ms=800):
        calls.append({"path": path, "volume": volume})
        # Mirror the real contract: None stops; otherwise becomes current.
        assets._current_music = path
    assets.play_music = fake_play_music  # type: ignore[assignment]
    return assets, calls


def _make_ctx(pack_root: Path):
    from world_gal_game.config import EngineConfig
    from world_gal_game.core.game_state import GameState
    from world_gal_game.core.localization import Localization
    from world_gal_game.scenes.base import SceneContext
    from world_gal_game.ui.fonts import FontRegistry
    from world_gal_game.ui.theme import default_theme

    assets, calls = _make_assets(pack_root)
    fonts = FontRegistry(candidates=())
    ctx = SceneContext(
        config=EngineConfig(),
        state=GameState(),
        npcs=None,            # type: ignore[arg-type]  (scene never uses these)
        brain=None,           # type: ignore[arg-type]
        dialogue=None,        # type: ignore[arg-type]
        assets=assets,
        fonts=fonts,
        theme=default_theme(),
        localization=Localization(),
        screen_size=(1280, 720),
    )
    return ctx, calls


def _bgm_pack(tmp_path: Path, names) -> Path:
    """Create a pack root with ``assets/bgm/<name>`` files. Returns the root."""
    bgm = tmp_path / "assets" / "bgm"
    bgm.mkdir(parents=True)
    for n in names:
        (bgm / n).write_bytes(b"\x00")  # content is irrelevant; we never load it
    return tmp_path


class _Input:
    """Minimal stand-in for the per-frame InputState the scene reads."""

    def __init__(self, *, cancel=False, mouse_pos=(0, 0), mouse_clicked=False,
                 mouse_wheel=0):
        self.cancel = cancel
        self.mouse_pos = mouse_pos
        self.mouse_clicked = mouse_clicked
        self.mouse_wheel = mouse_wheel


def _surface():
    return pygame.Surface((1280, 720), pygame.SRCALPHA)


def _open(ctx, on_close=None):
    from world_gal_game.scenes.music_room_scene import MusicRoomScene
    scene = MusicRoomScene(ctx)
    scene.enter(on_close=on_close)
    return scene


def _row_for(scene, ctx, path):
    """Draw one frame, then return the screen rect for a track row (or None).

    Hit rects are only registered for unlocked rows; the drawer recomputes
    them every draw, so we draw first and read ``_row_rects`` after.
    """
    scene.draw(_surface())
    for rect, p in scene._row_rects:
        if p == path:
            return rect
    return None


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------

def test_enumerates_bgm_dir_as_pack_relative_sorted(tmp_path):
    root = _bgm_pack(tmp_path, ["b.ogg", "a.mp3", "c.wav"])
    ctx, _ = _make_ctx(root)
    scene = _open(ctx)
    assert scene._tracks == [
        "assets/bgm/a.mp3", "assets/bgm/b.ogg", "assets/bgm/c.wav",
    ]


def test_ignores_non_audio_files(tmp_path):
    root = _bgm_pack(tmp_path, ["song.ogg", "readme.txt", "cover.png"])
    ctx, _ = _make_ctx(root)
    scene = _open(ctx)
    assert scene._tracks == ["assets/bgm/song.ogg"]


def test_extension_match_is_case_insensitive(tmp_path):
    root = _bgm_pack(tmp_path, ["Theme.OGG", "Intro.Mp3"])
    ctx, _ = _make_ctx(root)
    scene = _open(ctx)
    assert scene._tracks == ["assets/bgm/Intro.Mp3", "assets/bgm/Theme.OGG"]


def test_empty_bgm_dir_yields_no_tracks(tmp_path):
    root = _bgm_pack(tmp_path, [])
    ctx, _ = _make_ctx(root)
    scene = _open(ctx)
    assert scene._tracks == []
    # Drawing the empty case must not raise.
    scene.draw(_surface())
    assert scene.describe()["track_count"] == 0


def test_fallback_to_unlocked_set_when_no_dir(tmp_path):
    # No assets/bgm directory at all → degrade to the save's unlocked set.
    root = tmp_path / "packroot"
    root.mkdir()
    ctx, _ = _make_ctx(root)
    ctx.state.music_room.unlock("assets/bgm/heard.ogg")
    scene = _open(ctx)
    assert scene._tracks == ["assets/bgm/heard.ogg"]


def test_unlocked_track_outside_bgm_dir_still_listed(tmp_path):
    root = _bgm_pack(tmp_path, ["main.ogg"])
    ctx, _ = _make_ctx(root)
    ctx.state.music_room.unlock("assets/special/secret.ogg")
    scene = _open(ctx)
    assert "assets/bgm/main.ogg" in scene._tracks
    assert "assets/special/secret.ogg" in scene._tracks


# ---------------------------------------------------------------------------
# Playability gate
# ---------------------------------------------------------------------------

def test_locked_track_is_not_playable(tmp_path):
    root = _bgm_pack(tmp_path, ["locked.ogg"])
    ctx, calls = _make_ctx(root)
    scene = _open(ctx)
    # Nothing unlocked → no clickable hit rect for the row.
    assert _row_for(scene, ctx, "assets/bgm/locked.ogg") is None
    # And calling _play directly is a no-op for a locked track.
    scene._play("assets/bgm/locked.ogg")
    assert calls == []
    assert scene._now_playing() is None


def test_unlocked_track_plays_at_bgm_volume(tmp_path):
    root = _bgm_pack(tmp_path, ["main.ogg"])
    ctx, calls = _make_ctx(root)
    ctx.config.bgm_volume = 0.42
    ctx.state.music_room.unlock("assets/bgm/main.ogg")
    scene = _open(ctx)
    rect = _row_for(scene, ctx, "assets/bgm/main.ogg")
    assert rect is not None
    scene.update(0.0, _Input(mouse_clicked=True, mouse_pos=rect.center))
    assert calls == [{"path": "assets/bgm/main.ogg", "volume": 0.42}]
    assert scene._now_playing() == "assets/bgm/main.ogg"
    assert scene.describe()["now_playing"] == "assets/bgm/main.ogg"


def test_describe_counts(tmp_path):
    root = _bgm_pack(tmp_path, ["a.ogg", "b.ogg", "c.ogg"])
    ctx, _ = _make_ctx(root)
    ctx.state.music_room.unlock("assets/bgm/a.ogg")
    ctx.state.music_room.unlock("assets/bgm/c.ogg")
    scene = _open(ctx)
    d = scene.describe()
    assert d == {
        "scene": "MusicRoomScene",
        "track_count": 3,
        "unlocked_count": 2,
        "now_playing": None,
    }
    # JSON-able: no sets / surfaces leaked into the dict.
    import json
    assert json.loads(json.dumps(d)) == d


# ---------------------------------------------------------------------------
# Stop control + now-playing
# ---------------------------------------------------------------------------

def test_stop_button_clears_playback(tmp_path):
    root = _bgm_pack(tmp_path, ["main.ogg"])
    ctx, calls = _make_ctx(root)
    ctx.state.music_room.unlock("assets/bgm/main.ogg")
    scene = _open(ctx)
    scene._play("assets/bgm/main.ogg")
    assert scene._now_playing() == "assets/bgm/main.ogg"
    scene._stop()
    assert calls[-1] == {"path": None, "volume": 0.6}
    assert scene._now_playing() is None


# ---------------------------------------------------------------------------
# Close behavior (the documented contract)
# ---------------------------------------------------------------------------

def test_close_stops_track_started_in_room(tmp_path):
    root = _bgm_pack(tmp_path, ["main.ogg"])
    ctx, calls = _make_ctx(root)
    ctx.state.music_room.unlock("assets/bgm/main.ogg")
    closed = {"n": 0}
    scene = _open(ctx, on_close=lambda: closed.__setitem__("n", closed["n"] + 1))
    scene._play("assets/bgm/main.ogg")
    calls.clear()
    scene._close()
    # Preview must not bleed into the scene below: playback is stopped.
    assert {"path": None, "volume": 0.6} in calls
    assert scene._now_playing() is None
    assert closed["n"] == 1


def test_close_leaves_scene_bgm_when_nothing_started(tmp_path):
    root = _bgm_pack(tmp_path, ["main.ogg"])
    ctx, calls = _make_ctx(root)
    ctx.state.music_room.unlock("assets/bgm/main.ogg")
    # Simulate the underlying scene's BGM already playing.
    ctx.assets._current_music = "assets/bgm/scene_theme.ogg"
    closed = {"n": 0}
    scene = _open(ctx, on_close=lambda: closed.__setitem__("n", closed["n"] + 1))
    scene._close()
    # We never started a preview → don't touch existing playback.
    assert calls == []
    assert ctx.assets._current_music == "assets/bgm/scene_theme.ogg"
    assert closed["n"] == 1


def test_cancel_input_closes(tmp_path):
    root = _bgm_pack(tmp_path, ["main.ogg"])
    ctx, _ = _make_ctx(root)
    closed = {"n": 0}
    scene = _open(ctx, on_close=lambda: closed.__setitem__("n", closed["n"] + 1))
    scene.update(0.0, _Input(cancel=True))
    assert closed["n"] == 1


# ---------------------------------------------------------------------------
# Render smoke (no crash with a mix of locked/unlocked/playing rows)
# ---------------------------------------------------------------------------

def test_draw_does_not_raise_with_mixed_rows(tmp_path):
    root = _bgm_pack(tmp_path, ["a.ogg", "b.ogg", "c.ogg"])
    ctx, _ = _make_ctx(root)
    ctx.state.music_room.unlock("assets/bgm/a.ogg")
    scene = _open(ctx)
    scene._play("assets/bgm/a.ogg")   # one row "now playing"
    scene.draw(_surface())             # locked + unlocked + playing all rendered
    # Updating with a scroll wheel event must also be safe.
    scene.update(0.0, _Input(mouse_wheel=-1, mouse_pos=(640, 360)))
