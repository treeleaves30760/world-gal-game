"""Floating notification toasts.

Used for achievements and other in-line state changes. A toast slides in
from the top-right, holds for a few seconds, then slides out. Multiple
toasts stack vertically.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pygame

from .base import Widget
from ..fonts import FontRegistry
from ..theme import Theme


@dataclass
class Toast:
    title: str
    detail: str = ""
    icon: str | None = None
    duration: float = 4.0
    age: float = 0.0
    # state populated by ToastStack each frame
    visible_alpha: int = 0


class ToastStack(Widget):
    """A non-interactive widget that renders a stack of toasts.

    Place an instance high in the scene stack so it's drawn over
    everything else, and feed it toasts via .push().
    """

    def __init__(self, rect: pygame.Rect, *, fonts: FontRegistry, theme: Theme,
                 assets=None, slide_in: float = 0.35,
                 hold: float | None = None):
        super().__init__(rect)
        self.fonts = fonts
        self.theme = theme
        self.assets = assets
        self.slide_in = slide_in
        self.toasts: list[Toast] = []

    def push(self, toast: Toast) -> None:
        self.toasts.append(toast)

    def update(self, dt: float, inp) -> None:
        for t in self.toasts:
            t.age += dt
            if t.age < self.slide_in:
                t.visible_alpha = int(255 * (t.age / self.slide_in))
            elif t.age < t.duration - self.slide_in:
                t.visible_alpha = 255
            elif t.age < t.duration:
                rem = (t.duration - t.age) / self.slide_in
                t.visible_alpha = int(255 * max(0.0, min(1.0, rem)))
            else:
                t.visible_alpha = 0
        self.toasts = [t for t in self.toasts if t.age < t.duration]

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible or not self.toasts:
            return
        toast_w = 360
        toast_h = 80
        gap = 10
        x = surface.get_width() - toast_w - 24
        y = 70
        for t in self.toasts:
            if t.visible_alpha == 0:
                y += toast_h + gap
                continue
            card = pygame.Surface((toast_w, toast_h), pygame.SRCALPHA)
            # slide offset (slide in from the right)
            slide = 0
            if t.age < self.slide_in:
                slide = int(40 * (1 - t.age / self.slide_in))
            pygame.draw.rect(card,
                             (*self.theme.bg_overlay[:3],
                              min(t.visible_alpha, 235)),
                             card.get_rect(),
                             border_radius=self.theme.radius_m)
            pygame.draw.rect(card,
                             (*self.theme.accent_warm[:3], t.visible_alpha),
                             card.get_rect(), width=2,
                             border_radius=self.theme.radius_m)
            # icon (or coloured side-bar if no icon was supplied)
            if t.icon and self.assets is not None:
                img = self.assets.scaled(t.icon, (52, 52), fit="contain")
                img.set_alpha(t.visible_alpha)
                card.blit(img, (12, (toast_h - 52) // 2))
                tx = 76
            else:
                # Tall vertical bar in the warm accent colour so the toast
                # still feels distinctive without an emoji.
                bar = pygame.Surface((6, toast_h - 16), pygame.SRCALPHA)
                bar.fill((*self.theme.accent_warm[:3], t.visible_alpha))
                card.blit(bar, (10, 8))
                tx = 28
            title = self.fonts.render(t.title, 20,
                                       (*self.theme.text[:3], t.visible_alpha),
                                       bold=True)
            card.blit(title, (tx, 14))
            if t.detail:
                detail = self.fonts.render(t.detail[:42], 14,
                                            (*self.theme.text_mute[:3],
                                             t.visible_alpha))
                card.blit(detail, (tx, 14 + title.get_height() + 4))
            surface.blit(card, (x + slide, y))
            y += toast_h + gap
