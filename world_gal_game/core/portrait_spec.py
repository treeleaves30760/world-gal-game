"""Structured portrait reference used in dialogue lines.

Resolves to a file path via pack convention:
  assets/characters/<character>/<expression>_<pose>_<outfit>.png

Falls back step-by-step when a more specific file is missing.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Slot = Literal["left", "center", "right"]


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
