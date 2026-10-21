"""Asset manager: lazy-loads + caches images & sounds.

All asset paths are resolved through engine.config.resolve_asset so they
work the same in source and in a PyInstaller bundle. Missing assets fall
back to a procedurally drawn placeholder so the game keeps running while
art is still being generated.

Path conventions (in order of precedence):

1. Absolute path → used as-is.
2. Path starting with ``assets/`` (or ``./assets/``) → resolved against the
   *pack* root (set via :meth:`AssetManager.set_pack_root`). This is the
   recommended form for new packs because it keeps the pack relocatable.
3. Anything else → resolved against the project / resource root, falling
   back to legacy paths like ``games/<pack>/assets/...`` so old content
   continues to work without modification.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
from pathlib import Path
import time
from typing import Callable

import pygame

from ..config import resolve_asset
from ..core.portrait_spec import PortraitSpec
from ..core.map_system import Location as _Location


_DEFAULT_IMAGE_CACHE_BYTES = 256 * 1024 * 1024
_DEFAULT_SOUND_CACHE_BYTES = 64 * 1024 * 1024
# Authored files can race with deletion or make a native decoder reject an
# allocation. Contain only those asset-boundary failures: control-flow
# exceptions still propagate, and a genuinely exhausted process may still
# raise while constructing the fallback itself.
_ASSET_LOAD_ERRORS = (MemoryError, OSError, pygame.error)


@dataclass(frozen=True, slots=True)
class AudioPlaybackSnapshot:
    """Authored audio ownership captured before a temporary preview.

    Mixer playback positions are intentionally not exposed by pygame for every
    backend. A restore therefore keeps uninterrupted tracks running when a
    preview did not replace them. BGM/ambient restart after replacement; voice
    carries an additive logical checkpoint and resumes from frame-aligned PCM
    when possible. Legacy snapshots without it retain restart compatibility.
    """

    music: str | None
    music_volume: float
    ambient: str | None
    ambient_volume: float
    voice: str | None
    voice_volume: float
    voice_was_busy: bool
    music_revision: int = 0
    ambient_revision: int = 0
    voice_revision: int = 0
    # Additive logical checkpoint for engine-owned voice playback. Older test
    # doubles and integrations may construct snapshots without it; those keep
    # the historical restart-on-return behaviour.
    voice_playback: dict[str, object] | None = None
    voice_checkpoint_captured: bool = False


@dataclass(frozen=True, slots=True)
class SFXPlaybackOwnership:
    """Opaque ownership token for temporary one-shot sound playback.

    Unlike :class:`AudioPlaybackSnapshot`, this token does not snapshot the
    whole mixer.  It records only SFX started through :meth:`play_sound` while
    the token is active, so ending a temporary view cannot stop a live sound
    that was already playing before the view opened.
    """

    token_id: int


@dataclass(frozen=True, slots=True)
class VoicePlaybackHandle:
    """Opaque ownership token for one specific Channel-0 playback."""

    serial: int


@dataclass(frozen=True, slots=True)
class _OwnedSFXPlayback:
    channel: object
    sound: pygame.mixer.Sound
    channel_key: tuple[str, int]
    revision: int


class AssetManager:
    def __init__(
        self,
        pack_root: Path | None = None,
        *,
        max_image_cache_bytes: int = _DEFAULT_IMAGE_CACHE_BYTES,
        max_sound_cache_bytes: int = _DEFAULT_SOUND_CACHE_BYTES,
        monotonic: Callable[[], float] | None = None,
    ):
        self._images: OrderedDict[str, pygame.Surface] = OrderedDict()
        self._image_entry_bytes: dict[str, int] = {}
        self._image_cache_bytes = 0
        self._max_image_cache_bytes = max(0, int(max_image_cache_bytes))
        self._image_versions: dict[str, str] = {}
        # Monotonic invalidation token for widgets that cache a composition
        # containing loaded image pixels. Cache eviction does not change the
        # source; an explicit clear or pack-root switch does.
        self._image_cache_revision = 0
        self._sounds: OrderedDict[str, pygame.mixer.Sound] = OrderedDict()
        self._sound_entry_bytes: dict[str, int] = {}
        self._sound_cache_bytes = 0
        self._max_sound_cache_bytes = max(0, int(max_sound_cache_bytes))
        self._current_music: str | None = None
        self._music_revision: int = 0
        # Two-stream BGM crossfade: BGM plays as a Sound on one of two reserved
        # channels (1 / 2); switching tracks fades the new one in on the idle
        # channel while fading the old one out on the other, so they overlap
        # (a true crossfade, unlike the single ``mixer.music`` stream which can
        # only cut). Lazily bound; falls back to ``mixer.music`` if unavailable.
        self._bgm_channels: list = []
        self._bgm_active: int = 0
        # BGM is intentionally separate from the general Sound cache. Long
        # tracks decode to large PCM buffers; retaining every track visited in
        # a playthrough can consume hundreds of MiB. Exactly one reference per
        # crossfade channel is enough to keep active/outgoing audio alive.
        self._bgm_sound_refs: list[pygame.mixer.Sound | None] = [None, None]
        self._bgm_channel_paths: list[str | None] = [None, None]
        self._bgm_volume: float = 0.6
        self._bgm_uses_stream: bool = False   # True when on the legacy fallback
        self._pack_root: Path | None = Path(pack_root) if pack_root else None
        # Reserved voice channel (mixer Channel 0). The app reserves one
        # channel at init so auto-allocated SFX never steal it.
        self._voice_channel: pygame.mixer.Channel | None = None
        self._voice_sound_ref: pygame.mixer.Sound | None = None
        self._voice_volume: float = 1.0
        self._current_voice: str | None = None
        self._current_voice_volume: float = 1.0
        self._voice_revision: int = 0
        # pygame exposes no portable Channel seek/position API. Keep a logical
        # clock alongside each engine-owned clip; exact restore decodes a
        # frame-aligned PCM tail instead of moving voice onto mixer.music (which
        # is also the BGM fallback stream).
        self._monotonic = monotonic or time.monotonic
        self._voice_started_at: float | None = None
        self._voice_source_offset: float = 0.0
        self._voice_duration: float | None = None
        self._voice_pcm_sha256: str | None = None
        self._voice_mixer_format: tuple[int, int, int] | None = None
        self._voice_playback_serial: int = 0
        self._voice_handle: VoicePlaybackHandle | None = None
        self._last_voice_resume_outcome: str | None = None
        # Reserved ambient channel (mixer Channel 3): one looping environment
        # bed (room tone / rain / hum) under the BGM, switched per scene with a
        # fade. Carry-over by path so unchanged scenes don't restart it.
        self._ambient_channel: pygame.mixer.Channel | None = None
        self._ambient_sound_ref: pygame.mixer.Sound | None = None
        self._current_ambient: str | None = None
        self._ambient_volume: float = 0.4
        self._ambient_revision: int = 0
        # General one-shot SFX use pygame's auto-allocated channels (outside
        # the four reserved authored-audio channels). Temporary views such as
        # scene replay can own only the sounds they start and stop them on
        # teardown without disturbing SFX that predate the view.
        self._sfx_ownership_serial: int = 0
        self._sfx_playback_revision: int = 0
        self._active_sfx_ownership: list[int] = []
        self._sfx_owned_playbacks: dict[int, list[_OwnedSFXPlayback]] = {}
        self._sfx_channel_revisions: dict[tuple[str, int], int] = {}

    def set_pack_root(self, pack_root: Path | None) -> None:
        """Tell the asset manager where to resolve pack-relative paths.

        The path is interpreted as "any time someone references
        ``assets/foo.png``, that means ``<pack_root>/assets/foo.png``".
        Setting this also clears the image cache so old placeholders for
        previously-missing assets don't stick around.
        """
        new_root = Path(pack_root) if pack_root else None
        if new_root != self._pack_root:
            # Pack-relative cache keys are authored paths, not absolute paths.
            # They must never keep resolving to the previous pack's pixels or
            # PCM after a hot pack switch.
            self.stop_music(fade_ms=0)
            self.stop_ambient(fade_ms=0)
            self.stop_voice()
            self._pack_root = new_root
            self.clear_caches()
            self._bgm_sound_refs = [None, None]
            self._bgm_channel_paths = [None, None]
            self._ambient_sound_ref = None
            self._voice_sound_ref = None

    @property
    def image_cache_bytes(self) -> int:
        """Estimated bytes retained by cached pygame image surfaces."""
        return self._image_cache_bytes

    @property
    def sound_cache_bytes(self) -> int:
        """Estimated decoded PCM bytes retained by the short-sound cache."""
        return self._sound_cache_bytes

    @property
    def image_cache_entries(self) -> int:
        return len(self._images)

    @property
    def image_cache_revision(self) -> int:
        """Generation of source-image identity visible to composed UI caches."""
        return self._image_cache_revision

    @property
    def sound_cache_entries(self) -> int:
        return len(self._sounds)

    def cache_stats(self) -> dict[str, int]:
        """Return deterministic cache telemetry for debug/QA tooling."""
        bgm_bytes = self._unique_sound_bytes(self._bgm_sound_refs)
        ambient_bytes = self._unique_sound_bytes([self._ambient_sound_ref])
        voice_bytes = self._unique_sound_bytes([self._voice_sound_ref])
        retained_audio_bytes = self._unique_sound_bytes(
            [
                *self._sounds.values(),
                *self._bgm_sound_refs,
                self._ambient_sound_ref,
                self._voice_sound_ref,
            ]
        )
        return {
            "image_entries": self.image_cache_entries,
            "image_bytes": self.image_cache_bytes,
            "image_budget_bytes": self._max_image_cache_bytes,
            "sound_entries": self.sound_cache_entries,
            "sound_bytes": self.sound_cache_bytes,
            "sound_budget_bytes": self._max_sound_cache_bytes,
            "bgm_slots": sum(ref is not None for ref in self._bgm_sound_refs),
            "bgm_bytes": bgm_bytes,
            "ambient_bytes": ambient_bytes,
            "voice_bytes": voice_bytes,
            "retained_audio_bytes": retained_audio_bytes,
        }

    def clear_image_cache(self) -> None:
        self._images.clear()
        self._image_entry_bytes.clear()
        self._image_cache_bytes = 0
        self._image_versions.clear()
        self._image_cache_revision += 1

    def clear_sound_cache(self) -> None:
        self._sounds.clear()
        self._sound_entry_bytes.clear()
        self._sound_cache_bytes = 0

    def clear_caches(self) -> None:
        """Drop reusable image/short-sound entries, preserving live channels."""
        self.clear_image_cache()
        self.clear_sound_cache()

    @staticmethod
    def _surface_bytes(surface: pygame.Surface) -> int:
        return max(0, surface.get_pitch() * surface.get_height())

    def _cached_image(self, key: str) -> pygame.Surface | None:
        cached = self._images.get(key)
        if cached is not None:
            self._images.move_to_end(key)
        return cached

    def _cache_image(self, key: str, surface: pygame.Surface) -> pygame.Surface:
        rendered_bytes = self._surface_bytes(surface)
        previous = self._images.pop(key, None)
        if previous is not None:
            self._image_cache_bytes -= self._image_entry_bytes.pop(key, 0)
        if (
            self._max_image_cache_bytes <= 0
            or rendered_bytes > self._max_image_cache_bytes
        ):
            return surface
        self._images[key] = surface
        self._image_entry_bytes[key] = rendered_bytes
        self._image_cache_bytes += rendered_bytes
        while self._image_cache_bytes > self._max_image_cache_bytes:
            evicted_key, _ = self._images.popitem(last=False)
            self._image_cache_bytes -= self._image_entry_bytes.pop(
                evicted_key,
                0,
            )
        return surface

    @staticmethod
    def _decoded_sound_bytes(sound: pygame.mixer.Sound) -> int:
        init = pygame.mixer.get_init()
        if not init:
            return 0
        frequency, sample_format, channels = init
        sample_bytes = max(1, abs(int(sample_format)) // 8)
        frames = max(0, int(sound.get_length() * frequency + 0.999))
        return frames * max(1, int(channels)) * sample_bytes

    def _unique_sound_bytes(
        self,
        sounds: list[pygame.mixer.Sound | None],
    ) -> int:
        """Estimate decoded bytes without double-counting shared Sound refs."""
        total = 0
        seen: set[int] = set()
        for sound in sounds:
            if sound is None or id(sound) in seen:
                continue
            seen.add(id(sound))
            total += self._decoded_sound_bytes(sound)
        return total

    def _cached_sound(self, key: str) -> pygame.mixer.Sound | None:
        cached = self._sounds.get(key)
        if cached is not None:
            self._sounds.move_to_end(key)
        return cached

    def _cache_sound(
        self,
        key: str,
        sound: pygame.mixer.Sound,
    ) -> pygame.mixer.Sound:
        decoded_bytes = self._decoded_sound_bytes(sound)
        previous = self._sounds.pop(key, None)
        if previous is not None:
            self._sound_cache_bytes -= self._sound_entry_bytes.pop(key, 0)
        if (
            self._max_sound_cache_bytes <= 0
            or decoded_bytes > self._max_sound_cache_bytes
        ):
            return sound
        self._sounds[key] = sound
        self._sound_entry_bytes[key] = decoded_bytes
        self._sound_cache_bytes += decoded_bytes
        while self._sound_cache_bytes > self._max_sound_cache_bytes:
            evicted_key, _ = self._sounds.popitem(last=False)
            self._sound_cache_bytes -= self._sound_entry_bytes.pop(
                evicted_key,
                0,
            )
        return sound

    def _resolve(self, path: str) -> Path | None:
        """Return an absolute filesystem path for the asset, or None."""
        p = Path(path)
        if p.is_absolute():
            return p
        # Pack-relative: "assets/..." or "./assets/..."
        if self._pack_root is not None:
            parts = p.parts
            if parts and parts[0] in ("assets", "./assets"):
                return (self._pack_root / p).resolve()
            # Or "<pack-rel>/..." that happens to live inside the pack
            packed = (self._pack_root / p).resolve()
            if packed.exists():
                return packed
        # Legacy: relative to the resource root.
        return resolve_asset(path)

    @staticmethod
    def _normalized_fallback(
        size: tuple[int, int] | None,
        color: tuple,
    ) -> tuple[tuple[int, int], tuple]:
        """Return the exact placeholder inputs that affect its pixels."""
        resolved_size = size or (320, 320)
        return (
            (int(resolved_size[0]), int(resolved_size[1])),
            tuple(color[:3]),
        )

    @staticmethod
    def _fallback_image_key(
        path: str | None,
        size: tuple[int, int],
        color: tuple,
    ) -> str:
        return (
            f"__asset_fallback__::{path!r}::"
            f"{size[0]}x{size[1]}::{tuple(color[:3])!r}"
        )

    def _image_with_identity(
        self,
        path: str | None,
        *,
        fallback_size: tuple[int, int] | None,
        fallback_color: tuple,
    ) -> tuple[pygame.Surface, str, bool]:
        """Load an image and report its cache identity and source validity."""
        size, color = self._normalized_fallback(
            fallback_size,
            fallback_color,
        )
        if not path:
            return (
                self._placeholder(size, color),
                self._fallback_image_key(None, size, color),
                False,
            )

        # Successful authored assets retain the historical path identity, so
        # callers using different fallback colours still share one decode and
        # one scaled result.
        cached = self._cached_image(path)
        if cached is not None:
            return cached, path, True

        # Missing/corrupt placeholders are different render products. Their
        # resolved size and RGB tint therefore belong in cache identity rather
        # than poisoning the authored path key with whichever fallback won the
        # first lookup.
        fallback_key = self._fallback_image_key(path, size, color)
        cached_fallback = self._cached_image(fallback_key)
        if cached_fallback is not None:
            return cached_fallback, fallback_key, False

        abs_path = self._resolve(path)
        if abs_path is None or not Path(abs_path).exists():
            surface = self._placeholder(
                size,
                color,
                label=Path(path).name,
            )
            return self._cache_image(fallback_key, surface), fallback_key, False
        try:
            surface = pygame.image.load(str(abs_path))
        except _ASSET_LOAD_ERRORS:
            surface = self._placeholder(
                size,
                color,
                label=Path(path).name,
            )
            return self._cache_image(fallback_key, surface), fallback_key, False
        # convert() / convert_alpha() speed up later blits, but they need a
        # display surface. In headless rendering (screenshot tooling, visual
        # check) there is none — keep the *unconverted* loaded surface so real
        # art still renders instead of silently degrading to a placeholder.
        try:
            if surface.get_alpha() is not None:
                surface = surface.convert_alpha()
            else:
                surface = surface.convert()
        except _ASSET_LOAD_ERRORS:
            pass
        return self._cache_image(path, surface), path, True

    # ---------- images -------------------------------------------------------

    def image(self, path: str | None, *, fallback_size: tuple[int, int] | None = None,
              fallback_color: tuple = (40, 30, 60)) -> pygame.Surface:
        """Return a Surface for the given path; cached.

        If the path is None or the file is missing, return a placeholder
        surface (a tinted rectangle so layout work isn't blocked).
        """
        surface, _identity, _is_authored = self._image_with_identity(
            path,
            fallback_size=fallback_size,
            fallback_color=fallback_color,
        )
        return surface

    def has_image(self, path: str | None) -> bool:
        """True iff ``path`` resolves to an image file that actually exists.

        Unlike :meth:`image` (which returns a placeholder for a missing file),
        this just probes the filesystem. The layered portrait backend uses it to
        skip optional layers (blink / mouth) whose art isn't present, rather than
        compositing a visible placeholder over the portrait.
        """
        if not path:
            return False
        abs_path = self._resolve(path)
        return abs_path is not None and Path(abs_path).exists()

    def scaled(
        self,
        path: str | None,
        size: tuple[int, int],
        *,
        fit: str = "cover",
        fallback_color: tuple = (40, 30, 60),
        focus: tuple[float, float] = (0.5, 0.5),
        zoom: float = 1.0,
    ) -> pygame.Surface:
        """Return a scaled copy.

        ``fit`` is ``cover`` / ``contain`` / ``stretch``. For cover crops,
        ``focus`` selects the normalized source focal point that should remain
        near the destination centre, and ``zoom`` can tighten a cover crop.
        Their defaults preserve the historical centred crop byte-for-byte.
        """
        if (
            len(focus) != 2
            or any(type(value) not in (int, float) for value in focus)
            or any(not 0.0 <= float(value) <= 1.0 for value in focus)
        ):
            raise ValueError("image focus must contain two values from 0 to 1")
        normalized_focus = (float(focus[0]), float(focus[1]))
        if (
            type(zoom) not in (int, float)
            or not 1.0 <= float(zoom) <= 4.0
        ):
            raise ValueError("image zoom must be between 1 and 4")
        normalized_zoom = float(zoom)
        focus_suffix = (
            ""
            if normalized_focus == (0.5, 0.5)
            else f"::focus={normalized_focus[0]:.6f},{normalized_focus[1]:.6f}"
        )
        zoom_suffix = (
            ""
            if normalized_zoom == 1.0
            else f"::zoom={normalized_zoom:.6f}"
        )
        cache_key = (
            f"{path}::{size[0]}x{size[1]}::{fit}"
            f"{focus_suffix}{zoom_suffix}"
        )
        cached = self._cached_image(cache_key)
        if cached is not None:
            return cached
        fallback_size, normalized_fallback_color = self._normalized_fallback(
            size,
            fallback_color,
        )
        fallback_cache_key = (
            f"{cache_key}::fallback="
            f"{self._fallback_image_key(path, fallback_size, normalized_fallback_color)}"
        )
        base, source_identity, is_authored = self._image_with_identity(
            path,
            fallback_size=fallback_size,
            fallback_color=normalized_fallback_color,
        )
        resolved_cache_key = cache_key if is_authored else (
            f"{cache_key}::source={source_identity}"
        )
        cached = self._cached_image(resolved_cache_key)
        if cached is not None:
            return cached
        # A native scale failure is itself a fallback render product. Keep its
        # tint-specific identity even when the decoded source was real.
        cached_fallback = self._cached_image(fallback_cache_key)
        if cached_fallback is not None:
            return cached_fallback
        try:
            if fit == "stretch":
                scaled = pygame.transform.smoothscale(base, size)
            else:
                bw, bh = base.get_size()
                tw, th = size
                if bw == 0 or bh == 0:
                    scaled = pygame.transform.smoothscale(base, size)
                else:
                    br = bw / bh
                    tr = tw / th
                    if (fit == "cover") == (br > tr):
                        new_h = th
                        new_w = int(br * new_h)
                    else:
                        new_w = tw
                        new_h = int(new_w / br)
                    if fit == "cover" and normalized_zoom != 1.0:
                        new_w = max(1, int(round(new_w * normalized_zoom)))
                        new_h = max(1, int(round(new_h * normalized_zoom)))
                    resized = pygame.transform.smoothscale(
                        base,
                        (max(1, new_w), max(1, new_h)),
                    )
                    if fit == "cover":
                        canvas = pygame.Surface(size, pygame.SRCALPHA)
                        if normalized_focus == (0.5, 0.5):
                            offset = (
                                (tw - new_w) // 2,
                                (th - new_h) // 2,
                            )
                        else:
                            offset = (
                                -int(round(
                                    max(0, new_w - tw)
                                    * normalized_focus[0],
                                )),
                                -int(round(
                                    max(0, new_h - th)
                                    * normalized_focus[1],
                                )),
                            )
                        canvas.blit(resized, offset)
                        scaled = canvas
                    else:  # contain
                        canvas = pygame.Surface(size, pygame.SRCALPHA)
                        canvas.blit(
                            resized,
                            ((tw - new_w) // 2, (th - new_h) // 2),
                        )
                        scaled = canvas
        except _ASSET_LOAD_ERRORS:
            scaled = self._placeholder(
                fallback_size,
                normalized_fallback_color,
                label=Path(path).name if path else "",
            )
            resolved_cache_key = fallback_cache_key
        return self._cache_image(resolved_cache_key, scaled)

    def _placeholder(self, size: tuple[int, int], color: tuple,
                     label: str = "") -> pygame.Surface:
        size, color = self._normalized_fallback(size, color)
        cache_key = (
            f"__placeholder__::{size[0]}x{size[1]}::{color!r}"
        )
        if not label:
            cached = self._cached_image(cache_key)
            if cached is not None:
                return cached
        s = pygame.Surface(size, pygame.SRCALPHA)
        s.fill((*color[:3], 200))
        # diagonal stripes so it's obvious this is a placeholder
        for i in range(-size[1], size[0], 20):
            pygame.draw.line(s, (255, 255, 255, 18),
                             (i, 0), (i + size[1], size[1]), 1)
        pygame.draw.rect(s, (255, 255, 255, 70), s.get_rect(), 2,
                         border_radius=8)
        if label:
            font = pygame.font.SysFont(None, 18)
            txt = font.render(label[:24], True, (255, 255, 255, 200))
            s.blit(txt, ((size[0] - txt.get_width()) // 2,
                         (size[1] - txt.get_height()) // 2))
        if label:
            return s
        return self._cache_image(cache_key, s)

    # ---------- location background ------------------------------------------

    def location_background(self, loc: _Location, time_of_day: str,
                             size: tuple[int, int] | None = None) -> pygame.Surface | None:
        """Resolve and load the time-of-day-specific background for a location.

        Returns None when no background is defined (neither time-specific nor default).
        When size is given, returns a scaled cover-fit surface.
        """
        path = loc.background_for(time_of_day)
        if not path:
            return None
        if size is not None:
            return self.scaled(path, size, fit="cover")
        return self.image(path)

    # ---------- portrait spec ------------------------------------------------

    def resolve_portrait(self, spec: PortraitSpec,
                         fallback_size: tuple[int, int] = (320, 640)) -> pygame.Surface:
        """Walk candidate_paths until one exists; return placeholder if none found."""
        for path in spec.candidate_paths():
            abs_path = self._resolve(path)
            if abs_path is not None and Path(abs_path).exists():
                return self.image(path, fallback_size=fallback_size)
        # All candidates missing — return placeholder labelled with character name.
        return self._placeholder(fallback_size, (40, 30, 60), label=spec.character)

    # ---------- sound --------------------------------------------------------

    def sound(self, path: str | None) -> pygame.mixer.Sound | None:
        if not path:
            return None
        cached = self._cached_sound(path)
        if cached is not None:
            return cached
        abs_path = self._resolve(path)
        if abs_path is None or not Path(abs_path).exists():
            return None
        try:
            s = pygame.mixer.Sound(str(abs_path))
            return self._cache_sound(path, s)
        except _ASSET_LOAD_ERRORS:
            return None

    def play_sound(self, path: str | None, *, volume: float = 1.0) -> None:
        s = self.sound(path)
        if s is None:
            self._last_voice_resume_outcome = "unavailable"
            return
        s.set_volume(volume)
        channel = s.play()
        if channel is None:
            return
        self._sfx_playback_revision += 1
        revision = self._sfx_playback_revision
        channel_key = self._sfx_channel_key(channel)
        self._sfx_channel_revisions[channel_key] = revision
        if self._active_sfx_ownership:
            token_id = self._active_sfx_ownership[-1]
            owned = self._sfx_owned_playbacks.get(token_id)
            if owned is not None:
                owned.append(_OwnedSFXPlayback(
                    channel=channel,
                    sound=s,
                    channel_key=channel_key,
                    revision=revision,
                ))

    @staticmethod
    def _sfx_channel_key(channel: object) -> tuple[str, int]:
        """Return a stable key for a pygame channel or a test double."""
        channel_id = getattr(channel, "id", None)
        if isinstance(channel_id, int):
            return ("mixer", channel_id)
        return ("object", id(channel))

    def begin_sfx_ownership(self) -> SFXPlaybackOwnership:
        """Own one-shot SFX started until the returned token is ended.

        Ownership scopes may nest. A playback belongs to the innermost active
        scope, which keeps independent temporary views from stopping each
        other's sounds.
        """
        self._sfx_ownership_serial += 1
        token = SFXPlaybackOwnership(self._sfx_ownership_serial)
        self._active_sfx_ownership.append(token.token_id)
        self._sfx_owned_playbacks[token.token_id] = []
        return token

    def end_sfx_ownership(self, token: SFXPlaybackOwnership) -> None:
        """Stop only still-current SFX started under ``token``.

        A channel revision and Sound identity guard against stopping a newer
        playback if pygame (or an integration) reused the same channel before
        teardown. Ending a token more than once is a harmless no-op.
        """
        owned = self._sfx_owned_playbacks.pop(token.token_id, None)
        if owned is None:
            return
        try:
            self._active_sfx_ownership.remove(token.token_id)
        except ValueError:
            pass
        for playback in reversed(owned):
            if (
                self._sfx_channel_revisions.get(playback.channel_key)
                != playback.revision
            ):
                continue
            channel = playback.channel
            try:
                if not channel.get_busy():
                    continue
                get_sound = getattr(channel, "get_sound", None)
                if callable(get_sound) and get_sound() is not playback.sound:
                    continue
                channel.stop()
            except (AttributeError, pygame.error):
                continue
            if (
                self._sfx_channel_revisions.get(playback.channel_key)
                == playback.revision
            ):
                self._sfx_channel_revisions.pop(playback.channel_key, None)

    def _ensure_bgm_channels(self) -> bool:
        """Bind the two reserved BGM channels (1 / 2). Returns False when the
        mixer has no such channels (uninitialised / too few) so the caller can
        fall back to the single ``mixer.music`` stream."""
        if self._bgm_channels:
            return True
        try:
            self._bgm_channels = [pygame.mixer.Channel(1),
                                  pygame.mixer.Channel(2)]
            return True
        except pygame.error:
            self._bgm_channels = []
            return False

    def play_music(self, path: str | None, *, volume: float = 0.6,
                   loops: int = -1, fade_ms: int = 800) -> None:
        """Play looping BGM, crossfading from any current track.

        Two reserved channels let the outgoing and incoming tracks overlap (a
        real crossfade, not a cut). Degrades to the single ``mixer.music`` stream
        if those channels aren't available, and to a silent no-op on any mixer
        error or missing file — so packs without audio just stay quiet.
        """
        if path == self._current_music:
            return
        if path is None:
            self.stop_music(fade_ms=fade_ms)
            return
        abs_path = self._resolve(path)
        if abs_path is None or not Path(abs_path).exists():
            return
        if self._ensure_bgm_channels():
            out_idx = self._bgm_active
            new_idx = 1 - out_idx
            snd = None
            source_idx = None
            for idx, stored_path in enumerate(self._bgm_channel_paths):
                if stored_path == path and self._bgm_sound_refs[idx] is not None:
                    snd = self._bgm_sound_refs[idx]
                    source_idx = idx
                    break
            if snd is None:
                try:
                    snd = pygame.mixer.Sound(str(abs_path))
                except _ASSET_LOAD_ERRORS:
                    return
            try:
                new_ch = self._bgm_channels[new_idx]
                new_ch.set_volume(volume)
                # Start the replacement before touching the active channel.
                # Decode/play failures therefore leave both ownership and the
                # old track's playback position unchanged.
                new_ch.play(snd, loops=loops, fade_ms=fade_ms)
            except pygame.error:
                return
            out_ch = self._bgm_channels[out_idx]
            try:
                if out_ch.get_busy():
                    out_ch.fadeout(fade_ms)
            except pygame.error:
                pass
            if source_idx is not None and source_idx != new_idx:
                # Reusing a track retained by the other crossfade slot must
                # move its metadata instead of claiming the same Sound in
                # both slots. The pygame channel itself owns any outgoing
                # playback until its fade completes.
                self._bgm_sound_refs[source_idx] = None
                self._bgm_channel_paths[source_idx] = None
            self._bgm_sound_refs[new_idx] = snd
            self._bgm_channel_paths[new_idx] = path
            self._bgm_active = new_idx
            self._bgm_uses_stream = False
            self._bgm_volume = volume
            self._current_music = path
            self._music_revision += 1
            return
        # Fallback: single-stream music (a cut, not an overlap, but still plays).
        try:
            pygame.mixer.music.load(str(abs_path))
        except _ASSET_LOAD_ERRORS:
            return
        # A successful stream load replaces the previous decoder state even
        # when a later play call fails, so it is already an ownership change.
        self._music_revision += 1
        self._bgm_volume = volume
        self._bgm_uses_stream = True
        try:
            pygame.mixer.music.set_volume(volume)
            pygame.mixer.music.play(loops=loops, fade_ms=fade_ms)
            self._current_music = path
        except _ASSET_LOAD_ERRORS:
            self._current_music = None

    def stop_music(self, *, fade_ms: int = 800) -> None:
        """Fade out whatever BGM is playing (crossfade channels or the stream)."""
        self._music_revision += 1
        for ch in self._bgm_channels:
            try:
                if ch.get_busy():
                    ch.fadeout(fade_ms)
            except pygame.error:
                pass
        if self._bgm_uses_stream:
            try:
                pygame.mixer.music.fadeout(fade_ms)
            except pygame.error:
                pass
        self._current_music = None
        if fade_ms <= 0:
            # An immediate stop has no outgoing fade to keep alive. Releasing
            # both refs here prevents stopped tracks from retaining large PCM
            # buffers and ensures a later replay occupies only one slot.
            self._bgm_sound_refs = [None, None]
            self._bgm_channel_paths = [None, None]
            self._bgm_active = 0
            self._bgm_uses_stream = False

    def _ensure_ambient_channel(self) -> bool:
        """Bind the reserved ambient channel (3). False if unavailable."""
        if self._ambient_channel is not None:
            return True
        try:
            self._ambient_channel = pygame.mixer.Channel(3)
            return True
        except pygame.error:
            self._ambient_channel = None
            return False

    def play_ambient(self, path: str | None, *, volume: float = 0.4,
                     fade_ms: int = 1000) -> None:
        """Play a looping ambient bed on the reserved ambient channel.

        Carry-over by path: re-issuing the same ambient (the common case, since
        every line of a scene carries the scene's ambient) is a no-op, so it
        doesn't restart each line. ``None`` means "no change" (carry the current
        bed across scenes that don't declare one), NOT stop — use
        :meth:`stop_ambient` for that. Silent no-op on missing file / mixer
        error, so packs without ambient audio just stay quiet.
        """
        if path is None or path == self._current_ambient:
            return
        abs_path = self._resolve(path)
        if abs_path is None or not Path(abs_path).exists():
            return
        if not self._ensure_ambient_channel():
            return
        snd = self.sound(path)
        if snd is None:
            return
        self._ambient_volume = volume
        self._ambient_revision += 1
        try:
            ch = self._ambient_channel
            if ch.get_busy():
                ch.fadeout(fade_ms)
            ch.set_volume(volume)
            ch.play(snd, loops=-1, fade_ms=fade_ms)
            self._ambient_sound_ref = snd
            self._current_ambient = path
        except pygame.error:
            self._ambient_sound_ref = None
            self._current_ambient = None

    def stop_ambient(self, *, fade_ms: int = 1000) -> None:
        """Fade out the ambient bed."""
        self._ambient_revision += 1
        if self._ambient_channel is not None:
            try:
                if self._ambient_channel.get_busy():
                    self._ambient_channel.fadeout(fade_ms)
            except pygame.error:
                pass
        self._current_ambient = None
        if fade_ms <= 0:
            self._ambient_sound_ref = None

    def set_ambient_volume(self, volume: float) -> None:
        self._ambient_volume = volume
        if self._ambient_channel is not None:
            try:
                self._ambient_channel.set_volume(volume)
            except pygame.error:
                pass

    def set_music_volume(self, volume: float) -> None:
        """Set the live BGM volume (active crossfade channel or fallback stream);
        stored so the next track starts at this level."""
        self._bgm_volume = max(0.0, min(1.0, volume))
        try:
            if self._bgm_channels and not self._bgm_uses_stream:
                self._bgm_channels[self._bgm_active].set_volume(self._bgm_volume)
            else:
                pygame.mixer.music.set_volume(self._bgm_volume)
        except pygame.error:
            pass

    def get_music_volume(self) -> float:
        return self._bgm_volume

    def capture_audio_playback(self) -> AudioPlaybackSnapshot:
        """Capture BGM, ambient, and voice ownership for a temporary view."""
        return AudioPlaybackSnapshot(
            music=self._current_music,
            music_volume=self._bgm_volume,
            ambient=self._current_ambient,
            ambient_volume=self._ambient_volume,
            voice=self._current_voice,
            voice_volume=self._current_voice_volume,
            voice_was_busy=self.voice_busy(),
            music_revision=self._music_revision,
            ambient_revision=self._ambient_revision,
            voice_revision=self._voice_revision,
            voice_playback=self.capture_voice_playback(),
            # A real engine-started playback has a logical clock even when its
            # backend cannot expose PCM. Manually seeded/legacy integrations do
            # not; preserve their historical restart contract.
            voice_checkpoint_captured=self._voice_started_at is not None,
        )

    def restore_audio_playback(
        self,
        snapshot: AudioPlaybackSnapshot,
    ) -> None:
        """End temporary audio and return ownership to ``snapshot``.

        Tracks that were never replaced keep their playback position.  A
        replaced track is restarted without a crossfade so no preview channel
        can bleed into the restored scene.
        """
        music_changed = self._music_revision != snapshot.music_revision
        ambient_changed = (
            self._ambient_revision != snapshot.ambient_revision
        )
        voice_changed = self._voice_revision != snapshot.voice_revision

        if music_changed:
            self.stop_music(fade_ms=0)
            if snapshot.music is not None:
                self.play_music(
                    snapshot.music,
                    volume=snapshot.music_volume,
                    fade_ms=0,
                )
        else:
            self.set_music_volume(snapshot.music_volume)

        if ambient_changed:
            self.stop_ambient(fade_ms=0)
            if snapshot.ambient is not None:
                self.play_ambient(
                    snapshot.ambient,
                    volume=snapshot.ambient_volume,
                    fade_ms=0,
                )
        else:
            self.set_ambient_volume(snapshot.ambient_volume)

        if voice_changed:
            self.stop_voice()
            if snapshot.voice_was_busy and snapshot.voice is not None:
                checkpoint = snapshot.voice_playback
                if (
                    isinstance(checkpoint, dict)
                    and checkpoint.get("version") == 2
                    and checkpoint.get("state") == "playing"
                ):
                    self.resume_voice(
                        snapshot.voice,
                        position_seconds=checkpoint.get(
                            "position_seconds", 0.0),
                        duration_seconds=checkpoint.get(
                            "duration_seconds", 0.0),
                        pcm_sha256=checkpoint.get("pcm_sha256"),
                        mixer_format=checkpoint.get("mixer_format"),
                        volume=snapshot.voice_volume,
                    )
                elif (
                    checkpoint is None
                    and not snapshot.voice_checkpoint_captured
                ):
                    # Compatibility for snapshots captured before logical
                    # playback checkpoints existed.
                    self.play_voice(
                        snapshot.voice,
                        volume=snapshot.voice_volume,
                    )

    # ---------- voice --------------------------------------------------------

    def play_voice(
        self,
        path: str | None,
        *,
        volume: float = 1.0,
    ) -> VoicePlaybackHandle | None:
        """Play a per-line voice clip on the reserved mixer channel (0).

        Reuses the cached :meth:`sound` loader. A missing file or any mixer
        error is a silent no-op so packs without voiced lines just stay
        quiet. The previous voice (if any) is stopped first so a new line
        cuts the old clip.
        """
        s = self.sound(path)
        if s is None:
            return
        self._voice_revision += 1
        try:
            s.set_volume(volume)
            self._voice_channel = pygame.mixer.Channel(0)
            self._voice_channel.play(s)
            self._voice_sound_ref = s
            self._current_voice = path
            self._current_voice_volume = volume
            self._voice_started_at = self._clock_now()
            self._voice_source_offset = 0.0
            self._voice_duration = max(0.0, float(s.get_length()))
            identity = self._sound_pcm_identity(s)
            if identity is None:
                self._voice_pcm_sha256 = None
                self._voice_mixer_format = None
            else:
                self._voice_pcm_sha256, self._voice_mixer_format = identity
            self._voice_playback_serial += 1
            self._voice_handle = VoicePlaybackHandle(
                self._voice_playback_serial,
            )
            self._last_voice_resume_outcome = "started"
            return self._voice_handle
        except _ASSET_LOAD_ERRORS:
            self._clear_voice_tracking(clear_channel=True)
            self._last_voice_resume_outcome = "unavailable"
            return None

    def _clock_now(self) -> float:
        """Read the injected clock without letting a hostile clock escape."""
        try:
            value = float(self._monotonic())
        except Exception:
            return 0.0
        return value if value == value and abs(value) != float("inf") else 0.0

    def _clear_voice_tracking(self, *, clear_channel: bool = False) -> None:
        self._voice_sound_ref = None
        self._current_voice = None
        self._voice_started_at = None
        self._voice_source_offset = 0.0
        self._voice_duration = None
        self._voice_pcm_sha256 = None
        self._voice_mixer_format = None
        self._voice_handle = None
        if clear_channel:
            self._voice_channel = None

    def capture_voice_playback(
        self,
        expected_path: str | None = None,
        *,
        handle: VoicePlaybackHandle | None = None,
    ) -> dict[str, object] | None:
        """Return a JSON-native checkpoint for the engine-owned voice clip.

        ``None`` means there is no provable owned timeline. In particular, a
        manually seeded test double or an authored path that failed to load is
        not presented as resumable. Callers can distinguish this from an
        explicit completed checkpoint and preserve legacy save behaviour when
        the additive field itself is absent.
        """
        path = self._current_voice
        duration = self._voice_duration
        started_at = self._voice_started_at
        if (
            not isinstance(path, str)
            or not path
            or (expected_path is not None and path != expected_path)
            or (handle is not None and handle != self._voice_handle)
            or type(duration) not in (int, float)
            or type(started_at) not in (int, float)
            or self._voice_pcm_sha256 is None
            or self._voice_mixer_format is None
        ):
            return None
        duration_value = float(duration)
        if not 0.0 < duration_value < float("inf"):
            return None

        busy = self.voice_busy()
        if busy:
            elapsed = self._voice_source_offset + max(
                0.0,
                self._clock_now() - float(started_at),
            )
            position = min(duration_value, max(0.0, elapsed))
            state = "playing" if position < duration_value else "completed"
        else:
            # A channel that naturally became idle has consumed the authored
            # clip even if a backend stopped a few milliseconds early.
            position = duration_value
            state = "completed"
        return {
            "version": 2,
            "state": state,
            "position_seconds": position,
            "duration_seconds": duration_value,
            "pcm_sha256": self._voice_pcm_sha256,
            "mixer_format": list(self._voice_mixer_format),
            # Exact PCM-tail resume is the only truthful continuation. If a
            # backend cannot construct it, silence is less disruptive than
            # replaying already-heard dialogue from the beginning.
            "fallback": "silence",
        }

    def resume_voice(
        self,
        path: str | None,
        *,
        position_seconds: object,
        duration_seconds: object,
        pcm_sha256: object = None,
        mixer_format: object = None,
        volume: float = 1.0,
    ) -> str:
        """Resume ``path`` from a decoded, frame-aligned PCM offset.

        Returns ``"exact"`` on success, otherwise ``"silent"``. The method is
        deliberately fail-closed: new checkpoints never replay already-heard
        speech from zero when seeking is unsupported or an asset changed.
        """
        def _silent(reason: str) -> str:
            self._last_voice_resume_outcome = f"silent:{reason}"
            return "silent"

        try:
            position = float(position_seconds)
            saved_duration = float(duration_seconds)
        except (ArithmeticError, TypeError, ValueError):
            return _silent("invalid_checkpoint")
        if (
            not path
            or position < 0.0
            or saved_duration <= 0.0
            or position > saved_duration
            or position != position
            or saved_duration != saved_duration
            or position == float("inf")
            or saved_duration == float("inf")
            or not isinstance(pcm_sha256, str)
            or len(pcm_sha256) != 64
            or any(character not in "0123456789abcdef" for character in pcm_sha256)
            or not isinstance(mixer_format, (list, tuple))
            or len(mixer_format) != 3
            or any(type(value) is not int for value in mixer_format)
        ):
            return _silent("invalid_checkpoint")

        source = self.sound(path)
        if source is None:
            return _silent("asset_unavailable")
        try:
            mixer_init = pygame.mixer.get_init()
            if not mixer_init:
                return _silent("mixer_unavailable")
            frequency, sample_format, channels = mixer_init
            frequency = int(frequency)
            sample_bytes = abs(int(sample_format)) // 8
            channels = int(channels)
            if frequency <= 0 or sample_bytes <= 0 or channels <= 0:
                return _silent("mixer_format")
            current_format = (frequency, int(sample_format), channels)
            if tuple(mixer_format) != current_format:
                return _silent("mixer_changed")
            frame_bytes = sample_bytes * channels
            raw = source.get_raw()
            if hashlib.sha256(raw).hexdigest() != pcm_sha256:
                return _silent("asset_changed")
            if not raw or len(raw) % frame_bytes:
                return _silent("invalid_pcm")
            frame_count = len(raw) // frame_bytes
            if frame_count <= 0:
                return _silent("empty_pcm")
            pcm_duration = frame_count / frequency
            reported_duration = float(source.get_length())
            if (
                0.0 < reported_duration < float("inf")
                and abs(reported_duration - pcm_duration) > (1.0 / frequency)
            ):
                return _silent("invalid_pcm")
            actual_duration = pcm_duration
            # A substantially different decoded duration means the pack asset
            # changed after this save. Continuing into unrelated PCM would be
            # worse than the checkpoint's declared silent fallback. One mixer
            # frame of tolerance covers codec/float rounding only.
            if abs(actual_duration - saved_duration) > (1.0 / frequency):
                return _silent("asset_changed")
            # The authored asset may have changed since the save. The logical
            # position is still safe, but never extrapolate beyond new PCM.
            source_frame = min(
                frame_count,
                max(0, int(position * frequency)),
            )
            if source_frame >= frame_count or position >= actual_duration:
                return _silent("completed")
            tail = raw[source_frame * frame_bytes:frame_count * frame_bytes]
            resumed = pygame.mixer.Sound(buffer=tail)
            resumed.set_volume(volume)
            channel = pygame.mixer.Channel(0)
            channel.play(resumed)
        except (
            ArithmeticError,
            AttributeError,
            BufferError,
            MemoryError,
            OSError,
            OverflowError,
            TypeError,
            ValueError,
            pygame.error,
        ):
            return _silent("pcm_unsupported")

        self._voice_revision += 1
        self._voice_channel = channel
        self._voice_sound_ref = resumed
        self._current_voice = path
        self._current_voice_volume = volume
        self._voice_started_at = self._clock_now()
        self._voice_source_offset = source_frame / frequency
        self._voice_duration = actual_duration
        self._voice_pcm_sha256 = pcm_sha256
        self._voice_mixer_format = current_format
        self._voice_playback_serial += 1
        self._voice_handle = VoicePlaybackHandle(
            self._voice_playback_serial,
        )
        self._last_voice_resume_outcome = "exact"
        return "exact"

    @staticmethod
    def _sound_pcm_identity(
        sound: pygame.mixer.Sound,
    ) -> tuple[str, tuple[int, int, int]] | None:
        """Hash decoded PCM plus its interpretation format for exact resume."""
        try:
            mixer_init = pygame.mixer.get_init()
            if not mixer_init:
                return None
            frequency, sample_format, channels = (
                int(value) for value in mixer_init
            )
            sample_bytes = abs(sample_format) // 8
            if frequency <= 0 or sample_bytes <= 0 or channels <= 0:
                return None
            raw = sound.get_raw()
            frame_bytes = sample_bytes * channels
            if not raw or len(raw) % frame_bytes:
                return None
            frame_count = len(raw) // frame_bytes
            if frame_count <= 0:
                return None
            reported_duration = float(sound.get_length())
            pcm_duration = frame_count / frequency
            if (
                not 0.0 < reported_duration < float("inf")
                or abs(reported_duration - pcm_duration) > (1.0 / frequency)
            ):
                return None
            return (
                hashlib.sha256(raw).hexdigest(),
                (frequency, sample_format, channels),
            )
        except (
            AttributeError,
            BufferError,
            MemoryError,
            OSError,
            TypeError,
            ValueError,
            pygame.error,
        ):
            return None

    @property
    def current_music_path(self) -> str | None:
        """Authored path currently owned by the BGM channels or stream."""
        return self._current_music

    @property
    def current_ambient_path(self) -> str | None:
        """Authored path currently owned by the ambient channel."""
        return self._current_ambient

    @property
    def current_voice_handle(self) -> VoicePlaybackHandle | None:
        """Handle that owns the current engine-started voice, if any."""
        return self._voice_handle

    @property
    def current_voice_path(self) -> str | None:
        """Authored path owned by Channel 0, exposed without mixer internals."""
        return self._current_voice

    @property
    def last_voice_resume_outcome(self) -> str | None:
        """Diagnostic outcome for debug/QA; never drives narrative behavior."""
        return self._last_voice_resume_outcome

    def stop_voice(self) -> None:
        """Stop the current voice clip, if one is playing."""
        self._voice_revision += 1
        self._last_voice_resume_outcome = "stopped"
        if self._voice_channel is None:
            self._clear_voice_tracking()
            return
        try:
            self._voice_channel.stop()
        except pygame.error:
            pass
        self._clear_voice_tracking()

    def voice_busy(self) -> bool:
        """True while a voice clip is still playing on the reserved channel."""
        if self._voice_channel is None:
            return False
        try:
            return bool(self._voice_channel.get_busy())
        except pygame.error:
            return False
