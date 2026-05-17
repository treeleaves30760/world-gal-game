"""Read log: tracks which dialogue lines the player has already seen.

Persisted with the save file so skip-mode can distinguish new from old text.
"""
from __future__ import annotations

from typing import Annotated
from pydantic import BaseModel, Field, field_serializer


class ReadLog(BaseModel):
    """Already-seen line record that travels with the save file."""

    # "scene_id:line_index" keys — stored as list in JSON, reconstructed as set.
    lines: set[str] = Field(default_factory=set)
    # Scene ids the player has read completely.
    scenes: set[str] = Field(default_factory=set)

    model_config = {"arbitrary_types_allowed": True}

    # Pydantic v2 keeps set as set in model_dump(); we must serialise to list
    # so json.dumps works without a custom encoder.
    @field_serializer("lines", "scenes")
    def _serialize_set(self, v: set[str]) -> list[str]:
        return sorted(v)

    def mark_line(self, scene_id: str, line_index: int) -> bool:
        """Record that a line has been seen. Returns True on first encounter."""
        key = f"{scene_id}:{line_index}"
        is_new = key not in self.lines
        self.lines.add(key)
        return is_new

    def is_read(self, scene_id: str, line_index: int) -> bool:
        """Return True if this line has been seen before."""
        return f"{scene_id}:{line_index}" in self.lines

    def mark_scene_done(self, scene_id: str) -> None:
        """Mark a whole scene as completed."""
        self.scenes.add(scene_id)

    @classmethod
    def model_validate(cls, obj, **kwargs):  # type: ignore[override]
        # Accept both list and set for the set fields during deserialisation.
        if isinstance(obj, dict):
            for key in ("lines", "scenes"):
                if key in obj and isinstance(obj[key], list):
                    obj = {**obj, key: set(obj[key])}
        return super().model_validate(obj, **kwargs)
