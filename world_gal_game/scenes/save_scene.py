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
- Rows whose action is disabled (autosave slots in save mode) register no hit
  rect, so a click there is inert.

Special slots:
- ``quicksave`` and ``autosave_*`` are tagged with a badge and a distinct
  tint so they read apart from manual saves. Both are loadable in load mode.
- In save mode, autosave slots are read-only (no overwrite). Quicksave is
  overwritable from the menu by design: it is a user-driven slot, so manual
  overwrite from the save screen stays consistent with the F6 quicksave key.

Thumbnail handling:
- On save, we capture the current screen via a get_screen_surface callback
  (optional; degrades gracefully to no thumbnail if unavailable).
- On load, thumbnails are displayed at 120x68 to the left of each row.
- Load failures are shown as a red error message instead of crashing.
"""
from __future__ import annotations

from typing import Callable
import time

import pygame

from .base import Scene, SceneContext
from ..ui.widgets import Button, Panel, ScrollArea
from ..core.save_manager import SaveManager, SaveError
from ..core.game_state import GameState

# Thumbnail display dimensions inside each row.
_THUMB_W = 120
_THUMB_H = 68
_THUMB_MARGIN = 12   # gap between thumbnail and text

# Card geometry inside the scroll area.
_CARD_H = 84
_CARD_GAP = 8
_ACTION_W = 100
_ACTION_H = 38


class SaveScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        # Optional callback; caller may leave this as None.
        self._get_screen: Callable[[], pygame.Surface] | None = None

    def enter(
        self,
        *,
        mode: str = "save",
        on_close: Callable[[], None] | None = None,
        get_screen_surface: Callable[[], pygame.Surface] | None = None,
        **_,
    ) -> None:
        self.mode = mode   # "save" or "load"
        self.on_close = on_close
        self._get_screen = get_screen_surface
        self._error_msg: str | None = None

        sw, sh = self.ctx.screen_size
        self._panel_rect = pygame.Rect(160, 60, sw - 320, sh - 120)
        self._panel = Panel(
            self._panel_rect, self.ctx.theme,
            fill=(*self.ctx.theme.bg_overlay[:3], 240),
            border=self.ctx.theme.border_strong,
            radius=self.ctx.theme.radius_l,
            border_width=2,
        )
        self.close_btn = Button(
            pygame.Rect(
                self._panel_rect.right - 120 - 16,
                self._panel_rect.y + 16,
                120, 36,
            ),
            self.ctx.localization.t("close", "關閉"),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=15, style="ghost",
            on_click=(lambda: on_close() if on_close else None),
        )
        self.sm = SaveManager(self.ctx.config.save_dir())
        # The scroll area hosts the slot list; the close button + title sit
        # in the panel header above it.
        self._scroll = ScrollArea(
            pygame.Rect(
                self._panel_rect.x + 30,
                self._panel_rect.y + 80,
                self._panel_rect.width - 60,
                self._panel_rect.height - 110,
            ),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
        )
        self._scroll.set_drawer(self._draw_content)
        # Per-frame hit-test cache: (screen_rect, item) for actionable rows.
        self._row_rects: list[tuple[pygame.Rect, dict]] = []
        # Thumbnail cache keyed by slot so we only decode each PNG once.
        self._thumb_cache: dict[str, pygame.Surface | None] = {}
        self._refresh()

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

    # ----- list assembly ----------------------------------------------------

    def _refresh(self) -> None:
        self._error_msg = None
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
            surf = pygame.transform.smoothscale(loaded, (_THUMB_W, _THUMB_H))
        except Exception:
            surf = None
        if slot is not None:
            self._thumb_cache[slot] = surf
        return surf

    # ----- save / load action (persistence — unchanged behavior) -----------

    def _on_action(self, item: dict) -> None:
        self._error_msg = None
        if self.mode == "save":
            slot = item.get("slot") or f"slot_{int(time.time())}"
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
            self.sm.save(
                slot,
                payload,
                label=label,
                summary=summary,
                thumbnail=thumbnail,
                pack_meta=self.ctx.state.meta.get("__pack_meta__", {}),
            )
            # A fresh thumbnail may have been written for this slot; drop the
            # cached surface so the refreshed list re-decodes it.
            self._thumb_cache.pop(slot, None)
        elif self.mode == "load":
            slot = item.get("slot")
            if not slot:
                return
            try:
                data = self.sm.load(slot)
            except SaveError as exc:
                self._error_msg = f"載入失敗：{exc}"
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
                self._error_msg = f"載入失敗：{exc}"
                return
            # Strip save-manager internal keys before reconstructing state.
            for key in ("_saved_at", "_label", "_summary",
                        "_schema_version", "_thumbnail_path",
                        "_pack_id", "_pack_format_version", "_engine_version"):
                data.pop(key, None)
            try:
                new_state = GameState(**data)
            except Exception as exc:
                self._error_msg = f"存檔格式錯誤：{exc}"
                return
            # Preserve transient bridges (__plugin_manager__, __npc_registry__)
            # before the state swap — they were filtered out at save time.
            preserved = {
                k: v for k, v in self.ctx.state.meta.items()
                if k.startswith("__")
            }
            self.ctx.state.__dict__.update(new_state.__dict__)
            self.ctx.state.meta.update(preserved)
            from ..plugins import fire_event
            from ..plugins.context import HookEvent
            fire_event(self.ctx.state, HookEvent.SAVE_AFTER_LOAD,
                       slot=slot, payload=data)
            if self.on_close:
                self.on_close()
                return
        self._refresh()

    # ----- card rendering ---------------------------------------------------

    def _draw_card(self, surface: pygame.Surface, y: int, item: dict) -> int:
        """Paint one slot card onto ``surface`` at offset ``y``.

        Records the action's absolute on-screen hit rect when enabled.
        Returns the next y offset.
        """
        slot = item.get("slot")
        special = self._is_special(slot)
        new_row = slot is None
        w = surface.get_width() - 14
        card = pygame.Surface((w, _CARD_H), pygame.SRCALPHA)

        # Tint: special slots get a warm accent so they read apart from
        # manual saves; the "new save" row gets a soft accent; manual slots a
        # neutral translucent white.
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
                         border_radius=self.ctx.theme.radius_m)
        pygame.draw.rect(card, border_col, card.get_rect(), width=1,
                         border_radius=self.ctx.theme.radius_m)

        # Thumbnail column (skipped for the "new save" row).
        text_x = 16
        if not new_row:
            thumb = self._load_thumbnail(item)
            thumb_y = (_CARD_H - _THUMB_H) // 2
            if thumb is not None:
                card.blit(thumb, (8, thumb_y))
            else:
                ph = pygame.Rect(8, thumb_y, _THUMB_W, _THUMB_H)
                pygame.draw.rect(card, self.ctx.theme.border, ph,
                                 width=1, border_radius=4)
            text_x = 8 + _THUMB_W + _THUMB_MARGIN

        # Badge for special slots, drawn at the top-right of the text column.
        tag = self._slot_tag(slot)
        if tag:
            tag_surf = self.ctx.fonts.render(
                tag, 13, self.ctx.theme.bg_overlay[:3], bold=True)
            pad = 8
            badge_w = tag_surf.get_width() + pad * 2
            badge_h = tag_surf.get_height() + 6
            badge_x = w - _ACTION_W - 24 - badge_w
            badge = pygame.Surface((badge_w, badge_h), pygame.SRCALPHA)
            pygame.draw.rect(badge, (*self.ctx.theme.accent_warm[:3], 230),
                             badge.get_rect(),
                             border_radius=self.ctx.theme.radius_s)
            badge.blit(tag_surf, (pad, 3))
            card.blit(badge, (badge_x, 10))

        # Label / summary / timestamp.
        label = self.ctx.fonts.render(
            item.get("label") or "(無名)", 20,
            self.ctx.theme.text, bold=True,
        )
        card.blit(label, (text_x, 10))
        meta = self.ctx.fonts.render(
            item.get("summary") or "", 14,
            self.ctx.theme.text_mute,
        )
        card.blit(meta, (text_x, 38))
        if item.get("saved_at"):
            ts = self.ctx.fonts.render(
                item["saved_at"].replace("T", " ")[:19],
                13, self.ctx.theme.text_dim,
            )
            card.blit(ts, (text_x, 60))

        # Action chip (drawn; hit-tested manually). Disabled chips are dimmed
        # and register no hit rect.
        enabled = self._action_enabled(item)
        chip_label = self._action_label(item)
        chip_color = (self.ctx.theme.accent if enabled
                      else self.ctx.theme.text_dim)
        chip = pygame.Surface((_ACTION_W, _ACTION_H), pygame.SRCALPHA)
        pygame.draw.rect(chip, (*chip_color[:3], 110 if enabled else 50),
                         chip.get_rect(),
                         border_radius=self.ctx.theme.radius_s)
        pygame.draw.rect(chip, (*chip_color[:3], 220 if enabled else 110),
                         chip.get_rect(), width=1,
                         border_radius=self.ctx.theme.radius_s)
        if self.mode == "save" and not enabled:
            chip_label = self.ctx.localization.t("save_readonly", "唯讀")
        cl = self.ctx.fonts.render(
            chip_label, 14,
            self.ctx.theme.text if enabled else self.ctx.theme.text_dim,
            bold=True,
        )
        chip.blit(cl, ((_ACTION_W - cl.get_width()) // 2,
                       (_ACTION_H - cl.get_height()) // 2))
        chip_x = w - _ACTION_W - 10
        chip_y = (_CARD_H - _ACTION_H) // 2
        card.blit(chip, (chip_x, chip_y))

        surface.blit(card, (0, y))

        if enabled:
            # Absolute on-screen rect for the action chip (account for scroll).
            screen_x = self._scroll.rect.x + chip_x
            screen_y = self._scroll.rect.y + y + chip_y - self._scroll.scroll_y
            self._row_rects.append((
                pygame.Rect(screen_x, screen_y, _ACTION_W, _ACTION_H),
                item,
            ))
        return y + _CARD_H + _CARD_GAP

    def _draw_content(self, surface: pygame.Surface) -> int:
        # The drawer resets hit rects each frame so clicks always match the
        # currently-visible card layout.
        self._row_rects = []
        if not self._items:
            empty = self.ctx.fonts.render(
                self.ctx.localization.t("save_empty", "（沒有存檔。）"),
                18, self.ctx.theme.text_mute)
            surface.blit(empty, (0, 0))
            return empty.get_height()
        y = 0
        for item in self._items:
            y = self._draw_card(surface, y, item)
        return y

    # ----- lifecycle --------------------------------------------------------

    def update(self, dt: float, inp) -> None:
        if inp.cancel and self.on_close:
            self.on_close()
            return
        self.close_btn.update(dt, inp)
        self._scroll.update(dt, inp)
        if getattr(inp, "mouse_clicked", False):
            for rect, item in self._row_rects:
                if rect.collidepoint(inp.mouse_pos):
                    self._on_action(item)
                    return

    def draw(self, surface: pygame.Surface) -> None:
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 160))
        surface.blit(veil, (0, 0))
        self._panel.draw(surface)
        title = self.ctx.fonts.render(
            "存檔" if self.mode == "save" else "載入存檔",
            self.ctx.config.font_size_header,
            self.ctx.theme.accent, bold=True,
        )
        surface.blit(title, (self._panel_rect.x + 32, self._panel_rect.y + 28))
        self.close_btn.draw(surface)
        self._scroll.draw(surface)

        # Inline error message under the scroll area.
        if self._error_msg:
            err_surf = self.ctx.fonts.render(
                self._error_msg, 15,
                (220, 60, 60),  # red
            )
            surface.blit(err_surf,
                         (self._panel_rect.x + 30,
                          self._scroll.rect.bottom + 6))

    def describe(self) -> dict:
        return {
            "scene": "SaveScene",
            "mode": self.mode,
            "save_count": len(self._items),
            "special_slots": [
                it.get("slot") for it in self._items
                if self._is_special(it.get("slot"))
            ],
        }
