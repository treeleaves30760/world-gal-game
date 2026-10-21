"""Deterministic cross-frame gamepad and touch input contracts."""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from world_gal_game.ui.input import InputRouter
from world_gal_game.ui.widgets.scrollable import ScrollArea
from world_gal_game.ui.widgets.slider import Slider


@pytest.fixture(autouse=True)
def _pygame_init():
    pygame.init()
    yield
    pygame.quit()


def _axis(axis: int, value: float, *, joystick: int = 3):
    return pygame.event.Event(
        pygame.JOYAXISMOTION,
        axis=axis,
        value=value,
        instance_id=joystick,
    )


def _finger(kind: int, x: float, y: float, *, finger: int = 0):
    return pygame.event.Event(
        kind,
        x=x,
        y=y,
        dx=0.0,
        dy=0.0,
        finger_id=finger,
        touch_id=7,
        pressure=1.0,
    )


def _keys(state) -> list[int]:
    return [
        event.key
        for event in state.events
        if event.type == pygame.KEYDOWN
    ]


def test_left_stick_deadzone_and_vertical_navigation_semantics() -> None:
    router = InputRouter()

    below = router.collect([_axis(1, 0.54)])
    assert _keys(below) == []
    assert below.nav == 0

    down = router.collect([_axis(1, 0.56)])
    assert _keys(down) == [pygame.K_DOWN]
    assert down.nav == 1

    up = router.collect([_axis(1, -0.8)])
    assert _keys(up) == [pygame.K_UP]
    assert up.nav == -1


def test_left_stick_hysteresis_requires_release_before_reactivation() -> None:
    router = InputRouter()
    assert _keys(router.collect([_axis(0, 0.8)])) == [pygame.K_RIGHT]

    # Stick drift between the activation and release thresholds remains held,
    # but does not produce another edge before the repeat delay.
    held = router.collect([_axis(0, 0.45)], dt=0.1)
    assert _keys(held) == []

    released = router.collect([_axis(0, 0.34)])
    assert _keys(released) == []
    assert _keys(router.collect([_axis(0, 0.45)])) == []
    assert _keys(router.collect([_axis(0, 0.7)])) == [pygame.K_RIGHT]


def test_left_stick_repeat_has_delay_cadence_and_one_action_per_frame() -> None:
    router = InputRouter(
        analog_repeat_delay=0.30,
        analog_repeat_interval=0.10,
    )
    assert _keys(router.collect([_axis(0, -0.9)])) == [pygame.K_LEFT]
    assert _keys(router.collect([], dt=0.29)) == []
    assert _keys(router.collect([], dt=0.02)) == [pygame.K_LEFT]
    assert _keys(router.collect([], dt=0.09)) == []
    assert _keys(router.collect([], dt=0.02)) == [pygame.K_LEFT]

    # A long stalled frame cannot skip through an entire menu.
    assert _keys(router.collect([], dt=2.0)) == [pygame.K_LEFT]


def test_left_stick_uses_dominant_axis_and_ignores_trigger_axes() -> None:
    horizontal = InputRouter().collect([
        _axis(0, 0.9),
        _axis(1, 0.7),
    ])
    assert _keys(horizontal) == [pygame.K_RIGHT]

    # Exact diagonals resolve to one vertical action, never two menu steps.
    tied = InputRouter().collect([
        _axis(0, -0.8),
        _axis(1, -0.8),
    ])
    assert _keys(tied) == [pygame.K_UP]

    trigger = InputRouter().collect([_axis(2, 1.0)])
    assert _keys(trigger) == []


def test_controller_removal_clears_held_repeat_state() -> None:
    router = InputRouter(
        analog_repeat_delay=0.1,
        analog_repeat_interval=0.05,
    )
    router.collect([_axis(0, 0.9, joystick=12)])
    removed = pygame.event.Event(
        pygame.JOYDEVICEREMOVED,
        instance_id=12,
    )
    assert _keys(router.collect([removed], dt=1.0)) == []
    assert _keys(router.collect([], dt=1.0)) == []


