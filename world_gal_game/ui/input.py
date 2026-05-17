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
    advance_dialogue: bool = False  # click / Enter / Space

    def is_key_down(self, key: int) -> bool:
        return key in self.keys_down

    @classmethod
    def collect(cls, events: list[pygame.event.Event]) -> "InputState":
        s = cls(events=events)
        s.mouse_pos = pygame.mouse.get_pos()
        s.mouse_pressed = pygame.mouse.get_pressed(num_buttons=3)
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
        return s
