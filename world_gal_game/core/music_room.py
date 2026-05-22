"""Music room: tracks which BGM tracks the player has unlocked.

A track is unlocked the first time a dialogue line (or scene) that plays it is
shown. The set of unlocked BGM asset paths travels with the save file so the
music-room scene can offer playback of every track heard so far while keeping
the rest locked.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_serializer


class MusicRoom(BaseModel):
    """Unlocked-BGM record that travels with the save file."""

    # Asset paths of BGM tracks the player has heard — stored as a list in
    # JSON, reconstructed as a set on load.
    unlocked: set[str] = Field(default_factory=set)

    model_config = {"arbitrary_types_allowed": True}

    # Pydantic v2 keeps set as set in model_dump(); we must serialise to list
    # so json.dumps works without a custom encoder.
    @field_serializer("unlocked")
    def _serialize_set(self, v: set[str]) -> list[str]:
        return sorted(v)

    def unlock(self, path: str) -> bool:
        """Record that a track has been heard. Returns True on first encounter."""
        is_new = path not in self.unlocked
        self.unlocked.add(path)
        return is_new

    def is_unlocked(self, path: str) -> bool:
        """Return True if this track has been heard before."""
        return path in self.unlocked

    @classmethod
    def model_validate(cls, obj, **kwargs):  # type: ignore[override]
        # Accept both list and set for the set field during deserialisation.
        if isinstance(obj, dict) and isinstance(obj.get("unlocked"), list):
            obj = {**obj, "unlocked": set(obj["unlocked"])}
        return super().model_validate(obj, **kwargs)
