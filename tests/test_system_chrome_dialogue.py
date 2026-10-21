"""Paint-only pack chrome contracts for the core VN reading surfaces."""
from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from world_gal_game.core.ui_skin_spec import UISkinSpec
from world_gal_game.plugins import restore, snapshot, widget
from world_gal_game.scenes.dialogue_scene import DialogueScene
from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.input import InputState
from world_gal_game.ui.nvl_box import NVLBox
from world_gal_game.ui.system_chrome import (
    ChromeRequest,
    SystemChromeHost,
    UI_CHROME_PROTOCOL,
)
from world_gal_game.ui.theme import default_theme
from world_gal_game.ui.widgets.choice_menu import ChoiceMenu
from world_gal_game.ui.widgets.dialogue_box import DialogueBox
from world_gal_game.ui.widgets.quick_menu import QuickMenuBar


class RecordingChrome:
    """Small host double that paints opaque chrome and records semantics."""

    color = (181, 23, 91, 255)

    def __init__(self, *, handled: bool = True) -> None:
        self.handled = handled
        self.requests: list[tuple[pygame.Rect, ChromeRequest]] = []

    def paint(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        request: ChromeRequest,
    ) -> bool:
        self.requests.append((rect.copy(), request))
        if not self.handled:
            return False
        layer = pygame.Surface(rect.size, pygame.SRCALPHA)
        layer.fill(self.color)
        surface.blit(layer, rect.topleft)
        return True


@pytest.fixture(autouse=True)
def _pygame_init():
    pygame.display.init()
    pygame.font.init()
    pygame.display.set_mode((10, 10))
    yield
    pygame.quit()


@pytest.fixture(autouse=True)
def _clean_plugin_registries():
    before = snapshot()
    yield
    restore(before)


def _surface(size: tuple[int, int]) -> pygame.Surface:
    surface = pygame.Surface(size, pygame.SRCALPHA)
    surface.fill((5, 7, 11, 255))
    return surface


def _pixels(surface: pygame.Surface) -> bytes:
    return pygame.image.tobytes(surface, "RGBA")


def _dialogue_box(
    fonts: FontRegistry,
    *,
    chrome=None,
) -> DialogueBox:
    box = DialogueBox(
        pygame.Rect(32, 450, 1216, 230),
        fonts=fonts,
        theme=default_theme(),
        chrome=chrome,
    )
    box.set_line("白若雪", "這行文字必須由引擎畫在 chrome 上方。")
    box.force_reveal()
    return box


def _nvl_box(
    fonts: FontRegistry,
    *,
    chrome=None,
) -> NVLBox:
    box = NVLBox(
        pygame.Rect(60, 60, 1160, 520),
        fonts=fonts,
        theme=default_theme(),
        chrome=chrome,
    )
    box.set_line("白若雪", "NVL 的文字與繼續提示也必須保留。")
    box.force_reveal()
    return box


def _choice_menu(
    fonts: FontRegistry,
    *,
    chrome=None,
) -> ChoiceMenu:
    menu = ChoiceMenu(
        pygame.Rect(0, 0, 1280, 720),
        fonts=fonts,
        theme=default_theme(),
        on_choose=lambda _choice_id: None,
        chrome=chrome,
    )
    menu.set_choices([
        ("locked", "尚未開放", False, "先完成目前的目標"),
        ("go", "前往校門口", True),
    ])
    return menu


def _quick_menu(
    fonts: FontRegistry,
    *,
    chrome=None,
) -> QuickMenuBar:
    return QuickMenuBar(
        1248,
        400,
        fonts=fonts,
        theme=default_theme(),
        items=[
            ("自動", lambda: None, lambda: False),
            ("記錄", lambda: None, None),
        ],
        overflow_items=[
            ("設定", lambda: None, None),
        ],
        chrome=chrome,
    )


def test_unhandled_reading_chrome_is_pixel_identical_to_stock() -> None:
    """Every new seam must preserve the pre-skin rendering when unhandled."""
    fonts = FontRegistry(("Arial",))
    ignored = RecordingChrome(handled=False)

    pairs = [
        (_dialogue_box(fonts), _dialogue_box(fonts, chrome=ignored)),
        (_nvl_box(fonts), _nvl_box(fonts, chrome=ignored)),
        (_choice_menu(fonts), _choice_menu(fonts, chrome=ignored)),
        (_quick_menu(fonts), _quick_menu(fonts, chrome=ignored)),
    ]
    for stock, fallback in pairs:
        stock_surface = _surface((1280, 720))
        fallback_surface = _surface((1280, 720))
        stock.draw(stock_surface)
        fallback.draw(fallback_surface)
        assert _pixels(fallback_surface) == _pixels(stock_surface)

    assert ignored.requests


def test_failed_renderer_disables_host_and_falls_back_to_stock() -> None:
    """A renderer exception cannot crash or partially restyle dialogue."""

    @widget(
        "failing_dialogue_chrome",
        plugin_id="test_dialogue_chrome",
        signature={"protocol": UI_CHROME_PROTOCOL},
    )
    class FailingDialogueChrome:
        def __init__(self, **_):
            pass

        def render(self, _request):
            raise RuntimeError("paint failed")

    fonts = FontRegistry(("Arial",))
    theme = default_theme()
    host = SystemChromeHost(
        UISkinSpec(renderer="failing_dialogue_chrome"),
        assets=None,
        fonts=fonts,
        theme=theme,
    )
    stock = _dialogue_box(fonts)
    fallback = _dialogue_box(fonts, chrome=host)
    stock_surface = _surface((1280, 720))
    fallback_surface = _surface((1280, 720))

    stock.draw(stock_surface)
    fallback.draw(fallback_surface)

    assert host.active is False
    assert _pixels(fallback_surface) == _pixels(stock_surface)


