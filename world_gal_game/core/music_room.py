"""Music room: tracks which BGM tracks the player has unlocked.

A track is unlocked the first time a dialogue line (or scene) that plays it is
shown. The set of unlocked BGM asset paths travels with the save file so the
music-room scene can offer playback of every track heard so far while keeping
the rest locked.
"""
from __future__ import annotations

import posixpath

from pydantic import BaseModel, Field, field_serializer, field_validator


def canonical_music_path(path: str) -> str:
    """Normalize one pack-relative music identity without filesystem access."""
    normalized = posixpath.normpath(path.strip().replace("\\", "/"))
    return "" if normalized == "." else normalized


class MusicRoom(BaseModel):
    """Unlocked-BGM record that travels with the save file."""

    # Asset paths of BGM tracks the player has heard — stored as a list in
    # JSON, reconstructed as a set on load.
    unlocked: set[str] = Field(default_factory=set)

    model_config = {"arbitrary_types_allowed": True}

    @field_validator("unlocked", mode="before")
    @classmethod
    def _canonicalize_paths(cls, value):
        """Repair blank and non-canonical keys written by older builds."""
        if not isinstance(value, (list, set, tuple, frozenset)):
            return value
        return {
            canonical_music_path(path) if isinstance(path, str) else path
            for path in value
            if not isinstance(path, str) or canonical_music_path(path)
        }

    # Pydantic v2 keeps set as set in model_dump(); we must serialise to list
    # so json.dumps works without a custom encoder.
    @field_serializer("unlocked")
    def _serialize_set(self, v: set[str]) -> list[str]:
        return sorted({
            canonical_music_path(path)
            for path in v
            if isinstance(path, str) and canonical_music_path(path)
        })

    def unlock(self, path: str) -> bool:
        """Record that a track has been heard. Returns True on first encounter."""
        path = canonical_music_path(path)
        if not path:
            return False
        is_new = path not in self.unlocked
        self.unlocked.add(path)
        return is_new

    def is_unlocked(self, path: str) -> bool:
        """Return True if this track has been heard before."""
        path = canonical_music_path(path)
        return bool(path) and path in self.unlocked

    @classmethod
    def model_validate(cls, obj, **kwargs):  # type: ignore[override]
        # Accept both list and set for the set field during deserialisation.
        if isinstance(obj, dict) and isinstance(obj.get("unlocked"), list):
            obj = {**obj, "unlocked": set(obj["unlocked"])}
        return super().model_validate(obj, **kwargs)
