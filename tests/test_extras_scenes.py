"""Stub extras scenes + their menu/title wiring (WP-F3).

The four overlay scenes (CG gallery, music room, endings, scene replay)
ship as minimal stubs in this work package; later WPs fill their bodies.
This test only pins that they import, open via the app's ``_open_*``
methods, describe themselves, close cleanly, and are wired into both the
in-game menu and the title's extras entry.
"""
import os

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


@pytest.fixture
def driver():
    from world_gal_game.dev.driver import GameDriver
    d = GameDriver(pack="demo_pack")
    yield d
    d.quit()


_STUBS = [
    ("CGGalleryScene", "_open_cg_gallery"),
    ("MusicRoomScene", "_open_music_room"),
    ("EndingsScene", "_open_endings"),
    ("SceneReplayScene", "_open_scene_replay"),
]


@pytest.mark.parametrize("scene_name,opener", _STUBS)
def test_stub_scene_pushes_and_describes(driver, scene_name, opener):
    getattr(driver.app, opener)()
    driver.advance_frames(2)
    top = driver.app.manager.current
    assert type(top).__name__ == scene_name
    # Every extras overlay identifies itself via describe()["scene"].
    # Stubs return exactly {"scene": name}; filled scenes (Phase 2) return a
    # richer dict that still carries the "scene" key.
    assert top.describe()["scene"] == scene_name
    assert top.is_overlay is True
    # Close it again — must pop cleanly.
    driver.app.manager.pop()
    driver.advance_frames(2)
    assert type(driver.app.manager.current).__name__ != scene_name


def test_stub_scenes_reachable_through_menu(driver):
    """The in-game menu must carry the four extras callbacks, and firing
    one (after the menu's from_menu wrapper closes the menu) lands on the
    matching overlay."""
    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.advance_frames(5)
    driver.app._open_menu()
    driver.advance_frames(2)
    menu = driver.app.manager.current
    assert type(menu).__name__ == "MenuScene"
    # All four callbacks were threaded into the menu.
    assert menu.on_cg_gallery is not None
    assert menu.on_music_room is not None
    assert menu.on_endings is not None
    assert menu.on_scene_replay is not None
    # Firing the CG-gallery callback closes the menu and opens the gallery.
    menu.on_cg_gallery()
    driver.advance_frames(3)
    assert type(driver.app.manager.current).__name__ == "CGGalleryScene"


def test_title_extras_entry_opens_gallery(driver):
    """The title screen exposes an 鑑賞模式 entry wired to the galleries."""
    title = driver.app.manager.current
    assert type(title).__name__ == "TitleScene"
    assert title.on_cg_gallery is not None
    # The extras router opens the first available gallery (CG gallery).
    title._open_extras()
    driver.advance_frames(3)
    assert type(driver.app.manager.current).__name__ == "CGGalleryScene"
