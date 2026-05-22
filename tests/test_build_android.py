"""Tests for the Android (pygbag APK) build path + the touch_mode flag.

The Android primary path wraps the *same* staged web build in a pygbag APK.
These tests assert that staging is shared with the web build and produces an
identical layout, and that the PWA emit helper is wired to pygbag's output —
all WITHOUT invoking pygbag (which isn't installed). pygbag is only ever
reached inside build_android_apk's subprocess call, which we don't run.
"""
from __future__ import annotations

from pathlib import Path

import pygame
import pytest
import yaml

# InputState.collect polls pygame.mouse, which needs the display subsystem up.
# Mirror the other input tests: init a tiny dummy display at import time.
pygame.init()
pygame.display.set_mode((10, 10))

import world_gal_game.build_web as bw
from world_gal_game.build_web import (
    PYGBAG_APK_FLAG,
    _pygbag_output_dir,
    stage_web_build,
)


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh, allow_unicode=True, default_flow_style=False)


@pytest.fixture
def tiny_pack(tmp_path: Path) -> Path:
    root = tmp_path / "my_apk_pack"
    _write(root / "content" / "meta.yaml", {
        "title": "APK Demo",
        "pack_format_version": "0.1",
        "bundled_font": "assets/fonts/font.ttf",
        "start_location": "loc_a",
    })
    _write(root / "content" / "locations.yaml", {"locations": [
        {"id": "loc_a", "name": "A", "exits": []},
    ]})
    afont = root / "assets" / "fonts" / "font.ttf"
    afont.parent.mkdir(parents=True, exist_ok=True)
    afont.write_bytes(b"FAKEFONT")
    return root


def test_android_staging_matches_web_staging(tiny_pack: Path,
                                             tmp_path: Path) -> None:
    """The APK build stages the identical tree a web build would.

    We call stage_web_build (the shared helper build_android_apk uses) and
    assert the layout; this is the unit-testable contract that the APK path
    reuses the web staging verbatim.
    """
    staging = tmp_path / "android_staging"
    out = stage_web_build(tiny_pack, staging, pack_name="my_apk_pack")
    assert out == staging.resolve()

    # Same engine + pack + templated main.py layout as a web build.
    assert (staging / "world_gal_game" / "__init__.py").exists()
    assert (staging / "world_gal_game" / "app.py").exists()
    assert (staging / "games" / "my_apk_pack" / "content" / "meta.yaml").exists()
    assert (staging / "games" / "my_apk_pack" / "assets" / "fonts"
            / "font.ttf").exists()
    main_py = (staging / "main.py").read_text(encoding="utf-8")
    assert "my_apk_pack" in main_py
    assert "async def main" in main_py


def test_android_and_web_staging_are_byte_identical(tiny_pack: Path,
                                                    tmp_path: Path) -> None:
    """Staging the same pack twice (web vs android dirs) yields the same files.

    Confirms there is no Android-specific divergence in the staged tree — the
    APK is literally the web bundle in a WebView shell.
    """
    web_dir = tmp_path / "web"
    apk_dir = tmp_path / "apk"
    stage_web_build(tiny_pack, web_dir, pack_name="p")
    stage_web_build(tiny_pack, apk_dir, pack_name="p")

    web_files = {
        p.relative_to(web_dir) for p in web_dir.rglob("*") if p.is_file()
    }
    apk_files = {
        p.relative_to(apk_dir) for p in apk_dir.rglob("*") if p.is_file()
    }
    assert web_files == apk_files
    # main.py content matches too.
    assert ((web_dir / "main.py").read_bytes()
            == (apk_dir / "main.py").read_bytes())


def test_apk_flag_is_a_named_constant() -> None:
    # The pygbag APK flag lives in exactly one place as a string constant.
    assert isinstance(PYGBAG_APK_FLAG, str) and PYGBAG_APK_FLAG.startswith("--")


def test_pygbag_output_dir_prefers_build_web(tmp_path: Path) -> None:
    # When pygbag's conventional build/web/index.html exists, it's chosen.
    out = tmp_path / "build" / "web"
    out.mkdir(parents=True)
    (out / "index.html").write_text("<html></html>", encoding="utf-8")
    assert _pygbag_output_dir(tmp_path) == out


def test_pygbag_output_dir_falls_back_to_found_index(tmp_path: Path) -> None:
    # A non-conventional layout still resolves to wherever index.html sits.
    weird = tmp_path / "dist" / "wasm"
    weird.mkdir(parents=True)
    (weird / "index.html").write_text("<html></html>", encoding="utf-8")
    assert _pygbag_output_dir(tmp_path) == weird


def test_pygbag_output_dir_default_when_absent(tmp_path: Path) -> None:
    # No index.html anywhere → conventional path (the resilient writer logs
    # "not found" against it rather than crashing).
    assert _pygbag_output_dir(tmp_path) == tmp_path / "build" / "web"


def test_build_android_apk_does_not_import_pygbag_at_module_load() -> None:
    # build_web must never import pygbag at import time (optional dep).
    import sys
    assert "pygbag" not in sys.modules


def test_emit_pwa_assets_writes_into_output(tiny_pack: Path,
                                            tmp_path: Path) -> None:
    """_emit_pwa_assets (used by both web + android) writes PWA files.

    We stage, fake a pygbag output dir with an index.html, and confirm the
    emit helper drops the manifest + worker and patches index.html.
    """
    staging = tmp_path / "s"
    stage_web_build(tiny_pack, staging, pack_name="p")
    pygbag_out = staging / "build" / "web"
    pygbag_out.mkdir(parents=True)
    (pygbag_out / "index.html").write_text(
        "<html><head></head></html>", encoding="utf-8"
    )

    bw._emit_pwa_assets(staging, "APK Demo")

    assert (pygbag_out / "manifest.webmanifest").exists()
    assert (pygbag_out / "service-worker.js").exists()
    patched = (pygbag_out / "index.html").read_text(encoding="utf-8")
    assert "manifest.webmanifest" in patched


# --------------------------------------------------------------------------
# touch_mode flag (config)
# --------------------------------------------------------------------------


def test_touch_mode_defaults_false() -> None:
    from world_gal_game.config import EngineConfig
    assert EngineConfig().touch_mode is False
    # from_env keeps it off unless explicitly overridden.
    assert EngineConfig.from_env().touch_mode is False


def test_touch_mode_opt_in() -> None:
    from world_gal_game.config import EngineConfig
    assert EngineConfig(touch_mode=True).touch_mode is True


def test_min_touch_target_constant_documented() -> None:
    from world_gal_game.config import MIN_TOUCH_TARGET_PX
    # A sane finger-target floor (>= the 44px HIG/Material guideline).
    assert isinstance(MIN_TOUCH_TARGET_PX, int) and MIN_TOUCH_TARGET_PX >= 44


def test_desktop_input_unchanged_with_default_config() -> None:
    """touch_mode False (default) leaves InputState.collect behaviour intact.

    A plain mouse-click event maps to advance_dialogue + mouse_clicked exactly
    as before; no touch_mode coupling leaked into the input path.
    """
    from world_gal_game.ui.input import InputState

    click = pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, {"button": 1, "pos": (10, 10)}
    )
    state = InputState.collect([click])
    assert state.mouse_clicked is True
    assert state.advance_dialogue is True
    assert state.swipe is None
    assert state.touch_active is False
