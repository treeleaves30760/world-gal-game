"""CG gallery: tracks which CG images the player has unlocked.

A CG (event illustration) is unlocked the first time a dialogue line that
references it is shown. The set of unlocked CG asset paths travels with the
save file so the gallery scene can render thumbnails for everything seen so
far, and silhouettes / placeholders for the rest.
"""
from __future__ import annotations

import posixpath

from pydantic import BaseModel, Field, field_serializer, field_validator


def canonical_cg_path(path: str) -> str:
    """Normalize one pack-relative CG key without touching the filesystem."""
    normalized = posixpath.normpath(path.strip().replace("\\", "/"))
    return "" if normalized == "." else normalized


class CGGallery(BaseModel):
    """Unlocked-CG record that travels with the save file."""

    # Asset paths of CGs the player has seen — stored as a list in JSON,
    # reconstructed as a set on load.
    unlocked: set[str] = Field(default_factory=set)

    model_config = {"arbitrary_types_allowed": True}

    @field_validator("unlocked", mode="before")
    @classmethod
    def _canonicalize_paths(cls, value):
        """Repair blank/space-padded keys written by older engine builds."""
        if not isinstance(value, (list, set, tuple, frozenset)):
            return value
        return {
            canonical_cg_path(path) if isinstance(path, str) else path
            for path in value
            if not isinstance(path, str) or canonical_cg_path(path)
        }

    # Pydantic v2 keeps set as set in model_dump(); we must serialise to list
    # so json.dumps works without a custom encoder.
    @field_serializer("unlocked")
    def _serialize_set(self, v: set[str]) -> list[str]:
        return sorted({
            canonical_cg_path(path)
            for path in v
            if isinstance(path, str) and canonical_cg_path(path)
        })

    def unlock(self, path: str) -> bool:
        """Record that a CG has been seen. Returns True on first encounter."""
        path = canonical_cg_path(path)
        if not path:
            return False
        is_new = path not in self.unlocked
        self.unlocked.add(path)
        return is_new

    def is_unlocked(self, path: str) -> bool:
        """Return True if this CG has been seen before."""
        path = canonical_cg_path(path)
        return bool(path) and path in self.unlocked

    @classmethod
    def model_validate(cls, obj, **kwargs):  # type: ignore[override]
        # Accept both list and set for the set field during deserialisation.
        if isinstance(obj, dict) and isinstance(obj.get("unlocked"), list):
            obj = {**obj, "unlocked": set(obj["unlocked"])}
        return super().model_validate(obj, **kwargs)
