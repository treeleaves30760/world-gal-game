"""Live visual-profile contracts for stock widget fallbacks."""

from __future__ import annotations

from dataclasses import asdict
import os
from types import SimpleNamespace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.theme import Theme
from world_gal_game.ui.visual import VisualPolicy, VisualTheme
from world_gal_game.ui.widgets.button import Button
from world_gal_game.ui.widgets.panel import Panel


@pytest.fixture(scope="module", autouse=True)
def _pygame_font() -> None:
    pygame.font.init()


def _surface() -> pygame.Surface:
    return pygame.Surface((180, 90), pygame.SRCALPHA)


def _linear_channel(channel: int) -> float:
    value = channel / 255.0
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def _contrast_ratio(
    foreground: pygame.Color,
    background: pygame.Color,
) -> float:
    def luminance(color: pygame.Color) -> float:
        red, green, blue = (_linear_channel(channel) for channel in color[:3])
        return (0.2126 * red) + (0.7152 * green) + (0.0722 * blue)

    lighter = max(luminance(foreground), luminance(background))
    darker = min(luminance(foreground), luminance(background))
    return (lighter + 0.05) / (darker + 0.05)


class _DecliningChrome:
    def paint(self, *args: object, **kwargs: object) -> bool:
        return False


def _draw_button(
    theme: Theme | VisualTheme,
    *,
    selected: bool = False,
    enabled: bool = True,
    style: str = "primary",
    chrome: object | None = None,
) -> tuple[pygame.Surface, Button]:
    button = Button(
        pygame.Rect(20, 20, 120, 44),
        "",
        fonts=FontRegistry(candidates=()),
        theme=theme,
        style=style,
        selected=selected,
        enabled=enabled,
        chrome=chrome,  # type: ignore[arg-type]
    )
    surface = _surface()
    button.draw(surface)
    return surface, button


def test_visual_theme_is_live_without_mutating_the_authored_theme() -> None:
    config = SimpleNamespace(visual_accessibility_mode="standard")
    authored = Theme(
        bg_panel=(23, 31, 47, 173),
        accent=(73, 101, 149),
        text=(227, 219, 211),
        pad_m=19,
        radius_m=7,
    )
    authored_snapshot = asdict(authored)
    theme = VisualTheme(authored, VisualPolicy.from_config(config))

    assert theme.base_theme is authored
    assert theme.resolved_theme is authored
    assert theme.accent == authored.accent

    config.visual_accessibility_mode = "high_contrast"
    high_contrast = theme.resolved_theme

    assert high_contrast is theme.resolved_theme
    assert high_contrast is not authored
    assert high_contrast.bg_panel == (0, 0, 0, 255)
    assert high_contrast.pad_m == authored.pad_m
    assert high_contrast.radius_m == authored.radius_m
    assert theme.base_theme is authored
    assert asdict(authored) == authored_snapshot

    config.visual_accessibility_mode = "standard"

    assert theme.resolved_theme is authored
    assert theme.accent == authored.accent
    assert asdict(authored) == authored_snapshot


def test_visual_theme_overrides_preserve_live_policy_and_base_identity() -> None:
    config = SimpleNamespace(visual_accessibility_mode="standard")
    authored = Theme(pad_m=14, radius_m=10, accent=(17, 39, 61))
    theme = VisualTheme(authored, VisualPolicy.from_config(config))
    derived = theme.overridden(pad_m=29)

    assert derived.base_theme is not authored
    assert derived.resolved_theme is derived.base_theme
    assert derived.pad_m == 29
    assert authored.pad_m == 14
    assert authored.accent == (17, 39, 61)

    config.visual_accessibility_mode = "high_contrast"

    assert derived.visual_profile == "high_contrast"
    assert derived.pad_m == 29
    assert derived.bg_panel == (0, 0, 0, 255)
    assert theme.pad_m == 14
    assert theme.bg_panel == (0, 0, 0, 255)


