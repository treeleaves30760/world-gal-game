"""Vertical scroll container.

Holds a list of (label, callable | None) rows, supports mouse wheel and
drag-less scroll. Used by event-log + affection overlays. Designed to be
"good enough" rather than full HTML-style scrolling.
"""
from __future__ import annotations

import pygame

from .base import Widget
from ..fonts import FontRegistry
from ..theme import Theme


class ScrollArea(Widget):
    def __init__(self, rect: pygame.Rect, *, fonts: FontRegistry, theme: Theme):
        super().__init__(rect)
        self.fonts = fonts
        self.theme = theme
        self.surface: pygame.Surface | None = None
        self.scroll_y = 0
        self.content_height = 0
        self.content_drawer = None   # callable(target: Surface) -> int (content_height)

    def set_drawer(self, drawer) -> None:
        self.content_drawer = drawer
        self.scroll_y = 0

    def update(self, dt: float, inp) -> None:
        if self.rect.collidepoint(inp.mouse_pos):
            self.scroll_y -= inp.mouse_wheel * 36
        # clamp
        if self.content_height <= self.rect.height:
            self.scroll_y = 0
        else:
            self.scroll_y = max(0, min(self.scroll_y,
                                        self.content_height - self.rect.height))

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        # Pre-render content at full height onto an offscreen surface.
        if self.content_drawer is None:
            return
        # First draw to a large buffer; then blit a window slice into surface.
        big = pygame.Surface((self.rect.width, max(self.rect.height, 8000)),
                             pygame.SRCALPHA)
        try:
            self.content_height = int(self.content_drawer(big))
        except Exception:
            self.content_height = self.rect.height
        view = pygame.Surface(self.rect.size, pygame.SRCALPHA)
        view.blit(big, (0, -self.scroll_y))
        surface.blit(view, self.rect.topleft)
        # scrollbar
        if self.content_height > self.rect.height:
            track_h = self.rect.height
            knob_h = max(28, int(track_h * (self.rect.height / self.content_height)))
            knob_y = int(self.scroll_y / self.content_height * track_h)
            pygame.draw.rect(surface, (*self.theme.border_soft[:3], 80),
                             (self.rect.right - 6, self.rect.y, 4, track_h),
                             border_radius=2)
            pygame.draw.rect(surface, (*self.theme.accent[:3], 180),
                             (self.rect.right - 6,
                              self.rect.y + knob_y, 4, knob_h),
                             border_radius=2)
