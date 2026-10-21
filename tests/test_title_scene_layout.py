"""Responsive layout contract for the title scene.

The title panel contains a name input plus a callback-dependent menu.  These
tests exercise the largest main/extras menus at the engine's 720p and 1080p
canvases, plus smaller 16:9 canvases, and assert every visual/interactive rect
stays inside the canvas without covering the wordmark or footer.
"""
from __future__ import annotations

import os

import pygame
import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


@pytest.fixture(scope="module", autouse=True)
def _pygame_init():
    pygame.display.init()
    pygame.font.init()
    yield
    pygame.quit()


class _Assets:
    def stop_ambient(self, *, fade_ms=0):
        return None

    def play_music(self, path, *, volume=0.6):
        return None


def _make_scene(size, *, full_menu=True):
    from world_gal_game.config import EngineConfig
    from world_gal_game.core.game_state import GameState
    from world_gal_game.core.localization import Localization
    from world_gal_game.scenes.base import SceneContext
    from world_gal_game.scenes.title import TitleScene
    from world_gal_game.ui.fonts import FontRegistry
    from world_gal_game.ui.theme import default_theme

    config = EngineConfig(
        title="World Gal-Game",
        subtitle="A responsive visual novel engine",
        screen_size=size,
    )
    fonts = FontRegistry(
        candidates=(),
        scale=max(1.0, size[1] / 720.0),
    )
    ctx = SceneContext(
        config=config,
        state=GameState(),
        npcs=None,       # type: ignore[arg-type]
        brain=None,      # type: ignore[arg-type]
        dialogue=None,   # type: ignore[arg-type]
        assets=_Assets(),  # type: ignore[arg-type]
        fonts=fonts,
        theme=default_theme(),
        localization=Localization(),
        screen_size=size,
    )
    def callback() -> None:
        return None
    kwargs = {
        "title": config.title,
        "subtitle": config.subtitle,
        "version": "1.2.3",
        "on_new_game": callback,
        "on_load": callback,
        "on_quit": callback,
    }
    if full_menu:
        kwargs.update({
            "on_continue": callback,
            "on_cg_gallery": callback,
            "on_music_room": callback,
            "on_endings": callback,
            "on_flowchart": callback,
            "on_scene_replay": callback,
            "on_credits": callback,
            "on_settings": callback,
        })
    scene = TitleScene(ctx)
    scene.enter(**kwargs)
    return scene


def _assert_layout_is_safe(scene, size):
    canvas = pygame.Rect((0, 0), size)

    assert canvas.contains(scene._logo_rect)
    assert canvas.contains(scene._byline_rect)
    assert scene._version_rect is not None
    assert canvas.contains(scene._version_rect)
    assert canvas.contains(scene._panel_rect)
    assert scene._logo_rect.bottom < scene._panel_rect.top
    assert scene._panel_rect.bottom < scene._byline_rect.top
    assert scene._panel_rect.bottom < scene._version_rect.top

    assert scene.name_input is not None
    assert scene.menu is not None
    assert scene._panel_rect.contains(scene._prompt_rect)
    assert scene._panel_rect.contains(scene.name_input.rect)
    assert scene._panel_rect.contains(scene.menu.rect)
    assert scene._panel_rect.top < scene._divider_y < scene._panel_rect.bottom

    # MenuList maps mouse y positions in row_h-sized bands. Those complete
    # interaction bands, not only the painted highlight, must remain visible.
    visible_rows = getattr(scene.menu, "visible_rows", len(scene.menu.items))
    assert scene.menu.rect.height == visible_rows * scene.menu.row_h
    for index in range(visible_rows):
        row = pygame.Rect(
            scene.menu.rect.x,
            scene.menu.rect.y + index * scene.menu.row_h,
            scene.menu.rect.width,
            scene.menu.row_h,
        )
        assert canvas.contains(row)
        assert scene._panel_rect.contains(row)

    # A real draw is part of the contract: measured rects must agree with the
    # font/render path and must not merely satisfy construction-time maths.
    surface = pygame.Surface(size, pygame.SRCALPHA)
    scene.draw(surface)


@pytest.mark.parametrize(
    "size",
    [
        (1280, 720),
        (1920, 1080),
        (960, 540),
        (800, 450),
        (640, 360),
    ],
)
def test_full_title_menu_fits_canvas_without_covering_branding(size):
    scene = _make_scene(size, full_menu=True)
    assert len(scene.menu.items) == 6
    _assert_layout_is_safe(scene, size)


@pytest.mark.parametrize(
    "size",
    [
        (1280, 720),
        (1920, 1080),
        (960, 540),
        (800, 450),
        (640, 360),
    ],
)
def test_extras_menu_reflows_and_preserves_name(size):
    scene = _make_scene(size, full_menu=True)
    scene.name_input.text = "春樹"
    scene._open_extras()

    assert len(scene.menu.items) == 7
    assert scene.menu.row_h >= 44
    assert "場景重溫" in [item.label for item in scene.menu.items]
    assert scene.name_input.text == "春樹"
    _assert_layout_is_safe(scene, size)


def test_panel_height_tracks_actual_menu_item_count():
    sparse = _make_scene((1280, 720), full_menu=False)
    full = _make_scene((1280, 720), full_menu=True)

    assert len(sparse.menu.items) == 3
    assert len(full.menu.items) == 6
    assert sparse._panel_rect.height < full._panel_rect.height
    _assert_layout_is_safe(sparse, (1280, 720))
    _assert_layout_is_safe(full, (1280, 720))
