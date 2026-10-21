"""Accessible, metadata-driven Music Room overlay.

Packs may author ``content/music_room.yaml`` for formal titles, credits,
story-use notes, ordering, and spoiler-safe hidden entries.  The persistent
unlock identity remains the pack-relative audio path in ``state.music_room``;
an absent manifest therefore preserves the legacy directory/filename view and
existing saves need no migration.

The overlay owns every preview bus temporarily.  Enter captures BGM, ambient,
and voice playback; every close/exit path restores that snapshot exactly once.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import pygame

from .base import Scene, SceneContext
from ..core.media_authority import MediaAuthority
from ..core.music_room_spec import (
    MusicRoomEntrySpec,
    MusicRoomManifest,
    music_room_localization_id,
)
from ..ui.assets import AudioPlaybackSnapshot
from ..ui.input_bindings import BindingProfile, InputAction
from ..ui.system_chrome import ChromePart, ChromeRequest
from ..ui.widgets import Button, Panel, ScrollArea

_BGM_EXTS = (".ogg", ".mp3", ".wav")
_MAX_DISCOVERED_TRACKS = 4096


class MusicRoomScene(Scene):
    """A focusable single-column jukebox for heard story music."""

    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        self.on_close: Callable[[], None] | None = None
        # Compatibility contract: callers may inspect this path-only roster.
        self._tracks: list[str] = []
        self._catalog_by_asset: dict[str, MusicRoomEntrySpec] = {}
        self._catalog_authored = False
        self._started_playback = False
        self._audio_snapshot: AudioPlaybackSnapshot | None = None
        # Historical public-ish hit list remains unlocked rows only.
        self._row_rects: list[tuple[pygame.Rect, str]] = []
        self._row_screen_rects: list[tuple[pygame.Rect, str, bool]] = []
        self._row_content_rects: list[pygame.Rect] = []
        self._focus = -1
        self._focus_zone = "rows"
        self._hover_index = -1
        self._status_message = ""
        self._status_kind = "info"
        self._layout_signature: tuple[object, ...] | None = None

    # ----- lifecycle ---------------------------------------------------

    def _sp(self, value: int) -> int:
        return max(1, int(round(value * self._density)))

    def enter(self, *, on_close=None, **_) -> None:
        self._restore_audio()
        self.on_close = on_close
        self._started_playback = False
        self._status_message = ""
        self._status_kind = "info"
        self._audio_snapshot = self.ctx.assets.capture_audio_playback()
        try:
            self._tracks = self._enumerate_tracks()
            self._build_layout(self.ctx.screen_size)
        except Exception:
            self._restore_audio()
            raise

    def exit(self) -> None:
        self._restore_audio()

    def _restore_audio(self) -> None:
        snapshot = self._audio_snapshot
        if snapshot is None:
            return
        self._audio_snapshot = None
        self._started_playback = False
        self.ctx.assets.restore_audio_playback(snapshot)

    # ----- catalog and discovery --------------------------------------

    def _bgm_dir(self) -> Path | None:
        assets = self.ctx.assets
        resolver = getattr(assets, "_resolve", None)
        if callable(resolver):
            try:
                resolved = resolver("assets/bgm")
            except Exception:
                resolved = None
            if resolved is not None:
                path = Path(resolved)
                if path.is_dir():
                    return path
        pack_root = getattr(assets, "_pack_root", None)
        if pack_root is not None:
            path = Path(pack_root) / "assets" / "bgm"
            if path.is_dir():
                return path
        return None

    def _authorized_unlocked(self) -> set[str]:
        unlocked = set(self.ctx.state.music_room.unlocked)
        authority = self.ctx.state.meta.get("__pack_media_authority__")
        if isinstance(authority, MediaAuthority):
            unlocked = authority.filter_music_ids(unlocked)
        return unlocked

    def _enumerate_tracks(self) -> list[str]:
        """Return the visible roster in authored or legacy order."""
        unlocked = self._authorized_unlocked()
        manifest = self.ctx.state.meta.get("__music_room_manifest__")
        if isinstance(manifest, MusicRoomManifest) and manifest.authored:
            self._catalog_authored = True
            self._catalog_by_asset = manifest.by_asset()
            catalog_assets = set(self._catalog_by_asset)
            visible = [
                entry.asset
                for entry in manifest.ordered()
                if not entry.hidden or entry.asset in unlocked
            ]
            # Never erase still-authorized history when a pack adopts or edits
            # a catalog. Uncatalogued heard tracks retain legacy labels.
            visible.extend(sorted(unlocked - catalog_assets))
            return visible[:_MAX_DISCOVERED_TRACKS]

        self._catalog_authored = False
        self._catalog_by_asset = {}
        bgm_dir = self._bgm_dir()
        if bgm_dir is not None:
            try:
                files = [
                    entry
                    for entry in bgm_dir.iterdir()
                    if entry.is_file()
                    and entry.suffix.lower() in _BGM_EXTS
                ]
            except OSError:
                files = []
            tracks = sorted(
                f"assets/bgm/{entry.name}" for entry in files
            )[:_MAX_DISCOVERED_TRACKS]
            if tracks:
                tracks.extend(
                    sorted(unlocked - set(tracks))[
                        :max(0, _MAX_DISCOVERED_TRACKS - len(tracks))
                    ]
                )
                return tracks
        return sorted(unlocked)[:_MAX_DISCOVERED_TRACKS]

    def _catalog_entry(self, path: str) -> MusicRoomEntrySpec | None:
        return self._catalog_by_asset.get(path)

    @staticmethod
    def _legacy_label(path: str) -> str:
        stem = Path(path).stem
        return stem.replace("_", " ").strip() or path

    def _label_for(self, path: str) -> str:
        entry = self._catalog_entry(path)
        if entry is None:
            return self._legacy_label(path)
        return self.ctx.story(
            f"music_room.{music_room_localization_id(entry)}.title",
            entry.title,
        )

    def _entry_text(self, entry: MusicRoomEntrySpec, field: str) -> str:
        value = getattr(entry, field)
        if not value:
            return ""
        return self.ctx.story(
            f"music_room.{music_room_localization_id(entry)}.{field}",
            value,
        )

    def _is_unlocked(self, path: str) -> bool:
        return self.ctx.state.music_room.is_unlocked(path)

    def _now_playing(self) -> str | None:
        return getattr(self.ctx.assets, "_current_music", None)

    # ----- layout ------------------------------------------------------

    def _build_layout(self, screen_size: tuple[int, int]) -> None:
        sw, sh = screen_size
        theme = self.ctx.theme
        self._density = max(
            1.0,
            float(getattr(self.ctx.fonts, "scale", 1.0)),
        )
        self._layout_signature = (
            (sw, sh),
            self._density,
            getattr(self.ctx.fonts, "revision", None),
        )
        self._minimum_target = max(44, self._sp(44))
        self._title_font_size = 30 if sh <= 450 else self.ctx.config.font_size_header
        self._track_font_size = 19
        self._meta_font_size = 13
        self._status_font_size = 13
        self._button_font_size = 15
        self._footer_font_size = 12

        edge_x = max(12, self._sp(32))
        edge_y = max(12, self._sp(24))
        panel_w = min(self._sp(1120), max(1, sw - edge_x * 2))
        panel_h = min(self._sp(760), max(1, sh - edge_y * 2))
        self._panel_rect = pygame.Rect(0, 0, panel_w, panel_h)
        self._panel_rect.center = (sw // 2, sh // 2)
        self._panel = Panel(
            self._panel_rect,
            theme,
            fill=(*theme.bg_overlay[:3], 242),
            border=theme.border_strong,
            radius=theme.radius_l,
            border_width=2,
            chrome=getattr(self.ctx, "ui_chrome", None),
            screen="music_room",
            role="main",
            density=self._density,
        )

        inner = max(14, self._sp(28))
        header_pad = max(8, self._sp(12))
        bindings = BindingProfile(
            getattr(self.ctx.config, "input_bindings", {}),
        )
        close_label = (
            f"{self.ctx.localization.t('close', '關閉')} "
            f"({bindings.primary_display(InputAction.BACK)})"
        )
        stop_label = self.ctx.localization.t("music_stop", "停止播放")
        button_font = self.ctx.fonts.get(self._button_font_size, bold=True)
        button_h = max(
            self._minimum_target,
            button_font.get_linesize() + self._sp(14),
        )

        def button_width(label: str, minimum: int) -> int:
            return max(
                self._minimum_target,
                self._sp(minimum),
                button_font.size(label)[0] + self._sp(24),
            )

        close_w = button_width(close_label, 128)
        stop_w = button_width(stop_label, 118)
        button_gap = self._sp(10)

        title_text = self.ctx.localization.t("music_room", "音樂室")
        title_font = self.ctx.fonts.get(self._title_font_size, bold=True)
        raw_title_w = title_font.size(title_text)[0]
        unlocked_n = sum(self._is_unlocked(path) for path in self._tracks)
        self._counter_text = f"{unlocked_n} / {len(self._tracks)}"
        self._counter_surface = self.ctx.fonts.render(
            self._counter_text,
            17,
            theme.accent_warm,
            bold=True,
        )
        available_header_w = max(1, self._panel_rect.width - inner * 2)
        close_w = min(close_w, available_header_w)
        stop_w = min(stop_w, available_header_w)
        controls_w = stop_w + button_gap + close_w
        self._controls_stacked = controls_w > available_header_w
        self._header_stacked = (
            raw_title_w
            + self._sp(24)
            + controls_w
            > available_header_w
        )
        title_max_w = (
            available_header_w
            if self._header_stacked
            else max(1, available_header_w - controls_w - self._sp(24))
        )
        title_text = self._ellipsize(title_text, title_font, title_max_w)
        self._title_surface = self.ctx.fonts.render(
            title_text,
            self._title_font_size,
            theme.accent,
            bold=True,
        )
        title_block_h = (
            self._title_surface.get_height()
            + self._sp(3)
            + self._counter_surface.get_height()
        )
        controls_h = (
            button_h * 2 + button_gap
            if self._controls_stacked
            else button_h
        )
        self._header_h = (
            title_block_h + controls_h + header_pad * 3
            if self._header_stacked
            else max(title_block_h, controls_h) + header_pad * 2
        )
        title_x = self._panel_rect.x + inner
        title_y = self._panel_rect.y + header_pad
        self._title_rect = self._title_surface.get_rect(
            topleft=(title_x, title_y),
        )
        self._counter_rect = self._counter_surface.get_rect(
            topleft=(title_x, self._title_rect.bottom + self._sp(3)),
        )
        button_y = (
            self._panel_rect.y + header_pad * 2 + title_block_h
            if self._header_stacked
            else self._panel_rect.y + (self._header_h - button_h) // 2
        )
        if self._controls_stacked:
            stop_rect = pygame.Rect(
                self._panel_rect.x + inner,
                button_y,
                available_header_w,
                button_h,
            )
            close_rect = pygame.Rect(
                self._panel_rect.x + inner,
                stop_rect.bottom + button_gap,
                available_header_w,
                button_h,
            )
        else:
            close_rect = pygame.Rect(
                self._panel_rect.right - inner - close_w,
                button_y,
                close_w,
                button_h,
            )
            stop_rect = pygame.Rect(
                close_rect.x - button_gap - stop_w,
                button_y,
                stop_w,
                button_h,
            )

        def fit_button_label(label: str, width: int) -> str:
            text_pad = max(
                theme.pad_m,
                int(round(
                    theme.pad_m * getattr(self.ctx.fonts, "scale", 1.0)
                )),
            )
            return self._ellipsize(
                label,
                button_font,
                max(1, width - text_pad * 2),
            )

        fitted_close_label = fit_button_label(close_label, close_rect.width)
        fitted_stop_label = fit_button_label(stop_label, stop_rect.width)
        self.close_btn = Button(
            close_rect,
            fitted_close_label,
            fonts=self.ctx.fonts,
            theme=theme,
            font_size=self._button_font_size,
            style="ghost",
            on_click=self._close,
            chrome=getattr(self.ctx, "ui_chrome", None),
            screen="music_room",
            role="close",
            tooltip=(
                close_label if fitted_close_label != close_label else None
            ),
            motion=self.ctx.motion,
        )
        self.stop_btn = Button(
            stop_rect,
            fitted_stop_label,
            fonts=self.ctx.fonts,
            theme=theme,
            font_size=self._button_font_size,
            style="ghost",
            on_click=self._stop,
            chrome=getattr(self.ctx, "ui_chrome", None),
            screen="music_room",
            role="stop",
            tooltip=(
                stop_label if fitted_stop_label != stop_label else None
            ),
            motion=self.ctx.motion,
        )

        self._header_divider_y = self._panel_rect.y + self._header_h - 1
        footer_h = max(self._minimum_target, self._sp(48))
        footer_bottom_pad = self._sp(8)
        self._footer_rect = pygame.Rect(
            self._panel_rect.x + inner,
            self._panel_rect.bottom - footer_bottom_pad - footer_h,
            max(1, self._panel_rect.width - inner * 2),
            footer_h,
        )
        body_top = self._header_divider_y + self._sp(10)
        body_bottom = self._footer_rect.top - self._sp(8)
        self._scroll = ScrollArea(
            pygame.Rect(
                self._panel_rect.x + inner,
                body_top,
                max(1, self._panel_rect.width - inner * 2),
                max(1, body_bottom - body_top),
            ),
            fonts=self.ctx.fonts,
            theme=theme,
        )
        # ScrollArea supplies wheel/touch physics and clamping only. Rows are
        # drawn directly into the viewport below, so a large authored catalog
        # never allocates a width × full-content-height backing surface.
        self._scroll.set_drawer(None)

        title_line = self.ctx.fonts.get(
            self._track_font_size,
            bold=True,
        ).get_linesize()
        meta_line = self.ctx.fonts.get(self._meta_font_size).get_linesize()
        status_line = self.ctx.fonts.get(self._status_font_size).get_linesize()
        self._row_h = max(
            self._minimum_target,
            title_line + meta_line + status_line + self._sp(18),
            self._sp(82),
        )
        self._row_gap = self._sp(8)
        row_w = max(1, self._scroll.rect.width - self._sp(14))
        self._row_content_rects = [
            pygame.Rect(
                0,
                index * (self._row_h + self._row_gap),
                row_w,
                self._row_h,
            )
            for index in range(len(self._tracks))
        ]
        self._content_height = (
            self._row_content_rects[-1].bottom
            if self._row_content_rects
            else status_line
        )
        self._scroll.content_height = self._content_height
        now = self._now_playing()
        initial = next(
            (
                index
                for index, path in enumerate(self._tracks)
                if path == now
            ),
            next(
                (
                    index
                    for index, path in enumerate(self._tracks)
                    if self._is_unlocked(path)
                ),
                0 if self._tracks else -1,
            ),
        )
        self.stop_btn.enabled = now is not None
        self._focus = initial
        self._focus_zone = "rows" if initial >= 0 else (
            "stop" if self.stop_btn.enabled else "close"
        )
        self._hover_index = -1
        self._sync_focus_state()
        self._ensure_focus_visible()

    def _ensure_layout(self, screen_size: tuple[int, int]) -> None:
        """Reflow on window/font-scale changes without losing focus identity."""
        density = max(
            1.0,
            float(getattr(self.ctx.fonts, "scale", 1.0)),
        )
        signature = (
            tuple(screen_size),
            density,
            getattr(self.ctx.fonts, "revision", None),
        )
        if signature == self._layout_signature:
            return
        focused_path = (
            self._tracks[self._focus]
            if self._focus_zone == "rows" and 0 <= self._focus < len(self._tracks)
            else None
        )
        focused_zone = self._focus_zone
        self._build_layout(tuple(screen_size))
        if focused_path in self._tracks:
            self._set_row_focus(self._tracks.index(focused_path))
        elif focused_zone in {"stop", "close"}:
            self._set_button_focus(focused_zone)

    # ----- playback ----------------------------------------------------

    def _play(self, path: str) -> bool:
        if not self._is_unlocked(path):
            self._status_message = self.ctx.localization.t(
                "music_locked_explanation",
                "這首曲目尚未解鎖；在故事中聽過後即可播放。",
            )
            self._status_kind = "locked"
            return False
        authority = self.ctx.state.meta.get("__pack_media_authority__")
        if isinstance(authority, MediaAuthority) and path not in authority.music_ids:
            self._status_message = self.ctx.localization.t(
                "music_unavailable",
                "這首曲目目前無法播放。",
            )
            self._status_kind = "error"
            return False
        self.ctx.assets.play_music(path, volume=self.ctx.config.bgm_volume)
        if self._now_playing() != path:
            self._status_message = self.ctx.localization.t(
                "music_playback_failed",
                "無法載入這首曲目，請檢查音訊檔案。",
            )
            self._status_kind = "error"
            return False
        if not self._started_playback:
            # A jukebox preview owns the listening space. Remove scene ambience
            # and an interrupted voice only after BGM ownership succeeds; the
            # entry snapshot restores all three buses on close.
            self.ctx.assets.stop_ambient(fade_ms=200)
            self.ctx.assets.stop_voice()
        self._started_playback = True
        self.stop_btn.enabled = True
        self._status_message = self.ctx.localization.t(
            "music_now_playing_named",
            "正在播放：{title}",
            title=self._label_for(path),
        )
        self._status_kind = "playing"
        return True

    def _stop(self) -> None:
        self.ctx.assets.play_music(None)
        self._started_playback = True
        self.stop_btn.enabled = False
        if self._focus_zone == "stop":
            self._set_button_focus("close")
        self._status_message = self.ctx.localization.t(
            "music_playback_stopped",
            "已停止試聽；離開時會恢復原本的音訊。",
        )
        self._status_kind = "info"

    def _close(self) -> None:
        try:
            self._restore_audio()
        finally:
            if self.on_close:
                self.on_close()

    # ----- drawing helpers --------------------------------------------

    def _paint_chrome(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        *,
        part: ChromePart,
        variant: str,
        enabled: bool = True,
        hovered: bool = False,
        focused: bool = False,
        selected: bool = False,
        emphasis: int = 0,
    ) -> bool:
        chrome = getattr(self.ctx, "ui_chrome", None)
        if chrome is None:
            return False
        return chrome.paint(
            surface,
            rect,
            ChromeRequest(
                part=part,
                screen="music_room",
                variant=variant,
                size=rect.size,
                density=self._density,
                enabled=enabled,
                hovered=hovered,
                focused=focused,
                selected=selected,
                emphasis=emphasis,
            ),
        )

    @staticmethod
    def _ellipsize(text: str, font, max_width: int) -> str:
        text = text.strip()
        if not text or max_width <= 0:
            return ""
        if font.size(text)[0] <= max_width:
            return text
        ellipsis = "…"
        if font.size(ellipsis)[0] > max_width:
            return ""
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            candidate = text[:middle].rstrip() + ellipsis
            if font.size(candidate)[0] <= max_width:
                low = middle
            else:
                high = middle - 1
        return text[:low].rstrip() + ellipsis

    def _draw_content(self, surface: pygame.Surface) -> int:
        """Legacy drawer hook retained for embedders; runtime is virtualized."""
        if not self._tracks:
            empty = self.ctx.fonts.render(
                self.ctx.localization.t(
                    "music_room_empty",
                    "（這個遊戲沒有設定音樂鑑賞曲目。）",
                ),
                18,
                self.ctx.theme.text_mute,
            )
            surface.blit(empty, (0, 0))
            return empty.get_height()

        now = self._now_playing()
        for index, (rect, path) in enumerate(
            zip(self._row_content_rects, self._tracks, strict=True),
        ):
            self._draw_row(
                surface,
                rect,
                path,
                index=index,
                unlocked=self._is_unlocked(path),
                playing=now == path and self._is_unlocked(path),
                focused=(self._focus_zone == "rows" and self._focus == index),
                hovered=self._hover_index == index,
            )
        return self._content_height

    def _visible_row_range(self) -> range:
        if not self._tracks:
            return range(0)
        stride = max(1, self._row_h + self._row_gap)
        first = max(0, self._scroll.scroll_y // stride)
        last = min(
            len(self._tracks),
            (self._scroll.scroll_y + self._scroll.rect.height + stride - 1)
            // stride,
        )
        return range(first, max(first, last))

    def _draw_viewport(self, surface: pygame.Surface) -> None:
        """Draw only visible rows; catalog size does not control frame cost."""
        previous_clip = surface.get_clip()
        surface.set_clip(previous_clip.clip(self._scroll.rect))
        if not self._tracks:
            empty = self.ctx.fonts.render(
                self.ctx.localization.t(
                    "music_room_empty",
                    "（這個遊戲沒有設定音樂鑑賞曲目。）",
                ),
                18,
                self.ctx.theme.text_mute,
            )
            surface.blit(empty, self._scroll.rect.topleft)
        else:
            now = self._now_playing()
            for index in self._visible_row_range():
                path = self._tracks[index]
                content_rect = self._row_content_rects[index]
                screen_rect = content_rect.move(
                    self._scroll.rect.x,
                    self._scroll.rect.y - self._scroll.scroll_y,
                )
                unlocked = self._is_unlocked(path)
                self._draw_row(
                    surface,
                    screen_rect,
                    path,
                    index=index,
                    unlocked=unlocked,
                    playing=unlocked and now == path,
                    focused=(
                        self._focus_zone == "rows" and self._focus == index
                    ),
                    hovered=self._hover_index == index,
                )
        surface.set_clip(previous_clip)
        self._draw_scrollbar(surface)

    def _footer_hint(self, bindings: BindingProfile) -> str:
        return self.ctx.localization.t(
            "music_room_footer",
            up=bindings.primary_display(InputAction.NAV_UP),
            down=bindings.primary_display(InputAction.NAV_DOWN),
            confirm=bindings.primary_display(InputAction.CONFIRM_ADVANCE),
            page_up=bindings.primary_display(InputAction.PAGE_UP),
            page_down=bindings.primary_display(InputAction.PAGE_DOWN),
        )

    def _draw_scrollbar(self, surface: pygame.Surface) -> None:
        if self._content_height <= self._scroll.rect.height:
            return
        track_h = self._scroll.rect.height
        knob_h = max(
            min(28, track_h),
            int(track_h * self._scroll.rect.height / self._content_height),
        )
        knob_h = min(track_h, knob_h)
        maximum = max(1, self._content_height - self._scroll.rect.height)
        travel = max(0, track_h - knob_h)
        knob_y = round(self._scroll.scroll_y / maximum * travel)
        pygame.draw.rect(
            surface,
            (*self.ctx.theme.border_soft[:3], 80),
            (self._scroll.rect.right - 6, self._scroll.rect.y, 4, track_h),
            border_radius=2,
        )
        pygame.draw.rect(
            surface,
            (*self.ctx.theme.accent[:3], 190),
            (
                self._scroll.rect.right - 6,
                self._scroll.rect.y + knob_y,
                4,
                knob_h,
            ),
            border_radius=2,
        )

    def _draw_row(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        path: str,
        *,
        index: int,
        unlocked: bool,
        playing: bool,
        focused: bool,
        hovered: bool,
    ) -> None:
        theme = self.ctx.theme
        row = pygame.Surface(rect.size, pygame.SRCALPHA)
        painted = self._paint_chrome(
            row,
            row.get_rect(),
            part="card",
            variant="track",
            enabled=unlocked,
            hovered=hovered,
            focused=focused,
            selected=playing,
            emphasis=3 if playing else 1 if focused else 0,
        )
        if not painted:
            if playing:
                fill = (*theme.accent_warm[:3], 66)
            elif hovered or focused:
                fill = (*theme.accent[:3], 38)
            elif unlocked:
                fill = (255, 255, 255, 18)
            else:
                fill = (*theme.bg_panel[:3], 190)
            pygame.draw.rect(
                row,
                fill,
                row.get_rect(),
                border_radius=theme.radius_m,
            )
            border = (
                theme.accent_warm
                if playing
                else theme.border_strong
                if focused
                else theme.border
                if unlocked
                else theme.border_soft
            )
            pygame.draw.rect(
                row,
                border,
                row.get_rect(),
                width=2 if focused or playing else 1,
                border_radius=theme.radius_m,
            )

        index_text = f"{index + 1:02d}"
        index_surface = self.ctx.fonts.render(
            index_text,
            14,
            theme.accent_warm if unlocked else theme.text_dim,
            bold=True,
        )
        index_rect = index_surface.get_rect(
            center=(self._sp(28), rect.height // 2),
        )
        row.blit(index_surface, index_rect)
        pygame.draw.line(
            row,
            theme.border_soft,
            (self._sp(52), self._sp(12)),
            (self._sp(52), rect.height - self._sp(12)),
        )

        text_x = self._sp(70)
        right_pad = self._sp(24)
        text_w = max(1, rect.width - text_x - right_pad)
        title_font = self.ctx.fonts.get(self._track_font_size, bold=True)
        meta_font = self.ctx.fonts.get(self._meta_font_size)
        title = (
            self._label_for(path)
            if unlocked
            else self.ctx.localization.t("music_locked_title", "？？？")
        )
        title_surface = self.ctx.fonts.render(
            self._ellipsize(title, title_font, text_w),
            self._track_font_size,
            theme.text if unlocked else theme.text_dim,
            bold=True,
        )
        row.blit(title_surface, (text_x, self._sp(8)))

        entry = self._catalog_entry(path) if unlocked else None
        metadata = ""
        if entry is not None:
            parts = [
                part
                for part in (
                    self._entry_text(entry, "artist"),
                    self._entry_text(entry, "usage"),
                )
                if part
            ]
            metadata = " · ".join(parts)
        if not metadata and unlocked:
            metadata = self.ctx.localization.t(
                "music_catalog_legacy",
                "舊版曲目資料",
            )
        if not unlocked:
            metadata = self.ctx.localization.t(
                "music_locked",
                "尚未解鎖",
            )
        metadata_surface = self.ctx.fonts.render(
            self._ellipsize(metadata, meta_font, text_w),
            self._meta_font_size,
            theme.text_mute if unlocked else theme.text_dim,
        )
        meta_y = self._sp(34)
        row.blit(metadata_surface, (text_x, meta_y))

        if playing:
            status = self.ctx.localization.t(
                "music_now_playing",
                "正在播放",
            )
            status_color = theme.accent_warm
            marker = "▶"
        elif unlocked:
            status = self.ctx.localization.t(
                "music_confirm_to_play",
                "按確認或點擊播放",
            )
            status_color = theme.text_mute
            marker = "♪"
        else:
            status = self.ctx.localization.t(
                "music_hear_to_unlock",
                "在故事中聽過後解鎖",
            )
            status_color = theme.text_dim
            marker = "—"
        status_font = self.ctx.fonts.get(self._status_font_size)
        status_surface = self.ctx.fonts.render(
            self._ellipsize(status, status_font, text_w),
            self._status_font_size,
            status_color,
        )
        row.blit(status_surface, (text_x, rect.height - status_surface.get_height() - self._sp(7)))
        marker_surface = self.ctx.fonts.render(
            marker,
            18,
            status_color,
            bold=True,
        )
        row.blit(
            marker_surface,
            (
                rect.width - right_pad - marker_surface.get_width(),
                self._sp(9),
            ),
        )

        if focused:
            pygame.draw.rect(
                row,
                theme.accent_warm,
                row.get_rect(),
                width=max(3, self._sp(3)),
                border_radius=theme.radius_m,
            )
        if (
            not unlocked
            and getattr(theme, "visual_profile", "standard") == "high_contrast"
        ):
            for offset in (0, self._sp(7), self._sp(14)):
                pygame.draw.line(
                    row,
                    theme.text_dim,
                    (rect.width - self._sp(28) + offset, self._sp(4)),
                    (rect.width - self._sp(4), self._sp(28) - offset),
                    width=2,
                )
        surface.blit(row, rect.topleft)

    def _rebuild_screen_row_rects(self) -> None:
        self._row_rects = []
        self._row_screen_rects = []
        viewport = self._scroll.rect
        for rect, path in zip(
            self._row_content_rects,
            self._tracks,
            strict=True,
        ):
            screen_rect = rect.move(
                viewport.x,
                viewport.y - self._scroll.scroll_y,
            )
            clipped = screen_rect.clip(viewport)
            if clipped.width <= 0 or clipped.height <= 0:
                continue
            unlocked = self._is_unlocked(path)
            self._row_screen_rects.append((clipped, path, unlocked))
            if unlocked:
                self._row_rects.append((clipped, path))

    # ----- focus and input --------------------------------------------

    @staticmethod
    def _pressed(inp, action: str) -> bool:
        pressed = getattr(inp, "pressed", None)
        if callable(pressed):
            return bool(pressed(action))
        return action in getattr(inp, "actions_pressed", set())

    @staticmethod
    def _pointer_moved(inp) -> bool:
        return any(
            event.type == pygame.MOUSEMOTION
            for event in getattr(inp, "events", ())
        )

    def _sync_focus_state(self) -> None:
        self.stop_btn.focused = self._focus_zone == "stop"
        self.close_btn.focused = self._focus_zone == "close"

    def _set_row_focus(self, index: int, *, ensure: bool = True) -> bool:
        if not 0 <= index < len(self._tracks):
            return False
        self._focus = index
        self._focus_zone = "rows"
        self._sync_focus_state()
        if ensure:
            self._ensure_focus_visible()
        return True

    def _set_button_focus(self, zone: str) -> None:
        if zone not in {"stop", "close"}:
            raise ValueError("button focus zone must be stop or close")
        if zone == "stop" and not self.stop_btn.enabled:
            zone = "close"
        self._focus_zone = zone
        self._sync_focus_state()

    def _cycle_focus(self, step: int) -> None:
        targets: list[int | str] = [*range(len(self._tracks))]
        if self.stop_btn.enabled:
            targets.append("stop")
        targets.append("close")
        current: int | str = (
            self._focus if self._focus_zone == "rows" else self._focus_zone
        )
        try:
            position = targets.index(current)
        except ValueError:
            position = -1 if step > 0 else 0
        target = targets[(position + step) % len(targets)]
        if isinstance(target, int):
            self._set_row_focus(target)
        else:
            self._set_button_focus(target)

    def _move_vertical(self, delta: int) -> None:
        if not self._tracks:
            self._set_button_focus(
                "stop" if delta < 0 and self.stop_btn.enabled else "close"
            )
            return
        if self._focus_zone != "rows":
            if delta > 0:
                self._set_row_focus(0)
            elif self._focus_zone == "stop" or not self.stop_btn.enabled:
                self._set_button_focus("close")
            else:
                self._set_button_focus("stop")
            return
        target = self._focus + delta
        if target < 0:
            self._set_button_focus(
                "stop" if self.stop_btn.enabled else "close"
            )
        elif target >= len(self._tracks):
            self._set_button_focus("close")
        else:
            self._set_row_focus(target)

    def _move_horizontal(self, delta: int) -> None:
        if self._focus_zone == "stop" and delta > 0:
            self._set_button_focus("close")
        elif self._focus_zone == "close" and delta < 0:
            self._set_button_focus("stop")
        else:
            self._page(delta)

    def _page(self, direction: int) -> None:
        if not self._tracks:
            return
        if self._focus_zone != "rows":
            self._set_row_focus(0 if direction > 0 else len(self._tracks) - 1)
            return
        stride = max(
            1,
            self._scroll.rect.height // max(1, self._row_h + self._row_gap) - 1,
        )
        self._set_row_focus(
            max(0, min(len(self._tracks) - 1, self._focus + direction * stride)),
        )

    def _focus_near_viewport(self, *, bottom: bool) -> None:
        """Keep confirm bound to a row the player can currently see."""
        if not self._tracks:
            return
        stride = max(1, self._row_h + self._row_gap)
        content_y = self._scroll.scroll_y + (
            self._scroll.rect.height - 1 if bottom else 0
        )
        index = max(0, min(len(self._tracks) - 1, content_y // stride))
        self._set_row_focus(index, ensure=False)

    def _ensure_focus_visible(self) -> None:
        if self._focus_zone != "rows" or not 0 <= self._focus < len(self._tracks):
            self._scroll._clamp_scroll()
            return
        row = self._row_content_rects[self._focus]
        padding = self._sp(5)
        if row.top - padding < self._scroll.scroll_y:
            self._scroll.scroll_y = row.top - padding
        elif row.bottom + padding > self._scroll.scroll_y + self._scroll.rect.height:
            self._scroll.scroll_y = row.bottom + padding - self._scroll.rect.height
        self._scroll._clamp_scroll()

    def _index_at_pointer(self, point: tuple[int, int]) -> int | None:
        if not self._scroll.rect.collidepoint(point):
            return None
        content_point = (
            point[0] - self._scroll.rect.x,
            point[1] - self._scroll.rect.y + self._scroll.scroll_y,
        )
        for index, rect in enumerate(self._row_content_rects):
            if rect.collidepoint(content_point):
                return index
        return None

    def _activate_focus(self) -> bool:
        if self._focus_zone == "stop":
            return self.stop_btn.activate()
        if self._focus_zone == "close":
            return self.close_btn.activate()
        if 0 <= self._focus < len(self._tracks):
            path = self._tracks[self._focus]
            return self._play(path)
        return False

    def update(self, dt: float, inp) -> None:
        if getattr(inp, "cancel", False) or self._pressed(inp, InputAction.BACK):
            self._close()
            return

        pointer_click = bool(getattr(inp, "mouse_clicked", False))
        close_clicked = pointer_click and self.close_btn.rect.collidepoint(inp.mouse_pos)
        stop_clicked = pointer_click and self.stop_btn.rect.collidepoint(inp.mouse_pos)
        self._sync_focus_state()
        self.close_btn.update(dt, inp)
        if close_clicked:
            return
        self.stop_btn.update(dt, inp)
        if stop_clicked:
            return

        scroll_before = self._scroll.scroll_y
        self._scroll.update(dt, inp)
        if self._scroll.scroll_y != scroll_before:
            self._focus_near_viewport(
                bottom=self._scroll.scroll_y > scroll_before,
            )
        hovered = self._index_at_pointer(inp.mouse_pos)
        self._hover_index = hovered if hovered is not None else -1
        if self._pointer_moved(inp):
            if self.close_btn.rect.collidepoint(inp.mouse_pos):
                self._set_button_focus("close")
            elif self.stop_btn.rect.collidepoint(inp.mouse_pos):
                # A disabled control may explain itself on click, but pointer
                # motion must not steal keyboard/gamepad focus and redirect
                # the next Confirm to Close.
                if self.stop_btn.enabled:
                    self._set_button_focus("stop")
            elif hovered is not None:
                self._set_row_focus(hovered, ensure=False)

        if self._pressed(inp, InputAction.FOCUS_NEXT):
            self._cycle_focus(1)
        elif self._pressed(inp, InputAction.FOCUS_PREVIOUS):
            self._cycle_focus(-1)
        elif self._pressed(inp, InputAction.NAV_UP):
            self._move_vertical(-1)
        elif self._pressed(inp, InputAction.NAV_DOWN):
            self._move_vertical(1)
        elif self._pressed(inp, InputAction.PAGE_UP):
            self._page(-1)
        elif self._pressed(inp, InputAction.PAGE_DOWN):
            self._page(1)
        elif self._pressed(inp, InputAction.NAV_LEFT):
            self._move_horizontal(-1)
        elif self._pressed(inp, InputAction.NAV_RIGHT):
            self._move_horizontal(1)

        if self._pressed(inp, InputAction.CONFIRM_ADVANCE) or getattr(
            inp,
            "confirm",
            False,
        ):
            if self._activate_focus():
                return

        if pointer_click and hovered is not None:
            self._set_row_focus(hovered)
            self._play(self._tracks[hovered])

    # ----- final draw / inspection ------------------------------------

    def draw(self, surface: pygame.Surface) -> None:
        self._ensure_layout(surface.get_size())
        viewport = surface.get_rect()
        if not self._paint_chrome(
            surface,
            viewport,
            part="backdrop",
            variant="overlay",
        ):
            veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
            veil.fill((0, 0, 0, 168))
            surface.blit(veil, (0, 0))
        self._panel.draw(surface)
        surface.blit(self._title_surface, self._title_rect)
        surface.blit(self._counter_surface, self._counter_rect)

        divider_rect = pygame.Rect(
            self._panel_rect.x + self._sp(24),
            self._header_divider_y - self._sp(2),
            max(1, self._panel_rect.width - self._sp(48)),
            max(1, self._sp(4)),
        )
        if not self._paint_chrome(
            surface,
            divider_rect,
            part="divider",
            variant="header",
        ):
            pygame.draw.line(
                surface,
                self.ctx.theme.border_soft,
                divider_rect.midleft,
                divider_rect.midright,
            )
        self.stop_btn.draw(surface)
        self.close_btn.draw(surface)
        self._draw_viewport(surface)
        self._rebuild_screen_row_rects()

        if not self._paint_chrome(
            surface,
            self._footer_rect,
            part="footer",
            variant="hint",
        ):
            pygame.draw.line(
                surface,
                self.ctx.theme.border_soft,
                self._footer_rect.topleft,
                self._footer_rect.topright,
            )
        bindings = BindingProfile(
            getattr(self.ctx.config, "input_bindings", {}),
        )
        footer = self._status_message or self._footer_hint(bindings)
        footer_font = self.ctx.fonts.get(self._footer_font_size, bold=True)
        footer = self._ellipsize(
            footer,
            footer_font,
            max(1, self._footer_rect.width - self._sp(18)),
        )
        footer_color = (
            self.ctx.theme.warn
            if self._status_kind == "error"
            else self.ctx.theme.accent_warm
            if self._status_kind == "playing"
            else self.ctx.theme.text_mute
        )
        footer_surface = self.ctx.fonts.render(
            footer,
            self._footer_font_size,
            footer_color,
            bold=True,
        )
        footer_rect = footer_surface.get_rect(center=self._footer_rect.center)
        surface.blit(footer_surface, footer_rect)

    def describe(self) -> dict:
        unlocked_n = sum(self._is_unlocked(path) for path in self._tracks)
        return {
            "scene": "MusicRoomScene",
            "track_count": len(self._tracks),
            "unlocked_count": unlocked_n,
            "now_playing": self._now_playing(),
        }
