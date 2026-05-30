"""Shared helpers for ambient / weather overlay backends.

An *ambient backend* (the tenth plugin extension category, registered with
``@ambient_backend``) draws a full-screen atmospheric overlay — rain, snow,
falling petals, drifting sparkles, fireflies — above the world layer and below
the dialogue box. The dialogue scene instantiates one as ``cls(params,
screen_size)`` and calls ``update(dt)`` then ``draw(surface)`` each frame.

This module carries no pygame drawing of its own; it provides the two things a
backend needs to stay **deterministic** (so a save / replay / screenshot of the
same moment is reproducible) and **dependency-free**:

- :class:`Lcg` — a tiny self-contained linear-congruential RNG. Backends seed it
  from ``params['seed']`` instead of the global :mod:`random` (which would make
  frames irreproducible and is banned by the engine's determinism rule).
- :func:`coerce_color` — best-effort ``(r, g, b)`` from a pack value, so a bad
  colour can never raise from the render path.
- :class:`ParticleBackend` — an optional base class that owns the common
  particle lifecycle (deterministic spawn, per-frame integrate + screen wrap);
  a concrete weather subclasses it and implements only ``_spawn`` and ``_draw``.

Backends do not have to use :class:`ParticleBackend`; any object exposing
``update(dt)`` and ``draw(surface)`` is a valid ambient backend.
"""
from __future__ import annotations

from typing import Any

import pygame


class Lcg:
    """A deterministic linear-congruential generator (no global RNG).

    Numerical Recipes constants. Seeded from an int; ``next_float`` returns a
    value in ``[0, 1)``. Deliberately tiny — enough for particle scatter, not
    cryptography. Using this (rather than ``random``) keeps every frame
    reproducible for a given seed, which the engine's determinism rule requires.
    """

    __slots__ = ("_state",)

    def __init__(self, seed: int = 0) -> None:
        self._state = (int(seed) & 0xFFFFFFFF) or 0x9E3779B9

    def next_u32(self) -> int:
        self._state = (1664525 * self._state + 1013904223) & 0xFFFFFFFF
        return self._state

    def next_float(self) -> float:
        return self.next_u32() / 4294967296.0

    def uniform(self, lo: float, hi: float) -> float:
        return lo + (hi - lo) * self.next_float()


def coerce_color(color: Any, default: tuple[int, int, int] = (255, 255, 255)
                 ) -> tuple[int, int, int]:
    """Best-effort ``(r, g, b)`` from a pack value; ``default`` on anything bad."""
    try:
        r, g, b = int(color[0]), int(color[1]), int(color[2])
        return (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))
    except Exception:
        return default


def fnum(params: dict, key: str, default: float) -> float:
    """Read ``params[key]`` as a float, falling back to ``default`` on error."""
    try:
        return float(params.get(key, default))
    except Exception:
        return default


def inum(params: dict, key: str, default: int) -> int:
    try:
        return int(params.get(key, default))
    except Exception:
        return default


class ParticleBackend:
    """Base for particle-style weather: deterministic spawn + integrate + wrap.

    A subclass implements:

    - ``_spawn(self, rng, w, h) -> dict`` — one particle's initial state (any
      keys; ``x``/``y``/``vx``/``vy`` are integrated automatically if present).
    - ``_draw(self, surface, p) -> None`` — render one particle ``p``.

    and may override ``_advance(self, p, dt, w, h)`` for non-linear motion
    (the default integrates ``x += vx*dt`` / ``y += vy*dt`` and wraps particles
    that leave the screen back to the opposite edge so the field never empties).

    ``params`` keys read by the base: ``count`` (particle quantity), ``seed``
    (RNG seed), ``alpha`` (overall overlay opacity 0-255). Everything else is
    the subclass's own.
    """

    #: Subclasses set a sensible default particle count.
    default_count = 120

    def __init__(self, params: dict | None = None,
                 screen_size: tuple[int, int] = (1280, 720)) -> None:
        self.params = dict(params or {})
        self.w, self.h = screen_size
        self.alpha = max(0, min(255, inum(self.params, "alpha", 255)))
        self._rng = Lcg(inum(self.params, "seed", 1))
        count = max(0, inum(self.params, "count", self.default_count))
        self.particles: list[dict] = [
            self._spawn(self._rng, self.w, self.h) for _ in range(count)
        ]

    # -- subclass hooks ------------------------------------------------------

    def _spawn(self, rng: Lcg, w: int, h: int) -> dict:  # pragma: no cover
        raise NotImplementedError

    def _draw(self, surface: pygame.Surface, p: dict) -> None:  # pragma: no cover
        raise NotImplementedError

    def _advance(self, p: dict, dt: float, w: int, h: int) -> None:
        p["x"] = p.get("x", 0.0) + p.get("vx", 0.0) * dt
        p["y"] = p.get("y", 0.0) + p.get("vy", 0.0) * dt
        # Wrap around with a small margin so a particle re-enters smoothly.
        m = 40
        if p["x"] < -m:
            p["x"] = w + m
        elif p["x"] > w + m:
            p["x"] = -m
        if p["y"] < -m:
            p["y"] = h + m
        elif p["y"] > h + m:
            p["y"] = -m

    # -- per-frame -----------------------------------------------------------

    def update(self, dt: float) -> None:
        for p in self.particles:
            self._advance(p, dt, self.w, self.h)

    def draw(self, surface: pygame.Surface) -> None:
        if self.alpha <= 0 or not self.particles:
            return
        # Draw onto a per-frame layer so a single ``alpha`` controls the whole
        # overlay's opacity uniformly, then blit once.
        layer = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        for p in self.particles:
            try:
                self._draw(layer, p)
            except Exception:
                continue
        if self.alpha < 255:
            layer.set_alpha(self.alpha)
        surface.blit(layer, (0, 0))
