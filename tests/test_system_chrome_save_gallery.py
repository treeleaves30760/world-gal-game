"""System-chrome seams for save/load and the CG gallery."""
from __future__ import annotations

import os
import types
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from world_gal_game.config import EngineConfig
from world_gal_game.core.game_state import GameState
from world_gal_game.core.localization import Localization
from world_gal_game.core.save_manager import SaveManager
from world_gal_game.npc.npc_base import NPCRegistry
from world_gal_game.scenes.base import SceneContext
from world_gal_game.scenes.cg_gallery_scene import CGGalleryScene
from world_gal_game.scenes.save_scene import SaveScene
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
    """Small host-shaped spy; unhandled requests exercise stock fallback."""

    def __init__(self, handled: set[str] | None = None):
        self.handled = handled or set()
        self.calls: list[ChromeRequest] = []

    def paint(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        request: ChromeRequest,
    ) -> bool:
        assert request.size == rect.size
        self.calls.append(request)
        if request.part not in self.handled:
            return False
        colors = {
            "backdrop": (7, 9, 13, 210),
            "panel": (18, 23, 31, 248),
            "divider": (210, 160, 80, 255),
            "button": (55, 67, 82, 250),
            "card": (35, 43, 55, 245),
            "footer": (22, 28, 38, 250),
        }
        painted = pygame.Surface(rect.size, pygame.SRCALPHA)
        painted.fill(colors[request.part])
        surface.blit(painted, rect.topleft)
        return True


def _save_context(
    root: Path,
    size: tuple[int, int],
    chrome: _RecordingChrome | None,
) -> SceneContext:
    save_dir = root / "saves"
    config = EngineConfig(screen_size=size)
    config.save_dir = types.MethodType(
        lambda self, _path=save_dir: _path,
        config,
    )
    return SceneContext(
        config=config,
        state=GameState(),
        npcs=NPCRegistry(),
        brain=None,  # type: ignore[arg-type]
        dialogue=None,  # type: ignore[arg-type]
        assets=None,  # type: ignore[arg-type]
        fonts=FontRegistry(
            candidates=(),
            scale=max(1.0, size[1] / 720.0),
        ),
        theme=default_theme(),
        localization=Localization(),
        screen_size=size,
        ui_chrome=chrome,  # type: ignore[arg-type]
    )


def _plant_save_roles(root: Path) -> None:
    manager = SaveManager(root / "saves")
    state = {"player": {"name": "Tester"}, "meta": {}}
    manager.save("slot_manual", state, label="Manual", summary="Day 1")
    manager.save("quicksave", state, label="Quick", summary="Day 1")
    manager.save("autosave_1", state, label="Auto", summary="Day 1")


def _gallery_context(
    root: Path,
    size: tuple[int, int],
    chrome: _RecordingChrome | None,
) -> tuple[SceneContext, list[str]]:
    cg_dir = root / "assets" / "cgs"
    cg_dir.mkdir(parents=True, exist_ok=True)
    paths = [
        "assets/cgs/unlocked.png",
        "assets/cgs/locked.png",
    ]
    for index, relative in enumerate(paths):
        art = pygame.Surface((320, 180))
        art.fill((60 + index * 60, 80, 110))
        pygame.image.save(art, root / relative)

    state = GameState()
    state.cg_gallery.unlock(paths[0])
    config = EngineConfig(screen_size=size)
    ctx = SceneContext(
        config=config,
        state=state,
        npcs=NPCRegistry(),
        brain=None,  # type: ignore[arg-type]
        dialogue=None,  # type: ignore[arg-type]
        assets=AssetManager(pack_root=root),
        fonts=FontRegistry(
            candidates=(),
            scale=max(1.0, size[1] / 720.0),
        ),
        theme=default_theme(),
        localization=Localization(),
        screen_size=size,
        ui_chrome=chrome,  # type: ignore[arg-type]
    )
    return ctx, paths


@pytest.mark.parametrize(
    "size",
    [
        (640, 360),
        (1280, 720),
        (1920, 1080),
    ],
)
def test_save_requests_finite_roles_at_supported_densities(
    tmp_path: Path,
    size: tuple[int, int],
) -> None:
    _plant_save_roles(tmp_path)
    chrome = _RecordingChrome({
        "backdrop",
        "panel",
        "divider",
        "button",
        "card",
        "footer",
    })
    scene = SaveScene(_save_context(tmp_path, size, chrome))
    scene.enter(mode="save")
    assert scene._focused_index == scene._actionable_indices()[0]
    scene.draw(pygame.Surface(size, pygame.SRCALPHA))

    assert {
        "backdrop",
        "panel",
        "divider",
        "button",
        "card",
        "footer",
    } <= {
        request.part for request in chrome.calls
    }
    assert {
        request.variant
        for request in chrome.calls
        if request.part == "card"
    } == {"new", "special", "slot"}
    assert {request.screen for request in chrome.calls} == {"save"}
    assert all(
        type(request.hovered) is bool
        and type(request.focused) is bool
        and request.size[0] > 0
        and request.size[1] > 0
        for request in chrome.calls
    )
    assert any(
        request.part == "card" and request.focused
        for request in chrome.calls
    )
    assert scene.close_btn.rect.height >= 44
    assert scene._action_h >= 44
    assert pygame.Rect((0, 0), size).contains(scene._panel_rect)

    occupied = next(
        item
        for item in scene._items
        if item.get("slot") == "slot_manual"
    )
    scene._request_action(occupied)
    scene.draw(pygame.Surface(size, pygame.SRCALPHA))
    assert scene._confirm_focus == 1
    assert all(button.rect.height >= 44 for button in scene._confirm_buttons)
    assert [button.focused for button in scene._confirm_buttons] == [
        False,
        True,
    ]
    assert any(
        request.part == "backdrop" and request.variant == "modal"
        for request in chrome.calls
    )
    assert any(
        request.part == "panel"
        and request.variant == "confirmation"
        and request.emphasis == 3
        for request in chrome.calls
    )


