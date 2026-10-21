"""Save & Load overlay.

A unified screen with a scrollable list of slots; clicking a row's action
loads (load mode) or overwrites (save mode), clicking "New Save" creates a
fresh slot. Slots are grouped: the quicksave and autosave slots are pinned
to the top (visually tinted and tagged), with manual slots below them
newest-first.

Layout / hit-testing:
- The slot list lives inside a :class:`ScrollArea` so an arbitrary number of
  slots stays usable. The content drawer paints each card onto an offscreen
  buffer and, mirroring ``shop_scene``, records each card's *absolute*
  on-screen rect (accounting for the current ``scroll_y``) into
  ``self._row_rects`` as ``(rect, item)``. Clicks are matched against those
  rects in :meth:`update`. We do not use per-row ``Button`` widgets because a
  scrolled card moves every frame; a manual hit-test is the clean fit for a
  ScrollArea-hosted list.
- Every existing slot has a separate delete control. Autosave slots keep their
  read-only primary control in save mode but can still be explicitly deleted.
- All geometry is reflowed from canvas density and the selected font's real
  ``get_linesize()``.  This matters at 1080p, where ``FontRegistry`` scales
  glyphs by 1.5: fixed 720p card heights would otherwise let the title,
  summary, timestamp and badges collide.
- Keyboard/controller focus walks rows vertically and actions horizontally,
  paints a persistent high-contrast ring, and scrolls the focused row fully
  into view. Moving to another row always restores the safe primary action.

Special slots:
- ``quicksave`` and ``autosave_*`` are tagged with a badge and a distinct
  tint so they read apart from manual saves. Both are loadable in load mode.
- In save mode, autosave slots are read-only (no overwrite). Quicksave is
  overwritable from the menu by design: it is a user-driven slot, so manual
  overwrite from the save screen stays consistent with the quicksave action.

Thumbnail handling:
- On save, we capture the current screen via a get_screen_surface callback
  (optional; degrades gracefully to no thumbnail if unavailable).
- On load, thumbnails are displayed at 120x68 to the left of each row.
- Saving over an occupied slot and loading any occupied slot open a modal
  confirmation layer. The conservative default is Cancel; a synthetic empty
  slot still saves immediately.
- Delete has its own danger confirmation with the same Cancel-first default.
  Failed deletes leave the list and focused row intact.
- Save/load failures and successful saves are shown as persistent inline text
  instead of crashing or disappearing during list refresh.
"""
from __future__ import annotations

from typing import Callable
import time

import pygame

from .base import Scene, SceneContext
from ..ui.input_bindings import BindingProfile, InputAction
from ..ui.system_chrome import ChromePart, ChromeRequest
from ..ui.widgets import Button, Panel, ScrollArea
from ..core.save_manager import SaveManager
from ..core.game_state import GameState
from ..core.endings import (
    EndingTracker,
    rebind_authored_ending_catalog,
)
from ..core.map_system import MapSystem, rebind_authored_map_catalog
from ..core.cg_gallery_spec import CGGalleryManifest
from ..core.music_room_spec import MusicRoomManifest
from ..core.media_authority import (
    MediaAuthority,
    build_media_authority,
    rebind_authorized_media_progress,
)
from ..core.story_graph import (
    StoryGraph,
    rebind_authored_audio_meta,
    rebind_authored_dialogue_history_voices,
    rebind_authored_story_catalog,
)

# 720p design-baseline dimensions. Instance geometry is derived from these
# values plus measured font linesizes; none of these are used as fixed row
# geometry at runtime.
_THUMB_W = 120
_THUMB_H = 68
_ACTION_W = 100
_DELETE_W = 76
_MIN_TARGET = 44
_PRIMARY_ACTION = "primary"
_DELETE_ACTION = "delete"


def _ellipsized(text: object, font: pygame.font.Font, max_width: int) -> str:
    """Return a measured single-line label that cannot escape its column."""
    value = " ".join(str(text or "").split())
    max_width = max(0, int(max_width))
    if not value or max_width <= 0:
        return ""
    if font.size(value)[0] <= max_width:
        return value
    ellipsis = "…"
    if font.size(ellipsis)[0] > max_width:
        return ""
    low, high = 0, len(value)
    while low < high:
        mid = (low + high + 1) // 2
        if font.size(value[:mid] + ellipsis)[0] <= max_width:
            low = mid
        else:
            high = mid - 1
    return value[:low].rstrip() + ellipsis


