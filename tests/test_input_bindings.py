"""Semantic keyboard binding and cross-frame routing contracts."""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from world_gal_game.ui.input import InputRouter, InputState
from world_gal_game.ui.input_bindings import (
    ACTION_SPECS,
    BindingProfile,
    InputAction,
    rebind_action,
)


EXPECTED_ACTIONS = {
    "CONFIRM_ADVANCE": "confirm_advance",
    "BACK": "back",
    "OPEN_MENU": "open_menu",
    "NAV_UP": "nav_up",
    "NAV_DOWN": "nav_down",
    "NAV_LEFT": "nav_left",
    "NAV_RIGHT": "nav_right",
    "FOCUS_NEXT": "focus_next",
    "FOCUS_PREVIOUS": "focus_previous",
    "PAGE_UP": "page_up",
    "PAGE_DOWN": "page_down",
    "TOGGLE_AUTO": "toggle_auto",
    "HOLD_SKIP": "hold_skip",
    "OPEN_HISTORY": "open_history",
    "ROLLBACK": "rollback",
    "HIDE_UI": "hide_ui",
    "OPEN_MAP": "open_map",
    "OPEN_AFFECTION": "open_affection",
    "OPEN_EVENT_LOG": "open_event_log",
    "OPEN_SAVE": "open_save",
    "OPEN_ACHIEVEMENTS": "open_achievements",
    "OPEN_INVENTORY": "open_inventory",
    "OPEN_CLUES": "open_clues",
    "QUICKSAVE": "quicksave",
    "QUICKLOAD": "quickload",
    "SCREENSHOT": "screenshot",
    "TOGGLE_FULLSCREEN": "toggle_fullscreen",
    "MENU_SECTION_PREVIOUS": "menu_section_previous",
    "MENU_SECTION_NEXT": "menu_section_next",
}


@pytest.fixture(autouse=True)
def _pygame_init():
    pygame.init()
    yield
    pygame.quit()


def _key_event(kind: int, key: int, *, mod: int = 0):
    return pygame.event.Event(kind, key=key, mod=mod)


def test_action_ids_are_stable_and_every_action_has_player_metadata() -> None:
    actual = {
        name: value for name, value in vars(InputAction).items() if name.isupper()
    }
    assert actual == EXPECTED_ACTIONS
    assert set(ACTION_SPECS) == set(EXPECTED_ACTIONS.values())
    for action, spec in ACTION_SPECS.items():
        assert spec.id == action
        assert spec.label
        assert spec.group
        assert isinstance(spec.defaults, tuple)
        assert spec.contexts

    assert "exploration" in ACTION_SPECS[InputAction.CONFIRM_ADVANCE].contexts
    assert ACTION_SPECS[InputAction.BACK].required is True
    assert ACTION_SPECS[InputAction.CONFIRM_ADVANCE].required is True
    settings_navigation = (
        InputAction.NAV_UP,
        InputAction.NAV_DOWN,
        InputAction.NAV_LEFT,
        InputAction.NAV_RIGHT,
        InputAction.FOCUS_NEXT,
        InputAction.FOCUS_PREVIOUS,
        InputAction.PAGE_UP,
        InputAction.PAGE_DOWN,
        InputAction.MENU_SECTION_PREVIOUS,
        InputAction.MENU_SECTION_NEXT,
    )
    assert all(
        "settings" in ACTION_SPECS[action].contexts for action in settings_navigation
    )


def test_profile_normalizes_untrusted_values_and_preserves_required_actions() -> None:
    profile = BindingProfile(
        {
            "unknown_action": ["q"],
            InputAction.CONFIRM_ADVANCE: [],
            InputAction.BACK: ["f1", "not a real key"],
            InputAction.OPEN_HISTORY: [],
            InputAction.OPEN_MAP: "k",
            InputAction.OPEN_EVENT_LOG: [None, "K", "k"],
        }
    )

    assert profile.keys_for(InputAction.CONFIRM_ADVANCE)
    assert profile.keys_for(InputAction.BACK)
    assert profile.names_for(InputAction.OPEN_HISTORY) == ()
    assert profile.names_for(InputAction.OPEN_MAP) == ("m",)
    assert profile.names_for(InputAction.OPEN_EVENT_LOG) == ("k",)
    assert profile.names_for("unknown_action") == ()
    assert profile.overrides == {
        InputAction.OPEN_HISTORY: [],
        InputAction.OPEN_EVENT_LOG: ["k"],
    }


