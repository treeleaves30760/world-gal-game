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


from world_gal_game.ui.fonts import FontRegistry  # noqa: E402
from world_gal_game.ui.widgets.rich_text_view import RichText  # noqa: E402


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


def test_latin_wrap_prefers_whitespace_without_losing_cursor_positions(fonts):
    font = fonts.get(24)
    prefix = "A long "
    word = "volunteers"
    rt = _rt(fonts, prefix + word, width=font.size(word)[0])
    rt._ensure_layout()

    rows = ["".join(run.text for run in row) for row in rt._rows]
    assert rows == [prefix, word]
    assert "".join(rows) == prefix + word

    boundary = len(prefix)
    reveal = rt.reveal_cursor_for_plain(boundary)
    assert rt.plain_cursor_for_reveal(reveal) == boundary


def test_unspaced_text_keeps_glyph_wrap_fallback(fonts):
    rt = _rt(fonts, "abcdefghij", width=fonts.get(24).size("abc")[0])
    rt._ensure_layout()

    rows = ["".join(run.text for run in row) for row in rt._rows]
    assert len(rows) > 1
    assert "".join(rows) == "abcdefghij"


def test_word_wrap_can_move_a_word_across_style_and_wait_boundaries(fonts):
    bold = fonts.get(24, bold=True)
    regular = fonts.get(24)
    word_width = bold.size("volun")[0] + regular.size("teers")[0]
    markup = "Say [b]volun[/b][w=0.1]teers"
    rt = _rt(fonts, markup, width=word_width)
    rt._ensure_layout()

    rows = ["".join(run.text for run in row) for row in rt._rows]
    assert rows == ["Say ", "volunteers"]
    assert [run.text for run in rt._rows[1]] == ["volun", "teers"]

    wait_plain = len("Say volun")
    wait_reveal = rt.reveal_cursor_for_plain(wait_plain)
    assert rt.has_wait_at_plain(wait_plain)
    assert rt.plain_cursor_for_reveal(wait_reveal) == wait_plain


def test_cjk_wrap_does_not_strand_closing_punctuation(fonts):
    font = fonts.get(24)
    rt = _rt(fonts, "甲乙丙。丁", width=font.size("甲乙丙")[0])
    rt._ensure_layout()

    rows = ["".join(run.text for run in row) for row in rt._rows]
    assert "".join(rows) == "甲乙丙。丁"
    assert all(not row.startswith("。") for row in rows)


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
