"""Pack chrome seams for widgets, MenuScene, and SettingsScene.

The fake host deliberately paints loud flat colours.  This makes it possible
to prove that a renderer can replace chrome while the engine still owns text,
disabled treatment, focus, geometry, and input.
"""

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
from world_gal_game.scenes.menu_scene import MenuScene
from world_gal_game.scenes.settings_scene import SettingsScene
from world_gal_game.ui.assets import AssetManager
from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.input import InputState
from world_gal_game.ui.system_chrome import ChromeRequest
from world_gal_game.ui.theme import default_theme
from world_gal_game.ui.widgets.button import Button
from world_gal_game.ui.widgets.panel import Panel


class RecordingChrome:
    """Small structural stand-in for SystemChromeHost."""

    COLORS = {
        "backdrop": (7, 11, 17, 255),
        "panel": (29, 41, 59, 255),
        "divider": (71, 131, 179, 255),
        "button": (137, 31, 67, 255),
        "focus": (19, 223, 151, 255),
    }

    def __init__(
        self,
        *,
        handled: set[str] | None = None,
        active: bool = True,
    ) -> None:
        self.handled = set(self.COLORS) if handled is None else set(handled)
        self.active = active
        self.requests: list[tuple[pygame.Rect, ChromeRequest]] = []

    def paint(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        request: ChromeRequest,
    ) -> bool:
        self.requests.append((rect.copy(), request))
        if not self.active or request.part not in self.handled:
            return False
        layer = pygame.Surface(rect.size, pygame.SRCALPHA)
        layer.fill(self.COLORS[request.part])
        surface.blit(layer, rect.topleft)
        return True


class DisablingChrome(RecordingChrome):
    """Model a host that fault-isolates its renderer on the first request."""

    def paint(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        request: ChromeRequest,
    ) -> bool:
        self.requests.append((rect.copy(), request))
        self.active = False
        return False


@pytest.fixture(scope="module", autouse=True)
def _pygame_init():
    pygame.display.init()
    pygame.font.init()
    pygame.display.set_mode((10, 10))
    yield
    pygame.quit()


def _pixels(surface: pygame.Surface) -> bytes:
    return pygame.image.tobytes(surface, "RGBA")