def test_invalid_analog_tuning_is_rejected() -> None:
    with pytest.raises(ValueError, match="deadzones"):
        InputRouter(
            analog_activation_deadzone=0.3,
            analog_release_deadzone=0.4,
        )
    with pytest.raises(ValueError, match="interval"):
        InputRouter(analog_repeat_interval=0.0)


def test_touch_tap_activates_on_release_and_stays_active_across_frames() -> None:
    router = InputRouter(touch_mode=True)
    down = router.collect(
        [_finger(pygame.FINGERDOWN, 0.5, 0.5)],
        window_size=(1280, 720),
    )
    assert down.touch_started is True
    assert down.touch_active is True
    assert down.mouse_clicked is False
    assert down.advance_dialogue is False
    assert down.mouse_pressed[0] is True
    assert down.mouse_pos == (640, 360)

    held = router.collect([], dt=0.1, window_size=(1280, 720))
    assert held.touch_active is True
    assert held.mouse_clicked is False
    assert held.mouse_pos == (640, 360)

    up = router.collect(
        [_finger(pygame.FINGERUP, 0.505, 0.505)],
        dt=0.1,
        window_size=(1280, 720),
    )
    assert up.touch_released is True
    assert up.touch_active is False
    assert up.mouse_clicked is True
    assert up.advance_dialogue is True
    assert up.mouse_pos == (646, 363)


def test_touch_uses_release_position_after_letterbox_transform() -> None:
    router = InputRouter(touch_mode=True)

    def unproject(point):
        return ((point[0] - 160) / 1.5, (point[1] - 90) / 1.5)

    router.collect(
        [_finger(pygame.FINGERDOWN, 0.5, 0.5)],
        transform=unproject,
        window_size=(1920, 1080),
    )
    state = router.collect(
        [_finger(pygame.FINGERUP, 0.5, 0.5)],
        transform=unproject,
        window_size=(1920, 1080),
    )
    assert state.mouse_pos == (533, 300)
    assert state.mouse_clicked is True


def test_drag_out_and_back_is_not_misclassified_as_tap() -> None:
    router = InputRouter(touch_mode=True)
    size = (1280, 720)
    router.collect(
        [_finger(pygame.FINGERDOWN, 0.5, 0.5)],
        window_size=size,
    )
    motion = router.collect(
        [_finger(pygame.FINGERMOTION, 0.6, 0.5)],
        window_size=size,
    )
    assert motion.touch_delta == (128, 0)
    release = router.collect(
        [_finger(pygame.FINGERUP, 0.5, 0.5)],
        window_size=size,
    )
    assert release.mouse_clicked is False
    assert release.advance_dialogue is False
    assert release.swipe is None


def test_horizontal_swipe_never_also_advances_dialogue() -> None:
    router = InputRouter(touch_mode=True)
    size = (1280, 720)
    router.collect(
        [_finger(pygame.FINGERDOWN, 0.8, 0.5)],
        window_size=size,
    )
    release = router.collect(
        [_finger(pygame.FINGERUP, 0.4, 0.52)],
        window_size=size,
    )
    assert release.swipe == "left"
    assert release.mouse_clicked is False
    assert release.advance_dialogue is False


def test_vertical_drag_is_neither_tap_nor_horizontal_swipe() -> None:
    router = InputRouter(touch_mode=True)
    size = (1280, 720)
    router.collect(
        [_finger(pygame.FINGERDOWN, 0.5, 0.2)],
        window_size=size,
    )
    release = router.collect(
        [_finger(pygame.FINGERUP, 0.52, 0.8)],
        window_size=size,
    )
    assert release.swipe is None
    assert release.mouse_clicked is False
    assert release.advance_dialogue is False


def test_second_contact_cancels_primary_tap() -> None:
    router = InputRouter(touch_mode=True)
    size = (1280, 720)
    router.collect(
        [_finger(pygame.FINGERDOWN, 0.5, 0.5, finger=1)],
        window_size=size,
    )
    router.collect(
        [_finger(pygame.FINGERDOWN, 0.7, 0.5, finger=2)],
        window_size=size,
    )
    primary_up = router.collect(
        [_finger(pygame.FINGERUP, 0.5, 0.5, finger=1)],
        window_size=size,
    )
    assert primary_up.mouse_clicked is False
    assert primary_up.touch_active is True
    assert primary_up.mouse_pressed[0] is False

    secondary_up = router.collect(
        [_finger(pygame.FINGERUP, 0.7, 0.5, finger=2)],
        window_size=size,
    )
    assert secondary_up.mouse_clicked is False
    assert secondary_up.touch_active is False


