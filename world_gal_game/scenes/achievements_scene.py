"""Achievements overlay: list of unlocked + locked achievements."""
from __future__ import annotations

import pygame

from .base import Scene, SceneContext
from ..ui.widgets import Button, Panel, ScrollArea


class AchievementsScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True

    def enter(self, *, on_close=None, **_) -> None:
        self.on_close = on_close
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
        self._scroll = ScrollArea(
            pygame.Rect(self._panel_rect.x + 30, self._panel_rect.y + 70,
                        self._panel_rect.width - 60,
                        self._panel_rect.height - 100),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
        )
        self._scroll.set_drawer(self._draw_content)
        # Mark all newly-unlocked as seen so the toast indicator clears.
        for ach in self.ctx.state.achievements.newly_unlocked():
            self.ctx.state.achievements.mark_seen(ach.id)

    def _draw_content(self, surface: pygame.Surface) -> int:
        tracker = self.ctx.state.achievements
        y = 0
        card_h = 78
        items = tracker.visible_to_player()
        items.sort(key=lambda a: (a.id not in tracker.unlocked, a.title))
        for ach in items:
            unlocked = ach.id in tracker.unlocked
            card = pygame.Surface((self._scroll.rect.width - 14, card_h),
                                  pygame.SRCALPHA)
            tint = (*self.ctx.theme.accent_warm[:3], 60) if unlocked \
                else (*self.ctx.theme.text_dim[:3], 30)
            pygame.draw.rect(card, tint, card.get_rect(),
                             border_radius=self.ctx.theme.radius_m)
            pygame.draw.rect(card,
                             self.ctx.theme.border if unlocked
                             else self.ctx.theme.border_soft,
                             card.get_rect(), width=1,
                             border_radius=self.ctx.theme.radius_m)
            # icon (or sigil)
            if ach.icon:
                img = self.ctx.assets.scaled(ach.icon, (54, 54), fit="cover")
                card.blit(img, (12, 12))
            else:
                # Draw a coloured chip with a single-character label.
                chip = pygame.Surface((54, 54), pygame.SRCALPHA)
                chip_color = (self.ctx.theme.accent_warm if unlocked
                              else self.ctx.theme.text_dim)
                pygame.draw.rect(chip, (*chip_color[:3], 60),
                                 chip.get_rect(),
                                 border_radius=self.ctx.theme.radius_s)
                pygame.draw.rect(chip, (*chip_color[:3], 220),
                                 chip.get_rect(), width=2,
                                 border_radius=self.ctx.theme.radius_s)
                letter = self.ctx.fonts.render(
                    "成" if unlocked else "?", 28,
                    chip_color, bold=True,
                )
                chip.blit(letter, ((54 - letter.get_width()) // 2,
                                   (54 - letter.get_height()) // 2))
                card.blit(chip, (12, 12))
            title = ach.title if unlocked else "？？？"
            title_color = self.ctx.theme.text if unlocked \
                else self.ctx.theme.text_dim
            t = self.ctx.fonts.render(title, 22, title_color, bold=True)
            card.blit(t, (80, 10))
            desc = ach.description if unlocked else "（尚未解鎖）"
            d = self.ctx.fonts.render(desc[:60], 15,
                                      self.ctx.theme.text_mute if unlocked
                                      else self.ctx.theme.text_dim)
            card.blit(d, (80, 38))
            if unlocked:
                ts = tracker.unlocked.get(ach.id, "")[:19].replace("T", " ")
                ts_surf = self.ctx.fonts.render(ts, 12, self.ctx.theme.text_dim)
                card.blit(ts_surf, (80, 58))
            surface.blit(card, (0, y))
            y += card_h + 8
        if not items:
            empty = self.ctx.fonts.render(
                "（這個遊戲沒有設定成就。）",
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
        title = self.ctx.fonts.render(
            self.ctx.localization.t("achievements", "成就"),
            self.ctx.config.font_size_header,
            self.ctx.theme.accent, bold=True,
        )
        surface.blit(title, (self._panel_rect.x + 32, self._panel_rect.y + 28))
        # status counter
        tracker = self.ctx.state.achievements
        unlocked_n = len(tracker.unlocked)
        total_n = len(tracker.achievements)
        if total_n:
            cnt = self.ctx.fonts.render(
                f"{unlocked_n} / {total_n}", 18, self.ctx.theme.accent_warm,
            )
            surface.blit(cnt, (self._panel_rect.x + 32, self._panel_rect.y + 70))
        self.close_btn.draw(surface)
        self._scroll.draw(surface)

    def describe(self) -> dict:
        return {"scene": "AchievementsScene",
                "unlocked": list(self.ctx.state.achievements.unlocked.keys())}
