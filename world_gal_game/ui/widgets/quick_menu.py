"""Persistent quick-menu bar for the dialogue screen.

The signature visual-novel element: a slim row of compact buttons
(Auto / Skip / Log / Save / Load / Config / Menu ...) the player can reach
without opening a full menu. Buttons are laid out right-aligned from an
anchor edge and auto-size to their labels. An optional ``active_fn`` per
item lets a toggle (e.g. Auto) render highlighted while it is on.
"""
from __future__ import annotations

from typing import Callable

import pygame

from .base import Widget
from .button import Button
from ..fonts import FontRegistry
from ..theme import Theme


class QuickMenuBar(Widget):
    def __init__(self, right: int, y: int, *, fonts: FontRegistry, theme: Theme,
                 items: list[tuple[str, Callable[[], None] | None,
                                   Callable[[], bool] | None]],
                 height: int = 34, font_size: int = 14, gap: int = 6):
        self.fonts = fonts
        self.theme = theme
        self._entries: list[tuple[Button, Callable[[], bool] | None]] = []
        x = right
        font = fonts.get(font_size, bold=True)
        for label, cb, active_fn in reversed(items):
            w = font.size(label)[0] + 26
            btn = Button(pygame.Rect(x - w, y, w, height), label,
                         fonts=fonts, theme=theme, font_size=font_size,
                         style="ghost", on_click=cb, enabled=cb is not None)
            self._entries.insert(0, (btn, active_fn))
            x -= (w + gap)
        left = x + gap
        super().__init__(pygame.Rect(left, y, max(0, right - left), height))

    def update(self, dt: float, inp) -> None:
        if not self.visible:
            return
        for btn, _active in self._entries:
            btn.update(dt, inp)

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        for btn, active_fn in self._entries:
            # Reflect toggle state: an active toggle reads as a filled button.
            btn.style = "primary" if (active_fn and active_fn()) else "ghost"
            btn.draw(surface)

    def consumed(self, inp) -> bool:
        """True when the pointer is over the bar (so the caller can suppress
        click-to-advance for clicks that land on a quick-menu button)."""
        return self.visible and self.rect.collidepoint(inp.mouse_pos)
