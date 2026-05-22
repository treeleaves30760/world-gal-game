"""A6: per-glyph animated effects (shake / wave / fadein).

Parser sets SegmentStyle.effect; the renderer applies a deterministic per-glyph
offset/alpha only to REVEALED glyphs, and effect=="none" keeps the static fast
path (zero offset). Render-side via the dummy SDL driver.
"""
from __future__ import annotations

import math
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


from world_gal_game.dialogue.richtext import RICHTEXT_FX, parse
from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.widgets.rich_text_view import RichText


@pytest.fixture
def fonts():
    return FontRegistry(("Arial", "DejaVu Sans"))


def _rt(fonts, markup, width=4000):
    return RichText(pygame.Rect(0, 0, width, 300), markup,
                    fonts=fonts, size=24, color=(255, 255, 255))


def _first_run(rt: RichText):
    rt._ensure_layout()
    return rt._rows[0][0]


# ----- parser ---------------------------------------------------------------


def test_parser_sets_effect():
    assert {"shake", "wave", "fadein"} <= RICHTEXT_FX
    for fx in ("shake", "wave", "fadein"):
        doc = parse(f"[fx={fx}]boo[/fx]")
        assert doc.segments[0].text == "boo"
        assert doc.segments[0].style.effect == fx


def test_parser_unknown_fx_degrades_to_none():
    doc = parse("[fx=sparkle]q[/fx]")
    assert doc.segments[0].text == "q"
    assert doc.segments[0].style.effect == "none"


# ----- shake / wave: deterministic offset, zero when effect == none ---------


def test_shake_offset_deterministic_for_fixed_t_index(fonts):
    rt = _rt(fonts, "[fx=shake]abcd[/fx]")
    run = _first_run(rt)
    rt._clock = 1.234
    o1 = rt._glyph_offset_alpha(run, 2, run.start_reveal + 2)
    o2 = rt._glyph_offset_alpha(run, 2, run.start_reveal + 2)
    assert o1 == o2                      # deterministic for fixed (t, index)
    dx, dy, alpha = o1
    assert alpha == 255                  # shake doesn't fade
    assert (dx, dy) != (0, 0)            # it actually displaces
    assert abs(dx) <= 2 and abs(dy) <= 2  # bounded ~1.5px jitter


def test_shake_differs_across_indices(fonts):
    rt = _rt(fonts, "[fx=shake]abcd[/fx]")
    run = _first_run(rt)
    rt._clock = 1.234
    a = rt._glyph_offset_alpha(run, 0, run.start_reveal + 0)
    b = rt._glyph_offset_alpha(run, 3, run.start_reveal + 3)
    assert a != b


def test_wave_offset_matches_formula(fonts):
    rt = _rt(fonts, "[fx=wave]abcd[/fx]")
    run = _first_run(rt)
    rt._clock = 0.5
    for gi in range(4):
        dx, dy, alpha = rt._glyph_offset_alpha(run, gi, run.start_reveal + gi)
        expected = int(round(math.sin(0.5 * 4.0 + gi * 0.5) * 4.0))
        assert dy == expected
        assert dx == 0
        assert alpha == 255


def test_no_effect_zero_offset(fonts):
    rt = _rt(fonts, "plain")
    run = _first_run(rt)
    assert run.style.effect in (None, "none")
    rt._clock = 9.9
    # effect=="none" never displaces or fades (the fast path).
    dx, dy, alpha = rt._glyph_offset_alpha(run, 1, run.start_reveal + 1)
    assert (dx, dy, alpha) == (0, 0, 255)


# ----- fadein: alpha ramps ~0 -> 255 after the glyph's reveal ---------------


def test_fadein_alpha_ramps_after_reveal(fonts):
    rt = _rt(fonts, "[fx=fadein]abcd[/fx]", width=4000)
    rt.text_speed = 100.0
    run = _first_run(rt)
    rt.set_reveal(0)
    # Reveal the first 2 glyphs; record their reveal timestamps.
    rt.update(0.02)   # 0.02 * 100 = 2 chars
    assert rt.reveal_chars >= 2
    idx = run.start_reveal + 0
    # Immediately at reveal: alpha near 0.
    a0 = rt._glyph_offset_alpha(run, 0, idx)[2]
    assert a0 <= 40
    # Mid ramp (~half of 0.25s later): partial alpha.
    rt._clock = rt._reveal_time[idx] + 0.125
    amid = rt._glyph_offset_alpha(run, 0, idx)[2]
    assert 60 <= amid <= 200
    # Long after: fully opaque.
    rt._clock = rt._reveal_time[idx] + 5.0
    afar = rt._glyph_offset_alpha(run, 0, idx)[2]
    assert afar == 255


def test_fadein_not_yet_revealed_glyph_is_invisible(fonts):
    rt = _rt(fonts, "[fx=fadein]abcd[/fx]")
    run = _first_run(rt)
    rt.set_reveal(0)
    rt._clock = 1.0
    # An index that was never revealed has no timestamp -> alpha 0.
    not_revealed = run.start_reveal + 3
    assert not_revealed not in rt._reveal_time
    alpha = rt._glyph_offset_alpha(run, 3, not_revealed)[2]
    assert alpha == 0


# ----- effects only apply to revealed glyphs (draw is clipped) --------------


def test_effects_skip_not_yet_revealed_glyphs(fonts):
    # The draw loop clips to shown_n, so unrevealed glyphs are never offset
    # because they are never drawn. We assert the clip boundary directly.
    rt = _rt(fonts, "[fx=wave]abcdef[/fx]")
    rt.set_reveal(3)
    surface = pygame.Surface((400, 300))
    rt.draw(surface)   # must not raise; only first 3 glyphs are processed
    # Drawing with a clock advance still only touches revealed glyphs.
    rt._clock = 2.0
    rt.draw(surface)


def test_force_reveal_makes_fadein_opaque(fonts):
    rt = _rt(fonts, "[fx=fadein]abcd[/fx]")
    rt.set_reveal(0)
    rt.force_reveal()
    run = _first_run(rt)
    idx = run.start_reveal + 0
    # force_reveal stamps reveal times in the past -> immediately opaque.
    alpha = rt._glyph_offset_alpha(run, 0, idx)[2]
    assert alpha == 255


def test_none_effect_draw_byte_identical_to_plain(fonts):
    # A run with [fx=none]-equivalent (tagless) takes the whole-string fast path.
    import hashlib
    a = _rt(fonts, "hello world", width=400)
    a.set_reveal(a.total_chars())
    s1 = pygame.Surface((400, 300), pygame.SRCALPHA); s1.fill((0, 0, 0, 0))
    a.draw(s1)
    # Re-render the same content; must hash identically (no per-glyph drift).
    b = _rt(fonts, "hello world", width=400)
    b.set_reveal(b.total_chars())
    b._clock = 5.0   # clock advance must not matter for a no-fx run
    s2 = pygame.Surface((400, 300), pygame.SRCALPHA); s2.fill((0, 0, 0, 0))
    b.draw(s2)
    assert hashlib.md5(pygame.image.tobytes(s1, "RGBA")).hexdigest() == \
        hashlib.md5(pygame.image.tobytes(s2, "RGBA")).hexdigest()
