"""Clickable button with hover state."""
from __future__ import annotations

from typing import Callable

import pygame

from .base import Widget
from ..fonts import FontRegistry
from ..theme import Theme


# Module-level UI click hook. The app installs one callback at init (playing the
# pack's ui_sound on the SFX bus); every Button calls it on activation, so we get
# clickable feedback without threading the AssetManager through every widget.
# Stays None (silent) in headless / tests / packs without a UI sound.
_click_sound_hook: "Callable[[], None] | None" = None


def set_click_sound_hook(fn: "Callable[[], None] | None") -> None:
    global _click_sound_hook
    _click_sound_hook = fn


class Button(Widget):
    def __init__(self, rect: pygame.Rect, label: str, *,
                 fonts: FontRegistry, theme: Theme,
                 font_size: int | None = None,
                 on_click: Callable[[], None] | None = None,
                 style: str = "primary",   # primary | ghost | danger
                 enabled: bool = True):
        super().__init__(rect)
        self.label = label
        self.fonts = fonts
        self.theme = theme
        self.font_size = font_size or theme.pad_m + 12
        self.on_click = on_click
        self.style = style
        self.enabled = enabled
        self._hover = False
        # Eased highlight [0,1]: lerps toward the hover state each frame so
        # the brighten fades in/out (~120ms) instead of snapping.
        self._hover_t = 0.0

    def update(self, dt: float, inp) -> None:
        self._hover = self.enabled and self.rect.collidepoint(inp.mouse_pos)
        target = 1.0 if self._hover else 0.0
        self._hover_t += (target - self._hover_t) * min(1.0, dt * 12.0)
        if self._hover and inp.mouse_clicked and self.on_click is not None:
            if _click_sound_hook is not None:
                _click_sound_hook()
            self.on_click()

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        if self.style == "primary":
            base = (*self.theme.accent[:3], 90)
            border = self.theme.border
        elif self.style == "ghost":
            base = (255, 255, 255, 16)
            border = self.theme.border_soft
        elif self.style == "danger":
            base = (*self.theme.warn[:3], 90)
            border = self.theme.warn
        else:
            base = self.theme.bg_panel
            border = self.theme.border_soft

        if self.enabled and self._hover_t > 0.01:
            amt = self._hover_t
            base = (min(255, int(base[0] + 30 * amt)),
                    min(255, int(base[1] + 25 * amt)),
                    min(255, int(base[2] + 30 * amt)),
                    base[3] if len(base) > 3 else 220)
        if not self.enabled:
            base = (60, 60, 60, 160)

        bg = pygame.Surface(self.rect.size, pygame.SRCALPHA)
        pygame.draw.rect(bg, base, bg.get_rect(),
                         border_radius=self.theme.radius_m)
        pygame.draw.rect(bg, border, bg.get_rect(),
                         width=1, border_radius=self.theme.radius_m)
        surface.blit(bg, self.rect.topleft)

        color = self.theme.text if self.enabled else self.theme.text_dim
        text_surf = self.fonts.render(self.label, self.font_size, color, bold=True)
        x = self.rect.x + (self.rect.width - text_surf.get_width()) // 2
        y = self.rect.y + (self.rect.height - text_surf.get_height()) // 2
        surface.blit(text_surf, (x, y))
