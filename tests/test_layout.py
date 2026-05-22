"""fit_rect: aspect-preserving placement of a portrait inside slot bounds."""
from __future__ import annotations

import pygame
import pytest

from world_gal_game.ui.layout import fit_rect


def _ar(rect: pygame.Rect) -> float:
    return rect.width / rect.height


def test_tall_source_into_wide_bounds_is_height_limited():
    # 3:4 portrait (0.75) into the real 720p dialogue slot box (480x398).
    bounds = pygame.Rect(100, 30, 480, 398)
    r = fit_rect((1152, 1536), bounds)
    # Height fills the box; width shrinks to keep aspect (no stretch).
    assert r.height == 398
    assert r.width < 480
    assert _ar(r) == pytest.approx(0.75, abs=0.01)
    # Fits inside and stays horizontally centred on the bounds.
    assert bounds.contains(r)
    assert r.centerx == bounds.centerx


def test_wide_source_into_tall_bounds_is_width_limited():
    bounds = pygame.Rect(0, 0, 200, 400)
    r = fit_rect((800, 400), bounds)  # 2:1 source
    assert r.width == 200
    assert r.height < 400
    assert _ar(r) == pytest.approx(2.0, abs=0.01)
    assert bounds.contains(r)


def test_bottom_anchor_is_default():
    bounds = pygame.Rect(10, 20, 480, 398)
    r = fit_rect((1152, 1536), bounds)
    assert r.bottom == bounds.bottom
    assert r.top >= bounds.top


def test_explicit_anchors():
    bounds = pygame.Rect(0, 0, 400, 400)
    src = (400, 200)  # height-limited? no: src wider -> width-limited (h=200)
    top = fit_rect(src, bounds, anchor="top")
    center = fit_rect(src, bounds, anchor="center")
    bottom = fit_rect(src, bounds, anchor="bottom")
    assert top.top == bounds.top
    assert center.centery == bounds.centery
    assert bottom.bottom == bounds.bottom
    # Same size regardless of anchor.
    assert top.size == center.size == bottom.size


def test_matching_aspect_fills_bounds():
    bounds = pygame.Rect(5, 5, 300, 400)
    r = fit_rect((600, 800), bounds)  # also 0.75
    assert r.size == (300, 400)
    assert r.topleft == (5, 5)


def test_degenerate_sizes_return_bounds():
    bounds = pygame.Rect(1, 2, 100, 200)
    assert fit_rect((0, 100), bounds) == bounds
    assert fit_rect((100, 0), bounds) == bounds
    assert fit_rect((100, 100), pygame.Rect(0, 0, 0, 0)) == pygame.Rect(0, 0, 0, 0)


def test_never_exceeds_bounds_for_assorted_aspects():
    bounds = pygame.Rect(0, 0, 480, 398)
    for src in [(1152, 1536), (1086, 1448), (1024, 1024), (1920, 1080), (300, 1200)]:
        r = fit_rect(src, bounds)
        assert r.width <= bounds.width
        assert r.height <= bounds.height
        assert bounds.contains(r)
