"""Camera + screen-effect primitives for the dialogue presentation layer.

These are small per-frame state machines, modelled on
:class:`world_gal_game.ui.transitions.FadeTransition`: each exposes
``update(dt)`` (advance an internal clock), a ``done`` flag, and a way to
apply itself to the frame. They are pure presentation — no game logic — and
are driven by :class:`~world_gal_game.scenes.dialogue_scene.DialogueScene`,
which spawns them in response to directives queued by the ``camera_*`` /
``screen_*`` builtin effects (see ``plugins/builtin_effects.py``).

The split mirrors the rest of the engine: effect *handlers* run inside
``GameState.apply`` and only record intent (they never touch pygame); the
scene reads that intent and constructs the matching object from this module.

- :class:`Camera` — animated zoom + pan, applied to the background / CG blit.
- :class:`ScreenShake` — a decaying positional jitter applied to the whole
  frame for ``duration`` seconds.
- :class:`ScreenFlash` — a colour overlay whose alpha fades from full to zero
  over ``duration`` seconds (a quick "impact" pop).
- :class:`ColorTint` — a colour overlay that fades *in* over ``duration`` and
  then persists, or (``duration <= 0``) appears instantly and persists, until
  it is cleared / replaced.

All clocks clamp to ``duration`` so a frame-rate spike never produces garbage,
exactly like the transition classes.
"""
from __future__ import annotations

import math

import pygame

from .easing import resolve


def _clamp01(t: float) -> float:
    if t < 0.0:
        return 0.0
    if t > 1.0:
        return 1.0
    return t


def _coerce_color(color, default=(0, 0, 0)) -> tuple[int, int, int]:
    """Best-effort turn a directive's ``color`` into an (r, g, b) tuple.

    Accepts a 3- or 4-tuple/list (alpha dropped — these classes own alpha) or
    falls back to ``default`` for anything malformed, so a bad pack value can
    never raise from the render path.
    """
    try:
        r, g, b = int(color[0]), int(color[1]), int(color[2])
        return (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))
    except Exception:
        return default


class Camera:
    """Animated 2-D camera: zoom + pan, applied to a full-screen surface.

    The camera tweens from its current ``(zoom, pan_x, pan_y)`` to a target
    over ``duration`` seconds using ``easing``. ``apply(src)`` returns a
    ``(surface, topleft)`` pair: the source scaled by the live zoom and offset
    so the requested pan/zoom is visible. A neutral camera (zoom 1, no pan,
    nothing animating) returns the source untouched at ``(0, 0)`` so the
    historical no-effect path is byte-identical.

    Zoom is anchored at the screen centre (zooming in keeps the middle put);
    pan is measured in *source pixels* and shifts what part of the (possibly
    scaled) image lands on screen.
    """

    def __init__(self, *, zoom: float = 1.0, pan_x: float = 0.0,
                 pan_y: float = 0.0) -> None:
        self.zoom = max(0.01, float(zoom))
        self.pan_x = float(pan_x)
        self.pan_y = float(pan_y)
        # Animation endpoints; when no animation is running start == end.
        self._from = (self.zoom, self.pan_x, self.pan_y)
        self._to = (self.zoom, self.pan_x, self.pan_y)
        self.duration = 0.0
        self.t = 0.0
        self._ease = resolve(None)

    # -- intent --------------------------------------------------------------

    def pan_to(self, x: float, y: float, *, duration: float = 0.5,
               easing=None) -> None:
        self._start((self._to[0], float(x), float(y)), duration, easing)

    def zoom_to(self, scale: float, *, duration: float = 0.5,
                easing=None) -> None:
        self._start((max(0.01, float(scale)), self._to[1], self._to[2]),
                    duration, easing)

    def reset(self, *, duration: float = 0.0, easing=None) -> None:
        """Return to neutral (zoom 1, no pan)."""
        self._start((1.0, 0.0, 0.0), duration, easing)

    def _start(self, target: tuple[float, float, float], duration: float,
               easing) -> None:
        # Begin the tween from wherever we currently are (mid-animation safe).
        self._from = (self.zoom, self.pan_x, self.pan_y)
        self._to = target
        self.duration = max(0.0, float(duration))
        self.t = 0.0
        self._ease = resolve(easing)
        if self.duration <= 0.0:
            # Instant: snap and mark complete.
            self.zoom, self.pan_x, self.pan_y = target
            self._from = target

    # -- per-frame -----------------------------------------------------------

    @property
    def done(self) -> bool:
        return self.t >= self.duration

    @property
    def is_neutral(self) -> bool:
        """True when there is nothing to apply (and nothing animating)."""
        return (self.done and abs(self.zoom - 1.0) < 1e-4
                and abs(self.pan_x) < 1e-4 and abs(self.pan_y) < 1e-4)

    def update(self, dt: float) -> None:
        if self.done:
            return
        self.t = min(self.t + dt, self.duration)
        p = self._ease(self.t / self.duration) if self.duration > 0 else 1.0
        fz, fx, fy = self._from
        tz, tx, ty = self._to
        self.zoom = fz + (tz - fz) * p
        self.pan_x = fx + (tx - fx) * p
        self.pan_y = fy + (ty - fy) * p

    def apply(self, src: pygame.Surface) -> tuple[pygame.Surface, tuple[int, int]]:
        """Return ``(surface_to_blit, topleft)`` for the current transform.

        Neutral camera → the source as-is at ``(0, 0)``. Otherwise the source
        is scaled by ``zoom`` and offset so the centre stays anchored, then the
        pan shifts it.
        """
        if self.is_neutral:
            return src, (0, 0)
        sw, sh = src.get_size()
        z = self.zoom
        new_size = (max(1, int(sw * z)), max(1, int(sh * z)))
        scaled = pygame.transform.smoothscale(src, new_size)
        # Keep the centre anchored when zooming: the scaled image grows about
        # its middle, so shift back by half the size delta. Pan is then layered
        # on top (in source px, scaled to match).
        ox = -int((new_size[0] - sw) / 2) + int(self.pan_x * z)
        oy = -int((new_size[1] - sh) / 2) + int(self.pan_y * z)
        return scaled, (ox, oy)


