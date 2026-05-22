"""Tests for touch input + coordinate transform added in B0.

Touch events are synthesized directly, so no real display is required.
"""
from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from world_gal_game.ui.input import InputState
from world_gal_game.app import GalGameApp


@pytest.fixture(autouse=True)
def _pygame_init():
    pygame.init()
    yield
    pygame.quit()


def _finger(kind: int, x: float, y: float) -> pygame.event.Event:
    return pygame.event.Event(kind, {
        "x": x, "y": y, "dx": 0.0, "dy": 0.0,
        "finger_id": 0, "touch_id": 0, "pressure": 1.0,
    })


# ---------- coordinate transform --------------------------------------------


def test_collect_without_transform_is_identity():
    # No transform -> mouse_pos is whatever pygame reports, unchanged.
    inp = InputState.collect([])
    assert isinstance(inp.mouse_pos, tuple)
    assert inp.swipe is None
    assert inp.touch_active is False


def test_collect_applies_transform_to_mouse_pos():
    captured = {}

    def transform(pos):
        captured["in"] = pos
        return (111, 222)

    inp = InputState.collect([], transform=transform)
    assert inp.mouse_pos == (111, 222)


# ---------- touch tap -> click/advance --------------------------------------


def test_fingerdown_maps_to_click_and_advance():
    # Window 1280x720, identity transform: normalized (0.5, 0.5) -> (640, 360).
    down = _finger(pygame.FINGERDOWN, 0.5, 0.5)
    inp = InputState.collect([down], transform=lambda p: (int(p[0]), int(p[1])),
                             window_size=(1280, 720))
    assert inp.mouse_clicked is True
    assert inp.advance_dialogue is True
    assert inp.touch_active is True
    assert inp.mouse_pos == (640, 360)


def test_fingerup_clears_touch_active():
    up = _finger(pygame.FINGERUP, 0.5, 0.5)
    inp = InputState.collect([up], window_size=(1280, 720))
    assert inp.touch_active is False


# ---------- swipe classification (cross-frame, app-level) -------------------


class _FakeApp:
    """Minimal stand-in carrying just the gesture state the method uses."""
    def __init__(self):
        self._touch_start = None


_update = GalGameApp._update_touch_gesture


def test_swipe_left_detected():
    app = _FakeApp()
    _update(app, [_finger(pygame.FINGERDOWN, 0.8, 0.5)], InputState())
    inp = InputState()
    _update(app, [_finger(pygame.FINGERUP, 0.4, 0.5)], inp)
    assert inp.swipe == "left"


def test_swipe_right_detected():
    app = _FakeApp()
    _update(app, [_finger(pygame.FINGERDOWN, 0.2, 0.5)], InputState())
    inp = InputState()
    _update(app, [_finger(pygame.FINGERUP, 0.7, 0.5)], inp)
    assert inp.swipe == "right"


def test_small_or_vertical_drag_is_not_a_swipe():
    app = _FakeApp()
    _update(app, [_finger(pygame.FINGERDOWN, 0.5, 0.2)], InputState())
    inp = InputState()
    # Mostly vertical drag -> no horizontal swipe.
    _update(app, [_finger(pygame.FINGERUP, 0.52, 0.8)], inp)
    assert inp.swipe is None
