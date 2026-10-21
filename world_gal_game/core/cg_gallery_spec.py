"""Pack-authored metadata for the CG gallery.

The persistent :class:`~world_gal_game.core.cg_gallery.CGGallery` deliberately
stores only asset paths that the player has seen.  Human-facing titles,
captions, ordering, and relationships belong to the pack instead, in the
optional ``content/cg_gallery.yaml`` manifest::

    entries:
      - asset: assets/cgs/library_encounter.png
        title: "The Book on the Highest Shelf"
        subtitle: "Library · First encounter"
        description: "A memory from the first afternoon on campus."
        order: 110
        character_ids: [librarian]
        chapter_id: chapter_1
        thumbnail: assets/cgs/thumbnails/library_encounter.webp
        hidden: false

The asset path remains the canonical unlock key.  This keeps old saves
compatible and lets packs add the manifest without migrating player data.
When the file is absent, the runtime retains the legacy filename-based
gallery.
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)
from .cg_gallery import canonical_cg_path

CGGalleryCharacterId = Annotated[
    str,
    StringConstraints(pattern=r"\S"),
]


class CGGalleryEntrySpec(BaseModel):
    """One authored CG gallery entry.

    ``hidden`` means that a locked entry is omitted entirely (and therefore is
    not counted in the visible total) until its asset is unlocked.  It does not
    unlock the CG and does not affect save data.
    """

    model_config = ConfigDict(extra="forbid")

    asset: str = Field(
        min_length=1,
        pattern=r"\S",
        description="Pack-relative CG path and canonical save/unlock key.",
    )
    title: str = Field(
        min_length=1,
        pattern=r"\S",
        description="Human-readable title shown once the CG is unlocked.",
    )
    subtitle: str = Field(
        default="",
        description="Short contextual line shown below the title.",
    )
    description: str = Field(
        default="",
        description="Optional longer memory caption for the fullscreen viewer.",
    )
    order: int = Field(
        default=0,
        description="Gallery sort key; ties retain declaration order.",
    )
    hidden: bool = Field(
        default=False,
        description="Hide this entry and exclude it from the total until unlocked.",
    )
    thumbnail: str | None = Field(
        default=None,
        pattern=r"\S",
        description="Optional pack-relative grid image; fullscreen still uses asset.",
    )
    character_ids: list[CGGalleryCharacterId] = Field(
        default_factory=list,
        json_schema_extra={"uniqueItems": True},
        description="Characters associated with this memory.",
    )
    chapter_id: str = Field(
        default="",
        description="Optional chapter id associated with this memory.",
    )

    @field_validator("asset")
    @classmethod
    def _required_asset(cls, value: str) -> str:
        value = canonical_cg_path(value)
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("title")
    @classmethod
    def _required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("subtitle", "description", "chapter_id")
    @classmethod
    def _trim_optional_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("thumbnail")
    @classmethod
    def _trim_thumbnail(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = canonical_cg_path(value)
        if not value:
            raise ValueError("thumbnail must not be blank")
        return value

    @field_validator("character_ids")
    @classmethod
    def _unique_character_ids(cls, values: list[str]) -> list[str]:
        trimmed = [value.strip() for value in values]
        if any(not value for value in trimmed):
            raise ValueError("character_ids must not contain blank ids")
        if len(trimmed) != len(set(trimmed)):
            raise ValueError("character_ids must not contain duplicates")
        return trimmed


class CGGalleryManifest(BaseModel):
    """Optional static roster and metadata for a pack's CG gallery."""

    model_config = ConfigDict(extra="forbid")

    entries: list[CGGalleryEntrySpec] = Field(default_factory=list)
    # A directly constructed model represents authored static content. Only
    # ``load()`` can observe that the optional file is physically absent and
    # opt into legacy directory discovery.
    _authored: bool = PrivateAttr(default=True)

    @model_validator(mode="after")
    def _unique_assets(self) -> "CGGalleryManifest":
        seen: set[str] = set()
        for entry in self.entries:
            if entry.asset in seen:
                raise ValueError(
                    f"duplicate CG gallery asset: {entry.asset!r}"
                )
            seen.add(entry.asset)
        return self

    @classmethod
    def from_items(cls, items: Any) -> "CGGalleryManifest":
        """Build from a list or ``{asset: body}`` mapping.

        The mapping form is a compact authoring convenience; both forms
        produce the same strict entry models.
        """
        if isinstance(items, dict):
            rows = []
            for asset, body in items.items():
                if body is not None and not isinstance(body, dict):
                    raise ValueError(
                        f"CG gallery mapping entry {asset!r} "
                        "must contain an object"
                    )
                rows.append({**(body or {}), "asset": asset})
        elif isinstance(items, list):
            rows = list(items)
        else:
            raise ValueError(
                "expected a list or mapping of CG gallery entries, "
                f"got {type(items).__name__}"
            )

        entries = TypeAdapter(
            list[CGGalleryEntrySpec],
        ).validate_python(rows)
        manifest = cls(entries=entries)
        manifest._authored = True
        return manifest

    @classmethod
    def load(cls, path: Path) -> "CGGalleryManifest":
        """Load ``content/cg_gallery.yaml`` or return an empty manifest."""
        path = Path(path)
        if not path.exists():
            manifest = cls()
            manifest._authored = False
            return manifest
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if raw is None:
            return cls()
        if isinstance(raw, dict) and "entries" in raw:
            manifest = cls.model_validate(raw)
            manifest._authored = True
            return manifest
        return cls.from_items(raw)

    @property
    def authored(self) -> bool:
        """Whether a manifest file/list was explicitly supplied.

        This distinguishes an absent file (legacy directory discovery) from an
        authored empty roster (show only old-save/plugin unlocked fallbacks).
        The marker is private static load context and never enters schemas or
        save data.
        """
        return self._authored

    def ordered(self) -> list[CGGalleryEntrySpec]:
        """Return entries by order, retaining declaration order for ties."""
        return sorted(self.entries, key=lambda entry: entry.order)

    def by_asset(self) -> dict[str, CGGalleryEntrySpec]:
        """Return the canonical asset-path lookup."""
        return {entry.asset: entry for entry in self.entries}


__all__ = ["CGGalleryEntrySpec", "CGGalleryManifest"]
