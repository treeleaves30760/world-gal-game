"""WP-B — music-room overlay.

Covers BGM enumeration from a pack's ``assets/bgm/`` directory, the
unlocked-vs-locked playability gate, play/stop wiring through
``AssetManager.play_music``, the now-playing indicator, the empty case,
``describe()``'s JSON-able shape, and the snapshot-on-enter ownership contract
(a preview must restore the underlying BGM/ambient/voice on every exit path).

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
        if path == assets._current_music:
            return
        calls.append({"path": path, "volume": volume})
        # Mirror the ownership fields/revision used by AudioPlaybackSnapshot.
        assets._music_revision += 1
        assets._bgm_volume = volume
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

    def __init__(self, *, cancel=False, confirm=False, mouse_pos=(0, 0),
                 mouse_clicked=False, mouse_wheel=0, actions=(), events=(),
                 touch_started=False, touch_released=False,
                 touch_active=False, touch_delta=(0, 0)):
        self.cancel = cancel
        self.confirm = confirm
        self.mouse_pos = mouse_pos
        self.mouse_clicked = mouse_clicked
        self.mouse_wheel = mouse_wheel
        self.actions_pressed = set(actions)
        self.events = list(events)
        self.touch_started = touch_started
        self.touch_released = touch_released
        self.touch_active = touch_active
        self.touch_delta = touch_delta

    def pressed(self, action):
        return action in self.actions_pressed


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


def test_media_authority_hides_and_refuses_forged_unlocked_track(tmp_path):
    from world_gal_game.core.media_authority import build_media_authority

    root = _bgm_pack(tmp_path, ["main.ogg"])
    ctx, calls = _make_ctx(root)
    allowed = "assets/bgm/main.ogg"
    forged = "/tmp/save-forged.ogg"
    ctx.state.music_room.unlocked.update({allowed, forged})
    ctx.state.meta["__pack_media_authority__"] = build_media_authority(
        ctx.state.story,
        additional_music_ids={allowed},
    )

    scene = _open(ctx)

    assert scene._tracks == [allowed]
    scene._play(forged)
    assert calls == []


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
# Audio ownership on close / exit (the documented contract)
# ---------------------------------------------------------------------------

def test_close_returns_to_silence_after_preview_from_silence(tmp_path):
    root = _bgm_pack(tmp_path, ["main.ogg"])
    ctx, calls = _make_ctx(root)
    ctx.state.music_room.unlock("assets/bgm/main.ogg")
    closed = {"n": 0}
    scene = _open(ctx, on_close=lambda: closed.__setitem__("n", closed["n"] + 1))
    scene._play("assets/bgm/main.ogg")
    calls.clear()
    scene._close()
    # The entry snapshot was silent. Restoring it stops the preview without
    # inventing another track.
    assert calls == []
    assert scene._now_playing() is None
    assert closed["n"] == 1


def test_close_restores_scene_bgm_after_preview(tmp_path):
    root = _bgm_pack(tmp_path, ["main.ogg"])
    ctx, calls = _make_ctx(root)
    original = "assets/bgm/scene_theme.ogg"
    ctx.assets._current_music = original
    ctx.assets._bgm_volume = 0.31
    ctx.state.music_room.unlock("assets/bgm/main.ogg")
    live_before = ctx.state.model_dump(mode="json")
    closed = {"n": 0}
    scene = _open(
        ctx,
        on_close=lambda: closed.__setitem__("n", closed["n"] + 1),
    )

    scene._play("assets/bgm/main.ogg")
    calls.clear()
    scene._close()

    assert calls == [{"path": original, "volume": 0.31}]
    assert scene._now_playing() == original
    assert ctx.assets.get_music_volume() == pytest.approx(0.31)
    assert ctx.state.model_dump(mode="json") == live_before
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


def test_stop_is_temporary_and_close_restores_entry_bgm(tmp_path):
    root = _bgm_pack(tmp_path, ["main.ogg"])
    ctx, calls = _make_ctx(root)
    original = "assets/bgm/scene_theme.ogg"
    ctx.assets._current_music = original
    ctx.assets._bgm_volume = 0.28
    ctx.state.music_room.unlock("assets/bgm/main.ogg")
    scene = _open(ctx)
    scene._play("assets/bgm/main.ogg")
    scene._stop()
    assert scene._now_playing() is None

    calls.clear()
    scene._close()

    assert calls == [{"path": original, "volume": 0.28}]
    assert scene._now_playing() == original


def test_cancel_input_restores_preview_and_closes(tmp_path):
    root = _bgm_pack(tmp_path, ["main.ogg"])
    ctx, calls = _make_ctx(root)
    original = "assets/bgm/scene_theme.ogg"
    ctx.assets._current_music = original
    ctx.state.music_room.unlock("assets/bgm/main.ogg")
    closed = {"n": 0}
    scene = _open(ctx, on_close=lambda: closed.__setitem__("n", closed["n"] + 1))
    scene._play("assets/bgm/main.ogg")
    calls.clear()

    scene.update(0.0, _Input(cancel=True))

    assert calls == [{"path": original, "volume": 0.6}]
    assert scene._now_playing() == original
    assert closed["n"] == 1


def test_close_button_uses_the_same_restore_path(tmp_path):
    root = _bgm_pack(tmp_path, ["main.ogg"])
    ctx, calls = _make_ctx(root)
    original = "assets/bgm/title_theme.ogg"
    ctx.assets._current_music = original
    ctx.state.music_room.unlock("assets/bgm/main.ogg")
    closed = {"n": 0}
    scene = _open(ctx, on_close=lambda: closed.__setitem__("n", closed["n"] + 1))
    scene._play("assets/bgm/main.ogg")
    calls.clear()

    assert scene.close_btn.activate() is True

    assert calls == [{"path": original, "volume": 0.6}]
    assert scene._now_playing() == original
    assert closed["n"] == 1


def test_direct_exit_restores_once_and_is_idempotent(tmp_path):
    root = _bgm_pack(tmp_path, ["main.ogg"])
    ctx, calls = _make_ctx(root)
    original = "assets/bgm/scene_theme.ogg"
    ctx.assets._current_music = original
    ctx.state.music_room.unlock("assets/bgm/main.ogg")
    scene = _open(ctx)
    scene._play("assets/bgm/main.ogg")
    calls.clear()

    scene.exit()
    scene.exit()

    assert calls == [{"path": original, "volume": 0.6}]
    assert scene._now_playing() == original
    assert scene._audio_snapshot is None
    assert scene._started_playback is False


def test_scene_manager_pop_restores_entry_bgm(tmp_path):
    from world_gal_game.scenes.base import SceneManager
    from world_gal_game.scenes.music_room_scene import MusicRoomScene

    root = _bgm_pack(tmp_path, ["main.ogg"])
    ctx, calls = _make_ctx(root)
    original = "assets/bgm/scene_theme.ogg"
    ctx.assets._current_music = original
    ctx.state.music_room.unlock("assets/bgm/main.ogg")
    manager = SceneManager()
    scene = MusicRoomScene(ctx)
    manager.push(scene)
    manager.commit_pending()
    scene._play("assets/bgm/main.ogg")
    calls.clear()

    manager.pop()
    manager.commit_pending()

    assert manager.current is None
    assert calls == [{"path": original, "volume": 0.6}]
    assert scene._now_playing() == original


def test_exit_restores_bgm_ambient_and_interrupted_voice(tmp_path):
    root = _bgm_pack(tmp_path, ["main.ogg"])
    ctx, calls = _make_ctx(root)
    assets = ctx.assets
    original_music = "assets/bgm/scene_theme.ogg"
    original_ambient = "assets/ambient/rain.ogg"
    original_voice = "assets/voice/current.ogg"
    assets._current_music = original_music
    assets._bgm_volume = 0.33
    assets._current_ambient = original_ambient
    assets._ambient_volume = 0.21
    assets._current_voice = original_voice
    assets._current_voice_volume = 0.79
    assets.voice_busy = lambda: True  # type: ignore[method-assign]
    ctx.state.music_room.unlock("assets/bgm/main.ogg")
    scene = _open(ctx)

    # MusicRoom itself only previews BGM. Simulate another temporary audio
    # owner replacing the other buses while the overlay is active to prove its
    # captured snapshot owns and restores all three buses on teardown.
    scene._play("assets/bgm/main.ogg")
    assets._current_ambient = "assets/ambient/preview.ogg"
    assets._ambient_volume = 0.9
    assets._ambient_revision += 1
    assets._current_voice = "assets/voice/preview.ogg"
    assets._current_voice_volume = 0.8
    assets._voice_revision += 1
    ambient_calls: list[tuple[str, str | None, float]] = []
    voice_calls: list[tuple[str, str | None, float]] = []

    def stop_ambient(*, fade_ms=1000):
        ambient_calls.append(("stop", None, float(fade_ms)))
        assets._ambient_revision += 1
        assets._current_ambient = None

    def play_ambient(path, *, volume=0.4, fade_ms=1000):
        ambient_calls.append(("play", path, volume))
        assets._ambient_revision += 1
        assets._current_ambient = path
        assets._ambient_volume = volume

    def stop_voice():
        voice_calls.append(("stop", None, 0.0))
        assets._voice_revision += 1
        assets._current_voice = None

    def play_voice(path, *, volume=1.0):
        voice_calls.append(("play", path, volume))
        assets._voice_revision += 1
        assets._current_voice = path
        assets._current_voice_volume = volume

    assets.stop_ambient = stop_ambient  # type: ignore[method-assign]
    assets.play_ambient = play_ambient  # type: ignore[method-assign]
    assets.stop_voice = stop_voice  # type: ignore[method-assign]
    assets.play_voice = play_voice  # type: ignore[method-assign]
    calls.clear()

    scene.exit()

    assert calls == [{"path": original_music, "volume": 0.33}]
    assert assets._current_music == original_music
    assert assets._current_ambient == original_ambient
    assert assets._ambient_volume == pytest.approx(0.21)
    assert assets._current_voice == original_voice
    assert assets._current_voice_volume == pytest.approx(0.79)
    assert ambient_calls[-1] == ("play", original_ambient, 0.21)
    assert voice_calls[-1] == ("play", original_voice, 0.79)


def test_enter_failure_restores_audio_before_reraising(tmp_path):
    from world_gal_game.scenes.music_room_scene import MusicRoomScene

    root = _bgm_pack(tmp_path, ["main.ogg"])
    ctx, calls = _make_ctx(root)
    original = "assets/bgm/scene_theme.ogg"
    ctx.assets._current_music = original
    scene = MusicRoomScene(ctx)

    def fail_after_taking_audio():
        ctx.assets.play_music("assets/bgm/temporary.ogg", volume=0.95)
        raise RuntimeError("layout failed")

    scene._enumerate_tracks = fail_after_taking_audio  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="layout failed"):
        scene.enter()

    assert calls[-1] == {"path": original, "volume": 0.6}
    assert scene._now_playing() == original
    assert scene._audio_snapshot is None


def test_close_restores_before_a_failing_navigation_callback(tmp_path):
    root = _bgm_pack(tmp_path, ["main.ogg"])
    ctx, calls = _make_ctx(root)
    original = "assets/bgm/scene_theme.ogg"
    ctx.assets._current_music = original
    ctx.state.music_room.unlock("assets/bgm/main.ogg")

    def fail_navigation():
        raise RuntimeError("pop failed")

    scene = _open(ctx, on_close=fail_navigation)
    scene._play("assets/bgm/main.ogg")
    calls.clear()

    with pytest.raises(RuntimeError, match="pop failed"):
        scene._close()

    assert calls == [{"path": original, "volume": 0.6}]
    assert scene._now_playing() == original
    assert scene._audio_snapshot is None


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


# ---------------------------------------------------------------------------
# Product catalog, semantic navigation, and bounded rendering
# ---------------------------------------------------------------------------

def _catalog(*entries):
    from world_gal_game.core.music_room_spec import MusicRoomManifest

    return MusicRoomManifest.from_items(list(entries))


def test_authored_catalog_controls_order_hidden_and_formal_labels(tmp_path):
    root = _bgm_pack(tmp_path, ["directory_only.ogg"])
    ctx, _ = _make_ctx(root)
    ctx.state.meta["__music_room_manifest__"] = _catalog(
        {
            "asset": "assets/bgm/second.ogg",
            "title": "Formal Second",
            "artist": "Composer",
            "usage": "Route B",
            "order": 20,
        },
        {
            "asset": "assets/bgm/first.ogg",
            "title": "Formal First",
            "artist": "Composer",
            "usage": "Route A",
            "order": 10,
        },
        {
            "asset": "assets/bgm/spoiler.ogg",
            "title": "Spoiler Title",
            "hidden": True,
            "order": 30,
        },
    )
    ctx.state.music_room.unlock("assets/bgm/second.ogg")
    ctx.state.music_room.unlock("assets/bgm/legacy-heard.ogg")

    scene = _open(ctx)

    assert scene._tracks == [
        "assets/bgm/first.ogg",
        "assets/bgm/second.ogg",
        "assets/bgm/legacy-heard.ogg",
    ]
    assert scene._label_for("assets/bgm/second.ogg") == "Formal Second"
    assert "assets/bgm/directory_only.ogg" not in scene._tracks
    assert "assets/bgm/spoiler.ogg" not in scene._tracks

    ctx.state.music_room.unlock("assets/bgm/spoiler.ogg")
    reopened = _open(ctx)
    assert reopened._tracks[-2:] == [
        "assets/bgm/spoiler.ogg",
        "assets/bgm/legacy-heard.ogg",
    ]


def test_locked_pointer_and_confirm_explain_without_leaking_metadata(tmp_path):
    from world_gal_game.ui.input_bindings import InputAction

    root = _bgm_pack(tmp_path, [])
    ctx, calls = _make_ctx(root)
    secret = "assets/bgm/secret.ogg"
    ctx.state.meta["__music_room_manifest__"] = _catalog({
        "asset": secret,
        "title": "Secret Formal Title",
        "artist": "Secret Artist",
        "usage": "Secret Ending",
    })
    scene = _open(ctx)
    scene.draw(_surface())
    locked_rect = next(
        rect for rect, path, _unlocked in scene._row_screen_rects
        if path == secret
    )

    scene.update(0.0, _Input(
        mouse_clicked=True,
        mouse_pos=locked_rect.center,
    ))
    assert calls == []
    assert "Secret" not in scene._status_message
    assert "尚未解鎖" in scene._status_message

    scene.update(0.0, _Input(actions={InputAction.CONFIRM_ADVANCE}))
    assert calls == []
    assert "Secret" not in scene._status_message


def test_semantic_navigation_focuses_first_unlocked_and_plays(tmp_path):
    from world_gal_game.ui.input_bindings import InputAction

    root = _bgm_pack(tmp_path, [])
    ctx, calls = _make_ctx(root)
    locked = "assets/bgm/locked.ogg"
    heard = "assets/bgm/heard.ogg"
    ctx.state.meta["__music_room_manifest__"] = _catalog(
        {"asset": locked, "title": "Locked", "order": 10},
        {"asset": heard, "title": "Heard", "order": 20},
    )
    ctx.state.music_room.unlock(heard)
    scene = _open(ctx)

    assert scene._focus == 1
    scene.update(0.0, _Input(actions={InputAction.CONFIRM_ADVANCE}))
    assert calls[-1]["path"] == heard
    assert scene._started_playback is True

    scene.update(0.0, _Input(actions={InputAction.NAV_UP}))
    assert scene._focus == 0
    scene.update(0.0, _Input(actions={InputAction.CONFIRM_ADVANCE}))
    assert calls[-1]["path"] == heard
    assert "尚未解鎖" in scene._status_message


def test_hovering_disabled_stop_does_not_steal_row_focus_or_close(tmp_path):
    from world_gal_game.ui.input_bindings import InputAction

    root = _bgm_pack(tmp_path, ["heard.ogg"])
    ctx, calls = _make_ctx(root)
    path = "assets/bgm/heard.ogg"
    ctx.state.music_room.unlock(path)
    closed = {"count": 0}
    scene = _open(
        ctx,
        on_close=lambda: closed.__setitem__("count", closed["count"] + 1),
    )
    assert scene._focus_zone == "rows"
    assert scene.stop_btn.enabled is False

    scene.update(0.0, _Input(
        mouse_pos=scene.stop_btn.rect.center,
        events=[pygame.event.Event(
            pygame.MOUSEMOTION,
            pos=scene.stop_btn.rect.center,
            rel=(0, 0),
            buttons=(0, 0, 0),
        )],
    ))

    assert scene._focus_zone == "rows"
    assert scene.close_btn.focused is False

    scene.update(0.0, _Input(actions={InputAction.CONFIRM_ADVANCE}))

    assert calls[-1]["path"] == path
    assert closed["count"] == 0


def test_footer_hint_uses_runtime_localization_and_binding_labels(tmp_path):
    from world_gal_game.core.localization import Localization
    from world_gal_game.ui.input_bindings import BindingProfile

    root = _bgm_pack(tmp_path, [])
    ctx, _ = _make_ctx(root)
    ctx.localization = Localization.from_meta({
        "locale": {
            "ui": {
                "music_room_footer": (
                    "Choose {up}/{down}; play {confirm}; "
                    "pages {page_up}/{page_down}"
                ),
            },
        },
    })
    scene = _open(ctx)

    hint = scene._footer_hint(BindingProfile({}))

    assert hint.startswith("Choose ")
    assert "; play " in hint
    assert "; pages " in hint
    assert "選曲" not in hint


def test_page_and_wheel_navigation_keep_focus_visible(tmp_path):
    from world_gal_game.ui.input_bindings import InputAction

    root = _bgm_pack(tmp_path, [f"track_{i:02d}.ogg" for i in range(40)])
    ctx, _ = _make_ctx(root)
    for path in [f"assets/bgm/track_{i:02d}.ogg" for i in range(40)]:
        ctx.state.music_room.unlock(path)
    scene = _open(ctx)

    scene.update(0.0, _Input(actions={InputAction.PAGE_DOWN}))
    focused = scene._row_content_rects[scene._focus]
    assert focused.bottom <= scene._scroll.scroll_y + scene._scroll.rect.height

    old_focus = scene._focus
    scene.update(0.0, _Input(
        mouse_pos=scene._scroll.rect.center,
        mouse_wheel=-6,
    ))
    assert scene._focus > old_focus
    visible = scene._row_content_rects[scene._focus]
    assert visible.bottom > scene._scroll.scroll_y
    assert visible.top < scene._scroll.scroll_y + scene._scroll.rect.height


def test_header_click_is_consumed_before_scrolled_row_activation(tmp_path):
    root = _bgm_pack(tmp_path, [f"track_{i:02d}.ogg" for i in range(20)])
    ctx, calls = _make_ctx(root)
    for path in [f"assets/bgm/track_{i:02d}.ogg" for i in range(20)]:
        ctx.state.music_room.unlock(path)
    closed = {"count": 0}
    scene = _open(
        ctx,
        on_close=lambda: closed.__setitem__("count", closed["count"] + 1),
    )
    scene._scroll.scroll_y = scene._content_height
    scene._scroll._clamp_scroll()
    scene.draw(_surface())

    scene.update(0.0, _Input(
        mouse_clicked=True,
        mouse_pos=scene.close_btn.rect.center,
    ))

    assert closed["count"] == 1
    assert calls == []
    assert all(
        scene._scroll.rect.colliderect(rect)
        for rect, _path, _unlocked in scene._row_screen_rects
    )


def test_large_catalog_draws_only_visible_rows_without_backing_surface(
    tmp_path,
    monkeypatch,
):
    root = _bgm_pack(tmp_path, [])
    ctx, _ = _make_ctx(root)
    ctx.state.meta["__music_room_manifest__"] = _catalog(*[
        {
            "asset": f"assets/bgm/track_{index:04d}.ogg",
            "title": f"Track {index}",
            "order": index,
        }
        for index in range(1000)
    ])
    scene = _open(ctx)
    draws: list[int] = []
    original = scene._draw_row

    def counted(*args, **kwargs):
        draws.append(kwargs["index"])
        return original(*args, **kwargs)

    monkeypatch.setattr(scene, "_draw_row", counted)
    scene.draw(_surface())

    assert len(scene._tracks) == 1000
    assert 1 <= len(draws) < 20
    assert scene._scroll.surface is None


def test_resize_reflows_targets_and_preserves_focused_asset(tmp_path):
    from world_gal_game.ui.input_bindings import InputAction

    root = _bgm_pack(tmp_path, [f"track_{i:02d}.ogg" for i in range(12)])
    ctx, _ = _make_ctx(root)
    for path in [f"assets/bgm/track_{i:02d}.ogg" for i in range(12)]:
        ctx.state.music_room.unlock(path)
    scene = _open(ctx)
    for _ in range(5):
        scene.update(0.0, _Input(actions={InputAction.NAV_DOWN}))
    focused = scene._tracks[scene._focus]

    scene.draw(pygame.Surface((960, 540), pygame.SRCALPHA))

    assert scene._tracks[scene._focus] == focused
    assert scene.close_btn.rect.width >= 44
    assert scene.close_btn.rect.height >= 44
    assert scene.stop_btn.rect.width >= 44
    assert scene.stop_btn.rect.height >= 44
    assert scene._panel_rect.right <= 960
    assert scene._panel_rect.bottom <= 540


def test_failed_preview_does_not_claim_audio_or_cut_other_buses(tmp_path):
    root = _bgm_pack(tmp_path, ["main.ogg"])
    ctx, _calls = _make_ctx(root)
    path = "assets/bgm/main.ogg"
    ctx.state.music_room.unlock(path)
    scene = _open(ctx)
    bus_calls: list[str] = []
    ctx.assets.play_music = lambda *args, **kwargs: None  # type: ignore[assignment]
    ctx.assets.stop_ambient = lambda **kwargs: bus_calls.append("ambient")  # type: ignore[assignment]
    ctx.assets.stop_voice = lambda: bus_calls.append("voice")  # type: ignore[assignment]

    assert scene._play(path) is False
    assert scene._started_playback is False
    assert bus_calls == []
    assert "無法載入" in scene._status_message
