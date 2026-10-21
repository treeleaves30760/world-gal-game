"""Locale-safe font source switching."""
from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

from world_gal_game.ui.fonts import FontRegistry


def test_reconfigure_clears_cached_fonts_and_keeps_registry_identity() -> None:
    pygame.font.init()
    registry = FontRegistry((), scale=1.0)
    first = registry.get(18)
    assert registry._cache
    assert registry.revision == 0

    same_registry = registry
    registry.reconfigure(("DejaVu Sans",), bundled=None)

    assert registry is same_registry
    assert registry.candidates == ("DejaVu Sans",)
    assert registry._cache == {}
    assert registry.revision == 1
    second = registry.get(18)
    assert second is not first


def test_reconfigure_is_noop_for_identical_sources() -> None:
    pygame.font.init()
    registry = FontRegistry(("DejaVu Sans",), scale=1.0)
    cached = registry.get(16, bold=True)
    revision = registry.revision

    registry.reconfigure(("DejaVu Sans",), bundled=None)

    assert registry.revision == revision
    assert registry.get(16, bold=True) is cached
