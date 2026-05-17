"""Widget base class.

Widgets follow the same lifecycle as scenes:
- handle_event(event) — react to a single pygame event (optional)
- update(dt, inp)     — per-frame update with the input snapshot
- draw(surface)       — render onto a surface

Subclasses only override what they need. Widgets are positioned by their
bounding rect (pygame.Rect) and may be enabled / disabled.
"""
from __future__ import annotations

import pygame


Rect = pygame.Rect


class Widget:
    def __init__(self, rect: pygame.Rect):
        self.rect = pygame.Rect(rect)
        self.visible: bool = True
        self.enabled: bool = True
        self.parent: "Widget | None" = None

    def set_position(self, x: int, y: int) -> None:
        self.rect.topleft = (x, y)

    def hit(self, pos: tuple[int, int]) -> bool:
        return self.rect.collidepoint(pos)

    def update(self, dt: float, inp) -> None:
        pass

    def draw(self, surface: pygame.Surface) -> None:
        pass
