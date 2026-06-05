"""Horizontal value slider (0..1): a filled track with a draggable knob.

Mouse-driven (click-to-set + drag). Keyboard/gamepad users keep using the
adjacent -/+ steppers, which stay in the focus ring — so this widget never
needs to join the nav system; it's purely an additive visual + mouse control
that fills the dead margin a stepper row leaves and shows the level at a glance.
"""
from __future__ import annotations

from typing import Callable

import pygame

from .base import Widget
from ..fonts import FontRegistry
from ..theme import Theme


class Slider(Widget):
    def __init__(self, rect: pygame.Rect, value: float, *,
                 fonts: FontRegistry, theme: Theme,
                 on_change: Callable[[float], None] | None = None,
                 enabled: bool = True):
        super().__init__(rect)
        self.value = max(0.0, min(1.0, float(value)))
        self.fonts = fonts
        self.theme = theme
        self.on_change = on_change
        self.enabled = enabled
        self._dragging = False

    def set_value(self, value: float) -> None:
        """Sync the knob to an external change (e.g. a -/+ stepper press)."""
        self.value = max(0.0, min(1.0, float(value)))

    def _set_from_x(self, x: int) -> None:
        rel = (x - self.rect.x) / max(1, self.rect.width)
        v = max(0.0, min(1.0, rel))
        # Quantise to whole percents so it matches the -/+ steppers' 0.1 grid
        # feel without snapping (keeps drag smooth but the label tidy).
        v = round(v, 2)
        if abs(v - self.value) > 1e-4:
            self.value = v
            if self.on_change is not None:
                self.on_change(v)

    def update(self, dt: float, inp) -> None:
        if not self.enabled:
            self._dragging = False
            return
        mx, my = inp.mouse_pos
        # Generous vertical hit area (the visible track is thin).
        hit = (self.rect.x - 8 <= mx <= self.rect.right + 8
               and self.rect.y - 10 <= my <= self.rect.bottom + 10)
        held = bool(inp.mouse_pressed[0])
        if inp.mouse_clicked and hit:
            self._dragging = True
            self._set_from_x(mx)
        elif self._dragging and held:
            self._set_from_x(mx)        # track the drag even outside the bar
        elif not held:
            self._dragging = False

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        r = self.rect
        track_h = 6
        ty = r.y + (r.height - track_h) // 2
        # Track background.
        bg = pygame.Surface((r.width, track_h), pygame.SRCALPHA)
        pygame.draw.rect(bg, (*self.theme.text_dim[:3], 90), bg.get_rect(),
                         border_radius=3)
        surface.blit(bg, (r.x, ty))
        # Filled portion.
        fw = int(r.width * self.value)
        if fw > 0:
            fill = pygame.Surface((fw, track_h), pygame.SRCALPHA)
            pygame.draw.rect(fill, (*self.theme.accent[:3], 235),
                             fill.get_rect(), border_radius=3)
            surface.blit(fill, (r.x, ty))
        # Knob.
        kx = r.x + fw
        ky = r.y + r.height // 2
        pygame.draw.circle(surface, self.theme.text, (kx, ky), 8)
        pygame.draw.circle(surface, self.theme.accent[:3], (kx, ky), 8, 2)
