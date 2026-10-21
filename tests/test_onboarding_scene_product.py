"""First-run onboarding is concise, responsive, and skin-aware."""
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
from world_gal_game.npc.npc_base import NPCRegistry
from world_gal_game.scenes.base import SceneContext
from world_gal_game.scenes.onboarding_scene import OnboardingScene
from world_gal_game.ui.assets import AssetManager
from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.input import InputState
from world_gal_game.ui.system_chrome import ChromeRequest
from world_gal_game.ui.theme import default_theme


class RecordingChrome:
    def __init__(self) -> None:
        self.requests: list[ChromeRequest] = []

    def paint(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        request: ChromeRequest,
    ) -> bool:
        self.requests.append(request)
        layer = pygame.Surface(rect.size, pygame.SRCALPHA)
        layer.fill((18, 24, 44, 220))
        surface.blit(layer, rect.topleft)
        return True


@pytest.fixture(scope="module", autouse=True)
def _pygame_init():
    pygame.display.init()
    pygame.font.init()
    pygame.display.set_mode((10, 10))
    yield
    pygame.quit()


def _scene(
    size: tuple[int, int],
    root: Path,
    chrome: RecordingChrome | None,
) -> tuple[OnboardingScene, list[str]]:
    config = EngineConfig(
        screen_size=size,
        writable_root_override=root,
    )
    ctx = SceneContext(
        config=config,
        state=GameState(),
        npcs=NPCRegistry(),
        brain=None,  # type: ignore[arg-type]
        dialogue=None,  # type: ignore[arg-type]
        assets=AssetManager(pack_root=root),
        fonts=FontRegistry(
            candidates=config.font_candidates,
            scale=max(1.0, size[1] / 720.0),
        ),
        theme=default_theme(),
        localization=Localization(),
        screen_size=size,
        ui_chrome=chrome,  # type: ignore[arg-type]
    )
    closed: list[str] = []
    scene = OnboardingScene(ctx)
    scene.enter(on_close=lambda: closed.append("closed"))
    return scene, closed


@pytest.mark.parametrize("size", [
    (1280, 720),
    (1920, 1080),
    (800, 450),
    (640, 360),
])
def test_onboarding_teaches_only_immediate_actions_and_fits_canvas(
    size: tuple[int, int],
    tmp_path: Path,
) -> None:
    scene, _closed = _scene(size, tmp_path, None)
    canvas = pygame.Rect((0, 0), size)

    assert scene.describe() == {
        "scene": "OnboardingScene",
        "steps": 3,
        "primary_action": "開始閱讀",
    }
    assert [row[1] for row in scene._ROWS] == [
        "推進文字",
        "做出選擇",
        "開啟選單",
    ]
    assert scene.start_btn.focused is True
    assert canvas.contains(scene._panel_rect)
    assert scene._panel_rect.contains(scene.start_btn.rect)
    assert all(scene._panel_rect.contains(rect) for rect in scene._row_rects)
    # At 720p+ the first interruption stays materially smaller than the old
    # 720x560 manual. Compact canvases intentionally use most of the viewport
    # so 1x text and 44px controls remain readable instead of being scaled to
    # 24px hit targets.
    area_ratio = (
        scene._panel_rect.width * scene._panel_rect.height
        / (size[0] * size[1])
    )
    assert area_ratio < (0.31 if size[1] >= 720 else 0.90)
    assert scene.start_btn.rect.height >= 44
    assert all(rect.height >= 48 for rect in scene._row_rects)
    hint_font = scene.ctx.fonts.get(16)
    for rect, row in zip(scene._row_rects, scene._rows, strict=True):
        text_x = rect.x + round(48 * scene._density)
        fitted = scene._fit_text(
            row[2],
            hint_font,
            rect.right - text_x,
        )
        assert hint_font.size(fitted)[0] <= rect.right - text_x

    surface = pygame.Surface(size, pygame.SRCALPHA)
    scene.draw(surface)
    assert pygame.mask.from_surface(surface).count() > 0


def test_onboarding_routes_decorative_parts_through_pack_chrome(
    tmp_path: Path,
) -> None:
    chrome = RecordingChrome()
    scene, _closed = _scene((1280, 720), tmp_path, chrome)
    surface = pygame.Surface((1280, 720), pygame.SRCALPHA)

    scene.draw(surface)

    requests = {
        (request.part, request.screen, request.variant)
        for request in chrome.requests
    }
    assert ("backdrop", "onboarding", "overlay") in requests
    assert ("panel", "onboarding", "main") in requests
    assert ("button", "onboarding", "start:primary") in requests
    assert ("focus", "onboarding", "start:primary") in requests


def test_advance_dismisses_the_one_action_modal(tmp_path: Path) -> None:
    scene, closed = _scene((1280, 720), tmp_path, None)
    scene.update(
        1 / 60,
        InputState(events=[], advance_dialogue=True),
    )

    assert closed == ["closed"]
