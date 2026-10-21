"""Title-owned audio ends when gameplay replaces the title scene."""
from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from world_gal_game.config import EngineConfig
from world_gal_game.core.game_state import GameState
from world_gal_game.core.localization import Localization
from world_gal_game.scenes.base import SceneContext
from world_gal_game.scenes.title import TitleScene
from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.theme import default_theme


class _Assets:
    def __init__(self) -> None:
        self.stops: list[int] = []

    def play_music(self, path, *, volume=0.6):
        return None

    def stop_ambient(self, *, fade_ms=0):
        return None

    def stop_music(self, *, fade_ms=800):
        self.stops.append(fade_ms)


class _AcceptingAssets(_Assets):
    def __init__(self) -> None:
        super().__init__()
        self._current_music = None

    def play_music(self, path, *, volume=0.6):
        self._current_music = path


@pytest.fixture(scope="module", autouse=True)
def _pygame_init():
    pygame.display.init()
    pygame.font.init()
    yield
    pygame.quit()


def test_title_exit_fades_its_music_before_silent_opening() -> None:
    assets = _Assets()
    config = EngineConfig(screen_size=(1280, 720))
    scene = TitleScene(SceneContext(
        config=config,
        state=GameState(),
        npcs=None,  # type: ignore[arg-type]
        brain=None,  # type: ignore[arg-type]
        dialogue=None,  # type: ignore[arg-type]
        assets=assets,  # type: ignore[arg-type]
        fonts=FontRegistry(candidates=()),
        theme=default_theme(),
        localization=Localization(),
        screen_size=(1280, 720),
    ))
    scene.enter(
        bgm="assets/bgm/title.ogg",
        on_new_game=lambda: None,
        on_load=lambda: None,
        on_quit=lambda: None,
    )

    scene.exit()

    assert assets.stops == [350]


def test_title_exit_does_not_cut_music_already_owned_by_story() -> None:
    assets = _Assets()
    config = EngineConfig(screen_size=(1280, 720))
    state = GameState()
    scene = TitleScene(SceneContext(
        config=config,
        state=state,
        npcs=None,  # type: ignore[arg-type]
        brain=None,  # type: ignore[arg-type]
        dialogue=None,  # type: ignore[arg-type]
        assets=assets,  # type: ignore[arg-type]
        fonts=FontRegistry(candidates=()),
        theme=default_theme(),
        localization=Localization(),
        screen_size=(1280, 720),
    ))
    scene.enter(
        bgm="assets/bgm/title.ogg",
        on_new_game=lambda: None,
        on_load=lambda: None,
        on_quit=lambda: None,
    )
    state.meta["current_bgm"] = "assets/bgm/opening.ogg"

    scene.exit()

    assert assets.stops == []


def test_title_bgm_unlocks_only_after_authorized_backend_ownership() -> None:
    from world_gal_game.core.media_authority import build_media_authority
    from world_gal_game.core.story_graph import StoryGraph

    path = "assets/bgm/title.ogg"
    assets = _AcceptingAssets()
    state = GameState()
    state.meta["__pack_media_authority__"] = build_media_authority(
        StoryGraph(),
        additional_music_ids={path},
    )
    scene = TitleScene(SceneContext(
        config=EngineConfig(screen_size=(1280, 720)),
        state=state,
        npcs=None,  # type: ignore[arg-type]
        brain=None,  # type: ignore[arg-type]
        dialogue=None,  # type: ignore[arg-type]
        assets=assets,  # type: ignore[arg-type]
        fonts=FontRegistry(candidates=()),
        theme=default_theme(),
        localization=Localization(),
        screen_size=(1280, 720),
    ))

    scene.enter(bgm=path)

    assert state.music_room.unlocked == {path}


def test_title_bgm_unlock_uses_canonical_authorized_identity() -> None:
    from world_gal_game.core.media_authority import build_media_authority
    from world_gal_game.core.story_graph import StoryGraph

    canonical = "assets/bgm/title.ogg"
    authored = "assets/bgm/./title.ogg"
    assets = _AcceptingAssets()
    state = GameState()
    state.meta["__pack_media_authority__"] = build_media_authority(
        StoryGraph(),
        additional_music_ids={canonical},
    )
    scene = TitleScene(SceneContext(
        config=EngineConfig(screen_size=(1280, 720)),
        state=state,
        npcs=None,  # type: ignore[arg-type]
        brain=None,  # type: ignore[arg-type]
        dialogue=None,  # type: ignore[arg-type]
        assets=assets,  # type: ignore[arg-type]
        fonts=FontRegistry(candidates=()),
        theme=default_theme(),
        localization=Localization(),
        screen_size=(1280, 720),
    ))

    scene.enter(bgm=authored)

    assert assets._current_music == authored
    assert state.music_room.unlocked == {canonical}


def test_title_bgm_does_not_unlock_when_backend_rejects_or_path_is_forged() -> None:
    from world_gal_game.core.media_authority import build_media_authority
    from world_gal_game.core.story_graph import StoryGraph

    allowed = "assets/bgm/title.ogg"
    state = GameState()
    state.meta["__pack_media_authority__"] = build_media_authority(
        StoryGraph(),
        additional_music_ids={allowed},
    )
    rejecting = _Assets()
    scene = TitleScene(SceneContext(
        config=EngineConfig(screen_size=(1280, 720)),
        state=state,
        npcs=None,  # type: ignore[arg-type]
        brain=None,  # type: ignore[arg-type]
        dialogue=None,  # type: ignore[arg-type]
        assets=rejecting,  # type: ignore[arg-type]
        fonts=FontRegistry(candidates=()),
        theme=default_theme(),
        localization=Localization(),
        screen_size=(1280, 720),
    ))
    scene.enter(bgm=allowed)
    assert state.music_room.unlocked == set()

    accepting = _AcceptingAssets()
    scene.ctx.assets = accepting  # type: ignore[assignment]
    scene.enter(bgm="assets/bgm/forged.ogg")
    assert state.music_room.unlocked == set()
