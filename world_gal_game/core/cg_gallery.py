"""CG gallery: tracks which CG images the player has unlocked.

A CG (event illustration) is unlocked the first time a dialogue line that
references it is shown. The set of unlocked CG asset paths travels with the
save file so the gallery scene can render thumbnails for everything seen so
far, and silhouettes / placeholders for the rest.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_serializer


class CGGallery(BaseModel):
    """Unlocked-CG record that travels with the save file."""

    # Asset paths of CGs the player has seen — stored as a list in JSON,
    # reconstructed as a set on load.
    unlocked: set[str] = Field(default_factory=set)

    model_config = {"arbitrary_types_allowed": True}

    # Pydantic v2 keeps set as set in model_dump(); we must serialise to list
    # so json.dumps works without a custom encoder.
    @field_serializer("unlocked")
    def _serialize_set(self, v: set[str]) -> list[str]:
        return sorted(v)

    def unlock(self, path: str) -> bool:
        """Record that a CG has been seen. Returns True on first encounter."""
        is_new = path not in self.unlocked
        self.unlocked.add(path)
        return is_new

    def is_unlocked(self, path: str) -> bool:
        """Return True if this CG has been seen before."""
        return path in self.unlocked

    @classmethod
    def model_validate(cls, obj, **kwargs):  # type: ignore[override]
        # Accept both list and set for the set field during deserialisation.
        if isinstance(obj, dict) and isinstance(obj.get("unlocked"), list):
            obj = {**obj, "unlocked": set(obj["unlocked"])}
        return super().model_validate(obj, **kwargs)
