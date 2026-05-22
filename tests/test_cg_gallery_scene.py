"""CG gallery overlay scene (WP-A).

Drives the real app headlessly via GameDriver: opens the gallery overlay,
checks it reports the right unlocked count from ``state.cg_gallery``,
exercises the fullscreen open/close path, and confirms the empty-pack case
renders without error.
"""
import os

import pygame
import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


@pytest.fixture
def driver():
    from world_gal_game.dev.driver import GameDriver
    d = GameDriver(pack="demo_pack")
    yield d
    d.quit()


def _open_gallery(driver):
    driver.app._open_cg_gallery()
    driver.advance_frames(2)
    scene = driver.app.manager.current
    assert type(scene).__name__ == "CGGalleryScene"
    return scene


def test_gallery_opens_and_is_overlay(driver):
    scene = _open_gallery(driver)
    assert scene.is_overlay is True
    d = scene.describe()
    assert d["scene"] == "CGGalleryScene"
    # describe() is JSON-able and carries the unlocked count.
    assert "unlocked_count" in d
    assert isinstance(d["unlocked"], list)
    assert d["unlocked_count"] == len(driver.app.state.cg_gallery.unlocked)


def test_empty_pack_renders_without_error(driver):
    """demo_pack ships an empty assets/cgs dir; the grid must not crash."""
    scene = _open_gallery(driver)
    # No CGs unlocked and (in a clean pack) none on disk.
    assert scene.describe()["unlocked_count"] == 0
    # A draw pass exercises the empty-grid branch.
    driver.advance_frames(2)
    assert type(driver.app.manager.current).__name__ == "CGGalleryScene"


def test_unlocked_cg_appears_in_grid(driver):
    """Unlocking a CG then opening the gallery reflects it in the count and
    in the discovered set (graceful degradation enumerates the unlocked set
    even when assets/cgs is empty)."""
    driver.app.state.cg_gallery.unlock("assets/cgs/lake.png")
    driver.app.state.cg_gallery.unlock("assets/cgs/festival.png")
    scene = _open_gallery(driver)
    d = scene.describe()
    assert d["unlocked_count"] == 2
    assert d["total"] >= 2
    assert "assets/cgs/lake.png" in d["unlocked"]
    # Both unlocked paths must be present as cells after a draw pass.
    driver.advance_frames(1)
    cell_paths = {p for _r, p, _u in scene._cells}
    assert {"assets/cgs/lake.png", "assets/cgs/festival.png"} <= cell_paths


def test_click_unlocked_cell_opens_fullscreen_then_closes(driver):
    driver.app.state.cg_gallery.unlock("assets/cgs/lake.png")
    scene = _open_gallery(driver)
    driver.advance_frames(1)  # populate _cells via a draw pass
    # Locate the unlocked cell and click its centre (translated to screen).
    target = next((rect for rect, path, unlocked in scene._cells
                   if unlocked and path == "assets/cgs/lake.png"), None)
    assert target is not None
    screen_pt = (scene._scroll.rect.x + target.centerx,
                 scene._scroll.rect.y + target.centery - scene._scroll.scroll_y)
    driver.click(screen_pt)
    driver.advance_frames(2)
    assert scene._fullscreen == "assets/cgs/lake.png"
    assert scene.describe()["fullscreen"] == "assets/cgs/lake.png"
    # A click anywhere returns to the grid.
    driver.click((640, 360))
    driver.advance_frames(2)
    assert scene._fullscreen is None


def test_escape_in_fullscreen_returns_to_grid_not_close(driver):
    driver.app.state.cg_gallery.unlock("assets/cgs/lake.png")
    scene = _open_gallery(driver)
    scene._fullscreen = "assets/cgs/lake.png"
    driver.press_key(pygame.K_ESCAPE)
    driver.advance_frames(2)
    # Esc closes the fullscreen view but keeps the gallery on the stack.
    assert scene._fullscreen is None
    assert type(driver.app.manager.current).__name__ == "CGGalleryScene"


def test_cancel_in_grid_closes_overlay(driver):
    scene = _open_gallery(driver)
    assert scene._fullscreen is None
    driver.press_key(pygame.K_ESCAPE)
    driver.advance_frames(3)
    # on_close pops the overlay; we should no longer be on the gallery.
    assert type(driver.app.manager.current).__name__ != "CGGalleryScene"
