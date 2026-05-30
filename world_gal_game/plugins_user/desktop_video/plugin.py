"""desktop_video — real video playback via pyvidplayer2 (desktop only).

Registers a ``"video"`` movie player into the movie-player registry (see
:mod:`world_gal_game.ui.movie_player`) so ``MoviePlayerScene`` can play
``.mp4`` / ``.webm`` files in addition to the built-in image-sequence player.

The heavy dependency (``pyvidplayer2`` → ffmpeg) is imported **lazily**, inside
the player's constructor, so this plugin always loads cleanly — even on web or a
machine without the library. If the import fails the player reports ``done``
immediately and draws nothing, so a missing codec degrades to a skipped movie
rather than an error (the engine's "a failing plugin must not crash" rule).
"""
from __future__ import annotations

import pygame

from world_gal_game.ui.movie_player import register_movie_player


class VideoFilePlayer:
    """A real-video player wrapping pyvidplayer2, matching the player contract.

    Contract: ``update(dt)`` / ``draw(surface)`` / ``done`` / ``skip()``. If
    pyvidplayer2 (or the file) is unavailable the player is born ``done`` and
    inert, so the owning scene pops at once instead of trapping the player.
    """

    def __init__(self, path: str, assets, *, fps: float = 24.0,
                 loop: bool = False, screen_size=(1280, 720)):
        self._size = screen_size
        self._loop = bool(loop)
        self._video = None
        self._finished = False
        # Resolve the pack-relative path to an absolute file via the asset
        # manager, then open it. Any failure → inert/done player.
        try:
            abs_path = assets._resolve(path)
            if abs_path is None:
                raise FileNotFoundError(path)
            from pyvidplayer2 import Video  # lazy: desktop + ffmpeg only
            self._video = Video(str(abs_path))
        except Exception:
            self._video = None
            self._finished = True

    @property
    def done(self) -> bool:
        if self._finished or self._video is None:
            return True
        # pyvidplayer2 exposes `.active` (False once the stream ends).
        return not getattr(self._video, "active", False)

    def skip(self) -> None:
        self._finished = True
        if self._video is not None:
            for closer in ("stop", "close"):
                fn = getattr(self._video, closer, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:
                        pass

    def update(self, dt: float) -> None:
        # pyvidplayer2 advances itself on draw(); nothing to integrate here.
        return

    def draw(self, surface: pygame.Surface) -> None:
        if self._video is None or self._finished:
            return
        try:
            # draw_to scales/letterboxes onto the target surface when available;
            # fall back to a top-left draw on older API versions.
            draw_to = getattr(self._video, "draw", None)
            if draw_to is not None:
                self._video.draw(surface, (0, 0), force_draw=False)
        except Exception:
            self._finished = True


def _factory(path, assets, *, fps=24.0, loop=False, screen_size=(1280, 720)):
    return VideoFilePlayer(path, assets, fps=fps, loop=loop,
                           screen_size=screen_size)


# Register at import time (the manager imports this module under the plugin's
# loading context). Idempotent — re-registration on a re-load is fine.
register_movie_player("video", _factory)
