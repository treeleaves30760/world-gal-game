"""Character portrait display.

Slides in/out from the side, supports a `bounce` micro-animation when a
new line is shown. Internally just composites the cached portrait
Surface; the AssetManager handles loading + scaling.
"""
from __future__ import annotations

import math

import pygame

from .base import Widget
from ..assets import AssetManager


class PortraitView(Widget):
    def __init__(self, rect: pygame.Rect, assets: AssetManager,
                 *, side: str = "left"):
        super().__init__(rect)
        self.assets = assets
        self.side = side
        self.path: str | None = None
        self.t = 0.0   # time since last set, for bounce animation
        self.alpha = 0
        self.target_alpha = 0

    def show(self, path: str | None) -> None:
        if path == self.path:
            return
        self.path = path
        self.t = 0.0
        self.target_alpha = 0 if not path else 255

    def update(self, dt: float, inp) -> None:
        self.t += dt
        # animate alpha
        step = int(800 * dt)
        if self.alpha < self.target_alpha:
            self.alpha = min(self.target_alpha, self.alpha + step)
        elif self.alpha > self.target_alpha:
            self.alpha = max(self.target_alpha, self.alpha - step)

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible or self.alpha == 0 or not self.path:
            return
        img = self.assets.scaled(self.path, self.rect.size, fit="contain")
        if img is None:
            return
        canvas = img.copy()
        canvas.set_alpha(self.alpha)
        # bounce: gentle y-jitter for first 0.3s
        dy = 0
        if self.t < 0.3:
            dy = int(math.sin(self.t * math.pi / 0.3) * 6)
        surface.blit(canvas, (self.rect.x, self.rect.y - dy))
