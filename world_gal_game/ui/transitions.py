"""Screen transitions: fade-in, fade-out, dissolve.

Implemented as small state machines that overlay a black (or any color)
Surface and gradually change its alpha. The App calls .update(dt) +
.draw(surface) each frame and removes the transition when .done is True.
"""
from __future__ import annotations

import pygame

from .easing import resolve
from .layout import fit_rect

# numpy is optional (not a hard dependency / may be absent under pygbag/WASM).
# Only the image-``mask`` transition needs it; without numpy that style quietly
# degrades to ``dissolve`` (see :class:`SceneTransition`).
try:  # pragma: no cover - trivial import guard
    import numpy as _np
except Exception:  # pragma: no cover
    _np = None


class FadeTransition:
    def __init__(self, *, duration: float = 0.5, color=(0, 0, 0),
                 fade_in: bool = True, on_complete=None, easing=None):
        self.duration = max(0.01, duration)
        self.color = color
        self.fade_in = fade_in   # True = black -> clear; False = clear -> black
        self.t = 0.0
        self.done = False
        self.on_complete = on_complete
        self._ease = resolve(easing)

    def update(self, dt: float) -> None:
        self.t += dt
        if self.t >= self.duration:
            self.done = True
            self.t = self.duration
            if self.on_complete is not None:
                try:
                    self.on_complete()
                finally:
                    self.on_complete = None

    def draw(self, surface: pygame.Surface) -> None:
        progress = self._ease(self.t / self.duration)
        alpha = (1 - progress) if self.fade_in else progress
        alpha = max(0.0, min(1.0, alpha))
        overlay = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        overlay.fill((*self.color, int(alpha * 255)))
        surface.blit(overlay, (0, 0))


class PortraitCrossfade:
    """Tracks old + new surfaces with an alpha mix over `duration` seconds."""

    def __init__(self, old: pygame.Surface | None, new: pygame.Surface | None,
                 duration: float = 0.25, *, easing=None) -> None:
        self.old = old
        self.new = new
        self.duration = max(0.01, duration)
        self.t = 0.0
        self._ease = resolve(easing)

    def update(self, dt: float) -> None:
        # Clamp so we never exceed duration regardless of frame-rate spikes.
        self.t = min(self.t + dt, self.duration)

    @property
    def done(self) -> bool:
        return self.t >= self.duration

    def draw(self, surface: pygame.Surface, dest_rect: pygame.Rect) -> None:
        progress = self._ease(self.t / self.duration)

        # Fit each portrait into the slot bounds preserving its aspect ratio
        # (old and new may differ in size), rather than stretching to fill.
        if self.old is not None and progress < 1.0:
            r = fit_rect(self.old.get_size(), dest_rect)
            old_copy = pygame.transform.smoothscale(self.old, r.size)
            old_copy.set_alpha(int((1.0 - progress) * 255))
            surface.blit(old_copy, r.topleft)

        if self.new is not None:
            r = fit_rect(self.new.get_size(), dest_rect)
            new_copy = pygame.transform.smoothscale(self.new, r.size)
            new_copy.set_alpha(int(progress * 255))
            surface.blit(new_copy, r.topleft)


#: Transition styles understood by :class:`SceneTransition`. Exposed so tooling
#: (capability manifest, validator, docs) can enumerate the vocabulary without
#: importing pygame-heavy internals. Directional styles encode the direction in
#: the name; ``mask`` needs a mask surface (degrades to ``dissolve`` without one
#: or without numpy).
SCENE_TRANSITION_STYLES: tuple[str, ...] = (
    "cut",
    "dissolve",
    "fade",
    "wipe_left", "wipe_right", "wipe_up", "wipe_down",
    "slide_left", "slide_right", "slide_up", "slide_down",
    "iris_in", "iris_out",
    "blinds_h", "blinds_v",
    "pixellate",
    "mask",
)