class ScreenShake:
    """Decaying positional jitter applied to the whole frame.

    For ``duration`` seconds, :meth:`offset` returns an ``(dx, dy)`` whose
    magnitude starts at ``intensity`` pixels and decays to zero. The caller
    blits the frame shifted by that offset (and fills any exposed border).
    Deterministic given ``(intensity, duration)`` so tests can assert on it.
    """

    def __init__(self, *, intensity: float = 12.0, duration: float = 0.4,
                 easing=None) -> None:
        self.intensity = max(0.0, float(intensity))
        self.duration = max(0.01, float(duration))
        self.t = 0.0
        # out_cubic by default → a sharp hit that quickly settles.
        self._ease = resolve(easing or "out_cubic")

    def update(self, dt: float) -> None:
        self.t = min(self.t + dt, self.duration)

    @property
    def done(self) -> bool:
        return self.t >= self.duration

    def offset(self) -> tuple[int, int]:
        if self.done:
            return (0, 0)
        # Remaining strength: 1 at t=0 → 0 at t=duration.
        decay = 1.0 - self._ease(self.t / self.duration)
        amp = self.intensity * decay
        # High-frequency oscillation on each axis, phase-shifted so x/y differ.
        dx = math.sin(self.t * 53.0) * amp
        dy = math.cos(self.t * 47.0) * amp
        return (int(round(dx)), int(round(dy)))


class ScreenFlash:
    """A colour overlay whose alpha fades from full to zero over ``duration``.

    Models a camera flash / impact pop. ``draw(surface)`` blits the overlay at
    the current alpha; once :attr:`done` the caller drops it.
    """

    def __init__(self, *, color=(255, 255, 255), duration: float = 0.3,
                 max_alpha: int = 255, easing=None) -> None:
        self.color = _coerce_color(color, default=(255, 255, 255))
        self.duration = max(0.01, float(duration))
        self.max_alpha = max(0, min(255, int(max_alpha)))
        self.t = 0.0
        self._ease = resolve(easing)

    def update(self, dt: float) -> None:
        self.t = min(self.t + dt, self.duration)

    @property
    def done(self) -> bool:
        return self.t >= self.duration

    def alpha(self) -> int:
        progress = self._ease(self.t / self.duration)
        return int(self.max_alpha * (1.0 - _clamp01(progress)))

    def draw(self, surface: pygame.Surface) -> None:
        a = self.alpha()
        if a <= 0:
            return
        overlay = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        overlay.fill((*self.color, a))
        surface.blit(overlay, (0, 0))


class ColorTint:
    """A persistent colour overlay that fades in, then holds.

    With ``duration > 0`` the tint ramps from alpha 0 to ``max_alpha`` over
    that time and then stays put (``done`` reports the *fade-in* is finished,
    not that the tint should be removed). With ``duration <= 0`` it appears at
    full ``max_alpha`` immediately. The owning scene keeps a single active tint
    and replaces / clears it explicitly; a tint never expires on its own.
    """

    def __init__(self, *, color=(0, 0, 0), duration: float = 0.5,
                 max_alpha: int = 120, easing=None) -> None:
        self.color = _coerce_color(color, default=(0, 0, 0))
        self.duration = max(0.0, float(duration))
        self.max_alpha = max(0, min(255, int(max_alpha)))
        self.t = 0.0
        self._ease = resolve(easing)
        if self.duration <= 0.0:
            self.t = 0.0   # fully applied immediately (see alpha()).

    def update(self, dt: float) -> None:
        if self.duration <= 0.0:
            return
        self.t = min(self.t + dt, self.duration)

    @property
    def done(self) -> bool:
        """True once the fade-in has completed (the tint then persists)."""
        if self.duration <= 0.0:
            return True
        return self.t >= self.duration

    def alpha(self) -> int:
        if self.duration <= 0.0:
            return self.max_alpha
        progress = self._ease(self.t / self.duration)
        return int(self.max_alpha * _clamp01(progress))

    def draw(self, surface: pygame.Surface) -> None:
        a = self.alpha()
        if a <= 0:
            return
        overlay = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        overlay.fill((*self.color, a))
        surface.blit(overlay, (0, 0))
