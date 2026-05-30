"""Movie playback: the image-sequence player + a pluggable player registry.

Phase 6 (5B) adds full-screen movie playback for openings / endings / cutscenes
without binding the engine core to a video codec:

- :class:`ImageSequencePlayer` — the built-in, web-safe player. It plays a
  folder of numbered image frames at a fixed fps. Pure pygame, zero external
  dependencies, identical on desktop and web (pygbag). The trade-off (vs. real
  video) is file size and no embedded audio track — pair it with a ``bgm``.

- A tiny **player registry** (:func:`register_movie_player` /
  :func:`resolve_movie_player`) so a *desktop-only* plugin can register a real
  video player (e.g. ``pyvidplayer2`` for ``.mp4`` / ``.webm``) under the name
  ``"video"`` without touching core. :class:`~world_gal_game.scenes.movie_scene.MoviePlayerScene`
  picks a player by name (or auto-detects: a directory → image sequence, a video
  file → ``"video"`` if a plugin registered it, else a graceful placeholder).

Every player exposes the same minimal contract:

    player.update(dt: float) -> None
    player.draw(surface: pygame.Surface) -> None
    player.done -> bool          # True once playback has finished
    player.skip() -> None        # jump to the end (the skip key calls this)

All players degrade gracefully: a missing folder / file / codec yields an empty
player that is immediately ``done`` (so the scene pops at once) rather than
raising from the render path.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import pygame

# Image extensions the sequence player will pick up from a frame folder.
_FRAME_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


class ImageSequencePlayer:
    """Play a folder of numbered image frames at a fixed fps.

    ``frames`` is a sorted list of resolvable frame path strings (the scene
    builds it from the movie directory). Frames are loaded lazily and cached via
    the asset manager, scaled to fit the screen (letterboxed, aspect preserved).
    With ``loop=True`` it never reports ``done`` on its own (the skip key ends
    it); otherwise it finishes after the last frame.
    """

    def __init__(self, frames: list[str], assets, *, fps: float = 24.0,
                 loop: bool = False, screen_size: tuple[int, int] = (1280, 720)):
        self._frames = list(frames or [])
        self._assets = assets
        self._fps = max(1.0, float(fps))
        self._loop = bool(loop)
        self._size = screen_size
        self._t = 0.0
        self._finished = not self._frames   # empty → immediately done
        self._cache: dict[int, pygame.Surface] = {}

    @property
    def frame_count(self) -> int:
        return len(self._frames)

    @property
    def _index(self) -> int:
        idx = int(self._t * self._fps)
        if self._loop and self._frames:
            return idx % len(self._frames)
        return idx

    @property
    def done(self) -> bool:
        if self._finished:
            return True
        return (not self._loop) and self._index >= len(self._frames)

    def skip(self) -> None:
        self._finished = True

    def update(self, dt: float) -> None:
        if self._finished:
            return
        self._t += max(0.0, dt)

    def _surface_for(self, idx: int) -> pygame.Surface | None:
        if idx in self._cache:
            return self._cache[idx]
        try:
            surf = self._assets.scaled(self._frames[idx], self._size,
                                       fit="contain")
        except Exception:
            surf = None
        if surf is not None:
            self._cache[idx] = surf
        return surf

    def draw(self, surface: pygame.Surface) -> None:
        if self._finished or not self._frames:
            return
        idx = self._index
        if idx >= len(self._frames):
            idx = len(self._frames) - 1
        frame = self._surface_for(idx)
        if frame is None:
            return
        # Centre the (contain-fitted) frame; the scene paints the black bars.
        rect = frame.get_rect(center=surface.get_rect().center)
        surface.blit(frame, rect.topleft)


# ---------------------------------------------------------------------------
# Player registry — lets a desktop plugin add a real video player under a name.
# ---------------------------------------------------------------------------

#: name -> factory ``(path, assets, *, fps, loop, screen_size) -> player``.
MOVIE_PLAYERS: dict[str, Callable] = {}


def register_movie_player(name: str, factory: Callable) -> None:
    """Register a movie-player factory under ``name`` (e.g. ``"video"``).

    A factory takes ``(path, assets, *, fps, loop, screen_size)`` and returns an
    object exposing ``update`` / ``draw`` / ``done`` / ``skip``. Called by a
    plugin's entry module at load time; idempotent re-registration is allowed.
    """
    MOVIE_PLAYERS[name] = factory


def resolve_movie_player(name: str) -> Callable | None:
    """Return the factory registered under ``name``, or ``None``."""
    return MOVIE_PLAYERS.get(name)


def list_movie_players() -> list[str]:
    """Registered player names (``image_sequence`` is always available)."""
    return sorted({"image_sequence", *MOVIE_PLAYERS})


def frame_paths_in(directory: str, assets) -> list[str]:
    """Resolve ``directory`` (pack-relative) to a sorted list of frame paths.

    Returns pack-relative path strings (what :class:`ImageSequencePlayer` and
    the asset manager expect). An unresolvable / empty directory yields ``[]``
    so the player finishes immediately rather than erroring.
    """
    try:
        base = assets._resolve(directory)
    except Exception:
        base = None
    if base is None or not Path(base).is_dir():
        return []
    names = sorted(p.name for p in Path(base).iterdir()
                   if p.suffix.lower() in _FRAME_EXTS)
    return [f"{directory.rstrip('/')}/{n}" for n in names]


def build_image_sequence(path: str, assets, *, fps: float = 24.0,
                         loop: bool = False,
                         screen_size: tuple[int, int] = (1280, 720)
                         ) -> ImageSequencePlayer:
    """Factory matching the registry contract for the built-in player."""
    frames = frame_paths_in(path, assets)
    return ImageSequencePlayer(frames, assets, fps=fps, loop=loop,
                               screen_size=screen_size)
