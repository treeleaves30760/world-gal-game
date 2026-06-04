"""Structured portrait reference used in dialogue lines.

Resolves to a file path via pack convention:
  assets/characters/<character>/<expression>_<pose>_<outfit>.png

Falls back step-by-step when a more specific file is missing.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Slot = Literal["left", "center", "right"]

# Legal values for PortraitSpec.enter / .exit. Single vocabulary source shared
# by the animation runner (ui/portrait_anim.py), the validator, and the
# capability manifest so they never drift. "none" means snap with no animation.
PORTRAIT_ANIMATIONS: frozenset[str] = frozenset(
    {"none", "fade", "slide_left", "slide_right", "bounce", "pop"}
)


class PortraitSpec(BaseModel):
    """A composed portrait reference.

    Resolves to a file path via pack convention:
      assets/characters/<character>/<expression>_<pose>_<outfit>.png

    Falls back step-by-step if a more specific file is missing:
      <character>/<expression>_<pose>_<outfit>.png
      <character>/<expression>_<pose>.png
      <character>/<expression>.png
      <character>.png
    """

    character: str
    expression: str = "default"
    pose: str = "stand"
    outfit: str = "default"
    slot: Slot = "center"
    # Optional explicit image path. When set it is tried *first*, before the
    # naming convention below — so a portrait whose file doesn't follow the
    # convention (e.g. a character's default ``normal.png``) can still feed a
    # render backend (breath / layered). None keeps pure convention resolution.
    image: str | None = None
    # Optional staging fields. All default to neutral so legacy specs render
    # exactly as before (no offset, full size, no flip, no enter/exit anim).
    offset: tuple[int, int] = (0, 0)   # pixel nudge from the slot anchor
    scale: float = 1.0                 # size multiplier on the slot rect
    flip: bool = False                 # mirror horizontally
    enter: str | None = None           # entry animation (see PORTRAIT_ANIMATIONS)
    exit: str | None = None            # exit animation (see PORTRAIT_ANIMATIONS)
    # Render backend: how the *resting* portrait animates once it has settled
    # (enter/exit transitions stay surface-based regardless). "static" is the
    # built-in default and means "no backend" — the engine blits the resolved
    # still, exactly as before. Other names resolve against the global
    # PortraitBackendRegistry (plugin-provided: breath / sprite / live2d / ...);
    # an unregistered name degrades gracefully back to the static blit.
    backend: str = "static"
    # Backend-specific parameters (fps, columns, blink interval, model path...).
    # Kept free-form so core stays library-agnostic — each backend reads the
    # keys it understands and ignores the rest.
    backend_args: dict[str, Any] = Field(default_factory=dict)

    def candidate_paths(self) -> list[str]:
        """Return paths to try, most specific first."""
        c = self.character
        e = self.expression
        p = self.pose
        o = self.outfit
        base = f"assets/characters/{c}"
        convention = [
            f"{base}/{e}_{p}_{o}.png",
            f"{base}/{e}_{p}.png",
            f"{base}/{e}.png",
            f"{base}/{c}.png",
        ]
        if self.image:
            return [self.image, *convention]
        return convention
