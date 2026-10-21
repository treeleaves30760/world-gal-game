"""Player-facing rollback wired into DialogueScene.

The state-history buffer itself is unit-tested in ``test_history.py``; here we
drive a real DialogueScene through GameDriver to confirm Backspace rewinds the
displayed line (and that the feature is off when ``config.rollback_enabled`` is
False). Mirrors the GameDriver harness in ``test_skip_modes.py``.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from world_gal_game.core.story_graph import Choice, Line, Scene


@pytest.fixture(autouse=True, scope="module")
def _restore_pygame_after_module():
    """Re-init a tiny display on teardown so this module (which quits pygame
    via GameDriver) leaves global pygame state usable for later test modules."""
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


def _open_dialogue(driver, scene: Scene):
    app = driver.app
    app.state.story.add_scene(scene)
    app._start_dialogue(scene.id)
    app.manager.commit_pending()
    driver.advance_frames(1)
    ds = app.manager.current
    assert type(ds).__name__ == "DialogueScene"
    if ds.box and not ds.box.fully_revealed():
        ds.box.force_reveal()
    return ds


def _advance(ds):
    if ds.box and not ds.box.fully_revealed():
        ds.box.force_reveal()
    ds._advance()
    if ds.box and not ds.box.fully_revealed():
        ds.box.force_reveal()


def _press_backspace(driver):
    driver._pending.events.append(
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_BACKSPACE, mod=0))
    driver.advance_frames(1)


def test_backspace_rewinds_to_previous_line(driver):
    driver.app.config.rollback_enabled = True
    sc = Scene(id="probe_rollback", lines=[
        Line(text="line-A"), Line(text="line-B"), Line(text="line-C"),
    ])
    ds = _open_dialogue(driver, sc)        # shows + records line-A
    assert ds._current_line.text == "line-A"
    _advance(ds)                           # line-B recorded
    assert ds._current_line.text == "line-B"
    _advance(ds)                           # line-C recorded
    assert ds._current_line.text == "line-C"
    assert ds._history is not None and ds._history.can_rewind() is True

    _press_backspace(driver)               # rewind to line-B
    assert ds._current_line.text == "line-B"
    _press_backspace(driver)               # rewind to line-A
    assert ds._current_line.text == "line-A"
    # At the first displayed line there is nothing earlier to rewind to.
    assert ds._history.can_rewind() is False


def test_choice_render_cache_releases_on_leave_not_modal_pause_and_rebuilds(
    driver,
):
    driver.app.config.rollback_enabled = True
    target = Scene(
        id="choice_cache_target",
        lines=[Line(text="after choice")],
    )
    source = Scene(
        id="choice_cache_source",
        lines=[Line(text="before choice")],
        choices=[
            Choice(
                id="continue",
                text="Continue",
                next_scene=target.id,
            ),
            Choice(
                id="alternate",
                text="Alternate",
                next_scene=target.id,
            ),
        ],
    )
    driver.app.state.story.add_scene(target)
    ds = _open_dialogue(driver, source)
    _advance(ds)
    assert ds.choices.visible is True

    ds.choices.draw(driver.app.screen)
    retained_backdrop = ds.choices._fallback_backdrop_cache
    retained_panel = ds.choices.panel._fallback_cache
    assert retained_backdrop is not None
    assert retained_panel is not None

    releases = 0
    original_release = ds.choices.release_render_cache

    def tracked_release() -> None:
        nonlocal releases
        releases += 1
        original_release()

    ds.choices.release_render_cache = tracked_release  # type: ignore[method-assign]

    ds.pause()
    ds.resume()
    assert releases == 0
    assert ds.choices._fallback_backdrop_cache is retained_backdrop
    assert ds.choices.panel._fallback_cache is retained_panel

    ds._on_choose("continue")
    assert releases == 1
    assert ds.choices.visible is False
    assert ds.choices._fallback_backdrop_cache is None
    assert ds.choices.panel._fallback_cache is None
    assert ds._current_line.text == "after choice"

    _press_backspace(driver)
    assert ds.choices.visible is True
    ds.choices.draw(driver.app.screen)
    assert ds.choices._fallback_backdrop_cache is not None
    assert ds.choices.panel._fallback_cache is not None


def test_rollback_disabled_leaves_no_history(driver):
    driver.app.config.rollback_enabled = False
    sc = Scene(id="probe_norollback", lines=[Line(text="x"), Line(text="y")])
    ds = _open_dialogue(driver, sc)
    assert ds._history is None
    # Backspace is a harmless no-op (must not raise) when rollback is off.
    _press_backspace(driver)