def test_touch_mode_suppresses_emulated_and_delayed_ghost_mouse_clicks() -> None:
    router = InputRouter(touch_mode=True)
    size = (1280, 720)
    router.collect(
        [_finger(pygame.FINGERDOWN, 0.4, 0.4)],
        window_size=size,
    )
    synthetic_mouse = pygame.event.Event(
        pygame.MOUSEBUTTONDOWN,
        button=1,
        pos=(512, 288),
        touch=True,
    )
    release = router.collect(
        [
            _finger(pygame.FINGERUP, 0.4, 0.4),
            synthetic_mouse,
        ],
        dt=0.016,
        window_size=size,
    )
    # The tap is delivered once; its SDL-emulated mouse partner is discarded.
    assert release.mouse_clicked is True

    delayed_ghost = pygame.event.Event(
        pygame.MOUSEBUTTONDOWN,
        button=1,
        pos=(512, 288),
    )
    suppressed = router.collect([delayed_ghost], dt=0.10)
    assert suppressed.mouse_clicked is False

    router.collect([], dt=0.16)
    real_mouse = router.collect([delayed_ghost])
    assert real_mouse.mouse_clicked is True


def test_marked_touch_mouse_event_is_suppressed_even_outside_touch_mode() -> None:
    event = pygame.event.Event(
        pygame.MOUSEBUTTONDOWN,
        button=1,
        pos=(10, 10),
        touch=True,
    )
    state = InputRouter(touch_mode=False).collect([event])
    assert state.mouse_clicked is False
    assert state.advance_dialogue is False


def test_app_step_retains_router_state_between_real_event_batches(
    monkeypatch,
) -> None:
    from world_gal_game.app import GalGameApp

    captured = []

    class _Manager:
        current = None

        def update(self, dt, state):
            captured.append(state)

        def draw(self, surface):
            pass

    class _Toasts:
        def update(self, dt, state):
            pass

        def draw(self, surface):
            pass

    app = GalGameApp.__new__(GalGameApp)
    app.display = None
    app.screen = pygame.Surface((1280, 720))
    app.config = SimpleNamespace(touch_mode=True)
    app._input_router = InputRouter(
        touch_mode=True,
        analog_repeat_delay=0.09,
        analog_repeat_interval=0.10,
    )
    app._window_to_logical = lambda point: (int(point[0]), int(point[1]))
    app.manager = _Manager()
    app.toast_stack = _Toasts()
    app._steam = None
    app._dev_mode = False
    app._debug_overlay = None
    app._inspect_pending = False
    app._poll_achievement_toasts = lambda: None
    app._maybe_take_pending_screenshot = lambda: None
    app._present = lambda: None
    app._quit_app = lambda: None

    batches = iter([
        [_axis(1, 0.9)],
        [],
    ])
    monkeypatch.setattr(pygame.event, "get", lambda: next(batches))

    app._step(0.016)
    app._step(0.31)

    assert captured[0].nav == 1
    assert _keys(captured[0]) == [pygame.K_DOWN]
    assert captured[1].nav == 1
    assert _keys(captured[1]) == [pygame.K_DOWN]


