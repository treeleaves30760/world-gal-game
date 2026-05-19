"""Clue / journal overlay scene.

The journal is the player's curated guidance log: forward-looking hints
about what to do next plus the trail of important things they've
learned. Distinct from the chronological event log (every event in
order) and quest log (only formal Quest objects).
"""
from __future__ import annotations

import pygame

from .base import Scene, SceneContext
from ..ui.widgets import Button, Panel, ClueLog


class CluesScene(Scene):
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
            fill=(*self.ctx.theme.bg_overlay[:3], 238),
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
        # Mark-all-read sweep so the badge dot clears as soon as the
        # player opens the journal at all. Per-entry mark_read still
        # fires on selection (kept for clarity / future filters).
        self.ctx.state.clues.mark_all_read()
        content_rect = pygame.Rect(
            self._panel_rect.x + 24,
            self._panel_rect.y + 80,
            self._panel_rect.width - 48,
            self._panel_rect.height - 100,
        )
        self._clue_log = ClueLog(content_rect, fonts=self.ctx.fonts,
                                 theme=self.ctx.theme)
        self._clue_log.bind(self.ctx.state.clues, self.ctx.state)

    def update(self, dt: float, inp) -> None:
        if inp.cancel and self.on_close:
            self.on_close()
            return
        # Hotkey J also closes the panel (toggle behaviour).
        for e in inp.events:
            if e.type == pygame.KEYDOWN and e.key == pygame.K_j \
                    and self.on_close:
                self.on_close()
                return
        self.close_btn.update(dt, inp)
        self._clue_log.update(dt, inp)

    def draw(self, surface: pygame.Surface) -> None:
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 165))
        surface.blit(veil, (0, 0))
        self._panel.draw(surface)
        title = self.ctx.fonts.render(
            "線索筆記",
            self.ctx.config.font_size_header,
            self.ctx.theme.accent, bold=True,
        )
        surface.blit(title, (self._panel_rect.x + 32,
                             self._panel_rect.y + 24))
        # Count active clues so the player gets a quick sense of "how
        # many things am I supposed to be doing"
        entries = self._clue_log._entries()
        active_n = sum(1 for _, s in entries if s == "active")
        if entries:
            cnt = self.ctx.fonts.render(
                f"進行中 {active_n} / 共 {len(entries)}",
                15, self.ctx.theme.accent_warm,
            )
            surface.blit(cnt, (self._panel_rect.x + 200,
                               self._panel_rect.y + 32))
        hint = self.ctx.fonts.render(
            "粉色 = 目前可推進的線索；灰色 = 已經解開的線索。",
            13, self.ctx.theme.text_mute,
        )
        surface.blit(hint, (self._panel_rect.x + 32,
                            self._panel_rect.y + 56))
        self.close_btn.draw(surface)
        self._clue_log.draw(surface)

    def describe(self) -> dict:
        ct = self.ctx.state.clues
        entries = ct.journal(self.ctx.state)
        return {
            "scene": "CluesScene",
            "active": [c.id for c, s in entries if s == "active"],
            "resolved": [c.id for c, s in entries if s == "resolved"],
            "unread": list(ct.unread),
        }
