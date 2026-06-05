"""First-run control onboarding.

Shown once, over the first scene of a new game (gated by ``config.seen_intro``),
so a Steam player who has never touched a visual novel learns how to advance,
auto/skip, open the backlog, and roll back — without digging into Settings.
Closing it sets the persisted flag so it never appears again.
"""
from __future__ import annotations

import pygame

from .base import Scene, SceneContext
from ..ui.widgets import Button, Panel


class OnboardingScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True

    _ROWS = [
        ("推進對話", "空白鍵 / Enter / 滑鼠左鍵 / 點擊畫面"),
        ("自動播放", "A 鍵 — 自動播放台詞（再按一次取消）"),
        ("快進已讀", "按住 Ctrl — 略過已讀內容"),
        ("對話記錄", "滑鼠滾輪上 / B 鍵 — 回顧並重聽"),
        ("回上一句", "Backspace — 倒回到上一句（rollback）"),
        ("選單 / 設定", "右下工具列，或 Esc 開啟選單"),
    ]

    def enter(self, *, on_close=None, **_) -> None:
        self.on_close = on_close
        sw, sh = self.ctx.screen_size
        pw, ph = min(720, sw - 120), min(560, sh - 120)
        self._panel_rect = pygame.Rect(0, 0, pw, ph)
        self._panel_rect.center = (sw // 2, sh // 2)
        self._panel = Panel(self._panel_rect, self.ctx.theme,
                            fill=(*self.ctx.theme.bg_overlay[:3], 240),
                            border=self.ctx.theme.border_strong,
                            radius=self.ctx.theme.radius_l, border_width=2)
        bw = 220
        self.start_btn = Button(
            pygame.Rect(self._panel_rect.centerx - bw // 2,
                        self._panel_rect.bottom - 70, bw, 48),
            "開始遊戲", fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=18, style="primary",
            on_click=(lambda: on_close() if on_close else None))

    def update(self, dt: float, inp) -> None:
        # Esc or advance both dismiss — a player who just hits Space should not
        # be trapped behind the card.
        if (inp.cancel or inp.advance_dialogue) and self.on_close:
            self.on_close()
            return
        self.start_btn.update(dt, inp)

    def draw(self, surface: pygame.Surface) -> None:
        theme = self.ctx.theme
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 200))
        surface.blit(veil, (0, 0))
        self._panel.draw(surface)
        x = self._panel_rect.x + 44
        y = self._panel_rect.y + 30
        surface.blit(self.ctx.fonts.render("操作說明", 30, theme.accent,
                                           bold=True), (x, y))
        surface.blit(self.ctx.fonts.render(
            "歡迎遊玩。這些是基本操作（隨時可在設定查看）：", 15,
            theme.text_mute), (x, y + 44))
        ry = y + 86
        key_w = 150
        for key, desc in self._ROWS:
            chip = pygame.Surface((key_w, 38), pygame.SRCALPHA)
            pygame.draw.rect(chip, (*theme.accent[:3], 55), chip.get_rect(),
                             border_radius=theme.radius_s)
            pygame.draw.rect(chip, (*theme.accent[:3], 200), chip.get_rect(),
                             width=1, border_radius=theme.radius_s)
            ks = self.ctx.fonts.render(key, 16, theme.text, bold=True)
            chip.blit(ks, ((key_w - ks.get_width()) // 2,
                           (38 - ks.get_height()) // 2))
            surface.blit(chip, (x, ry))
            surface.blit(self.ctx.fonts.render(desc, 16, theme.text),
                         (x + key_w + 18, ry + 8))
            ry += 50
        self.start_btn.draw(surface)

    def describe(self) -> dict:
        return {"scene": "OnboardingScene"}
