"""Tests for the responsive letterbox view math + window->logical mapping.

These cover the pure helpers behind the canvas-scaling layer added in B0, so
they need no display or full App.
"""
from __future__ import annotations

import pytest

from world_gal_game.app import _letterbox_view, _unproject


LOGICAL = (1280, 720)


def test_same_size_is_identity():
    scale, offset, size = _letterbox_view(LOGICAL, (1280, 720))
    assert scale == 1.0
    assert offset == (0, 0)
    assert size == (1280, 720)


def test_uniform_upscale_keeps_aspect():
    # 1920x1080 is exactly 1.5x of 1280x720 -> fills, no letterbox.
    scale, offset, size = _letterbox_view(LOGICAL, (1920, 1080))
    assert scale == pytest.approx(1.5)
    assert size == (1920, 1080)
    assert offset == (0, 0)


def test_letterbox_top_bottom_when_window_too_tall():
    # 1280x800: width matches (scale 1.0), extra 80px height -> 40px bars.
    scale, offset, size = _letterbox_view(LOGICAL, (1280, 800))
    assert scale == pytest.approx(1.0)
    assert size == (1280, 720)
    assert offset == (0, 40)


def test_letterbox_left_right_when_window_too_wide():
    # 1600x720: height matches (scale 1.0), extra 320px width -> 160px bars.
    scale, offset, size = _letterbox_view(LOGICAL, (1600, 720))
    assert scale == pytest.approx(1.0)
    assert size == (1280, 720)
    assert offset == (160, 0)


def test_unproject_inverts_projection_corners():
    scale, offset, _size = _letterbox_view(LOGICAL, (1280, 800))
    # Top-left of the active (non-bar) region maps to logical origin.
    assert _unproject((offset[0], offset[1]), scale, offset) == (0, 0)
    # Bottom-right of the active region maps to logical bottom-right.
    assert _unproject((1280, 760), scale, offset) == (1280, 720)


def test_unproject_under_upscale():
    scale, offset, _ = _letterbox_view(LOGICAL, (1920, 1080))
    # A click at window center maps to logical center.
    assert _unproject((960, 540), scale, offset) == (640, 360)
