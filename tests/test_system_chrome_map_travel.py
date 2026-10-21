"""Pack chrome seams for the spatial map and travel confirmation."""
from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from world_gal_game.scenes.destination_picker import DestinationPickerScene
from world_gal_game.scenes.map_scene import MapScene
from world_gal_game.ui.system_chrome import ChromeRequest


class RecordingChrome:
    """Structural stand-in that records and handles every paint request."""

    active = True

    def __init__(self) -> None:
        self.requests: list[tuple[pygame.Rect, ChromeRequest]] = []

    def paint(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        request: ChromeRequest,
    ) -> bool:
        self.requests.append((rect.copy(), request))
        layer = pygame.Surface(rect.size, pygame.SRCALPHA)
        layer.fill((13, 17, 29, 210))
        surface.blit(layer, rect.topleft)
        return True


@pytest.fixture(scope="module")
def driver():
    from world_gal_game.dev.driver import GameDriver

    instance = GameDriver(pack="demo_pack")
    instance.new_game()
    instance.skip_dialogue(max_frames=800)
    yield instance
    instance.quit()


def _requests(
    chrome: RecordingChrome,
    *,
    part: str | None = None,
    screen: str | None = None,
) -> list[ChromeRequest]:
    return [
        request
        for _rect, request in chrome.requests
        if (part is None or request.part == part)
        and (screen is None or request.screen == screen)
    ]


def test_spatial_map_routes_backdrop_panel_controls_and_selection_to_skin(
    driver,
) -> None:
    chrome = RecordingChrome()
    original_chrome = driver.app.ctx.ui_chrome
    original_meta = driver.app.ctx.meta
    try:
        driver.app.ctx.ui_chrome = chrome  # type: ignore[assignment]
        driver.app.ctx.meta = {
            "travel": {
                "scope": "graph",
                "guided_story": {
                    "future_horizon_phases": 4,
                    "max_primary": 3,
                },
            },
        }
        driver.app.state.map.move_to("town_square")
        scene = MapScene(driver.app.ctx)
        scene.enter(on_close=lambda: None, on_move_to=lambda _target: None)

        surface = pygame.Surface(driver.app.ctx.screen_size)
        scene.draw(surface)

        assert any(
            request.variant == "overlay"
            for request in _requests(chrome, part="backdrop", screen="map")
        )
        assert any(
            request.variant == "main"
            for request in _requests(chrome, part="panel", screen="map")
        )
        variants = {
            request.variant
            for request in _requests(chrome, part="button", screen="map")
        }
        assert {"close:ghost", "filter:ghost"} <= variants
        marker_requests = _requests(
            chrome,
            part="card",
            screen="map",
        )
        assert marker_requests
        assert all(
            request.variant.startswith(("marker:", "edge:off_campus:"))
            for request in marker_requests
        )
        assert any(
            request.selected
            and request.variant == "marker:current"
            for request in marker_requests
        )

        chrome.requests.clear()
        scene._toggle_ordinary()
        scene.draw(surface)
        filter_request = next(
            request
            for request in _requests(chrome, part="button", screen="map")
            if request.variant.startswith("filter:")
        )
        assert filter_request.variant == "filter:primary"
        assert filter_request.selected is True
    finally:
        driver.app.ctx.ui_chrome = original_chrome
        driver.app.ctx.meta = original_meta


def test_travel_browser_and_confirmation_share_one_pack_skin(driver) -> None:
    chrome = RecordingChrome()
    original_chrome = driver.app.ctx.ui_chrome
    try:
        driver.app.ctx.ui_chrome = chrome  # type: ignore[assignment]
        driver.app.state.map.move_to("town_square")
        scene = DestinationPickerScene(driver.app.ctx)
        scene.enter(
            on_close=lambda: None,
            on_move_to=lambda _target: None,
            on_world_map=lambda: None,
        )
        assert scene._dests

        surface = pygame.Surface(driver.app.ctx.screen_size)
        scene.draw(surface)

        assert _requests(chrome, part="backdrop", screen="travel")
        assert any(
            request.variant == "main"
            for request in _requests(chrome, part="panel", screen="travel")
        )
        assert _requests(chrome, part="card", screen="travel")
        button_variants = {
            request.variant
            for request in _requests(chrome, part="button", screen="travel")
        }
        assert "close:ghost" in button_variants
        assert "map:ghost" in button_variants

        chrome.requests.clear()
        scene._open_preview(scene._dests[0].loc.id)
        scene.draw(surface)

        layout = scene._preview_layout
        panel = pygame.Rect(layout["panel"])
        chrome_header = pygame.Rect(layout["chrome_header"])
        image = pygame.Rect(layout["image"])
        assert panel.contains(chrome_header)
        assert chrome_header.height > 0
        assert chrome_header.bottom <= image.top
        header_pixel = surface.get_at(chrome_header.center)[:3]
        assert all(
            abs(actual - expected) <= 1
            for actual, expected in zip(
                header_pixel,
                (13, 17, 29),
                strict=True,
            )
        )

        assert any(
            request.variant == "modal"
            for request in _requests(
                chrome,
                part="backdrop",
                screen="travel",
            )
        )
        assert any(
            request.variant == "confirmation"
            for request in _requests(chrome, part="panel", screen="travel")
        )
        preview_variants = {
            request.variant
            for request in _requests(chrome, part="button", screen="travel")
        }
        assert "confirm:primary" in preview_variants
        assert "cancel:ghost" in preview_variants
    finally:
        driver.app.ctx.ui_chrome = original_chrome
