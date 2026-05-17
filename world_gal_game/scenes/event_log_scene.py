"""Event log overlay: scrollable timeline of recorded events."""
from __future__ import annotations

import pygame

from .base import Scene, SceneContext
from ..ui.widgets import Button, Panel, ScrollArea


class EventLogScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True

    def enter(self, *, on_close=None, **_) -> None:
        self.on_close = on_close
        sw, sh = self.ctx.screen_size
        self._panel_rect = pygame.Rect(120, 60, sw - 240, sh - 120)
        self._panel = Panel(self._panel_rect, self.ctx.theme,
                            fill=(*self.ctx.theme.bg_overlay[:3], 235),
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

    def _kind_color(self, kind: str):
        return {
            "scene": self.ctx.theme.accent_warm,
            "choice": self.ctx.theme.accent_alt,
            "dialogue": self.ctx.theme.text_mute,
            "location": self.ctx.theme.good,
            "unlock": self.ctx.theme.accent,
            "custom": self.ctx.theme.accent,
        }.get(kind, self.ctx.theme.text_mute)

    def _draw_content(self, surface: pygame.Surface) -> int:
        entries = list(reversed(self.ctx.state.events.entries))
        y = 0
        for e in entries:
            color = self._kind_color(e.kind)
            line_h = 56 if not e.summary else 78
            row = pygame.Surface((self._scroll.rect.width - 14, line_h),
                                 pygame.SRCALPHA)
            pygame.draw.rect(row, (255, 255, 255, 14),
                             row.get_rect(),
                             border_radius=self.ctx.theme.radius_s)
            pygame.draw.rect(row, (*color[:3], 220),
                             (0, 0, 4, row.get_height()),
                             border_radius=self.ctx.theme.radius_s)
            title = self.ctx.fonts.render(e.title, 18,
                                          self.ctx.theme.text, bold=True)
            row.blit(title, (14, 6))
            meta = []
            meta.append(f"[{e.kind}]")
            if e.location:
                meta.append(e.location)
            if e.timestamp:
                meta.append(e.timestamp.replace("T", " ")[:19])
            meta_surf = self.ctx.fonts.render(" · ".join(meta), 13,
                                              self.ctx.theme.text_dim)
            row.blit(meta_surf, (14, 6 + title.get_height() + 2))
            if e.summary:
                summ = self.ctx.fonts.render(e.summary[:80], 14,
                                             self.ctx.theme.text_mute)
                row.blit(summ, (14, 6 + title.get_height() + meta_surf.get_height() + 6))
            surface.blit(row, (0, y))
            y += line_h + 6
        if not entries:
            empty = self.ctx.fonts.render(
                "（事件記錄是空的。去校園裡走走吧。）",
                18, self.ctx.theme.text_mute,
            )
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
        title = self.ctx.fonts.render("事件記錄",
                                      self.ctx.config.font_size_header,
                                      self.ctx.theme.accent, bold=True)
        surface.blit(title, (self._panel_rect.x + 32, self._panel_rect.y + 28))
        self.close_btn.draw(surface)
        self._scroll.draw(surface)

    def describe(self) -> dict:
        return {"scene": "EventLogScene",
                "entries_count": len(self.ctx.state.events.entries),
                "recent": [e.title for e in self.ctx.state.events.recent(8)]}
