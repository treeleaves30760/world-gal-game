"""Dialogue text box.

Displays a speaker name (top-left of the panel) plus a wrapped text body
with typewriter reveal. Pressing space / clicking advances:
- first press: reveal all text instantly
- next press: advance to next line
"""
from __future__ import annotations

import pygame

from .base import Widget
from .label import WrappedText
from .panel import Panel
from ..fonts import FontRegistry
from ..theme import Theme


class DialogueBox(Widget):
    def __init__(self, rect: pygame.Rect, *, fonts: FontRegistry, theme: Theme,
                 text_speed: float = 45.0):
        super().__init__(rect)
        self.fonts = fonts
        self.theme = theme
        self.text_speed = text_speed   # chars/sec; 0 = instant
        self.panel = Panel(rect, theme,
                           fill=(*theme.bg_panel[:3], 240),
                           border=theme.border_strong,
                           radius=theme.radius_l,
                           border_width=2)
        speaker_h = theme.pad_l + 14
        pad = theme.pad_l
        body_rect = pygame.Rect(rect.x + pad, rect.y + pad + speaker_h,
                                rect.width - pad * 2,
                                rect.height - pad * 2 - speaker_h - 30)
        self.body = WrappedText(body_rect, "",
                                fonts=fonts, size=24,
                                color=theme.text, line_spacing=8)
        self.speaker: str | None = None
        self._reveal_t = 0.0
        self._chars_visible = 0
        self._hint_t = 0.0

    def set_line(self, speaker: str | None, text: str) -> None:
        self.speaker = speaker
        self.body.set_text(text, reveal_chars=0)
        self._reveal_t = 0.0
        self._chars_visible = 0

    def fully_revealed(self) -> bool:
        return self.body.fully_revealed()

    def force_reveal(self) -> None:
        self._chars_visible = self.body.total_chars() + 1
        self.body.set_reveal(self._chars_visible)

    def update(self, dt: float, inp) -> None:
        self._hint_t += dt
        if self.text_speed <= 0:
            self.force_reveal()
            return
        self._reveal_t += dt
        target = int(self._reveal_t * self.text_speed)
        # Monotonic: never roll back. force_reveal() bumps _chars_visible to
        # total+1; without this guard the next frame would compute a small
        # target and overwrite the full reveal.
        if target > self._chars_visible:
            self._chars_visible = target
            self.body.set_reveal(self._chars_visible)

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        self.panel.draw(surface)
        pad = self.theme.pad_l
        if self.speaker:
            sp = self.fonts.render(self.speaker,
                                   self.theme.pad_l + 10,
                                   self.theme.accent,
                                   bold=True)
            surface.blit(sp, (self.rect.x + pad, self.rect.y + pad))
            # underline
            uy = self.rect.y + pad + sp.get_height() + 2
            pygame.draw.line(surface,
                             (*self.theme.accent[:3], 200),
                             (self.rect.x + pad, uy),
                             (self.rect.x + pad + sp.get_width(), uy),
                             1)
        self.body.draw(surface)
        # advance hint
        if self.body.fully_revealed():
            t = self._hint_t * 2
            alpha = 120 + int(80 * abs(((t) % 2) - 1))
            hint = self.fonts.render("按 Space / 點擊 繼續",
                                     16,
                                     (*self.theme.text_mute[:3], alpha))
            surface.blit(hint, (self.rect.right - hint.get_width() - pad,
                                self.rect.bottom - hint.get_height() - 8))