def test_panel_keeps_standard_pixels_and_switches_live_to_strong_fallback() -> None:
    config = SimpleNamespace(visual_accessibility_mode="standard")
    authored = Theme(
        bg_panel=(27, 37, 57, 181),
        border=(29, 179, 107, 255),
        radius_m=0,
    )
    theme = VisualTheme(authored, VisualPolicy.from_config(config))
    rect = pygame.Rect(10, 10, 80, 40)
    stored_fill = (71, 83, 109, 137)
    stored_border = (203, 79, 43, 255)
    panel = Panel(
        rect,
        theme,  # type: ignore[arg-type]
        fill=stored_fill,
        border=stored_border,
        radius=0,
        border_width=1,
    )

    standard = _surface()
    panel.draw(standard)

    assert standard.get_at(rect.center) == stored_fill
    assert standard.get_at((rect.centerx, rect.top)) == stored_border
    assert standard.get_at((rect.centerx, rect.top + 1)) == stored_fill

    config.visual_accessibility_mode = "high_contrast"
    high_contrast = _surface()
    panel.draw(high_contrast)

    assert high_contrast.get_at(rect.center) == (0, 0, 0, 255)
    assert high_contrast.get_at((rect.centerx, rect.top)) == (
        *theme.border_strong[:3],
        255,
    )
    assert high_contrast.get_at((rect.centerx, rect.top + 1)) == (
        *theme.border_strong[:3],
        255,
    )

    config.visual_accessibility_mode = "standard"
    restored = _surface()
    panel.draw(restored)

    assert restored.get_at(rect.center) == standard.get_at(rect.center)
    assert restored.get_at((rect.centerx, rect.top)) == standard.get_at(
        (rect.centerx, rect.top)
    )
    assert restored.get_at((rect.centerx, rect.top + 1)) == standard.get_at(
        (rect.centerx, rect.top + 1)
    )


def test_panel_cache_snapshots_mutable_fill_and_border_values() -> None:
    fill = [31, 47, 63, 211]
    border = [173, 89, 41, 255]
    rect = pygame.Rect(10, 10, 80, 40)
    panel = Panel(
        rect,
        Theme(radius_m=0),
        fill=fill,  # type: ignore[arg-type]
        border=border,  # type: ignore[arg-type]
        radius=0,
        border_width=1,
    )

    first = _surface()
    panel.draw(first)
    retained = panel._fallback_cache
    assert retained is not None

    fill[:] = [71, 103, 137, 223]
    border[:] = [47, 191, 113, 255]
    second = _surface()
    panel.draw(second)

    assert panel._fallback_cache is not retained
    assert second.get_at(rect.center) == tuple(fill)
    assert second.get_at((rect.centerx, rect.top)) == tuple(border)

    panel.release_render_cache()
    assert panel._fallback_cache_key is None
    assert panel._fallback_cache is None


def test_button_standard_proxy_keeps_selected_and_disabled_pixel_semantics() -> None:
    authored = Theme(
        accent=(41, 83, 127),
        border=(199, 73, 37, 255),
        text_dim=(137, 149, 163),
        radius_m=0,
    )
    proxy = VisualTheme(authored, VisualPolicy("standard"))

    raw_selected, raw_selected_button = _draw_button(authored, selected=True)
    proxy_selected, proxy_selected_button = _draw_button(proxy, selected=True)
    raw_disabled, raw_disabled_button = _draw_button(authored, enabled=False)
    proxy_disabled, proxy_disabled_button = _draw_button(proxy, enabled=False)

    selected_probes = (
        proxy_selected_button.rect.center,
        (proxy_selected_button.rect.centerx, proxy_selected_button.rect.top),
        (proxy_selected_button.rect.centerx, proxy_selected_button.rect.top + 4),
        (proxy_selected_button.rect.left + 4, proxy_selected_button.rect.centery),
    )
    disabled_probes = (
        proxy_disabled_button.rect.center,
        (proxy_disabled_button.rect.centerx, proxy_disabled_button.rect.top),
        (proxy_disabled_button.rect.right - 18, proxy_disabled_button.rect.top + 3),
        (proxy_disabled_button.rect.right - 3, proxy_disabled_button.rect.top + 18),
    )

    assert raw_selected_button.rect == proxy_selected_button.rect
    assert raw_disabled_button.rect == proxy_disabled_button.rect
    for point in selected_probes:
        assert proxy_selected.get_at(point) == raw_selected.get_at(point)
    for point in disabled_probes:
        assert proxy_disabled.get_at(point) == raw_disabled.get_at(point)


