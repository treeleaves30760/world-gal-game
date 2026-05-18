"""Visual theme — colors, paddings, border radii.

Centralizes all "look and feel" constants so every widget agrees on
palette, padding, and radii. A pack can override any subset of these via
``meta.yaml`` ``theme:`` block; the defaults below are the engine's
generic look — packs are expected to override colors and accents to
match their own visual identity.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping


def _normalize_color(value: Any, default: tuple) -> tuple:
    """Accept either ``[r,g,b]`` or ``[r,g,b,a]`` (lists or tuples)."""
    if value is None:
        return default
    if not isinstance(value, (list, tuple)):
        return default
    try:
        nums = tuple(int(v) for v in value)
    except (TypeError, ValueError):
        return default
    if len(nums) in (3, 4):
        return nums
    return default


@dataclass(frozen=True)
class Theme:
    # colors (R, G, B[, A])
    bg_deep: tuple = (13, 10, 20)
    bg_panel: tuple = (20, 16, 36, 235)
    bg_overlay: tuple = (8, 4, 18, 240)

    accent: tuple = (216, 80, 143)        # sakura pink
    accent_alt: tuple = (107, 107, 255)   # spectral indigo
    accent_warm: tuple = (240, 198, 116)  # lantern amber

    text: tuple = (243, 233, 255)
    text_mute: tuple = (179, 159, 204)
    text_dim: tuple = (110, 100, 130)
    good: tuple = (110, 215, 154)
    warn: tuple = (255, 118, 118)

    border: tuple = (216, 80, 143, 130)
    border_soft: tuple = (255, 255, 255, 40)
    border_strong: tuple = (216, 80, 143, 220)

    # spacing
    pad_xs: int = 4
    pad_s: int = 8
    pad_m: int = 14
    pad_l: int = 22
    pad_xl: int = 32

    radius_s: int = 6
    radius_m: int = 10
    radius_l: int = 16

    def overridden(self, **kwargs) -> "Theme":
        """Return a copy of self with named fields replaced.

        Unknown keys are silently dropped so a pack can supply extra
        future-proofing keys without breaking the engine.
        """
        valid = {k: v for k, v in kwargs.items()
                 if k in self.__dataclass_fields__}
        if not valid:
            return self
        return replace(self, **valid)

    @classmethod
    def from_meta(cls, meta: Mapping[str, Any]) -> "Theme":
        """Construct a theme, layering ``meta['theme']`` over defaults."""
        base = cls()
        block = meta.get("theme") or {}
        if not isinstance(block, Mapping):
            return base

        color_keys = ("bg_deep", "bg_panel", "bg_overlay",
                      "accent", "accent_alt", "accent_warm",
                      "text", "text_mute", "text_dim", "good", "warn",
                      "border", "border_soft", "border_strong")
        overrides: dict[str, Any] = {}
        for k in color_keys:
            if k in block:
                overrides[k] = _normalize_color(block[k], getattr(base, k))
        for k in ("pad_xs", "pad_s", "pad_m", "pad_l", "pad_xl",
                  "radius_s", "radius_m", "radius_l"):
            if k in block:
                try:
                    overrides[k] = int(block[k])
                except (TypeError, ValueError):
                    pass
        return base.overridden(**overrides)


def default_theme() -> Theme:
    return Theme()
