"""Choice menu shown at a decision point in a scene."""
from __future__ import annotations

from typing import Callable

import pygame

from .base import Widget
from .button import Button
from .panel import Panel
from ..fonts import FontRegistry
from ..theme import Theme


class ChoiceMenu(Widget):
    def __init__(self, rect: pygame.Rect, *, fonts: FontRegistry, theme: Theme,
                 on_choose: Callable[[str], None]):
        super().__init__(rect)
        self.fonts = fonts
        self.theme = theme
        self.on_choose = on_choose
        self.buttons: list[Button] = []
        self.panel: Panel | None = None

    def set_choices(self, choices: list[tuple[str, str, bool]]) -> None:
        """choices: list of (choice_id, label, enabled)."""
        self.buttons = []
        n = max(1, len(choices))
        btn_w = min(720, self.rect.width - 80)
        btn_h = 56
        gap = 12
        title_h = 0
        total_h = title_h + n * btn_h + (n - 1) * gap + 40
        panel_rect = pygame.Rect(0, 0, btn_w + 80, total_h)
        panel_rect.center = self.rect.center
        self.panel = Panel(panel_rect, self.theme,
                           fill=(*self.theme.bg_overlay[:3], 220),
                           border=self.theme.border_strong,
                           radius=self.theme.radius_l, border_width=2)
        start_y = panel_rect.y + 20
        for i, (cid, label, enabled) in enumerate(choices):
            r = pygame.Rect(panel_rect.x + 40,
                            start_y + i * (btn_h + gap),
                            btn_w, btn_h)
            b = Button(r, label, fonts=self.fonts, theme=self.theme,
                       on_click=(lambda cid=cid: self.on_choose(cid))
                                if enabled else None,
                       enabled=enabled,
                       style="primary" if enabled else "ghost")
            self.buttons.append(b)

    def update(self, dt: float, inp) -> None:
        for b in self.buttons:
            b.update(dt, inp)

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        # darken background
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 150))
        surface.blit(veil, (0, 0))
        if self.panel:
            self.panel.draw(surface)
        for b in self.buttons:
            b.draw(surface)