def test_dialogue_scene_emits_all_additive_reading_roles() -> None:
    from world_gal_game.dev.driver import GameDriver

    chrome = RecordingChrome()
    driver = GameDriver(pack="demo_pack")
    try:
        driver.app.ctx.ui_chrome = chrome  # type: ignore[assignment]
        scene = DialogueScene(driver.app.ctx)
        scene.enter()

        assert isinstance(scene.box, DialogueBox)
        assert scene.box.chrome is chrome
        assert scene.choices is not None
        assert scene.choices.chrome is chrome
        assert scene._quick_bar is not None
        assert scene._quick_bar.chrome is chrome

        scene.box.set_line("白若雪", "品牌對話框仍由核心掌握正文。")
        scene.box.force_reveal()
        scene.auto_play_enabled = True
        scene.draw(driver.app.screen)

        scene._quick_bar.toggle_overflow()
        scene.draw(driver.app.screen)

        scene.choices.set_choices([
            ("locked", "現在不能選", False, "先完成目前的目標"),
            ("go", "繼續", True),
        ])
        scene.choices._focus = 1
        scene.choices.visible = True
        scene.draw(driver.app.screen)

        requests = [request for _rect, request in chrome.requests]
        assert {request.screen for request in requests} == {
            "dialogue",
            "choice",
        }
        variants = {request.variant for request in requests}
        roles = {variant.split(":", 1)[0] for variant in variants}
        assert {
            "main_adv",
            "nameplate",
            "quick_primary",
            "quick_overflow",
            "option",
            "status",
            "mode",
            "advance",
        } <= roles

        option_requests = [
            request
            for request in requests
            if request.screen == "choice"
            and request.part == "button"
            and request.variant.startswith("option:")
        ]
        assert any(not request.enabled for request in option_requests)
        assert any(request.focused for request in option_requests)
        assert any(
            request.variant == "quick_primary:primary"
            and request.selected
            for request in requests
        )

        disabled, focused = scene.choices.buttons
        disabled_pixel = driver.app.screen.get_at(
            (disabled.rect.left + 8, disabled.rect.centery),
        )
        assert disabled_pixel[:3] != chrome.color[:3]
        focus_pixel = driver.app.screen.get_at(
            (focused.rect.centerx, focused.rect.top + 1),
        )
        assert focus_pixel[:3] == scene.ctx.theme.accent_warm[:3]

        chosen: list[str] = []
        scene.choices.on_choose = chosen.append
        scene.choices._focus = -1
        scene.choices.update(
            1 / 60,
            InputState(
                events=[],
                nav=1,
                mouse_pos=(-1000, -1000),
            ),
        )
        assert scene.choices.focused_index == 1
        scene.choices.update(
            1 / 60,
            InputState(
                events=[],
                confirm=True,
                mouse_pos=(-1000, -1000),
            ),
        )
        assert chosen == ["go"]
    finally:
        driver.quit()


def test_dialogue_scene_passes_chrome_to_nvl_and_emits_main_nvl() -> None:
    from world_gal_game.dev.driver import GameDriver

    chrome = RecordingChrome()
    driver = GameDriver(pack="demo_pack")
    try:
        driver.cfg.nvl_mode = True
        driver.app.ctx.ui_chrome = chrome  # type: ignore[assignment]
        scene = DialogueScene(driver.app.ctx)
        scene.enter()

        assert isinstance(scene.box, NVLBox)
        assert scene.box.chrome is chrome
        scene.box.set_line("白若雪", "NVL 品牌底板。")
        scene.box.force_reveal()
        scene.draw(driver.app.screen)

        requests = [request for _rect, request in chrome.requests]
        assert any(
            request.screen == "dialogue"
            and request.part == "panel"
            and request.variant == "main_nvl"
            for request in requests
        )
        assert any(
            request.screen == "dialogue"
            and request.part == "footer"
            and request.variant == "advance"
            for request in requests
        )
    finally:
        driver.quit()


def test_single_enabled_choice_starts_focused_but_real_decision_does_not() -> None:
    fonts = FontRegistry(("Arial",))
    chosen: list[str] = []
    menu = ChoiceMenu(
        pygame.Rect(0, 0, 1280, 720),
        fonts=fonts,
        theme=default_theme(),
        on_choose=chosen.append,
    )
    menu.set_choices([
        ("locked", "尚未開放", False, "先完成目前的目標"),
        ("continue", "繼續", True),
    ])

    assert menu.focused_index == 1
    menu.update(
        1 / 60,
        InputState(
            events=[],
            confirm=True,
            mouse_pos=(-1000, -1000),
        ),
    )
    assert chosen == ["continue"]

    menu.set_choices([
        ("left", "先去圖書館", True),
        ("right", "先去校門口", True),
    ])
    assert menu.focused_index is None


def test_modal_over_new_dialogue_draws_settled_background_not_black() -> None:
    from world_gal_game.dev.driver import GameDriver
    from world_gal_game.ui.transitions import BackgroundFade

    driver = GameDriver(pack="demo_pack")
    try:
        scene = DialogueScene(driver.app.ctx)
        scene.enter()
        bright = pygame.Surface(driver.app.screen.get_size())
        bright.fill((210, 150, 90))
        scene._bg_surface = bright
        scene._bg_fade = BackgroundFade(None, bright, duration=0.6)
        scene._covered_by_overlay = True

        scene.draw(driver.app.screen)

        pixel = driver.app.screen.get_at(
            (driver.app.screen.get_width() // 2, 80),
        )
        assert pixel.r > scene.ctx.theme.bg_deep[0] + 40
        assert scene._bg_fade.t == 0.0
    finally:
        driver.quit()
