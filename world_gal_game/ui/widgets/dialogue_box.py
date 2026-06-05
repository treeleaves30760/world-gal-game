"""Dialogue text box.

Displays a speaker name (top-left of the panel) plus a wrapped text body
with typewriter reveal. Pressing space / clicking advances:
- first press: reveal all text instantly
- next press: advance to next line
"""
from __future__ import annotations

import pygame

from .base import Widget
from .rich_text_view import RichText
from .panel import Panel
from ..fonts import FontRegistry
from ..theme import Theme


class DialogueBox(Widget):
    def __init__(self, rect: pygame.Rect, *, fonts: FontRegistry, theme: Theme,
                 text_speed: float = 45.0, text_scale: float = 1.0):
        super().__init__(rect)
        self.fonts = fonts
        self.theme = theme
        self.text_speed = text_speed   # chars/sec; 0 = instant
        self.text_scale = text_scale   # accessibility text-size multiplier
        self.panel = Panel(rect, theme,
                           fill=(*theme.bg_panel[:3], 240),
                           border=theme.border_strong,
                           radius=theme.radius_l,
                           border_width=2)
        pad = theme.pad_l
        # The speaker sits in a name-plate straddling the box's top edge
        # (drawn in draw()), so the body uses the full inner height.
        body_rect = pygame.Rect(rect.x + pad + 6, rect.y + pad,
                                rect.width - pad * 2 - 12,
                                rect.height - pad * 2 - 16)
        self.body = RichText(body_rect, "",
                             fonts=fonts, size=int(26 * text_scale),
                             color=theme.text,
                             line_spacing=int(10 * text_scale),
                             text_speed=text_speed)
        self.speaker: str | None = None
        # Optional per-speaker name-plate colour (RGB tuple); None = theme accent.
        self.speaker_color: tuple | None = None
        self._hint_t = 0.0

    def set_line(self, speaker: str | None, text: str,
                 *, speaker_color: tuple | None = None) -> None:
        self.speaker = speaker
        self.speaker_color = speaker_color
        # Reveal timing now flows through RichText.update(dt): the body drives
        # its own cursor honouring per-span speed and inline waits.
        self.body.text_speed = self.text_speed
        self.body.set_text(text, reveal_chars=0)

    def fully_revealed(self) -> bool:
        return self.body.fully_revealed()

    def force_reveal(self) -> None:
        # A click / advance during a wait completes the line instantly and
        # clears any pending waits (handled inside RichText.force_reveal).
        self.body.force_reveal()

    def update(self, dt: float, inp) -> None:
        self._hint_t += dt
        if self.text_speed <= 0:
            self.force_reveal()
            return
        self.body.update(dt, inp)

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        self.panel.draw(surface)
        # Speaker-coloured accent bar along the left inner edge — a VN textbox
        # motif that ties the box to the active speaker's colour.
        accent = self.speaker_color or self.theme.accent
        bar = pygame.Surface((4, max(1, self.rect.height - 28)), pygame.SRCALPHA)
        bar.fill((*accent[:3], 175))
        surface.blit(bar, (self.rect.x + 16, self.rect.y + 14))
        pad = self.theme.pad_l
        if self.speaker:
            # Name-plate: a filled accent tab straddling the box's top edge —
            # the classic VN speaker label. Name in light text for contrast.
            name = self.fonts.render(self.speaker, int(22 * self.text_scale),
                                     self.theme.text, bold=True)
            plate_w = name.get_width() + 40
            plate_h = int(42 * self.text_scale)
            px = self.rect.x + 30
            py = self.rect.y - plate_h // 2
            plate = pygame.Surface((plate_w, plate_h), pygame.SRCALPHA)
            plate_col = self.speaker_color or self.theme.accent
            pygame.draw.rect(plate, (*plate_col[:3], 240),
                             plate.get_rect(),
                             border_radius=self.theme.radius_m)
            pygame.draw.rect(plate, (*self.theme.text[:3], 50),
                             plate.get_rect(), 1,
                             border_radius=self.theme.radius_m)
            surface.blit(plate, (px, py))
            surface.blit(name, (px + 20,
                                py + (plate_h - name.get_height()) // 2))
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
