"""Rich-text parser: nesting, escaping, degradation, color/size attrs.

Pygame-free — exercises world_gal_game.dialogue.richtext directly.
"""
from __future__ import annotations

from world_gal_game.dialogue.richtext import (
    RICHTEXT_TAGS, parse, strip_markup, SegmentStyle,
)


def _styles(doc):
    return [(s.text, s.style) for s in doc.segments]


# ----- tag set --------------------------------------------------------------


def test_richtext_tags_a3_set():
    assert {"b", "color", "size"} <= RICHTEXT_TAGS


# ----- plain / stripping ----------------------------------------------------


def test_plain_text_no_tags():
    doc = parse("hello world")
    assert doc.plain_text() == "hello world"
    assert len(doc.segments) == 1
    assert doc.segments[0].style == SegmentStyle()


def test_strip_markup_equals_plain():
    markup = "a [b]bold[/b] and [color=#f00]red[/color] tail"
    assert strip_markup(markup) == "a bold and red tail"
    assert strip_markup(markup) == parse(markup).plain_text()


def test_total_chars_equals_plain_len():
    markup = "[b]abc[/b]def"
    doc = parse(markup)
    assert doc.total_chars() == len("abcdef")


# ----- bold -----------------------------------------------------------------


def test_bold_segment():
    doc = parse("x[b]Y[/b]z")
    assert doc.plain_text() == "xYz"
    # The middle segment is bold; the outer ones are not.
    bolds = [(s.text, s.style.bold) for s in doc.segments]
    assert ("Y", True) in bolds
    assert all(not s.style.bold for s in doc.segments if s.text in ("x", "z"))


# ----- color ----------------------------------------------------------------


def test_color_hex_6():
    doc = parse("[color=#ff0000]red[/color]")
    seg = doc.segments[0]
    assert seg.text == "red"
    assert seg.style.color == (255, 0, 0)


def test_color_hex_3_expands():
    doc = parse("[color=#0f0]g[/color]")
    assert doc.segments[0].style.color == (0, 255, 0)


def test_color_named():
    doc = parse("[color=blue]b[/color]")
    assert doc.segments[0].style.color == (80, 130, 230)


def test_color_malformed_ignored_literal_text_kept():
    # Bad hex -> the color attr is ignored (no color), but text is preserved.
    doc = parse("[color=#zz]q[/color]")
    assert doc.plain_text() == "q"
    assert doc.segments[0].style.color is None


# ----- size -----------------------------------------------------------------


def test_size_absolute():
    doc = parse("[size=40]big[/size]")
    seg = doc.segments[0]
    assert seg.style.size_abs == 40
    assert seg.style.resolved_size(24) == 40


def test_size_relative_delta():
    doc = parse("[size=+8]bigger[/size]")
    seg = doc.segments[0]
    assert seg.style.size_delta == 8
    assert seg.style.resolved_size(24) == 32


def test_size_relative_negative():
    doc = parse("[size=-6]small[/size]")
    assert doc.segments[0].style.resolved_size(24) == 18


def test_size_malformed_ignored():
    doc = parse("[size=abc]q[/size]")
    assert doc.plain_text() == "q"
    assert doc.segments[0].style.size_abs is None
    assert doc.segments[0].style.size_delta == 0


def test_size_clamped():
    # Absurd size clamps into the safe range rather than blowing up layout.
    assert parse("[size=9999]x[/size]").segments[0].style.resolved_size(24) <= 200
    assert parse("[size=1]x[/size]").segments[0].style.resolved_size(24) >= 6


# ----- nesting --------------------------------------------------------------


def test_nesting_combines_styles():
    doc = parse("[b][color=#ff0000]hot[/color][/b]")
    seg = doc.segments[0]
    assert seg.text == "hot"
    assert seg.style.bold is True
    assert seg.style.color == (255, 0, 0)


def test_inner_close_restores_outer_style():
    doc = parse("[b]A[color=#0000ff]B[/color]C[/b]")
    by_text = {s.text: s.style for s in doc.segments}
    assert by_text["A"].bold and by_text["A"].color is None
    assert by_text["B"].bold and by_text["B"].color == (0, 0, 255)
    assert by_text["C"].bold and by_text["C"].color is None


# ----- degradation: unknown tags, escapes, malformed close ------------------


def test_unknown_tag_is_literal():
    doc = parse("a [wobble]b[/wobble] c")
    # Unknown tag stays verbatim in the text.
    assert doc.plain_text() == "a [wobble]b[/wobble] c"


def test_double_bracket_op_is_literal():
    # [[op:arg]] belongs to dialogue ops; never treated as markup.
    doc = parse("hp [[hp:show]] now")
    assert doc.plain_text() == "hp [[hp:show]] now"


def test_escaped_bracket_is_literal():
    doc = parse(r"price is \[50]")
    assert doc.plain_text() == "price is [50]"


def test_unclosed_tag_auto_closes_at_end():
    doc = parse("[b]bold forever")
    assert doc.plain_text() == "bold forever"
    assert doc.segments[-1].style.bold is True


def test_mismatched_close_is_ignored():
    # A stray close with no open just disappears; text is intact.
    doc = parse("hello[/b] world")
    assert doc.plain_text() == "hello world"
    assert all(not s.style.bold for s in doc.segments)


def test_close_wrong_order_pops_nearest_match():
    # [b][color]..[/b] : closing b pops both b and the color above it.
    doc = parse("[b]A[color=#f00]B[/b]C")
    by_text = {s.text: s.style for s in doc.segments}
    assert by_text["A"].bold
    assert by_text["B"].bold and by_text["B"].color == (255, 0, 0)
    # After [/b] both styles are gone.
    assert not by_text["C"].bold and by_text["C"].color is None


def test_lone_bracket_is_literal():
    doc = parse("array[0] = x")
    assert doc.plain_text() == "array[0] = x"
