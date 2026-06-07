"""Credits / 鳴謝 overlay.

A themed, scrollable credits screen reachable from the title's extras menu (next
to 流程圖 / 關係狀態) and the in-game menu. The content is entirely **pack-
supplied** and data-driven: it is resolved by :func:`world_gal_game.core.credits
.load_credits` from the pack's ``content/credits.yaml`` → ``meta.yaml``
``credits:`` / ``attribution:`` → a bundled ``CREDITS.md`` → engine defaults, so
each pack ships its own attributions (the engine carries no game-specific
strings). This satisfies attribution obligations that distribution imposes — a
pack scored to CC-BY music, for instance, *must* credit the composer in-game or
on the store page; this screen is where that credit lives.

Long credit rolls scroll (mouse wheel / drag); each section's heading is
emphasised and its body wraps to the panel width. A pack with no credits data
still renders the graceful engine-default block — safe for any pack.
"""
from __future__ import annotations

import pygame

from .base import Scene, SceneContext
from ..ui.widgets import Button, Panel, ScrollArea
from ..ui.widgets.label import _wrap_lines
from ..core.credits import Credits, load_credits


class CreditsScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        self.on_close = None
        self._credits: Credits | None = None

    # ---- data ----------------------------------------------------------
    def _load(self) -> Credits:
        """Resolve the pack's credits once per enter (cached on the scene).

        Isolated: any failure in resolution degrades to the engine default so
        the overlay never fails to open.
        """
        try:
            return load_credits(getattr(self.ctx, "meta", None) or {},
                                 self.ctx.pack_root())
        except Exception:
            from ..core.credits import _engine_default
            return _engine_default()

    # ---- lifecycle -----------------------------------------------------
    def enter(self, *, on_close=None, **_) -> None:
        self.on_close = on_close
        self._credits = self._load()
        sw, sh = self.ctx.screen_size
        self._panel_rect = pygame.Rect(140, 70, sw - 280, sh - 140)
        self._panel = Panel(self._panel_rect, self.ctx.theme,
                            fill=(*self.ctx.theme.bg_overlay[:3], 240),
                            border=self.ctx.theme.border_strong,
                            radius=self.ctx.theme.radius_l, border_width=2)
        self.close_btn = Button(
            # Standard system-overlay close button (120x36, inset 16).
            pygame.Rect(self._panel_rect.right - 120 - 16,
                        self._panel_rect.y + 16, 120, 36),
            self.ctx.localization.t("close", "關閉"),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=15, style="ghost",
            on_click=(lambda: self.on_close() if self.on_close else None),
        )
        self._scroll = ScrollArea(
            pygame.Rect(self._panel_rect.x + 36, self._panel_rect.y + 80,
                        self._panel_rect.width - 72,
                        self._panel_rect.height - 110),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
        )
        self._scroll.set_drawer(self._draw_content)

    # ---- content rendering ---------------------------------------------
    _HEADING_SIZE = 20
    _BODY_SIZE = 16

    def _draw_content(self, surface: pygame.Surface) -> int:
        theme = self.ctx.theme
        width = self._scroll.rect.width - 14
        body_font = self.ctx.fonts.get(self._BODY_SIZE)
        y = 0
        sections = self._credits.sections if self._credits else []
        if not sections:
            empty = self.ctx.fonts.render("（本作未提供製作資訊。）", 18,
                                          theme.text_mute)
            surface.blit(empty, (0, 0))
            return empty.get_height()
        for i, sec in enumerate(sections):
            if i:
                y += 16   # gap between sections
            if sec.heading:
                hsurf = self.ctx.fonts.render(sec.heading, self._HEADING_SIZE,
                                              theme.accent_warm, bold=True)
                surface.blit(hsurf, (0, y))
                y += hsurf.get_height() + 4
                # subtle underline rule under the heading
                pygame.draw.line(surface, (*theme.border_soft[:3], 120),
                                 (0, y), (width, y), 1)
                y += 8
            for line in sec.body:
                if not line.strip():
                    y += self._BODY_SIZE // 2   # blank line -> half-row gap
                    continue
                # Wrap each source line to the panel width so long licence /
                # URL / table rows stay readable instead of clipping.
                for wrapped in _wrap_lines(line, body_font, width):
                    lsurf = self.ctx.fonts.render(wrapped, self._BODY_SIZE,
                                                  theme.text)
                    surface.blit(lsurf, (0, y))
                    y += lsurf.get_height() + 3
        return y

    # ---- input / draw --------------------------------------------------
    def update(self, dt: float, inp) -> None:
        if inp.cancel and self.on_close:
            self.on_close()
            return
        self.close_btn.update(dt, inp)
        self._scroll.update(dt, inp)

    def draw(self, surface: pygame.Surface) -> None:
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 170))
        surface.blit(veil, (0, 0))
        self._panel.draw(surface)
        title_text = (self._credits.title if self._credits
                      else self.ctx.localization.t("credits", "鳴謝"))
        title = self.ctx.fonts.render(
            title_text, self.ctx.config.font_size_header,
            self.ctx.theme.accent, bold=True)
        surface.blit(title, (self._panel_rect.x + 36, self._panel_rect.y + 28))
        self.close_btn.draw(surface)
        self._scroll.draw(surface)

    def describe(self) -> dict:
        """Headless dump: the resolved credits (title + source + sections) so
        an agent / test can verify the screen sources pack-supplied content."""
        cr = self._credits if self._credits is not None else self._load()
        return {"scene": "CreditsScene", **cr.to_dict()}
