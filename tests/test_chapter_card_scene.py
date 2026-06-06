"""Chapter title-card overlay + chapter-select entry (chapter-system UI layer).

Covers:
- ``ChapterCardScene``: describe(), one-shot dismissal via ``on_done``, an empty
  title degrading gracefully, and a draw() that does not raise.
- The dialogue scene routing a ``chapter_card`` visual-fx directive to
  ``on_chapter_card`` (mirrors the ``play_movie`` → ``on_movie`` routing).
- End-to-end: a ``set_chapter`` effect in a played scene pushes the
  ``ChapterCardScene`` overlay, which a key dismisses back to the dialogue
  (mirrors ``test_play_movie_pushes_overlay_that_skips_back``).
- The title screen's extras → flowchart browse entry opens the FlowchartScene
  with no jump callback, and the chart is safe with ``on_jump=None``.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from world_gal_game.core.story_graph import Effect, Scene, Line
from world_gal_game.ui.input import InputState


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


# ---------------------------------------------------------------------------
# ChapterCardScene — direct unit behaviour (built on the live SceneContext)
# ---------------------------------------------------------------------------

def _card(driver, **enter_kwargs):
    from world_gal_game.scenes.chapter_card_scene import ChapterCardScene
    sc = ChapterCardScene(driver.app.ctx)
    sc.enter(**enter_kwargs)
    return sc


def test_chapter_card_is_overlay(driver):
    sc = _card(driver, title="第一章 · 搬家當天")
    assert sc.is_overlay is True


def test_chapter_card_describe(driver):
    sc = _card(driver, title="第一章 · 搬家當天", subtitle="common")
    d = sc.describe()
    assert d == {"scene": "ChapterCardScene", "title": "第一章 · 搬家當天"}


def test_chapter_card_draws_without_error(driver):
    sc = _card(driver, title="第一章 · 搬家當天", subtitle="common")
    surf = pygame.Surface((1280, 720))
    sc.draw(surf)            # must not raise


def test_chapter_card_empty_title_is_safe(driver):
    # No title / no subtitle: degrades to a generic label, never a crash.
    sc = _card(driver, title="", subtitle="")
    surf = pygame.Surface((1280, 720))
    sc.draw(surf)
    assert sc.describe()["title"] == ""


def test_chapter_card_dismisses_once_on_advance(driver):
    calls = []
    sc = _card(driver, title="ch", on_done=lambda: calls.append(1))
    # First update is inside the grace window — no dismiss yet.
    sc.update(0.05, InputState(advance_dialogue=True))
    assert calls == []
    # Past the grace window, an advance input dismisses exactly once.
    sc.update(0.3, InputState(advance_dialogue=True))
    assert calls == [1]
    # Further advances do not re-fire on_done (guarded by _ended).
    sc.update(0.3, InputState(advance_dialogue=True))
    assert calls == [1]


def test_chapter_card_auto_dismisses(driver):
    calls = []
    sc = _card(driver, title="ch", on_done=lambda: calls.append(1))
    sc.update(0.3, InputState())          # clear grace, no input
    assert calls == []
    sc.update(sc.AUTO_DISMISS_S, InputState())   # time runs out
    assert calls == [1]


def test_chapter_card_cancel_dismisses(driver):
    calls = []
    sc = _card(driver, title="ch", on_done=lambda: calls.append(1))
    sc.update(0.3, InputState())          # clear grace
    sc.update(0.0, InputState(cancel=True))
    assert calls == [1]


# ---------------------------------------------------------------------------
# Dialogue scene routes a chapter_card directive to on_chapter_card
# ---------------------------------------------------------------------------

def test_dialogue_routes_chapter_card_directive(driver):
    from world_gal_game.scenes.dialogue_scene import DialogueScene
    sc = DialogueScene(driver.app.ctx)
    seen = []
    sc.on_chapter_card = lambda d: seen.append(d)
    directive = {"fx": "chapter_card", "chapter": "ch1",
                 "title": "第一章", "subtitle": "common"}
    sc._spawn_visual_fx(directive)
    assert seen == [directive]


def test_dialogue_chapter_card_without_callback_is_silent(driver):
    from world_gal_game.scenes.dialogue_scene import DialogueScene
    sc = DialogueScene(driver.app.ctx)
    sc.on_chapter_card = None
    # No callback wired → directive is silently ignored (no raise).
    sc._spawn_visual_fx({"fx": "chapter_card", "chapter": "ch1",
                         "title": "第一章", "subtitle": ""})


# ---------------------------------------------------------------------------
# End-to-end: set_chapter in a played scene pushes the card overlay
# ---------------------------------------------------------------------------

def test_set_chapter_pushes_card_overlay_that_dismisses(driver):
    app = driver.app
    # demo_pack ships content/chapters.yaml, so __chapters__ is populated and
    # set_chapter resolves a real chapter.
    assert app.state.meta.get("__chapters__") is not None
    sc = Scene(id="probe_chapter_card", lines=[
        Line(text="enter chapter", effects=[
            Effect(kind="set_chapter", target="ch1_arrival")]),
        Line(text="after the card"),
    ])
    app.state.story.add_scene(sc)
    app._start_dialogue(sc.id)
    app.manager.commit_pending()
    driver.advance_frames(2)
    top = app.manager.current
    assert type(top).__name__ == "ChapterCardScene"
    assert top.describe()["title"] == "第一章 · 搬家當天"

    # Burn enough frames (at 1/60s/frame) to clear the ~0.25s grace window,
    # well short of the 4s auto-dismiss, so the card is still up.
    driver.advance_frames(30)
    assert type(app.manager.current).__name__ == "ChapterCardScene"
    # A key now dismisses the card back to the dialogue (events live one frame,
    # so inject immediately before a single advancing frame).
    driver._pending.events.append(
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE, mod=0))
    driver.advance_frames(2)
    assert type(app.manager.current).__name__ == "DialogueScene"


def test_set_chapter_value_false_suppresses_card(driver):
    app = driver.app
    sc = Scene(id="probe_chapter_no_card", lines=[
        Line(text="silent chapter", effects=[
            Effect(kind="set_chapter", target="ch2_investigation", value=False)]),
        Line(text="no card here"),
    ])
    app.state.story.add_scene(sc)
    app._start_dialogue(sc.id)
    app.manager.commit_pending()
    driver.advance_frames(3)
    # No card overlay was pushed; we stay in the dialogue.
    assert type(app.manager.current).__name__ == "DialogueScene"
    assert app.state.current_chapter == "ch2_investigation"


# ---------------------------------------------------------------------------
# Chapter-select from the title (browse-only flowchart)
# ---------------------------------------------------------------------------

def test_title_flowchart_browse_opens_chart(driver):
    app = driver.app
    app._open_flowchart_browse()
    driver.advance_frames(2)
    top = app.manager.current
    assert type(top).__name__ == "FlowchartScene"
    assert top.on_jump is None          # browse-only from the title
    # describe() lists the demo pack's chapters and is JSON-able.
    d = top.describe()
    ids = {c["id"] for c in d["chapters"]}
    assert "ch1_arrival" in ids
    assert all("current" in c for c in d["chapters"])


def test_flowchart_safe_with_none_jump_on_click(driver):
    app = driver.app
    app._open_flowchart_browse()
    driver.advance_frames(2)
    top = app.manager.current
    # A click anywhere must not raise even though on_jump is None (guarded).
    top.update(0.0, InputState(mouse_clicked=True, mouse_pos=(640, 360)))


def test_flowchart_marks_current_chapter(driver):
    app = driver.app
    app.state.current_chapter = "ch1_arrival"
    app._open_flowchart_browse()
    driver.advance_frames(2)
    d = app.manager.current.describe()
    current = {c["id"] for c in d["chapters"] if c["current"]}
    assert current == {"ch1_arrival"}
