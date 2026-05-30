"""ambient_weather — built-in, web-safe ambient / weather overlays.

Registers five ``@ambient_backend`` classes (rain / snow / petals / sparkles /
fireflies). Each is instantiated by the dialogue scene as
``cls(params, screen_size)`` and exposes ``update(dt)`` / ``draw(surface)``.

Every backend is deterministic — it seeds an :class:`Lcg` from ``params['seed']``
rather than the global :mod:`random`, so the same moment renders identically on a
replay or screenshot (the engine's determinism rule). All math is plain pygame,
so they run the same on desktop and web (pygbag). Everything is defensive: bad
params fall back to defaults via ``fnum`` / ``inum``, and a per-particle draw
error skips that particle instead of breaking the frame.

Common ``params`` (read by :class:`ParticleBackend`): ``count``, ``seed``,
``alpha`` (0-255 overlay opacity). Per-backend params are documented on each
class.
"""
from __future__ import annotations

import math

import pygame

from world_gal_game.plugins import ambient_backend
from world_gal_game.ui.ambient_backend import (
    ParticleBackend, Lcg, coerce_color, fnum, inum,
)


@ambient_backend("rain", description="Slanted falling rain streaks.")
class RainBackend(ParticleBackend):
    """Falling rain drawn as short slanted streaks.

    params: ``count`` (default 220), ``speed`` px/s downward (default 900),
    ``wind`` horizontal px/s (default -200), ``length`` streak px (default 18),
    ``color`` [r,g,b] (default light blue), ``alpha`` (default 160).
    """

    default_count = 220

    def __init__(self, params=None, screen_size=(1280, 720)):
        # Parse fields from the raw params BEFORE super().__init__, because the
        # base spawns particles (calling _spawn) inside its constructor and
        # _spawn reads these attributes.
        params = dict(params or {})
        params.setdefault("alpha", 160)
        self._speed = fnum(params, "speed", 900.0)
        self._wind = fnum(params, "wind", -200.0)
        self._len = fnum(params, "length", 18.0)
        self._color = coerce_color(params.get("color"), (180, 200, 230))
        super().__init__(params, screen_size)

    def _spawn(self, rng: Lcg, w: int, h: int) -> dict:
        return {"x": rng.uniform(-w * 0.3, w), "y": rng.uniform(-h, h),
                "vx": self._wind, "vy": self._speed}

    def _draw(self, surface: pygame.Surface, p: dict) -> None:
        # Streak points along the velocity direction.
        norm = max(1.0, math.hypot(self._wind, self._speed))
        dx = self._wind / norm * self._len
        dy = self._speed / norm * self._len
        start = (int(p["x"]), int(p["y"]))
        end = (int(p["x"] - dx), int(p["y"] - dy))
        pygame.draw.line(surface, (*self._color, 255), start, end, 2)


@ambient_backend("snow", description="Drifting snow flakes with sway.")
class SnowBackend(ParticleBackend):
    """Snow flakes drifting down with a gentle per-flake horizontal sway.

    params: ``count`` (default 160), ``speed`` px/s (default 90), ``sway`` px
    amplitude (default 26), ``size`` px radius (default 3), ``color`` (white),
    ``alpha`` (default 220).
    """

    default_count = 160

    def __init__(self, params=None, screen_size=(1280, 720)):
        params = dict(params or {})
        params.setdefault("alpha", 220)
        self._speed = fnum(params, "speed", 90.0)
        self._sway = fnum(params, "sway", 26.0)
        self._size = fnum(params, "size", 3.0)
        self._color = coerce_color(params.get("color"), (255, 255, 255))
        super().__init__(params, screen_size)

    def _spawn(self, rng: Lcg, w: int, h: int) -> dict:
        return {"x": rng.uniform(0, w), "y": rng.uniform(-h, h),
                "vx": 0.0, "vy": self._speed * rng.uniform(0.6, 1.2),
                "phase": rng.uniform(0.0, math.tau),
                "freq": rng.uniform(0.5, 1.4),
                "r": self._size * rng.uniform(0.6, 1.4)}

    def _advance(self, p: dict, dt: float, w: int, h: int) -> None:
        p["phase"] += p["freq"] * dt
        p["x"] += math.cos(p["phase"]) * self._sway * dt
        p["y"] += p["vy"] * dt
        if p["y"] > h + 10:
            p["y"] = -10

    def _draw(self, surface: pygame.Surface, p: dict) -> None:
        pygame.draw.circle(surface, (*self._color, 255),
                           (int(p["x"]), int(p["y"])), max(1, int(p["r"])))


