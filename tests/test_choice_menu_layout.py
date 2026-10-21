"""Density-aware layout contracts for the dialogue choice menu."""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.theme import default_theme
from world_gal_game.ui.widgets.choice_menu import ChoiceMenu


@pytest.fixture(scope="module", autouse=True)
def _pygame_init():
    pygame.display.init()
    pygame.font.init()
    yield
    pygame.quit()


@pytest.mark.parametrize("size", [(1280, 720), (1920, 1080)])
def test_choice_panel_uses_live_font_metrics_without_vertical_overlap(
    size: tuple[int, int],
) -> None:
    density = max(1.0, size[1] / 720.0)
    fonts = FontRegistry(candidates=(), scale=density)
    theme = default_theme()
    menu = ChoiceMenu(
        pygame.Rect((0, 0), size),
        fonts=fonts,
        theme=theme,
        on_choose=lambda _choice_id: None,
    )
    rows = [
        ("locked", "尚未開放", False, "需要先完成新生指引"),
        ("first", "先去學生餐廳", True),
        ("second", "先去圖書館", True),
    ]

    menu.set_choices(rows)

    assert menu.panel is not None
    panel = menu.panel.rect
    prompt_line_h = fonts.get(18, bold=True).get_linesize()
    button_line_h = fonts.get(18, bold=True).get_linesize()
    reason_line_h = fonts.get(16).get_linesize()
    panel_pad_x = round(
        (theme.pad_xl + theme.pad_m + theme.pad_xs) * density,
    )
    panel_pad_y = round((theme.pad_l + theme.pad_xs) * density)
    title_gap = round(theme.pad_m * density)
    row_gap = round(theme.pad_m * density)
    reason_gap = round(theme.pad_xs * density)
    button_pad_y = round(theme.pad_m * density)
    button_h = max(
        round(44 * density),
        button_line_h + button_pad_y * 2,
    )
    title_h = panel_pad_y + prompt_line_h + title_gap
    reason_h = reason_gap + reason_line_h
    expected_panel_h = (
        title_h
        + button_h * len(rows)
        + reason_h
        + row_gap * (len(rows) - 1)
        + panel_pad_y
    )

    assert menu._density == density
    assert menu._prompt_line_h == prompt_line_h
    assert menu._button_line_h == button_line_h
    assert menu._reason_line_h == reason_line_h
    assert menu._panel_pad_x == panel_pad_x
    assert menu._panel_pad_y == panel_pad_y
    assert menu._title_gap == title_gap
    assert menu._row_gap == row_gap
    assert menu._reason_gap == reason_gap
    assert menu._button_h == button_h
    assert menu._title_h == title_h
    assert panel.height == expected_panel_h

    first, second, third = (button.rect for button in menu.buttons)
    assert first.top == panel.top + title_h
    assert first.height == second.height == third.height == button_h
    assert menu._prompt_rect.bottom + title_gap <= first.top
    assert not menu._prompt_rect.colliderect(first)
    assert second.top == first.bottom + reason_h + row_gap
    assert third.top == second.bottom + row_gap
    assert third.bottom + panel_pad_y == panel.bottom

    reason_anchor = menu._reason_anchors[0]
    assert reason_anchor is not None
    assert reason_anchor[1] == first.bottom + reason_gap
    assert reason_anchor[1] + reason_line_h + row_gap == second.top
    assert all(panel.contains(button.rect) for button in menu.buttons)
    assert panel.contains(menu._prompt_rect)

    surface = pygame.Surface(size, pygame.SRCALPHA)
    menu.draw(surface)


def test_choice_button_height_uses_tall_font_line_not_nominal_size() -> None:
    """A font with tall line metrics must expand controls at baseline density."""

    class TallFontRegistry(FontRegistry):
        def get(self, size: int, *, bold: bool = False) -> pygame.font.Font:
            # Keep the advertised UI density at 1.0 while using deliberately
            # tall glyph metrics, as real CJK fonts commonly do.
            return pygame.font.Font(None, round(size * 1.8))

    fonts = TallFontRegistry(candidates=(), scale=1.0)
    theme = default_theme()
    menu = ChoiceMenu(
        pygame.Rect(0, 0, 1280, 720),
        fonts=fonts,
        theme=theme,
        on_choose=lambda _choice_id: None,
    )

    menu.set_choices([("choice", "繼續", True)])

    line_h = fonts.get(18, bold=True).get_linesize()
    expected_h = max(44, line_h + theme.pad_m * 2)
    assert menu._button_line_h == line_h
    assert menu.buttons[0].rect.height == expected_h
    assert menu._prompt_rect.bottom + menu._title_gap <= menu.buttons[0].rect.top
