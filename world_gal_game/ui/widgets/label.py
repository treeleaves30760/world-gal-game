"""Text labels.

`Label` renders a single line of text. `WrappedText` wraps long Chinese
or mixed-script text by CJK width-aware splitting, since pygame.font does
not auto-wrap on its own.
"""
from __future__ import annotations

import pygame

from .base import Widget
from ..fonts import FontRegistry


class Label(Widget):
    def __init__(self, pos: tuple[int, int], text: str, *,
                 fonts: FontRegistry, size: int,
                 color=(255, 255, 255), bold: bool = False,
                 max_width: int | None = None,
                 align: str = "left"):
        super().__init__(pygame.Rect(pos[0], pos[1], 0, 0))
        self.text = text
        self.fonts = fonts
        self.size = size
        self.color = color
        self.bold = bold
        self.max_width = max_width
        self.align = align
        self._cached: pygame.Surface | None = None
        self._cache_key: tuple | None = None
        self._rebuild()

    def set_text(self, text: str) -> None:
        if text != self.text:
            self.text = text
            self._cached = None

    def _rebuild(self) -> None:
        key = (self.text, self.size, self.color, self.bold)
        if key == self._cache_key and self._cached is not None:
            return
        self._cached = self.fonts.render(self.text, self.size, self.color,
                                         bold=self.bold)
        self.rect.size = self._cached.get_size()
        self._cache_key = key

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        self._rebuild()
        x = self.rect.x
        if self.align == "center" and self.max_width:
            x = self.rect.x + (self.max_width - self._cached.get_width()) // 2
        elif self.align == "right" and self.max_width:
            x = self.rect.x + (self.max_width - self._cached.get_width())
        surface.blit(self._cached, (x, self.rect.y))


def _wrap_lines(text: str, font: pygame.font.Font, max_width: int) -> list[str]:
    """Split text into lines that fit max_width pixels.

    Splits on existing newlines first, then on spaces, then char-by-char
    for CJK so a sentence without spaces still wraps cleanly.
    """
    if max_width <= 0:
        return [text]
    out_lines: list[str] = []
    for paragraph in text.split("\n"):
        if paragraph == "":
            out_lines.append("")
            continue
        line = ""
        for ch in paragraph:
            test = line + ch
            w, _ = font.size(test)
            if w > max_width and line:
                out_lines.append(line)
                line = ch
            else:
                line = test
        if line:
            out_lines.append(line)
    return out_lines


class WrappedText(Widget):
    def __init__(self, rect: pygame.Rect, text: str, *,
                 fonts: FontRegistry, size: int,
                 color=(255, 255, 255), line_spacing: int = 6,
                 reveal_chars: int | None = None):
        super().__init__(rect)
        self.fonts = fonts
        self.size = size
        self.color = color
        self.line_spacing = line_spacing
        self.text = text
        self.reveal_chars = reveal_chars   # if set, only show first N chars
        self._wrapped: list[str] = []
        self._dirty = True

    def set_text(self, text: str, *, reveal_chars: int | None = None) -> None:
        self.text = text
        self.reveal_chars = reveal_chars
        self._dirty = True

    def set_reveal(self, n: int | None) -> None:
        if n != self.reveal_chars:
            self.reveal_chars = n
            # Don't rewrap unless text changed; clipping uses the same wrap.

    def _ensure_wrap(self) -> None:
        if not self._dirty:
            return
        font = self.fonts.get(self.size)
        self._wrapped = _wrap_lines(self.text, font, self.rect.width)
        self._dirty = False

    def total_chars(self) -> int:
        return sum(len(line) for line in self._wrapped) + max(0, len(self._wrapped) - 1)

    def fully_revealed(self) -> bool:
        if self.reveal_chars is None:
            return True
        self._ensure_wrap()
        return self.reveal_chars >= self.total_chars()

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        self._ensure_wrap()
        font = self.fonts.get(self.size)
        remaining = self.reveal_chars if self.reveal_chars is not None else None
        y = self.rect.y
        for line in self._wrapped:
            if remaining is not None:
                if remaining <= 0:
                    break
                take = min(len(line), remaining)
                drawn = line[:take]
                remaining -= take + 1   # +1 for the implicit newline
            else:
                drawn = line
            if drawn:
                surf = font.render(drawn, True, self.color)
                surface.blit(surf, (self.rect.x, y))
            y += font.get_linesize() + self.line_spacing