def _wrapped_lines(
    text: object,
    font: pygame.font.Font,
    max_width: int,
    *,
    max_lines: int,
) -> list[str]:
    """Measure CJK/Latin text into bounded lines, ellipsizing the final row."""
    remaining = " ".join(str(text or "").split())
    if not remaining or max_width <= 0 or max_lines <= 0:
        return []
    lines: list[str] = []
    while remaining and len(lines) < max_lines:
        if font.size(remaining)[0] <= max_width:
            lines.append(remaining)
            break
        if len(lines) == max_lines - 1:
            lines.append(_ellipsized(remaining, font, max_width))
            break
        fit = 0
        low, high = 0, len(remaining)
        while low < high:
            mid = (low + high + 1) // 2
            if font.size(remaining[:mid])[0] <= max_width:
                low = mid
            else:
                high = mid - 1
        fit = low
        if fit <= 0:
            break
        # Prefer a nearby word boundary for Latin text without producing very
        # short lines; CJK naturally falls back to the measured character cut.
        split = remaining.rfind(" ", 0, fit + 1)
        if split >= max(1, fit // 2):
            line = remaining[:split].rstrip()
            remaining = remaining[split + 1:].lstrip()
        else:
            line = remaining[:fit].rstrip()
            remaining = remaining[fit:].lstrip()
        if line:
            lines.append(line)
    return lines


class SaveScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        # Optional callback; caller may leave this as None.
        self._get_screen: Callable[[], pygame.Surface] | None = None
        self._get_resume: Callable[[], dict | None] | None = None

    def enter(
        self,
        *,
        mode: str = "save",
        on_close: Callable[[], None] | None = None,
        get_screen_surface: Callable[[], pygame.Surface] | None = None,
        get_resume_context: Callable[[], dict | None] | None = None,
        **_,
    ) -> None:
        self.mode = mode   # "save" or "load"
        self.on_close = on_close
        self._get_screen = get_screen_surface
        self._get_resume = get_resume_context
        self._error_msg: str | None = None
        self._status_msg: str | None = None
        self._status_kind = "hint"  # hint | success | error
        self._layout_size: tuple[int, int] | None = None
        self._focused_index = -1
        self._focused_action = _PRIMARY_ACTION
        self._hovered_index = -1
        self._hovered_action: str | None = None
        self._pending_item: dict | None = None
        self._pending_action = _PRIMARY_ACTION
        self._confirm_focus = 1  # conservative default: Cancel
        self._confirm_buttons: list[Button] = []
        self._row_rects: list[tuple[pygame.Rect, dict]] = []
        self._delete_rects: list[tuple[pygame.Rect, dict]] = []
        self._card_regions: list[dict] = []
        # Thumbnail cache keyed by slot. Reflow clears it because the target
        # thumbnail size changes with canvas density.
        self._thumb_cache: dict[str, pygame.Surface | None] = {}

        self.sm = SaveManager(self.ctx.config.save_dir())
        self._configure_layout(self.ctx.screen_size)
        self._scroll = ScrollArea(
            self._scroll_rect,
            fonts=self.ctx.fonts, theme=self.ctx.theme,
        )
        self._scroll.set_drawer(self._draw_content)
        self._refresh()

    # ----- responsive layout ------------------------------------------------

    def _configure_layout(self, canvas_size: tuple[int, int]) -> None:
        """Measure the complete overlay for ``canvas_size``.

        ``FontRegistry`` may itself scale glyphs for a high-density canvas, so
        container heights are based on real font linesizes rather than point
        sizes. On sub-720p canvases we reduce requested point sizes first,
        because the registry deliberately never scales typography below 1.0.
        """
        sw, sh = (max(1, int(canvas_size[0])), max(1, int(canvas_size[1])))
        previous_size = self._layout_size
        self._layout_size = (sw, sh)
        self._density = max(0.65, sh / 720.0)
        compact = min(1.0, self._density)

        def _size(base: int, minimum: int) -> int:
            return max(minimum, int(round(base * compact)))

        self._title_font_size = _size(
            self.ctx.config.font_size_header, 24,
        )
        self._close_font_size = _size(15, 13)
        self._label_font_size = _size(20, 15)
        self._summary_font_size = _size(14, 12)
        self._time_font_size = _size(13, 11)
        self._tag_font_size = _size(13, 11)
        self._action_font_size = _size(14, 12)
        self._status_font_size = _size(15, 12)
        self._confirm_eyebrow_size = _size(13, 11)
        self._confirm_title_size = _size(24, 18)
        self._confirm_body_size = _size(15, 12)
        self._confirm_button_size = _size(15, 13)

        title_h = self.ctx.fonts.get(
            self._title_font_size, bold=True,
        ).get_linesize()
        close_font = self.ctx.fonts.get(
            self._close_font_size, bold=True,
        )
        close_h = max(
            _MIN_TARGET,
            close_font.get_linesize() + max(12, int(round(14 * self._density))),
        )
        close_label = self.ctx.localization.t("close", "關閉")
        close_w = max(
            _MIN_TARGET,
            int(round(112 * self._density)),
            close_font.size(close_label)[0]
            + max(24, int(round(30 * self._density))),
        )

        margin_x = max(12, int(round(sw * 0.083)))
        margin_y = max(12, int(round(40 * self._density)))
        # Preserve useful list width on compact canvases while keeping the
        # large-screen overlay visually framed rather than edge-to-edge.
        panel_w = max(1, sw - margin_x * 2)
        panel_h = max(1, sh - margin_y * 2)
        self._panel_rect = pygame.Rect(
            (sw - panel_w) // 2,
            (sh - panel_h) // 2,
            panel_w,
            panel_h,
        )
        panel_pad_x = max(12, int(round(30 * self._density)))
        header_pad_y = max(10, int(round(16 * self._density)))
        self._header_h = (
            max(title_h, close_h)
            + header_pad_y * 2
            + max(1, int(round(4 * self._density)))
        )
        status_line_h = self.ctx.fonts.get(
            self._status_font_size, bold=True,
        ).get_linesize()
        self._status_h = (
            status_line_h + max(14, int(round(18 * self._density)))
        )

        self._panel = Panel(
            self._panel_rect, self.ctx.theme,
            fill=(*self.ctx.theme.bg_overlay[:3], 240),
            border=self.ctx.theme.border_strong,
            radius=max(1, int(round(
                self.ctx.theme.radius_l * self._density))),
            border_width=max(1, int(round(2 * self._density))),
            chrome=getattr(self.ctx, "ui_chrome", None),
            screen=self.mode,
            role="main",
            density=self._density,
        )
        self._title_pos = (
            self._panel_rect.x + panel_pad_x,
            self._panel_rect.y
            + (self._header_h - title_h) // 2,
        )
        self.close_btn = Button(
            pygame.Rect(
                self._panel_rect.right - panel_pad_x - close_w,
                self._panel_rect.y
                + (self._header_h - close_h) // 2,
                close_w,
                close_h,
            ),
            close_label,
            fonts=self.ctx.fonts,
            theme=self.ctx.theme,
            font_size=self._close_font_size,
            style="ghost",
            on_click=(
                lambda: self.on_close() if self.on_close else None
            ),
            chrome=getattr(self.ctx, "ui_chrome", None),
            screen=self.mode,
            role="close",
            motion=self.ctx.motion,
        )

        status_y = self._panel_rect.bottom - self._status_h
        self._status_rect = pygame.Rect(
            self._panel_rect.x + panel_pad_x,
            status_y,
            max(1, self._panel_rect.width - panel_pad_x * 2),
            self._status_h,
        )
        self._scroll_rect = pygame.Rect(
            self._panel_rect.x + panel_pad_x,
            self._panel_rect.y + self._header_h,
            max(1, self._panel_rect.width - panel_pad_x * 2),
            max(
                _MIN_TARGET,
                status_y
                - (self._panel_rect.y + self._header_h),
            ),
        )

        self._thumb_w = max(1, int(round(_THUMB_W * self._density)))
        self._thumb_h = max(1, int(round(_THUMB_H * self._density)))
        self._thumb_margin = max(8, int(round(12 * self._density)))
        self._card_gap = max(6, int(round(8 * self._density)))
        self._card_pad_x = max(8, int(round(10 * self._density)))
        self._card_pad_y = max(7, int(round(9 * self._density)))
        self._line_gap = max(2, int(round(4 * self._density)))
        self._action_w = max(
            _MIN_TARGET, int(round(_ACTION_W * self._density)),
        )
        delete_label = self.ctx.localization.t("save_delete", "刪除")
        action_font = self.ctx.fonts.get(
            self._action_font_size, bold=True,
        )
        self._delete_w = max(
            _MIN_TARGET,
            int(round(_DELETE_W * self._density)),
            action_font.size(delete_label)[0]
            + max(12, int(round(18 * self._density))),
        )
        self._control_gap = max(6, int(round(8 * self._density)))
        action_line_h = self.ctx.fonts.get(
            self._action_font_size, bold=True,
        ).get_linesize()
        self._action_h = max(
            _MIN_TARGET,
            action_line_h + max(12, int(round(14 * self._density))),
        )
        label_line_h = self.ctx.fonts.get(
            self._label_font_size, bold=True,
        ).get_linesize()
        summary_line_h = self.ctx.fonts.get(
            self._summary_font_size,
        ).get_linesize()
        time_line_h = self.ctx.fonts.get(
            self._time_font_size,
        ).get_linesize()
        tag_line_h = self.ctx.fonts.get(
            self._tag_font_size, bold=True,
        ).get_linesize()
        self._first_line_h = max(
            label_line_h,
            tag_line_h + max(4, int(round(6 * self._density))),
        )
        text_block_h = (
            self._first_line_h
            + self._line_gap
            + summary_line_h
            + self._line_gap
            + time_line_h
        )
        self._card_h = max(
            _MIN_TARGET,
            self._thumb_h + self._card_pad_y * 2,
            self._action_h + self._card_pad_y * 2,
            text_block_h + self._card_pad_y * 2,
        )

        if hasattr(self, "_scroll"):
            self._scroll.rect = self._scroll_rect.copy()
            self._scroll.scroll_y = max(
                0, min(self._scroll.scroll_y, self._max_scroll()),
            )
        if previous_size is not None and previous_size != self._layout_size:
            self._thumb_cache.clear()
        if self._pending_item is not None:
            self._configure_confirmation()

    def _ensure_layout(self, canvas_size: tuple[int, int]) -> None:
        if tuple(canvas_size) != self._layout_size:
            self._configure_layout(canvas_size)

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
        """Let the optional pack renderer paint one finite visual role."""
        chrome = getattr(self.ctx, "ui_chrome", None)
        if chrome is None:
            return False
        return chrome.paint(
            surface,
            rect,
            ChromeRequest(
                part=part,
                screen=(
                    self.mode
                    if self.mode in {"save", "load"}
                    else "save"
                ),
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

    def _content_height(self) -> int:
        if not self._items:
            return self.ctx.fonts.get(
                self._summary_font_size,
            ).get_linesize()
        return len(self._items) * (self._card_h + self._card_gap)

    def _max_scroll(self) -> int:
        if not hasattr(self, "_scroll"):
            return 0
        return max(0, self._content_height() - self._scroll.rect.height)

    # ----- slot classification ---------------------------------------------

    def _is_quicksave(self, slot: str | None) -> bool:
        if not slot:
            return False
        return slot == self.ctx.config.quicksave_slot

    @staticmethod
    def _is_autosave(slot: str | None) -> bool:
        return bool(slot) and slot.startswith("autosave_")

    def _is_special(self, slot: str | None) -> bool:
        return self._is_quicksave(slot) or self._is_autosave(slot)

    def _slot_tag(self, slot: str | None) -> str | None:
        """Short badge text for a special slot, else None."""
        if self._is_quicksave(slot):
            return self.ctx.localization.t("quicksave", "快速存檔")
        if self._is_autosave(slot):
            return self.ctx.localization.t("autosave", "自動存檔")
        return None

    def _action_enabled(self, item: dict) -> bool:
        """Whether the row's action (load / overwrite / new) is clickable.

        In save mode autosave_* slots are read-only — the engine owns them, so
        a manual overwrite would be clobbered on the next autosave anyway.
        """
        if self.mode == "save" and self._is_autosave(item.get("slot")):
            return False
        return True

    def _action_label(self, item: dict) -> str:
        if not item.get("slot"):
            return self.ctx.localization.t("save_new", "新增")
        if self.mode == "save":
            return self.ctx.localization.t("save_overwrite", "覆寫")
        return self.ctx.localization.t("load_action", "載入")

    @staticmethod
    def _delete_enabled(item: dict) -> bool:
        return bool(item.get("slot"))

    # ----- list assembly ----------------------------------------------------

    def _refresh(self) -> None:
        saves = self.sm.list_saves()  # newest-first by mtime

        # Group: quicksave, then autosave_* (numeric order), then manual
        # (newest-first, as returned). Pinning the special slots keeps the
        # engine-managed saves together at the top of the list.
        quick = [s for s in saves if self._is_quicksave(s.get("slot"))]

        def _auto_index(row: dict) -> int:
            slot = row.get("slot") or ""
            tail = slot[len("autosave_"):]
            try:
                return int(tail)
            except ValueError:
                return 1 << 30
        autos = sorted(
            (s for s in saves if self._is_autosave(s.get("slot"))),
            key=_auto_index,
        )
        manual = [
            s for s in saves
            if not self._is_special(s.get("slot"))
        ]

        items: list[dict] = []
        if self.mode == "save":
            items.append({
                "slot": None,
                "label": self.ctx.localization.t("save_new_label", "+ 新增存檔"),
                "summary": self.ctx.localization.t(
                    "save_new_hint", "（建立新存檔）"),
                "saved_at": "",
                "thumbnail_path": None,
            })
        items.extend(quick)
        items.extend(autos)
        items.extend(manual)

        self._items = items
        focusable = self._focusable_indices()
        if self._focused_index not in focusable:
            self._focused_index = focusable[0] if focusable else -1
        self._focused_action = _PRIMARY_ACTION
        if hasattr(self, "_scroll"):
            self._scroll.content_height = self._content_height()
            self._scroll.scroll_y = max(
                0, min(self._scroll.scroll_y, self._max_scroll()),
            )

    def _load_thumbnail(self, item: dict) -> pygame.Surface | None:
        """Load a PNG thumbnail (cached by slot), scaled to display size.

        Returns None silently on any failure or when no thumbnail exists.
        """
        path = item.get("thumbnail_path")
        slot = item.get("slot")
        if not path:
            return None
        if slot in self._thumb_cache:
            return self._thumb_cache[slot]
        surf: pygame.Surface | None
        try:
            loaded = pygame.image.load(path).convert_alpha()
            surf = pygame.transform.smoothscale(
                loaded, (self._thumb_w, self._thumb_h),
            )
        except Exception:
            surf = None
        if slot is not None:
            self._thumb_cache[slot] = surf
        return surf

    # ----- action routing / confirmation -----------------------------------

    def _set_status(self, message: str | None, kind: str = "hint") -> None:
        self._status_msg = str(message) if message else None
        self._status_kind = kind
        # Keep the legacy field for integrations/tests that inspect load
        # failures directly. Success/hint messages are intentionally separate.
        self._error_msg = self._status_msg if kind == "error" else None

    def _actionable_indices(self) -> list[int]:
        return [
            index for index, item in enumerate(getattr(self, "_items", []))
            if self._action_enabled(item)
        ]

    def _focusable_indices(self) -> list[int]:
        """Rows reachable by vertical navigation.

        An autosave's primary action is read-only in save mode, but the row is
        still focusable because its explicit delete control is available.
        Keeping the default sub-focus on primary makes Enter inert/safe until
        the player deliberately moves right to Delete.
        """
        return list(range(len(getattr(self, "_items", []))))

    def _request_action(self, item: dict) -> None:
        """UI entrypoint: confirm occupied slots, save empty slots directly."""
        if not self._action_enabled(item):
            self._set_status(
                self.ctx.localization.t(
                    "save_readonly_hint",
                    "自動存檔為唯讀，無法在此覆寫。",
                ),
                "hint",
            )
            return
        if self.mode == "save" and not item.get("slot"):
            self._on_action(item)
            return
        if not item.get("slot"):
            return
        self._set_status(None)
        self._pending_item = item
        self._pending_action = _PRIMARY_ACTION
        self._confirm_focus = 1
        self._configure_confirmation()

    def _request_delete(self, item: dict) -> None:
        """Open the dedicated destructive guard for one existing slot."""
        if not self._delete_enabled(item):
            return
        self._set_status(None)
        self._pending_item = item
        self._pending_action = _DELETE_ACTION
        self._confirm_focus = 1
        self._configure_confirmation()

    def _configure_confirmation(self) -> None:
        if self._pending_item is None:
            self._confirm_buttons = []
            return
        density = self._density
        pad_x = max(16, int(round(28 * density)))
        pad_y = max(14, int(round(22 * density)))
        gap = max(10, int(round(14 * density)))
        title_h = self.ctx.fonts.get(
            self._confirm_title_size, bold=True,
        ).get_linesize()
        eyebrow_h = self.ctx.fonts.get(
            self._confirm_eyebrow_size, bold=True,
        ).get_linesize()
        body_h = self.ctx.fonts.get(
            self._confirm_body_size,
        ).get_linesize()
        button_font = self.ctx.fonts.get(
            self._confirm_button_size, bold=True,
        )
        button_h = max(
            _MIN_TARGET,
            button_font.get_linesize()
            + max(12, int(round(14 * density))),
        )
        dialog_w = min(
            self._panel_rect.width - max(24, int(round(48 * density))),
            max(360, int(round(620 * density))),
        )
        dialog_w = max(1, dialog_w)
        # Reserve exactly two measured body lines so changing text never moves
        # the controls or lets the confirmation copy collide with them.
        dialog_h = (
            pad_y
            + eyebrow_h
            + gap
            + title_h
            + gap
            + body_h * 2
            + gap * 2
            + button_h
            + pad_y
        )
        dialog_h = min(
            dialog_h,
            max(1, self._panel_rect.height - max(20, int(round(40 * density)))),
        )
        self._confirm_rect = pygame.Rect(
            self._panel_rect.centerx - dialog_w // 2,
            self._panel_rect.centery - dialog_h // 2,
            dialog_w,
            dialog_h,
        )
        self._confirm_pad_x = pad_x
        self._confirm_pad_y = pad_y
        self._confirm_gap = gap

        button_gap = max(10, int(round(12 * density)))
        button_w = max(
            _MIN_TARGET,
            (dialog_w - pad_x * 2 - button_gap) // 2,
        )
        buttons_y = self._confirm_rect.bottom - pad_y - button_h
        confirm_label = (
            self.ctx.localization.t("save_delete_confirm", "確認刪除")
            if self._pending_action == _DELETE_ACTION
            else self.ctx.localization.t("confirm", "確認")
        )
        cancel_label = self.ctx.localization.t("cancel", "取消")
        self._confirm_buttons = [
            Button(
                pygame.Rect(
                    self._confirm_rect.x + pad_x,
                    buttons_y,
                    button_w,
                    button_h,
                ),
                confirm_label,
                fonts=self.ctx.fonts,
                theme=self.ctx.theme,
                font_size=self._confirm_button_size,
                style=(
                    "danger"
                    if self._pending_action == _DELETE_ACTION
                    or self.mode == "save"
                    else "primary"
                ),
                on_click=self._confirm_pending,
                chrome=getattr(self.ctx, "ui_chrome", None),
                screen=self.mode,
                role="action",
                motion=self.ctx.motion,
            ),
            Button(
                pygame.Rect(
                    self._confirm_rect.right - pad_x - button_w,
                    buttons_y,
                    button_w,
                    button_h,
                ),
                cancel_label,
                fonts=self.ctx.fonts,
                theme=self.ctx.theme,
                font_size=self._confirm_button_size,
                style="ghost",
                on_click=self._cancel_confirmation,
                chrome=getattr(self.ctx, "ui_chrome", None),
                screen=self.mode,
                role="action",
                motion=self.ctx.motion,
            ),
        ]

    def _confirm_pending(self) -> None:
        item = self._pending_item
        action = self._pending_action
        self._pending_item = None
        self._pending_action = _PRIMARY_ACTION
        self._confirm_buttons = []
        if item is not None:
            if action == _DELETE_ACTION:
                self._delete_item(item)
            else:
                self._on_action(item)

    def _cancel_confirmation(self) -> None:
        if self._pending_item is None:
            return
        was_delete = self._pending_action == _DELETE_ACTION
        self._pending_item = None
        self._pending_action = _PRIMARY_ACTION
        self._confirm_buttons = []
        # Returning from any modal lands on the safe action. In particular,
        # Enter after cancelling Delete must never reopen a destructive guard.
        self._focused_action = _PRIMARY_ACTION
        if was_delete:
            message = self.ctx.localization.t(
                "save_delete_cancelled",
                "已取消刪除，現有存檔未變更。",
            )
        else:
            message = self.ctx.localization.t(
                "save_action_cancelled",
                "已取消，現有存檔與目前進度均未變更。",
            )
        self._set_status(
            message,
            "hint",
        )

    def _delete_item(self, item: dict) -> None:
        """Delete one slot while preserving UI state on every failure."""
        slot = item.get("slot")
        if not slot:
            return
        try:
            deleted_index = next(
                index
                for index, candidate in enumerate(self._items)
                if candidate is item or candidate.get("slot") == slot
            )
        except StopIteration:
            deleted_index = self._focused_index
        label = item.get("label") or slot

        try:
            deleted = self.sm.delete(slot)
        except Exception as exc:
            self._set_status(
                self.ctx.localization.t(
                    "save_delete_failed",
                    "刪除失敗：{error}",
                    error=exc,
                ),
                "error",
            )
            return
        if not deleted:
            self._set_status(
                self.ctx.localization.t(
                    "save_delete_missing",
                    "刪除失敗：找不到「{label}」，清單未變更。",
                    label=label,
                ),
                "error",
            )
            return

        self._thumb_cache.pop(slot, None)
        self._refresh()
        focusable = self._focusable_indices()
        if focusable:
            target = max(0, min(deleted_index, len(self._items) - 1))
            self._focused_index = target
            self._focused_action = _PRIMARY_ACTION
            self._ensure_focused_visible()
        else:
            self._focused_index = -1
            self._focused_action = _PRIMARY_ACTION
        self._hovered_index = -1
        self._hovered_action = None
        self._scroll.scroll_y = max(
            0, min(self._scroll.scroll_y, self._max_scroll()),
        )
        self._set_status(
            self.ctx.localization.t(
                "save_delete_succeeded",
                "已刪除：{label}",
                label=label,
            ),
            "success",
        )

    # ----- save / load action (persistence) ---------------------------------

    def _on_action(self, item: dict) -> None:
        self._set_status(None)
        if self.mode == "save":
            slot = item.get("slot") or f"slot_{time.time_ns()}"
            loc = self.ctx.state.map.current
            summary = (
                f"{self.ctx.state.time.label()} · "
                f"{(loc.name if loc else '無位置')}"
            )
            # Clean slot title (the card already shows the summary line +
            # timestamp separately, so don't embed the summary in the label).
            label = (
                item.get("label")
                if item.get("slot")
                else (self.ctx.state.player.name or "存檔")
            )
            # Grab current screen as thumbnail when the callback is available.
            thumbnail = None
            if self._get_screen is not None:
                try:
                    thumbnail = self._get_screen()
                except Exception:
                    thumbnail = None
            resume_context = None
            if self._get_resume is not None:
                try:
                    resume_context = self._get_resume()
                except Exception:
                    resume_context = None

            # mode='json' is critical: pydantic converts set[] -> list[],
            # tuple -> list, etc, so the JSON round-trip can validate back
            # into GameState. Without it, default model_dump() leaves sets
            # as Python sets and json.dump(default=str) writes them as repr
            # strings that pydantic refuses to validate.
            payload = self.ctx.state.model_dump(mode="json")
            # Lifecycle hook so plugins can patch / persist auxiliary data
            # before the JSON write. They mutate `payload` directly.
            from ..plugins import fire_event
            from ..plugins.context import HookEvent
            fire_event(self.ctx.state, HookEvent.SAVE_BEFORE_SERIALIZE,
                       slot=slot, payload=payload)
            try:
                self.sm.save(
                    slot,
                    payload,
                    label=label,
                    summary=summary,
                    thumbnail=thumbnail,
                    pack_meta=self.ctx.state.meta.get("__pack_meta__", {}),
                    resume_context=resume_context,
                )
            except Exception as exc:
                self._set_status(
                    self.ctx.localization.t(
                        "save_failed",
                        "儲存失敗：{error}",
                        error=exc,
                    ),
                    "error",
                )
                return
            # A fresh thumbnail may have been written for this slot; drop the
            # cached surface so the refreshed list re-decodes it.
            self._thumb_cache.pop(slot, None)
            self._set_status(
                self.ctx.localization.t(
                    "save_succeeded",
                    "儲存完成：{label}",
                    label=label,
                ),
                "success",
            )
        elif self.mode == "load":
            slot = item.get("slot")
            if not slot:
                return
            try:
                data = self.sm.load(slot)
            except Exception as exc:
                self._set_status(f"載入失敗：{exc}", "error")
                return
            # Pack-level compatibility gate: reject saves from a different
            # pack and migrate older pack content versions to the current
            # one, before reconstructing state. Pack identity comes from the
            # transient bridge the loader parked on state.meta.
            from ..core.pack_migration import (
                check_and_migrate_pack, PACK_MIGRATIONS,
                SavePackMismatchError, SavePackSchemaError,
            )
            pack_meta = self.ctx.state.meta.get("__pack_meta__", {})
            try:
                data = check_and_migrate_pack(
                    data,
                    current_pack_id=str(pack_meta.get("pack_id", "")),
                    current_pack_version=str(
                        pack_meta.get("pack_format_version", "0")),
                    registry=PACK_MIGRATIONS,
                )
            except (SavePackMismatchError, SavePackSchemaError) as exc:
                self._set_status(f"載入失敗：{exc}", "error")
                return
            # Presentation metadata participates in pack migration: breaking
            # content updates may rename its scene ids or move its cursor in
            # lockstep with StoryGraph. Strip it only after migrations have
            # finished because it is not a GameState model field.
            has_resume_context = "_resume" in data
            resume_context = data.pop("_resume", None)
            # Strip save-manager internal keys before reconstructing state.
            for key in ("_saved_at", "_label", "_summary",
                        "_schema_version", "_thumbnail_path",
                        "_pack_id", "_pack_format_version", "_engine_version"):
                data.pop(key, None)
            try:
                new_state = GameState(**data)
            except Exception as exc:
                self._set_status(f"存檔格式錯誤：{exc}", "error")
                return
            authored_map = self.ctx.state.meta.get("__pack_map_catalog__")
            if not isinstance(authored_map, MapSystem):
                authored_map = self.ctx.state.map
            new_state.map = rebind_authored_map_catalog(
                authored_map,
                new_state.map,
            )
            authored_endings = self.ctx.state.meta.get(
                "__pack_ending_catalog__",
            )
            if not isinstance(authored_endings, EndingTracker):
                # Standalone/custom SceneContext users may not have the app's
                # transient bridge. The live pre-load tracker is still the
                # current pack authority.
                authored_endings = self.ctx.state.endings
            new_state.endings = rebind_authored_ending_catalog(
                authored_endings,
                new_state.endings,
            )
            authored_story = self.ctx.state.meta.get(
                "__pack_story_catalog__",
            )
            if not isinstance(authored_story, StoryGraph):
                authored_story = self.ctx.state.story
            media_authority = self.ctx.state.meta.get(
                "__pack_media_authority__",
            )
            manifest = self.ctx.state.meta.get(
                "__cg_gallery_manifest__",
            )
            if not isinstance(manifest, CGGalleryManifest):
                manifest = None
            music_manifest = self.ctx.state.meta.get(
                "__music_room_manifest__",
            )
            if not isinstance(music_manifest, MusicRoomManifest):
                music_manifest = None
            if isinstance(media_authority, MediaAuthority):
                # Include scenes a trusted runtime/plugin explicitly added to
                # the authored story catalogue after application startup.
                media_authority = build_media_authority(
                    authored_story,
                    cg_manifest=manifest,
                    music_manifest=music_manifest,
                    additional_cg_ids=media_authority.cg_ids,
                    additional_music_ids=media_authority.music_ids,
                )
            else:
                # A standalone SceneContext has no application snapshot. Its
                # live pre-load trackers are still trusted runtime state and
                # preserve explicitly registered plugin media.
                media_authority = build_media_authority(
                    authored_story,
                    cg_manifest=manifest,
                    music_manifest=music_manifest,
                    additional_cg_ids=tuple(
                        self.ctx.state.cg_gallery.unlocked,
                    ),
                    additional_music_ids=tuple(
                        self.ctx.state.music_room.unlocked,
                    ),
                )
            try:
                new_state.story = rebind_authored_story_catalog(
                    authored_story,
                    new_state.story,
                )
                (
                    new_state.cg_gallery,
                    new_state.music_room,
                ) = rebind_authorized_media_progress(
                    new_state.cg_gallery,
                    new_state.music_room,
                    media_authority,
                )
                new_state.meta = rebind_authored_audio_meta(
                    authored_story,
                    new_state.meta,
                    authored_map,
                    has_resume_context=has_resume_context,
                    resume_context=resume_context,
                )
                new_state.dialogue_history = (
                    rebind_authored_dialogue_history_voices(
                        authored_story,
                        new_state.dialogue_history,
                    )
                )
                from ..dialogue.dialogue_engine import (
                    rebind_dialogue_history_localization,
                )
                new_state.dialogue_history = (
                    rebind_dialogue_history_localization(
                        authored_story,
                        new_state.dialogue_history,
                        new_state,
                        localization=self.ctx.localization,
                        speaker_id_resolver=getattr(
                            self.ctx.dialogue,
                            "speaker_id_resolver",
                            None,
                        ),
                    )
                )
                from ..dialogue.replay_engine import (
                    rebind_authored_replay_visual_fx,
                )
                new_state.read_log = rebind_authored_replay_visual_fx(
                    new_state.read_log,
                    authored_story,
                )
            except ValueError as exc:
                self._set_status(f"存檔故事進度錯誤：{exc}", "error")
                return
            if has_resume_context:
                from ..dialogue.dialogue_engine import (
                    rebind_resume_choice_presentations,
                )
                resume_context = rebind_resume_choice_presentations(
                    resume_context,
                    new_state,
                    localization=self.ctx.localization,
                    speaker_id_resolver=getattr(
                        self.ctx.dialogue,
                        "speaker_id_resolver",
                        None,
                    ),
                )
            if has_resume_context:
                from ..dialogue.dialogue_engine import resume_matches_state
                if not resume_matches_state(
                    resume_context,
                    new_state,
                    authored_story=authored_story,
                    authored_npcs=self.ctx.npcs,
                    authored_map=authored_map,
                    localization=self.ctx.localization,
                ):
                    # Do not swap the live state or close/rebuild the overlay:
                    # invalid presentation metadata makes the whole load an
                    # atomic no-op, just like an invalid GameState payload.
                    self._set_status(
                        "存檔顯示狀態錯誤，未載入。", "error",
                    )
                    return
            # Ending definitions are pack content. Capture the authoritative
            # ids before swapping in an older serialized tracker, whose
            # registry may still include endings removed by the current pack.
            pack_ending_ids = self.ctx.state.meta.get(
                "__pack_ending_ids__",
            )
            if isinstance(pack_ending_ids, (set, frozenset, list, tuple)):
                registered_endings = set(pack_ending_ids)
            else:
                registered_endings = set(
                    self.ctx.state.endings.endings,
                )
            # Preserve transient bridges (__plugin_manager__, __npc_registry__)
            # before the state swap — they were filtered out at save time.
            preserved = {
                k: v for k, v in self.ctx.state.meta.items()
                if k.startswith("__")
            }
            self.ctx.state.__dict__.update(new_state.__dict__)
            self.ctx.state.meta.update(preserved)
            self.ctx.state.time.bind_localization(self.ctx.localization)
            self.ctx.state.affection.bind_localization(self.ctx.localization)
            self.ctx.state.meta["__pack_media_authority__"] = media_authority
            clear_data = self.ctx.state.meta.get("__clear_data__")
            apply_clear_data = getattr(clear_data, "apply_to_state", None)
            if callable(apply_clear_data):
                apply_clear_data(
                    self.ctx.state,
                    registered_endings=registered_endings,
                    authored_cg_ids=media_authority.cg_ids,
                    authored_music_ids=media_authority.music_ids,
                )
            if isinstance(resume_context, dict):
                self.ctx.state.meta["__loaded_resume__"] = resume_context
            else:
                self.ctx.state.meta.pop("__loaded_resume__", None)
            from ..plugins import fire_event
            from ..plugins.context import HookEvent
            fire_event(self.ctx.state, HookEvent.SAVE_AFTER_LOAD,
                       slot=slot, payload=data)
            self._set_status(
                self.ctx.localization.t(
                    "load_succeeded",
                    "載入完成：{label}",
                    label=item.get("label") or slot,
                ),
                "success",
            )
            if self.on_close:
                self.on_close()
                return
        self._refresh()

    # ----- card rendering ---------------------------------------------------

    def _draw_focus_ring(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        *,
        radius: int,
    ) -> None:
        """Paint a focus treatment identifiable by shape as well as colour."""
        width = max(2, int(round(3 * self._density)))
        ring = rect.inflate(-width, -width)
        pygame.draw.rect(
            surface,
            self.ctx.theme.accent_warm,
            ring,
            width=width,
            border_radius=max(1, radius),
        )
        # Two opposing corner brackets remain legible on bright thumbnails.
        inset = max(6, int(round(8 * self._density)))
        length = max(8, int(round(12 * self._density)))
        bracket_w = max(1, int(round(2 * self._density)))
        x, y = ring.x + inset, ring.y + inset
        pygame.draw.lines(
            surface,
            self.ctx.theme.text,
            False,
            [(x, y + length), (x, y), (x + length, y)],
            width=bracket_w,
        )
        x, y = ring.right - inset, ring.bottom - inset
        pygame.draw.lines(
            surface,
            self.ctx.theme.text,
            False,
            [(x - length, y), (x, y), (x, y - length)],
            width=bracket_w,
        )

    def _render_action_chip(
        self,
        *,
        width: int,
        label: str,
        enabled: bool,
        hovered: bool,
        focused: bool,
        danger: bool = False,
    ) -> pygame.Surface:
        """Render one row action with an explicit persistent focus state."""
        chip = pygame.Surface(
            (width, self._action_h),
            pygame.SRCALPHA,
        )
        color = self.ctx.theme.warn if danger else self.ctx.theme.accent
        painted = self._paint_chrome(
            chip,
            chip.get_rect(),
            part="button",
            variant="action",
            enabled=enabled,
            hovered=hovered and enabled,
            focused=focused,
            emphasis=3 if focused or hovered else (2 if danger else 0),
        )
        radius = max(2, int(round(
            self.ctx.theme.radius_s * self._density,
        )))
        if not painted:
            pygame.draw.rect(
                chip,
                (*color[:3], 120 if enabled else 50),
                chip.get_rect(),
                border_radius=radius,
            )
        elif not enabled:
            disabled_veil = pygame.Surface(
                chip.get_size(),
                pygame.SRCALPHA,
            )
            pygame.draw.rect(
                disabled_veil,
                (*self.ctx.theme.bg_panel[:3], 190),
                disabled_veil.get_rect(),
                border_radius=radius,
            )
            chip.blit(disabled_veil, (0, 0))
        pygame.draw.rect(
            chip,
            (*color[:3], 230 if enabled else 110),
            chip.get_rect(),
            width=max(1, int(round(self._density))),
            border_radius=radius,
        )

        action_font = self.ctx.fonts.get(
            self._action_font_size, bold=True,
        )
        measured = _ellipsized(
            label,
            action_font,
            width - max(8, int(round(12 * self._density))),
        )
        text = action_font.render(
            measured,
            True,
            self.ctx.theme.text if enabled else self.ctx.theme.text_dim,
        )
        chip.blit(
            text,
            (
                (width - text.get_width()) // 2,
                (self._action_h - text.get_height()) // 2,
            ),
        )
        if focused:
            self._draw_focus_ring(
                chip,
                chip.get_rect(),
                radius=radius,
            )
        return chip

    def _draw_card(
        self,
        surface: pygame.Surface,
        y: int,
        item: dict,
        item_index: int,
    ) -> int:
        """Paint one slot card onto ``surface`` at offset ``y``.

        Records the action's absolute on-screen hit rect when enabled.
        Returns the next y offset.
        """
        slot = item.get("slot")
        special = self._is_special(slot)
        new_row = slot is None
        enabled = self._action_enabled(item)
        delete_enabled = self._delete_enabled(item)
        card_enabled = enabled or delete_enabled
        scrollbar_gutter = max(10, int(round(14 * self._density)))
        w = max(1, surface.get_width() - scrollbar_gutter)
        card = pygame.Surface((w, self._card_h), pygame.SRCALPHA)

        # Tint: special slots get a warm accent so they read apart from
        # manual saves; the "new save" row gets a soft accent; manual slots a
        # neutral translucent white.
        card_variant = "special" if special else ("new" if new_row else "slot")
        if not self._paint_chrome(
            card,
            card.get_rect(),
            part="card",
            variant=card_variant,
            enabled=card_enabled,
            hovered=item_index == self._hovered_index and card_enabled,
            focused=item_index == self._focused_index,
            emphasis=2 if special else (1 if new_row else 0),
        ):
            if special:
                tint = (*self.ctx.theme.accent_warm[:3], 48)
                border_col = self.ctx.theme.border_strong
            elif new_row:
                tint = (*self.ctx.theme.accent[:3], 40)
                border_col = self.ctx.theme.border
            else:
                tint = (255, 255, 255, 22)
                border_col = self.ctx.theme.border
            pygame.draw.rect(card, tint, card.get_rect(),
                             border_radius=max(1, int(round(
                                 self.ctx.theme.radius_m * self._density))))
            pygame.draw.rect(card, border_col, card.get_rect(), width=1,
                             border_radius=max(1, int(round(
                                 self.ctx.theme.radius_m * self._density))))

        # Thumbnail column (skipped for the "new save" row).
        text_x = self._card_pad_x
        if not new_row:
            thumb = self._load_thumbnail(item)
            thumb_y = (self._card_h - self._thumb_h) // 2
            if thumb is not None:
                card.blit(thumb, (self._card_pad_x, thumb_y))
            else:
                ph = pygame.Rect(
                    self._card_pad_x,
                    thumb_y,
                    self._thumb_w,
                    self._thumb_h,
                )
                pygame.draw.rect(card, self.ctx.theme.border, ph,
                                 width=1,
                                 border_radius=max(
                                     2, int(round(4 * self._density))))
            text_x = (
                self._card_pad_x
                + self._thumb_w
                + self._thumb_margin
            )

        delete_x: int | None = None
        if delete_enabled:
            delete_x = w - self._delete_w - self._card_pad_x
            chip_x = delete_x - self._control_gap - self._action_w
        else:
            chip_x = w - self._action_w - self._card_pad_x
        chip_y = (self._card_h - self._action_h) // 2
        text_right = max(text_x, chip_x - self._thumb_margin)
        text_width = max(0, text_right - text_x)

        label_font = self.ctx.fonts.get(
            self._label_font_size, bold=True,
        )
        summary_font = self.ctx.fonts.get(self._summary_font_size)
        time_font = self.ctx.fonts.get(self._time_font_size)
        tag_font = self.ctx.fonts.get(self._tag_font_size, bold=True)

        # Badge for special slots, drawn at the top-right of the text column.
        tag = self._slot_tag(slot)
        badge_rect: pygame.Rect | None = None
        badge_w = 0
        badge_gap = max(6, int(round(8 * self._density)))
        badge_pad_x = max(5, int(round(8 * self._density)))
        badge_pad_y = max(2, int(round(3 * self._density)))
        if tag:
            max_badge_w = max(0, min(
                text_width // 3,
                text_width - max(1, int(round(80 * self._density))),
            ))
            measured_tag = _ellipsized(
                tag,
                tag_font,
                max(0, max_badge_w - badge_pad_x * 2),
            )
            if measured_tag:
                tag_surf = tag_font.render(
                    measured_tag,
                    True,
                    self.ctx.theme.bg_overlay[:3],
                )
                badge_w = min(
                    max_badge_w,
                    tag_surf.get_width() + badge_pad_x * 2,
                )
                badge_h = tag_surf.get_height() + badge_pad_y * 2
                badge_x = text_right - badge_w
                text_block_h = (
                    self._first_line_h
                    + self._line_gap
                    + summary_font.get_linesize()
                    + self._line_gap
                    + time_font.get_linesize()
                )
                text_top = (self._card_h - text_block_h) // 2
                badge_y = text_top + (self._first_line_h - badge_h) // 2
                badge_rect = pygame.Rect(
                    badge_x, badge_y, badge_w, badge_h,
                )
                pygame.draw.rect(
                    card,
                    (*self.ctx.theme.accent_warm[:3], 230),
                    badge_rect,
                    border_radius=max(2, int(round(
                        self.ctx.theme.radius_s * self._density))),
                )
                card.blit(
                    tag_surf,
                    (
                        badge_rect.x
                        + (badge_rect.width - tag_surf.get_width()) // 2,
                        badge_rect.y
                        + (badge_rect.height - tag_surf.get_height()) // 2,
                    ),
                )

        # Label / summary / timestamp.
        text_block_h = (
            self._first_line_h
            + self._line_gap
            + summary_font.get_linesize()
            + self._line_gap
            + time_font.get_linesize()
        )
        text_top = (self._card_h - text_block_h) // 2
        label_w = text_width
        if badge_rect is not None:
            label_w = max(0, badge_rect.x - badge_gap - text_x)
        label_text = _ellipsized(
            item.get("label") or "(無名)",
            label_font,
            label_w,
        )
        label = label_font.render(
            label_text, True, self.ctx.theme.text,
        )
        label_rect = label.get_rect(
            x=text_x,
            centery=text_top + self._first_line_h // 2,
        )
        card.blit(label, label_rect)

        summary_y = text_top + self._first_line_h + self._line_gap
        summary_text = _ellipsized(
            item.get("summary") or "",
            summary_font,
            text_width,
        )
        meta = summary_font.render(
            summary_text, True, self.ctx.theme.text_mute,
        )
        meta_rect = meta.get_rect(x=text_x, y=summary_y)
        card.blit(meta, meta_rect)

        timestamp_rect: pygame.Rect | None = None
        if item.get("saved_at"):
            time_y = (
                summary_y
                + summary_font.get_linesize()
                + self._line_gap
            )
            time_text = _ellipsized(
                str(item["saved_at"]).replace("T", " ")[:19],
                time_font,
                text_width,
            )
            ts = time_font.render(
                time_text, True, self.ctx.theme.text_dim,
            )
            timestamp_rect = ts.get_rect(x=text_x, y=time_y)
            card.blit(ts, timestamp_rect)

        # Primary action + explicit Delete.  Delete is never the default
        # keyboard/controller sub-focus; the player must move right or click
        # the danger control before its confirmation can open.
        chip_label = self._action_label(item)
        if self.mode == "save" and not enabled:
            chip_label = self.ctx.localization.t("save_readonly", "唯讀")
        primary_hovered = (
            item_index == self._hovered_index
            and self._hovered_action == _PRIMARY_ACTION
        )
        primary_focused = (
            item_index == self._focused_index
            and self._focused_action == _PRIMARY_ACTION
        )
        chip = self._render_action_chip(
            width=self._action_w,
            label=chip_label,
            enabled=enabled,
            hovered=primary_hovered,
            focused=primary_focused,
        )
        card.blit(chip, (chip_x, chip_y))
        delete_content_rect: pygame.Rect | None = None
        if delete_x is not None:
            delete_chip = self._render_action_chip(
                width=self._delete_w,
                label=self.ctx.localization.t("save_delete", "刪除"),
                enabled=True,
                hovered=(
                    item_index == self._hovered_index
                    and self._hovered_action == _DELETE_ACTION
                ),
                focused=(
                    item_index == self._focused_index
                    and self._focused_action == _DELETE_ACTION
                ),
                danger=True,
            )
            card.blit(delete_chip, (delete_x, chip_y))
            delete_content_rect = pygame.Rect(
                delete_x,
                y + chip_y,
                self._delete_w,
                self._action_h,
            )
        surface.blit(card, (0, y))

        action_content_rect = pygame.Rect(
            chip_x, y + chip_y, self._action_w, self._action_h,
        )
        self._card_regions.append({
            "index": item_index,
            "slot": slot,
            "card": pygame.Rect(0, y, w, self._card_h),
            "label": label_rect.move(0, y),
            "summary": meta_rect.move(0, y),
            "timestamp": (
                timestamp_rect.move(0, y)
                if timestamp_rect is not None else None
            ),
            "tag": (
                badge_rect.move(0, y)
                if badge_rect is not None else None
            ),
            "action": action_content_rect,
            "delete": delete_content_rect,
        })
        if enabled:
            # Absolute on-screen rect for the action chip (account for scroll).
            screen_x = self._scroll.rect.x + chip_x
            screen_y = self._scroll.rect.y + y + chip_y - self._scroll.scroll_y
            self._row_rects.append((
                pygame.Rect(
                    screen_x,
                    screen_y,
                    self._action_w,
                    self._action_h,
                ),
                item,
            ))
        if delete_content_rect is not None:
            self._delete_rects.append((
                pygame.Rect(
                    self._scroll.rect.x + delete_content_rect.x,
                    self._scroll.rect.y
                    + delete_content_rect.y
                    - self._scroll.scroll_y,
                    delete_content_rect.width,
                    delete_content_rect.height,
                ),
                item,
            ))
        return y + self._card_h + self._card_gap

    def _draw_content(self, surface: pygame.Surface) -> int:
        # The drawer resets hit rects each frame so clicks always match the
        # currently-visible card layout.
        self._row_rects = []
        self._delete_rects = []
        self._card_regions = []
        if not self._items:
            empty = self.ctx.fonts.get(
                self._summary_font_size,
            ).render(
                self.ctx.localization.t("save_empty", "（沒有存檔。）"),
                True,
                self.ctx.theme.text_mute,
            )
            surface.blit(empty, (0, 0))
            return empty.get_height()
        y = 0
        for index, item in enumerate(self._items):
            y = self._draw_card(surface, y, item, index)
        return y

    # ----- lifecycle --------------------------------------------------------

    def _ensure_focused_visible(self) -> None:
        if not (0 <= self._focused_index < len(self._items)):
            return
        top = self._focused_index * (self._card_h + self._card_gap)
        bottom = top + self._card_h
        if top < self._scroll.scroll_y:
            self._scroll.scroll_y = top
        elif bottom > self._scroll.scroll_y + self._scroll.rect.height:
            self._scroll.scroll_y = bottom - self._scroll.rect.height
        self._scroll.scroll_y = max(
            0, min(self._scroll.scroll_y, self._max_scroll()),
        )

    def _update_confirmation(self, dt: float, inp) -> None:
        if inp.cancel:
            self._cancel_confirmation()
            return
        nav = getattr(inp, "nav", 0)
        horizontal = (
            -1
            if self._pressed(inp, InputAction.NAV_LEFT)
            else 1
            if self._pressed(inp, InputAction.NAV_RIGHT)
            else 0
        )
        if horizontal and self._confirm_buttons:
            # Buttons are laid out Confirm, Cancel from left to right.
            self._confirm_focus = 0 if horizontal < 0 else 1
        elif nav and self._confirm_buttons:
            step = 1 if nav > 0 else -1
            self._confirm_focus = (
                self._confirm_focus + step
            ) % len(self._confirm_buttons)
        self._sync_confirmation_focus()
        if getattr(inp, "confirm", False) and self._confirm_buttons:
            button = self._confirm_buttons[self._confirm_focus]
            button.activate()
            return
        for button in list(self._confirm_buttons):
            button.update(dt, inp)
            if self._pending_item is None:
                return

    def _sync_confirmation_focus(self) -> None:
        for index, button in enumerate(self._confirm_buttons):
            button.focused = (
                button.enabled and index == self._confirm_focus
            )

    @staticmethod
    def _pressed(inp, action: str) -> bool:
        pressed = getattr(inp, "pressed", None)
        return bool(callable(pressed) and pressed(action))

    def update(self, dt: float, inp) -> None:
        if self._pending_item is not None:
            self._update_confirmation(dt, inp)
            return
        if inp.cancel and self.on_close:
            self.on_close()
            return
        self.close_btn.update(dt, inp)
        self._scroll.update(dt, inp)
        self._hovered_index = -1
        self._hovered_action = None
        if self._scroll.rect.collidepoint(inp.mouse_pos):
            for rect, item in self._row_rects:
                if rect.collidepoint(inp.mouse_pos):
                    self._hovered_index = next(
                        (
                            index
                            for index, candidate in enumerate(self._items)
                            if candidate is item
                        ),
                        -1,
                    )
                    self._hovered_action = _PRIMARY_ACTION
                    break
            if self._hovered_index < 0:
                for rect, item in self._delete_rects:
                    if rect.collidepoint(inp.mouse_pos):
                        self._hovered_index = next(
                            (
                                index
                                for index, candidate in enumerate(self._items)
                                if candidate is item
                            ),
                            -1,
                        )
                        self._hovered_action = _DELETE_ACTION
                        break

        if inp.mouse_wheel:
            # Pointer scrolling hands navigation back to the pointer. Keeping
            # a hidden keyboard focus would be ambiguous and could arm Enter
            # on a row no longer visible.
            self._focused_index = -1
            self._focused_action = _PRIMARY_ACTION

        focusable = self._focusable_indices()
        nav = getattr(inp, "nav", 0)
        if nav and focusable:
            if self._focused_index not in focusable:
                self._focused_index = (
                    focusable[0] if nav > 0 else focusable[-1]
                )
            else:
                position = focusable.index(self._focused_index)
                self._focused_index = focusable[
                    (position + nav) % len(focusable)
                ]
            # Safe default: moving between rows always returns to the primary
            # action, even when that action is a read-only autosave label.
            self._focused_action = _PRIMARY_ACTION
            self._ensure_focused_visible()

        if self._focused_index in focusable:
            focused_item = self._items[self._focused_index]
            if self._pressed(inp, InputAction.NAV_LEFT):
                self._focused_action = _PRIMARY_ACTION
            elif (
                self._pressed(inp, InputAction.NAV_RIGHT)
                and self._delete_enabled(focused_item)
            ):
                self._focused_action = _DELETE_ACTION

        if (getattr(inp, "confirm", False)
                and self._focused_index in focusable):
            item = self._items[self._focused_index]
            if self._focused_action == _DELETE_ACTION:
                self._request_delete(item)
            else:
                self._request_action(item)
            return

        if getattr(inp, "mouse_clicked", False):
            if not self._scroll.rect.collidepoint(inp.mouse_pos):
                return
            for rect, item in self._row_rects:
                if rect.collidepoint(inp.mouse_pos):
                    self._focused_index = next(
                        (
                            index
                            for index, candidate in enumerate(self._items)
                            if candidate is item
                        ),
                        -1,
                    )
                    self._focused_action = _PRIMARY_ACTION
                    self._request_action(item)
                    return
            for rect, item in self._delete_rects:
                if rect.collidepoint(inp.mouse_pos):
                    self._focused_index = next(
                        (
                            index
                            for index, candidate in enumerate(self._items)
                            if candidate is item
                        ),
                        -1,
                    )
                    self._focused_action = _DELETE_ACTION
                    self._request_delete(item)
                    return

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
            veil.fill((0, 0, 0, 160))
            surface.blit(veil, (0, 0))
        self._panel.draw(surface)
        title_font = self.ctx.fonts.get(
            self._title_font_size, bold=True,
        )
        title_text = (
            self.ctx.localization.t("save", "存檔")
            if self.mode == "save"
            else self.ctx.localization.t("load_game", "載入存檔")
        )
        title_text = _ellipsized(
            title_text,
            title_font,
            self.close_btn.rect.x
            - self._title_pos[0]
            - max(8, int(round(12 * self._density))),
        )
        title = title_font.render(
            title_text,
            True,
            self.ctx.theme.accent,
        )
        surface.blit(title, self._title_pos)
        self.close_btn.draw(surface)
        header_rule_y = self._scroll.rect.y - 1
        header_rule_h = max(1, int(round(6 * self._density)))
        header_rule = pygame.Rect(
            self._scroll.rect.x,
            header_rule_y - header_rule_h // 2,
            self._scroll.rect.width,
            header_rule_h,
        )
        if not self._paint_chrome(
            surface,
            header_rule,
            part="divider",
            variant="header",
        ):
            pygame.draw.line(
                surface,
                self.ctx.theme.border_soft,
                (
                    self._scroll.rect.x,
                    header_rule_y,
                ),
                (
                    self._scroll.rect.right,
                    header_rule_y,
                ),
                width=max(1, int(round(self._density))),
            )
        self._scroll.draw(surface)

        footer_variant = (
            self._status_kind
            if self._status_msg
            and self._status_kind in {"hint", "success", "error"}
            else "hint"
        )
        footer_emphasis = {
            "success": 2,
            "error": 3,
        }.get(footer_variant, 0)
        if not self._paint_chrome(
            surface,
            self._status_rect,
            part="footer",
            variant=footer_variant,
            emphasis=footer_emphasis,
        ):
            pygame.draw.line(
                surface,
                self.ctx.theme.border_soft,
                self._status_rect.topleft,
                self._status_rect.topright,
                width=max(1, int(round(self._density))),
            )
        status_font = self.ctx.fonts.get(
            self._status_font_size, bold=True,
        )
        if self._status_msg:
            prefix = {
                "error": self.ctx.localization.t(
                    "status_error", "錯誤｜",
                ),
                "success": self.ctx.localization.t(
                    "status_success", "完成｜",
                ),
            }.get(self._status_kind, "")
            status_text = prefix + self._status_msg
        else:
            bindings = BindingProfile(
                getattr(self.ctx.config, "input_bindings", {}),
            )
            status_text = (
                f"{bindings.primary_display(InputAction.NAV_UP)}／"
                f"{bindings.primary_display(InputAction.NAV_DOWN)} 選擇　"
                f"{bindings.primary_display(InputAction.NAV_LEFT)}／"
                f"{bindings.primary_display(InputAction.NAV_RIGHT)} 切換動作　"
                f"{bindings.primary_display(InputAction.CONFIRM_ADVANCE)} 確認　"
                f"{bindings.primary_display(InputAction.BACK)} 關閉"
            )
        status_color = {
            "error": self.ctx.theme.warn,
            "success": self.ctx.theme.good,
        }.get(self._status_kind, self.ctx.theme.text_mute)
        marker_w = max(3, int(round(4 * self._density)))
        marker_h = status_font.get_linesize()
        marker = pygame.Rect(
            self._status_rect.x,
            self._status_rect.centery - marker_h // 2,
            marker_w,
            marker_h,
        )
        pygame.draw.rect(
            surface,
            status_color,
            marker,
            border_radius=max(1, marker_w // 2),
        )
        status_text = _ellipsized(
            status_text,
            status_font,
            self._status_rect.width
            - marker_w
            - max(8, int(round(10 * self._density))),
        )
        status = status_font.render(status_text, True, status_color)
        surface.blit(
            status,
            (
                marker.right + max(8, int(round(10 * self._density))),
                self._status_rect.centery - status.get_height() // 2,
            ),
        )

        if self._pending_item is not None:
            self._draw_confirmation(surface)

    def _draw_confirmation(self, surface: pygame.Surface) -> None:
        item = self._pending_item
        if item is None:
            return
        viewport = surface.get_rect()
        if not self._paint_chrome(
            surface,
            viewport,
            part="backdrop",
            variant="modal",
            emphasis=2,
        ):
            veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
            veil.fill((0, 0, 0, 185))
            surface.blit(veil, (0, 0))

        if not self._paint_chrome(
            surface,
            self._confirm_rect,
            part="panel",
            variant="confirmation",
            emphasis=(
                3
                if self._pending_action == _DELETE_ACTION
                or self.mode == "save"
                else 2
            ),
        ):
            dialog = pygame.Surface(
                self._confirm_rect.size,
                pygame.SRCALPHA,
            )
            pygame.draw.rect(
                dialog,
                (*self.ctx.theme.bg_overlay[:3], 252),
                dialog.get_rect(),
                border_radius=max(2, int(round(
                    self.ctx.theme.radius_l * self._density))),
            )
            pygame.draw.rect(
                dialog,
                self.ctx.theme.border_strong,
                dialog.get_rect(),
                width=max(2, int(round(2 * self._density))),
                border_radius=max(2, int(round(
                    self.ctx.theme.radius_l * self._density))),
            )
            surface.blit(dialog, self._confirm_rect)

        content_x = self._confirm_rect.x + self._confirm_pad_x
        content_w = max(
            1, self._confirm_rect.width - self._confirm_pad_x * 2,
        )
        y = self._confirm_rect.y + self._confirm_pad_y
        eyebrow_font = self.ctx.fonts.get(
            self._confirm_eyebrow_size, bold=True,
        )
        title_font = self.ctx.fonts.get(
            self._confirm_title_size, bold=True,
        )
        body_font = self.ctx.fonts.get(self._confirm_body_size)

        if self._pending_action == _DELETE_ACTION:
            eyebrow_text = self.ctx.localization.t(
                "save_delete_confirm_label", "刪除存檔",
            )
            title_template = self.ctx.localization.t(
                "save_delete_confirm_title", "要刪除「{label}」嗎？",
            )
            detail_text = self.ctx.localization.t(
                "save_delete_confirm_detail",
                "這項操作無法復原；預設會取消，現有遊戲進度不受影響。",
            )
        elif self.mode == "save":
            eyebrow_text = self.ctx.localization.t(
                "save_overwrite_confirm_label", "覆寫確認",
            )
            title_template = self.ctx.localization.t(
                "save_overwrite_confirm_title", "要覆寫「{label}」嗎？",
            )
            detail_text = self.ctx.localization.t(
                "save_overwrite_confirm_detail",
                "原本的存檔內容會被目前進度取代；取消不會更動資料。",
            )
        else:
            eyebrow_text = self.ctx.localization.t(
                "load_confirm_label", "載入確認",
            )
            title_template = self.ctx.localization.t(
                "load_confirm_title", "要載入「{label}」嗎？",
            )
            detail_text = self.ctx.localization.t(
                "load_confirm_detail",
                "目前尚未存檔的進度將不會保留；取消會留在現在的遊戲狀態。",
            )
        label = " ".join(str(
            item.get("label") or item.get("slot") or ""
        ).split())
        try:
            title_text = title_template.format(label=label)
        except Exception:
            title_text = title_template

        eyebrow = eyebrow_font.render(
            _ellipsized(eyebrow_text, eyebrow_font, content_w),
            True,
            self.ctx.theme.accent_warm,
        )
        surface.blit(eyebrow, (content_x, y))
        y += eyebrow_font.get_linesize() + self._confirm_gap

        title = title_font.render(
            _ellipsized(title_text, title_font, content_w),
            True,
            self.ctx.theme.text,
        )
        surface.blit(title, (content_x, y))
        y += title_font.get_linesize() + self._confirm_gap

        detail_lines = _wrapped_lines(
            detail_text,
            body_font,
            content_w,
            max_lines=2,
        )
        for line in detail_lines:
            detail = body_font.render(
                line, True, self.ctx.theme.text_mute,
            )
            surface.blit(detail, (content_x, y))
            y += body_font.get_linesize()

        self._sync_confirmation_focus()
        for button in self._confirm_buttons:
            button.draw(surface)
        if 0 <= self._confirm_focus < len(self._confirm_buttons):
            self._draw_focus_ring(
                surface,
                self._confirm_buttons[self._confirm_focus].rect,
                radius=max(2, int(round(
                    self.ctx.theme.radius_m * self._density))),
            )

    def describe(self) -> dict:
        focused_slot = None
        if 0 <= self._focused_index < len(self._items):
            focused_slot = self._items[self._focused_index].get("slot")
        return {
            "scene": "SaveScene",
            "mode": self.mode,
            "save_count": len(self._items),
            "special_slots": [
                it.get("slot") for it in self._items
                if self._is_special(it.get("slot"))
            ],
            "focus": {
                "index": (
                    self._focused_index
                    if self._focused_index >= 0 else None
                ),
                "slot": focused_slot,
                "action": (
                    self._focused_action
                    if self._focused_index >= 0 else None
                ),
            },
            "confirmation": {
                "open": self._pending_item is not None,
                "slot": (
                    self._pending_item.get("slot")
                    if self._pending_item is not None else None
                ),
                "action": (
                    self._pending_action
                    if self._pending_item is not None else None
                ),
                "default": (
                    "cancel" if self._pending_item is not None else None
                ),
            },
            "status": {
                "kind": self._status_kind,
                "message": self._status_msg,
            },
            "layout": {
                "canvas": list(self._layout_size or self.ctx.screen_size),
                "row_height": self._card_h,
                "action_height": self._action_h,
                "delete_width": self._delete_w,
                "close_height": self.close_btn.rect.height,
            },
        }
