"""Pack-authored metadata for the Music Room.

Persistent progress remains a set of canonical asset paths in
:class:`~world_gal_game.core.music_room.MusicRoom`.  Human-facing titles,
credits, usage notes, ordering, and spoiler visibility live in the optional
``content/music_room.yaml`` manifest instead::

    entries:
      - asset: assets/bgm/library_theme.ogg
        title: "The Book on the Highest Shelf"
        artist: "Composer Name"
        usage: "Library · Common route"
        order: 110
        hidden: false

When the manifest is absent, the runtime retains legacy directory discovery
and filename-derived labels.  This lets existing packs adopt the catalog
without migrating saves or changing their unlock identities.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import re
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    TypeAdapter,
    field_validator,
    model_validator,
)

from .music_room import canonical_music_path


_AUDIO_EXTENSIONS = frozenset({".ogg", ".mp3", ".wav"})
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:/")
_MAX_MUSIC_ROOM_ENTRIES = 4096
_MAX_MUSIC_ROOM_BYTES = 2 * 1024 * 1024
_LOC_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class MusicRoomEntrySpec(BaseModel):
    """One authored player-facing music catalog entry."""

    model_config = ConfigDict(extra="forbid")

    asset: str = Field(
        min_length=1,
        pattern=r"\S",
        description="Pack-relative BGM path and canonical unlock key.",
    )
    loc_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$",
        description=(
            "Optional stable localization key segment; asset digest fallback "
            "keeps older catalogs valid."
        ),
    )
    title: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"\S",
        description="Human-readable title shown after unlock.",
    )
    artist: str = Field(
        default="",
        max_length=160,
        description="Optional player-facing composer or performer credit.",
    )
    usage: str = Field(
        default="",
        max_length=240,
        description="Optional short story context shown after unlock.",
    )
    order: int = Field(
        default=0,
        description="Music Room sort key; ties retain declaration order.",
    )
    hidden: bool = Field(
        default=False,
        description=(
            "Hide this entry and exclude it from the visible total until heard."
        ),
    )

    @field_validator("asset")
    @classmethod
    def _required_asset(cls, value: str) -> str:
        raw = value.strip().replace("\\", "/")
        if (
            not raw
            or raw.startswith("/")
            or raw.startswith("//")
            or _WINDOWS_ABSOLUTE_RE.match(raw)
            or ".." in raw.split("/")
            or any(ord(character) < 32 for character in raw)
        ):
            raise ValueError("must be a safe pack-relative asset path")
        normalized = canonical_music_path(raw)
        if Path(normalized).suffix.lower() not in _AUDIO_EXTENSIONS:
            raise ValueError("must use a supported .ogg, .mp3, or .wav file")
        return normalized

    @field_validator("title")
    @classmethod
    def _required_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("artist", "usage")
    @classmethod
    def _trim_optional_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("loc_id")
    @classmethod
    def _trim_loc_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value != value.strip() or _LOC_ID_RE.fullmatch(value) is None:
            raise ValueError("must be a stable 1-64 character ASCII id")
        return value


class MusicRoomManifest(BaseModel):
    """Optional static roster and metadata for a pack's Music Room."""

    model_config = ConfigDict(extra="forbid")

    entries: list[MusicRoomEntrySpec] = Field(
        default_factory=list,
        max_length=_MAX_MUSIC_ROOM_ENTRIES,
    )
    _authored: bool = PrivateAttr(default=True)

    @model_validator(mode="after")
    def _unique_assets(self) -> "MusicRoomManifest":
        seen: set[str] = set()
        seen_loc_ids: set[str] = set()
        for entry in self.entries:
            if entry.asset in seen:
                raise ValueError(
                    f"duplicate Music Room asset: {entry.asset!r}"
                )
            seen.add(entry.asset)
            localization_id = music_room_localization_id(entry)
            if localization_id in seen_loc_ids:
                raise ValueError(
                    "duplicate Music Room loc_id: "
                    f"{localization_id!r}"
                )
            seen_loc_ids.add(localization_id)
        return self

    @classmethod
    def from_items(cls, items: Any) -> "MusicRoomManifest":
        """Build from a list or compact ``{asset: body}`` mapping."""
        if isinstance(items, dict):
            rows = []
            for asset, body in items.items():
                if body is not None and not isinstance(body, dict):
                    raise ValueError(
                        f"Music Room mapping entry {asset!r} "
                        "must contain an object"
                    )
                rows.append({**(body or {}), "asset": asset})
        elif isinstance(items, list):
            rows = list(items)
        else:
            raise ValueError(
                "expected a list or mapping of Music Room entries, "
                f"got {type(items).__name__}"
            )
        entries = TypeAdapter(list[MusicRoomEntrySpec]).validate_python(rows)
        manifest = cls(entries=entries)
        manifest._authored = True
        return manifest

    @classmethod
    def load(cls, path: Path) -> "MusicRoomManifest":
        """Load ``content/music_room.yaml`` or mark an absent legacy catalog."""
        path = Path(path)
        if not path.exists():
            manifest = cls()
            manifest._authored = False
            return manifest
        if path.stat().st_size > _MAX_MUSIC_ROOM_BYTES:
            raise ValueError(
                "Music Room manifest exceeds the 2 MiB authoring limit"
            )
        from .localization import load_yaml_strict

        raw = load_yaml_strict(path, max_bytes=_MAX_MUSIC_ROOM_BYTES)
        if raw is None:
            return cls()
        if isinstance(raw, dict) and "entries" in raw:
            manifest = cls.model_validate(raw)
            manifest._authored = True
            return manifest
        return cls.from_items(raw)

    @property
    def authored(self) -> bool:
        return self._authored

    def ordered(self) -> list[MusicRoomEntrySpec]:
        return sorted(self.entries, key=lambda entry: entry.order)

    def by_asset(self) -> dict[str, MusicRoomEntrySpec]:
        return {entry.asset: entry for entry in self.entries}


def music_room_localization_id(entry: MusicRoomEntrySpec) -> str:
    """Return the stable message-key segment for one catalog entry."""
    if entry.loc_id is not None:
        return entry.loc_id
    digest = hashlib.sha256(entry.asset.encode("utf-8")).hexdigest()[:16]
    return f"track_{digest}"


__all__ = [
    "MusicRoomEntrySpec",
    "MusicRoomManifest",
    "music_room_localization_id",
]