def test_load_screen_and_status_use_bounded_semantic_states(
    tmp_path: Path,
) -> None:
    _plant_save_roles(tmp_path)
    chrome = _RecordingChrome({"card", "footer"})
    scene = SaveScene(_save_context(tmp_path, (1280, 720), chrome))
    scene.enter(mode="load")
    scene._set_status("Unavailable", "error")
    scene.draw(pygame.Surface((1280, 720), pygame.SRCALPHA))

    assert {request.screen for request in chrome.calls} == {"load"}
    assert {
        request.variant
        for request in chrome.calls
        if request.part == "card"
    } == {"special", "slot"}
    footer = next(
        request
        for request in chrome.calls
        if request.part == "footer"
    )
    assert footer.variant == "error"
    assert footer.emphasis == 3


@pytest.mark.parametrize(
    "size",
    [
        (640, 360),
        (1280, 720),
        (1920, 1080),
    ],
)
def test_gallery_requests_locked_and_unlocked_card_roles(
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
    ctx, _paths = _gallery_context(tmp_path, size, chrome)
    scene = CGGalleryScene(ctx)
    scene.enter()
    scene.draw(pygame.Surface(size, pygame.SRCALPHA))

    assert {
        "backdrop",
        "panel",
        "divider",
        "button",
        "card",
        "footer",
    } <= {
        request.part for request in chrome.calls
    }
    assert any(
        request.part == "footer"
        and request.screen == "gallery"
        and request.variant == "hint"
        for request in chrome.calls
    )
    assert {
        request.variant
        for request in chrome.calls
        if request.part == "card"
    } == {"locked", "unlocked"}
    assert {request.screen for request in chrome.calls} == {"gallery"}
    focused = next(
        request
        for request in chrome.calls
        if request.part == "card" and request.focused
    )
    assert focused.enabled is (focused.variant == "unlocked")
    assert {
        (request.variant, request.enabled)
        for request in chrome.calls
        if request.part == "card"
    } == {("locked", False), ("unlocked", True)}
    assert all(type(request.hovered) is bool for request in chrome.calls)
    assert scene.close_btn.rect.height >= 44
    assert scene._cell_h >= 44
    assert pygame.Rect((0, 0), size).contains(scene._panel_rect)
    assert scene._panel_rect.contains(scene._footer_rect)
    assert scene._scroll.rect.bottom < scene._footer_rect.top


def test_unhandled_renderer_is_pixel_identical_to_stock_fallback(
    tmp_path: Path,
) -> None:
    size = (1280, 720)
    _plant_save_roles(tmp_path)
    save_ctx = _save_context(tmp_path, size, None)
    save_scene = SaveScene(save_ctx)
    save_scene.enter(mode="save")
    stock_save = pygame.Surface(size, pygame.SRCALPHA)
    save_scene.draw(stock_save)

    save_spy = _RecordingChrome()
    fallback_save_scene = SaveScene(
        _save_context(tmp_path, size, save_spy),
    )
    fallback_save_scene.enter(mode="save")
    fallback_save = pygame.Surface(size, pygame.SRCALPHA)
    fallback_save_scene.draw(fallback_save)
    assert save_spy.calls
    assert pygame.image.tobytes(stock_save, "RGBA") == pygame.image.tobytes(
        fallback_save,
        "RGBA",
    )

    gallery_ctx, _paths = _gallery_context(tmp_path, size, None)
    gallery_scene = CGGalleryScene(gallery_ctx)
    gallery_scene.enter()
    stock_gallery = pygame.Surface(size, pygame.SRCALPHA)
    gallery_scene.draw(stock_gallery)

    gallery_spy = _RecordingChrome()
    fallback_gallery_ctx, _paths = _gallery_context(
        tmp_path,
        size,
        gallery_spy,
    )
    fallback_gallery_scene = CGGalleryScene(fallback_gallery_ctx)
    fallback_gallery_scene.enter()
    fallback_gallery = pygame.Surface(size, pygame.SRCALPHA)
    fallback_gallery_scene.draw(fallback_gallery)
    assert gallery_spy.calls
    assert (
        pygame.image.tobytes(stock_gallery, "RGBA")
        == pygame.image.tobytes(fallback_gallery, "RGBA")
    )


def test_gallery_fullscreen_keeps_stock_black_viewer(
    tmp_path: Path,
) -> None:
    size = (1280, 720)
    chrome = _RecordingChrome({
        "backdrop",
        "panel",
        "divider",
        "button",
        "card",
        "footer",
    })
    ctx, paths = _gallery_context(tmp_path, size, chrome)
    scene = CGGalleryScene(ctx)
    scene.enter()
    scene._fullscreen = paths[0]
    chrome.calls.clear()

    surface = pygame.Surface(size, pygame.SRCALPHA)
    surface.fill((255, 0, 255, 255))
    scene.draw(surface)

    assert chrome.calls == []
    assert surface.get_at((0, 0)) == pygame.Color(0, 0, 0, 255)
    assert surface.get_at((size[0] - 1, 0)) == pygame.Color(0, 0, 0, 255)