class SceneTransition:
    """Reveal a freshly-composed frame from underneath a retreating snapshot.

    This is the engine's general, Ren'Py-style transition primitive. The owning
    scene composes the *new* world frame (background + CG + portraits) onto the
    target each frame as usual; :class:`SceneTransition` then draws the *old*
    frame (a snapshot taken the instant the change was requested) on top of it,
    animating that old frame *away* according to ``style`` so the new frame is
    progressively revealed beneath.

    Holding only the ``old`` snapshot (the new frame is whatever the scene drew
    underneath) keeps the model simple and matches how VN engines work: state
    changes happen instantly; the transition just animates the hand-off from the
    previous screen to the current one.

    Styles (see :data:`SCENE_TRANSITION_STYLES`):

    - ``cut`` — instant (no overlay; ``done`` immediately).
    - ``dissolve`` — the old frame fades its alpha to zero (crossfade).
    - ``fade`` — the old frame fades to ``color`` over the first half, then the
      colour clears over the second half, revealing the new frame ("fade to
      black and back"). The canonical scene-break beat.
    - ``wipe_{left,right,up,down}`` — a hard edge sweeps across, wiping the old
      frame away toward the named direction.
    - ``slide_{left,right,up,down}`` — the old frame slides off toward the named
      direction, uncovering the new frame.
    - ``iris_{in,out}`` — a circular aperture (``out`` opens from the centre,
      ``in`` closes toward it).
    - ``blinds_{h,v}`` — horizontal / vertical bars that close over the old
      frame and reveal the new one.
    - ``pixellate`` — the old frame coarsens into blocks while fading out.
    - ``mask`` — image-driven dissolve: pixels of the old frame disappear in the
      order given by a grayscale ``mask`` surface (dark first). Needs numpy and a
      mask surface; gracefully degrades to ``dissolve`` without either.

    The clock clamps to ``duration`` exactly like the other transition classes,
    so a frame-rate spike never produces garbage.
    """

    def __init__(self, old: pygame.Surface | None, *, style: str = "dissolve",
                 duration: float = 0.6, color=(0, 0, 0),
                 mask: pygame.Surface | None = None, easing=None) -> None:
        self.old = old
        self.style = style if style in SCENE_TRANSITION_STYLES else "dissolve"
        # ``cut`` is instantaneous; everything else needs a positive clock.
        self.duration = 0.0 if self.style == "cut" else max(0.01, float(duration))
        try:
            self.color = (int(color[0]), int(color[1]), int(color[2]))
        except Exception:
            self.color = (0, 0, 0)
        self.mask = mask
        self.t = 0.0
        self._ease = resolve(easing)
        # ``mask`` silently degrades to ``dissolve`` when its prerequisites are
        # missing, so a pack that names it always renders *something* sensible.
        if self.style == "mask" and (mask is None or _np is None):
            self.style = "dissolve"

    def update(self, dt: float) -> None:
        self.t = min(self.t + dt, self.duration)

    @property
    def done(self) -> bool:
        return self.t >= self.duration

    @property
    def progress(self) -> float:
        if self.duration <= 0.0:
            return 1.0
        return self._ease(self.t / self.duration)

    # -- rendering -----------------------------------------------------------

    def draw(self, surface: pygame.Surface) -> None:
        """Overlay the retreating old frame onto ``surface`` (the live new frame).

        Any failure degrades to drawing nothing (the new frame, already on the
        target, simply shows through) rather than raising from the render path.
        """
        if self.old is None or self.style == "cut" or self.done:
            return
        try:
            self._draw(surface, self.progress)
        except Exception:
            return

    def _draw(self, surface: pygame.Surface, p: float) -> None:
        size = surface.get_size()
        old = self.old
        if old.get_size() != size:
            old = pygame.transform.smoothscale(old, size)
        style = self.style
        if style == "dissolve":
            self._draw_dissolve(surface, old, p)
        elif style == "fade":
            self._draw_fade(surface, old, p, size)
        elif style.startswith("wipe_"):
            self._draw_wipe(surface, old, p, size, style[len("wipe_"):])
        elif style.startswith("slide_"):
            self._draw_slide(surface, old, p, size, style[len("slide_"):])
        elif style.startswith("iris_"):
            self._draw_iris(surface, old, p, size, style[len("iris_"):])
        elif style.startswith("blinds_"):
            self._draw_blinds(surface, old, p, size, style[len("blinds_"):])
        elif style == "pixellate":
            self._draw_pixellate(surface, old, p, size)
        elif style == "mask":
            self._draw_mask(surface, old, p, size)

    @staticmethod
    def _draw_dissolve(surface: pygame.Surface, old: pygame.Surface,
                       p: float) -> None:
        frame = old.copy()
        frame.set_alpha(int((1.0 - p) * 255))
        surface.blit(frame, (0, 0))

    def _draw_fade(self, surface: pygame.Surface, old: pygame.Surface,
                   p: float, size: tuple[int, int]) -> None:
        # First half: old, with the colour rising over it. Second half: the new
        # frame (already on ``surface``) with the colour falling away.
        overlay = pygame.Surface(size, pygame.SRCALPHA)
        if p < 0.5:
            surface.blit(old, (0, 0))
            a = int((p / 0.5) * 255)
        else:
            a = int((1.0 - (p - 0.5) / 0.5) * 255)
        overlay.fill((*self.color, max(0, min(255, a))))
        surface.blit(overlay, (0, 0))

    @staticmethod
    def _draw_wipe(surface: pygame.Surface, old: pygame.Surface, p: float,
                   size: tuple[int, int], direction: str) -> None:
        w, h = size
        # The old frame remains only in the not-yet-wiped region; the wipe edge
        # advances toward ``direction``.
        if direction == "right":
            x = int(p * w)
            rect = pygame.Rect(x, 0, w - x, h)
        elif direction == "left":
            rect = pygame.Rect(0, 0, int((1.0 - p) * w), h)
        elif direction == "down":
            y = int(p * h)
            rect = pygame.Rect(0, y, w, h - y)
        else:  # up
            rect = pygame.Rect(0, 0, w, int((1.0 - p) * h))
        if rect.width > 0 and rect.height > 0:
            surface.blit(old, rect.topleft, rect)

    @staticmethod
    def _draw_slide(surface: pygame.Surface, old: pygame.Surface, p: float,
                    size: tuple[int, int], direction: str) -> None:
        w, h = size
        if direction == "left":
            surface.blit(old, (-int(p * w), 0))
        elif direction == "right":
            surface.blit(old, (int(p * w), 0))
        elif direction == "up":
            surface.blit(old, (0, -int(p * h)))
        else:  # down
            surface.blit(old, (0, int(p * h)))

    @staticmethod
    def _draw_iris(surface: pygame.Surface, old: pygame.Surface, p: float,
                   size: tuple[int, int], mode: str) -> None:
        w, h = size
        frame = old.copy().convert_alpha()
        mask = pygame.Surface(size, pygame.SRCALPHA)
        mask.fill((255, 255, 255, 255))
        max_r = int(((w * w + h * h) ** 0.5) / 2) + 2
        center = (w // 2, h // 2)
        # ``out``: aperture opens (hole grows) → reveal from centre outward.
        # ``in``: aperture closes (old shrinks to centre).
        radius = int((p if mode == "out" else (1.0 - p)) * max_r)
        if radius > 0:
            pygame.draw.circle(mask, (0, 0, 0, 0), center, radius)
        frame.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        surface.blit(frame, (0, 0))

    @staticmethod
    def _draw_blinds(surface: pygame.Surface, old: pygame.Surface, p: float,
                     size: tuple[int, int], axis: str) -> None:
        w, h = size
        bars = 8
        if axis == "v":
            bar_w = w / bars
            keep = int(bar_w * (1.0 - p))
            for i in range(bars):
                x = int(i * bar_w)
                if keep > 0:
                    surface.blit(old, (x, 0), pygame.Rect(x, 0, keep, h))
        else:  # horizontal bars
            bar_h = h / bars
            keep = int(bar_h * (1.0 - p))
            for i in range(bars):
                y = int(i * bar_h)
                if keep > 0:
                    surface.blit(old, (0, y), pygame.Rect(0, y, w, keep))

    @staticmethod
    def _draw_pixellate(surface: pygame.Surface, old: pygame.Surface, p: float,
                        size: tuple[int, int]) -> None:
        w, h = size
        # Coarsen by downscaling then nearest-neighbour upscaling; fade out too.
        factor = 1.0 + p * 40.0
        small = (max(1, int(w / factor)), max(1, int(h / factor)))
        down = pygame.transform.smoothscale(old, small)
        up = pygame.transform.scale(down, size)
        up.set_alpha(int((1.0 - p) * 255))
        surface.blit(up, (0, 0))

    def _draw_mask(self, surface: pygame.Surface, old: pygame.Surface, p: float,
                   size: tuple[int, int]) -> None:
        # Image dissolve: build a per-pixel alpha from the mask's brightness.
        # Old pixels survive while ``brightness >= threshold``; the threshold
        # sweeps 0→255 over the transition (dark mask regions vanish first).
        # A soft band around the threshold avoids a hard edge.
        m = self.mask
        if m.get_size() != size:
            m = pygame.transform.smoothscale(m, size)
        frame = old.copy().convert_alpha()
        bright = _np.asarray(pygame.surfarray.array3d(m)).mean(axis=2)
        threshold = p * 255.0
        soft = 32.0
        a = ((bright - (threshold - soft)) / soft).clip(0.0, 1.0)
        alpha = (a * 255.0).astype("uint8")
        existing = pygame.surfarray.pixels_alpha(frame)
        existing[:, :] = _np.minimum(existing, alpha)
        del existing  # release the surface lock before blitting
        surface.blit(frame, (0, 0))


class BackgroundFade:
    """Crossfades between two background surfaces (typically ~0.6 s)."""

    def __init__(self, old: pygame.Surface | None, new: pygame.Surface | None,
                 duration: float = 0.6, *, easing=None) -> None:
        self.old = old
        self.new = new
        self.duration = max(0.01, duration)
        self.t = 0.0
        self._ease = resolve(easing)

    def update(self, dt: float) -> None:
        self.t = min(self.t + dt, self.duration)

    @property
    def done(self) -> bool:
        return self.t >= self.duration

    def draw(self, surface: pygame.Surface) -> None:
        size = surface.get_size()
        progress = self._ease(self.t / self.duration)

        if self.old is not None and progress < 1.0:
            old_copy = pygame.transform.smoothscale(self.old, size)
            old_copy.set_alpha(int((1.0 - progress) * 255))
            surface.blit(old_copy, (0, 0))

        if self.new is not None:
            new_copy = pygame.transform.smoothscale(self.new, size)
            new_copy.set_alpha(int(progress * 255))
            surface.blit(new_copy, (0, 0))
