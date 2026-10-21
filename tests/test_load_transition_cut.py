"""Loaded views must never dissolve from the abandoned pre-load screen."""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

import world_gal_game.app as app_module
from world_gal_game.app import GalGameApp
from world_gal_game.scenes.base import Scene, SceneManager
from world_gal_game.ui.transitions import SceneTransition


@pytest.fixture(scope="module", autouse=True)
def _pygame() -> None:
    pygame.display.init()
    yield
    pygame.quit()


class _PaintScene(Scene):
    def __init__(self, color: tuple[int, int, int]):
        super().__init__(None)  # type: ignore[arg-type]
        self.color = color

    def draw(self, surface: pygame.Surface) -> None:
        surface.fill(self.color)


def _manager_with_abandoned_frame() -> tuple[SceneManager, pygame.Surface]:
    manager = SceneManager()
    surface = pygame.Surface((32, 18))
    manager.replace(_PaintScene((160, 20, 30)))
    manager.commit_pending()
    manager.draw(surface)
    assert manager._last_frame is not None
    return manager, surface


def test_clear_to_can_hard_cut_an_entire_mutation_batch() -> None:
    manager, surface = _manager_with_abandoned_frame()
    manager._transition = SceneTransition(
        manager._last_frame.copy(),
        style="dissolve",
        duration=1.0,
    )

    restored_base = _PaintScene((20, 80, 150))
    restored_overlay = _PaintScene((15, 130, 70))
    manager.clear_to(restored_base, transition=False)
    manager.push(restored_overlay)
    manager.commit_pending()

    assert manager.stack() == [restored_base, restored_overlay]
    assert manager._transition is None
    assert manager._last_frame is None

    manager.draw(surface)
    assert surface.get_at((0, 0))[:3] == restored_overlay.color
    assert manager._last_frame is not None
    assert manager._last_frame.get_at((0, 0))[:3] == restored_overlay.color


def test_clear_to_preserves_normal_transitions_by_default() -> None:
    manager, _surface = _manager_with_abandoned_frame()

    manager.clear_to(_PaintScene((20, 80, 150)))
    manager.commit_pending()

    assert manager._transition is not None
    assert manager._transition.style == "dissolve"
    assert manager._last_frame is not None


def test_loaded_view_reconstruction_requests_a_hard_cut(monkeypatch) -> None:
    manager, _surface = _manager_with_abandoned_frame()
    restored = _PaintScene((20, 80, 150))
    monkeypatch.setattr(app_module, "ExplorationScene", lambda _ctx: restored)

    app = object.__new__(GalGameApp)
    app.ctx = object()
    app.manager = manager
    app._resume_matches_loaded_state = lambda _resume: False
    app._exploration_callbacks = lambda: {}

    assert app._restore_loaded_view(None) is True
    manager.commit_pending()

    assert manager.stack() == [restored]
    assert manager._transition is None
    assert manager._last_frame is None
