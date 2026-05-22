"""Music room overlay.

A jukebox for the pack's background music. Tracks are enumerated by
listing the pack's ``assets/bgm/`` directory (``*.ogg`` / ``*.mp3`` /
``*.wav``), so the room reflects everything the pack ships — not just what
the save happens to remember. A track is only *playable* once the player
has heard it in-game (``state.music_room.is_unlocked(path)``); locked rows
render greyed-out and labelled, the same "list everything, gate the
payoff" shape used by the achievements overlay.

How the pack root is obtained
------------------------------
``ctx.assets`` is the :class:`~world_gal_game.ui.assets.AssetManager`. Its
``_pack_root`` is set at app boot from ``config.pack_root(pack)``; the BGM
directory is therefore ``<_pack_root>/assets/bgm``. Track paths are kept in
the pack-relative ``assets/bgm/<file>`` form — the exact shape
``AssetManager._resolve`` / ``play_music`` accept and the same string the
dialogue engine records into ``state.music_room`` when a line's ``bgm``
plays, so unlock look-ups line up. When ``_pack_root`` is unavailable (or
the directory does not exist) the room degrades to listing whatever paths
``state.music_room.unlocked`` already holds, so it still works under an
unusual asset layout.

Playback on close
-----------------
Closing the room **stops any track it started** (``play_music(None)``). A
music-room preview is a deliberate one-off; letting it bleed into the scene
underneath would leave that scene's BGM silently replaced. If the player
did not start anything here (we never touched playback), close leaves the
existing music alone. This is tracked by ``_started_playback``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import pygame

from .base import Scene, SceneContext
from ..ui.widgets import Button, Panel, ScrollArea

# Audio file extensions pygame's mixer can stream as music.
_BGM_EXTS = (".ogg", ".mp3", ".wav")


class MusicRoomScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        self.on_close: Callable[[], None] | None = None
        # Track paths (pack-relative "assets/bgm/...") in display order.
        self._tracks: list[str] = []
        # Did we start a preview here? Governs stop-on-close (see module doc).
        self._started_playback = False
        # Per-frame hit rects for the visible rows: (rect, track_path).
        self._row_rects: list[tuple[pygame.Rect, str]] = []

    # ----- lifecycle -------------------------------------------------------

    def enter(self, *, on_close=None, **_) -> None:
        self.on_close = on_close
        self._started_playback = False
        self._tracks = self._enumerate_tracks()

        sw, sh = self.ctx.screen_size
        self._panel_rect = pygame.Rect(120, 60, sw - 240, sh - 120)
        self._panel = Panel(self._panel_rect, self.ctx.theme,
                            fill=(*self.ctx.theme.bg_overlay[:3], 235),
                            border=self.ctx.theme.border_strong,
                            radius=self.ctx.theme.radius_l, border_width=2)
        self.close_btn = Button(
            pygame.Rect(self._panel_rect.right - 120 - 16,
                        self._panel_rect.y + 16, 120, 36),
            self.ctx.localization.t("close", "關閉"),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=15, style="ghost",
            on_click=(lambda: on_close() if on_close else None),
        )
        # Stop control: silences whatever is playing (scene BGM included).
        self.stop_btn = Button(
            pygame.Rect(self._panel_rect.right - 120 - 16 - 110 - 12,
                        self._panel_rect.y + 16, 110, 36),
            self.ctx.localization.t("music_stop", "停止"),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=15, style="ghost",
            on_click=self._stop,
        )
        self._scroll = ScrollArea(
            pygame.Rect(self._panel_rect.x + 30, self._panel_rect.y + 100,
                        self._panel_rect.width - 60,
                        self._panel_rect.height - 130),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
        )
        self._scroll.set_drawer(self._draw_content)

    # ----- track enumeration ----------------------------------------------

    def _bgm_dir(self) -> Path | None:
        """Locate ``<pack_root>/assets/bgm`` via the asset manager.

        Prefers ``AssetManager._resolve("assets/bgm")`` so we honour exactly
        the same path resolution playback uses; falls back to ``_pack_root``
        directly. Returns None when no usable directory is found.
        """
        assets = self.ctx.assets
        resolver = getattr(assets, "_resolve", None)
        if callable(resolver):
            try:
                resolved = resolver("assets/bgm")
            except Exception:
                resolved = None
            if resolved is not None:
                p = Path(resolved)
                if p.is_dir():
                    return p
        pack_root = getattr(assets, "_pack_root", None)
        if pack_root is not None:
            p = Path(pack_root) / "assets" / "bgm"
            if p.is_dir():
                return p
        return None

    def _enumerate_tracks(self) -> list[str]:
        """List BGM as pack-relative ``assets/bgm/<file>`` paths, sorted.

        Degrades to the unlocked set recorded on the save when the directory
        cannot be enumerated, so the room is never empty just because the
        layout is unusual.
        """
        bgm_dir = self._bgm_dir()
        if bgm_dir is not None:
            try:
                files = [
                    f for f in bgm_dir.iterdir()
                    if f.is_file() and f.suffix.lower() in _BGM_EXTS
                ]
            except OSError:
                files = []
            tracks = sorted(f"assets/bgm/{f.name}" for f in files)
            if tracks:
                # Make sure any unlocked track that lives elsewhere still
                # shows up (e.g. a pack referencing BGM outside assets/bgm).
                extra = sorted(
                    p for p in self.ctx.state.music_room.unlocked
                    if p not in tracks
                )
                return tracks + extra
        # Fallback: only what the player has already heard.
        return sorted(self.ctx.state.music_room.unlocked)

    # ----- track helpers ---------------------------------------------------

    @staticmethod
    def _label_for(path: str) -> str:
        """Human-friendly title from a track path (stem, underscores → spaces)."""
        stem = Path(path).stem
        return stem.replace("_", " ").strip() or path

    def _is_unlocked(self, path: str) -> bool:
        return self.ctx.state.music_room.is_unlocked(path)

    def _now_playing(self) -> str | None:
        return getattr(self.ctx.assets, "_current_music", None)

    # ----- playback --------------------------------------------------------

    def _play(self, path: str) -> None:
        if not self._is_unlocked(path):
            return
        self.ctx.assets.play_music(path, volume=self.ctx.config.bgm_volume)
        self._started_playback = True

    def _stop(self) -> None:
        self.ctx.assets.play_music(None)
        # We've taken charge of playback; close needn't stop again, but the
        # flag staying True is harmless (stopping silence is a no-op).
        self._started_playback = True

    def _close(self) -> None:
        # A preview shouldn't hijack the underlying scene's BGM: stop only
        # what we started. If we never touched playback, leave it be.
        if self._started_playback:
            self.ctx.assets.play_music(None)
        if self.on_close:
            self.on_close()

    # ----- drawing ---------------------------------------------------------

    def _draw_content(self, surface: pygame.Surface) -> int:
        now = self._now_playing()
        y = 0
        row_h = 64
        width = self._scroll.rect.width - 14
        if not self._tracks:
            empty = self.ctx.fonts.render(
                self.ctx.localization.t(
                    "music_room_empty", "（這個遊戲沒有收錄音樂。）"),
                18, self.ctx.theme.text_mute)
            surface.blit(empty, (0, 0))
            return empty.get_height()

        for path in self._tracks:
            unlocked = self._is_unlocked(path)
            playing = unlocked and now == path
            row = pygame.Surface((width, row_h), pygame.SRCALPHA)
            # Fill: playing → accent tint, unlocked → faint, locked → dim.
            if playing:
                tint = (*self.ctx.theme.accent_warm[:3], 70)
            elif unlocked:
                tint = (255, 255, 255, 20)
            else:
                tint = (*self.ctx.theme.text_dim[:3], 24)
            pygame.draw.rect(row, tint, row.get_rect(),
                             border_radius=self.ctx.theme.radius_m)
            border = (self.ctx.theme.accent_warm if playing
                      else self.ctx.theme.border if unlocked
                      else self.ctx.theme.border_soft)
            pygame.draw.rect(row, border, row.get_rect(), width=1,
                             border_radius=self.ctx.theme.radius_m)

            # Status chip: ▶ playing, ♪ playable, lock glyph otherwise.
            chip_color = (self.ctx.theme.accent_warm if unlocked
                          else self.ctx.theme.text_dim)
            glyph = "▶" if playing else ("♪" if unlocked else "—")
            g = self.ctx.fonts.render(glyph, 24, chip_color, bold=True)
            row.blit(g, (18, (row_h - g.get_height()) // 2))

            # Title.
            title_color = (self.ctx.theme.text if unlocked
                           else self.ctx.theme.text_dim)
            title = (self._label_for(path) if unlocked
                     else self.ctx.localization.t("music_locked_title", "？？？"))
            t = self.ctx.fonts.render(title, 20, title_color, bold=True)
            row.blit(t, (58, 10))

            # Sub-line: "now playing" or "locked" hint.
            if playing:
                sub = self.ctx.localization.t("music_now_playing", "正在播放")
                sub_color = self.ctx.theme.accent_warm
            elif unlocked:
                sub = self.ctx.localization.t("music_tap_to_play", "點擊播放")
                sub_color = self.ctx.theme.text_mute
            else:
                sub = self.ctx.localization.t("music_locked", "尚未解鎖")
                sub_color = self.ctx.theme.text_dim
            s = self.ctx.fonts.render(sub, 14, sub_color)
            row.blit(s, (58, 38))

            surface.blit(row, (0, y))
            # Hit rect in absolute screen coords (only unlocked rows click).
            if unlocked:
                screen_y = self._scroll.rect.y + y - self._scroll.scroll_y
                self._row_rects.append((
                    pygame.Rect(self._scroll.rect.x, screen_y, width, row_h),
                    path,
                ))
            y += row_h + 8
        return y

    # ----- input -----------------------------------------------------------

    def update(self, dt: float, inp) -> None:
        if inp.cancel:
            self._close()
            return
        self.close_btn.update(dt, inp)
        self.stop_btn.update(dt, inp)
        self._scroll.update(dt, inp)
        if inp.mouse_clicked:
            for rect, path in self._row_rects:
                if rect.collidepoint(inp.mouse_pos):
                    self._play(path)
                    return

    def draw(self, surface: pygame.Surface) -> None:
        # Rebuild hit rects against the current visible layout each frame.
        self._row_rects = []
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 160))
        surface.blit(veil, (0, 0))
        self._panel.draw(surface)
        title = self.ctx.fonts.render(
            self.ctx.localization.t("music_room", "音樂室"),
            self.ctx.config.font_size_header,
            self.ctx.theme.accent, bold=True,
        )
        surface.blit(title, (self._panel_rect.x + 32, self._panel_rect.y + 28))
        # Status counter: unlocked / total.
        unlocked_n = sum(1 for p in self._tracks if self._is_unlocked(p))
        total_n = len(self._tracks)
        if total_n:
            cnt = self.ctx.fonts.render(
                f"{unlocked_n} / {total_n}", 18, self.ctx.theme.accent_warm,
            )
            surface.blit(cnt, (self._panel_rect.x + 32,
                               self._panel_rect.y + 68))
        self.stop_btn.draw(surface)
        self.close_btn.draw(surface)
        self._scroll.draw(surface)

    def describe(self) -> dict:
        unlocked_n = sum(1 for p in self._tracks if self._is_unlocked(p))
        return {
            "scene": "MusicRoomScene",
            "track_count": len(self._tracks),
            "unlocked_count": unlocked_n,
            "now_playing": self._now_playing(),
        }
