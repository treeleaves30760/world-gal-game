"""Shop overlay: buy / sell items with a configurable currency.

Opened from the exploration scene when the player interacts with an NPC
that has a ``shop`` field. The overlay has two columns:

- left  = buy:  visible listings from the NPC's shop
- right = sell: items in the player's inventory the shop is willing
                to buy back (if ``buy_back_ratio > 0``)

Buying / selling is dispatched through the regular ``buy_item`` /
``sell_item`` effect kinds, so transactions live in the event log and
respect the standard apply_all pipeline (achievements re-evaluated,
toasts surfaced, etc.).
"""
from __future__ import annotations

from typing import Callable

import pygame

from .base import Scene, SceneContext
from ..core.story_graph import Effect
from ..ui.widgets import Button, Panel, ScrollArea


class ShopScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        self.npc_id: str | None = None
        self.on_close: Callable[[], None] | None = None

    def enter(self, *, npc_id: str, on_close: Callable[[], None] | None = None,
              **_) -> None:
        self.npc_id = npc_id
        self.on_close = on_close
        sw, sh = self.ctx.screen_size
        self._panel_rect = pygame.Rect(80, 60, sw - 160, sh - 120)
        self._panel = Panel(self._panel_rect, self.ctx.theme,
                            fill=(*self.ctx.theme.bg_overlay[:3], 240),
                            border=self.ctx.theme.border_strong,
                            radius=self.ctx.theme.radius_l, border_width=2)
        self.close_btn = Button(
            pygame.Rect(self._panel_rect.right - 120 - 16,
                        self._panel_rect.y + 16, 120, 36),
            "關閉 (Esc)", fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=15, style="ghost",
            on_click=(lambda: on_close() if on_close else None),
        )
        col_w = (self._panel_rect.width - 80) // 2
        col_h = self._panel_rect.height - 140
        self._buy_rect = pygame.Rect(self._panel_rect.x + 30,
                                     self._panel_rect.y + 110,
                                     col_w, col_h)
        self._sell_rect = pygame.Rect(self._panel_rect.x + 30 + col_w + 20,
                                      self._panel_rect.y + 110,
                                      col_w, col_h)
        self._buy_scroll = ScrollArea(self._buy_rect,
                                       fonts=self.ctx.fonts,
                                       theme=self.ctx.theme)
        self._sell_scroll = ScrollArea(self._sell_rect,
                                        fonts=self.ctx.fonts,
                                        theme=self.ctx.theme)
        self._buy_scroll.set_drawer(self._draw_buy)
        self._sell_scroll.set_drawer(self._draw_sell)
        # Click hit-tests: tuples of (rect, "buy"/"sell", item_id, price).
        self._row_rects: list[tuple[pygame.Rect, str, str, int]] = []

    # ----- shop / NPC helpers ----------------------------------------------

    def _npc(self):
        return self.ctx.npcs.get(self.npc_id) if self.npc_id else None

    def _shop(self):
        npc = self._npc()
        return npc.shop if (npc is not None and npc.shop is not None) else None

    def _currency(self) -> str:
        shop = self._shop()
        return shop.currency if shop else "money"

    def _balance(self) -> int:
        return self.ctx.state.resources.get(self._currency())

    def _currency_label(self) -> str:
        d = self.ctx.state.resources.definition(self._currency())
        if d is None:
            return self._currency()
        return f"{d.symbol or ''}{self._balance()} {d.name or self._currency()}".strip()

    # ----- transactions ----------------------------------------------------

    def _buy(self, item_id: str, price: int) -> None:
        shop = self._shop()
        if shop is None:
            return
        # Pre-check funds; if not enough, skip the apply (which would
        # log an error). Surface a tiny system message via the toast.
        if not self.ctx.state.resources.can_afford(shop.currency, price):
            from ..ui.widgets.toast import Toast
            # Toasts are drawn by App / screenshot loop; push only when
            # the engine actually has a stack.
            stack = getattr(self.ctx, "toast_stack", None) or \
                getattr(self.ctx.state.meta.get("__app_ref__", None),
                        "toast_stack", None)
            if stack is not None:
                stack.push(Toast(title="餘額不足",
                                  detail=f"還差 {price - self._balance()}"))
            return
        # Honour stock; consume_stock returns False on out-of-stock.
        if not shop.consume_stock(item_id):
            return
        self.ctx.state.apply_all([
            Effect(kind="buy_item", target=item_id,
                   stat=shop.currency, value=price),
        ])

    def _sell(self, item_id: str, price: int) -> None:
        shop = self._shop()
        if shop is None:
            return
        # Respect Item.locked — don't allow selling story-critical items.
        item = self.ctx.state.items.get(item_id)
        if item is not None and item.locked:
            return
        self.ctx.state.apply_all([
            Effect(kind="sell_item", target=item_id,
                   stat=shop.currency, value=price),
        ])

    # ----- drawers ---------------------------------------------------------

    def _draw_row(self, surface: pygame.Surface, y: int, *,
                  side: str, item_id: str, name: str,
                  desc: str, price: int, owned: int = 0,
                  can_afford: bool = True) -> int:
        w = surface.get_width() - 14
        h = 72
        row = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(row, (255, 255, 255, 22),
                         row.get_rect(),
                         border_radius=self.ctx.theme.radius_m)
        border = (self.ctx.theme.border if can_afford
                  else self.ctx.theme.border_soft)
        pygame.draw.rect(row, border, row.get_rect(), width=1,
                         border_radius=self.ctx.theme.radius_m)
        # icon placeholder
        chip = pygame.Surface((52, 52), pygame.SRCALPHA)
        pygame.draw.rect(chip, (*self.ctx.theme.accent[:3], 70),
                         chip.get_rect(),
                         border_radius=self.ctx.theme.radius_s)
        row.blit(chip, (10, 10))
        # name + desc
        name_color = (self.ctx.theme.text if can_afford
                      else self.ctx.theme.text_dim)
        n = self.ctx.fonts.render(name, 18, name_color, bold=True)
        row.blit(n, (74, 8))
        d_text = (desc or "")[:48]
        d = self.ctx.fonts.render(d_text, 14, self.ctx.theme.text_mute)
        row.blit(d, (74, 32))
        # price
        cur_def = self.ctx.state.resources.definition(self._currency())
        sym = cur_def.symbol if cur_def else ""
        p_text = f"{sym}{price}"
        if side == "sell":
            p_text = f"+{p_text}"
        p_color = self.ctx.theme.accent_warm if can_afford else self.ctx.theme.warn
        p = self.ctx.fonts.render(p_text, 20, p_color, bold=True)
        row.blit(p, (w - p.get_width() - 90, 22))
        # owned-count badge (right of price)
        if side == "sell":
            o = self.ctx.fonts.render(f"× {owned}", 14,
                                       self.ctx.theme.text_mute)
            row.blit(o, (w - p.get_width() - 90 + 24, 48))
        # action button area (rightmost)
        btn_label = "購買" if side == "buy" else "賣出"
        btn_color = self.ctx.theme.accent if can_afford else self.ctx.theme.text_dim
        bb = pygame.Surface((70, 36), pygame.SRCALPHA)
        pygame.draw.rect(bb, (*btn_color[:3], 110), bb.get_rect(),
                         border_radius=self.ctx.theme.radius_s)
        pygame.draw.rect(bb, (*btn_color[:3], 220), bb.get_rect(),
                         width=1, border_radius=self.ctx.theme.radius_s)
        bl = self.ctx.fonts.render(btn_label, 14, self.ctx.theme.text, bold=True)
        bb.blit(bl, ((70 - bl.get_width()) // 2,
                     (36 - bl.get_height()) // 2))
        row.blit(bb, (w - 80, 18))
        surface.blit(row, (0, y))
        # remember hit rect (absolute on-screen position)
        sub_rect = self._buy_scroll.rect if side == "buy" else self._sell_scroll.rect
        scroll_y = (self._buy_scroll.scroll_y if side == "buy"
                    else self._sell_scroll.scroll_y)
        screen_y = sub_rect.y + y - scroll_y
        self._row_rects.append((
            pygame.Rect(sub_rect.x, screen_y, w, h),
            side, item_id, price,
        ))
        return y + h + 8

    def _draw_buy(self, surface: pygame.Surface) -> int:
        shop = self._shop()
        if shop is None:
            empty = self.ctx.fonts.render("（這個 NPC 沒有開店。）",
                                           18, self.ctx.theme.text_mute)
            surface.blit(empty, (0, 0))
            return empty.get_height()
        y = 0
        listings = shop.visible_listings(self.ctx.state.events.flags)
        if not listings:
            empty = self.ctx.fonts.render("（今天沒貨。）", 18,
                                           self.ctx.theme.text_mute)
            surface.blit(empty, (0, 0))
            return empty.get_height()
        for l in listings:
            item = self.ctx.state.items.get(l.item)
            name = item.name if item else l.item
            desc = item.description if item else ""
            can_afford = self.ctx.state.resources.can_afford(shop.currency,
                                                              l.price)
            y = self._draw_row(surface, y, side="buy",
                               item_id=l.item, name=name, desc=desc,
                               price=l.price, can_afford=can_afford)
        return y

    def _draw_sell(self, surface: pygame.Surface) -> int:
        shop = self._shop()
        if shop is None or shop.buy_back_ratio <= 0:
            empty = self.ctx.fonts.render("（這家店不回收物品。）",
                                           18, self.ctx.theme.text_mute)
            surface.blit(empty, (0, 0))
            return empty.get_height()
        y = 0
        any_row = False
        for item_id, owned in self.ctx.state.inventory.list_owned():
            item = self.ctx.state.items.get(item_id)
            if item is None or item.locked:
                continue
            base_value = item.prices.get(shop.currency, item.value)
            price = round(base_value * shop.buy_back_ratio)
            if price <= 0:
                continue
            y = self._draw_row(surface, y, side="sell",
                               item_id=item_id, name=item.name,
                               desc=item.description, price=price,
                               owned=owned, can_afford=True)
            any_row = True
        if not any_row:
            empty = self.ctx.fonts.render("（你沒有可以賣的東西。）",
                                           18, self.ctx.theme.text_mute)
            surface.blit(empty, (0, 0))
            return empty.get_height()
        return y

    # ----- lifecycle -------------------------------------------------------

    def update(self, dt: float, inp) -> None:
        if inp.cancel and self.on_close:
            self.on_close()
            return
        self.close_btn.update(dt, inp)
        self._buy_scroll.update(dt, inp)
        self._sell_scroll.update(dt, inp)
        if inp.mouse_clicked:
            for rect, side, item_id, price in self._row_rects:
                if rect.collidepoint(inp.mouse_pos):
                    if side == "buy":
                        self._buy(item_id, price)
                    else:
                        self._sell(item_id, price)
                    return

    def draw(self, surface: pygame.Surface) -> None:
        # The drawers reset hit-rects each frame so clicks always match
        # the currently-visible row layout.
        self._row_rects = []
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 170))
        surface.blit(veil, (0, 0))
        self._panel.draw(surface)
        npc = self._npc()
        title_text = (f"商店 · {npc.name if npc else self.npc_id}")
        title = self.ctx.fonts.render(title_text,
                                       self.ctx.config.font_size_header,
                                       self.ctx.theme.accent, bold=True)
        surface.blit(title, (self._panel_rect.x + 32, self._panel_rect.y + 28))
        # balance display
        bal = self.ctx.fonts.render(
            f"持有：{self._currency_label()}", 18,
            self.ctx.theme.accent_warm,
        )
        surface.blit(bal, (self._panel_rect.x + 32,
                           self._panel_rect.y + 72))
        # greeting
        shop = self._shop()
        if shop and shop.greeting:
            g = self.ctx.fonts.render(shop.greeting, 14,
                                       self.ctx.theme.text_mute)
            surface.blit(g, (self._panel_rect.x + 32 + bal.get_width() + 20,
                             self._panel_rect.y + 76))
        self.close_btn.draw(surface)
        # column headers
        bh = self.ctx.fonts.render("買入", 20,
                                    self.ctx.theme.accent, bold=True)
        sh = self.ctx.fonts.render("賣出", 20,
                                    self.ctx.theme.accent, bold=True)
        surface.blit(bh, (self._buy_rect.x, self._buy_rect.y - 34))
        surface.blit(sh, (self._sell_rect.x, self._sell_rect.y - 34))
        self._buy_scroll.draw(surface)
        self._sell_scroll.draw(surface)

    def describe(self) -> dict:
        return {"scene": "ShopScene", "npc": self.npc_id,
                "currency": self._currency(), "balance": self._balance()}
