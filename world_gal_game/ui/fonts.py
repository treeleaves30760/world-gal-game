"""Font lookup with CJK fallback.

Pygame's default font does not render Chinese glyphs. We probe a list of
system font names plus an optional bundled .ttf and cache pygame.font.Font
objects so we don't re-open the file on every draw call.
"""
from __future__ import annotations

from pathlib import Path

import pygame


class FontRegistry:
    """Loads & caches fonts. Always pass through .get(size) at draw time."""

    def __init__(self, candidates: tuple[str, ...], bundled: Path | None = None):
        self.candidates = candidates
        self.bundled = bundled
        self._resolved_path: Path | str | None = None
        self._cache: dict[tuple[str | None, int, bool], pygame.font.Font] = {}
        self._resolve()

    def _resolve(self) -> None:
        # 1) prefer a bundled font file (works inside a PyInstaller binary)
        if self.bundled is not None and Path(self.bundled).exists():
            self._resolved_path = Path(self.bundled)
            return
        # 2) try sysfont names; pygame returns None-ish if missing
        for name in self.candidates:
            match = pygame.font.match_font(name)
            if match:
                self._resolved_path = match
                return
        # 3) last resort: pygame's default. CJK glyphs will tofu, but it
        # won't crash; the user will see boxes and notice the missing font.
        self._resolved_path = None

    def get(self, size: int, *, bold: bool = False) -> pygame.font.Font:
        key = (str(self._resolved_path) if self._resolved_path else None,
               size, bold)
        if key not in self._cache:
            if self._resolved_path:
                f = pygame.font.Font(str(self._resolved_path), size)
                f.set_bold(bold)
            else:
                f = pygame.font.SysFont(None, size, bold=bold)
            self._cache[key] = f
        return self._cache[key]

    def render(self, text: str, size: int, color, *,
               bold: bool = False, antialias: bool = True) -> pygame.Surface:
        return self.get(size, bold=bold).render(text, antialias, color)
