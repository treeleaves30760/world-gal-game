"""Movie playback (Pillar 4 of the presentation layer).

Covers the image-sequence player, the player registry, the play_movie effect,
the MoviePlayerScene overlay, and the play_movie → on_movie → push flow.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import Effect, Scene, Line
from world_gal_game.plugins.builtin_effects import VISUAL_FX_QUEUE
from world_gal_game.ui.movie_player import (
    ImageSequencePlayer, register_movie_player, resolve_movie_player,
    list_movie_players, MOVIE_PLAYERS,
)


class _FakeAssets:
    """Minimal asset stub: returns a solid surface for any frame path."""

    def scaled(self, path, size, fit="contain"):
        s = pygame.Surface(size)
        s.fill((30, 30, 30))
        return s

    def _resolve(self, path):
        return None


# ---------------------------------------------------------------------------
# ImageSequencePlayer
# ---------------------------------------------------------------------------

def test_empty_sequence_is_immediately_done():
    p = ImageSequencePlayer([], _FakeAssets())
    assert p.done is True
    assert p.frame_count == 0


def test_sequence_plays_through_then_done():
    pygame.display.init()
    p = ImageSequencePlayer(["a", "b", "c"], _FakeAssets(), fps=10,
                            screen_size=(160, 120))
    assert p.done is False
    p.update(0.05)
    p.draw(pygame.Surface((160, 120)))   # mid-playback, must not raise
    assert p.done is False
    p.update(1.0)                        # well past 3 frames at 10fps
    assert p.done is True


def test_skip_ends_immediately():
    p = ImageSequencePlayer(["a", "b", "c"], _FakeAssets(), fps=1)
    p.skip()
    assert p.done is True


def test_loop_never_finishes_on_its_own():
    p = ImageSequencePlayer(["a", "b"], _FakeAssets(), fps=10, loop=True)
    p.update(100.0)
    assert p.done is False               # loops forever until skipped
    p.skip()
    assert p.done is True


# ---------------------------------------------------------------------------
# Player registry
# ---------------------------------------------------------------------------

def test_registry_register_resolve_list():
    sentinel = object()
    try:
        register_movie_player("unit_fmt", lambda *a, **k: sentinel)
        assert resolve_movie_player("unit_fmt") is not None
        assert "unit_fmt" in list_movie_players()
        assert "image_sequence" in list_movie_players()   # always present
    finally:
        MOVIE_PLAYERS.pop("unit_fmt", None)


# ---------------------------------------------------------------------------
# play_movie effect
# ---------------------------------------------------------------------------

def test_play_movie_enqueues_directive():
    s = GameState()
    out = s.apply(Effect(kind="play_movie", target="movies/op",
                         value={"kind": "image_sequence", "fps": 30,
                                "loop": False, "skippable": False}))
    assert out["path"] == "movies/op"
    d = s.meta.get(VISUAL_FX_QUEUE, [])[0]
    assert d == {"fx": "play_movie", "path": "movies/op",
                 "kind": "image_sequence", "fps": 30.0,
                 "loop": False, "skippable": False}


def test_play_movie_defaults():
    s = GameState()
    s.apply(Effect(kind="play_movie", target="movies/ed"))
    d = s.meta.get(VISUAL_FX_QUEUE, [])[0]
    assert d["kind"] == "auto" and d["fps"] == 24.0
    assert d["loop"] is False and d["skippable"] is True


def test_capability_manifest_exports_play_movie_and_players():
    from world_gal_game.dev.capability_manifest import build_manifest
    m = build_manifest()
    by_kind = {e["kind"]: e for e in m["effects"]}
    assert "play_movie" in by_kind and "args_schema" in by_kind["play_movie"]
    assert "image_sequence" in m["markup"]["movie_players"]
    assert "jump" in m["markup"]["portrait_emotes"]


# ---------------------------------------------------------------------------
# MoviePlayerScene + integration
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="module")
def _restore_pygame_after_module():
    yield
    if not pygame.display.get_init():
        pygame.init()
        pygame.display.set_mode((10, 10))


@pytest.fixture
def driver():
    from world_gal_game.dev.driver import GameDriver
    d = GameDriver(pack="demo_pack")
    yield d
    d.quit()


class _LivePlayer:
    """A player that stays alive until skipped (for push/skip assertions)."""

    def __init__(self, *a, **k):
        self._done = False
        self.frame_count = 3

    @property
    def done(self):
        return self._done

    def skip(self):
        self._done = True

    def update(self, dt):
        pass

    def draw(self, surface):
        pass


def test_play_movie_pushes_overlay_that_skips_back(driver):
    register_movie_player("unit_live", _LivePlayer)
    try:
        sc = Scene(id="probe_movie_push", lines=[
            Line(text="op", effects=[Effect(
                kind="play_movie", target="movies/op",
                value={"kind": "unit_live", "skippable": True})]),
            Line(text="after movie"),
        ])
        app = driver.app
        app.state.story.add_scene(sc)
        app._start_dialogue(sc.id)
        app.manager.commit_pending()
        driver.advance_frames(2)
        top = app.manager.current
        assert type(top).__name__ == "MoviePlayerScene"
        assert top.describe()["has_player"] is True

        # A click skips the movie; the overlay pops back to the dialogue.
        driver._pending.events.append(
            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE, mod=0))
        driver.advance_frames(2)
        assert type(app.manager.current).__name__ == "DialogueScene"
    finally:
        MOVIE_PLAYERS.pop("unit_live", None)


def test_missing_movie_pops_immediately(driver):
    # An unregistered kind / missing folder → inert player → instant pop back.
    sc = Scene(id="probe_movie_missing", lines=[
        Line(text="op", effects=[Effect(
            kind="play_movie", target="movies/does_not_exist",
            value={"kind": "image_sequence"})]),
        Line(text="after"),
    ])
    app = driver.app
    app.state.story.add_scene(sc)
    app._start_dialogue(sc.id)
    app.manager.commit_pending()
    driver.advance_frames(3)
    # Never gets stuck on a blank movie overlay.
    assert type(app.manager.current).__name__ == "DialogueScene"