def test_context_reuse_allows_a_and_s_without_hiding_effective_actions() -> None:
    profile = BindingProfile()

    assert set(profile.actions_for_key("a")) >= {
        InputAction.NAV_LEFT,
        InputAction.TOGGLE_AUTO,
        InputAction.OPEN_AFFECTION,
    }
    assert profile.conflicts_for(InputAction.NAV_LEFT, "a") == ()
    assert profile.conflicts_for(InputAction.OPEN_AFFECTION, "a") == ()
    assert InputAction.OPEN_SAVE in profile.actions_for_key("s")
    assert InputAction.NAV_DOWN in profile.actions_for_key("s")
    assert profile.display(InputAction.NAV_UP) == "↑ / W"
    assert profile.display(InputAction.NAV_UP, concise=True) == "↑"
    assert profile.primary_display(InputAction.NAV_UP) == "↑"
    assert profile.display_primary(InputAction.NAV_UP) == "↑"
    assert profile.display(InputAction.OPEN_HISTORY) == "B"


def test_rebind_is_pure_swaps_conflicts_and_rejects_reserved_keys() -> None:
    original: dict[str, list[str]] = {}
    result = rebind_action(
        original,
        InputAction.NAV_UP,
        "a",
        resolution="swap",
    )

    assert result.success is True
    assert result.reason is None
    assert result.swapped_with == (InputAction.NAV_LEFT,)
    assert original == {}
    profile = BindingProfile(result.overrides)
    assert profile.names_for(InputAction.NAV_UP)[0] == "a"
    assert "up" in profile.names_for(InputAction.NAV_LEFT)

    reserved = rebind_action(
        original,
        InputAction.OPEN_MAP,
        pygame.K_F5,
    )
    assert reserved.success is False
    assert reserved.reason == "reserved_key"
    assert reserved.overrides == {}

    invalid = rebind_action(original, InputAction.BACK, "")
    assert invalid.success is False
    assert invalid.reason == "invalid_key"

    rejected = rebind_action(
        original,
        InputAction.NAV_UP,
        "a",
        resolution="reject",
    )
    assert rejected.success is False
    assert rejected.reason == "binding_conflict"
    assert rejected.swapped_with == (InputAction.NAV_LEFT,)

    replaced = rebind_action(
        original,
        InputAction.OPEN_MAP,
        "l",
        resolution="replace",
    )
    assert replaced.success is True
    replaced_profile = BindingProfile(replaced.overrides)
    assert replaced_profile.names_for(InputAction.OPEN_EVENT_LOG) == ()


@pytest.mark.parametrize("reserved", [pygame.K_F1, pygame.K_F5, pygame.K_F11])
def test_every_engine_reserved_key_is_forbidden(reserved: int) -> None:
    result = rebind_action({}, InputAction.OPEN_MAP, reserved)
    assert result.success is False
    assert result.reason == "reserved_key"


def test_multi_conflict_swap_rescues_required_actions_atomically() -> None:
    conflicting = {
        InputAction.BACK: ["k"],
        InputAction.NAV_UP: ["k"],
    }
    swapped = rebind_action(
        conflicting,
        InputAction.SCREENSHOT,
        "k",
        resolution="swap",
    )
    assert swapped.success is True
    assert set(swapped.swapped_with) == {
        InputAction.BACK,
        InputAction.NAV_UP,
    }
    profile = BindingProfile(swapped.overrides)
    assert profile.names_for(InputAction.BACK)
    assert profile.names_for(InputAction.NAV_UP)
    assert "f12" in profile.names_for(InputAction.BACK)
    assert "f12" in profile.names_for(InputAction.NAV_UP)

    cannot_rescue = rebind_action(
        {
            **conflicting,
            InputAction.SCREENSHOT: [],
        },
        InputAction.SCREENSHOT,
        "k",
        resolution="swap",
    )
    assert cannot_rescue.success is False
    assert cannot_rescue.reason == "required_action_would_be_unbound"
    assert cannot_rescue.overrides == {
        **conflicting,
        InputAction.SCREENSHOT: [],
    }

    # Resetting is intentionally just the canonical default profile.
    assert BindingProfile({}).overrides == {}


