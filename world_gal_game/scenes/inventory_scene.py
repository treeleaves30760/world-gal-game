"""Inventory overlay: shows all items the player is carrying.

Doubles as a *gift picker* when ``pick_for_npc`` is set on enter: clicking
an item then sends the chosen item back to the parent scene via
``on_pick``. The chat scene uses this to let the player give a gift from
within a free-chat conversation.
"""
from __future__ import annotations

from typing import Callable

import pygame

from .base import Scene, SceneContext
from ..ui.widgets import Button, Panel, ScrollArea


class InventoryScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        self.on_close: Callable[[], None] | None = None
        self.on_pick: Callable[[str], None] | None = None
        self.pick_for_npc: str | None = None

    def enter(self, *, on_close: Callable[[], None] | None = None,
              on_pick: Callable[[str], None] | None = None,
              pick_for_npc: str | None = None, **_) -> None:
        self.on_close = on_close
        self.on_pick = on_pick
        self.pick_for_npc = pick_for_npc
        sw, sh = self.ctx.screen_size
        self._panel_rect = pygame.Rect(120, 60, sw - 240, sh - 120)
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
        # Cards = (rect, item_id, owned_count) -- recomputed each draw.
        self._cards: list[tuple[pygame.Rect, str, int]] = []
        self._scroll = ScrollArea(
            pygame.Rect(self._panel_rect.x + 30, self._panel_rect.y + 70,
                        self._panel_rect.width - 60,
                        self._panel_rect.height - 100),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
        )
        self._scroll.set_drawer(self._draw_content)

    def _draw_content(self, surface: pygame.Surface) -> int:
        self._cards = []
        inv = self.ctx.state.inventory
        registry = self.ctx.state.items
        # Enumerate items the player actually owns (or all known items in
        # gift-picker mode so they can see the catalogue).
        owned = inv.list_owned()
        if not owned:
            empty = self.ctx.fonts.render(
                "（你現在什麼都沒有。試著從場景或對話中獲得物品。）",
                18, self.ctx.theme.text_mute)
            surface.blit(empty, (0, 0))
            return empty.get_height()

        card_h = 96
        y = 0
        for item_id, count in owned:
            item = registry.get(item_id)
            if item is None:
                # Item ID with no definition — show a stub.
                title = item_id
                desc = "(未在 items.yaml 註冊)"
                icon_path = None
            else:
                title = item.name
                desc = item.description
                icon_path = item.icon
            card = pygame.Surface((self._scroll.rect.width - 14, card_h),
                                  pygame.SRCALPHA)
            pygame.draw.rect(card, (255, 255, 255, 22),
                             card.get_rect(),
                             border_radius=self.ctx.theme.radius_m)
            pygame.draw.rect(card, self.ctx.theme.border,
                             card.get_rect(), width=1,
                             border_radius=self.ctx.theme.radius_m)
            # icon
            if icon_path:
                img = self.ctx.assets.scaled(icon_path, (72, 72), fit="contain")
                card.blit(img, (12, 12))
            else:
                # placeholder block (no icon supplied for this item)
                placeholder = pygame.Surface((54, 54), pygame.SRCALPHA)
                pygame.draw.rect(placeholder, (*self.ctx.theme.accent[:3], 80),
                                 placeholder.get_rect(),
                                 border_radius=self.ctx.theme.radius_s)
                pygame.draw.rect(placeholder, self.ctx.theme.border,
                                 placeholder.get_rect(), width=1,
                                 border_radius=self.ctx.theme.radius_s)
                card.blit(placeholder, (12, 12))
            name = self.ctx.fonts.render(title, 22,
                                          self.ctx.theme.text, bold=True)
            card.blit(name, (100, 12))
            cnt = self.ctx.fonts.render(f"× {count}", 18,
                                         self.ctx.theme.accent_warm)
            card.blit(cnt, (card.get_width() - cnt.get_width() - 16, 14))
            d = self.ctx.fonts.render(desc[:80], 15,
                                       self.ctx.theme.text_mute)
            card.blit(d, (100, 44))
            # gift hint when picking
            if self.pick_for_npc:
                hint = self.ctx.fonts.render("→ 點擊送出", 14,
                                              self.ctx.theme.accent)
                card.blit(hint, (100, 70))
            # blit card and remember its hit rect (in scroll-area coords)
            surface.blit(card, (0, y))
            self._cards.append(
                (pygame.Rect(self._scroll.rect.x,
                             self._scroll.rect.y + y - self._scroll.scroll_y,
                             card.get_width(), card_h),
                 item_id, count))
            y += card_h + 10
        return y

    def update(self, dt: float, inp) -> None:
        if inp.cancel and self.on_close:
            self.on_close()
            return
        self.close_btn.update(dt, inp)
        self._scroll.update(dt, inp)
        # Click on an item card to pick it (only in gift-picker mode).
        if self.pick_for_npc and inp.mouse_clicked and self.on_pick:
            for rect, item_id, _count in self._cards:
                if rect.collidepoint(inp.mouse_pos):
                    self.on_pick(item_id)
                    return

    def draw(self, surface: pygame.Surface) -> None:
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 160))
        surface.blit(veil, (0, 0))
        self._panel.draw(surface)
        title_text = "送什麼禮物？" if self.pick_for_npc else "持有物品"
        title = self.ctx.fonts.render(title_text,
                                      self.ctx.config.font_size_header,
                                      self.ctx.theme.accent, bold=True)
        surface.blit(title, (self._panel_rect.x + 32, self._panel_rect.y + 28))
        if self.pick_for_npc:
            npc = self.ctx.npcs.get(self.pick_for_npc)
            sub = self.ctx.fonts.render(
                f"→ {npc.name if npc else self.pick_for_npc}",
                18, self.ctx.theme.text_mute,
            )
            surface.blit(sub, (self._panel_rect.x + 32 + title.get_width() + 12,
                               self._panel_rect.y + 38))
        self.close_btn.draw(surface)
        self._scroll.draw(surface)

    def describe(self) -> dict:
        return {"scene": "InventoryScene",
                "items": dict(self.ctx.state.inventory.counts),
                "picking_for": self.pick_for_npc}
