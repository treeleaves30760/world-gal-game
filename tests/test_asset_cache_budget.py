"""Byte-bounded image/sound caching for long visual-novel playthroughs."""

from __future__ import annotations

import os
from pathlib import Path

import pygame
import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from world_gal_game.ui.assets import AssetManager


@pytest.fixture(autouse=True, scope="module")
def _pygame_display():
    pygame.init()
    pygame.display.set_mode((1, 1))
    yield
    pygame.quit()


def _write_png(
    path: Path,
    color: tuple[int, int, int, int],
    *,
    size: tuple[int, int] = (10, 10),
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    surface = pygame.Surface(size, pygame.SRCALPHA)
    surface.fill(color)
    pygame.image.save(surface, path)
    return surface.get_pitch() * surface.get_height()


def test_image_cache_uses_surface_bytes_and_lru_order(tmp_path: Path) -> None:
    entry_bytes = _write_png(tmp_path / "assets/a.png", (255, 0, 0, 255))
    _write_png(tmp_path / "assets/b.png", (0, 255, 0, 255))
    _write_png(tmp_path / "assets/c.png", (0, 0, 255, 255))
    assets = AssetManager(
        pack_root=tmp_path,
        max_image_cache_bytes=entry_bytes * 2,
    )

    first = assets.image("assets/a.png")
    assets.image("assets/b.png")
    assert assets.image_cache_bytes == entry_bytes * 2

    assert assets.image("assets/a.png") is first  # refresh A as most-recent
    assets.image("assets/c.png")

    assert list(assets._images) == ["assets/a.png", "assets/c.png"]
    assert assets.image_cache_entries == 2
    assert assets.image_cache_bytes == entry_bytes * 2


def test_oversized_image_is_returned_without_poisoning_cache(
    tmp_path: Path,
) -> None:
    entry_bytes = _write_png(
        tmp_path / "assets/large.png",
        (20, 30, 40, 255),
        size=(20, 10),
    )
    assets = AssetManager(
        pack_root=tmp_path,
        max_image_cache_bytes=entry_bytes - 1,
    )

    loaded = assets.image("assets/large.png")

    assert loaded.get_size() == (20, 10)
    assert assets.image_cache_entries == 0
    assert assets.image_cache_bytes == 0


def test_scaled_surface_can_replace_base_within_same_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_bytes = _write_png(
        tmp_path / "assets/base.png",
        (80, 90, 100, 255),
        size=(20, 10),
    )
    calls = 0
    real_load = pygame.image.load

    def counted_load(path):
        nonlocal calls
        calls += 1
        return real_load(path)

    monkeypatch.setattr(pygame.image, "load", counted_load)
    assets = AssetManager(
        pack_root=tmp_path,
        max_image_cache_bytes=base_bytes,
    )

    first = assets.scaled("assets/base.png", (10, 10), fit="stretch")
    second = assets.scaled("assets/base.png", (10, 10), fit="stretch")

    assert second is first
    assert calls == 1
    assert list(assets._images) == ["assets/base.png::10x10::stretch"]
    assert assets.image_cache_bytes <= base_bytes


def test_cover_focus_keeps_authored_vertical_subject_and_caches_variants(
    tmp_path: Path,
) -> None:
    path = tmp_path / "assets/portrait.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    portrait = pygame.Surface((20, 60), pygame.SRCALPHA)
    portrait.fill((220, 30, 30, 255), pygame.Rect(0, 0, 20, 20))
    portrait.fill((30, 220, 30, 255), pygame.Rect(0, 20, 20, 20))
    portrait.fill((30, 30, 220, 255), pygame.Rect(0, 40, 20, 20))
    pygame.image.save(portrait, path)
    assets = AssetManager(pack_root=tmp_path)

    top = assets.scaled(
        "assets/portrait.png",
        (20, 10),
        fit="cover",
        focus=(0.5, 0.0),
    )
    middle = assets.scaled(
        "assets/portrait.png",
        (20, 10),
        fit="cover",
    )
    bottom = assets.scaled(
        "assets/portrait.png",
        (20, 10),
        fit="cover",
        focus=(0.5, 1.0),
    )

    assert top.get_at((10, 5)).r > 200
    assert middle.get_at((10, 5)).g > 200
    assert bottom.get_at((10, 5)).b > 200
    assert assets.scaled(
        "assets/portrait.png",
        (20, 10),
        fit="cover",
        focus=(0.5, 0.0),
    ) is top
    zoomed = assets.scaled(
        "assets/portrait.png",
        (20, 10),
        fit="cover",
        focus=(0.5, 0.0),
        zoom=1.2,
    )
    assert zoomed is not top
    with pytest.raises(ValueError):
        assets.scaled(
            "assets/portrait.png",
            (20, 10),
            focus=(0.5, 1.5),
        )
    with pytest.raises(ValueError):
        assets.scaled(
            "assets/portrait.png",
            (20, 10),
            zoom=0.5,
        )


def test_placeholder_surfaces_share_the_bounded_image_lru() -> None:
    placeholder_bytes = 10 * 10 * 4
    assets = AssetManager(max_image_cache_bytes=placeholder_bytes)

    first = assets.image(None, fallback_size=(10, 10))
    second = assets.image(None, fallback_size=(10, 10))

    assert second is first
    assert assets.image_cache_entries == 1
    assert assets.image_cache_bytes == placeholder_bytes

    assets.clear_caches()

    assert assets.image_cache_entries == 0
    assert assets.image_cache_bytes == 0


def test_missing_image_fallback_variants_do_not_poison_path_cache() -> None:
    assets = AssetManager()
    path = "assets/does-not-exist.png"

    red = assets.image(
        path,
        fallback_size=(21, 17),
        fallback_color=(140, 20, 30),
    )
    blue = assets.image(
        path,
        fallback_size=(21, 17),
        fallback_color=(20, 30, 150),
    )
    larger = assets.image(
        path,
        fallback_size=(23, 19),
        fallback_color=(140, 20, 30),
    )
    red_again = assets.image(
        path,
        fallback_size=(21, 17),
        fallback_color=(140, 20, 30, 255),
    )

    assert red_again is red
    assert blue is not red
    assert larger is not red
    assert larger.get_size() == (23, 19)
    assert pygame.image.tobytes(blue, "RGBA") != pygame.image.tobytes(
        red,
        "RGBA",
    )


def test_missing_scaled_fallback_variants_include_color_without_clear() -> None:
    assets = AssetManager()
    path = "assets/missing-scaled.png"

    red = assets.scaled(
        path,
        (31, 19),
        fit="stretch",
        fallback_color=(150, 25, 35),
    )
    blue = assets.scaled(
        path,
        (31, 19),
        fit="stretch",
        fallback_color=(25, 35, 160),
    )
    red_again = assets.scaled(
        path,
        (31, 19),
        fit="stretch",
        fallback_color=(150, 25, 35, 255),
    )

    assert red_again is red
    assert blue is not red
    assert pygame.image.tobytes(blue, "RGBA") != pygame.image.tobytes(
        red,
        "RGBA",
    )


def test_real_scaled_asset_reuses_cache_across_irrelevant_fallback_colors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_png(
        tmp_path / "assets/real.png",
        (70, 110, 150, 255),
        size=(12, 8),
    )
    calls = 0
    real_load = pygame.image.load

    def counted_load(path):
        nonlocal calls
        calls += 1
        return real_load(path)

    monkeypatch.setattr(pygame.image, "load", counted_load)
    assets = AssetManager(pack_root=tmp_path)

    first = assets.scaled(
        "assets/real.png",
        (24, 16),
        fit="stretch",
        fallback_color=(150, 25, 35),
    )
    second = assets.scaled(
        "assets/real.png",
        (24, 16),
        fit="stretch",
        fallback_color=(25, 35, 160),
    )

    assert second is first
    assert calls == 1
    assert list(assets._images)[-1] == (
        "assets/real.png::24x16::stretch"
    )


def test_oversized_placeholder_is_not_retained_outside_the_budget() -> None:
    assets = AssetManager(max_image_cache_bytes=1)

    rendered = assets.scaled(None, (1000, 1000))

    assert rendered.get_size() == (1000, 1000)
    assert assets.image_cache_entries == 0
    assert assets.image_cache_bytes == 0


def test_pack_root_switch_clears_path_keyed_image_cache(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    _write_png(first_root / "assets/shared.png", (240, 10, 20, 255))
    _write_png(second_root / "assets/shared.png", (20, 10, 240, 255))
    assets = AssetManager(pack_root=first_root)

    assert assets.image("assets/shared.png").get_at((0, 0)).r > 200
    assert assets.image_cache_entries == 1

    assets.set_pack_root(second_root)

    assert assets.image_cache_entries == 0
    assert assets.image_cache_bytes == 0
    assert assets.image("assets/shared.png").get_at((0, 0)).b > 200


def test_app_threads_runtime_cache_budgets_from_config(tmp_path: Path) -> None:
    from world_gal_game.app import GalGameApp
    from world_gal_game.config import EngineConfig

    pack = Path("games/demo_pack").resolve()
    app = GalGameApp(
        config=EngineConfig(
            default_pack=str(pack),
            screen_size=(1280, 720),
            writable_root_override=tmp_path,
            seen_intro=True,
            max_image_cache_mb=3,
            max_sound_cache_mb=5,
        ),
        pack=str(pack),
        headless=True,
    )

    stats = app.assets.cache_stats()

    assert stats["image_budget_bytes"] == 3 * 1024 * 1024
    assert stats["sound_budget_bytes"] == 5 * 1024 * 1024


class _FakeSound:
    def __init__(self, path: str):
        self.path = path
        self.volume = 1.0

    def get_length(self) -> float:
        return 1.0

    def set_volume(self, value: float) -> None:
        self.volume = value

    def play(self, *_, **__) -> None:
        return None


class _FakeChannel:
    def __init__(self):
        self.busy = False
        self.played: list[_FakeSound] = []

    def get_busy(self) -> bool:
        return self.busy

    def fadeout(self, _milliseconds: int) -> None:
        self.busy = False

    def set_volume(self, _volume: float) -> None:
        return None

    def play(self, sound: _FakeSound, **_) -> None:
        self.busy = True
        self.played.append(sound)


def _fake_audio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    relative_paths = [
        "assets/audio/a.ogg",
        "assets/audio/b.ogg",
        "assets/audio/c.ogg",
    ]
    for path in relative_paths:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fixture")
    monkeypatch.setattr(pygame.mixer, "Sound", _FakeSound)
    # One second = 1000 frames * stereo * 16-bit = 4000 decoded bytes.
    monkeypatch.setattr(pygame.mixer, "get_init", lambda: (1000, -16, 2))
    return relative_paths


def test_short_sound_cache_is_byte_bounded_lru(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fake_audio(tmp_path, monkeypatch)
    assets = AssetManager(
        pack_root=tmp_path,
        max_sound_cache_bytes=8000,
    )

    first = assets.sound(paths[0])
    assets.sound(paths[1])
    assert assets.sound(paths[0]) is first
    assets.sound(paths[2])

    assert list(assets._sounds) == [paths[0], paths[2]]
    assert assets.sound_cache_entries == 2
    assert assets.sound_cache_bytes == 8000


def test_oversized_sound_is_playable_but_not_cached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fake_audio(tmp_path, monkeypatch)
    assets = AssetManager(
        pack_root=tmp_path,
        max_sound_cache_bytes=3999,
    )

    assert assets.sound(paths[0]) is not None
    assert assets.sound_cache_entries == 0
    assert assets.sound_cache_bytes == 0


def test_bgm_crossfade_keeps_two_slots_out_of_short_sound_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fake_audio(tmp_path, monkeypatch)
    assets = AssetManager(pack_root=tmp_path)
    assets._bgm_channels = [_FakeChannel(), _FakeChannel()]

    for path in paths:
        assets.play_music(path, fade_ms=0)

    assert assets.sound_cache_entries == 0
    assert assets.sound_cache_bytes == 0
    assert assets._bgm_channel_paths == [paths[1], paths[2]]
    assert all(ref is not None for ref in assets._bgm_sound_refs)
    assert assets.cache_stats()["bgm_slots"] == 2
    assert assets.cache_stats()["bgm_bytes"] == 8000
    assert assets.cache_stats()["retained_audio_bytes"] == 8000


def test_bgm_decode_failure_preserves_current_ownership_and_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fake_audio(tmp_path, monkeypatch)
    assets = AssetManager(pack_root=tmp_path)
    assets._bgm_channels = [_FakeChannel(), _FakeChannel()]
    assets.play_music(paths[0], volume=0.4, fade_ms=0)
    snapshot = assets.capture_audio_playback()
    active_channel = assets._bgm_channels[assets._bgm_active]

    def fail_decode(_path: str) -> _FakeSound:
        raise OSError("decode failed")

    monkeypatch.setattr(pygame.mixer, "Sound", fail_decode)

    assets.play_music(paths[1], volume=0.9, fade_ms=0)

    assert assets._current_music == paths[0]
    assert assets._music_revision == snapshot.music_revision
    assert assets.get_music_volume() == pytest.approx(0.4)
    assert active_channel.get_busy() is True

    assets.restore_audio_playback(snapshot)

    assert assets._current_music == paths[0]
    assert assets._music_revision == snapshot.music_revision


def test_bgm_reuses_a_track_found_in_either_crossfade_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fake_audio(tmp_path, monkeypatch)
    loaded: list[_FakeSound] = []

    def tracked_sound(path: str) -> _FakeSound:
        sound = _FakeSound(path)
        loaded.append(sound)
        return sound

    monkeypatch.setattr(pygame.mixer, "Sound", tracked_sound)
    assets = AssetManager(pack_root=tmp_path)
    assets._bgm_channels = [_FakeChannel(), _FakeChannel()]
    assets.play_music(paths[0], fade_ms=0)
    assets.play_music(paths[1], fade_ms=0)
    retained_b = next(
        ref
        for ref, path in zip(
            assets._bgm_sound_refs,
            assets._bgm_channel_paths,
            strict=True,
        )
        if path == paths[1]
    )

    assets.stop_music(fade_ms=200)
    assets.play_music(paths[1], fade_ms=0)

    assert len(loaded) == 2
    assert assets._bgm_channel_paths.count(paths[1]) == 1
    assert assets._bgm_sound_refs.count(retained_b) == 1
    assert assets.cache_stats()["bgm_slots"] == 1


def test_immediate_bgm_stop_releases_both_slots_before_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fake_audio(tmp_path, monkeypatch)
    assets = AssetManager(pack_root=tmp_path)
    assets._bgm_channels = [_FakeChannel(), _FakeChannel()]
    assets.play_music(paths[0], fade_ms=0)
    assets.play_music(paths[1], fade_ms=0)

    assets.stop_music(fade_ms=0)

    assert assets._bgm_channel_paths == [None, None]
    assert assets._bgm_sound_refs == [None, None]
    assert assets.cache_stats()["bgm_slots"] == 0
    assert assets.cache_stats()["bgm_bytes"] == 0

    assets.play_music(paths[1], fade_ms=0)

    assert assets._bgm_channel_paths.count(paths[1]) == 1
    assert assets.cache_stats()["bgm_slots"] == 1
