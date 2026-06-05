"""Per-frame input snapshot.

Each frame the App fills an InputState from pygame events, then passes it
to the active scene. Scenes never poll pygame directly — they consult
InputState. This makes input testable and consistent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import pygame


@dataclass
class InputState:
    events: list[pygame.event.Event] = field(default_factory=list)
    mouse_pos: tuple[int, int] = (0, 0)
    mouse_pressed: tuple[bool, bool, bool] = (False, False, False)
    mouse_clicked: bool = False
    mouse_rclicked: bool = False
    mouse_wheel: int = 0
    keys_down: set[int] = field(default_factory=set)
    text_input: str = ""
    quit_requested: bool = False
    confirm: bool = False         # Enter / Space / Z
    cancel: bool = False          # Esc / X
    advance_dialogue: bool = False  # click / tap / Enter / Space
    swipe: str | None = None      # "left" / "right" (touch), set by the App
    touch_active: bool = False    # a finger is currently down
    nav: int = 0                  # gamepad/keyboard menu nav: -1 up / +1 down

    def is_key_down(self, key: int) -> bool:
        return key in self.keys_down

    @classmethod
    def collect(cls, events: list[pygame.event.Event], *,
                transform=None, window_size: tuple[int, int] | None = None
                ) -> "InputState":
        """Build a frame's input snapshot.

        ``transform`` (window-pixel -> logical-canvas point) lets the App map
        coordinates back when the window is scaled/letterboxed; pass ``None``
        for an identity mapping (the default, so existing callers are
        unchanged). ``window_size`` is needed to turn normalized touch
        coordinates into pixels before transforming.
        """
        s = cls(events=events)
        s.mouse_pressed = pygame.mouse.get_pressed(num_buttons=3)
        mouse_pos = pygame.mouse.get_pos()
        if transform is not None:
            mouse_pos = transform(mouse_pos)
        # Gamepad → keyboard: synthesize the equivalent KEYDOWN so every
        # keyboard-navigable widget (MenuList, dialogue advance) responds to a
        # controller with no per-widget change. Appended to events after the
        # loop so the synthesized keys don't re-trigger this mapping.
        synth: list[pygame.event.Event] = []

        def _key(k: int) -> None:
            synth.append(pygame.event.Event(pygame.KEYDOWN, key=k, mod=0))

        for e in events:
            if e.type == pygame.QUIT:
                s.quit_requested = True
            elif e.type == pygame.MOUSEBUTTONDOWN:
                if e.button == 1:
                    s.mouse_clicked = True
                    s.advance_dialogue = True
                elif e.button == 3:
                    s.mouse_rclicked = True
                elif e.button == 4:
                    s.mouse_wheel += 1
                elif e.button == 5:
                    s.mouse_wheel -= 1
            elif e.type == pygame.MOUSEWHEEL:
                s.mouse_wheel += e.y
            elif e.type == pygame.KEYDOWN:
                s.keys_down.add(e.key)
                if e.key in (pygame.K_RETURN, pygame.K_KP_ENTER,
                             pygame.K_SPACE, pygame.K_z):
                    s.confirm = True
                    s.advance_dialogue = True
                if e.key in (pygame.K_ESCAPE, pygame.K_x):
                    s.cancel = True
            elif e.type == pygame.TEXTINPUT:
                s.text_input += e.text
            elif e.type == pygame.FINGERDOWN:
                # Treat a touch as a click/advance at the touched point.
                s.touch_active = True
                if window_size is not None:
                    wx = e.x * window_size[0]
                    wy = e.y * window_size[1]
                    mouse_pos = (transform((wx, wy)) if transform is not None
                                 else (int(wx), int(wy)))
                s.mouse_clicked = True
                s.advance_dialogue = True
            elif e.type == pygame.FINGERUP:
                s.touch_active = False
            elif e.type == pygame.JOYBUTTONDOWN:
                if e.button == 0:            # A / cross — confirm / advance
                    s.confirm = True
                    s.advance_dialogue = True
                    _key(pygame.K_RETURN)
                elif e.button == 1:          # B / circle — cancel / back
                    s.cancel = True
                    _key(pygame.K_ESCAPE)
                elif e.button in (6, 7):     # select / start — open menu (Esc)
                    s.cancel = True
                    _key(pygame.K_ESCAPE)
            elif e.type == pygame.JOYHATMOTION:
                hx, hy = e.value
                if hy == 1:
                    _key(pygame.K_UP)
                elif hy == -1:
                    _key(pygame.K_DOWN)
                if hx == -1:
                    _key(pygame.K_LEFT)
                elif hx == 1:
                    _key(pygame.K_RIGHT)
            elif e.type == pygame.JOYDEVICEADDED:
                # Hot-plugged pad: open it so its events flow from now on.
                try:
                    pygame.joystick.Joystick(e.device_index).init()
                except (pygame.error, AttributeError):
                    pass
        if synth:
            s.events = events + synth
        for ev in s.events:
            if ev.type == pygame.KEYDOWN:
                if ev.key in (pygame.K_UP, pygame.K_w):
                    s.nav = -1
                elif ev.key in (pygame.K_DOWN, pygame.K_s):
                    s.nav = 1
        s.mouse_pos = mouse_pos
        return s