def test_direct_input_state_derives_default_actions_and_raw_aliases() -> None:
    pressed = InputState(keys_down={pygame.K_m})
    assert pressed.pressed(InputAction.OPEN_MAP)
    assert pressed.held(InputAction.OPEN_MAP)
    assert pressed.physical_keys_down == {pygame.K_m}
    assert pressed.physical_keys_held == {pygame.K_m}

    released = InputState(
        events=[
            _key_event(pygame.KEYUP, pygame.K_m),
        ]
    )
    assert released.released(InputAction.OPEN_MAP)
    assert released.physical_keys_released == {pygame.K_m}
    assert released.physical_keys_held == set()

    down_then_up = InputState(
        events=[
            _key_event(pygame.KEYDOWN, pygame.K_m),
            _key_event(pygame.KEYUP, pygame.K_m),
        ]
    )
    assert down_then_up.pressed(InputAction.OPEN_MAP)
    assert down_then_up.released(InputAction.OPEN_MAP)
    assert not down_then_up.held(InputAction.OPEN_MAP)


def test_router_tracks_keyboard_edges_hold_release_and_live_overrides() -> None:
    router = InputRouter(
        binding_overrides={
            InputAction.CONFIRM_ADVANCE: ["c"],
        }
    )

    old_default = router.collect(
        [
            _key_event(pygame.KEYDOWN, pygame.K_RETURN),
        ]
    )
    assert not old_default.pressed(InputAction.CONFIRM_ADVANCE)
    router.collect([_key_event(pygame.KEYUP, pygame.K_RETURN)])

    down = router.collect([_key_event(pygame.KEYDOWN, pygame.K_c)])
    assert down.pressed(InputAction.CONFIRM_ADVANCE)
    assert down.held(InputAction.CONFIRM_ADVANCE)
    assert down.confirm is True
    assert down.last_device == "keyboard"

    held = router.collect([])
    assert not held.pressed(InputAction.CONFIRM_ADVANCE)
    assert held.held(InputAction.CONFIRM_ADVANCE)
    assert held.physical_keys_held == {pygame.K_c}

    up = router.collect([_key_event(pygame.KEYUP, pygame.K_c)])
    assert up.released(InputAction.CONFIRM_ADVANCE)
    assert not up.held(InputAction.CONFIRM_ADVANCE)

    rebound = router.collect(
        [_key_event(pygame.KEYDOWN, pygame.K_v)],
        binding_overrides={InputAction.CONFIRM_ADVANCE: ["v"]},
    )
    assert rebound.pressed(InputAction.CONFIRM_ADVANCE)


def test_back_rebinding_does_not_leak_open_menu_into_legacy_cancel() -> None:
    router = InputRouter(
        binding_overrides={
            InputAction.BACK: ["c"],
        }
    )

    menu = router.collect(
        [
            _key_event(pygame.KEYDOWN, pygame.K_ESCAPE),
        ]
    )
    assert menu.pressed(InputAction.OPEN_MENU)
    assert not menu.pressed(InputAction.BACK)
    assert menu.cancel is False

    router.collect([_key_event(pygame.KEYUP, pygame.K_ESCAPE)])
    back = router.collect([_key_event(pygame.KEYDOWN, pygame.K_c)])
    assert back.pressed(InputAction.BACK)
    assert back.cancel is True


def test_unchanged_live_overrides_keep_the_active_profile_instance() -> None:
    overrides = {InputAction.CONFIRM_ADVANCE: ["c"]}
    router = InputRouter(binding_overrides=overrides)
    profile = router.binding_profile

    state = router.collect([], binding_overrides=dict(overrides))

    assert state.actions_pressed == set()
    assert router.binding_profile is profile


