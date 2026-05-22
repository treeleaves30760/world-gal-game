"""NVLBox: transcript accumulation, scene-change reset, DialogueBox-drop-in API.

NVLBox is meant to be a drop-in for DialogueBox in DialogueScene, so these
tests assert (a) the shared surface (set_line / force_reveal / fully_revealed /
update / draw) behaves, (b) lines accumulate into a transcript, and (c) reset()
clears it for a scene change.
"""
from __future__ import annotations

import os

import pygame
import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


@pytest.fixture(autouse=True, scope="module")
def init_pygame():
    pygame.init()
    pygame.display.set_mode((1, 1))
    yield
    pygame.quit()


from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.theme import Theme
from world_gal_game.ui.nvl_box import NVLBox
from world_gal_game.ui.widgets.dialogue_box import DialogueBox


@pytest.fixture
def fonts():
    return FontRegistry(("Arial", "DejaVu Sans"))


@pytest.fixture
def theme():
    return Theme()


def _nvl(fonts, theme, text_speed=0.0):
    # text_speed=0 → instant reveal so fully_revealed() is deterministic.
    return NVLBox(pygame.Rect(40, 40, 1200, 640),
                  fonts=fonts, theme=theme, text_speed=text_speed)


# ---------------------------------------------------------------------------
# Drop-in API parity with DialogueBox
# ---------------------------------------------------------------------------

def test_exposes_dialoguebox_surface(fonts, theme):
    box = _nvl(fonts, theme)
    for name in ("set_line", "force_reveal", "fully_revealed", "update", "draw"):
        assert callable(getattr(box, name)), name
        assert hasattr(DialogueBox, name), name


def test_empty_box_is_fully_revealed(fonts, theme):
    box = _nvl(fonts, theme)
    assert box.line_count == 0
    # No active line → treated as fully revealed (matches "nothing to type").
    assert box.fully_revealed() is True


def test_set_line_then_instant_reveal(fonts, theme):
    box = _nvl(fonts, theme, text_speed=0.0)
    box.set_line("愛麗絲", "你好。")
    # text_speed 0 → update completes the reveal immediately.
    box.update(0.016, None)
    assert box.fully_revealed() is True


def test_force_reveal_completes_active_line(fonts, theme):
    box = _nvl(fonts, theme, text_speed=20.0)   # slow typewriter
    box.set_line("愛麗絲", "這是一句比較長的測試台詞，需要時間顯示。")
    # A single tiny tick should not finish a slow line...
    box.update(0.01, None)
    assert box.fully_revealed() is False
    # ...but force_reveal does.
    box.force_reveal()
    assert box.fully_revealed() is True


# ---------------------------------------------------------------------------
# Accumulation
# ---------------------------------------------------------------------------

def test_lines_accumulate_into_transcript(fonts, theme):
    box = _nvl(fonts, theme)
    box.set_line("A", "line one")
    box.set_line("B", "line two")
    box.set_line(None, "line three")
    assert box.line_count == 3
    assert box.speakers() == ["A", "B", None]


def test_prior_lines_forced_revealed_when_new_line_added(fonts, theme):
    box = _nvl(fonts, theme, text_speed=20.0)
    box.set_line("A", "first line of dialogue")
    # The first line is still typing; adding a second snaps the first to full.
    box.set_line("B", "second line")
    assert box.entries[0].body.fully_revealed() is True
    # The newest (second) is the one now typing.
    assert box.entries[-1].speaker == "B"


# ---------------------------------------------------------------------------
# Scene-change reset
# ---------------------------------------------------------------------------

def test_reset_clears_transcript(fonts, theme):
    box = _nvl(fonts, theme)
    box.set_line("A", "one")
    box.set_line("B", "two")
    assert box.line_count == 2
    box.reset()
    assert box.line_count == 0
    assert box.speakers() == []
    assert box.fully_revealed() is True


def test_clear_alias_matches_reset(fonts, theme):
    box = _nvl(fonts, theme)
    box.set_line("A", "one")
    box.clear()
    assert box.line_count == 0


# ---------------------------------------------------------------------------
# draw() smoke
# ---------------------------------------------------------------------------

def test_draw_does_not_raise(fonts, theme):
    box = _nvl(fonts, theme)
    surf = pygame.Surface((1280, 720))
    box.set_line("A", "one")
    box.set_line("B", "two\nwith a newline")
    box.update(0.016, None)
    box.draw(surf)   # must not raise


def test_draw_many_lines_overflow_does_not_raise(fonts, theme):
    # More lines than the panel can hold → oldest scroll off; must not raise.
    box = _nvl(fonts, theme)
    surf = pygame.Surface((1280, 720))
    for i in range(40):
        box.set_line(f"S{i}", f"transcript line number {i} with some text")
    box.update(0.016, None)
    box.draw(surf)
    assert box.line_count == 40