@ambient_backend("petals", description="Sakura petals tumbling on the wind.")
class PetalsBackend(ParticleBackend):
    """Tumbling petals: drift down-and-sideways while rotating.

    params: ``count`` (default 80), ``speed`` px/s (default 70), ``wind`` px/s
    (default 60), ``size`` px (default 10), ``color`` (default pink),
    ``alpha`` (default 210).
    """

    default_count = 80

    def __init__(self, params=None, screen_size=(1280, 720)):
        params = dict(params or {})
        params.setdefault("alpha", 210)
        self._speed = fnum(params, "speed", 70.0)
        self._wind = fnum(params, "wind", 60.0)
        self._size = fnum(params, "size", 10.0)
        self._color = coerce_color(params.get("color"), (250, 190, 210))
        super().__init__(params, screen_size)

    def _spawn(self, rng: Lcg, w: int, h: int) -> dict:
        return {"x": rng.uniform(0, w), "y": rng.uniform(-h, h),
                "vy": self._speed * rng.uniform(0.7, 1.3),
                "phase": rng.uniform(0.0, math.tau),
                "spin": rng.uniform(1.0, 3.0),
                "size": self._size * rng.uniform(0.7, 1.3)}

    def _advance(self, p: dict, dt: float, w: int, h: int) -> None:
        p["phase"] += p["spin"] * dt
        # Sway sideways on the wind, sinusoidally, while falling.
        p["x"] += (self._wind + math.sin(p["phase"]) * 40.0) * dt
        p["y"] += p["vy"] * dt
        if p["y"] > h + 16:
            p["y"] = -16
        if p["x"] > w + 16:
            p["x"] = -16

    def _draw(self, surface: pygame.Surface, p: dict) -> None:
        # An ellipse whose width breathes with rotation reads as a tumbling petal.
        s = p["size"]
        wpx = max(2, int(abs(math.cos(p["phase"])) * s + 2))
        hpx = max(2, int(s))
        rect = pygame.Rect(int(p["x"]), int(p["y"]), wpx, hpx)
        pygame.draw.ellipse(surface, (*self._color, 255), rect)


@ambient_backend("sparkles", description="Twinkling motes that fade in place.")
class SparklesBackend(ParticleBackend):
    """Motes that hold position and twinkle (alpha pulses in and out).

    params: ``count`` (default 90), ``size`` px (default 3), ``speed`` twinkle
    rate (default 1.5), ``color`` (default warm white), ``alpha`` (default 255).
    """

    default_count = 90

    def __init__(self, params=None, screen_size=(1280, 720)):
        params = dict(params or {})
        self._size = fnum(params, "size", 3.0)
        self._rate = fnum(params, "speed", 1.5)
        self._color = coerce_color(params.get("color"), (255, 248, 210))
        super().__init__(params, screen_size)

    def _spawn(self, rng: Lcg, w: int, h: int) -> dict:
        return {"x": rng.uniform(0, w), "y": rng.uniform(0, h),
                "phase": rng.uniform(0.0, math.tau),
                "r": self._size * rng.uniform(0.6, 1.5)}

    def _advance(self, p: dict, dt: float, w: int, h: int) -> None:
        p["phase"] += self._rate * dt

    def _draw(self, surface: pygame.Surface, p: dict) -> None:
        a = int((0.5 + 0.5 * math.sin(p["phase"])) * 255)
        if a <= 4:
            return
        pygame.draw.circle(surface, (*self._color, a),
                           (int(p["x"]), int(p["y"])), max(1, int(p["r"])))


@ambient_backend("fireflies", description="Soft glowing dots that wander.")
class FirefliesBackend(ParticleBackend):
    """Wandering glow dots that drift slowly and pulse their brightness.

    params: ``count`` (default 40), ``speed`` wander px/s (default 30),
    ``size`` px (default 4), ``color`` (default soft green-yellow),
    ``alpha`` (default 255).
    """

    default_count = 40

    def __init__(self, params=None, screen_size=(1280, 720)):
        params = dict(params or {})
        self._speed = fnum(params, "speed", 30.0)
        self._size = fnum(params, "size", 4.0)
        self._color = coerce_color(params.get("color"), (190, 255, 150))
        super().__init__(params, screen_size)

    def _spawn(self, rng: Lcg, w: int, h: int) -> dict:
        return {"x": rng.uniform(0, w), "y": rng.uniform(0, h),
                "phase": rng.uniform(0.0, math.tau),
                "dir": rng.uniform(0.0, math.tau),
                "turn": rng.uniform(-1.5, 1.5),
                "r": self._size * rng.uniform(0.7, 1.3)}

    def _advance(self, p: dict, dt: float, w: int, h: int) -> None:
        p["phase"] += 2.0 * dt
        p["dir"] += p["turn"] * dt
        p["x"] += math.cos(p["dir"]) * self._speed * dt
        p["y"] += math.sin(p["dir"]) * self._speed * dt
        # Bounce softly off edges by reversing the heading.
        if p["x"] < 0 or p["x"] > w:
            p["dir"] = math.pi - p["dir"]
        if p["y"] < 0 or p["y"] > h:
            p["dir"] = -p["dir"]

    def _draw(self, surface: pygame.Surface, p: dict) -> None:
        glow = 0.5 + 0.5 * math.sin(p["phase"])
        a = int(glow * 255)
        r = max(1, int(p["r"]))
        # A dim halo under a brighter core reads as a soft glow.
        pygame.draw.circle(surface, (*self._color, a // 3),
                           (int(p["x"]), int(p["y"])), r * 2)
        pygame.draw.circle(surface, (*self._color, a),
                           (int(p["x"]), int(p["y"])), r)
