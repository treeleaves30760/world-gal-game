"""Scrollback overlay — every dialogue line the player has seen.

Activated by mouse-wheel-up in the dialogue scene (or by the `B` key).
Closes on Esc or mouse-wheel-down at the bottom of history. Limited by
``GameState.dialogue_history.max_lines`` (default 500).
"""
from __future__ import annotations

import pygame

from .base import Scene, SceneContext
from ..ui.widgets import Button, Panel, ScrollArea


class ScrollbackScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True

    def enter(self, *, on_close=None, **_) -> None:
        self.on_close = on_close
        sw, sh = self.ctx.screen_size
        self._panel_rect = pygame.Rect(80, 40, sw - 160, sh - 80)
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
        self._scroll = ScrollArea(
            pygame.Rect(self._panel_rect.x + 30, self._panel_rect.y + 70,
                        self._panel_rect.width - 60,
                        self._panel_rect.height - 100),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
        )
        self._scroll.set_drawer(self._draw_content)
        # Start scrolled to the bottom (most recent) so the user lands on
        # the latest line.
        self._initial_scroll_done = False

    def _draw_content(self, surface: pygame.Surface) -> int:
        history = self.ctx.state.dialogue_history.lines
        from ..ui.widgets.label import _wrap_lines
        body_font = self.ctx.fonts.get(18)
        max_w = surface.get_width() - 40
        y = 0
        for entry in history:
            speaker = entry.get("speaker")
            text = entry.get("text", "")
            # speaker badge
            if speaker:
                sp = self.ctx.fonts.render(speaker, 18,
                                            self.ctx.theme.accent, bold=True)
                surface.blit(sp, (0, y))
                y += sp.get_height() + 2
            wrapped = _wrap_lines(text, body_font, max_w)
            for line in wrapped:
                surf = body_font.render(line, True, self.ctx.theme.text)
                surface.blit(surf, (20 if speaker else 0, y))
                y += body_font.get_linesize()
            y += 10   # gap between entries
        if not history:
            empty = self.ctx.fonts.render(
                "（還沒有任何對話。）",
                18, self.ctx.theme.text_mute,
            )
            surface.blit(empty, (0, 0))
            y = empty.get_height()
        return y

    def update(self, dt: float, inp) -> None:
        if inp.cancel and self.on_close:
            self.on_close()
            return
        if not self._initial_scroll_done:
            # Trigger one draw to populate content_height, then jump to bottom.
            self._scroll.update(0.0, inp)
            ghost = pygame.Surface((self._scroll.rect.width, 8000),
                                    pygame.SRCALPHA)
            self._scroll.content_height = int(self._draw_content(ghost))
            self._scroll.scroll_y = max(0, self._scroll.content_height
                                          - self._scroll.rect.height)
            self._initial_scroll_done = True
        self.close_btn.update(dt, inp)
        self._scroll.update(dt, inp)

    def draw(self, surface: pygame.Surface) -> None:
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 170))
        surface.blit(veil, (0, 0))
        self._panel.draw(surface)
        title = self.ctx.fonts.render(
            "對話回顧 (Scrollback)",
            self.ctx.config.font_size_header,
            self.ctx.theme.accent, bold=True,
        )
        surface.blit(title, (self._panel_rect.x + 32, self._panel_rect.y + 28))
        self.close_btn.draw(surface)
        self._scroll.draw(surface)

    def describe(self) -> dict:
        return {"scene": "ScrollbackScene",
                "lines": len(self.ctx.state.dialogue_history.lines)}
