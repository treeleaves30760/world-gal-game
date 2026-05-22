"""RichText widget: reveal contract, wrapping, per-segment size affects rows."""
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
from world_gal_game.ui.widgets.rich_text_view import RichText


@pytest.fixture
def fonts():
    # No bundled font; FontRegistry falls back to a system/default font.
    return FontRegistry(("Arial", "DejaVu Sans"))


def _rt(fonts, markup, width=400):
    return RichText(pygame.Rect(0, 0, width, 300), markup,
                    fonts=fonts, size=24, color=(255, 255, 255))


def test_total_chars_matches_plain_len_no_wrap(fonts):
    rt = _rt(fonts, "[b]abc[/b]def", width=4000)   # wide enough: no wrap
    assert rt.total_chars() == len("abcdef")
    assert rt.plain_text() == "abcdef"


def test_reveal_monotonic_across_segments(fonts):
    rt = _rt(fonts, "[b]ab[/b][color=#f00]cd[/color]", width=4000)
    total = rt.total_chars()
    assert total == 4
    # fully_revealed flips only once reveal reaches total.
    rt.set_reveal(0)
    assert not rt.fully_revealed()
    rt.set_reveal(2)
    assert not rt.fully_revealed()
    rt.set_reveal(total)
    assert rt.fully_revealed()
    rt.set_reveal(total + 1)
    assert rt.fully_revealed()


def test_reveal_none_is_fully_revealed(fonts):
    rt = _rt(fonts, "abc")
    rt.set_reveal(None)
    assert rt.fully_revealed()


def test_draw_partial_reveal_does_not_raise(fonts):
    surface = pygame.Surface((400, 300))
    rt = _rt(fonts, "[color=#00ff00]hello[/color] world")
    for n in range(0, rt.total_chars() + 2):
        rt.set_reveal(n)
        rt.draw(surface)   # must not raise at any reveal count


def test_size_span_increases_row_height(fonts):
    # A row with a [size=+N] glyph must be taller than the plain baseline row.
    plain = _rt(fonts, "abc", width=4000)
    big = _rt(fonts, "a[size=+30]B[/size]c", width=4000)
    plain._ensure_layout()
    big._ensure_layout()

    def row_height(rt):
        row = rt._rows[0]
        return max(run.height for run in row)

    assert row_height(big) > row_height(plain)


def test_wrapping_creates_multiple_rows(fonts):
    # A long string in a narrow box wraps into more than one row.
    rt = _rt(fonts, "abcdefghij klmnopqrst uvwxyz", width=40)
    rt._ensure_layout()
    assert len(rt._rows) > 1
    # total_chars accounts for the implicit row breaks (glyphs + rows-1).
    glyphs = sum(len(run.text) for row in rt._rows for run in row)
    assert rt.total_chars() == glyphs + (len(rt._rows) - 1)


def test_set_markup_reparses(fonts):
    rt = _rt(fonts, "first")
    assert rt.plain_text() == "first"
    rt.set_markup("[b]second[/b]")
    assert rt.plain_text() == "second"


def test_tagless_string_single_segment(fonts):
    # Backward-compat: a tagless string is one default segment, like WrappedText.
    rt = _rt(fonts, "just text", width=4000)
    assert rt.plain_text() == "just text"
    assert rt._doc.segments[0].style.bold is False
    assert rt._doc.segments[0].style.color is None
