"""Current-pack authority for persistent CG and music unlock progress.

Save slots and ``clear_data.json`` are user-writable progress records.  They
may remember that an authored media item was seen, but they must never invent
an asset path that a gallery later hands to an image or audio decoder.  This
module keeps that boundary pure: callers build one immutable authority from
freshly loaded pack content, then rebind candidate trackers by intersection.
"""
from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
import posixpath
import re

from .cg_gallery import CGGallery
from .cg_gallery_spec import CGGalleryManifest
from .music_room import MusicRoom
from .music_room_spec import MusicRoomManifest
from .story_graph import StoryGraph


_AUDIO_SILENCE_SENTINELS = frozenset({"none", "silence", "-"})
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:/")


def canonical_pack_media_id(value: object) -> str | None:
    """Return a safe canonical pack-relative media id, or ``None``.

    This deliberately rejects traversal *before* normalization.  Otherwise a
    legacy key such as ``assets/cgs/../../outside.png`` could normalize to an
    innocent-looking relative string and accidentally match supplemental
    authority.  POSIX, UNC, and Windows-drive absolute paths are likewise not
    valid persistent gallery identities even if an old save contains them.
    """
    if not isinstance(value, str):
        return None
    raw = value.strip().replace("\\", "/")
    if (
        not raw
        or len(raw) > 1024
        or raw.startswith("/")
        or raw.startswith("//")
        or _WINDOWS_ABSOLUTE_RE.match(raw)
        or any(ord(character) < 32 for character in raw)
    ):
        return None
    parts = raw.split("/")
    if ".." in parts or (parts and ":" in parts[0]):
        return None
    normalized = posixpath.normpath(raw)
    if normalized in ("", ".", "..") or normalized.startswith("../"):
        return None
    return normalized


def _canonical_ids(values: Iterable[object]) -> frozenset[str]:
    return frozenset(
        canonical
        for value in values
        if (canonical := canonical_pack_media_id(value)) is not None
    )


def _story_effects(story: StoryGraph):
    for scene in story.scenes.values():
        yield from scene.on_end
        for line in scene.lines:
            yield from line.effects
        for choice in scene.choices:
            yield from choice.effects


@dataclass(frozen=True, slots=True)
class MediaAuthority:
    """Immutable unlock identities authorized by the current pack."""

    cg_ids: frozenset[str]
    music_ids: frozenset[str]

    def filter_cg_ids(self, values: Iterable[object]) -> set[str]:
        """Canonicalize and retain only currently authored CG identities."""
        return set(_canonical_ids(values) & self.cg_ids)

    def filter_music_ids(self, values: Iterable[object]) -> set[str]:
        """Canonicalize and retain only currently authored BGM identities."""
        return set(_canonical_ids(values) & self.music_ids)


def build_media_authority(
    authored_story: StoryGraph,
    *,
    cg_manifest: CGGalleryManifest | None = None,
    music_manifest: MusicRoomManifest | None = None,
    additional_cg_ids: Collection[str] = (),
    additional_music_ids: Collection[str] = (),
) -> MediaAuthority:
    """Build decoder authority solely from freshly loaded pack content.

    CG authority includes scene/line CG fields, builtin ``show_cg`` effect
    targets, and manifest ``asset`` identities.  A manifest thumbnail is only
    display metadata and never becomes an unlock key.  Music authority includes
    scene/line BGM fields and Music Room manifest assets.  Callers may
    supplement deterministic current-pack sources such as an ``assets/bgm``
    directory scan or initial plugin unlocks; unsafe supplemental paths are
    discarded by the same canonicalizer.
    """
    if not isinstance(authored_story, StoryGraph):
        raise TypeError("authored_story must be a StoryGraph")

    cg_values: list[object] = []
    music_values: list[object] = []
    for scene in authored_story.scenes.values():
        cg_values.append(scene.cg)
        if (
            isinstance(scene.bgm, str)
            and scene.bgm.strip().lower() not in _AUDIO_SILENCE_SENTINELS
        ):
            music_values.append(scene.bgm)
        for line in scene.lines:
            cg_values.append(line.cg)
            if (
                isinstance(line.bgm, str)
                and line.bgm.strip().lower() not in _AUDIO_SILENCE_SENTINELS
            ):
                music_values.append(line.bgm)

    for effect in _story_effects(authored_story):
        if effect.kind == "show_cg":
            cg_values.append(effect.target)

    if cg_manifest is not None:
        if not isinstance(cg_manifest, CGGalleryManifest):
            raise TypeError("cg_manifest must be a CGGalleryManifest or None")
        cg_values.extend(entry.asset for entry in cg_manifest.entries)

    if music_manifest is not None:
        if not isinstance(music_manifest, MusicRoomManifest):
            raise TypeError(
                "music_manifest must be a MusicRoomManifest or None"
            )
        music_values.extend(entry.asset for entry in music_manifest.entries)

    cg_values.extend(additional_cg_ids)
    music_values.extend(additional_music_ids)
    return MediaAuthority(
        cg_ids=_canonical_ids(cg_values),
        music_ids=_canonical_ids(music_values),
    )


def rebind_authorized_media_progress(
    saved_cg_gallery: CGGallery,
    saved_music_room: MusicRoom,
    authority: MediaAuthority,
) -> tuple[CGGallery, MusicRoom]:
    """Return deep-copied trackers restricted to current pack authority.

    Both outputs are constructed before returning and neither input is mutated,
    allowing a loader to use tuple assignment after every other candidate check
    succeeds.  Unknown, absolute, traversal, and retired ids disappear; valid
    legacy aliases such as ``assets/cgs/./memory.png`` become canonical.
    """
    if not isinstance(saved_cg_gallery, CGGallery):
        raise TypeError("saved_cg_gallery must be a CGGallery")
    if not isinstance(saved_music_room, MusicRoom):
        raise TypeError("saved_music_room must be a MusicRoom")
    if not isinstance(authority, MediaAuthority):
        raise TypeError("authority must be a MediaAuthority")

    rebound_cg = saved_cg_gallery.model_copy(deep=True)
    rebound_music = saved_music_room.model_copy(deep=True)
    rebound_cg.unlocked = authority.filter_cg_ids(saved_cg_gallery.unlocked)
    rebound_music.unlocked = authority.filter_music_ids(
        saved_music_room.unlocked,
    )
    return rebound_cg, rebound_music


def filter_authorized_cg_ids(
    values: Iterable[object],
    authored_cg_ids: Collection[str],
) -> set[str]:
    """Filter CG progress against a caller-provided current-pack roster.

    This narrow helper lets :class:`ClearData` share the exact same path and
    traversal rules without needing a complete :class:`MediaAuthority`.
    """
    authority = _canonical_ids(authored_cg_ids)
    return set(_canonical_ids(values) & authority)


def filter_authorized_music_ids(
    values: Iterable[object],
    authored_music_ids: Collection[str],
) -> set[str]:
    """Filter Music Room progress against current-pack audio authority."""
    authority = _canonical_ids(authored_music_ids)
    return set(_canonical_ids(values) & authority)


__all__ = [
    "MediaAuthority",
    "build_media_authority",
    "canonical_pack_media_id",
    "filter_authorized_cg_ids",
    "filter_authorized_music_ids",
    "rebind_authorized_media_progress",
]
