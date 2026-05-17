"""Rounded translucent panel — the "frosted glass" container.

Used as the background for dialogue boxes, location info, overlays,
menus, etc. Draws a filled rounded rect + an accent border.
"""
from __future__ import annotations

import pygame

from .base import Widget
from ..theme import Theme


class Panel(Widget):
    def __init__(self, rect: pygame.Rect, theme: Theme, *,
                 fill: tuple | None = None,
                 border: tuple | None = None,
                 radius: int | None = None,
                 border_width: int = 1):
        super().__init__(rect)
        self.theme = theme
        self.fill = fill if fill is not None else theme.bg_panel
        self.border = border if border is not None else theme.border
        self.radius = radius if radius is not None else theme.radius_m
        self.border_width = border_width

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        panel = pygame.Surface(self.rect.size, pygame.SRCALPHA)
        pygame.draw.rect(panel, self.fill, panel.get_rect(),
                         border_radius=self.radius)
        if self.border_width > 0:
            pygame.draw.rect(panel, self.border, panel.get_rect(),
                             width=self.border_width, border_radius=self.radius)
        surface.blit(panel, self.rect.topleft)
