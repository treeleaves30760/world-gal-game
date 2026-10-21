"""Native asset-loader failures degrade without stealing runtime ownership."""

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


def _existing_asset(tmp_path: Path, relative: str) -> Path:
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"loader fixture")
    return target


@pytest.mark.parametrize("error", [OSError("decode"), MemoryError()])
def test_image_decode_failure_returns_and_caches_placeholder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    _existing_asset(tmp_path, "assets/art/broken.png")
    calls = 0

    def fail_load(_path: str) -> pygame.Surface:
        nonlocal calls
        calls += 1
        raise error

    monkeypatch.setattr(pygame.image, "load", fail_load)
    assets = AssetManager(pack_root=tmp_path)

    first = assets.image(
        "assets/art/broken.png",
        fallback_size=(17, 13),
        fallback_color=(120, 30, 40),
    )
    second = assets.image(
        "assets/art/broken.png",
        fallback_size=(17, 13),
        fallback_color=(120, 30, 40, 255),
    )
    alternate = assets.image(
        "assets/art/broken.png",
        fallback_size=(19, 11),
        fallback_color=(20, 70, 150),
    )

    assert first.get_size() == (17, 13)
    assert second is first
    assert alternate.get_size() == (19, 11)
    assert alternate is not first
    assert pygame.image.tobytes(alternate, "RGBA") != pygame.image.tobytes(
        first,
        "RGBA",
    )
    assert calls == 2
    assert len(assets._images) == 2
    assert all(
        key.startswith("__asset_fallback__::'assets/art/broken.png'")
        for key in assets._images
    )


@pytest.mark.parametrize("error", [OSError("decode"), MemoryError()])
def test_corrupt_scaled_placeholder_identity_includes_fallback_color(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    _existing_asset(tmp_path, "assets/art/broken-scaled.png")
    calls = 0

    def fail_load(_path: str) -> pygame.Surface:
        nonlocal calls
        calls += 1
        raise error

    monkeypatch.setattr(pygame.image, "load", fail_load)
    assets = AssetManager(pack_root=tmp_path)

    red = assets.scaled(
        "assets/art/broken-scaled.png",
        (29, 17),
        fit="stretch",
        fallback_color=(130, 20, 30),
    )
    blue = assets.scaled(
        "assets/art/broken-scaled.png",
        (29, 17),
        fit="stretch",
        fallback_color=(20, 30, 140),
    )
    red_again = assets.scaled(
        "assets/art/broken-scaled.png",
        (29, 17),
        fit="stretch",
        fallback_color=(130, 20, 30, 255),
    )

    assert red_again is red
    assert blue is not red
    assert pygame.image.tobytes(blue, "RGBA") != pygame.image.tobytes(
        red,
        "RGBA",
    )
    assert calls == 2


@pytest.mark.parametrize("error", [OSError("scale"), MemoryError()])
def test_image_scale_failure_returns_cached_destination_placeholder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    _existing_asset(tmp_path, "assets/art/huge.png")
    source = pygame.Surface((4, 4), pygame.SRCALPHA)
    monkeypatch.setattr(pygame.image, "load", lambda _path: source)
    calls = 0

    def fail_scale(
        _surface: pygame.Surface,
        _size: tuple[int, int],
    ) -> pygame.Surface:
        nonlocal calls
        calls += 1
        raise error

    monkeypatch.setattr(pygame.transform, "smoothscale", fail_scale)
    assets = AssetManager(pack_root=tmp_path)

    first = assets.scaled(
        "assets/art/huge.png",
        (23, 19),
        fit="stretch",
        fallback_color=(120, 30, 40),
    )
    second = assets.scaled(
        "assets/art/huge.png",
        (23, 19),
        fit="stretch",
        fallback_color=(120, 30, 40, 255),
    )
    alternate = assets.scaled(
        "assets/art/huge.png",
        (23, 19),
        fit="stretch",
        fallback_color=(20, 70, 150),
    )

    assert first.get_size() == (23, 19)
    assert second is first
    assert alternate is not first
    assert pygame.image.tobytes(alternate, "RGBA") != pygame.image.tobytes(
        first,
        "RGBA",
    )
    assert calls == 2
    assert list(assets._images)[-1].startswith(
        "assets/art/huge.png::23x19::stretch::fallback="
    )


@pytest.mark.parametrize("error", [OSError("decode"), MemoryError()])
def test_short_sound_decode_failure_is_silent_and_uncached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    _existing_asset(tmp_path, "assets/voice/broken.ogg")

    def fail_sound(*_args: object, **_kwargs: object) -> pygame.mixer.Sound:
        raise error

    monkeypatch.setattr(pygame.mixer, "Sound", fail_sound)
    assets = AssetManager(pack_root=tmp_path)

    assert assets.sound("assets/voice/broken.ogg") is None
    assert assets.play_voice("assets/voice/broken.ogg") is None
    assert assets.sound_cache_entries == 0
    assert assets.current_voice_path is None
    assert assets.current_voice_handle is None


@pytest.mark.parametrize("error", [OSError("decode"), MemoryError()])
def test_voice_restore_decode_failure_uses_declared_silent_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    _existing_asset(tmp_path, "assets/voice/broken.ogg")

    def fail_sound(*_args: object, **_kwargs: object) -> pygame.mixer.Sound:
        raise error

    monkeypatch.setattr(pygame.mixer, "Sound", fail_sound)
    assets = AssetManager(pack_root=tmp_path)

    outcome = assets.resume_voice(
        "assets/voice/broken.ogg",
        position_seconds=0.25,
        duration_seconds=1.0,
        pcm_sha256="0" * 64,
        mixer_format=[44100, -16, 2],
    )

    assert outcome == "silent"
    assert assets.last_voice_resume_outcome == "silent:asset_unavailable"
    assert assets.current_voice_path is None
    assert assets.current_voice_handle is None
    assert assets.sound_cache_entries == 0


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("loader", ["image", "sound"])
def test_asset_loaders_do_not_swallow_control_flow_exceptions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[BaseException],
    loader: str,
) -> None:
    relative = f"assets/{loader}/control.asset"
    _existing_asset(tmp_path, relative)
    assets = AssetManager(pack_root=tmp_path)

    def interrupt(*_args: object, **_kwargs: object) -> object:
        raise error_type()

    if loader == "image":
        monkeypatch.setattr(pygame.image, "load", interrupt)

        def operation() -> object:
            return assets.image(relative)
    else:
        monkeypatch.setattr(pygame.mixer, "Sound", interrupt)

        def operation() -> object:
            return assets.sound(relative)

    with pytest.raises(error_type):
        operation()
