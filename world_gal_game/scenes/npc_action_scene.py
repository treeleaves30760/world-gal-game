"""NPC interaction popup.

Lightweight overlay shown when the player clicks an NPC card in the
exploration scene. Offers the non-LLM interactions: send a gift, browse
the NPC's shop (if any), examine the NPC. Future versions will re-add a
"chat" button when the LLM brain is wired back in.
"""
from __future__ import annotations

from typing import Callable

import pygame

from .base import Scene, SceneContext
from ..ui.widgets import Button, Panel


class NPCActionScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        self.npc_id: str | None = None
        self.on_close: Callable[[], None] | None = None
        self.on_open_shop: Callable[[str], None] | None = None
        self.on_request_gift: Callable | None = None
        self._buttons: list[Button] = []
        self._panel_rect: pygame.Rect | None = None

    def enter(self, *, npc_id: str, on_close=None,
              on_open_shop=None, on_request_gift=None, **_) -> None:
        self.npc_id = npc_id
        self.on_close = on_close
        self.on_open_shop = on_open_shop
        self.on_request_gift = on_request_gift
        self._build()

    def _build(self) -> None:
        sw, sh = self.ctx.screen_size
        pw, ph = 520, 360
        self._panel_rect = pygame.Rect((sw - pw) // 2, (sh - ph) // 2, pw, ph)
        self._panel = Panel(self._panel_rect, self.ctx.theme,
                            fill=(*self.ctx.theme.bg_panel[:3], 240),
                            border=self.ctx.theme.border_strong,
                            radius=self.ctx.theme.radius_l, border_width=2)
        npc = self.ctx.npcs.get(self.npc_id) if self.npc_id else None
        has_shop = npc is not None and getattr(npc, "shop", None) is not None
        # Buttons: gift, shop (optional), close
        btn_w, btn_h = 280, 52
        bx = self._panel_rect.centerx - btn_w // 2
        by = self._panel_rect.y + 140
        self._buttons = []
        if self.on_request_gift is not None:
            self._buttons.append(Button(
                pygame.Rect(bx, by, btn_w, btn_h),
                "送禮 (Gift)", fonts=self.ctx.fonts, theme=self.ctx.theme,
                font_size=18, style="primary",
                on_click=self._do_gift,
            ))
            by += btn_h + 10
        if has_shop and self.on_open_shop is not None:
            self._buttons.append(Button(
                pygame.Rect(bx, by, btn_w, btn_h),
                "看貨 (Shop)", fonts=self.ctx.fonts, theme=self.ctx.theme,
                font_size=18, style="primary",
                on_click=lambda: self.on_open_shop(self.npc_id) if self.npc_id else None,
            ))
            by += btn_h + 10
        self._buttons.append(Button(
            pygame.Rect(bx, by, btn_w, btn_h),
            "離開 (Esc)", fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=18, style="ghost",
            on_click=lambda: self.on_close() if self.on_close else None,
        ))

    def _do_gift(self) -> None:
        if self.on_request_gift is None or self.npc_id is None:
            return
        self.on_request_gift(self.npc_id, self._after_gift_picked)

    def _after_gift_picked(self, item_id: str) -> None:
        from ..core.story_graph import Effect
        if self.npc_id is None:
            return
        self.ctx.state.apply_all([
            Effect(kind="gift", target=self.npc_id, stat=item_id),
        ])

    def update(self, dt: float, inp) -> None:
        if inp.cancel and self.on_close:
            self.on_close()
            return
        for b in self._buttons:
            b.update(dt, inp)

    def draw(self, surface: pygame.Surface) -> None:
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 170))
        surface.blit(veil, (0, 0))
        self._panel.draw(surface)
        # Title block: NPC name + affection label + present location
        npc = self.ctx.npcs.get(self.npc_id) if self.npc_id else None
        name = npc.name if npc else (self.npc_id or "?")
        title = self.ctx.fonts.render(
            name, self.ctx.config.font_size_header,
            self.ctx.theme.accent, bold=True,
        )
        surface.blit(title, (self._panel_rect.x + 32, self._panel_rect.y + 28))
        if self.npc_id is not None:
            level = self.ctx.state.affection.level_label(self.npc_id)
            value = self.ctx.state.affection.get(self.npc_id)
            sub = self.ctx.fonts.render(
                f"好感 {level} ({value})", 18, self.ctx.theme.text_mute,
            )
            surface.blit(sub, (self._panel_rect.x + 32,
                               self._panel_rect.y + 28 + title.get_height() + 6))
        for b in self._buttons:
            b.draw(surface)

    def describe(self) -> dict:
        return {"scene": "NPCActionScene", "npc": self.npc_id}
