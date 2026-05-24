"""animated_portraits — built-in, web-safe portrait render backends.

Registers two ``@portrait_backend`` classes:

- ``"breath"`` — procedural idle motion on a single still (needs no extra art).
- ``"sprite"`` — sprite-sheet frame animation (``cols`` x ``rows`` at ``fps``).

Each backend is instantiated per slot by the dialogue scene as
``cls(spec, assets, fallback_size)`` and exposes ``update`` / ``draw`` /
``base_surface`` (see :mod:`world_gal_game.ui.portrait_backend`). All math is
plain pygame, so both run identically on desktop and web (pygbag). Everything is
defensive: a missing asset degrades to a placeholder, bad params fall back to a
static blit, so a malformed spec can never break rendering.
"""
from __future__ import annotations

import math

import pygame

from world_gal_game.plugins import portrait_backend
from world_gal_game.ui.portrait_backend import blit_fitted


def _args(spec) -> dict:
    a = getattr(spec, "backend_args", None)
    return a if isinstance(a, dict) else {}


def _f(args: dict, key: str, default: float) -> float:
    try:
        return float(args.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _i(args: dict, key: str, default: int) -> int:
    try:
        return int(args.get(key, default))
    except (TypeError, ValueError):
        return int(default)


@portrait_backend("breath", description="Procedural idle breathing on a still.")
class BreathBackend:
    """Gentle resting motion synthesised from a single portrait still.

    ``backend_args`` (all optional):
      ``period`` seconds per breath cycle        (default 3.6)
      ``scale``  peak breathing scale amplitude  (default 0.02 -> +2% height)
      ``bob``    peak vertical bob in px          (default 5.0)
      ``sway``   peak horizontal sway in px       (default 0.0)

    The still is grown from its bottom baseline (shoulders rise on the inhale,
    feet stay planted) and nudged by bob / sway. Subtle by design — enough to
    read as "alive" without distorting the art.
    """

    def __init__(self, spec, assets, fallback_size):
        self._surf = assets.resolve_portrait(spec, fallback_size=fallback_size)
        a = _args(spec)
        self._period = max(0.2, _f(a, "period", 3.6))
        self._scale = _f(a, "scale", 0.02)
        self._bob = _f(a, "bob", 5.0)
        self._sway = _f(a, "sway", 0.0)
        self._t = 0.0

    def update(self, dt: float) -> None:
        self._t += dt

    def base_surface(self):
        return self._surf

    def draw(self, surface, rect, *, flip: bool = False, alpha: int = 255) -> None:
        # Inhale curve in [0, 1] from a cosine, so t=0 is a resting low.
        phase = (self._t / self._period) * math.tau
        inhale = 0.5 - 0.5 * math.cos(phase)
        grow_w = int(round(rect.width * self._scale * inhale))
        grow_h = int(round(rect.height * self._scale * inhale))
        bob = int(round(-self._bob * inhale))
        sway = int(round(self._sway * math.sin(phase)))
        animated = pygame.Rect(
            rect.x - grow_w // 2 + sway,
            rect.y - grow_h + bob,        # grow upward from the baseline
            rect.width + grow_w,
            rect.height + grow_h,
        )
        blit_fitted(surface, self._surf, animated, flip=flip, alpha=alpha)


@portrait_backend("sprite", description="Sprite-sheet frame animation.")
class SpriteBackend:
    """Cycle frames sliced row-major from a sprite sheet.

    ``backend_args``:
      ``cols``   columns in the sheet              (default 1)
      ``rows``   rows in the sheet                  (default 1)
      ``fps``    frames per second                  (default 8.0; <=0 freezes)
      ``frames`` cap on frame count (row-major)     (default: cols*rows)

    Falls back to a single-frame (static) draw when the sheet can't be sliced.
    """

    def __init__(self, spec, assets, fallback_size):
        sheet = assets.resolve_portrait(spec, fallback_size=fallback_size)
        a = _args(spec)
        cols = max(1, _i(a, "cols", 1))
        rows = max(1, _i(a, "rows", 1))
        self._fps = _f(a, "fps", 8.0)
        self._frames = self._slice(sheet, cols, rows, a.get("frames"))
        self._t = 0.0

    @staticmethod
    def _slice(sheet, cols, rows, cap):
        w = sheet.get_width() // cols
        h = sheet.get_height() // rows
        if w <= 0 or h <= 0:
            return [sheet]
        frames = []
        for r in range(rows):
            for c in range(cols):
                sub = sheet.subsurface(pygame.Rect(c * w, r * h, w, h)).copy()
                frames.append(sub)
        if cap is not None:
            try:
                frames = frames[:max(1, int(cap))]
            except (TypeError, ValueError):
                pass
        return frames or [sheet]

    def update(self, dt: float) -> None:
        self._t += dt

    def base_surface(self):
        return self._frames[0]

    def draw(self, surface, rect, *, flip: bool = False, alpha: int = 255) -> None:
        if self._fps <= 0 or len(self._frames) == 1:
            frame = self._frames[0]
        else:
            idx = int(self._t * self._fps) % len(self._frames)
            frame = self._frames[idx]
        blit_fitted(surface, frame, rect, flip=flip, alpha=alpha)
