"""Geometry helpers shared by the portrait render paths.

Portraits are authored at their own native aspect ratio (commonly 3:4) while
each dialogue slot exposes a fixed *bounds* rectangle. Scaling the source to
fill the bounds (the historical behaviour) squashed tall portraits into the
wider slot box — characters looked vertically compressed. :func:`fit_rect`
instead returns the largest rect with the source's aspect ratio that fits
inside the bounds, horizontally centred and (by default) anchored to the
bounds' bottom edge — the visual-novel convention, so a character's feet sit
on a consistent baseline just above the dialogue box.
"""
from __future__ import annotations

import pygame


def fit_rect(src_size: tuple[int, int], bounds: pygame.Rect,
             *, anchor: str = "bottom") -> pygame.Rect:
    """Largest rect with ``src_size``'s aspect ratio fitting inside ``bounds``.

    The result is horizontally centred on ``bounds``. Its vertical placement
    follows ``anchor``: ``"bottom"`` (default), ``"center"`` or ``"top"``.
    Degenerate inputs (zero-area source or bounds) return ``bounds`` unchanged
    so callers never divide by zero.
    """
    sw, sh = src_size
    if sw <= 0 or sh <= 0 or bounds.width <= 0 or bounds.height <= 0:
        return bounds.copy()

    src_ar = sw / sh
    bnd_ar = bounds.width / bounds.height
    if src_ar > bnd_ar:
        # Source is relatively wider than the bounds -> limited by width.
        w = bounds.width
        h = max(1, round(w / src_ar))
    else:
        # Source is relatively taller than the bounds -> limited by height.
        h = bounds.height
        w = max(1, round(h * src_ar))

    x = bounds.centerx - w // 2
    if anchor == "top":
        y = bounds.top
    elif anchor == "center":
        y = bounds.centery - h // 2
    else:  # "bottom"
        y = bounds.bottom - h
    return pygame.Rect(x, y, w, h)
