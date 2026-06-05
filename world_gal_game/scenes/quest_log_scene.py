"""Quest log overlay scene."""
from __future__ import annotations

import pygame

from .base import Scene, SceneContext
from ..ui.widgets import Button, Panel, QuestLog


class QuestLogScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        self.on_close = None

    def enter(self, *, on_close=None, **_) -> None:
        self.on_close = on_close
        sw, sh = self.ctx.screen_size
        self._panel_rect = pygame.Rect(80, 50, sw - 160, sh - 100)
        self._panel = Panel(
            self._panel_rect, self.ctx.theme,
            fill=(*self.ctx.theme.bg_overlay[:3], 240),
            border=self.ctx.theme.border_strong,
            radius=self.ctx.theme.radius_l, border_width=2,
        )
        self.close_btn = Button(
            pygame.Rect(self._panel_rect.right - 120 - 16,
                        self._panel_rect.y + 16, 120, 36),
            self.ctx.localization.t("close", "關閉 (Esc)"),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=15, style="ghost",
            on_click=(lambda: self.on_close() if self.on_close else None),
        )
        content_rect = pygame.Rect(
            self._panel_rect.x + 24,
            self._panel_rect.y + 70,
            self._panel_rect.width - 48,
            self._panel_rect.height - 90,
        )
        self._quest_log = QuestLog(
            content_rect,
            fonts=self.ctx.fonts,
            theme=self.ctx.theme,
        )
        self._quest_log.bind(self.ctx.state.quests)

    # ------------------------------------------------------------------

    def update(self, dt: float, inp) -> None:
        if inp.cancel and self.on_close:
            self.on_close()
            return
        self.close_btn.update(dt, inp)
        self._quest_log.update(dt, inp)

    def draw(self, surface: pygame.Surface) -> None:
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 165))
        surface.blit(veil, (0, 0))
        self._panel.draw(surface)
        title = self.ctx.fonts.render(
            "任務記錄",
            self.ctx.config.font_size_header,
            self.ctx.theme.accent, bold=True,
        )
        surface.blit(title, (self._panel_rect.x + 32,
                             self._panel_rect.y + 24))
        # Active quest count
        active_n = len(self.ctx.state.quests.active())
        if active_n:
            cnt = self.ctx.fonts.render(
                f"進行中：{active_n}", 16, self.ctx.theme.accent_warm,
            )
            surface.blit(cnt, (self._panel_rect.x + 200,
                               self._panel_rect.y + 30))
        self.close_btn.draw(surface)
        self._quest_log.draw(surface)

    def describe(self) -> dict:
        active = [q.id for q in self.ctx.state.quests.active()]
        return {"scene": "QuestLogScene", "active_quests": active}
