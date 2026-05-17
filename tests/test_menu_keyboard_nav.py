"""MenuList.keyboard_nav: regressions for the title-screen W-key bug.

When the player is typing into the name field on the title screen, W/S
keys should type into the field — they should NOT also move the menu
cursor (or worse, fire a menu item via Enter). The fix toggles
MenuList.keyboard_nav based on TextInput.focused.
"""
import os
import pygame

# Initialize pygame headlessly so we can construct Surfaces.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
pygame.init()
pygame.display.set_mode((10, 10))

from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.theme import default_theme
from world_gal_game.ui.widgets.menu_list import MenuList, MenuItem
from world_gal_game.ui.input import InputState


def _make_menu() -> MenuList:
    fonts = FontRegistry(("Arial",))
    theme = default_theme()
    items = [
        MenuItem("First", lambda: None),
        MenuItem("Second", lambda: None),
        MenuItem("Third", lambda: None),
    ]
    return MenuList(pygame.Rect(0, 0, 200, 200), items,
                    fonts=fonts, theme=theme)


def _key_event(key: int) -> pygame.event.Event:
    return pygame.event.Event(pygame.KEYDOWN, {"key": key,
                                                "mod": 0,
                                                "scancode": 0,
                                                "unicode": ""})


def _input(events: list[pygame.event.Event]) -> InputState:
    """Build an InputState with the mouse parked far away so hover
    doesn't pick a row under the cursor."""
    return InputState(events=events, mouse_pos=(-1000, -1000))


def test_w_advances_selection_when_keyboard_nav_enabled():
    menu = _make_menu()
    menu.selected = 1
    menu.update(0.0, _input([_key_event(pygame.K_w)]))
    # W is "up", so selection goes 1 -> 0.
    assert menu.selected == 0


def test_w_does_nothing_when_keyboard_nav_disabled():
    menu = _make_menu()
    menu.keyboard_nav = False
    menu.selected = 1
    menu.update(0.0, _input([_key_event(pygame.K_w)]))
    assert menu.selected == 1


def test_enter_does_not_fire_when_keyboard_nav_disabled():
    """If the player presses Enter while typing in the name field, the
    menu shouldn't start a new game."""
    fired = []
    fonts = FontRegistry(("Arial",))
    theme = default_theme()
    menu = MenuList(pygame.Rect(0, 0, 200, 200),
                    [MenuItem("Start", lambda: fired.append(True))],
                    fonts=fonts, theme=theme,
                    keyboard_nav=False)
    menu.update(0.0, _input([_key_event(pygame.K_RETURN)]))
    assert fired == []


def test_mouse_click_still_works_when_keyboard_nav_disabled():
    fired = []
    fonts = FontRegistry(("Arial",))
    theme = default_theme()
    menu = MenuList(pygame.Rect(0, 0, 200, 200),
                    [MenuItem("Start", lambda: fired.append(True))],
                    fonts=fonts, theme=theme,
                    keyboard_nav=False, row_h=200)
    inp = InputState(events=[], mouse_pos=(50, 50), mouse_clicked=True)
    menu.update(0.0, inp)
    assert fired == [True]
