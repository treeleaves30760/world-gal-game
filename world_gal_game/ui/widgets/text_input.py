"""Single-line text input box.

Handles focus, the system caret (pygame.key.start_text_input), backspace,
delete, left/right arrows, and a blinking cursor.
"""
from __future__ import annotations

import pygame

from .base import Widget
from ..fonts import FontRegistry
from ..theme import Theme


class TextInput(Widget):
    def __init__(self, rect: pygame.Rect, *, fonts: FontRegistry, theme: Theme,
                 placeholder: str = "", initial: str = "",
                 max_length: int = 64,
                 font_size: int | None = None,
                 on_submit=None):
        super().__init__(rect)
        self.fonts = fonts
        self.theme = theme
        self.placeholder = placeholder
        self.text = initial
        self.max_length = max_length
        self.font_size = font_size or theme.pad_m + 14
        self.on_submit = on_submit
        self.focused = False
        self.cursor_t = 0.0
        self._caret = len(initial)

    def focus(self) -> None:
        if self.focused:
            return
        self.focused = True
        pygame.key.start_text_input()
        pygame.key.set_text_input_rect(self.rect)

    def unfocus(self) -> None:
        if not self.focused:
            return
        self.focused = False
        pygame.key.stop_text_input()

    def update(self, dt: float, inp) -> None:
        self.cursor_t += dt
        # Click to focus/unfocus.
        if inp.mouse_clicked:
            if self.rect.collidepoint(inp.mouse_pos):
                self.focus()
            else:
                self.unfocus()
        if not self.focused:
            return
        # Append typed text.
        if inp.text_input:
            keep = self.max_length - len(self.text)
            if keep > 0:
                addition = inp.text_input[:keep]
                self.text = self.text[:self._caret] + addition + self.text[self._caret:]
                self._caret += len(addition)
        for e in inp.events:
            if e.type == pygame.KEYDOWN:
                if e.key == pygame.K_BACKSPACE:
                    if self._caret > 0:
                        self.text = self.text[:self._caret - 1] + self.text[self._caret:]
                        self._caret -= 1
                elif e.key == pygame.K_DELETE:
                    self.text = self.text[:self._caret] + self.text[self._caret + 1:]
                elif e.key == pygame.K_LEFT:
                    self._caret = max(0, self._caret - 1)
                elif e.key == pygame.K_RIGHT:
                    self._caret = min(len(self.text), self._caret + 1)
                elif e.key == pygame.K_HOME:
                    self._caret = 0
                elif e.key == pygame.K_END:
                    self._caret = len(self.text)
                elif e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    if self.on_submit is not None:
                        self.on_submit(self.text)

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        bg = pygame.Surface(self.rect.size, pygame.SRCALPHA)
        border = self.theme.border if self.focused else self.theme.border_soft
        pygame.draw.rect(bg, (255, 255, 255, 16), bg.get_rect(),
                         border_radius=self.theme.radius_s)
        pygame.draw.rect(bg, border, bg.get_rect(),
                         width=1, border_radius=self.theme.radius_s)
        surface.blit(bg, self.rect.topleft)

        font = self.fonts.get(self.font_size)
        if self.text:
            text_surf = font.render(self.text, True, self.theme.text)
            color = self.theme.text
        else:
            text_surf = font.render(self.placeholder, True, self.theme.text_dim)
            color = self.theme.text_dim
        surface.blit(text_surf, (self.rect.x + self.theme.pad_m,
                                 self.rect.y + (self.rect.height
                                                - text_surf.get_height()) // 2))
        # cursor
        if self.focused and int(self.cursor_t * 2) % 2 == 0:
            pre = font.render(self.text[:self._caret], True, color)
            cx = self.rect.x + self.theme.pad_m + pre.get_width()
            cy = self.rect.y + 6
            pygame.draw.line(surface, color, (cx, cy),
                             (cx, self.rect.bottom - 6), 2)
