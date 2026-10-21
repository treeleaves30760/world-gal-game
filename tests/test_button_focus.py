"""Keyboard/gamepad focus is a first-class Button visual state."""
from __future__ import annotations

import os

import pygame

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.input import InputState
from world_gal_game.ui.theme import default_theme
from world_gal_game.ui.widgets.button import Button, set_click_sound_hook


def _button() -> Button:
    pygame.font.init()
    return Button(
        pygame.Rect(10, 10, 180, 52),
        "設定",
        fonts=FontRegistry(("Arial",)),
        theme=default_theme(),
    )


def test_keyboard_focus_animates_without_pointer_hover() -> None:
    button = _button()
    button.focused = True

    button.update(
        1 / 60,
        InputState(mouse_pos=(-1000, -1000)),
    )

    assert button._pointer_over is False
    assert button._hover_t > 0


def test_keyboard_focus_does_not_turn_unrelated_click_into_activation() -> None:
    fired: list[bool] = []
    button = _button()
    button.on_click = lambda: fired.append(True)
    button.focused = True

    button.update(
        1 / 60,
        InputState(mouse_pos=(-1000, -1000), mouse_clicked=True),
    )

    assert fired == []


def test_keyboard_focus_ring_is_visible_even_at_zero_delta_time() -> None:
    button = _button()
    button.focused = True
    surface = pygame.Surface((220, 90), pygame.SRCALPHA)

    button.update(0.0, InputState(mouse_pos=(-1000, -1000)))
    button.draw(surface)

    assert surface.get_at((button.rect.centerx, button.rect.top + 1))[:3] == (
        button.theme.accent_warm[:3]
    )


def test_activate_gives_keyboard_and_pointer_the_same_click_feedback() -> None:
    feedback: list[str] = []
    fired: list[str] = []
    button = _button()
    button.on_click = lambda: fired.append("callback")
    set_click_sound_hook(lambda: feedback.append("sound"))
    try:
        assert button.activate() is True
    finally:
        set_click_sound_hook(None)

    assert feedback == ["sound"]
    assert fired == ["callback"]


def test_text_overflow_is_auditable_and_never_paints_outside_button() -> None:
    pygame.font.init()
    button = Button(
        pygame.Rect(20, 20, 100, 52),
        "A deliberately overlong unwrapped button label",
        fonts=FontRegistry(("Arial",)),
        theme=default_theme(),
        font_size=16,
    )
    surface = pygame.Surface((320, 100), pygame.SRCALPHA)

    assert button.text_overflow is True
    button.draw(surface)

    outside = surface.subsurface(
        pygame.Rect(button.rect.right, 0, 320 - button.rect.right, 100),
    )
    assert pygame.mask.from_surface(outside).count() == 0
