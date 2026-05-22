"""A5: ruby / furigana layout.

Parser puts the reading on SegmentStyle.ruby; the renderer reserves a top band
so a ruby row is taller, and the reading is NOT counted in the typewriter
reveal. Render-side checks use the dummy SDL driver like test_rich_text_render.
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


from world_gal_game.dialogue.richtext import parse, strip_markup
from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.widgets.rich_text_view import RichText


@pytest.fixture
def fonts():
    return FontRegistry(("Arial", "DejaVu Sans"))


def _rt(fonts, markup, width=4000):
    return RichText(pygame.Rect(0, 0, width, 400), markup,
                    fonts=fonts, size=24, color=(255, 255, 255))


# ----- parser ---------------------------------------------------------------


def test_ruby_reading_on_style_base_text_is_inner():
    doc = parse("[ruby=かんじ]漢字[/ruby]")
    # Exactly one segment: base text is the inner text, reading on the style.
    base = [s for s in doc.segments if s.text]
    assert len(base) == 1
    seg = base[0]
    assert seg.text == "漢字"
    assert seg.style.ruby == "かんじ"


def test_ruby_base_counted_reading_not_in_plain_text():
    doc = parse("[ruby=reading]base[/ruby]tail")
    # Plain text is base + tail; the reading never appears.
    assert doc.plain_text() == "basetail"
    assert "reading" not in doc.plain_text()


def test_strip_markup_drops_ruby_tag_keeps_base():
    assert strip_markup("a[ruby=yomi]字[/ruby]b") == "a字b"


def test_empty_ruby_reading_is_none():
    doc = parse("[ruby=]字[/ruby]")
    assert doc.segments[0].text == "字"
    assert doc.segments[0].style.ruby is None


# ----- reveal count: ruby reading is not counted ----------------------------


def test_reveal_count_equals_base_plain_length(fonts):
    rt = _rt(fonts, "[ruby=かんじ]漢字[/ruby]")
    # Two base glyphs; the 3-char reading does not inflate the count.
    assert rt.total_chars() == 2
    assert rt.plain_text() == "漢字"


def test_reveal_count_with_surrounding_text(fonts):
    rt = _rt(fonts, "x[ruby=longreading]字[/ruby]y")
    assert rt.total_chars() == len("x字y")


# ----- layout: a ruby row reserves extra height -----------------------------


def _rendered_height(rt: RichText) -> int:
    rt._ensure_layout()
    default_h = rt.fonts.get(rt.base_size).get_linesize()
    y = 0
    for ri, row in enumerate(rt._rows):
        band = rt._row_bands[ri] if ri < len(rt._row_bands) else 0
        row_h = max((r.height for r in row), default=default_h)
        y += band + row_h + rt.line_spacing
    return y


def test_ruby_row_reserves_extra_height(fonts):
    plain = _rt(fonts, "漢字")
    ruby = _rt(fonts, "[ruby=かんじ]漢字[/ruby]")
    assert _rendered_height(ruby) > _rendered_height(plain)
    # And concretely: the plain row reserves no band, the ruby row does.
    plain._ensure_layout()
    ruby._ensure_layout()
    assert plain._row_bands[0] == 0
    assert ruby._row_bands[0] > 0


def test_ruby_draw_does_not_raise_at_partial_reveal(fonts):
    surface = pygame.Surface((400, 400))
    rt = _rt(fonts, "[ruby=かんじ]漢字[/ruby]です", width=400)
    for n in range(0, rt.total_chars() + 2):
        rt.set_reveal(n)
        rt.draw(surface)   # must not raise at any reveal count
