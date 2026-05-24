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

    def update(self, dt: float, **_ctx) -> None:
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

    def update(self, dt: float, **_ctx) -> None:
        self._t += dt

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

    def base_surface(self):
        return self._frames[0]

    def draw(self, surface, rect, *, flip: bool = False, alpha: int = 255) -> None:
        if self._fps <= 0 or len(self._frames) == 1:
            frame = self._frames[0]
        else:
            idx = int(self._t * self._fps) % len(self._frames)
            frame = self._frames[idx]
        blit_fitted(surface, frame, rect, flip=flip, alpha=alpha)


@portrait_backend("layered",
                  description="Layered rig: blink + lip-sync + breathing (web-safe).")
class LayeredBackend:
    """The flagship cross-platform animated portrait — pure pygame, web-safe.

    Composites stacked PNG layers (all sharing the base's canvas / registration)
    and animates them procedurally to approximate the Live2D *feel* without any
    native SDK or model asset:

    - **blink** — swaps the eye layer on a natural, self-driven schedule (no
      external signal). 2 layers = [open, closed]; 3 = [open, mid, closed]
      (mid eases the close/open).
    - **lip-sync** — cycles mouth layers while the scene reports ``talking=True``
      (the slot's character is the active speaker, mid-typewriter); rests on the
      closed mouth otherwise.
    - **breathing** — the whole composite gently breathes (shared with ``breath``).

    ``backend_args`` (everything optional; missing layers are simply skipped, so
    a spec degrades from a full rig down to a breathing still):
      ``base``   path to the body layer        (default: the spec's resolved still)
      ``blink``  [open, closed] or [open, mid, closed] layer paths
      ``mouth``  [closed, ..., open] layer paths (>=2 to lip-sync)
      ``blink_min`` / ``blink_max`` seconds between blinks  (default 2.5 / 6.0)
      ``blink_dur`` eyes-closed duration in seconds          (default 0.12)
      ``mouth_fps`` mouth cycling rate while talking          (default 10)
      ``period`` / ``scale`` / ``bob`` / ``sway`` breathing   (subtle defaults)

    Blink timing uses a tiny inline LCG (no ``import random`` — the engine's
    determinism guard scans this file), so it looks irregular yet reproducible.
    """

    def __init__(self, spec, assets, fallback_size):
        a = _args(spec)
        # Base layer: explicit path, else the spec's normal resolved still.
        base_path = a.get("base")
        if isinstance(base_path, str) and assets.has_image(base_path):
            self._base = assets.image(base_path, fallback_size=fallback_size)
        else:
            self._base = assets.resolve_portrait(spec, fallback_size=fallback_size)
        size = self._base.get_size()
        self._blink = self._load_layers(assets, a.get("blink"), size)
        self._mouth = self._load_layers(assets, a.get("mouth"), size)
        self._blink_min = max(0.05, _f(a, "blink_min", 2.5))
        self._blink_max = max(self._blink_min, _f(a, "blink_max", 6.0))
        self._blink_dur = max(0.02, _f(a, "blink_dur", 0.12))
        self._mouth_fps = max(0.0, _f(a, "mouth_fps", 10.0))
        self._period = max(0.2, _f(a, "period", 4.0))
        self._scale = _f(a, "scale", 0.015)
        self._bob = _f(a, "bob", 4.0)
        self._sway = _f(a, "sway", 0.0)

        self._t = 0.0
        self._talking = False
        # Blink state machine.
        self._lcg = (abs(hash(getattr(spec, "character", "x"))) % 2147483647) or 1
        self._next_blink = self._rand(self._blink_min, self._blink_max)
        self._blink_until = -1.0
        # Composite cache keyed by (eye_idx, mouth_idx).
        self._cache_key = None
        self._composite = None

    @staticmethod
    def _load_layers(assets, paths, size):
        """Load a list of layer paths that exist; missing ones are dropped."""
        if not isinstance(paths, (list, tuple)):
            return []
        out = []
        for p in paths:
            if isinstance(p, str) and assets.has_image(p):
                out.append(assets.image(p, fallback_size=size))
        return out

    def _rand(self, lo, hi):
        """Next interval in [lo, hi] from an inline LCG (no global random)."""
        self._lcg = (self._lcg * 1103515245 + 12345) & 0x7FFFFFFF
        return lo + (hi - lo) * (self._lcg / 0x7FFFFFFF)

    # ------------------------------------------------------------------

    def update(self, dt: float, *, talking: bool = False, **_ctx) -> None:
        self._t += dt
        self._talking = bool(talking)
        # Blink scheduler: when due, close the eyes for blink_dur, then schedule
        # the next blink. No-op when no blink layers were provided.
        if self._blink and self._t >= self._next_blink and self._blink_until < 0:
            self._blink_until = self._t + self._blink_dur
        if self._blink_until >= 0 and self._t >= self._blink_until:
            self._blink_until = -1.0
            self._next_blink = self._t + self._rand(self._blink_min, self._blink_max)

    def _eye_index(self):
        if len(self._blink) < 2 or self._blink_until < 0:
            return 0  # eyes open (or no blink layers)
        # 3-frame blink: mid frame on the first/last 30% of the close, else shut.
        if len(self._blink) >= 3:
            frac = 1.0 - (self._blink_until - self._t) / max(1e-3, self._blink_dur)
            return 1 if (frac < 0.3 or frac > 0.7) else 2
        return 1  # 2-frame: just closed

    def _mouth_index(self):
        if len(self._mouth) < 2 or not self._talking or self._mouth_fps <= 0:
            return 0  # closed / resting
        return int(self._t * self._mouth_fps) % len(self._mouth)

    def _compose(self):
        key = (self._eye_index(), self._mouth_index())
        if key == self._cache_key and self._composite is not None:
            return self._composite
        comp = self._base.copy()
        if self._blink:
            comp.blit(self._blink[min(key[0], len(self._blink) - 1)], (0, 0))
        if self._mouth:
            comp.blit(self._mouth[min(key[1], len(self._mouth) - 1)], (0, 0))
        self._cache_key = key
        self._composite = comp
        return comp

    def base_surface(self):
        # Resting frame (eyes open, mouth closed) for enter/exit transitions.
        self._talking = False
        self._blink_until = -1.0
        return self._compose()

    def draw(self, surface, rect, *, flip: bool = False, alpha: int = 255) -> None:
        comp = self._compose()
        # Breathing: grow the composite from its bottom baseline + gentle bob/sway.
        phase = (self._t / self._period) * math.tau
        inhale = 0.5 - 0.5 * math.cos(phase)
        grow_w = int(round(rect.width * self._scale * inhale))
        grow_h = int(round(rect.height * self._scale * inhale))
        bob = int(round(-self._bob * inhale))
        sway = int(round(self._sway * math.sin(phase)))
        animated = pygame.Rect(
            rect.x - grow_w // 2 + sway,
            rect.y - grow_h + bob,
            rect.width + grow_w,
            rect.height + grow_h,
        )
        blit_fitted(surface, comp, animated, flip=flip, alpha=alpha)
