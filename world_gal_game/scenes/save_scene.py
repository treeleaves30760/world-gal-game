"""Save & Load overlay.

A unified screen with a list of slots; clicking a row loads, clicking
"New Save" creates a fresh slot. The user can also overwrite an existing
slot with the current state.
"""
from __future__ import annotations

from typing import Callable
import time

import pygame

from .base import Scene, SceneContext
from ..ui.widgets import Button, Panel
from ..core.save_manager import SaveManager
from ..core.game_state import GameState


class SaveScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True

    def enter(self, *, mode: str = "save", on_close: Callable[[], None] | None = None,
              **_) -> None:
        self.mode = mode   # "save" or "load"
        self.on_close = on_close
        sw, sh = self.ctx.screen_size
        self._panel_rect = pygame.Rect(160, 60, sw - 320, sh - 120)
        self._panel = Panel(self._panel_rect, self.ctx.theme,
                            fill=(*self.ctx.theme.bg_overlay[:3], 240),
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
        self.sm = SaveManager(self.ctx.config.save_dir())
        self._row_buttons: list[Button] = []
        self._row_rects: list[pygame.Rect] = []
        self._refresh()

    def _refresh(self) -> None:
        self._row_buttons = []
        self._row_rects = []
        saves = self.sm.list_saves()
        # "New save" slot at top (only in save mode).
        items: list[dict] = []
        if self.mode == "save":
            items.append({"slot": None, "label": "＋ 新增存檔",
                          "summary": "（建立新存檔）", "saved_at": ""})
        items.extend(saves)
        y = self._panel_rect.y + 80
        row_h = 76
        row_w = self._panel_rect.width - 60
        for it in items:
            r = pygame.Rect(self._panel_rect.x + 30, y, row_w, row_h)
            self._row_rects.append(r)
            btn_label = ("覆寫" if self.mode == "save"
                         else "載入") if it["slot"] else "新增"
            btn = Button(
                pygame.Rect(r.right - 110, r.y + (row_h - 38) // 2, 100, 38),
                btn_label, fonts=self.ctx.fonts, theme=self.ctx.theme,
                font_size=15, style="primary",
                on_click=(lambda it=it: self._on_action(it)),
            )
            self._row_buttons.append(btn)
            y += row_h + 8

        self._items = items

    def _on_action(self, item: dict) -> None:
        if self.mode == "save":
            slot = item.get("slot") or f"slot_{int(time.time())}"
            loc = self.ctx.state.map.current
            summary = (f"{self.ctx.state.time.label()} · "
                       f"{(loc.name if loc else '無位置')}")
            label = item.get("label") if item.get("slot") else f"存檔 {summary}"
            self.sm.save(slot, self.ctx.state.model_dump(),
                         label=label, summary=summary)
        elif self.mode == "load":
            slot = item.get("slot")
            if not slot:
                return
            data = self.sm.load(slot)
            data.pop("_saved_at", None)
            data.pop("_label", None)
            data.pop("_summary", None)
            try:
                new_state = GameState(**data)
            except Exception as e:
                print(f"[save] load failed: {e}")
                return
            # In-place replacement
            self.ctx.state.__dict__.update(new_state.__dict__)
            # Loaded — close overlay
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
        for rect, btn, item in zip(self._row_rects, self._row_buttons, self._items):
            row_surf = pygame.Surface(rect.size, pygame.SRCALPHA)
            pygame.draw.rect(row_surf, (255, 255, 255, 22),
                             row_surf.get_rect(),
                             border_radius=self.ctx.theme.radius_m)
            pygame.draw.rect(row_surf, self.ctx.theme.border,
                             row_surf.get_rect(), width=1,
                             border_radius=self.ctx.theme.radius_m)
            surface.blit(row_surf, rect.topleft)
            label = self.ctx.fonts.render(item.get("label") or "(無名)", 20,
                                          self.ctx.theme.text, bold=True)
            surface.blit(label, (rect.x + 16, rect.y + 10))
            meta = self.ctx.fonts.render(item.get("summary") or "", 14,
                                         self.ctx.theme.text_mute)
            surface.blit(meta, (rect.x + 16, rect.y + 36))
            if item.get("saved_at"):
                ts = self.ctx.fonts.render(item["saved_at"].replace("T", " ")[:19],
                                           13, self.ctx.theme.text_dim)
                surface.blit(ts, (rect.x + 16, rect.y + 54))
            btn.draw(surface)

    def describe(self) -> dict:
        return {"scene": "SaveScene", "mode": self.mode,
                "save_count": len(self._items)}
