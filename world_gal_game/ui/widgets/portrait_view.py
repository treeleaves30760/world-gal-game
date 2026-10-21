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
from ..motion import MotionKind, MotionPolicy, coerce_motion_policy


class PortraitView(Widget):
    def __init__(self, rect: pygame.Rect, assets: AssetManager,
                 *, side: str = "left",
                 motion: MotionPolicy | None = None):
        super().__init__(rect)
        self.assets = assets
        self.side = side
        self.motion = coerce_motion_policy(motion)
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
        # Opacity is kept as a safe hand-off, but reduced mode caps it to the
        # same short duration as every other central dissolve.
        fade_duration = self.motion.duration(
            255.0 / 800.0,
            MotionKind.OPACITY,
        )
        if fade_duration <= 0.0:
            self.alpha = self.target_alpha
            return
        elapsed = max(0.0, dt)
        step = (
            max(1, int((255.0 / fade_duration) * elapsed))
            if elapsed > 0.0 else 0
        )
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
        if (
            self.t < 0.3
            and self.motion.allows(MotionKind.SPATIAL)
        ):
            dy = int(math.sin(self.t * math.pi / 0.3) * 6)
        surface.blit(canvas, (self.rect.x, self.rect.y - dy))
