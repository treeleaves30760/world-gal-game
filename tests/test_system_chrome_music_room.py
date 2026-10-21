"""System-chrome and accessibility seams for the Music Room."""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from world_gal_game.config import EngineConfig
from world_gal_game.core.game_state import GameState
from world_gal_game.core.localization import Localization
from world_gal_game.core.music_room_spec import MusicRoomManifest
from world_gal_game.npc.npc_base import NPCRegistry
from world_gal_game.scenes.base import SceneContext
from world_gal_game.scenes.music_room_scene import MusicRoomScene
from world_gal_game.ui.assets import AssetManager
from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.system_chrome import ChromeRequest
from world_gal_game.ui.theme import default_theme


@pytest.fixture(scope="module", autouse=True)
def _pygame_init():
    pygame.display.init()
    pygame.font.init()
    pygame.display.set_mode((10, 10))
    yield
    pygame.quit()


class _RecordingChrome:
    def __init__(self, handled=()):
        self.handled = set(handled)
        self.calls: list[ChromeRequest] = []

    def paint(self, surface, rect, request):
        assert request.size == rect.size
        self.calls.append(request)
        if request.part not in self.handled:
            return False
        layer = pygame.Surface(rect.size, pygame.SRCALPHA)
        layer.fill((32, 44, 58, 245))
        surface.blit(layer, rect.topleft)
        return True


def _context(
    root: Path,
    size: tuple[int, int],
    chrome: _RecordingChrome | None,
) -> SceneContext:
    state = GameState()
    state.meta["__music_room_manifest__"] = MusicRoomManifest.from_items([
        {
            "asset": "assets/bgm/unlocked.ogg",
            "loc_id": "unlocked",
            "title": "Unlocked Theme",
            "artist": "Composer",
            "usage": "Route A",
        },
        {
            "asset": "assets/bgm/locked.ogg",
            "loc_id": "locked",
            "title": "Locked Secret",
            "artist": "Composer",
            "usage": "Secret Route",
        },
    ])
    state.music_room.unlock("assets/bgm/unlocked.ogg")
    config = EngineConfig(screen_size=size)
    return SceneContext(
        config=config,
        state=state,
        npcs=NPCRegistry(),
        brain=None,  # type: ignore[arg-type]
        dialogue=None,  # type: ignore[arg-type]
        assets=AssetManager(pack_root=root),
        fonts=FontRegistry(
            candidates=(),
            scale=max(1.0, size[1] / 720),
        ),
        theme=default_theme(),
        localization=Localization(),
        screen_size=size,
        ui_chrome=chrome,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("size", [(640, 360), (1280, 720), (1920, 1080)])
def test_music_room_requests_finite_semantic_roles(
    tmp_path: Path,
    size: tuple[int, int],
) -> None:
    chrome = _RecordingChrome({
        "backdrop",
        "panel",
        "divider",
        "button",
        "card",
        "footer",
    })
    scene = MusicRoomScene(_context(tmp_path, size, chrome))
    scene.enter()
    scene.draw(pygame.Surface(size, pygame.SRCALPHA))

    parts = {request.part for request in chrome.calls}
    assert {"backdrop", "panel", "divider", "button", "card", "footer"} <= parts
    assert {request.screen for request in chrome.calls} == {"music_room"}
    assert {
        (request.enabled, request.selected)
        for request in chrome.calls
        if request.part == "card"
    } == {(True, False), (False, False)}
    assert any(
        request.part == "card" and request.focused
        for request in chrome.calls
    )
    assert scene.close_btn.rect.height >= 44
    assert scene.stop_btn.rect.height >= 44
    assert pygame.Rect((0, 0), size).contains(scene._panel_rect)
    assert scene._scroll.surface is None


def test_unhandled_music_renderer_is_pixel_identical_to_stock(tmp_path: Path):
    size = (1280, 720)
    stock = MusicRoomScene(_context(tmp_path, size, None))
    stock.enter()
    stock_surface = pygame.Surface(size, pygame.SRCALPHA)
    stock.draw(stock_surface)

    chrome = _RecordingChrome()
    fallback = MusicRoomScene(_context(tmp_path, size, chrome))
    fallback.enter()
    fallback_surface = pygame.Surface(size, pygame.SRCALPHA)
    fallback.draw(fallback_surface)

    assert chrome.calls
    assert pygame.image.tobytes(stock_surface, "RGBA") == pygame.image.tobytes(
        fallback_surface,
        "RGBA",
    )


@pytest.mark.parametrize("size", [(640, 360), (375, 667)])
def test_long_locale_header_reflows_inside_small_safe_area(
    tmp_path: Path,
    size: tuple[int, int],
) -> None:
    ctx = _context(tmp_path, size, None)
    ctx.localization = Localization.from_meta({
        "locale": {
            "ui": {
                "music_room": (
                    "Music archive with an intentionally extended heading"
                ),
                "music_stop": (
                    "Stop the currently previewed musical selection"
                ),
                "close": (
                    "Close this archive and return to the previous screen"
                ),
            },
        },
    })
    scene = MusicRoomScene(ctx)
    scene.enter()
    scene.draw(pygame.Surface(size, pygame.SRCALPHA))

    screen = pygame.Rect((0, 0), size)
    assert screen.contains(scene._panel_rect)
    assert scene._panel_rect.contains(scene._title_rect)
    assert scene._panel_rect.contains(scene.stop_btn.rect)
    assert scene._panel_rect.contains(scene.close_btn.rect)
    assert not scene.stop_btn.rect.colliderect(scene.close_btn.rect)
    assert scene.stop_btn.rect.height >= 44
    assert scene.close_btn.rect.height >= 44
    assert scene.stop_btn.text_overflow is False
    assert scene.close_btn.text_overflow is False
    assert scene._header_divider_y >= scene.close_btn.rect.bottom
    assert scene._scroll.rect.height >= 1
