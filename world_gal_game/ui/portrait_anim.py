"""Per-slot portrait staging animation.

A :class:`SlotAnimation` drives one portrait slot's transition over a fixed
duration, interpolating the portrait's destination rect and alpha with an
easing curve. It generalises the older :class:`PortraitCrossfade` (which only
mixed alpha at a fixed rect) to also cover entry/exit slides, scale pops and
bounces, and moves between slot positions.

Kinds:

- ``"enter"``  — a new portrait animates *in* to ``rect`` (fade / slide_left /
  slide_right / bounce / pop). ``old`` is ignored.
- ``"exit"``   — a leaving portrait animates *out* from ``rect`` (the reverse
  of the matching enter curve). ``new`` is ignored.
- ``"move"``   — the same portrait travels from ``from_rect`` to ``rect``.
- ``"crossfade"`` (default) — ``old`` fades out and ``new`` fades in, both at
  ``rect``. This is the behaviour packs without enter/exit get.

The class is intentionally pygame-aware (it blits surfaces) but holds no game
state; the dialogue scene owns one per slot and ticks it each frame.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pygame

from .easing import resolve, EasingFn
from .layout import fit_rect


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


#: In-place portrait emotes (Phase 6 presentation layer). Unlike SlotAnimation
#: (which animates a portrait *into* / *out of* / *between* slots), an emote is a
#: short one-shot accent played on a *settled* portrait — a jump, a shake, a
#: nod, a squash-bounce — that returns the portrait to rest when it finishes.
PORTRAIT_EMOTES: tuple[str, ...] = ("jump", "shake", "nod", "bounce")


@dataclass
class PortraitEmote:
    """A one-shot in-place accent applied to a settled portrait.

    Holds a clock over ``duration`` and, at any moment, yields a
    ``(dx, dy, scale_x, scale_y)`` transform the scene applies to the slot's
    draw rect (offsets in px, scales about the rect's bottom-centre so the feet
    stay planted). The portrait itself is unchanged — the emote only nudges /
    squashes the existing render — so it composes with any portrait backend.

    Kinds (:data:`PORTRAIT_EMOTES`):

    - ``jump``   — a quick hop up and back down (one arc).
    - ``nod``    — a small bow: dip down and back up.
    - ``shake``  — a decaying horizontal jitter (a "no" / fluster).
    - ``bounce`` — a hop with an anticipatory squash on take-off and landing.

    ``intensity`` scales the motion in px (``jump``/``nod``/``shake``) and is
    clamped to something sane. Deterministic in ``t`` so a replay matches.
    """

    kind: str
    duration: float = 0.45
    intensity: float = 30.0
    t: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self.kind = self.kind if self.kind in PORTRAIT_EMOTES else "jump"
        self.duration = max(0.05, float(self.duration))
        try:
            self.intensity = float(self.intensity)
        except Exception:
            self.intensity = 30.0

    def update(self, dt: float) -> None:
        self.t = min(self.t + dt, self.duration)

    @property
    def done(self) -> bool:
        return self.t >= self.duration

    def transform(self) -> tuple[int, int, float, float]:
        """Return ``(dx, dy, scale_x, scale_y)`` for the current moment."""
        if self.done:
            return (0, 0, 1.0, 1.0)
        p = self.t / self.duration
        amp = self.intensity
        if self.kind == "jump":
            # Single arc up (negative y is up) and back: sin(pi*p) in [0,1].
            return (0, int(-amp * math.sin(math.pi * p)), 1.0, 1.0)
        if self.kind == "nod":
            # A gentle bow: dip down then return, at half the jump amplitude.
            return (0, int(amp * 0.5 * math.sin(math.pi * p)), 1.0, 1.0)
        if self.kind == "shake":
            # Decaying horizontal jitter (a few oscillations that settle).
            decay = 1.0 - p
            return (int(amp * 0.5 * math.sin(p * math.pi * 6.0) * decay),
                    0, 1.0, 1.0)
        # bounce: a plain hop. No geometric squash — stretching/squashing a
        # hand-drawn static立繪 reads as rubber (the same distortion the breath
        # backend was dropped for); keep scale at 1.0 and only translate.
        hop = math.sin(math.pi * p)
        return (0, int(-amp * hop), 1.0, 1.0)


def _lerp_rect(a: pygame.Rect, b: pygame.Rect, t: float) -> pygame.Rect:
    return pygame.Rect(
        round(_lerp(a.x, b.x, t)),
        round(_lerp(a.y, b.y, t)),
        max(1, round(_lerp(a.width, b.width, t))),
        max(1, round(_lerp(a.height, b.height, t))),
    )


# Animations that need a default easing other than the supplied one. ``bounce``
# wants an out-bounce feel; ``pop`` overshoots with out-back. The caller can
# still override via the ``easing`` argument.
_ANIM_DEFAULT_EASE = {
    "bounce": "out_bounce",
    "pop": "out_back",
    "fade": "out_quad",
    "rise": "out_cubic",
    "slide_left": "out_cubic",
    "slide_right": "out_cubic",
}


@dataclass
class SlotAnimation:
    """One slot's in-flight transition.

    ``rect`` is the resolved *target* rect (already offset/scaled by the
    caller). ``from_rect`` is only used by ``kind="move"``.
    """

    kind: str
    rect: pygame.Rect
    duration: float = 0.25
    old: pygame.Surface | None = None
    new: pygame.Surface | None = None
    anim: str | None = None            # the named curve (fade/slide_left/...)
    from_rect: pygame.Rect | None = None
    easing: str | EasingFn | None = None
    flip: bool = False                 # mirror the moving/entering surface

    t: float = field(default=0.0, init=False)
    _ease: EasingFn = field(init=False)

    def __post_init__(self) -> None:
        self.duration = max(0.01, self.duration)
        ease = self.easing
        if ease is None and self.anim in _ANIM_DEFAULT_EASE:
            ease = _ANIM_DEFAULT_EASE[self.anim]
        self._ease = resolve(ease)

    # ------------------------------------------------------------------

    def update(self, dt: float) -> None:
        # Clamp so a frame spike never pushes progress past 1.0.
        self.t = min(self.t + dt, self.duration)

    @property
    def done(self) -> bool:
        return self.t >= self.duration

    @property
    def progress(self) -> float:
        return self._ease(self.t / self.duration)

    # ------------------------------------------------------------------

    def _scaled(self, surf: pygame.Surface, size: tuple[int, int]) -> pygame.Surface:
        out = pygame.transform.smoothscale(surf, (max(1, size[0]), max(1, size[1])))
        if self.flip:
            out = pygame.transform.flip(out, True, False)
        return out

    def _blit(self, surface: pygame.Surface, src: pygame.Surface,
              rect: pygame.Rect, alpha: int) -> None:
        # ``rect`` is the animation envelope (target / popped / slid bounds).
        # Fit the portrait inside it preserving aspect, bottom-anchored, so a
        # tall portrait is never stretched into a wider slot box.
        dest = fit_rect(src.get_size(), rect)
        img = self._scaled(src, dest.size)
        if alpha < 255:
            img.set_alpha(max(0, min(255, alpha)))
        surface.blit(img, dest.topleft)

    def _entry_state(self, p: float) -> tuple[pygame.Rect, int]:
        """Return (rect, alpha) for an entering portrait at progress ``p``."""
        target = self.rect
        if self.anim in (None, "none"):
            return target, 255
        if self.anim == "fade":
            return target, int(p * 255)
        if self.anim == "rise":
            # Subtle one-time arrival: the portrait drifts up a short distance
            # into place while fading in. NOT idle breathing/scaling — it fires
            # once, only when a slot goes from empty to occupied. Gives static
            # WA2-style sprites the "the character arrives" feel.
            dy = round((1.0 - p) * 26)
            r = pygame.Rect(target.x, target.y + dy, target.width, target.height)
            return r, int(min(1.0, p * 1.4) * 255)
        if self.anim == "pop":
            # Scale from a small box up to full, centered on the target.
            scale = _lerp(0.6, 1.0, p)
            w = max(1, int(target.width * scale))
            h = max(1, int(target.height * scale))
            r = pygame.Rect(0, 0, w, h)
            r.center = target.center
            return r, int(min(1.0, p * 1.5) * 255)
        if self.anim == "bounce":
            # Drop in from above with a bounce easing on the y offset.
            start_y = target.y - target.height
            y = round(_lerp(start_y, target.y, p))
            return pygame.Rect(target.x, y, target.width, target.height), 255
        if self.anim in ("slide_left", "slide_right"):
            # slide_left = enters travelling toward the left (from the right).
            sign = 1 if self.anim == "slide_left" else -1
            start_x = target.x + sign * target.width
            x = round(_lerp(start_x, target.x, p))
            return pygame.Rect(x, target.y, target.width, target.height), int(min(1.0, p * 1.5) * 255)
        return target, 255

    def draw(self, surface: pygame.Surface) -> None:
        p = self.progress

        if self.kind == "crossfade":
            if self.old is not None and p < 1.0:
                self._blit(surface, self.old, self.rect, int((1.0 - p) * 255))
            if self.new is not None:
                self._blit(surface, self.new, self.rect, int(p * 255))
            return

        if self.kind == "move":
            src = self.new or self.old
            if src is None:
                return
            start = self.from_rect if self.from_rect is not None else self.rect
            cur = _lerp_rect(start, self.rect, p)
            self._blit(surface, src, cur, 255)
            return

        if self.kind == "exit":
            if self.old is None:
                return
            # Reverse of entry: progress runs the entry curve backwards.
            saved = self.new
            self.new = None
            rect, alpha = self._entry_state(1.0 - p)
            self.new = saved
            self._blit(surface, self.old, rect, alpha)
            return

        # Default: enter.
        if self.new is None:
            return
        rect, alpha = self._entry_state(p)
        self._blit(surface, self.new, rect, alpha)
