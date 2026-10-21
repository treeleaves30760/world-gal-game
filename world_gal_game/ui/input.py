"""Per-frame input snapshot and persistent device routing.

Each frame the App fills an InputState from pygame events, then passes it
to the active scene. Scenes never poll pygame directly — they consult
InputState. This makes input testable and consistent.

``InputRouter`` owns the small amount of state that cannot be reconstructed
from one event batch: a held analogue stick, repeat timing, and touch contacts
whose down/up events span frames.  ``InputState.collect`` remains as the
backwards-compatible, stateless entry point for tests and embedding clients.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import math
from typing import Mapping, Sequence

import pygame

from .input_bindings import BindingProfile, InputAction


ANALOG_ACTIVATION_DEADZONE = 0.55
"""Left-stick magnitude required to begin menu navigation."""

ANALOG_RELEASE_DEADZONE = 0.35
"""Lower release threshold used as hysteresis against stick jitter."""

ANALOG_REPEAT_DELAY_S = 0.36
"""Delay before a held left stick starts repeating."""

ANALOG_REPEAT_INTERVAL_S = 0.09
"""Interval between navigation repeats after the initial delay."""

TOUCH_TAP_SLOP_PX = 18
"""Maximum physical travel still classified as a tap."""

TOUCH_TAP_SLOP_NORMALIZED = 0.025
"""Fallback tap slop when the host cannot provide a window size."""

TOUCH_SWIPE_THRESHOLD = 0.12
"""Minimum normalized horizontal travel classified as a swipe."""


_GAMEPAD_BUTTON_ACTIONS: dict[int, tuple[str, ...]] = {
    0: (InputAction.CONFIRM_ADVANCE,),
    1: (InputAction.BACK,),
    3: (InputAction.FOCUS_NEXT,),
    4: (InputAction.PAGE_UP,),
    5: (InputAction.PAGE_DOWN,),
    # Preserve the historical select/start -> Escape behaviour while exposing
    # the more precise semantic menu action to migrated consumers.
    6: (InputAction.OPEN_MENU, InputAction.BACK),
    7: (InputAction.OPEN_MENU, InputAction.BACK),
}

_DIRECTION_ACTIONS: dict[tuple[int, int], str] = {
    (-1, 0): InputAction.NAV_LEFT,
    (1, 0): InputAction.NAV_RIGHT,
    (0, -1): InputAction.NAV_UP,
    (0, 1): InputAction.NAV_DOWN,
}


@dataclass
class InputState:
    events: list[pygame.event.Event] = field(default_factory=list)
    mouse_pos: tuple[int, int] = (0, 0)
    mouse_pressed: tuple[bool, bool, bool] = (False, False, False)
    mouse_clicked: bool = False
    mouse_rclicked: bool = False
    mouse_wheel: int = 0
    # Historical name: these are raw KEYDOWN edges for this frame, not held
    # state. Keep it for tests/plugins and expose the precise companions below.
    keys_down: set[int] = field(default_factory=set)
    keys_released: set[int] = field(default_factory=set)
    keys_held: set[int] = field(default_factory=set)
    text_input: str = ""
    quit_requested: bool = False
    confirm: bool = False         # Enter / Space / Z
    cancel: bool = False          # Esc / X
    advance_dialogue: bool = False  # click / tap / Enter / Space
    swipe: str | None = None      # "left" / "right" release gesture
    touch_active: bool = False    # a finger is currently down
    touch_started: bool = False   # primary contact began this frame
    touch_released: bool = False  # primary contact ended this frame
    touch_delta: tuple[int, int] = (0, 0)  # primary logical movement this frame
    touch_mode: bool = False      # host requested finger-first semantics
    nav: int = 0                  # gamepad/keyboard menu nav: -1 up / +1 down
    actions_pressed: set[str] = field(default_factory=set)
    actions_released: set[str] = field(default_factory=set)
    actions_held: set[str] = field(default_factory=set)
    last_device: str = "keyboard"

    def __post_init__(self) -> None:
        """Make lightweight direct construction honour the default profile.

        Many focused scene tests and embedding clients instantiate
        ``InputState(events=[...])`` or only provide ``keys_down``. Runtime
        routing is richer and stateful, but these compatibility paths must still
        expose the same semantic actions.
        """

        explicit_keys_down = set(self.keys_down)
        shift_modified: set[int] = set()
        for event in self.events:
            if event.type == pygame.KEYDOWN:
                self.keys_down.add(int(event.key))
                self.keys_held.add(int(event.key))
                if int(getattr(event, "mod", 0)) & pygame.KMOD_SHIFT:
                    shift_modified.add(int(event.key))
                self.last_device = "keyboard"
            elif event.type == pygame.KEYUP:
                self.keys_released.add(int(event.key))
                self.keys_held.discard(int(event.key))
                self.last_device = "keyboard"
            elif event.type in (
                pygame.MOUSEMOTION,
                pygame.MOUSEBUTTONDOWN,
                pygame.MOUSEBUTTONUP,
                pygame.MOUSEWHEEL,
            ):
                self.last_device = "mouse"
            elif event.type in (
                pygame.FINGERDOWN,
                pygame.FINGERMOTION,
                pygame.FINGERUP,
            ):
                self.last_device = "touch"
            elif event.type == pygame.JOYBUTTONDOWN:
                self.last_device = "gamepad"
                for action in _GAMEPAD_BUTTON_ACTIONS.get(
                    int(event.button), (),
                ):
                    self.actions_pressed.add(action)
                    self.actions_held.add(action)
            elif event.type == pygame.JOYBUTTONUP:
                self.last_device = "gamepad"
                for action in _GAMEPAD_BUTTON_ACTIONS.get(
                    int(event.button), (),
                ):
                    self.actions_released.add(action)
                    self.actions_held.discard(action)
            elif event.type == pygame.JOYHATMOTION:
                self.last_device = "gamepad"
                hx, hy = event.value
                direction = (
                    (1 if hx > 0 else -1, 0)
                    if hx
                    else (
                        (0, -1 if hy > 0 else 1)
                        if hy
                        else None
                    )
                )
                action = (
                    _DIRECTION_ACTIONS.get(direction)
                    if direction is not None
                    else None
                )
                if action is not None:
                    self.actions_pressed.add(action)
                    self.actions_held.add(action)

        # ``keys_down=...`` is a supported shorthand for a one-frame held key,
        # while an ordered KEYDOWN+KEYUP event pair must end released.
        self.keys_held.update(explicit_keys_down)
        profile = BindingProfile()
        self._apply_keyboard_profile(profile)
        self._apply_shift_tab(shift_modified)

        # Direct semantic construction remains supported in both directions.
        if self.confirm:
            self.actions_pressed.add(InputAction.CONFIRM_ADVANCE)
        if self.cancel:
            self.actions_pressed.add(InputAction.BACK)
        if self.nav < 0:
            self.actions_pressed.add(InputAction.NAV_UP)
        elif self.nav > 0:
            self.actions_pressed.add(InputAction.NAV_DOWN)
        self._sync_legacy_aliases()

    def is_key_down(self, key: int) -> bool:
        return key in self.keys_down

    def pressed(self, action: str) -> bool:
        return action in self.actions_pressed

    def released(self, action: str) -> bool:
        return action in self.actions_released

    def held(self, action: str) -> bool:
        return action in self.actions_held

    @property
    def physical_keys_down(self) -> set[int]:
        """Raw keyboard press edges (compatibility alias for ``keys_down``)."""

        return self.keys_down

    @property
    def physical_keys_released(self) -> set[int]:
        """Raw keyboard release edges for this frame."""

        return self.keys_released

    @property
    def physical_keys_held(self) -> set[int]:
        """Raw keyboard keys that remain held at the end of this frame."""

        return self.keys_held

    def _apply_keyboard_profile(self, profile: BindingProfile) -> None:
        for key in self.keys_down:
            self.actions_pressed.update(profile.actions_for_key(key))
        for key in self.keys_released:
            self.actions_released.update(profile.actions_for_key(key))
        for key in self.keys_held:
            self.actions_held.update(profile.actions_for_key(key))

    def _apply_shift_tab(self, shift_modified: set[int] | None = None) -> None:
        """Preserve Shift+Tab reverse focus without persisting a chord."""

        tab = pygame.K_TAB
        shift_held = bool(
            self.keys_held & {pygame.K_LSHIFT, pygame.K_RSHIFT}
        )
        if (
            self.pressed(InputAction.FOCUS_NEXT)
            and (shift_held or tab in (shift_modified or set()))
            and tab in self.keys_down
        ):
            self.actions_pressed.discard(InputAction.FOCUS_NEXT)
            self.actions_pressed.add(InputAction.FOCUS_PREVIOUS)
            self.actions_held.discard(InputAction.FOCUS_NEXT)
            self.actions_held.add(InputAction.FOCUS_PREVIOUS)

    def _sync_legacy_aliases(self) -> None:
        self.confirm = (
            self.confirm or self.pressed(InputAction.CONFIRM_ADVANCE)
        )
        self.cancel = (
            self.cancel
            or self.pressed(InputAction.BACK)
        )
        self.advance_dialogue = (
            self.advance_dialogue or self.confirm or self.mouse_clicked
        )
        if self.pressed(InputAction.NAV_UP):
            self.nav = -1
        elif self.pressed(InputAction.NAV_DOWN):
            self.nav = 1

    @classmethod
    def collect(
        cls,
        events: list[pygame.event.Event],
        *,
        transform=None,
        window_size: tuple[int, int] | None = None,
        binding_overrides: (
            BindingProfile | Mapping[str, Sequence[str]] | None
        ) = None,
    ) -> "InputState":
        """Build a backwards-compatible, one-frame input snapshot.

        ``transform`` (window-pixel -> logical-canvas point) lets the App map
        coordinates back when the window is scaled/letterboxed; pass ``None``
        for an identity mapping (the default, so existing callers are
        unchanged). ``window_size`` is needed to turn normalized touch
        coordinates into pixels before transforming.

        Runtime code should keep one :class:`InputRouter` and call its
        :meth:`~InputRouter.collect` method instead.  This stateless helper
        intentionally preserves the historical FINGERDOWN-as-click behaviour
        for lightweight callers that submit only one event batch.
        """
        return InputRouter(
            tap_on_release=False,
            binding_overrides=binding_overrides,
        ).collect(
            events,
            transform=transform,
            window_size=window_size,
        )


@dataclass
class _TouchContact:
    start_norm: tuple[float, float]
    last_norm: tuple[float, float]
    last_logical: tuple[int, int] | None
    max_norm_distance: float = 0.0
    max_pixel_distance: float = 0.0
    cancelled: bool = False


class InputRouter:
    """Translate pygame events into stable, device-neutral actions.

    One instance must be retained for the life of the application. Analogue
    stick repeats and touch taps both cross frame boundaries, so constructing
    this class per frame would deliberately reduce it to the legacy
    :meth:`InputState.collect` semantics.
    """

    def __init__(
        self,
        *,
        touch_mode: bool = False,
        tap_on_release: bool = True,
        binding_overrides: (
            BindingProfile | Mapping[str, Sequence[str]] | None
        ) = None,
        analog_activation_deadzone: float = ANALOG_ACTIVATION_DEADZONE,
        analog_release_deadzone: float = ANALOG_RELEASE_DEADZONE,
        analog_repeat_delay: float = ANALOG_REPEAT_DELAY_S,
        analog_repeat_interval: float = ANALOG_REPEAT_INTERVAL_S,
    ) -> None:
        if not 0.0 <= analog_release_deadzone < analog_activation_deadzone <= 1.0:
            raise ValueError(
                "analogue deadzones must satisfy "
                "0 <= release < activation <= 1"
            )
        if analog_repeat_delay < 0.0 or analog_repeat_interval <= 0.0:
            raise ValueError(
                "analogue repeat delay must be non-negative and interval positive"
            )
        self.touch_mode = bool(touch_mode)
        self.tap_on_release = bool(tap_on_release)
        self.analog_activation_deadzone = float(analog_activation_deadzone)
        self.analog_release_deadzone = float(analog_release_deadzone)
        self.analog_repeat_delay = float(analog_repeat_delay)
        self.analog_repeat_interval = float(analog_repeat_interval)

        self._bindings = BindingProfile()
        self._binding_source_snapshot: object = {}
        self.set_binding_overrides(binding_overrides)
        self._held_keys: set[int] = set()
        self._held_gamepad_buttons: dict[
            tuple[int, int], tuple[str, ...]
        ] = {}
        self._held_hats: dict[int, set[str]] = {}
        self._last_device = "keyboard"

        self._axes: dict[int, dict[int, float]] = {}
        self._active_joystick: int | None = None
        self._analog_direction: tuple[int, int] = (0, 0)
        self._analog_repeat_remaining = 0.0

        self._touches: dict[tuple[int, int], _TouchContact] = {}
        self._primary_touch: tuple[int, int] | None = None
        self._touch_mouse_suppression_remaining = 0.0

    @property
    def binding_profile(self) -> BindingProfile:
        """The currently active, validated keyboard profile."""

        return self._bindings

    def set_binding_overrides(
        self,
        overrides: BindingProfile | Mapping[str, Sequence[str]] | None,
    ) -> bool:
        """Replace changed bindings while retaining all device state.

        Returns ``True`` only when the effective profile changed. Applications
        may safely pass their current config every frame; an unchanged mapping
        keeps the same profile instance.
        """

        if isinstance(overrides, BindingProfile):
            raw: object = overrides.overrides
        elif isinstance(overrides, Mapping):
            try:
                raw = dict(overrides)
            except Exception:
                raw = {}
        else:
            raw = {}
        try:
            if raw == self._binding_source_snapshot:
                return False
        except Exception:
            # Host-provided Mapping values can implement hostile equality.
            # Falling through merely revalidates the tiny profile.
            pass

        candidate = (
            overrides
            if isinstance(overrides, BindingProfile)
            else BindingProfile(overrides)
        )
        try:
            self._binding_source_snapshot = deepcopy(raw)
        except Exception:
            self._binding_source_snapshot = object()
        if candidate.overrides == self._bindings.overrides:
            return False
        self._bindings = candidate
        return True

    def reset(self) -> None:
        """Release all held transient input, e.g. after focus loss."""
        self._held_keys.clear()
        self._held_gamepad_buttons.clear()
        self._held_hats.clear()
        self._axes.clear()
        self._active_joystick = None
        self._analog_direction = (0, 0)
        self._analog_repeat_remaining = 0.0
        self._touches.clear()
        self._primary_touch = None
        self._touch_mouse_suppression_remaining = 0.0

    def _gamepad_actions_held(self) -> set[str]:
        held: set[str] = set()
        for actions in self._held_gamepad_buttons.values():
            held.update(actions)
        for actions in self._held_hats.values():
            held.update(actions)
        analog = _DIRECTION_ACTIONS.get(self._analog_direction)
        if analog is not None:
            held.add(analog)
        return held

    def _release_all_into(self, state: InputState) -> None:
        """Emit semantic/raw releases before clearing transient device state."""

        state.keys_released.update(self._held_keys)
        for key in self._held_keys:
            state.actions_released.update(
                self._bindings.actions_for_key(key)
            )
        state.actions_released.update(self._gamepad_actions_held())
        self.reset()

    def _release_gamepad_into(
        self,
        joystick_id: int,
        state: InputState,
    ) -> None:
        """Release only controls owned by a removed gamepad."""

        before = self._gamepad_actions_held()
        for source in tuple(self._held_gamepad_buttons):
            if source[0] == joystick_id:
                del self._held_gamepad_buttons[source]
        self._held_hats.pop(joystick_id, None)
        self._axes.pop(joystick_id, None)
        if self._active_joystick == joystick_id:
            old_analog = _DIRECTION_ACTIONS.get(self._analog_direction)
            self._analog_direction = (0, 0)
            self._analog_repeat_remaining = 0.0
            self._active_joystick = next(iter(self._axes), None)
            if old_analog is not None:
                state.actions_released.add(old_analog)
        after = self._gamepad_actions_held()
        state.actions_released.update(before - after)

    @staticmethod
    def _joystick_id(event: pygame.event.Event) -> int:
        return int(
            getattr(event, "instance_id", getattr(event, "joy", 0))
        )

    @staticmethod
    def _touch_id(event: pygame.event.Event) -> tuple[int, int]:
        return (
            int(getattr(event, "touch_id", 0)),
            int(getattr(event, "finger_id", 0)),
        )

    @staticmethod
    def _normalized_position(
        event: pygame.event.Event,
    ) -> tuple[float, float]:
        return (
            max(0.0, min(1.0, float(getattr(event, "x", 0.0)))),
            max(0.0, min(1.0, float(getattr(event, "y", 0.0)))),
        )

    @staticmethod
    def _logical_touch_position(
        norm: tuple[float, float],
        *,
        transform,
        window_size: tuple[int, int] | None,
    ) -> tuple[int, int] | None:
        if window_size is None:
            return None
        wx = norm[0] * window_size[0]
        wy = norm[1] * window_size[1]
        if transform is not None:
            point = transform((wx, wy))
            return int(point[0]), int(point[1])
        return int(wx), int(wy)

    @staticmethod
    def _is_touch_generated_mouse(event: pygame.event.Event) -> bool:
        if bool(getattr(event, "touch", False)):
            return True
        touch_mouse_id = getattr(pygame, "TOUCH_MOUSEID", None)
        return (
            touch_mouse_id is not None
            and getattr(event, "which", None) == touch_mouse_id
        )

    def _update_contact(
        self,
        contact: _TouchContact,
        norm: tuple[float, float],
        logical: tuple[int, int] | None,
        window_size: tuple[int, int] | None,
    ) -> None:
        dx = norm[0] - contact.start_norm[0]
        dy = norm[1] - contact.start_norm[1]
        contact.max_norm_distance = max(
            contact.max_norm_distance,
            math.hypot(dx, dy),
        )
        if window_size is not None:
            contact.max_pixel_distance = max(
                contact.max_pixel_distance,
                math.hypot(dx * window_size[0], dy * window_size[1]),
            )
        contact.last_norm = norm
        contact.last_logical = logical

    def _classify_primary_release(
        self,
        state: InputState,
        contact: _TouchContact,
        *,
        window_size: tuple[int, int] | None,
    ) -> None:
        if contact.cancelled:
            return
        dx = contact.last_norm[0] - contact.start_norm[0]
        dy = contact.last_norm[1] - contact.start_norm[1]
        if abs(dx) >= TOUCH_SWIPE_THRESHOLD and abs(dx) > abs(dy):
            state.swipe = "right" if dx > 0 else "left"
            return
        within_slop = (
            contact.max_pixel_distance <= TOUCH_TAP_SLOP_PX
            if window_size is not None
            else contact.max_norm_distance <= TOUCH_TAP_SLOP_NORMALIZED
        )
        if within_slop:
            state.mouse_clicked = True
            state.advance_dialogue = True

    def _resolved_analog_direction(self) -> tuple[int, int]:
        if self._active_joystick is None:
            return (0, 0)
        axes = self._axes.get(self._active_joystick, {})
        x = max(-1.0, min(1.0, axes.get(0, 0.0)))
        y = max(-1.0, min(1.0, axes.get(1, 0.0)))
        previous = self._analog_direction

        # Hysteresis keeps the active axis held until it crosses the lower
        # release threshold, unless the perpendicular axis becomes dominant.
        if previous != (0, 0):
            px, py = previous
            component = x if px else y
            perpendicular = y if px else x
            expected_sign = px or py
            same_sign = (
                component == 0.0
                or (component > 0.0) == (expected_sign > 0)
            )
            if (
                same_sign
                and abs(component) >= self.analog_release_deadzone
                and (
                    abs(perpendicular) < self.analog_activation_deadzone
                    or abs(component) >= abs(perpendicular)
                )
            ):
                return previous

        ax, ay = abs(x), abs(y)
        if max(ax, ay) < self.analog_activation_deadzone:
            return (0, 0)
        # Resolve diagonals to one dominant axis so a single frame never skips
        # two controls. Vertical wins an exact tie, matching VN menu priority.
        if ax > ay:
            return (1 if x > 0.0 else -1, 0)
        return (0, 1 if y > 0.0 else -1)

    @staticmethod
    def _direction_key(direction: tuple[int, int]) -> int | None:
        return {
            (-1, 0): pygame.K_LEFT,
            (1, 0): pygame.K_RIGHT,
            (0, -1): pygame.K_UP,
            (0, 1): pygame.K_DOWN,
        }.get(direction)

    def _route_analog(
        self,
        dt: float,
        synth_key,
        state: InputState,
    ) -> None:
        direction = self._resolved_analog_direction()
        if direction != self._analog_direction:
            previous_action = _DIRECTION_ACTIONS.get(
                self._analog_direction
            )
            if previous_action is not None:
                state.actions_released.add(previous_action)
            self._analog_direction = direction
            self._analog_repeat_remaining = self.analog_repeat_delay
            action = _DIRECTION_ACTIONS.get(direction)
            if action is not None:
                state.actions_pressed.add(action)
                state.actions_held.add(action)
            key = self._direction_key(direction)
            if key is not None:
                synth_key(key)
            return
        if direction == (0, 0):
            self._analog_repeat_remaining = 0.0
            return
        action = _DIRECTION_ACTIONS.get(direction)
        if action is not None:
            state.actions_held.add(action)
        self._analog_repeat_remaining -= max(0.0, float(dt))
        if self._analog_repeat_remaining <= 0.0:
            if action is not None:
                state.actions_pressed.add(action)
            key = self._direction_key(direction)
            if key is not None:
                synth_key(key)
            # Emit at most one repeat per rendered frame. This avoids skipping
            # several menu items after a short stall while preserving cadence.
            self._analog_repeat_remaining = self.analog_repeat_interval

    def collect(
        self,
        events: list[pygame.event.Event],
        *,
        dt: float = 0.0,
        transform=None,
        window_size: tuple[int, int] | None = None,
        touch_mode: bool | None = None,
        binding_overrides: (
            BindingProfile | Mapping[str, Sequence[str]] | None
        ) = None,
    ) -> InputState:
        """Build one frame while retaining analogue/touch state."""
        if touch_mode is not None:
            self.touch_mode = bool(touch_mode)
        if binding_overrides is not None:
            self.set_binding_overrides(binding_overrides)
        # Do not let InputState's direct-construction compatibility path apply
        # default bindings before this router applies the live user profile.
        s = InputState(events=[])
        s.events = events
        s.touch_mode = self.touch_mode
        s.last_device = self._last_device
        shift_modified: set[int] = set()
        try:
            s.mouse_pressed = pygame.mouse.get_pressed(num_buttons=3)
            mouse_pos = pygame.mouse.get_pos()
        except pygame.error:
            # Headless embedding clients can route synthetic keyboard,
            # controller, or touch events without initializing a video device.
            s.mouse_pressed = (False, False, False)
            mouse_pos = (0, 0)
        if transform is not None:
            mouse_pos = transform(mouse_pos)
        # Gamepad → keyboard: synthesize the equivalent KEYDOWN so every
        # keyboard-navigable widget (MenuList, dialogue advance) responds to a
        # controller with no per-widget change. Appended to events after the
        # loop so the synthesized keys don't re-trigger this mapping.
        synth: list[pygame.event.Event] = []
        synthesized_keys: set[int] = set()

        def _key(k: int) -> None:
            if k in synthesized_keys:
                return
            synthesized_keys.add(k)
            synth.append(pygame.event.Event(pygame.KEYDOWN, key=k, mod=0))

        self._touch_mouse_suppression_remaining = max(
            0.0,
            self._touch_mouse_suppression_remaining - max(0.0, float(dt)),
        )
        has_touch_event = any(
            event.type in (
                pygame.FINGERDOWN,
                pygame.FINGERMOTION,
                pygame.FINGERUP,
            )
            for event in events
        )
        if has_touch_event and self.touch_mode:
            # Some SDL backends emit an unmarked emulated mouse click beside
            # the finger event. Keep a short finger-first suppression window;
            # explicitly marked touch-mouse events are ignored in every mode.
            self._touch_mouse_suppression_remaining = 0.25

        for e in events:
            if e.type == pygame.QUIT:
                s.quit_requested = True
            elif e.type == pygame.MOUSEMOTION:
                self._last_device = "mouse"
            elif e.type == pygame.MOUSEBUTTONUP:
                if not self._is_touch_generated_mouse(e):
                    self._last_device = "mouse"
            elif e.type == pygame.MOUSEBUTTONDOWN:
                if self._is_touch_generated_mouse(e) or (
                    self.touch_mode
                    and self._touch_mouse_suppression_remaining > 0.0
                    and e.button in (1, 3)
                ):
                    continue
                self._last_device = "mouse"
                if e.button == 1:
                    s.mouse_clicked = True
                    s.advance_dialogue = True
                elif e.button == 3:
                    s.mouse_rclicked = True
                    # Visual-novel convention: right-click means "back" on
                    # overlays and "open menu" while reading/exploring. Both
                    # actions are emitted so the active scene can resolve its
                    # own context without hard-coding a mouse path.
                    s.actions_pressed.update({
                        InputAction.BACK,
                        InputAction.OPEN_MENU,
                    })
                elif e.button == 4:
                    s.mouse_wheel += 1
                elif e.button == 5:
                    s.mouse_wheel -= 1
            elif e.type == pygame.MOUSEWHEEL:
                self._last_device = "mouse"
                s.mouse_wheel += e.y
            elif e.type == pygame.KEYDOWN:
                key = int(e.key)
                self._last_device = "keyboard"
                s.keys_down.add(key)
                self._held_keys.add(key)
                if int(getattr(e, "mod", 0)) & pygame.KMOD_SHIFT:
                    shift_modified.add(key)
            elif e.type == pygame.KEYUP:
                key = int(e.key)
                self._last_device = "keyboard"
                s.keys_released.add(key)
                self._held_keys.discard(key)
            elif e.type == pygame.TEXTINPUT:
                self._last_device = "keyboard"
                s.text_input += e.text
            elif e.type == pygame.FINGERDOWN:
                self._last_device = "touch"
                touch_id = self._touch_id(e)
                norm = self._normalized_position(e)
                logical = self._logical_touch_position(
                    norm,
                    transform=transform,
                    window_size=window_size,
                )
                self._touches[touch_id] = _TouchContact(
                    start_norm=norm,
                    last_norm=norm,
                    last_logical=logical,
                )
                if self._primary_touch is None and len(self._touches) == 1:
                    self._primary_touch = touch_id
                    s.touch_started = True
                    if logical is not None:
                        mouse_pos = logical
                    if not self.tap_on_release:
                        s.mouse_clicked = True
                        s.advance_dialogue = True
                elif touch_id != self._primary_touch:
                    # Multi-touch is not a click gesture. Cancelling the
                    # primary prevents a second finger (or a palm) from
                    # activating a button when the first finger lifts.
                    primary = self._touches.get(self._primary_touch)
                    if primary is not None:
                        primary.cancelled = True
            elif e.type == pygame.FINGERMOTION:
                self._last_device = "touch"
                touch_id = self._touch_id(e)
                contact = self._touches.get(touch_id)
                if contact is not None:
                    norm = self._normalized_position(e)
                    logical = self._logical_touch_position(
                        norm,
                        transform=transform,
                        window_size=window_size,
                    )
                    previous_logical = contact.last_logical
                    self._update_contact(
                        contact,
                        norm,
                        logical,
                        window_size,
                    )
                    if touch_id == self._primary_touch and logical is not None:
                        mouse_pos = logical
                        if previous_logical is not None:
                            s.touch_delta = (
                                s.touch_delta[0]
                                + logical[0] - previous_logical[0],
                                s.touch_delta[1]
                                + logical[1] - previous_logical[1],
                            )
            elif e.type == pygame.FINGERUP:
                self._last_device = "touch"
                touch_id = self._touch_id(e)
                contact = self._touches.get(touch_id)
                if contact is not None:
                    norm = self._normalized_position(e)
                    logical = self._logical_touch_position(
                        norm,
                        transform=transform,
                        window_size=window_size,
                    )
                    previous_logical = contact.last_logical
                    self._update_contact(
                        contact,
                        norm,
                        logical,
                        window_size,
                    )
                    if touch_id == self._primary_touch:
                        s.touch_released = True
                        if logical is not None:
                            mouse_pos = logical
                            if previous_logical is not None:
                                s.touch_delta = (
                                    s.touch_delta[0]
                                    + logical[0] - previous_logical[0],
                                    s.touch_delta[1]
                                    + logical[1] - previous_logical[1],
                                )
                        if self.tap_on_release:
                            self._classify_primary_release(
                                s,
                                contact,
                                window_size=window_size,
                            )
                        self._primary_touch = None
                    del self._touches[touch_id]
            elif e.type == pygame.JOYBUTTONDOWN:
                self._last_device = "gamepad"
                joystick_id = self._joystick_id(e)
                button = int(e.button)
                actions = _GAMEPAD_BUTTON_ACTIONS.get(button, ())
                source = (joystick_id, button)
                self._held_gamepad_buttons[source] = actions
                s.actions_pressed.update(actions)
                s.actions_held.update(actions)
                key = {
                    0: pygame.K_RETURN,
                    1: pygame.K_ESCAPE,
                    3: pygame.K_TAB,
                    4: pygame.K_PAGEUP,
                    5: pygame.K_PAGEDOWN,
                    6: pygame.K_ESCAPE,
                    7: pygame.K_ESCAPE,
                }.get(button)
                if key is not None:
                    _key(key)
            elif e.type == pygame.JOYBUTTONUP:
                self._last_device = "gamepad"
                source = (self._joystick_id(e), int(e.button))
                released = set(
                    self._held_gamepad_buttons.pop(
                        source,
                        _GAMEPAD_BUTTON_ACTIONS.get(int(e.button), ()),
                    )
                )
                still_held = self._gamepad_actions_held()
                s.actions_released.update(released - still_held)
            elif e.type == pygame.JOYHATMOTION:
                self._last_device = "gamepad"
                joystick_id = self._joystick_id(e)
                old_actions = self._held_hats.get(joystick_id, set())
                new_actions: set[str] = set()
                hx, hy = (int(e.value[0]), int(e.value[1]))
                if hy > 0:
                    new_actions.add(InputAction.NAV_UP)
                    _key(pygame.K_UP)
                elif hy < 0:
                    new_actions.add(InputAction.NAV_DOWN)
                    _key(pygame.K_DOWN)
                if hx < 0:
                    new_actions.add(InputAction.NAV_LEFT)
                    _key(pygame.K_LEFT)
                elif hx > 0:
                    new_actions.add(InputAction.NAV_RIGHT)
                    _key(pygame.K_RIGHT)
                self._held_hats[joystick_id] = new_actions
                s.actions_pressed.update(new_actions - old_actions)
                s.actions_released.update(old_actions - new_actions)
                s.actions_held.update(new_actions)
            elif e.type == pygame.JOYAXISMOTION:
                self._last_device = "gamepad"
                axis = int(e.axis)
                # SDL axes 0/1 are the left stick. Triggers and the right
                # camera stick must never steal menu focus.
                if axis in (0, 1):
                    joystick_id = self._joystick_id(e)
                    self._axes.setdefault(joystick_id, {})[axis] = float(
                        e.value
                    )
                    self._active_joystick = joystick_id
            elif e.type == pygame.JOYDEVICEADDED:
                # Hot-plugged pad: open it so its events flow from now on.
                try:
                    pygame.joystick.Joystick(e.device_index).init()
                except (pygame.error, AttributeError):
                    pass
            elif e.type == pygame.JOYDEVICEREMOVED:
                joystick_id = self._joystick_id(e)
                self._release_gamepad_into(joystick_id, s)
            elif e.type == getattr(pygame, "WINDOWFOCUSLOST", -1):
                self._release_all_into(s)

        self._route_analog(dt, _key, s)
        if synth:
            s.events = events + synth

        s.keys_held = set(self._held_keys)
        s._apply_keyboard_profile(self._bindings)
        s._apply_shift_tab(shift_modified)
        s.actions_held.update(self._gamepad_actions_held())
        # Releasing one of two keys/controllers bound to the same action does
        # not release that semantic action while another source remains held.
        s.actions_released.difference_update(s.actions_held)
        s._sync_legacy_aliases()

        s.touch_active = bool(self._touches)
        if self._primary_touch is not None:
            contact = self._touches.get(self._primary_touch)
            if contact is not None and contact.last_logical is not None:
                mouse_pos = contact.last_logical
        if self.touch_mode and self._primary_touch is not None:
            # Pointer-drag consumers can treat the primary finger as held.
            s.mouse_pressed = (True, s.mouse_pressed[1], s.mouse_pressed[2])
        s.mouse_pos = mouse_pos
        s.last_device = self._last_device
        return s
