"""Product-grade dialogue history overlay.

The old implementation rendered the complete (up to 500-line) history into
one off-screen surface every frame.  At 4K that surface could approach a GiB
and a single draw could take more than a second.  This scene instead measures
rows once, keeps only scalar geometry, and paints the viewport plus one row of
overscan directly onto the destination surface.

Every row participates in the same mouse, touch, keyboard, and controller
focus model.  A voiced row can be replayed with pointer activation or the
semantic confirm action; the effective per-character volume is resolved from
the canonical speaker id when present and falls back safely for legacy saves.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import Any, Callable

import pygame

from .base import Scene, SceneContext
from ..ui.assets import AudioPlaybackSnapshot
from ..ui.input_bindings import BindingProfile, InputAction
from ..ui.system_chrome import ChromeRequest
from ..ui.widgets import Button, Panel
from ..ui.widgets.sound_caption import (
    SoundCaptionCue,
    normalize_sound_captions,
)


_MIN_TARGET = 44
_MAX_ENTRY_CHARS = 20_000
_OVERSCAN_ROWS = 1
_DESCRIBE_CAPTION_ROW_LIMIT = 32


def _clean_single_line(value: object, *, limit: int = 256) -> str:
    return " ".join(str(value or "").split())[:limit]


def _ellipsize(text: str, font: pygame.font.Font, width: int) -> str:
    value = _clean_single_line(text)
    if not value or width <= 0:
        return ""
    if font.size(value)[0] <= width:
        return value
    ellipsis = "…"
    if font.size(ellipsis)[0] > width:
        return ""
    lo, hi = 0, len(value)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.size(value[:mid] + ellipsis)[0] <= width:
            lo = mid
        else:
            hi = mid - 1
    return value[:lo].rstrip() + ellipsis


def _wrap_measured(
    value: object,
    font: pygame.font.Font,
    width: int,
) -> tuple[str, ...]:
    """Wrap CJK/Latin text in O(lines * log(chars)) measurement calls.

    The historical character-at-a-time implementation repeatedly measured an
    ever-growing prefix, which made opening a long history unnecessarily
    quadratic.  Explicit newlines remain visible as blank rows.
    """
    text = str(value or "")
    if len(text) > _MAX_ENTRY_CHARS:
        text = text[: _MAX_ENTRY_CHARS - 1] + "…"
    if width <= 0:
        return (text,)
    wrapped: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph:
            wrapped.append("")
            continue
        remaining = paragraph
        while remaining:
            if font.size(remaining)[0] <= width:
                wrapped.append(remaining)
                break
            lo, hi = 0, len(remaining)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if font.size(remaining[:mid])[0] <= width:
                    lo = mid
                else:
                    hi = mid - 1
            fit = max(1, lo)
            split = remaining.rfind(" ", 0, fit + 1)
            if split >= max(1, fit // 2):
                line = remaining[:split].rstrip()
                remaining = remaining[split + 1 :].lstrip()
            else:
                line = remaining[:fit]
                remaining = remaining[fit:]
            wrapped.append(line)
    return tuple(wrapped or ("",))


@dataclass(frozen=True, slots=True)
class _HistoryRow:
    index: int
    top: int
    height: int
    speaker: str
    speaker_id: str | None
    text_lines: tuple[str, ...]
    voice: str | None
    sound_captions: tuple[SoundCaptionCue, ...]
    caption_lines: tuple[tuple[str, ...], ...]

    @property
    def bottom(self) -> int:
        return self.top + self.height


class ScrollbackScene(Scene):
    """Virtualized, responsive, all-input dialogue history."""

    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        self.on_close: Callable[[], None] | None = None
        self._rows: list[_HistoryRow] = []
        self._row_tops: list[int] = []
        self._scroll_y = 0
        self._content_height = 0
        self._focus = -1
        self._focus_close = False
        self._hover = -1
        self._last_visible_range = (0, 0)
        self._last_drawn_rows = 0
        self._last_replay: dict[str, Any] | None = None
        self._touch_dragging = False
        self._touch_moved = False
        self._touch_distance = 0
        # Backlog voice replay shares the authored voice channel with the
        # dialogue underneath this overlay.  Capture that ownership before a
        # preview can replace it, then restore it on every teardown path.
        # ``None`` also acts as the restored sentinel, making close + the
        # subsequent SceneManager exit idempotent.
        self._audio_snapshot: AudioPlaybackSnapshot | None = None
        # The entry snapshot remains live only until backlog replay first
        # takes over Channel 0.  Refresh it immediately before that hand-off:
        # the underlying dialogue voice may have naturally completed while
        # the player was reading history, making the entry checkpoint stale.
        self._audio_preview_started = False

    # ------------------------------------------------------------------
    # Layout and row model

    def _sp(self, value: int) -> int:
        return max(1, int(round(value * self._density)))

    def _font_size(self, base: int, minimum: int) -> int:
        return max(minimum, int(round(base * min(1.0, self._density))))

    def _display_text(self, text: str) -> str:
        """Apply layout-audit expansion without changing history storage."""
        policy = getattr(self.ctx, "presentation_text", None)
        apply = getattr(policy, "apply", None)
        return apply(text) if callable(apply) else text

    def _bindings(self) -> BindingProfile:
        return BindingProfile(
            getattr(self.ctx.config, "input_bindings", {}) or {},
        )

    def enter(self, *, on_close=None, **_) -> None:
        # Reused/direct callers must not strand ownership from a previous
        # entry before taking a fresh snapshot.
        self._restore_audio()
        self.on_close = on_close
        self._audio_snapshot = self.ctx.assets.capture_audio_playback()
        self._audio_preview_started = False
        try:
            self._configure_layout()
            self._build_rows()
            if self._rows:
                self._focus = len(self._rows) - 1
                self._focus_close = False
                self._scroll_y = self._max_scroll()
            else:
                self._focus = -1
                self._focus_close = True
            self._sync_focus_state()
        except Exception:
            self._restore_audio()
            raise

    def exit(self) -> None:
        """Return voice ownership for pop/replace/clear teardown paths."""
        self._restore_audio()

    def _restore_audio(self) -> None:
        """Restore the entry audio snapshot at most once."""
        snapshot = self._audio_snapshot
        if snapshot is None:
            return
        # Clear first so a custom backend failure cannot make SceneManager
        # retry a partial restoration during ``exit``.
        self._audio_snapshot = None
        self._audio_preview_started = False
        self.ctx.assets.restore_audio_playback(snapshot)

    def _refresh_audio_before_preview(self) -> bool:
        """Freeze the latest authored audio state before the first replay.

        Scrollback does not replace voice on entry, so an authored clip keeps
        running underneath the overlay.  The checkpoint captured on entry can
        therefore be behind the live playback -- or claim ``playing`` after
        the clip naturally ended.  Capture once more at the exact ownership
        hand-off, then keep that snapshot stable across all backlog previews.
        """
        if self._audio_preview_started:
            return True
        if self._audio_snapshot is None:
            return False
        try:
            snapshot = self.ctx.assets.capture_audio_playback()
        except Exception:
            # Starting a preview without a trustworthy return point could
            # revive or overwrite authored audio on close.  Keep the overlay
            # usable, but decline this replay attempt.
            return False
        self._audio_snapshot = snapshot
        return True

    def resume(self) -> None:
        # Settings may change bindings or dialogue text scale while this scene
        # is retained by an embedding client.  Reflow without changing the
        # focused history identity.
        old_index = self._focus
        old_close = self._focus_close
        self._configure_layout()
        self._build_rows()
        self._focus = min(old_index, len(self._rows) - 1)
        self._focus_close = old_close or not self._rows
        self._ensure_focus_visible()
        self._sync_focus_state()

    def _configure_layout(self) -> None:
        sw, sh = (max(1, int(v)) for v in self.ctx.screen_size)
        self._density = max(0.65, sh / 720.0)
        text_scale = max(
            1.0,
            min(1.4, float(getattr(self.ctx.config, "text_scale", 1.0))),
        )
        self._title_size = self._font_size(
            self.ctx.config.font_size_header,
            24,
        )
        self._speaker_size = max(
            14,
            int(round(self._font_size(18, 14) * text_scale)),
        )
        self._body_size = max(
            14,
            int(round(self._font_size(18, 14) * text_scale)),
        )
        self._caption_size = max(
            12,
            int(round(self._font_size(16, 12) * text_scale)),
        )
        self._badge_size = self._font_size(12, 10)
        self._footer_size = self._font_size(13, 11)
        self._close_size = self._font_size(15, 13)

        title_h = self.ctx.fonts.get(
            self._title_size,
            bold=True,
        ).get_linesize()
        close_font = self.ctx.fonts.get(self._close_size, bold=True)
        close_label = (
            f"{self.ctx.t('close', '關閉')}　"
            f"{self._bindings().primary_display(InputAction.BACK)}"
        )
        close_h = max(
            _MIN_TARGET,
            close_font.get_linesize() + self._sp(14),
        )
        close_w = max(
            _MIN_TARGET,
            self._sp(120),
            close_font.size(close_label)[0] + self._sp(24),
        )

        margin_x = max(12, min(self._sp(80), sw // 12))
        margin_y = max(12, min(self._sp(40), sh // 14))
        self._panel_rect = pygame.Rect(
            margin_x,
            margin_y,
            max(1, sw - margin_x * 2),
            max(1, sh - margin_y * 2),
        )
        inner = max(14, self._sp(30))
        header_h = max(title_h, close_h) + self._sp(26)
        footer_h = max(
            _MIN_TARGET,
            self.ctx.fonts.get(self._footer_size).get_linesize()
            + self._sp(16),
        )
        self._header_rect = pygame.Rect(
            self._panel_rect.x + inner,
            self._panel_rect.y + self._sp(10),
            max(1, self._panel_rect.width - inner * 2),
            header_h,
        )
        self._close_rect = pygame.Rect(
            self._header_rect.right - min(close_w, self._header_rect.width),
            self._header_rect.y
            + (self._header_rect.height - close_h) // 2,
            min(close_w, self._header_rect.width),
            close_h,
        )
        self._title_rect = pygame.Rect(
            self._header_rect.x,
            self._header_rect.y,
            max(1, self._close_rect.x - self._header_rect.x - self._sp(12)),
            self._header_rect.height,
        )
        self._footer_rect = pygame.Rect(
            self._panel_rect.x + inner,
            self._panel_rect.bottom - self._sp(10) - footer_h,
            max(1, self._panel_rect.width - inner * 2),
            footer_h,
        )
        viewport_top = self._header_rect.bottom + self._sp(4)
        viewport_bottom = self._footer_rect.top - self._sp(4)
        self._viewport = pygame.Rect(
            self._panel_rect.x + inner,
            viewport_top,
            max(1, self._panel_rect.width - inner * 2),
            max(1, viewport_bottom - viewport_top),
        )

        self._panel = Panel(
            self._panel_rect,
            self.ctx.theme,
            fill=(*self.ctx.theme.bg_overlay[:3], 244),
            border=self.ctx.theme.border_strong,
            radius=self.ctx.theme.radius_l,
            border_width=max(1, self._sp(2)),
            chrome=self.ctx.ui_chrome,
            screen="history",
            role="main",
            density=self._density,
        )
        self.close_btn = Button(
            self._close_rect,
            close_label,
            fonts=self.ctx.fonts,
            theme=self.ctx.theme,
            font_size=self._close_size,
            style="ghost",
            on_click=self._close,
            chrome=self.ctx.ui_chrome,
            screen="history",
            role="close",
            motion=self.ctx.motion,
        )

        self._row_pad_x = max(12, self._sp(18))
        self._row_pad_y = max(8, self._sp(12))
        self._row_gap = max(4, self._sp(8))
        self._speaker_gap = max(2, self._sp(4))
        self._caption_section_gap = max(4, self._sp(7))
        self._caption_cue_gap = max(2, self._sp(3))
        self._body_line_h = self.ctx.fonts.get(
            self._body_size,
        ).get_linesize()
        self._speaker_line_h = self.ctx.fonts.get(
            self._speaker_size,
            bold=True,
        ).get_linesize()
        self._caption_line_h = self.ctx.fonts.get(
            self._caption_size,
            bold=True,
        ).get_linesize()
        self._row_min_h = max(_MIN_TARGET, self._sp(_MIN_TARGET))
        self._scroll_step = max(self._row_min_h, self._body_line_h * 3)

    def _build_rows(self) -> None:
        body_font = self.ctx.fonts.get(self._body_size)
        caption_font = self.ctx.fonts.get(self._caption_size, bold=True)
        text_width = max(1, self._viewport.width - self._row_pad_x * 2)
        rows: list[_HistoryRow] = []
        tops: list[int] = []
        top = 0
        raw_history = self.ctx.state.dialogue_history.lines
        for index, raw in enumerate(raw_history):
            entry = raw if isinstance(raw, dict) else {}
            speaker = _clean_single_line(entry.get("speaker"))
            raw_speaker_id = entry.get("speaker_id")
            speaker_id = (
                _clean_single_line(raw_speaker_id)
                if isinstance(raw_speaker_id, str) and raw_speaker_id.strip()
                else None
            )
            voice_raw = entry.get("voice")
            voice = (
                str(voice_raw)
                if isinstance(voice_raw, str) and voice_raw.strip()
                else None
            )
            text_lines = _wrap_measured(
                self._display_text(str(entry.get("text", "") or "")),
                body_font,
                text_width,
            )
            sound_captions = (
                normalize_sound_captions(
                    entry.get("sound_captions", ()),
                    self.ctx.localization,
                )
                if getattr(
                    self.ctx.config,
                    "show_sound_captions",
                    True,
                ) is True
                else ()
            )
            caption_lines = tuple(
                _wrap_measured(
                    self._display_text(cue.display_text),
                    caption_font,
                    text_width,
                )
                for cue in sound_captions
            )
            meta_h = self._speaker_line_h if (speaker or voice) else 0
            if meta_h:
                meta_h += self._speaker_gap
            body_h = max(1, len(text_lines)) * self._body_line_h
            captions_h = 0
            if caption_lines:
                captions_h = (
                    self._caption_section_gap
                    + sum(
                        len(lines) * self._caption_line_h
                        for lines in caption_lines
                    )
                    + self._caption_cue_gap * (len(caption_lines) - 1)
                )
            height = max(
                self._row_min_h,
                self._row_pad_y * 2 + meta_h + body_h + captions_h,
            )
            tops.append(top)
            rows.append(
                _HistoryRow(
                    index=index,
                    top=top,
                    height=height,
                    speaker=speaker,
                    speaker_id=speaker_id,
                    text_lines=text_lines,
                    voice=voice,
                    sound_captions=sound_captions,
                    caption_lines=caption_lines,
                )
            )
            top += height + self._row_gap
        self._rows = rows
        self._row_tops = tops
        self._content_height = max(0, top - (self._row_gap if rows else 0))
        self._scroll_y = min(self._scroll_y, self._max_scroll())

    # ------------------------------------------------------------------
    # Focus, scrolling, replay

    def _row_text_bands(
        self,
        row: _HistoryRow,
    ) -> tuple[tuple[int, int], ...]:
        """Return content-space bands occupied by complete text lines.

        A pixel scroll may legitimately expose only part of a card, but it
        must not leave half of a speaker label or glyph row at the viewport
        edge.  Keeping this geometry in one place lets initial bottom
        alignment and painting agree for named, narrated, voiced, captioned,
        and explicitly blank lines.
        """
        bands: list[tuple[int, int]] = []
        y = row.top + self._row_pad_y
        if row.speaker or row.voice:
            bands.append((y, y + self._speaker_line_h))
            y += self._speaker_line_h + self._speaker_gap
        for _line in row.text_lines:
            bands.append((y, y + self._body_line_h))
            y += self._body_line_h
        if row.caption_lines:
            y += self._caption_section_gap
            for cue_index, lines in enumerate(row.caption_lines):
                for _line in lines:
                    bands.append((y, y + self._caption_line_h))
                    y += self._caption_line_h
                if cue_index + 1 < len(row.caption_lines):
                    y += self._caption_cue_gap
        return tuple(bands)

    def _max_scroll(self) -> int:
        natural_bottom = max(
            0,
            self._content_height - self._viewport.height,
        )
        if natural_bottom <= 0 or not self._rows:
            return natural_bottom

        # Bottom alignment is the correct default for a dialogue backlog, but
        # an arbitrary content height can put the viewport edge through the
        # middle of a glyph row.  Advance by at most one line so the latest
        # entry remains fully visible with only a small breathing-space gutter
        # below it.  This avoids both clipped text and a potentially huge blank
        # area from snapping to the next whole card.
        index = max(0, bisect_right(self._row_tops, natural_bottom) - 1)
        while (
            index < len(self._rows)
            and self._rows[index].bottom <= natural_bottom
        ):
            index += 1
        if index < len(self._rows):
            for top, bottom in self._row_text_bands(self._rows[index]):
                if top < natural_bottom < bottom:
                    return bottom
        return natural_bottom

    def _clamp_scroll(self) -> None:
        self._scroll_y = max(0, min(int(self._scroll_y), self._max_scroll()))

    def _close(self) -> None:
        self._restore_audio()
        if self.on_close is not None:
            self.on_close()

    @staticmethod
    def _pressed(inp, action: str) -> bool:
        pressed = getattr(inp, "pressed", None)
        return bool(callable(pressed) and pressed(action))

    def _sync_focus_state(self) -> None:
        self.close_btn.focused = self._focus_close

    def _move_focus(self, delta: int) -> None:
        if not self._rows:
            self._focus = -1
            self._focus_close = True
            self._sync_focus_state()
            return
        self._focus_close = False
        if not 0 <= self._focus < len(self._rows):
            self._focus = len(self._rows) - 1 if delta < 0 else 0
        else:
            self._focus = max(0, min(len(self._rows) - 1, self._focus + delta))
        self._ensure_focus_visible()
        self._sync_focus_state()

    def _ensure_focus_visible(self) -> None:
        if not 0 <= self._focus < len(self._rows):
            self._clamp_scroll()
            return
        row = self._rows[self._focus]
        pad = self._sp(6)
        if row.top - pad < self._scroll_y:
            self._scroll_y = row.top - pad
        elif row.bottom + pad > self._scroll_y + self._viewport.height:
            self._scroll_y = row.bottom + pad - self._viewport.height
        self._clamp_scroll()

    def _index_at_content_y(self, content_y: int) -> int:
        if not self._rows or content_y < 0:
            return -1
        index = bisect_right(self._row_tops, int(content_y)) - 1
        if 0 <= index < len(self._rows):
            row = self._rows[index]
            if row.top <= content_y < row.bottom:
                return index
        return -1

    def _index_at_pointer(self, point: tuple[int, int]) -> int:
        if not self._viewport.collidepoint(point):
            return -1
        return self._index_at_content_y(
            point[1] - self._viewport.y + self._scroll_y,
        )

    def _focus_near_viewport(self, *, bottom: bool) -> None:
        if not self._rows:
            return
        content_y = self._scroll_y + (
            self._viewport.height - 1 if bottom else 0
        )
        index = self._index_at_content_y(content_y)
        if index < 0:
            index = min(
                len(self._rows) - 1,
                max(0, bisect_right(self._row_tops, content_y) - 1),
            )
        self._focus = index
        self._focus_close = False
        self._ensure_focus_visible()
        self._sync_focus_state()

    def _page(self, direction: int) -> None:
        amount = max(self._row_min_h, int(self._viewport.height * 0.86))
        self._scroll_y += amount * direction
        self._clamp_scroll()
        self._focus_near_viewport(bottom=direction > 0)

    def _speaker_id_for(self, row: _HistoryRow) -> str | None:
        if row.speaker_id:
            npc = self.ctx.npcs.get(row.speaker_id)
            if npc is not None:
                return npc.id
        if row.speaker:
            npc = self.ctx.npcs.by_name(row.speaker)
            if npc is not None:
                return npc.id
        return None

    def _replay(self, index: int) -> bool:
        if not 0 <= index < len(self._rows):
            return False
        row = self._rows[index]
        if not row.voice:
            return False
        if not self._refresh_audio_before_preview():
            return False
        speaker_id = self._speaker_id_for(row)
        overrides = getattr(
            self.ctx.config,
            "per_character_voice_volume",
            {},
        ) or {}
        volume = float(
            overrides.get(speaker_id, self.ctx.config.voice_volume)
            if speaker_id is not None
            else self.ctx.config.voice_volume
        )
        volume = max(0.0, min(1.0, volume))
        try:
            handle = self.ctx.assets.play_voice(row.voice, volume=volume)
        except Exception:
            return False
        if hasattr(self.ctx.assets, "current_voice_handle") and handle is None:
            # Stock AssetManager returns None when the decoder/backend failed.
            # No ownership hand-off occurred, so a later valid preview must
            # refresh the still-running authored voice checkpoint again.
            return False
        self._audio_preview_started = True
        self._last_replay = {
            "index": index,
            "speaker": row.speaker or None,
            "speaker_id": speaker_id,
            "voice": row.voice,
            "volume": volume,
        }
        return True

    # ------------------------------------------------------------------
    # Input

    def update(self, dt: float, inp) -> None:
        if getattr(inp, "cancel", False) or self._pressed(inp, InputAction.BACK):
            self._close()
            return

        self._hover = self._index_at_pointer(inp.mouse_pos)
        if self._pressed(inp, InputAction.FOCUS_NEXT):
            self._focus_close = True if not self._rows else not self._focus_close
            if not self._focus_close and self._rows and self._focus < 0:
                self._focus = len(self._rows) - 1
                self._ensure_focus_visible()
            self._sync_focus_state()
        elif self._pressed(inp, InputAction.FOCUS_PREVIOUS):
            self._focus_close = True if not self._rows else not self._focus_close
            if not self._focus_close and self._rows and self._focus < 0:
                self._focus = len(self._rows) - 1
                self._ensure_focus_visible()
            self._sync_focus_state()

        if self._pressed(inp, InputAction.NAV_UP):
            self._move_focus(-1)
        elif self._pressed(inp, InputAction.NAV_DOWN):
            self._move_focus(1)
        elif self._pressed(inp, InputAction.PAGE_UP) or self._pressed(
            inp,
            InputAction.NAV_LEFT,
        ):
            self._page(-1)
        elif self._pressed(inp, InputAction.PAGE_DOWN) or self._pressed(
            inp,
            InputAction.NAV_RIGHT,
        ):
            self._page(1)

        if self._pressed(inp, InputAction.CONFIRM_ADVANCE) or getattr(
            inp,
            "confirm",
            False,
        ):
            if self._focus_close:
                self.close_btn.activate()
                return
            self._replay(self._focus)

        wheel = int(getattr(inp, "mouse_wheel", 0) or 0)
        if wheel and self._viewport.collidepoint(inp.mouse_pos):
            self._scroll_y -= wheel * self._scroll_step
            self._clamp_scroll()

        if getattr(inp, "touch_started", False):
            self._touch_dragging = self._viewport.collidepoint(inp.mouse_pos)
            self._touch_moved = False
            self._touch_distance = 0
        if self._touch_dragging:
            delta_y = int(getattr(inp, "touch_delta", (0, 0))[1])
            if delta_y:
                self._scroll_y -= delta_y
                self._touch_distance += abs(delta_y)
                self._touch_moved = self._touch_distance > self._sp(10)
                self._clamp_scroll()
        touch_released = bool(getattr(inp, "touch_released", False))
        if touch_released and self._touch_dragging:
            if not self._touch_moved:
                index = self._index_at_pointer(inp.mouse_pos)
                if index >= 0:
                    self._focus = index
                    self._focus_close = False
                    self._replay(index)
                    self._sync_focus_state()
            self._touch_dragging = False

        # Runtime touch taps normally also surface as mouse_clicked.  Suppress
        # that duplicate when this frame completed a captured touch gesture.
        pointer_click = bool(getattr(inp, "mouse_clicked", False))
        if pointer_click and not touch_released:
            index = self._index_at_pointer(inp.mouse_pos)
            if index >= 0:
                self._focus = index
                self._focus_close = False
                self._replay(index)
                self._sync_focus_state()

        self.close_btn.update(dt, inp)

    # ------------------------------------------------------------------
    # Drawing

    def _paint_chrome(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        *,
        part: str,
        variant: str,
        hovered: bool = False,
        focused: bool = False,
        selected: bool = False,
        emphasis: int = 0,
    ) -> bool:
        chrome = self.ctx.ui_chrome
        if chrome is None or rect.width <= 0 or rect.height <= 0:
            return False
        return chrome.paint(
            surface,
            rect,
            ChromeRequest(
                part=part,
                screen="history",
                variant=variant,
                size=rect.size,
                density=self._density,
                hovered=hovered,
                focused=focused,
                selected=selected,
                emphasis=max(0, min(3, int(emphasis))),
            ),
        )

    def _visible_range(self) -> tuple[int, int]:
        if not self._rows:
            return (0, 0)
        first = max(0, bisect_right(self._row_tops, self._scroll_y) - 1)
        while first < len(self._rows) and self._rows[first].bottom <= self._scroll_y:
            first += 1
        first = max(0, first - _OVERSCAN_ROWS)
        limit = self._scroll_y + self._viewport.height
        last = first
        while last < len(self._rows) and self._rows[last].top < limit:
            last += 1
        last = min(len(self._rows), last + _OVERSCAN_ROWS)
        return first, last

    def _row_screen_rect(self, row: _HistoryRow) -> pygame.Rect:
        return pygame.Rect(
            self._viewport.x,
            self._viewport.y + row.top - self._scroll_y,
            self._viewport.width - max(0, self._sp(10)),
            row.height,
        )

    def _draw_row(self, surface: pygame.Surface, row: _HistoryRow) -> None:
        rect = self._row_screen_rect(row)
        visible = rect.clip(self._viewport)
        if visible.width <= 0 or visible.height <= 0:
            return
        focused = row.index == self._focus and not self._focus_close
        hovered = row.index == self._hover
        emphasis = 3 if focused else 2 if hovered else 0
        variant = "row:voice" if row.voice else "row:plain"
        if not self._paint_chrome(
            surface,
            visible,
            part="card",
            variant=variant,
            hovered=hovered,
            focused=focused,
            selected=focused,
            emphasis=emphasis,
        ):
            card = pygame.Surface(visible.size, pygame.SRCALPHA)
            alpha = 202 if focused else 178 if hovered else 142
            card.fill((*self.ctx.theme.bg_panel[:3], alpha))
            surface.blit(card, visible.topleft)
            pygame.draw.line(
                surface,
                self.ctx.theme.border_soft,
                visible.bottomleft,
                visible.bottomright,
                width=max(1, self._sp(1)),
            )

        if focused:
            self._paint_chrome(
                surface,
                visible,
                part="focus",
                variant=variant,
                hovered=hovered,
                focused=True,
                selected=True,
                emphasis=3,
            )
            # Core-owned and immediate: accessibility never depends on a pack
            # renderer or on an animated hover state.
            pygame.draw.rect(
                surface,
                self.ctx.theme.accent_warm,
                rect,
                width=max(3, self._sp(3)),
                border_radius=max(2, self._sp(8)),
            )

        clip = self._viewport
        x = rect.x + self._row_pad_x
        y = rect.y + self._row_pad_y
        right = rect.right - self._row_pad_x
        if row.speaker or row.voice:
            badge_w = 0
            meta_visible = (
                y >= clip.top
                and y + self._speaker_line_h <= clip.bottom
            )
            if row.voice and meta_visible:
                badge_font = self.ctx.fonts.get(self._badge_size, bold=True)
                badge_text = self.ctx.t("voice_replay", "VOICE")
                badge_w = badge_font.size(badge_text)[0] + self._sp(14)
                badge_rect = pygame.Rect(
                    x,
                    y,
                    badge_w,
                    self._speaker_line_h,
                )
                pygame.draw.rect(
                    surface,
                    (*self.ctx.theme.accent_alt[:3], 92),
                    badge_rect,
                    border_radius=max(2, self._sp(4)),
                )
                badge = self.ctx.fonts.render(
                    badge_text,
                    self._badge_size,
                    self.ctx.theme.text,
                    bold=True,
                )
                surface.blit(
                    badge,
                    (
                        badge_rect.centerx - badge.get_width() // 2,
                        badge_rect.centery - badge.get_height() // 2,
                    ),
                )
            if row.speaker and meta_visible:
                speaker_x = x + (badge_w + self._sp(10) if badge_w else 0)
                speaker_font = self.ctx.fonts.get(self._speaker_size, bold=True)
                speaker = _ellipsize(
                    self._display_text(row.speaker),
                    speaker_font,
                    max(1, right - speaker_x),
                )
                if speaker:
                    speaker_surface = self.ctx.fonts.render(
                        speaker,
                        self._speaker_size,
                        self.ctx.theme.accent,
                        bold=True,
                    )
                    surface.blit(speaker_surface, (speaker_x, y))
            y += self._speaker_line_h + self._speaker_gap

        body_font = self.ctx.fonts.get(self._body_size)
        for line in row.text_lines:
            if (
                y >= clip.top
                and y + self._body_line_h <= clip.bottom
                and line
            ):
                text_surface = body_font.render(line, True, self.ctx.theme.text)
                surface.blit(text_surface, (x, y))
            y += self._body_line_h

        if row.caption_lines:
            y += self._caption_section_gap
            caption_font = self.ctx.fonts.get(
                self._caption_size,
                bold=True,
            )
            for cue_index, lines in enumerate(row.caption_lines):
                for line in lines:
                    if (
                        y >= clip.top
                        and y + self._caption_line_h <= clip.bottom
                        and line
                    ):
                        caption = caption_font.render(
                            line,
                            True,
                            self.ctx.theme.accent_warm,
                        )
                        surface.blit(caption, (x, y))
                    y += self._caption_line_h
                if cue_index + 1 < len(row.caption_lines):
                    y += self._caption_cue_gap

    def _draw_scrollbar(self, surface: pygame.Surface) -> None:
        if self._content_height <= self._viewport.height:
            return
        track_w = max(4, self._sp(4))
        track = pygame.Rect(
            self._viewport.right - track_w,
            self._viewport.y,
            track_w,
            self._viewport.height,
        )
        pygame.draw.rect(
            surface,
            (*self.ctx.theme.border_soft[:3], 100),
            track,
            border_radius=max(1, track_w // 2),
        )
        scroll_extent_height = self._max_scroll() + self._viewport.height
        knob_h = max(
            self._sp(28),
            int(
                track.height
                * self._viewport.height
                / scroll_extent_height
            ),
        )
        knob_h = min(track.height, knob_h)
        travel = max(0, track.height - knob_h)
        knob_y = (
            int(self._scroll_y / self._max_scroll() * travel)
            if self._max_scroll() > 0
            else 0
        )
        pygame.draw.rect(
            surface,
            self.ctx.theme.accent,
            (track.x, track.y + knob_y, track.width, knob_h),
            border_radius=max(1, track_w // 2),
        )

    def _draw_footer(self, surface: pygame.Surface) -> None:
        self._paint_chrome(
            surface,
            self._footer_rect,
            part="footer",
            variant="navigation",
        )
        profile = self._bindings()
        hints = [
            f"{profile.primary_display(InputAction.NAV_UP)}／"
            f"{profile.primary_display(InputAction.NAV_DOWN)} 選擇",
            f"{profile.primary_display(InputAction.PAGE_UP)}／"
            f"{profile.primary_display(InputAction.PAGE_DOWN)} 翻頁",
        ]
        if any(row.voice for row in self._rows):
            hints.append(
                f"{profile.primary_display(InputAction.CONFIRM_ADVANCE)} "
                "重播語音",
            )
        hints.append(f"{profile.primary_display(InputAction.BACK)} 關閉")
        status = self._display_text("　".join(hints))
        font = self.ctx.fonts.get(self._footer_size)
        status = _ellipsize(status, font, self._footer_rect.width - self._sp(12))
        text = self.ctx.fonts.render(
            status,
            self._footer_size,
            self.ctx.theme.text_mute,
        )
        surface.blit(
            text,
            (
                self._footer_rect.x + self._sp(6),
                self._footer_rect.centery - text.get_height() // 2,
            ),
        )

    def draw(self, surface: pygame.Surface) -> None:
        full = surface.get_rect()
        if not self._paint_chrome(
            surface,
            full,
            part="backdrop",
            variant="overlay",
        ):
            veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
            veil.fill((0, 0, 0, 176))
            surface.blit(veil, (0, 0))
        self._panel.draw(surface)

        title = self.ctx.fonts.render(
            self.ctx.t("dialogue_history", "對話回顧"),
            self._title_size,
            self.ctx.theme.accent,
            bold=True,
        )
        title_y = self._title_rect.centery - title.get_height() // 2
        surface.blit(title, (self._title_rect.x, title_y))
        self.close_btn.draw(surface)

        old_clip = surface.get_clip()
        surface.set_clip(self._viewport.clip(old_clip))
        first, last = self._visible_range()
        self._last_visible_range = (first, last)
        self._last_drawn_rows = 0
        for row in self._rows[first:last]:
            self._draw_row(surface, row)
            self._last_drawn_rows += 1
        if not self._rows:
            empty = self.ctx.fonts.render(
                self.ctx.t("history_empty", "（還沒有任何對話。）"),
                self._body_size,
                self.ctx.theme.text_mute,
            )
            surface.blit(
                empty,
                (
                    self._viewport.centerx - empty.get_width() // 2,
                    self._viewport.centery - empty.get_height() // 2,
                ),
            )
        surface.set_clip(old_clip)
        self._draw_scrollbar(surface)
        self._draw_footer(surface)

    def describe(self) -> dict:
        replayable = sum(1 for row in self._rows if row.voice)
        first, last = self._last_visible_range
        captioned_rows = sum(1 for row in self._rows if row.sound_captions)

        def caption_payload(row: _HistoryRow) -> list[dict[str, str]]:
            return [
                {"kind": cue.kind, "text": cue.text}
                for cue in row.sound_captions
            ]

        visible_caption_rows = [
            row
            for row in self._rows[first:last]
            if (
                row.sound_captions
                and self._row_screen_rect(row).colliderect(self._viewport)
            )
        ]
        visible_caption_rows_truncated = (
            len(visible_caption_rows) > _DESCRIBE_CAPTION_ROW_LIMIT
        )
        visible_caption_rows = visible_caption_rows[
            :_DESCRIBE_CAPTION_ROW_LIMIT
        ]
        focused_row = (
            self._rows[self._focus]
            if not self._focus_close and 0 <= self._focus < len(self._rows)
            else None
        )
        return {
            "scene": "ScrollbackScene",
            "lines": len(self._rows),
            "replayable": replayable,
            "sound_captions_enabled": getattr(
                self.ctx.config,
                "show_sound_captions",
                True,
            ) is True,
            "captioned_rows": captioned_rows,
            "visible_caption_rows": [
                {
                    "index": row.index,
                    "captions": caption_payload(row),
                }
                for row in visible_caption_rows
            ],
            "visible_caption_rows_truncated": (
                visible_caption_rows_truncated
            ),
            "focused_sound_captions": (
                caption_payload(focused_row) if focused_row is not None else []
            ),
            "focused_index": None if self._focus_close else self._focus,
            "focus": "close" if self._focus_close else "history_row",
            "visible_range": [first, last],
            "drawn_rows": self._last_drawn_rows,
            "content_height": self._content_height,
            "viewport_height": self._viewport.height,
            "scroll_y": self._scroll_y,
            "last_replay": self._last_replay,
        }