@pytest.mark.parametrize(
    "key",
    [pygame.K_F6, pygame.K_F9, pygame.K_F12],
)
def test_app_step_does_not_fire_global_action_during_binding_capture(
    key,
    monkeypatch,
) -> None:
    from world_gal_game.app import GalGameApp

    calls: list[str] = []

    class _Manager:
        current = SimpleNamespace(capturing=True)

        def update(self, dt, state):
            calls.append("scene-update")

        def draw(self, surface):
            pass

    class _Toasts:
        def update(self, dt, state):
            pass

        def draw(self, surface):
            pass

    app = GalGameApp.__new__(GalGameApp)
    app.display = None
    app.screen = pygame.Surface((1280, 720))
    app.config = SimpleNamespace(touch_mode=False, input_bindings={})
    app._input_router = InputRouter()
    app._window_to_logical = lambda point: point
    app.manager = _Manager()
    app.toast_stack = _Toasts()
    app._steam = None
    app._dev_mode = False
    app._debug_overlay = None
    app._inspect_pending = False
    app._poll_achievement_toasts = lambda: None
    app._maybe_take_pending_screenshot = lambda: None
    app._present = lambda: None
    app._quit_app = lambda: None
    app.take_screenshot = lambda: calls.append("screenshot")
    app._quicksave = lambda: calls.append("quicksave")
    app._quickload = lambda: calls.append("quickload")

    monkeypatch.setattr(
        pygame.event,
        "get",
        lambda: [
            pygame.event.Event(
                pygame.KEYDOWN,
                key=key,
                mod=0,
                unicode="",
            ),
        ],
    )

    app._step(0.016)

    assert calls == ["scene-update"]


def test_right_click_routes_contextual_back_and_menu_actions() -> None:
    from world_gal_game.ui.input_bindings import InputAction

    state = InputRouter().collect([
        pygame.event.Event(
            pygame.MOUSEBUTTONDOWN,
            button=3,
            pos=(20, 30),
        ),
    ])

    assert state.mouse_rclicked is True
    assert state.pressed(InputAction.BACK) is True
    assert state.pressed(InputAction.OPEN_MENU) is True
    assert state.cancel is True


def test_scroll_area_captures_vertical_touch_drag_until_release() -> None:
    router = InputRouter(touch_mode=True)
    size = (200, 400)
    area = ScrollArea(
        pygame.Rect(0, 0, 200, 200),
        fonts=None,
        theme=None,
    )
    area.content_height = 800

    down = router.collect(
        [_finger(pygame.FINGERDOWN, 0.5, 0.25)],
        window_size=size,
    )
    area.update(0.0, down)
    motion = router.collect(
        [_finger(pygame.FINGERMOTION, 0.5, 0.10)],
        window_size=size,
    )
    area.update(0.0, motion)
    assert motion.touch_delta == (0, -60)
    assert area.scroll_y == 60

    # Pointer capture applies the final delta even after leaving the viewport.
    release = router.collect(
        [_finger(pygame.FINGERUP, 0.5, 0.80)],
        window_size=size,
    )
    area.update(0.0, release)
    assert area.scroll_y == 0
    assert area._touch_dragging is False


def test_slider_drag_starts_on_touch_down_and_tracks_primary_finger() -> None:
    values: list[float] = []
    slider = Slider(
        pygame.Rect(0, 0, 200, 44),
        0.5,
        fonts=None,
        theme=None,
        on_change=values.append,
    )
    router = InputRouter(touch_mode=True)
    size = (200, 100)

    down = router.collect(
        [_finger(pygame.FINGERDOWN, 0.25, 0.22)],
        window_size=size,
    )
    slider.update(0.0, down)
    assert slider.value == 0.25
    assert slider._dragging is True

    motion = router.collect(
        [_finger(pygame.FINGERMOTION, 0.75, 0.22)],
        window_size=size,
    )
    slider.update(0.0, motion)
    assert slider.value == 0.75

    release = router.collect(
        [_finger(pygame.FINGERUP, 0.75, 0.22)],
        window_size=size,
    )
    slider.update(0.0, release)
    assert release.mouse_clicked is False
    assert slider._dragging is False
    assert values == [0.25, 0.75]


def test_slider_touch_tap_releases_pointer_capture_immediately() -> None:
    slider = Slider(
        pygame.Rect(0, 0, 200, 44),
        0.5,
        fonts=None,
        theme=None,
    )
    router = InputRouter(touch_mode=True)
    size = (200, 100)

    slider.update(
        0.0,
        router.collect(
            [_finger(pygame.FINGERDOWN, 0.25, 0.22)],
            window_size=size,
        ),
    )
    release = router.collect(
        [_finger(pygame.FINGERUP, 0.25, 0.22)],
        window_size=size,
    )
    slider.update(0.0, release)

    assert release.mouse_clicked is True
    assert slider.value == 0.25
    assert slider._dragging is False
