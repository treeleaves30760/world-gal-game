"""Vertical menu list — used by the title screen, load menu, etc.

Keyboard navigable (up/down/Enter) and mouse-clickable.
"""
from __future__ import annotations

from typing import Callable

import pygame

from .base import Widget
from ..fonts import FontRegistry
from ..input_bindings import InputAction
from ..theme import Theme


class MenuItem:
    def __init__(self, label: str, on_select: Callable[[], None],
                 *, enabled: bool = True):
        self.label = label
        self.on_select = on_select
        self.enabled = enabled


class MenuList(Widget):
    def __init__(self, rect: pygame.Rect, items: list[MenuItem], *,
                 fonts: FontRegistry, theme: Theme,
                 font_size: int | None = None,
                 row_h: int = 56,
                 keyboard_nav: bool = True):
        super().__init__(rect)
        self.items = items
        self.fonts = fonts
        self.theme = theme
        self.font_size = font_size or theme.pad_m + 12
        self.row_h = row_h
        # When False, W/S/Up/Down/Enter no longer move or activate the
        # selection. Mouse clicks still work. Used by TitleScene so that
        # typing into the name field doesn't also scroll the menu.
        self.keyboard_nav = keyboard_nav
        self.selected = 0
        for i, it in enumerate(items):
            if it.enabled:
                self.selected = i
                break

    def update(self, dt: float, inp) -> None:
        if not self.items:
            return
        # Pointer motion transfers focus to the hovered row. Merely polling a
        # stationary cursor must not overwrite a selection the player just
        # moved with the keyboard/gamepad.
        pointer_active = bool(
            inp.mouse_clicked
            or getattr(inp, "touch_active", False)
            or any(
                event.type == pygame.MOUSEMOTION
                for event in getattr(inp, "events", ())
            )
        )
        if pointer_active and self.rect.collidepoint(inp.mouse_pos):
            local_y = inp.mouse_pos[1] - self.rect.y
            idx = local_y // self.row_h
            if 0 <= idx < len(self.items):
                self.selected = idx
            if inp.mouse_clicked and 0 <= idx < len(self.items):
                if self.items[idx].enabled:
                    self.items[idx].on_select()
                return
        if not self.keyboard_nav:
            return
        if inp.pressed(InputAction.NAV_UP):
            self.selected = (self.selected - 1) % len(self.items)
            while not self.items[self.selected].enabled:
                self.selected = (self.selected - 1) % len(self.items)
        elif inp.pressed(InputAction.NAV_DOWN):
            self.selected = (self.selected + 1) % len(self.items)
            while not self.items[self.selected].enabled:
                self.selected = (self.selected + 1) % len(self.items)
        elif inp.confirm and self.items[self.selected].enabled:
            self.items[self.selected].on_select()

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        y = self.rect.y
        for i, it in enumerate(self.items):
            row = pygame.Rect(self.rect.x, y, self.rect.width, self.row_h - 6)
            if i == self.selected:
                hl = pygame.Surface(row.size, pygame.SRCALPHA)
                pygame.draw.rect(hl, (*self.theme.accent[:3], 60),
                                 hl.get_rect(),
                                 border_radius=self.theme.radius_m)
                pygame.draw.rect(hl, self.theme.border_strong,
                                 hl.get_rect(), width=1,
                                 border_radius=self.theme.radius_m)
                surface.blit(hl, row.topleft)
            color = (self.theme.text if it.enabled else self.theme.text_dim)
            text = self.fonts.render(it.label, self.font_size, color,
                                     bold=(i == self.selected))
            tx = row.x + 24
            ty = row.y + (row.height - text.get_height()) // 2
            surface.blit(text, (tx, ty))
            y += self.row_h
