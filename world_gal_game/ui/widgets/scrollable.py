"""Vertical scroll container.

Holds a list of (label, callable | None) rows, supports mouse wheel and
drag-less scroll. Used by event-log + affection overlays. Designed to be
lightweight rather than full HTML-style scrolling.
"""
from __future__ import annotations

from collections.abc import Callable

import pygame

from .base import Widget
from ..fonts import FontRegistry
from ..theme import Theme


class ScrollArea(Widget):
    def __init__(self, rect: pygame.Rect, *, fonts: FontRegistry, theme: Theme):
        super().__init__(rect)
        self.fonts = fonts
        self.theme = theme
        self.surface: pygame.Surface | None = None
        self.scroll_y = 0
        self.content_height = 0
        self.content_drawer: Callable[[pygame.Surface], int] | None = None
        self._touch_dragging = False

    def set_drawer(
        self,
        drawer: Callable[[pygame.Surface], int] | None,
    ) -> None:
        self.content_drawer = drawer
        self.scroll_y = 0
        self._touch_dragging = False

    def _clamp_scroll(self) -> None:
        viewport_height = max(0, self.rect.height)
        max_scroll = max(0, self.content_height - viewport_height)
        self.scroll_y = max(0, min(int(self.scroll_y), max_scroll))

    def _ensure_surface(self, required_height: int) -> pygame.Surface:
        """Return a reusable content buffer large enough for this draw.

        The buffer starts at viewport height and grows only after the drawer
        reports taller content.  It deliberately does not shrink: retaining
        capacity avoids reallocating a large surface when dynamic content
        briefly becomes shorter.
        """
        width = max(1, self.rect.width)
        height = max(1, self.rect.height, int(required_height))
        if (
            self.surface is None
            or self.surface.get_width() != width
            or self.surface.get_height() < height
        ):
            self.surface = pygame.Surface((width, height), pygame.SRCALPHA)
        return self.surface

    def _draw_into_buffer(self, buffer: pygame.Surface) -> int:
        buffer.fill((0, 0, 0, 0))
        try:
            return max(0, int(self.content_drawer(buffer)))
        except Exception:
            return max(0, self.rect.height)

    def update(self, dt: float, inp) -> None:
        if self.rect.collidepoint(inp.mouse_pos):
            self.scroll_y -= inp.mouse_wheel * 36
        if getattr(inp, "touch_started", False):
            self._touch_dragging = self.rect.collidepoint(inp.mouse_pos)
        if self._touch_dragging:
            # Content follows the finger. Pointer capture continues outside
            # the viewport until release, matching native mobile lists.
            self.scroll_y -= int(getattr(inp, "touch_delta", (0, 0))[1])
        if (
            getattr(inp, "touch_released", False)
            or (
                self._touch_dragging
                and not getattr(inp, "touch_active", False)
            )
        ):
            self._touch_dragging = False
        self._clamp_scroll()

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        if self.content_drawer is None:
            return

        # Reuse the previous allocation.  On the first frame the viewport-sized
        # buffer also acts as a measurement pass; pygame clips drawing safely.
        # If the returned height is larger, grow once to the measured content
        # height and redraw so content beyond the first viewport is retained.
        buffer = self._ensure_surface(
            max(self.rect.height, self.content_height),
        )
        measured_height = self._draw_into_buffer(buffer)
        if measured_height > buffer.get_height():
            buffer = self._ensure_surface(measured_height)
            measured_height = self._draw_into_buffer(buffer)

        self.content_height = measured_height
        # Content can shrink between frames, so clamp again after measuring;
        # otherwise one frame at the old scroll offset would appear blank.
        self._clamp_scroll()

        # Blit the visible source rectangle directly.  No per-frame viewport
        # surface is needed.
        source_rect = pygame.Rect(
            0,
            self.scroll_y,
            max(0, self.rect.width),
            max(0, self.rect.height),
        )
        surface.blit(buffer, self.rect.topleft, area=source_rect)

        # scrollbar
        if self.content_height > self.rect.height:
            track_h = self.rect.height
            min_knob_h = min(28, track_h)
            knob_h = max(
                min_knob_h,
                int(track_h * (self.rect.height / self.content_height)),
            )
            knob_h = min(track_h, knob_h)
            max_scroll = self.content_height - self.rect.height
            knob_travel = track_h - knob_h
            knob_y = int(self.scroll_y / max_scroll * knob_travel)
            pygame.draw.rect(surface, (*self.theme.border_soft[:3], 80),
                             (self.rect.right - 6, self.rect.y, 4, track_h),
                             border_radius=2)
            pygame.draw.rect(surface, (*self.theme.accent[:3], 180),
                             (self.rect.right - 6,
                              self.rect.y + knob_y, 4, knob_h),
                             border_radius=2)
