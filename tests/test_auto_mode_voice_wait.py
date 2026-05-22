"""Auto-mode voice wait + delay scaling (WP-3A).

Auto-play polish guarantees:

- When ``config.auto_play_wait_voice`` is True the scene must NOT auto-advance
  while ``assets.voice_busy()`` reports a clip is still playing; once the clip
  finishes it advances after the (speed-scaled) delay.
- The effective delay is ``auto_play_delay / max(0.1, auto_play_speed)`` — a
  higher ``auto_play_speed`` shortens the wait.

``assets.voice_busy`` is monkeypatched to a controllable flag so these run
without a real audio device or timing flakiness.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from world_gal_game.core.story_graph import Scene, Line


@pytest.fixture(autouse=True, scope="module")
def _restore_pygame_after_module():
    """GameDriver.quit() calls pygame.quit(), which tears the video subsystem
    down. Some sibling test modules (e.g. test_build_android) init pygame at
    import time and assume it stays up at run time. Re-init a tiny display on
    teardown so this module leaves the global pygame state usable for whatever
    runs next, regardless of collection order."""
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


def _open_two_line_dialogue(driver, *, busy_flag):
    """DialogueScene over a two-line scene, with voice_busy driven by the
    mutable ``busy_flag`` dict ({'busy': bool}) and play_voice neutralised."""
    app = driver.app
    app.assets.voice_busy = lambda: busy_flag["busy"]
    app.assets.play_voice = lambda *a, **k: None
    app.assets.stop_voice = lambda *a, **k: None
    sc = Scene(id="auto_probe", lines=[
        Line(speaker="akari", text="first", voice="assets/voice/a.ogg"),
        Line(speaker="akari", text="second"),
    ])
    app.state.story.add_scene(sc)
    app._start_dialogue("auto_probe")
    app.manager.commit_pending()
    driver.advance_frames(1)
    ds = app.manager.current
    assert type(ds).__name__ == "DialogueScene"
    # Reveal the first line instantly so the typewriter isn't what's gating us.
    if ds.box:
        ds.box.force_reveal()
    return ds


def test_auto_does_not_advance_while_voice_busy(driver):
    driver.app.config.auto_play_wait_voice = True
    driver.app.config.auto_play_delay = 0.5
    driver.app.config.auto_play_speed = 1.0
    busy = {"busy": True}
    ds = _open_two_line_dialogue(driver, busy_flag=busy)
    ds.auto_play_enabled = True
    assert ds._current_line.text == "first"
    # Pump well past the delay; while busy, the line must not advance.
    driver.advance_frames(120)
    assert ds._current_line.text == "first"
    # The countdown timer is held at zero while waiting on voice.
    assert ds._auto_play_timer == 0.0


def test_auto_advances_after_voice_finishes(driver):
    driver.app.config.auto_play_wait_voice = True
    driver.app.config.auto_play_delay = 0.2
    driver.app.config.auto_play_speed = 1.0
    busy = {"busy": True}
    ds = _open_two_line_dialogue(driver, busy_flag=busy)
    ds.auto_play_enabled = True
    driver.advance_frames(30)
    assert ds._current_line.text == "first"   # still waiting on voice
    # Voice finishes -> the delay starts counting, then it advances.
    busy["busy"] = False
    driver.advance_frames(60)                  # 1.0s @ 1/60 >> 0.2s delay
    assert ds._current_line.text == "second"


def test_wait_voice_off_advances_even_while_busy(driver):
    driver.app.config.auto_play_wait_voice = False
    driver.app.config.auto_play_delay = 0.2
    driver.app.config.auto_play_speed = 1.0
    busy = {"busy": True}                       # busy, but we don't wait
    ds = _open_two_line_dialogue(driver, busy_flag=busy)
    ds.auto_play_enabled = True
    driver.advance_frames(60)
    assert ds._current_line.text == "second"


def test_higher_speed_advances_sooner(driver):
    """A higher auto_play_speed shortens the effective delay, so the line
    advances within a frame budget that a slow speed would not yet hit."""
    driver.app.config.auto_play_wait_voice = False
    driver.app.config.auto_play_delay = 1.0
    busy = {"busy": False}

    # Slow: delay = 1.0 / 0.5 = 2.0s -> ~120 frames. After 30 frames (~0.5s)
    # it must NOT have advanced yet.
    driver.app.config.auto_play_speed = 0.5
    ds_slow = _open_two_line_dialogue(driver, busy_flag=busy)
    ds_slow.auto_play_enabled = True
    driver.advance_frames(30)
    assert ds_slow._current_line.text == "first"

    # Fast: delay = 1.0 / 4.0 = 0.25s -> ~15 frames. After 30 frames it has
    # advanced.
    driver.app.config.auto_play_speed = 4.0
    ds_fast = _open_two_line_dialogue(driver, busy_flag=busy)
    ds_fast.auto_play_enabled = True
    driver.advance_frames(30)
    assert ds_fast._current_line.text == "second"
