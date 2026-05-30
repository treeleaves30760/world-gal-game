"""Full-screen movie overlay — openings / endings / cutscenes.

A :class:`MoviePlayerScene` is pushed as an overlay (by the app's ``on_movie``
callback, itself triggered by the ``play_movie`` effect, or directly for an
OP/ED). It owns a *player* (see :mod:`world_gal_game.ui.movie_player`): the
built-in image-sequence player by default, or a real video player a desktop
plugin registered under ``"video"``. It paints letterbox-black behind the frame,
lets the player draw, eats input while playing, and pops itself (via
``on_done``) when the movie finishes or the player skips.

Player selection (``kind``):

- ``"image_sequence"`` — a folder of numbered frames (built-in, web-safe).
- ``"video"`` (or any plugin-registered name) — used when registered; falls back
  to a brief placeholder if the named player is absent.
- ``"auto"`` (default) — a video-file path (``.mp4`` / ``.webm`` / ...) uses
  ``"video"`` when available, otherwise the path is treated as a frame folder.
"""
from __future__ import annotations

import pygame

from .base import Scene, SceneContext
from ..ui.movie_player import (
    build_image_sequence, resolve_movie_player,
)

_VIDEO_EXTS = (".mp4", ".webm", ".mov", ".avi", ".mkv", ".ogv")


class MoviePlayerScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        self._player = None
        self._on_done = None
        self._skippable = True
        self._ended = False

    def enter(self, *, path: str = "", kind: str = "auto", fps: float = 24.0,
              loop: bool = False, skippable: bool = True,
              on_done=None, **_) -> None:
        self._on_done = on_done
        self._skippable = bool(skippable)
        self._ended = False
        self._player = self._build_player(path, kind, fps, loop)

    def _build_player(self, path: str, kind: str, fps: float, loop: bool):
        size = self.ctx.screen_size
        is_video_file = any(path.lower().endswith(e) for e in _VIDEO_EXTS)
        if kind == "auto":
            kind = "video" if is_video_file else "image_sequence"
        try:
            if kind == "image_sequence":
                return build_image_sequence(path, self.ctx.assets, fps=fps,
                                            loop=loop, screen_size=size)
            factory = resolve_movie_player(kind)
            if factory is not None:
                return factory(path, self.ctx.assets, fps=fps, loop=loop,
                               screen_size=size)
        except Exception:
            return None
        # Named player (e.g. "video") not registered — no plugin installed.
        return None

    def _finish(self) -> None:
        if self._ended:
            return
        self._ended = True
        if self._on_done is not None:
            cb, self._on_done = self._on_done, None
            cb()

    def update(self, dt: float, inp) -> None:
        # No player (missing folder / codec / plugin) → end immediately so the
        # overlay never traps the player on a blank screen.
        if self._player is None:
            self._finish()
            return
        try:
            self._player.update(dt)
        except Exception:
            self._finish()
            return
        # Any advance / cancel key or click skips (when allowed).
        if self._skippable and (inp.advance_dialogue or inp.cancel):
            try:
                self._player.skip()
            except Exception:
                self._finish()
                return
        if getattr(self._player, "done", True):
            self._finish()

    def draw(self, surface: pygame.Surface) -> None:
        surface.fill((0, 0, 0))         # letterbox bars + clean backdrop
        if self._player is not None:
            try:
                self._player.draw(surface)
            except Exception:
                pass
        if self._skippable and not self._ended:
            hint = self.ctx.fonts.render(
                "點擊 / 空白鍵 跳過", 14,
                (*self.ctx.theme.text_mute[:3], 170))
            sw, sh = surface.get_size()
            surface.blit(hint, (sw - hint.get_width() - 20,
                                sh - hint.get_height() - 16))

    def describe(self) -> dict:
        return {
            "scene": "MoviePlayerScene",
            "has_player": self._player is not None,
            "frame_count": getattr(self._player, "frame_count", None),
            "ended": self._ended,
            "skippable": self._skippable,
        }
