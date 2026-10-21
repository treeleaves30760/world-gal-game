"""Rounded translucent panel — the "frosted glass" container.

Used as the background for dialogue boxes, location info, overlays,
menus, etc. Draws a filled rounded rect + an accent border.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pygame

from .base import Widget
from ..system_chrome import ChromeRequest
from ..theme import Theme

if TYPE_CHECKING:
    from ..system_chrome import SystemChromeHost


class Panel(Widget):
    def __init__(self, rect: pygame.Rect, theme: Theme, *,
                 fill: tuple | None = None,
                 border: tuple | None = None,
                 radius: int | None = None,
                 border_width: int = 1,
                 chrome: "SystemChromeHost | None" = None,
                 screen: str = "system",
                 role: str = "main",
                 density: float = 1.0):
        super().__init__(rect)
        self.theme = theme
        self.fill = fill if fill is not None else theme.bg_panel
        self.border = border if border is not None else theme.border
        self.radius = radius if radius is not None else theme.radius_m
        self.border_width = border_width
        self.chrome = chrome
        self.screen = screen
        self.role = role
        self.density = max(0.01, float(density))
        # Stock panel pixels are immutable until one of the resolved paint
        # inputs changes.  Large comparison panels can cover most of a 4K
        # canvas, so rebuilding this temporary alpha surface every frame is a
        # substantial allocation/draw cost. Keep one exact fallback surface;
        # plugin results remain owned by the bounded SystemChromeHost cache.
        self._fallback_cache_key: tuple[object, ...] | None = None
        self._fallback_cache: pygame.Surface | None = None

    def release_render_cache(self) -> None:
        """Drop retained paint surfaces without changing panel semantics."""
        self._fallback_cache_key = None
        self._fallback_cache = None

    @staticmethod
    def _frozen_color(value: object) -> object:
        """Snapshot mutable colour sequences for reliable cache identity."""
        try:
            return tuple(value)  # type: ignore[arg-type]
        except TypeError:
            return value

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        if self.chrome is not None and self.chrome.paint(
            surface,
            self.rect,
            ChromeRequest(
                part="panel",
                screen=self.screen,
                variant=self.role,
                size=self.rect.size,
                density=self.density,
            ),
        ):
            return
        fill = self.fill
        border = self.border
        border_width = self.border_width
        # Pack chrome may deliberately decline high contrast.  The stock
        # fallback must then use the live engine palette instead of retaining
        # an alpha colour captured when the scene was constructed in standard
        # mode.  Standard mode keeps the historical stored values exactly.
        if getattr(self.theme, "visual_profile", "standard") == "high_contrast":
            fill = self.theme.bg_panel
            border = self.theme.border_strong
            border_width = max(2, border_width)
        cache_key: tuple[object, ...] = (
            self.rect.size,
            self._frozen_color(fill),
            self._frozen_color(border),
            border_width,
            self.radius,
            getattr(self.theme, "visual_profile", "standard"),
        )
        if (
            cache_key != self._fallback_cache_key
            or self._fallback_cache is None
        ):
            panel = pygame.Surface(self.rect.size, pygame.SRCALPHA)
            pygame.draw.rect(
                panel,
                fill,
                panel.get_rect(),
                border_radius=self.radius,
            )
            if border_width > 0:
                pygame.draw.rect(
                    panel,
                    border,
                    panel.get_rect(),
                    width=border_width,
                    border_radius=self.radius,
                )
            self._fallback_cache_key = cache_key
            self._fallback_cache = panel
        panel = self._fallback_cache
        assert panel is not None
        surface.blit(panel, self.rect.topleft)
