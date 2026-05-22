"""Screen transitions: fade-in, fade-out, dissolve.

Implemented as small state machines that overlay a black (or any color)
Surface and gradually change its alpha. The App calls .update(dt) +
.draw(surface) each frame and removes the transition when .done is True.
"""
from __future__ import annotations

import pygame

from .easing import resolve
from .layout import fit_rect


class FadeTransition:
    def __init__(self, *, duration: float = 0.5, color=(0, 0, 0),
                 fade_in: bool = True, on_complete=None, easing=None):
        self.duration = max(0.01, duration)
        self.color = color
        self.fade_in = fade_in   # True = black -> clear; False = clear -> black
        self.t = 0.0
        self.done = False
        self.on_complete = on_complete
        self._ease = resolve(easing)

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
        progress = self._ease(self.t / self.duration)
        alpha = (1 - progress) if self.fade_in else progress
        alpha = max(0.0, min(1.0, alpha))
        overlay = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        overlay.fill((*self.color, int(alpha * 255)))
        surface.blit(overlay, (0, 0))


class PortraitCrossfade:
    """Tracks old + new surfaces with an alpha mix over `duration` seconds."""

    def __init__(self, old: pygame.Surface | None, new: pygame.Surface | None,
                 duration: float = 0.25, *, easing=None) -> None:
        self.old = old
        self.new = new
        self.duration = max(0.01, duration)
        self.t = 0.0
        self._ease = resolve(easing)

    def update(self, dt: float) -> None:
        # Clamp so we never exceed duration regardless of frame-rate spikes.
        self.t = min(self.t + dt, self.duration)

    @property
    def done(self) -> bool:
        return self.t >= self.duration

    def draw(self, surface: pygame.Surface, dest_rect: pygame.Rect) -> None:
        progress = self._ease(self.t / self.duration)

        # Fit each portrait into the slot bounds preserving its aspect ratio
        # (old and new may differ in size), rather than stretching to fill.
        if self.old is not None and progress < 1.0:
            r = fit_rect(self.old.get_size(), dest_rect)
            old_copy = pygame.transform.smoothscale(self.old, r.size)
            old_copy.set_alpha(int((1.0 - progress) * 255))
            surface.blit(old_copy, r.topleft)

        if self.new is not None:
            r = fit_rect(self.new.get_size(), dest_rect)
            new_copy = pygame.transform.smoothscale(self.new, r.size)
            new_copy.set_alpha(int(progress * 255))
            surface.blit(new_copy, r.topleft)


class BackgroundFade:
    """Crossfades between two background surfaces (typically ~0.6 s)."""

    def __init__(self, old: pygame.Surface | None, new: pygame.Surface | None,
                 duration: float = 0.6, *, easing=None) -> None:
        self.old = old
        self.new = new
        self.duration = max(0.01, duration)
        self.t = 0.0
        self._ease = resolve(easing)

    def update(self, dt: float) -> None:
        self.t = min(self.t + dt, self.duration)

    @property
    def done(self) -> bool:
        return self.t >= self.duration

    def draw(self, surface: pygame.Surface) -> None:
        size = surface.get_size()
        progress = self._ease(self.t / self.duration)

        if self.old is not None and progress < 1.0:
            old_copy = pygame.transform.smoothscale(self.old, size)
            old_copy.set_alpha(int((1.0 - progress) * 255))
            surface.blit(old_copy, (0, 0))

        if self.new is not None:
            new_copy = pygame.transform.smoothscale(self.new, size)
            new_copy.set_alpha(int(progress * 255))
            surface.blit(new_copy, (0, 0))
