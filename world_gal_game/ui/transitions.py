"""Screen transitions: fade-in, fade-out, dissolve.

Implemented as small state machines that overlay a black (or any color)
Surface and gradually change its alpha. The App calls .update(dt) +
.draw(surface) each frame and removes the transition when .done is True.
"""
from __future__ import annotations

import pygame


class FadeTransition:
    def __init__(self, *, duration: float = 0.5, color=(0, 0, 0),
                 fade_in: bool = True, on_complete=None):
        self.duration = max(0.01, duration)
        self.color = color
        self.fade_in = fade_in   # True = black -> clear; False = clear -> black
        self.t = 0.0
        self.done = False
        self.on_complete = on_complete

    def update(self, dt: float) -> None:
        self.t += dt
        if self.t >= self.duration:
            self.done = True
            self.t = self.duration
            if self.on_complete is not None:
                try:
                    self.on_complete()
                finally:
                    self.on_complete = None

    def draw(self, surface: pygame.Surface) -> None:
        progress = self.t / self.duration
        alpha = (1 - progress) if self.fade_in else progress
        alpha = max(0.0, min(1.0, alpha))
        overlay = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        overlay.fill((*self.color, int(alpha * 255)))
        surface.blit(overlay, (0, 0))
