"""App-level contracts for entering and applying Settings in game."""
from __future__ import annotations

import json
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402
import pytest  # noqa: E402

from world_gal_game.config import EngineConfig  # noqa: E402
from world_gal_game.dev.driver import GameDriver  # noqa: E402
from world_gal_game.scenes.menu_scene import MenuScene  # noqa: E402
from world_gal_game.scenes.settings_scene import SettingsScene  # noqa: E402


@pytest.fixture
def driver() -> GameDriver:
    instance = GameDriver(pack="demo_pack", isolated_runtime=True)
    yield instance
    instance.quit()


def test_menu_settings_back_resumes_same_category_and_focus(
    driver: GameDriver,
) -> None:
    driver.cfg.seen_intro = True
    driver.new_game()
    assert type(driver.app.manager.current).__name__ != "TitleScene"

    driver.app._open_menu()
    driver.advance_frames(1)
    menu = driver.app.manager.current
    assert isinstance(menu, MenuScene)

    system_index = next(
        index
        for index, (category_id, _label, _items) in enumerate(menu._categories)
        if category_id == "system"
    )
    menu._set_category(system_index)
    settings_index = next(
        index
        for index, button in enumerate(menu._buttons)
        if button.on_click == menu.on_settings
    )
    menu._focus = settings_index
    before = menu.describe()

    assert menu._buttons[settings_index].activate() is True
    driver.advance_frames(1)

    stack = driver.app.manager.stack()
    assert isinstance(stack[-1], SettingsScene)
    assert stack[-2] is menu

    driver.press_escape()
    driver.advance_frames(1)

    assert driver.app.manager.current is menu
    after = menu.describe()
    assert after["active_category"] == before["active_category"] == "system"
    assert after["focused"] == before["focused"]
    assert menu._category_index == system_index
    assert menu._focus == settings_index


def test_headless_fullscreen_toggle_calls_app_callback_and_persists(
    driver: GameDriver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = driver.app
    assert app.headless is True
    assert app.display is None
    app.config.fullscreen = False

    callback_values: list[bool] = []
    apply_to_live_window = app._apply_fullscreen_setting

    def observe_and_apply(enabled: bool) -> None:
        callback_values.append(enabled)
        apply_to_live_window(enabled)

    monkeypatch.setattr(app, "_apply_fullscreen_setting", observe_and_apply)

    def unexpected_window_creation(*_args, **_kwargs):
        pytest.fail("headless fullscreen changes must not create a window")

    monkeypatch.setattr(pygame.display, "set_mode", unexpected_window_creation)
    app._open_settings()
    app.manager.commit_pending()
    settings = app.manager.current
    assert isinstance(settings, SettingsScene)

    for expected in (True, False):
        settings._toggle_fullscreen()

        assert app.config.fullscreen is expected
        assert callback_values[-1] is expected
        payload = json.loads(
            app.config.settings_path().read_text(encoding="utf-8"),
        )
        assert payload["fullscreen"] is expected

        restored = EngineConfig(
            writable_root_override=app.config.writable_root_override,
        )
        restored.load_from_disk()
        assert restored.fullscreen is expected

    assert callback_values == [True, False]
