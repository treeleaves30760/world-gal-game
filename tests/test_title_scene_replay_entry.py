"""Title-screen entry contracts for the persistent replay library."""
from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from world_gal_game.config import EngineConfig
from world_gal_game.core.game_state import GameState
from world_gal_game.core.localization import Localization
from world_gal_game.core.title_screen_spec import TitleScreenSpec
from world_gal_game.dev.driver import GameDriver
from world_gal_game.scenes.base import SceneContext
from world_gal_game.scenes.scene_replay_scene import SceneReplayScene
from world_gal_game.scenes.title import TitleScene
from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.input import InputState
from world_gal_game.ui.theme import default_theme


class _Assets:
    def stop_ambient(self, *, fade_ms=0) -> None:
        pass

    def play_music(self, path, *, volume=0.6) -> None:
        pass

    def scaled(self, path, size, *, fit="cover") -> pygame.Surface:
        return pygame.Surface(size, pygame.SRCALPHA)


@pytest.fixture(autouse=True)
def _pygame_lifecycle():
    pygame.display.init()
    pygame.font.init()
    yield
    pygame.quit()


def _make_scene(
    size: tuple[int, int],
    *,
    on_scene_replay=None,
    presentation: TitleScreenSpec | None = None,
) -> TitleScene:
    config = EngineConfig(
        title="World Gal-Game",
        subtitle="Persistent gallery regression",
        screen_size=size,
    )
    ctx = SceneContext(
        config=config,
        state=GameState(),
        npcs=None,  # type: ignore[arg-type]
        brain=None,  # type: ignore[arg-type]
        dialogue=None,  # type: ignore[arg-type]
        assets=_Assets(),  # type: ignore[arg-type]
        fonts=FontRegistry(
            candidates=(),
            scale=max(1.0, size[1] / 720.0),
        ),
        theme=default_theme(),
        localization=Localization(),
        screen_size=size,
    )

    def noop() -> None:
        pass

    scene = TitleScene(ctx)
    scene.enter(
        title=config.title,
        subtitle=config.subtitle,
        version="1.0",
        presentation=presentation,
        on_continue=noop,
        on_new_game=noop,
        on_load=noop,
        on_quit=noop,
        on_cg_gallery=noop,
        on_music_room=noop,
        on_endings=noop,
        on_flowchart=noop,
        on_scene_replay=on_scene_replay,
        on_credits=noop,
        on_settings=noop,
    )
    return scene


def _replay_item(scene: TitleScene):
    assert scene.menu is not None
    return next(
        (index, item)
        for index, item in enumerate(scene.menu.items)
        if item.label == "場景重溫"
    )


@pytest.mark.parametrize(
    "size",
    [
        (640, 360),
        (800, 450),
        (1280, 720),
        (1920, 1080),
    ],
)
def test_scene_replay_extra_fits_supported_title_canvases(size) -> None:
    scene = _make_scene(size, on_scene_replay=lambda: None)
    scene._open_extras()
    assert scene.menu is not None
    assert [item.label for item in scene.menu.items].count("場景重溫") == 1

    canvas = pygame.Rect((0, 0), size)
    assert canvas.contains(scene._panel_rect)
    assert canvas.contains(scene.menu.rect)
    assert scene._panel_rect.contains(scene.menu.rect)
    assert scene.menu.row_h >= 44
    assert scene.menu.rect.height == (
        scene.menu.visible_rows * scene.menu.row_h
    )
    for index in range(scene.menu.visible_rows):
        row = pygame.Rect(
            scene.menu.rect.x,
            scene.menu.rect.y + index * scene.menu.row_h,
            scene.menu.rect.width,
            scene.menu.row_h,
        )
        assert canvas.contains(row)
        assert scene._panel_rect.contains(row)

    scene.draw(pygame.Surface(size, pygame.SRCALPHA))


@pytest.mark.parametrize(
    "size",
    [
        (640, 360),
        (800, 450),
        (1280, 720),
        (1920, 1080),
    ],
)
@pytest.mark.parametrize("layout", ["left_rail", "right_rail"])
def test_scene_replay_extra_keeps_44px_rows_in_branded_rails(
    size,
    layout: str,
) -> None:
    scene = _make_scene(
        size,
        on_scene_replay=lambda: None,
        presentation=TitleScreenSpec(
            layout=layout,
            panel_style="rail",
            name_entry="on_new_game",
        ),
    )
    scene._open_extras()

    assert scene.menu.row_h >= 44
    assert scene._panel_rect.contains(scene.menu.rect)
    assert scene.menu.rect.height == (
        scene.menu.visible_rows * scene.menu.row_h
    )
    scene.draw(pygame.Surface(size, pygame.SRCALPHA))


