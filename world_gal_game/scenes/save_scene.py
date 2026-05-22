"""Save & Load overlay.

A unified screen with a list of slots; clicking a row loads, clicking
"New Save" creates a fresh slot. The user can also overwrite an existing
slot with the current state.

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
from ..ui.widgets import Button, Panel
from ..core.save_manager import SaveManager, SaveError
from ..core.game_state import GameState

# Thumbnail display dimensions inside each row.
_THUMB_W = 120
_THUMB_H = 68
_THUMB_MARGIN = 12   # gap between thumbnail and text


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
        self._row_buttons: list[Button] = []
        self._row_rects: list[pygame.Rect] = []
        self._thumb_surfs: list[pygame.Surface | None] = []
        self._refresh()

    def _load_thumbnail(self, path: str | None) -> pygame.Surface | None:
        """Load a PNG thumbnail from disk, scaled to display size.

        Returns None silently on any failure.
        """
        if not path:
            return None
        try:
            surf = pygame.image.load(path).convert_alpha()
            return pygame.transform.smoothscale(surf, (_THUMB_W, _THUMB_H))
        except Exception:
            return None

    def _refresh(self) -> None:
        self._row_buttons = []
        self._row_rects = []
        self._thumb_surfs = []
        self._error_msg = None
        saves = self.sm.list_saves()

        items: list[dict] = []
        if self.mode == "save":
            items.append({
                "slot": None,
                "label": "+ 新增存檔",
                "summary": "（建立新存檔）",
                "saved_at": "",
                "thumbnail_path": None,
            })
        items.extend(saves)

        y = self._panel_rect.y + 80
        row_h = 76
        row_w = self._panel_rect.width - 60
        for it in items:
            r = pygame.Rect(self._panel_rect.x + 30, y, row_w, row_h)
            self._row_rects.append(r)
            self._thumb_surfs.append(self._load_thumbnail(it.get("thumbnail_path")))

            btn_label = (
                ("覆寫" if self.mode == "save" else "載入")
                if it["slot"] else "新增"
            )
            btn = Button(
                pygame.Rect(r.right - 110, r.y + (row_h - 38) // 2, 100, 38),
                btn_label,
                fonts=self.ctx.fonts, theme=self.ctx.theme,
                font_size=15, style="primary",
                on_click=(lambda it=it: self._on_action(it)),
            )
            self._row_buttons.append(btn)
            y += row_h + 8

        self._items = items

    def _on_action(self, item: dict) -> None:
        self._error_msg = None
        if self.mode == "save":
            slot = item.get("slot") or f"slot_{int(time.time())}"
            loc = self.ctx.state.map.current
            summary = (
                f"{self.ctx.state.time.label()} · "
                f"{(loc.name if loc else '無位置')}"
            )
            label = (
                item.get("label")
                if item.get("slot")
                else f"存檔 {summary}"
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

    def update(self, dt: float, inp) -> None:
        if inp.cancel and self.on_close:
            self.on_close()
            return
        self.close_btn.update(dt, inp)
        for b in self._row_buttons:
            b.update(dt, inp)

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

        for rect, btn, item, thumb in zip(
            self._row_rects, self._row_buttons, self._items, self._thumb_surfs
        ):
            row_surf = pygame.Surface(rect.size, pygame.SRCALPHA)
            pygame.draw.rect(
                row_surf, (255, 255, 255, 22),
                row_surf.get_rect(),
                border_radius=self.ctx.theme.radius_m,
            )
            pygame.draw.rect(
                row_surf, self.ctx.theme.border,
                row_surf.get_rect(), width=1,
                border_radius=self.ctx.theme.radius_m,
            )
            surface.blit(row_surf, rect.topleft)

            # Thumbnail column on the left side of each row.
            text_x_offset = rect.x + 16
            if thumb is not None:
                thumb_y = rect.y + (rect.height - _THUMB_H) // 2
                surface.blit(thumb, (rect.x + 8, thumb_y))
                text_x_offset = rect.x + 8 + _THUMB_W + _THUMB_MARGIN
            elif item.get("thumbnail_path") is None and item.get("slot") is not None:
                # Draw a placeholder box so the layout stays stable.
                ph = pygame.Rect(rect.x + 8, rect.y + (rect.height - _THUMB_H) // 2,
                                 _THUMB_W, _THUMB_H)
                pygame.draw.rect(surface, self.ctx.theme.border, ph,
                                 width=1, border_radius=4)
                text_x_offset = ph.right + _THUMB_MARGIN

            label = self.ctx.fonts.render(
                item.get("label") or "(無名)", 20,
                self.ctx.theme.text, bold=True,
            )
            surface.blit(label, (text_x_offset, rect.y + 10))
            meta = self.ctx.fonts.render(
                item.get("summary") or "", 14,
                self.ctx.theme.text_mute,
            )
            surface.blit(meta, (text_x_offset, rect.y + 36))
            if item.get("saved_at"):
                ts = self.ctx.fonts.render(
                    item["saved_at"].replace("T", " ")[:19],
                    13, self.ctx.theme.text_dim,
                )
                surface.blit(ts, (text_x_offset, rect.y + 54))
            btn.draw(surface)

        # Inline error message under the row list.
        if self._error_msg:
            err_surf = self.ctx.fonts.render(
                self._error_msg, 15,
                (220, 60, 60),  # red
            )
            last_y = (
                self._row_rects[-1].bottom + 12
                if self._row_rects
                else self._panel_rect.y + 100
            )
            surface.blit(err_surf, (self._panel_rect.x + 30, last_y))

    def describe(self) -> dict:
        return {
            "scene": "SaveScene",
            "mode": self.mode,
            "save_count": len(self._items),
        }