def test_keyboard_remap_never_changes_gamepad_semantics_or_synthetic_events() -> None:
    router = InputRouter(
        binding_overrides={
            InputAction.CONFIRM_ADVANCE: ["c"],
            InputAction.NAV_LEFT: ["j"],
            InputAction.NAV_RIGHT: ["l"],
        }
    )

    confirm = router.collect(
        [
            pygame.event.Event(
                pygame.JOYBUTTONDOWN,
                button=0,
                instance_id=2,
            ),
        ]
    )
    assert confirm.pressed(InputAction.CONFIRM_ADVANCE)
    assert confirm.confirm is True
    assert confirm.last_device == "gamepad"
    assert any(
        event.type == pygame.KEYDOWN and event.key == pygame.K_RETURN
        for event in confirm.events
    )

    hat = router.collect(
        [
            pygame.event.Event(
                pygame.JOYHATMOTION,
                value=(-1, 0),
                instance_id=2,
            ),
        ]
    )
    assert hat.pressed(InputAction.NAV_LEFT)
    assert hat.held(InputAction.NAV_LEFT)
    assert any(
        event.type == pygame.KEYDOWN and event.key == pygame.K_LEFT
        for event in hat.events
    )
    hat_held = router.collect([])
    assert hat_held.held(InputAction.NAV_LEFT)
    assert not hat_held.pressed(InputAction.NAV_LEFT)
    hat_up = router.collect(
        [
            pygame.event.Event(
                pygame.JOYHATMOTION,
                value=(0, 0),
                instance_id=2,
            ),
        ]
    )
    assert hat_up.released(InputAction.NAV_LEFT)
    assert not hat_up.held(InputAction.NAV_LEFT)

    analog = router.collect(
        [
            pygame.event.Event(
                pygame.JOYAXISMOTION,
                axis=0,
                value=0.9,
                instance_id=2,
            ),
        ]
    )
    assert analog.pressed(InputAction.NAV_RIGHT)
    assert analog.held(InputAction.NAV_RIGHT)
    analog_held = router.collect([])
    assert analog_held.held(InputAction.NAV_RIGHT)
    analog_up = router.collect(
        [
            pygame.event.Event(
                pygame.JOYAXISMOTION,
                axis=0,
                value=0.0,
                instance_id=2,
            ),
        ]
    )
    assert analog_up.released(InputAction.NAV_RIGHT)
    assert not analog_up.held(InputAction.NAV_RIGHT)


@pytest.mark.parametrize(
    ("button", "action", "synthetic_key"),
    [
        (4, InputAction.PAGE_UP, pygame.K_PAGEUP),
        (5, InputAction.PAGE_DOWN, pygame.K_PAGEDOWN),
    ],
)
def test_gamepad_shoulders_emit_settings_page_semantics(
    button: int,
    action: str,
    synthetic_key: int,
) -> None:
    state = InputRouter().collect(
        [
            pygame.event.Event(
                pygame.JOYBUTTONDOWN,
                button=button,
                instance_id=7,
            ),
        ]
    )

    assert state.pressed(action)
    assert state.held(action)
    assert state.last_device == "gamepad"
    assert any(
        event.type == pygame.KEYDOWN and event.key == synthetic_key
        for event in state.events
    )


def test_releasing_one_of_two_bound_keys_keeps_action_held() -> None:
    router = InputRouter()
    router.collect(
        [
            _key_event(pygame.KEYDOWN, pygame.K_RETURN),
            _key_event(pygame.KEYDOWN, pygame.K_SPACE),
        ]
    )

    partial = router.collect(
        [
            _key_event(pygame.KEYUP, pygame.K_RETURN),
        ]
    )
    assert partial.held(InputAction.CONFIRM_ADVANCE)
    assert not partial.released(InputAction.CONFIRM_ADVANCE)

    final = router.collect(
        [
            _key_event(pygame.KEYUP, pygame.K_SPACE),
        ]
    )
    assert final.released(InputAction.CONFIRM_ADVANCE)
    assert not final.held(InputAction.CONFIRM_ADVANCE)


def test_focus_loss_releases_raw_keyboard_and_gamepad_actions() -> None:
    router = InputRouter(
        binding_overrides={
            InputAction.HOLD_SKIP: ["c"],
        }
    )
    router.collect(
        [
            _key_event(pygame.KEYDOWN, pygame.K_c),
            pygame.event.Event(
                pygame.JOYBUTTONDOWN,
                button=0,
                instance_id=4,
            ),
        ]
    )

    lost = router.collect(
        [
            pygame.event.Event(pygame.WINDOWFOCUSLOST),
        ]
    )
    assert lost.released(InputAction.HOLD_SKIP)
    assert lost.released(InputAction.CONFIRM_ADVANCE)
    assert lost.physical_keys_released == {pygame.K_c}
    assert lost.physical_keys_held == set()
    assert not lost.held(InputAction.HOLD_SKIP)
    assert not lost.held(InputAction.CONFIRM_ADVANCE)
