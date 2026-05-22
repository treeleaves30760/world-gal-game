"""Two-stage skip (WP-3A).

Skip has two modes, switched by ``config.skip_unread_only``:

- ``True``  (default): jump past already-read lines, stop at the first unread
  line or any choice point. Implemented by ``DialogueEngine.skip_to_next_unread``.
- ``False`` (skip-all): race through *every* remaining line — read or unread —
  until a choice or the scene end. Implemented by ``DialogueEngine.skip_all``.

The first half tests the engine helpers in isolation; the second half drives a
real DialogueScene through GameDriver to confirm ``_trigger_skip`` dispatches on
the config flag and that the SKIP indicator state is tracked.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import Scene, Line, Choice
from world_gal_game.dialogue.dialogue_engine import DialogueEngine
from world_gal_game.scenes.dialogue_scene import DialogueScene


@pytest.fixture(autouse=True, scope="module")
def _restore_pygame_after_module():
    """Re-init a tiny display on teardown so this module (which quits pygame
    via GameDriver) leaves the global pygame state usable for whatever test
    module pytest runs next, regardless of collection order."""
    yield
    if not pygame.display.get_init():
        pygame.init()
        pygame.display.set_mode((10, 10))


def _build_engine(scenes: list[Scene]) -> tuple[DialogueEngine, GameState]:
    s = GameState()
    for sc in scenes:
        s.story.add_scene(sc)
    return DialogueEngine(s), s


# ---------------------------------------------------------------------------
# Engine helper: skip_to_next_unread (skip-read)
# ---------------------------------------------------------------------------


def test_skip_to_next_unread_stops_at_first_unread():
    sc = Scene(id="s", lines=[
        Line(text="a"), Line(text="b"), Line(text="c"), Line(text="d"),
    ])
    eng, state = _build_engine([sc])
    eng.start_scene("s")            # presents "a" (idx 0 marked read)
    # Pre-mark lines 1 and 2 as read; line 3 ("d") is still unread.
    state.read_log.mark_line("s", 1)
    state.read_log.mark_line("s", 2)
    pres = eng.skip_to_next_unread()
    assert pres is not None
    assert pres.kind == "line"
    # It skipped the read b/c and landed on the unread "d".
    assert pres.line.text == "d"


def test_skip_to_next_unread_stops_at_choice():
    sc = Scene(id="s", lines=[Line(text="a"), Line(text="b")], choices=[
        Choice(id="c", text="pick"),
    ])
    eng, state = _build_engine([sc])
    eng.start_scene("s")
    state.read_log.mark_line("s", 1)   # b is read; nothing unread remains
    pres = eng.skip_to_next_unread()
    assert pres is not None
    assert pres.kind == "choice"


# ---------------------------------------------------------------------------
# Engine helper: skip_all (skip-all)
# ---------------------------------------------------------------------------


def test_skip_all_races_past_unread_to_choice():
    sc = Scene(id="s", lines=[
        Line(text="a"), Line(text="b"), Line(text="c"),
    ], choices=[Choice(id="c", text="pick")])
    eng, state = _build_engine([sc])
    eng.start_scene("s")            # presents "a"; b and c are UNREAD
    pres = eng.skip_all()
    # Unlike skip_to_next_unread, skip_all blows past the unread b/c and
    # halts only at the choice.
    assert pres is not None
    assert pres.kind == "choice"


def test_skip_all_to_scene_end_returns_none():
    sc = Scene(id="s", lines=[Line(text="a"), Line(text="b")])
    eng, state = _build_engine([sc])
    eng.start_scene("s")
    pres = eng.skip_all()
    # No choice; the scene ends -> None, and the scene is marked played.
    assert pres is None
    assert state.story.is_played("s")


def test_skip_all_marks_skipped_lines_read():
    sc = Scene(id="s", lines=[Line(text="a"), Line(text="b"), Line(text="c")],
               choices=[Choice(id="c", text="pick")])
    eng, state = _build_engine([sc])
    eng.start_scene("s")
    eng.skip_all()
    # Every line was walked through, so all are now in the read log.
    assert state.read_log.is_read("s", 0)
    assert state.read_log.is_read("s", 1)
    assert state.read_log.is_read("s", 2)


# ---------------------------------------------------------------------------
# Scene-level dispatch via _trigger_skip honouring config.skip_unread_only
# ---------------------------------------------------------------------------


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
    # Force the first line fully revealed so skip doesn't just complete the
    # typewriter on its first call.
    if ds.box and not ds.box.fully_revealed():
        ds.box.force_reveal()
    return ds


def test_scene_skip_read_stops_at_unread(driver):
    driver.app.config.skip_unread_only = True
    sc = Scene(id="probe_read", lines=[
        Line(text="a"), Line(text="b"), Line(text="c"), Line(text="d"),
    ])
    ds = _open_dialogue(driver, sc)
    driver.app.state.read_log.mark_line("probe_read", 1)
    driver.app.state.read_log.mark_line("probe_read", 2)
    ds._trigger_skip()
    # Stopped on the first unread line ("d"), not at the end.
    assert ds._current_line is not None
    assert ds._current_line.text == "d"


def test_scene_skip_all_races_to_end(driver):
    driver.app.config.skip_unread_only = False
    done = {"hit": False}
    sc = Scene(id="probe_all", lines=[
        Line(text="a"), Line(text="b"), Line(text="c"),
    ])
    app = driver.app
    app.state.story.add_scene(sc)
    # Use a sentinel on_done so we can assert the scene completed.
    app.manager.push(
        DialogueScene(app.ctx),
        scene_id="probe_all",
        on_done=lambda: done.__setitem__("hit", True),
    )
    app.manager.commit_pending()
    driver.advance_frames(1)
    ds = app.manager.current
    if ds.box and not ds.box.fully_revealed():
        ds.box.force_reveal()
    ds._trigger_skip()
    # Skip-all blew through every line and ended the scene -> on_done fired.
    assert done["hit"] is True


def test_ctrl_keydown_sets_skip_indicator_and_keyup_clears(driver):
    sc = Scene(id="probe_ind", lines=[
        Line(text="a"), Line(text="b"), Line(text="c"), Line(text="d"),
    ])
    # Keep something unread so a skip-read step lands on a line (scene stays up).
    driver.app.config.skip_unread_only = True
    ds = _open_dialogue(driver, sc)
    # Inject a bare Ctrl KEYDOWN (held) — sets the indicator + fires one step.
    driver._pending.events.append(
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_LCTRL, mod=0))
    driver.advance_frames(1)
    assert ds._skip_active is True
    assert ds.describe()["skip_active"] is True
    # Now release Ctrl — the indicator clears.
    driver._pending.events.append(
        pygame.event.Event(pygame.KEYUP, key=pygame.K_LCTRL, mod=0))
    driver.advance_frames(1)
    assert ds._skip_active is False