def test_high_contrast_selected_button_has_a_closed_inset_shape_cue() -> None:
    config = SimpleNamespace(visual_accessibility_mode="high_contrast")
    theme = VisualTheme(Theme(radius_m=0), VisualPolicy.from_config(config))
    selected, button = _draw_button(theme, selected=True)
    unselected, _ = _draw_button(theme, selected=False)
    inset = button.rect.inflate(-8, -8)
    cue = (*theme.accent_alt[:3], 255)

    horizontal_points = ((x, inset.top) for x in range(inset.left + 3, inset.right - 3))
    vertical_points = ((inset.left, y) for y in range(inset.top + 3, inset.bottom - 3))

    assert all(selected.get_at(point) == cue for point in horizontal_points)
    assert all(selected.get_at(point) == cue for point in vertical_points)
    for point in (
        (inset.centerx, inset.top),
        (inset.centerx, inset.bottom - 1),
        (inset.left, inset.centery),
        (inset.right - 1, inset.centery),
    ):
        assert selected.get_at(point) == cue
        assert unselected.get_at(point) != cue
    assert selected.get_at(inset.center) != cue


@pytest.mark.parametrize("style", ["primary", "ghost", "danger"])
@pytest.mark.parametrize("chrome", [None, _DecliningChrome()])
@pytest.mark.parametrize(
    ("selected", "enabled"),
    [(False, True), (True, True), (False, False)],
)
def test_high_contrast_stock_button_is_opaque_black_with_strong_boundary(
    style: str,
    chrome: object | None,
    selected: bool,
    enabled: bool,
) -> None:
    config = SimpleNamespace(visual_accessibility_mode="high_contrast")
    theme = VisualTheme(Theme(radius_m=0), VisualPolicy.from_config(config))
    button = Button(
        pygame.Rect(20, 20, 120, 44),
        "",
        fonts=FontRegistry(candidates=()),
        theme=theme,  # type: ignore[arg-type]
        style=style,
        chrome=chrome,  # type: ignore[arg-type]
        selected=selected,
        enabled=enabled,
    )
    surface = _surface()
    # The fallback must be safe over arbitrary authored art, not just over a
    # transparent or already-black test canvas.
    surface.fill((91, 23, 147, 255))
    button.draw(surface)

    background = surface.get_at(button.rect.center)
    boundary = surface.get_at((button.rect.centerx, button.rect.top))

    assert background == (0, 0, 0, 255)
    assert boundary.a == 255
    assert _contrast_ratio(boundary, background) >= 3.0


def test_high_contrast_disabled_button_has_repeated_diagonal_hatches() -> None:
    config = SimpleNamespace(visual_accessibility_mode="high_contrast")
    theme = VisualTheme(Theme(radius_m=0), VisualPolicy.from_config(config))
    disabled, button = _draw_button(theme, enabled=False)
    cue = (*theme.text_dim[:3], 255)

    starts = [
        (button.rect.right - 18 + offset, button.rect.top + 3) for offset in (0, 6, 12)
    ]
    ends = [
        (button.rect.right - 3, button.rect.top + 18 - offset) for offset in (0, 6, 12)
    ]

    assert all(disabled.get_at(point) == cue for point in starts)
    assert all(disabled.get_at(point) == cue for point in ends)
    assert len({point[0] for point in starts}) == 3
    assert len({point[1] for point in ends}) == 3
    assert disabled.get_at(button.rect.center) != cue
