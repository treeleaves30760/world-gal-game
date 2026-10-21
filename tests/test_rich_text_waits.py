"""A4: inline wait / per-span speed reveal timing.

Parser side ([w]/[w=N]/[s=N] -> controls + SegmentStyle.speed) is pygame-free;
the reveal-cursor side (RichText.update/force_reveal) needs pygame/font, so this
module follows the same dummy-driver pattern as test_rich_text_render.py.
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


from world_gal_game.dialogue.richtext import (
    MAX_INLINE_WAIT_SECONDS,
    lint_markup,
    parse,
    strip_markup,
)
from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.widgets.rich_text_view import RichText


@pytest.fixture
def fonts():
    return FontRegistry(("Arial", "DejaVu Sans"))


def _rt(fonts, markup, *, speed=10.0, width=4000):
    # Wide box so there are no implicit wrap breaks: reveal units == plain idx.
    rt = RichText(pygame.Rect(0, 0, width, 300), markup,
                  fonts=fonts, size=24, color=(255, 255, 255),
                  text_speed=speed)
    rt.set_reveal(0)
    return rt


# ----- parser: wait control char index --------------------------------------


def test_wait_parsed_at_correct_char_index():
    # "ab" then a wait, then "cd": the wait fires before plain index 2.
    doc = parse("ab[w]cd")
    assert doc.plain_text() == "abcd"
    assert doc.waits() == {2: pytest.approx(0.4)}


def test_wait_explicit_duration():
    doc = parse("hi[w=1.5]there")
    assert doc.waits() == {2: pytest.approx(1.5)}


def test_wait_at_start_is_index_zero():
    doc = parse("[w=0.5]go")
    assert doc.waits() == {0: pytest.approx(0.5)}


@pytest.mark.parametrize("raw", ["inf", "nan", "90000", "0", "-1", "bad"])
def test_invalid_wait_duration_degrades_to_bounded_default(raw):
    doc = parse(f"a[w={raw}]b")

    assert doc.waits() == {1: pytest.approx(0.4)}
    assert any("停頓秒數" in issue for issue in lint_markup(f"a[w={raw}]b"))


def test_same_position_waits_are_bounded_and_linted_when_accumulated():
    markup = "a[w=60000][b][/b][w=60000]b"

    assert parse(markup).waits() == {
        1: pytest.approx(MAX_INLINE_WAIT_SECONDS),
    }
    assert any("累積" in issue for issue in lint_markup(markup))


def test_strip_markup_removes_wait_and_speed():
    # [w] and [s=N] are control tokens, not visible text.
    assert strip_markup("a[w]b[s=30]c[s]d") == "abcd"
    assert strip_markup("[w=2]start[s=5]slow") == "startslow"


# ----- reveal: update() holds at a wait then resumes ------------------------


def test_update_holds_at_wait_then_resumes(fonts):
    # speed=10 chars/sec; wait of 0.5s sits before index 2.
    rt = _rt(fonts, "ab[w=0.5]cd", speed=10.0)
    assert rt.total_chars() == 4
    # 0.25s -> 2 chars revealed (10 c/s), now parked at the wait.
    rt.update(0.25)
    assert rt.reveal_chars == 2
    # Another 0.25s is consumed entirely by the wait: still 2.
    rt.update(0.25)
    assert rt.reveal_chars == 2
    # Still inside the 0.5s wait window (0.4s elapsed of the hold): held at 2.
    rt.update(0.15)
    assert rt.reveal_chars == 2
    # Now the wait (0.5s) is satisfied and reveal resumes past index 2.
    rt.update(0.2)
    assert rt.reveal_chars > 2
    # Plenty of time -> fully revealed.
    rt.update(5.0)
    assert rt.reveal_chars == 4
    assert rt.fully_revealed()


def test_no_wait_reveals_at_constant_rate(fonts):
    rt = _rt(fonts, "abcdef", speed=10.0)
    rt.update(0.3)   # 0.3 * 10 = 3
    assert rt.reveal_chars == 3
    rt.update(0.3)
    assert rt.reveal_chars == 6
    assert rt.fully_revealed()


# ----- per-span [s=N] changes reveal rate -----------------------------------


def test_per_span_speed_changes_rate(fonts):
    # First span is the base 10 c/s; the [s=2] span crawls at 2 c/s.
    # Layout: "aa" (base) then "bbbb" (slow).
    rt = _rt(fonts, "aa[s=2]bbbb[/s]", speed=10.0)
    assert rt.total_chars() == 6
    # 0.2s at 10 c/s reveals the 2 base chars exactly.
    rt.update(0.2)
    assert rt.reveal_chars == 2
    # Now in the slow span: 0.2s at 2 c/s reveals < 1 more char -> still ~2.
    rt.update(0.2)
    assert rt.reveal_chars == 2
    # 0.5s at 2 c/s -> 1 char of the slow span.
    rt.update(0.5)
    assert rt.reveal_chars == 3
    # Compare against an all-base run over the same time budget: the slow span
    # is demonstrably behind.
    fast = _rt(fonts, "aabbbb", speed=10.0)
    fast.update(0.9)
    assert fast.reveal_chars > rt.reveal_chars


def test_span_speed_none_inherits_base(fonts):
    # [s] with no value resets to inherited base speed.
    rt = _rt(fonts, "aa[s=2]bb[s]cc", speed=10.0)
    # The trailing "cc" inherits base 10 c/s again. Reveal everything quickly.
    rt.update(2.0)
    assert rt.reveal_chars == 6
    assert rt.fully_revealed()


# ----- force_reveal clears waits + reveals all ------------------------------


def test_force_reveal_clears_waits_and_reveals_all(fonts):
    rt = _rt(fonts, "ab[w=10]cd", speed=10.0)
    rt.update(0.25)
    assert rt.reveal_chars == 2   # parked at the long wait
    rt.force_reveal()
    assert rt.reveal_chars == rt.total_chars() == 4
    assert rt.fully_revealed()
    # A subsequent update must NOT re-park on the (now consumed) wait.
    rt.update(0.1)
    assert rt.reveal_chars == 4
    assert rt.fully_revealed()


def test_set_markup_resets_wait_state(fonts):
    rt = _rt(fonts, "ab[w=10]cd", speed=10.0)
    rt.update(0.25)
    rt.force_reveal()
    # Loading a new line clears the consumed-wait bookkeeping.
    rt.set_markup("xy[w=0.5]z", reveal_chars=0)
    rt.update(0.25)   # 2 chars then park at the fresh wait
    assert rt.reveal_chars == 2
    rt.update(0.05)   # still inside the new wait
    assert rt.reveal_chars == 2


def test_active_wait_timeline_restores_only_remaining_duration(fonts):
    original = _rt(fonts, "ab[w=0.5]cd", speed=10.0)
    original.update(0.25)  # 0.20s to the wait, then consume 0.05s.
    timeline = original.export_wait_timeline()

    assert original.reveal_chars == 2
    assert timeline == {
        "consumed_plain_positions": [2],
        "active": {
            "plain_position": 2,
            "remaining_seconds": pytest.approx(0.45),
        },
    }

    restored = _rt(fonts, "ab[w=0.5]cd", speed=10.0)
    restored.restore_reveal(2, wait_timeline=timeline)

    assert restored._wait_left == pytest.approx(0.45)
    restored.update(0.44)
    assert restored.reveal_chars == 2
    assert restored._wait_left == pytest.approx(0.01)
    restored.update(0.02)
    assert restored._wait_left == 0.0
    restored.update(0.1)
    assert restored.reveal_chars == 3


def test_completed_wait_at_same_cursor_does_not_retrigger_after_restore(fonts):
    original = _rt(fonts, "ab[w=0.5]cd", speed=10.0)
    original.update(0.25)
    original.update(0.45)
    timeline = original.export_wait_timeline()

    assert original.reveal_chars == 2
    assert timeline == {
        "consumed_plain_positions": [2],
        "active": None,
    }

    restored = _rt(fonts, "ab[w=0.5]cd", speed=10.0)
    restored.restore_reveal(2, wait_timeline=timeline)
    restored.update(0.1)

    assert restored._wait_left == 0.0
    assert restored.reveal_chars == 3


def test_active_wait_timeline_survives_resolution_reflow(fonts):
    plain = "甲乙丙丁" * 40
    wait_plain = 80
    markup = plain[:wait_plain] + "[w=2]" + plain[wait_plain:]
    narrow = _rt(fonts, markup, speed=20.0, width=180)
    narrow_cursor = narrow.reveal_cursor_for_plain(wait_plain)
    narrow.set_reveal(narrow_cursor)
    narrow.update(0.25)
    timeline = narrow.export_wait_timeline()

    wide = _rt(fonts, markup, speed=20.0, width=620)
    wide_cursor = wide.reveal_cursor_for_plain(wait_plain)
    assert wide_cursor != narrow_cursor
    wide.restore_reveal(wide_cursor, wait_timeline=timeline)

    assert wide.plain_cursor_for_reveal() == wait_plain
    assert wide._wait_left == pytest.approx(1.75)


def test_terminal_wait_is_not_a_resumable_pause_without_a_following_glyph(fonts):
    original = _rt(fonts, "甲[w=2]", speed=10.0)
    original.update(0.1)

    assert original.fully_revealed() is True
    assert original.export_wait_timeline() == {
        "consumed_plain_positions": [],
        "active": None,
    }

    restored = _rt(fonts, "甲[w=2]", speed=10.0)
    restored.restore_reveal(
        original.reveal_chars,
        wait_timeline=original.export_wait_timeline(),
    )
    assert restored.fully_revealed() is True
