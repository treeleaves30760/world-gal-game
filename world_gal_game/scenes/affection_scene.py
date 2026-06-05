"""Affection overlay: shows all tracked characters & their affection bars."""
from __future__ import annotations

import pygame

from .base import Scene, SceneContext
from ..ui.widgets import Button, Panel, ScrollArea


class AffectionScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True

    def enter(self, *, on_close=None, **_) -> None:
        self.on_close = on_close
        sw, sh = self.ctx.screen_size
        self._panel_rect = pygame.Rect(120, 80, sw - 240, sh - 160)
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
        self._scroll = ScrollArea(
            pygame.Rect(self._panel_rect.x + 30, self._panel_rect.y + 70,
                        self._panel_rect.width - 60,
                        self._panel_rect.height - 100),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
        )
        self._scroll.set_drawer(self._draw_content)

    def _draw_content(self, surface: pygame.Surface) -> int:
        chars = list(self.ctx.state.affection.characters.values())
        y = 0
        card_h = 96
        for ca in chars:
            npc = self.ctx.npcs.get(ca.character_id)
            card = pygame.Surface((self._scroll.rect.width - 14, card_h),
                                   pygame.SRCALPHA)
            pygame.draw.rect(card, (255, 255, 255, 18),
                             card.get_rect(),
                             border_radius=self.ctx.theme.radius_m)
            pygame.draw.rect(card, self.ctx.theme.border, card.get_rect(),
                             width=1, border_radius=self.ctx.theme.radius_m)
            # portrait
            if npc and npc.portrait:
                img = self.ctx.assets.scaled(npc.portrait, (72, 72), fit="cover")
                card.blit(img, (12, 12))
            # name + label
            name = (npc.name if npc else ca.character_id)
            name_surf = self.ctx.fonts.render(name, 22, self.ctx.theme.text,
                                              bold=True)
            card.blit(name_surf, (100, 14))
            label = self.ctx.state.affection.level_label(ca.character_id)
            aff = ca.get("affection")
            lbl = self.ctx.fonts.render(f"{label} · 好感 {aff}",
                                        16, self.ctx.theme.accent_warm)
            card.blit(lbl, (100, 42))
            # affection bar
            bar_x, bar_y, bar_w, bar_h = 100, 70, card.get_width() - 130, 8
            pygame.draw.rect(card, (255, 255, 255, 30),
                             (bar_x, bar_y, bar_w, bar_h),
                             border_radius=4)
            fill_w = max(0, min(bar_w, int(bar_w * (aff / 150.0))))
            pygame.draw.rect(card, self.ctx.theme.accent,
                             (bar_x, bar_y, fill_w, bar_h),
                             border_radius=4)
            # other stats inline (trust, fear, etc.)
            sx = bar_x
            sy = 84
            for stat, val in ca.stats.items():
                if stat == "affection":
                    continue
                pill = self.ctx.fonts.render(f"{stat}: {val}", 14,
                                             self.ctx.theme.text_mute)
                card.blit(pill, (sx, sy))
                sx += pill.get_width() + 14
            surface.blit(card, (0, y))
            y += card_h + 10
        if not chars:
            empty = self.ctx.fonts.render(
                "（還沒有任何角色被記錄。先在校園裡逛逛吧。）",
                18, self.ctx.theme.text_mute)
            surface.blit(empty, (0, 0))
            y = empty.get_height()
        return y

    def update(self, dt: float, inp) -> None:
        if inp.cancel and self.on_close:
            self.on_close()
            return
        self.close_btn.update(dt, inp)
        self._scroll.update(dt, inp)

    def draw(self, surface: pygame.Surface) -> None:
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 160))
        surface.blit(veil, (0, 0))
        self._panel.draw(surface)
        title = self.ctx.fonts.render("角色關係",
                                      self.ctx.config.font_size_header,
                                      self.ctx.theme.accent, bold=True)
        surface.blit(title, (self._panel_rect.x + 32, self._panel_rect.y + 28))
        self.close_btn.draw(surface)
        self._scroll.draw(surface)

    def describe(self) -> dict:
        return {"scene": "AffectionScene",
                "stats": self.ctx.state.affection.all_stats()}