def _context(
    size: tuple[int, int],
    root: Path,
    chrome: RecordingChrome | None,
) -> SceneContext:
    config = EngineConfig(
        screen_size=size,
        writable_root_override=root,
    )
    return SceneContext(
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


def _menu(
    size: tuple[int, int],
    root: Path,
    chrome: RecordingChrome | None,
) -> tuple[MenuScene, list[str]]:
    calls: list[str] = []
    ctx = _context(size, root, chrome)
    scene = MenuScene(ctx)
    callback_names = (
        "map",
        "affection",
        "log",
        "achievements",
        "inventory",
        "cg_gallery",
        "music_room",
        "endings",
        "scene_replay",
        "flowchart",
        "relationships",
        "character_profiles",
        "quest_log",
        "clues",
        "credits",
        "save",
        "load",
        "settings",
        "quit_to_title",
        "quit_app",
    )
    scene.enter(
        on_close=lambda: calls.append("close"),
        **{
            f"on_{name}": (lambda name=name: calls.append(name))
            for name in callback_names
        },
    )
    return scene, calls


def _settings(
    size: tuple[int, int],
    root: Path,
    chrome: RecordingChrome | None,
) -> SettingsScene:
    scene = SettingsScene(_context(size, root, chrome))
    scene.enter()
    return scene


def _key(key: int) -> InputState:
    return InputState(
        events=[
            pygame.event.Event(
                pygame.KEYDOWN,
                key=key,
                mod=0,
                unicode="",
            ),
        ],
        mouse_pos=(-1000, -1000),
    )


def test_panel_and_button_fallback_are_pixel_identical() -> None:
    theme = default_theme()
    fonts = FontRegistry(("Arial",))
    rect = pygame.Rect(8, 8, 180, 52)
    inactive = RecordingChrome(active=False)

    plain_panel = pygame.Surface((200, 70), pygame.SRCALPHA)
    fallback_panel = pygame.Surface((200, 70), pygame.SRCALPHA)
    Panel(rect, theme).draw(plain_panel)
    Panel(
        rect,
        theme,
        chrome=inactive,  # type: ignore[arg-type]
        screen="menu",
        role="main",
    ).draw(fallback_panel)
    assert _pixels(plain_panel) == _pixels(fallback_panel)

    plain_button = pygame.Surface((200, 70), pygame.SRCALPHA)
    fallback_button = pygame.Surface((200, 70), pygame.SRCALPHA)
    Button(rect, "系統", fonts=fonts, theme=theme).draw(plain_button)
    Button(
        rect,
        "系統",
        fonts=fonts,
        theme=theme,
        chrome=inactive,  # type: ignore[arg-type]
        screen="menu",
        role="action",
    ).draw(fallback_button)
    assert _pixels(plain_button) == _pixels(fallback_button)


@pytest.mark.parametrize("style", ["ghost", "other", "primary", "danger"])
def test_button_request_keeps_style_separate_from_interaction_emphasis(
    style: str,
) -> None:
    chrome = RecordingChrome(handled={"button", "focus"})
    theme = default_theme()
    rect = pygame.Rect(10, 10, 220, 56)
    button = Button(
        rect,
        "可讀文字",
        fonts=FontRegistry(("Arial",)),
        theme=theme,
        style=style,
        chrome=chrome,  # type: ignore[arg-type]
        screen="menu",
        role="action",
        selected=False,
    )
    button.focused = True
    surface = pygame.Surface((240, 80), pygame.SRCALPHA)
    button.draw(surface)

    button_request = next(
        request for _rect, request in chrome.requests if request.part == "button"
    )
    assert button_request.screen == "menu"
    assert button_request.variant == f"action:{style}"
    # Focus has an explicit boolean and therefore does not need to turn every
    # semantic ``primary`` button into a permanently high-emphasis surface.
    assert button_request.emphasis == 0
    assert button_request.selected is False
    assert button_request.focused is True

    # The renderer's green focus fill is underneath the engine-owned gold ring.
    assert surface.get_at((rect.centerx, rect.top + 1))[:3] == (theme.accent_warm[:3])


def test_button_hover_emphasis_is_bounded_and_cache_safe() -> None:
    chrome = RecordingChrome(handled={"button"})
    button = Button(
        pygame.Rect(10, 10, 220, 56),
        "移動",
        fonts=FontRegistry(("Arial",)),
        theme=default_theme(),
        style="primary",
        chrome=chrome,  # type: ignore[arg-type]
        screen="menu",
        role="action",
    )
    surface = pygame.Surface((240, 80), pygame.SRCALPHA)

    for hover_t, expected in ((0.0, 0), (0.2, 1), (0.5, 2), (1.0, 3)):
        button._hover_t = hover_t
        button.draw(surface)
        request = next(
            request
            for _rect, request in reversed(chrome.requests)
            if request.part == "button"
        )
        assert request.variant == "action:primary"
        assert request.emphasis == expected


def test_button_keeps_text_and_disabled_treatment_engine_owned() -> None:
    chrome = RecordingChrome(handled={"button"})
    theme = default_theme()
    fonts = FontRegistry(("Arial",))
    rect = pygame.Rect(10, 10, 240, 56)
    disabled = Button(
        rect,
        "不可使用",
        fonts=fonts,
        theme=theme,
        enabled=False,
        chrome=chrome,  # type: ignore[arg-type]
        screen="settings",
        role="action",
        text_align="left",
    )
    surface = pygame.Surface((270, 80), pygame.SRCALPHA)
    disabled.draw(surface)

    # A bright renderer background is visibly neutralised by the core veil.
    assert (
        surface.get_at((rect.x + 4, rect.centery))[:3]
        != (RecordingChrome.COLORS["button"][:3])
    )
    text_x = rect.x + theme.pad_m
    text_probe = pygame.Rect(
        text_x,
        rect.y,
        rect.width - theme.pad_m * 2,
        rect.height,
    )
    assert any(
        surface.get_at((x, y))[:3] == theme.text_dim[:3]
        for x in range(text_probe.left, text_probe.right)
        for y in range(text_probe.top, text_probe.bottom)
    )


@pytest.mark.parametrize("size", [(1280, 720), (1920, 1080), (640, 360)])
def test_menu_chrome_roles_responsive_targets_and_keyboard(
    size: tuple[int, int],
    tmp_path: Path,
) -> None:
    chrome = RecordingChrome()
    scene, calls = _menu(size, tmp_path / f"menu-{size[0]}", chrome)
    surface = pygame.Surface(size, pygame.SRCALPHA)
    scene.draw(surface)

    requests = [request for _rect, request in chrome.requests]
    assert any(
        request.part == "backdrop"
        and request.screen == "menu"
        and request.variant == "overlay"
        for request in requests
    )
    assert any(
        request.part == "panel" and request.variant == "main" for request in requests
    )
    assert any(
        request.part == "divider" and request.variant == "header"
        for request in requests
    )
    assert any(
        request.part == "footer"
        and request.screen == "menu"
        and request.variant == "hint"
        for request in requests
    )
    roles = {
        scene.close_btn.role,
        *(tab.role for tab in scene._tab_buttons),
        *(button.role for button in scene._buttons),
    }
    assert {"close", "tab", "action"} <= roles
    assert (
        sum(
            request.selected
            for request in requests
            if request.part == "button" and request.variant.startswith("tab:")
        )
        == 1
    )
    assert not any(
        request.selected
        for request in requests
        if request.part == "button" and request.variant.startswith("action:")
    )
    assert any(
        request.part == "focus" and request.variant.startswith("action:")
        for request in requests
    )

    minimum = round(44 * max(1.0, size[1] / 720.0))
    controls = [scene.close_btn, *scene._tab_buttons, *scene._buttons]
    assert all(
        control.rect.width >= minimum and control.rect.height >= minimum
        for control in controls
    )
    assert scene._panel_rect.contains(scene._footer_rect)
    assert scene._body_rect.bottom < scene._footer_rect.top
    assert all(scene._body_rect.contains(button.rect) for button in scene._buttons)
    assert scene._content_height <= scene._body_rect.height
    assert all(button.text_align == "left" for button in scene._buttons)
    focused = scene._buttons[scene._focus]
    assert (
        surface.get_at((focused.rect.centerx, focused.rect.top + 1))[:3]
        == (scene.ctx.theme.accent_warm[:3])
    )

    scene.update(0.0, _key(pygame.K_DOWN))
    scene.update(
        0.0,
        InputState(
            confirm=True,
            mouse_pos=(-1000, -1000),
        ),
    )
    assert calls == ["affection"]


def test_menu_chrome_receives_locked_map_state_and_focus(
    tmp_path: Path,
) -> None:
    chrome = RecordingChrome()
    scene, _calls = _menu((1280, 720), tmp_path / "locked-map", chrome)
    reason = "先完成新生集合。"
    scene.get_map_availability = lambda: (False, reason)
    scene._build()
    chrome.requests.clear()

    surface = pygame.Surface((1280, 720), pygame.SRCALPHA)
    scene.draw(surface)
    map_button = scene._buttons[0]
    request = next(
        request
        for rect, request in chrome.requests
        if rect == map_button.rect
        and request.part == "button"
        and request.variant.startswith("action:")
    )
    assert request.enabled is False
    assert request.focused is True
    assert map_button.tooltip == reason
    assert scene._focus_ring_rect is not None
    assert scene._focus_ring_rect.contains(map_button.rect)


@pytest.mark.parametrize(
    "size",
    [(1280, 720), (1920, 1080), (640, 360), (375, 667)],
)
def test_settings_chrome_roles_responsive_targets_and_keyboard(
    size: tuple[int, int],
    tmp_path: Path,
) -> None:
    chrome = RecordingChrome()
    scene = _settings(size, tmp_path / f"settings-{size[0]}", chrome)
    surface = pygame.Surface(size, pygame.SRCALPHA)
    scene.draw(surface)

    requests = [request for _rect, request in chrome.requests]
    assert any(
        request.part == "backdrop"
        and request.screen == "settings"
        and request.variant == "overlay"
        for request in requests
    )
    assert any(
        request.part == "panel" and request.variant == "main" for request in requests
    )
    assert any(
        request.part == "divider" and request.variant == "header"
        for request in requests
    )
    assert any(
        request.part == "footer"
        and request.screen == "settings"
        and request.variant == "hint"
        for request in requests
    )
    tab_requests = [
        request
        for request in requests
        if request.part == "button" and request.variant.startswith("tab:")
    ]
    assert len(tab_requests) == len(scene.CATEGORY_ORDER)
    assert sum(request.selected for request in tab_requests) == 1
    assert all(button.role == "tab" for _category, button in scene._tab_buttons)

    roles = {
        scene.close_btn.role,
        *(button.role for _category, button in scene._tab_buttons),
        *(button.role for button, _cx, _cy in scene._buttons),
    }
    assert {"close", "tab", "choice", "toggle"} <= roles

    text_scale_index = scene._button_ids.index("accessibility:text_scale")
    scene._focus = text_scale_index
    scene._ensure_focus_visible()
    scene._sync_focus_state()
    chrome.requests.clear()
    scene.draw(surface)
    assert any(
        request.selected
        for _rect, request in chrome.requests
        if request.part == "button" and request.variant.startswith("choice:")
    )

    scene._set_category("audio")
    chrome.requests.clear()
    scene.draw(surface)
    assert "stepper" in {button.role for button, _cx, _cy in scene._buttons}
    assert any(
        request.part == "button" and request.variant.startswith("stepper:")
        for _rect, request in chrome.requests
    )

    minimum = round(44 * max(1.0, size[1] / 720.0))
    controls = [
        scene.close_btn,
        *(button for _category, button in scene._tab_buttons),
        *(button for button, _cx, _cy in scene._buttons),
    ]
    assert all(
        control.rect.width >= minimum and control.rect.height >= minimum
        for control in controls
    )
    assert scene._panel_rect.contains(scene._footer_rect)
    assert scene._panel_rect.contains(scene._tabs_rect)
    assert scene._body_rect.bottom < scene._footer_rect.top
    action_like = [
        button
        for button, _cx, _cy in scene._buttons
        if button.role in {"action", "toggle"}
    ]
    assert action_like
    assert all(button.text_align == "left" for button in action_like)

    focused = scene._buttons[scene._focus][0]
    assert (
        surface.get_at((focused.rect.centerx, focused.rect.top + 1))[:3]
        == (scene.ctx.theme.accent_warm[:3])
    )
    before = scene._focus
    scene.update(0.0, _key(pygame.K_DOWN))
    assert scene._focus != before


@pytest.mark.parametrize("scene_name", ["menu", "settings"])
@pytest.mark.parametrize("failure_mode", ["inactive", "disable_on_paint"])
def test_inactive_skin_scene_fallback_is_pixel_identical(
    scene_name: str,
    failure_mode: str,
    tmp_path: Path,
) -> None:
    size = (1280, 720)
    fallback_chrome = (
        RecordingChrome(active=False)
        if failure_mode == "inactive"
        else DisablingChrome()
    )
    if scene_name == "menu":
        plain, _calls = _menu(size, tmp_path / "plain-menu", None)
        fallback, _calls = _menu(
            size,
            tmp_path / "fallback-menu",
            fallback_chrome,
        )
    else:
        plain = _settings(size, tmp_path / "plain-settings", None)
        fallback = _settings(
            size,
            tmp_path / "fallback-settings",
            fallback_chrome,
        )
    plain_surface = pygame.Surface(size, pygame.SRCALPHA)
    fallback_surface = pygame.Surface(size, pygame.SRCALPHA)
    plain.draw(plain_surface)
    fallback.draw(fallback_surface)
    assert _pixels(plain_surface) == _pixels(fallback_surface)
