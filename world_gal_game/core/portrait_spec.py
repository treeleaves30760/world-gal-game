"""Structured portrait reference used in dialogue lines.

Resolves to a file path via pack convention:
  assets/characters/<character>/<expression>_<pose>_<outfit>.png

Falls back step-by-step when a more specific file is missing.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

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
    # Optional staging fields. All default to neutral so legacy specs render
    # exactly as before (no offset, full size, no flip, no enter/exit anim).
    offset: tuple[int, int] = (0, 0)   # pixel nudge from the slot anchor
    scale: float = 1.0                 # size multiplier on the slot rect
    flip: bool = False                 # mirror horizontally
    enter: str | None = None           # entry animation (see PORTRAIT_ANIMATIONS)
    exit: str | None = None            # exit animation (see PORTRAIT_ANIMATIONS)

    def candidate_paths(self) -> list[str]:
        """Return paths to try, most specific first."""
        c = self.character
        e = self.expression
        p = self.pose
        o = self.outfit
        base = f"assets/characters/{c}"
        return [
            f"{base}/{e}_{p}_{o}.png",
            f"{base}/{e}_{p}.png",
            f"{base}/{e}.png",
            f"{base}/{c}.png",
        ]