def test_missing_scene_replay_callback_keeps_legacy_title_api() -> None:
    scene = _make_scene((1280, 720))
    scene._open_extras()

    assert scene.on_scene_replay is None
    assert "場景重溫" not in [item.label for item in scene.menu.items]


def test_scene_replay_callback_alone_exposes_extras_menu() -> None:
    scene = _make_scene((1280, 720), on_scene_replay=lambda: None)
    scene.on_cg_gallery = None
    scene.on_music_room = None
    scene.on_endings = None
    scene.on_flowchart = None
    scene.on_credits = None
    scene._build_menu()

    assert "鑑賞模式" in [item.label for item in scene.menu.items]
    scene._open_extras()
    assert [item.label for item in scene.menu.items] == [
        "場景重溫",
        "← 返回",
    ]


@pytest.mark.parametrize(
    "event",
    [
        pygame.event.Event(
            pygame.KEYDOWN,
            key=pygame.K_ESCAPE,
            mod=0,
            unicode="",
        ),
        pygame.event.Event(
            pygame.JOYBUTTONDOWN,
            joy=0,
            instance_id=0,
            button=1,
        ),
    ],
    ids=["keyboard", "gamepad"],
)
def test_keyboard_and_gamepad_back_close_title_extras(event) -> None:
    callbacks: list[str] = []
    scene = _make_scene(
        (1280, 720),
        on_scene_replay=lambda: callbacks.append("replay"),
    )
    scene._open_extras()
    replay_index, _ = _replay_item(scene)
    scene.menu.selected = replay_index

    scene.update(0.0, InputState(events=[event]))

    assert scene.describe()["mode"] == "main"
    assert scene.describe()["selected_index"] == 0
    assert callbacks == []


@pytest.mark.parametrize(
    "event",
    [
        pygame.event.Event(
            pygame.KEYDOWN,
            key=pygame.K_RETURN,
            mod=0,
            unicode="\r",
        ),
        pygame.event.Event(
            pygame.JOYBUTTONDOWN,
            joy=0,
            instance_id=0,
            button=0,
        ),
    ],
    ids=["keyboard", "gamepad"],
)
def test_scene_replay_extra_invokes_callback_from_keyboard_or_gamepad(
    event,
) -> None:
    callbacks: list[str] = []
    scene = _make_scene(
        (1280, 720),
        on_scene_replay=lambda: callbacks.append("replay"),
    )
    scene._open_extras()
    replay_index, _ = _replay_item(scene)
    scene.menu.selected = replay_index

    scene.update(0.0, InputState(events=[event]))

    assert callbacks == ["replay"]


@pytest.mark.parametrize("device", ["keyboard", "gamepad"])
def test_360p_focus_scroll_reaches_every_replay_item(device: str) -> None:
    callbacks: list[str] = []
    scene = _make_scene(
        (640, 360),
        on_scene_replay=lambda: callbacks.append("replay"),
    )
    scene._open_extras()
    replay_index, _ = _replay_item(scene)
    assert replay_index >= scene.menu.visible_rows

    for _ in range(replay_index):
        event = (
            pygame.event.Event(
                pygame.KEYDOWN,
                key=pygame.K_DOWN,
                mod=0,
                unicode="",
            )
            if device == "keyboard"
            else pygame.event.Event(
                pygame.JOYHATMOTION,
                joy=0,
                instance_id=0,
                hat=0,
                value=(0, -1),
            )
        )
        scene.update(0.0, InputState(events=[event]))

    assert scene.menu.selected == replay_index
    assert scene.menu.scroll_offset > 0
    confirm = (
        pygame.event.Event(
            pygame.KEYDOWN,
            key=pygame.K_RETURN,
            mod=0,
            unicode="\r",
        )
        if device == "keyboard"
        else pygame.event.Event(
            pygame.JOYBUTTONDOWN,
            joy=0,
            instance_id=0,
            button=0,
        )
    )
    scene.update(0.0, InputState(events=[confirm]))

    assert callbacks == ["replay"]


def test_app_wires_title_scene_replay_to_existing_overlay() -> None:
    driver = GameDriver(pack="demo_pack")
    try:
        title = driver.app.manager.current
        assert isinstance(title, TitleScene)
        assert title.on_scene_replay is not None
        title._open_extras()
        _, replay_item = _replay_item(title)

        replay_item.on_select()
        driver.app.manager.commit_pending()

        assert isinstance(driver.app.manager.current, SceneReplayScene)
        assert driver.app.manager.stack()[-2] is title

        driver.app.manager.pop()
        driver.app.manager.commit_pending()
        assert driver.app.manager.current is title
        assert title.describe()["mode"] == "extras"
    finally:
        driver.quit()
