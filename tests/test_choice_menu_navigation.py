"""Keyboard/gamepad focus navigation for the dialogue choice menu."""
from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.input import InputState
from world_gal_game.ui.theme import default_theme
from world_gal_game.ui.widgets.choice_menu import ChoiceMenu


def _make_menu(
    rows: list[tuple] | None = None,
) -> tuple[ChoiceMenu, list[str]]:
    pygame.init()
    chosen: list[str] = []
    menu = ChoiceMenu(
        pygame.Rect(0, 0, 1280, 720),
        fonts=FontRegistry(candidates=()),
        theme=default_theme(),
        on_choose=chosen.append,
    )
    menu.set_choices(rows or [
        ("locked-first", "Locked first", False, "locked"),
        ("first", "First", True),
        ("locked-middle", "Locked middle", False),
        ("last", "Last", True),
    ])
    return menu, chosen


def _input(*, nav: int = 0, confirm: bool = False) -> InputState:
    return InputState(
        nav=nav,
        confirm=confirm,
        mouse_pos=(-1000, -1000),
    )


def test_first_navigation_press_establishes_focus() -> None:
    down_menu, _ = _make_menu()
    assert down_menu.focused_index is None
    down_menu.update(0.0, _input(nav=1))
    assert down_menu.focused_index == 1

    up_menu, _ = _make_menu()
    up_menu.update(0.0, _input(nav=-1))
    assert up_menu.focused_index == 3


def test_navigation_skips_locked_choices_and_wraps() -> None:
    menu, _ = _make_menu()

    menu.update(0.0, _input(nav=1))
    assert menu.focused_index == 1
    menu.update(0.0, _input(nav=1))
    assert menu.focused_index == 3
    menu.update(0.0, _input(nav=1))
    assert menu.focused_index == 1
    menu.update(0.0, _input(nav=-1))
    assert menu.focused_index == 3


def test_confirm_selects_only_the_focused_enabled_choice() -> None:
    menu, chosen = _make_menu()

    # Confirm does not guess while no choice is focused.
    menu.update(0.0, _input(confirm=True))
    assert chosen == []

    menu.update(0.0, _input(nav=1))
    menu.update(0.0, _input(confirm=True))
    assert chosen == ["first"]


def test_all_locked_choices_never_gain_focus_or_activate() -> None:
    menu, chosen = _make_menu([
        ("a", "A", False, "locked"),
        ("b", "B", False),
    ])

    menu.update(0.0, _input(nav=1))
    menu.update(0.0, _input(nav=-1))
    menu.update(0.0, _input(confirm=True))

    assert menu.focused_index is None
    assert chosen == []


def test_setting_single_new_choice_replaces_stale_focus() -> None:
    menu, _ = _make_menu()
    menu.update(0.0, _input(nav=1))
    assert menu.focused_index == 1

    menu.set_choices([("replacement", "Replacement", True)])
    # A sole actionable replacement is immediately confirmable; it must not
    # inherit the old row index, but it does receive the single-option focus.
    assert menu.focused_index == 0


def test_mouse_click_behavior_is_unchanged() -> None:
    menu, chosen = _make_menu([("mouse", "Mouse", True)])
    button = menu.buttons[0]

    menu.update(0.016, InputState(
        mouse_pos=button.rect.center,
        mouse_clicked=True,
    ))

    assert chosen == ["mouse"]
    # The only enabled row began focused by contract; pointer activation still
    # calls the same callback without changing that single-option focus.
    assert menu.focused_index == 0


def test_mouse_motion_synchronises_keyboard_focus_and_confirm_target() -> None:
    menu, chosen = _make_menu([
        ("first", "First", True),
        ("second", "Second", True),
        ("locked", "Locked", False),
    ])
    menu.update(0.0, _input(nav=1))
    assert menu.focused_index == 0

    second = menu.buttons[1]
    motion = pygame.event.Event(
        pygame.MOUSEMOTION,
        {"pos": second.rect.center, "rel": (8, 4), "buttons": (0, 0, 0)},
    )
    menu.update(0.016, InputState(
        events=[motion],
        mouse_pos=second.rect.center,
    ))
    assert menu.focused_index == 1

    menu.update(0.0, _input(confirm=True))
    assert chosen == ["second"]

    locked = menu.buttons[2]
    locked_motion = pygame.event.Event(
        pygame.MOUSEMOTION,
        {"pos": locked.rect.center, "rel": (0, 12), "buttons": (0, 0, 0)},
    )
    menu.update(0.016, InputState(
        events=[locked_motion],
        mouse_pos=locked.rect.center,
    ))
    assert menu.focused_index is None


def test_gamepad_hat_and_confirm_flow_through_input_state() -> None:
    menu, chosen = _make_menu()
    down = pygame.event.Event(
        pygame.JOYHATMOTION,
        {"joy": 0, "instance_id": 0, "hat": 0, "value": (0, -1)},
    )
    confirm = pygame.event.Event(
        pygame.JOYBUTTONDOWN,
        {"joy": 0, "instance_id": 0, "button": 0},
    )

    menu.update(0.0, InputState.collect([down]))
    assert menu.focused_index == 1
    menu.update(0.0, InputState.collect([confirm]))

    assert chosen == ["first"]
