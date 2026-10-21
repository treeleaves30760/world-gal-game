"""Tests for DebugOverlay widget — verifies no crash on construction and draw."""
from __future__ import annotations

import os

import pytest

# Use the dummy SDL driver so no display is required.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from world_gal_game.ui.theme import Theme
from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.widgets.debug_overlay import DebugOverlay
from world_gal_game.core.game_state import GameState


@pytest.fixture(scope="module", autouse=True)
def pygame_init():
    pygame.init()
    yield
    pygame.quit()


@pytest.fixture()
def overlay():
    fonts = FontRegistry(candidates=(), bundled=None)
    theme = Theme()
    rect = pygame.Rect(10, 10, 350, 600)
    return DebugOverlay(rect, fonts=fonts, theme=theme)


def test_construction_does_not_crash(overlay: DebugOverlay) -> None:
    assert overlay is not None


def test_starts_hidden(overlay: DebugOverlay) -> None:
    assert overlay.visible is False


def test_toggle_shows(overlay: DebugOverlay) -> None:
    overlay.visible = False
    overlay.toggle()
    assert overlay.visible is True


def test_toggle_hides(overlay: DebugOverlay) -> None:
    overlay.visible = True
    overlay.toggle()
    assert overlay.visible is False


def test_set_state_and_draw_no_crash(overlay: DebugOverlay) -> None:
    state = GameState()
    state.affection.register("npc_a")
    state.affection.adjust("npc_a", 20)
    state.events.set_flag("met_npc", True)

    overlay.set_state(state)
    overlay.visible = True

    surface = pygame.Surface((1280, 720))
    overlay.draw(surface)   # must not raise


def test_set_state_surfaces_asset_cache_telemetry(overlay: DebugOverlay) -> None:
    overlay.set_state(
        GameState(),
        cache_stats={
            "image_bytes": 16 * 1024 * 1024,
            "image_budget_bytes": 256 * 1024 * 1024,
            "sound_bytes": 4 * 1024 * 1024,
            "sound_budget_bytes": 64 * 1024 * 1024,
            "retained_audio_bytes": 12 * 1024 * 1024,
        },
    )

    assert "Images: 16.0/256 MiB" in overlay._lines
    assert "Audio: 4.0/64 MiB (live 12.0)" in overlay._lines


def test_draw_while_hidden_no_crash(overlay: DebugOverlay) -> None:
    overlay.visible = False
    surface = pygame.Surface((1280, 720))
    overlay.draw(surface)   # early-out, must not raise


def test_update_tracks_fps(overlay: DebugOverlay) -> None:
    from world_gal_game.ui.input import InputState
    inp = InputState()
    overlay.update(0.016, inp)
    # FPS should be roughly 62.5; just verify it's a positive number.
    assert overlay._fps > 0
