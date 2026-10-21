"""Measured ADV layout, lossless row pagination, and resume contracts."""
from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.input import InputState
from world_gal_game.ui.theme import default_theme
from world_gal_game.ui.widgets.dialogue_box import DialogueBox
from world_gal_game.ui.widgets.rich_text_view import RichText


@pytest.fixture(autouse=True)
def _pygame_init():
    pygame.display.init()
    pygame.font.init()
    pygame.display.set_mode((10, 10))
    yield
    pygame.quit()


def _box(
    *,
    width: int = 420,
    body_lines: int = 2,
    text_speed: float = 1000.0,
) -> DialogueBox:
    fonts = FontRegistry(("Arial",))
    theme = default_theme()
    metrics = DialogueBox.measure(
        fonts,
        theme,
        body_lines=body_lines,
    )
    return DialogueBox(
        pygame.Rect(20, 40, width, metrics.preferred_height),
        fonts=fonts,
        theme=theme,
        text_speed=text_speed,
        metrics=metrics,
    )


def _open_scene(driver, scene):
    driver.app.state.story.add_scene(scene)
    # These synthetic scenes stand in for pack/plugin-authored content.  The
    # load security boundary intentionally refuses to serialize resume data
    # for a scene that exists only in the mutable live/save StoryGraph, so
    # register the same definition in the driver's trusted runtime catalogue.
    driver.app._authored_story_catalog.add_scene(
        scene.model_copy(deep=True),
    )
    driver.app.state.meta["__pack_story_catalog__"] = (
        driver.app._authored_story_catalog
    )
    driver.app._start_dialogue(scene.id)
    driver.app.manager.commit_pending()
    driver.advance_frames(1)
    dialogue = driver.app.manager.current
    assert type(dialogue).__name__ == "DialogueScene"
    return dialogue


@pytest.mark.parametrize(
    ("size", "text_scale", "expected_lines"),
    [
        ((1280, 720), 1.0, 3),
        ((1920, 1080), 1.0, 3),
        ((3840, 2160), 1.0, 3),
        ((1280, 720), 1.4, 3),
        ((1920, 1080), 1.4, 3),
        ((3840, 2160), 1.4, 3),
        ((800, 450), 1.4, 2),
    ],
)
def test_adv_reading_geometry_tracks_live_density(
    size: tuple[int, int],
    text_scale: float,
    expected_lines: int,
) -> None:
    from world_gal_game.dev.driver import GameDriver

    driver = GameDriver(pack="demo_pack", screen_size=size)
    try:
        driver.cfg.text_scale = text_scale
        driver.new_game()
        scene = driver.app.manager.current
        box = scene.box
        metrics = box.metrics
        canvas = pygame.Rect((0, 0), size)

        assert metrics.body_lines == expected_lines
        assert box.rect.height == metrics.preferred_height
        assert box.body.rect.height == metrics.body_height
        assert box.body.rect.bottom < box.footer_rect.top
        assert box.rect.contains(box.body.rect)
        assert box.rect.contains(box.footer_rect)
        assert metrics.nameplate_height >= driver.app.fonts.get(
            metrics.speaker_font_size,
            bold=True,
        ).get_linesize()

        bar = scene._quick_bar
        assert canvas.contains(bar.rect)
        assert canvas.contains(bar._overflow_rect)
        assert bar.rect.bottom <= (
            box.rect.top - metrics.nameplate_height // 2
        )
        for button, _active in bar._entries + bar._overflow_entries:
            font = driver.app.fonts.get(button.font_size, bold=True)
            assert button.rect.height >= font.get_linesize()
            assert button.rect.width >= font.size(button.display_label)[0]
        for button, _active in bar._entries:
            assert button.text_overflow is False
    finally:
        driver.quit()


def test_standard_box_height_ratio_is_stable_through_4k() -> None:
    from world_gal_game.dev.driver import GameDriver

    ratios: list[float] = []
    for size in ((1280, 720), (1920, 1080), (3840, 2160)):
        driver = GameDriver(pack="demo_pack", screen_size=size)
        try:
            driver.new_game()
            ratios.append(
                driver.app.manager.current.box.rect.height / size[1],
            )
        finally:
            driver.quit()

    assert max(ratios) - min(ratios) < 0.015
    assert all(0.20 <= ratio <= 0.30 for ratio in ratios)


def test_long_authored_line_pages_without_changing_global_text() -> None:
    box = _box()
    text = "甲乙丙丁" * 80
    box.set_line("測試者", text)

    assert box.page_count >= 3
    assert box.body.plain_text() == text
    assert box.body.total_chars() >= len(text)
    assert all(
        page.height <= box.body.rect.height
        for page in box._pages
    )

    first = box.current_page
    box.force_reveal()
    assert box.body.reveal_chars == first.end_reveal
    assert box.advance_page() is True
    assert box.page_index == 1
    assert box.body.reveal_chars == box.current_page.start_reveal
    assert box.body.plain_text() == text


def test_three_line_yaml_literal_has_no_empty_terminal_page() -> None:
    box = _box(width=800, body_lines=3, text_speed=0)
    text = "第一行\n第二行\n第三行\n"

    box.set_line("測試者", text)
    box.update(0.0, InputState())

    assert box.body.plain_text() == text
    assert box.body.total_chars() == len(text)
    assert box.body.plain_cursor_for_reveal(box.body.total_chars()) == len(text)
    assert box.page_count == 1
    assert box.current_page.start_row == 0
    assert box.current_page.end_row == 3
    assert box.current_page.end_reveal == box.body.total_chars()
    assert box.has_more_pages() is False
    assert box.fully_revealed() is True


@pytest.mark.parametrize(
    ("size", "text_scale", "expected_pages"),
    [
        pytest.param((1280, 720), 1.0, 1, id="720p-standard"),
        pytest.param((1920, 1080), 1.4, 1, id="1080p-large-text"),
        pytest.param((800, 450), 1.4, 2, id="450p-large-text"),
    ],
)
def test_three_line_literal_pages_are_never_visually_empty(
    size: tuple[int, int],
    text_scale: float,
    expected_pages: int,
) -> None:
    from world_gal_game.core.story_graph import Line, Scene
    from world_gal_game.dev.driver import GameDriver

    driver = GameDriver(pack="demo_pack", screen_size=size)
    try:
        driver.cfg.text_scale = text_scale
        dialogue = _open_scene(
            driver,
            Scene(
                id=f"three_line_layout_{size[0]}_{size[1]}",
                lines=[Line(text="第一行\n第二行\n第三行\n")],
            ),
        )
        box = dialogue.box

        assert box.page_count == expected_pages
        assert box._pages[-1].end_reveal == box.body.total_chars()
        assert all(
            any(
                box.body._row_has_visible_glyph(row)
                for row in box.body._rows[page.start_row:page.end_row]
            )
            for page in box._pages
        )
    finally:
        driver.quit()


def test_trailing_empty_row_trim_keeps_an_internal_blank_line() -> None:
    box = _box(width=800, body_lines=3, text_speed=0)
    text = "第一行\n\n第三行\n"

    box.set_line("測試者", text)

    assert box.body.plain_text() == text
    assert box.page_count == 1
    assert box.current_page.start_row == 0
    assert box.current_page.end_row == 3
    assert box.body._rows[1] == []
    assert box.body._row_window == (0, 3)


@pytest.mark.parametrize(
    "tail",
    [
        pytest.param(" ", id="space"),
        pytest.param("\t", id="tab"),
        pytest.param(" \t\n\t ", id="mixed-terminal-whitespace"),
        pytest.param("\u00a0", id="non-breaking-space"),
        pytest.param("\u3000", id="ideographic-space"),
        pytest.param("\r", id="carriage-return"),
    ],
)
def test_terminal_whitespace_rows_never_create_visual_pages(
    tail: str,
) -> None:
    box = _box(width=800, body_lines=3, text_speed=0)
    text = f"第一行\n第二行\n第三行\n{tail}"

    box.set_line("測試者", text)
    box.update(0.0, InputState())

    assert box.body.plain_text() == text
    assert box.body.total_chars() == len(text)
    assert box.body.plain_cursor_for_reveal(box.body.total_chars()) == len(text)
    assert box.body.reveal_cursor_for_plain(len(text)) == box.body.total_chars()
    assert box.page_count == 1
    assert box.current_page.end_row == 3
    assert box.current_page.end_reveal == box.body.total_chars()
    assert box.has_more_pages() is False


def test_internal_whitespace_row_remains_in_visual_geometry() -> None:
    box = _box(width=800, body_lines=3, text_speed=0)
    text = "第一行\n \t\n第三行\n"

    box.set_line("測試者", text)

    assert box.page_count == 1
    assert box.current_page.end_row == 3
    assert [run.text for run in box.body._rows[1]] == [" \t"]
    assert box.body._row_window == (0, 3)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("", id="empty"),
        pytest.param("\n", id="single-lf-only"),
        pytest.param("\n\n\n", id="multiple-lf-only"),
        pytest.param(
            "唯一可見行\n\n\n",
            id="text-with-multiple-terminal-lf",
        ),
    ],
)
def test_terminal_empty_rows_never_create_visual_pages(text: str) -> None:
    # A one-line viewport makes every untrimmed empty row become another page.
    box = _box(width=800, body_lines=1, text_speed=0)

    box.set_line("測試者", text)
    box.update(0.0, InputState())

    assert box.body.plain_text() == text
    assert box.body.total_chars() == len(text)
    assert box.body.plain_cursor_for_reveal(box.body.total_chars()) == len(text)
    assert box.body.reveal_cursor_for_plain(len(text)) == box.body.total_chars()
    assert box.page_count == 1
    assert box.has_more_pages() is False
    assert box.current_page.start_row == 0
    assert box.current_page.end_row == 1
    assert box.current_page.start_reveal == 0
    assert box.current_page.end_reveal == box.body.total_chars()
    assert box.body._row_window == (0, 1)
    assert box.fully_revealed() is True


def test_multi_page_terminal_lfs_keep_cursor_and_restore_after_reflow() -> None:
    text = ("甲乙丙丁" * 80) + "\n\n\n"
    narrow = _box(width=220, body_lines=2)
    narrow.set_line("測試者", text)

    assert narrow.page_count > 1
    assert narrow.current_page.start_reveal == 0
    assert all(
        left.end_row == right.start_row and left.end_reveal == right.start_reveal
        for left, right in zip(narrow._pages, narrow._pages[1:])
    )
    last = narrow._pages[-1]
    assert last.end_reveal == narrow.body.total_chars()
    assert last.end_row < len(narrow.body._rows)
    assert all(not row for row in narrow.body._rows[last.end_row :])

    assert narrow.reveal_last_page() is True
    raw_cursor = narrow.body.total_chars() - 1
    narrow.body.set_reveal(raw_cursor)
    plain_cursor = narrow.body.plain_cursor_for_reveal()
    assert plain_cursor == len(text) - 1

    wide = _box(width=620, body_lines=2)
    wide.set_line("測試者", text)
    reflowed_cursor = wide.body.reveal_cursor_for_plain(plain_cursor)
    assert reflowed_cursor != raw_cursor

    wide.restore_page(narrow.page_index, raw_cursor, plain_cursor)

    assert wide.page_index == wide.page_count - 1
    assert wide.current_page.end_reveal == wide.body.total_chars()
    assert wide.body.reveal_chars == reflowed_cursor
    assert wide.body.plain_cursor_for_reveal() == plain_cursor


def test_wait_in_trimmed_terminal_rows_keeps_authored_timing() -> None:
    box = _box(width=800, body_lines=1, text_speed=20)
    markup = "可見內容\n[w=5]\n\n"
    box.set_line("測試者", markup)
    wait_plain = next(iter(box.body._doc.waits()))
    wait_reveal = box.body.reveal_cursor_for_plain(wait_plain)

    assert box.page_count == 1
    assert box.current_page.end_row == 1
    assert box.current_page.end_reveal == box.body.total_chars()
    assert all(not row for row in box.body._rows[1:])
    assert box.body.plain_cursor_for_reveal(wait_reveal) == wait_plain
    assert wait_reveal < box.current_page.end_reveal

    box.body.set_reveal(wait_reveal)
    box.update(0.01, InputState())

    assert box.body.reveal_chars == wait_reveal
    assert wait_reveal in box.body._waits_done
    assert box.body._wait_left > 4.0


def test_page_ceiling_does_not_consume_future_wait_or_fade_clock() -> None:
    box = _box()
    box.set_line(
        "測試者",
        ("甲" * 100) + "[w=5][fx=fadein]乙[/fx]" + ("丙" * 100),
    )
    wait_plain = next(iter(box.body._doc.waits()))
    wait_reveal = box.body.reveal_cursor_for_plain(wait_plain)
    assert wait_reveal > box.current_page.end_reveal

    box.update(10.0, InputState())

    assert box.body.reveal_chars == box.current_page.end_reveal
    assert wait_reveal not in box.body._waits_done
    assert wait_reveal not in box.body._reveal_time


def test_page_boundary_wait_survives_restore_and_parks_after_flip() -> None:
    base = "甲" * 180
    box = _box()
    box.set_line("測試者", base)
    boundary_plain = box.body.plain_cursor_for_reveal(
        box.current_page.end_reveal,
    )
    markup = (
        base[:boundary_plain]
        + "[w=5]"
        + base[boundary_plain:]
    )
    box.set_line("測試者", markup)
    wait_reveal = box.body.reveal_cursor_for_plain(boundary_plain)
    assert wait_reveal == box.current_page.end_reveal
    box.force_reveal()

    restored = _box()
    restored.set_line("測試者", markup)
    restored.restore_page(
        box.page_index,
        box.body.reveal_chars,
        box.body.plain_cursor_for_reveal(),
    )
    assert wait_reveal not in restored.body._waits_done
    assert restored.advance_page() is True

    restored.update(0.01, InputState())

    assert restored.body.reveal_chars == wait_reveal
    assert restored.body._wait_left > 4.0


def test_active_page_boundary_wait_restores_on_the_same_visual_page() -> None:
    base = "甲" * 180
    original = _box()
    original.set_line("測試者", base)
    boundary_plain = original.body.plain_cursor_for_reveal(
        original.current_page.end_reveal,
    )
    markup = (
        base[:boundary_plain]
        + "[w=5]"
        + base[boundary_plain:]
    )
    original.set_line("測試者", markup)
    original.force_reveal()
    assert original.advance_page() is True
    original.update(0.25, InputState())
    wait_reveal = original.body.reveal_cursor_for_plain(boundary_plain)
    timeline = original.body.export_wait_timeline()

    assert original.page_index == 1
    assert original.body.reveal_chars == wait_reveal
    assert original.body._wait_left == pytest.approx(4.75)

    restored = _box()
    restored.set_line("測試者", markup)
    restored.restore_page(
        original.page_index,
        original.body.reveal_chars,
        original.body.plain_cursor_for_reveal(),
        wait_timeline=timeline,
    )

    assert restored.page_index == 1
    assert restored.body.reveal_chars == wait_reveal
    assert restored.body._wait_left == pytest.approx(4.75)


def test_active_wait_reflow_uses_downstream_side_of_new_page_boundary() -> None:
    plain = "甲乙丙丁" * 200
    wait_plain = 36
    markup = plain[:wait_plain] + "[w=2]" + plain[wait_plain:]
    original = _box(width=220, body_lines=2, text_speed=20)
    original.set_line("測試者", markup)
    original.force_reveal()
    assert original.advance_page() is True
    original.force_reveal()
    assert original.advance_page() is True
    original.update(0.25, InputState())
    timeline = original.body.export_wait_timeline()

    assert original.page_index == 2
    assert original.body.reveal_chars == 40
    assert original.body._wait_left == pytest.approx(1.75)

    restored = _box(width=180, body_lines=2, text_speed=20)
    restored.set_line("測試者", markup)
    restored.restore_page(
        original.page_index,
        original.body.reveal_chars,
        original.body.plain_cursor_for_reveal(),
        wait_timeline=timeline,
    )

    assert restored.page_index == 3
    assert restored.body.reveal_chars == 42
    assert restored.current_page.start_reveal == 42
    assert restored.fully_revealed() is False
    restored.update(0.5, InputState())
    assert restored.body._wait_left == pytest.approx(1.25)


@pytest.mark.parametrize("phase", ["pending", "completed"])
def test_wait_boundary_reflow_preserves_pending_and_completed_side(
    phase: str,
) -> None:
    plain = "甲" * 200
    wait_plain = 48
    markup = plain[:wait_plain] + "[w=2]" + plain[wait_plain:]
    original = _box(width=180, body_lines=2, text_speed=20)
    original.set_line("測試者", markup)
    for _ in range(3):
        original.force_reveal()
        assert original.advance_page() is True
    original.force_reveal()
    assert original.page_index == 3
    assert original.body.reveal_chars == 56
    if phase == "completed":
        assert original.advance_page() is True
        original.update(0.25, InputState())
        original.update(1.75, InputState())
        assert original.body._wait_left == 0.0
    timeline = original.body.export_wait_timeline()

    restored = _box(width=200, body_lines=2, text_speed=20)
    restored.set_line("測試者", markup)
    restored.restore_page(
        original.page_index,
        original.body.reveal_chars,
        original.body.plain_cursor_for_reveal(),
        wait_timeline=timeline,
    )

    if phase == "pending":
        assert timeline == {
            "consumed_plain_positions": [],
            "active": None,
        }
        assert restored.page_index == 2
        assert restored.body.reveal_chars == restored.current_page.end_reveal
        restored.update(0.25, InputState())
        assert restored.body._wait_left == 0.0
        assert restored.advance_page() is True
        restored.update(0.25, InputState())
        assert restored.body._wait_left == pytest.approx(1.75)
    else:
        assert timeline == {
            "consumed_plain_positions": [wait_plain],
            "active": None,
        }
        assert restored.page_index == 3
        assert restored.body.reveal_chars == restored.current_page.start_reveal
        restored.update(0.1, InputState())
        assert restored.body._wait_left == 0.0
        assert restored.body.reveal_chars > restored.current_page.start_reveal


def test_plain_cursor_survives_reflow_and_trailing_newline() -> None:
    fonts = FontRegistry(("Arial",))
    text = "甲乙丙丁" * 40
    narrow = RichText(
        pygame.Rect(0, 0, 180, 500),
        text,
        fonts=fonts,
        size=24,
        text_speed=0,
        reveal_chars=0,
    )
    wide = RichText(
        pygame.Rect(0, 0, 520, 500),
        text,
        fonts=fonts,
        size=24,
        text_speed=0,
        reveal_chars=0,
    )
    narrow_pages = narrow.paginate(80)
    narrow.set_reveal(narrow_pages[0].end_reveal)
    plain_cursor = narrow.plain_cursor_for_reveal()
    wide_cursor = wide.reveal_cursor_for_plain(plain_cursor)

    assert wide.plain_cursor_for_reveal(wide_cursor) == plain_cursor

    trailing = RichText(
        pygame.Rect(0, 0, 300, 200),
        "甲\n\n",
        fonts=fonts,
        size=24,
        text_speed=0,
        reveal_chars=None,
    )
    assert trailing.plain_cursor_for_reveal(trailing.total_chars()) == 3
    assert trailing.reveal_cursor_for_plain(3) == trailing.total_chars()


def test_plain_cursor_roundtrips_every_explicit_blank_line_position() -> None:
    fonts = FontRegistry(("Arial",))
    text = RichText(
        pygame.Rect(0, 0, 300, 200),
        "甲\n\n乙",
        fonts=fonts,
        size=24,
        text_speed=0,
        reveal_chars=0,
    )

    assert [
        text.plain_cursor_for_reveal(cursor)
        for cursor in range(text.total_chars() + 1)
    ] == [0, 1, 2, 3, 4]
    assert [
        text.reveal_cursor_for_plain(cursor)
        for cursor in range(len(text.plain_text()) + 1)
    ] == [0, 1, 2, 3, 4]


def test_dirty_rich_text_maps_plain_cursor_after_wrapping_layout() -> None:
    fonts = FontRegistry(("Arial",))
    text = RichText(
        pygame.Rect(0, 0, 120, 800),
        "甲乙丙丁" * 40,
        fonts=fonts,
        size=24,
        text_speed=0,
        reveal_chars=0,
    )

    reveal_cursor = text.reveal_cursor_for_plain(100)

    assert reveal_cursor > 100
    assert text.plain_cursor_for_reveal(reveal_cursor) == 100


def test_wait_before_blank_line_survives_page_restore() -> None:
    markup = "甲[w=5]\n\n乙"
    original = _box()
    original.set_line("測試者", markup)
    wait_plain = next(iter(original.body._doc.waits()))
    wait_reveal = original.body.reveal_cursor_for_plain(wait_plain)
    original.body.set_reveal(wait_reveal)
    plain_cursor = original.body.plain_cursor_for_reveal()

    restored = _box()
    restored.set_line("測試者", markup)
    restored.restore_page(
        original.page_index,
        wait_reveal,
        plain_cursor,
    )
    restored.update(0.01, InputState())

    assert restored.body.reveal_chars == wait_reveal
    assert restored.body._wait_left > 4.0


def test_same_layout_restore_preserves_raw_side_of_implicit_wrap() -> None:
    original = _box(width=220)
    original.set_line("測試者", "甲乙丙丁" * 40)
    first_wrap = original.body._rows[1][0]
    raw_cursor = first_wrap.start_reveal - 1
    plain_cursor = original.body.plain_cursor_for_reveal(raw_cursor)
    assert original.body.reveal_cursor_for_plain(plain_cursor) != raw_cursor

    restored = _box(width=220)
    restored.set_line("測試者", "甲乙丙丁" * 40)
    restored.restore_page(0, raw_cursor, plain_cursor)

    assert restored.body.reveal_chars == raw_cursor


def test_dialogue_box_restore_reflows_without_revealing_extra_text() -> None:
    authored = "甲乙丙丁" * 80
    narrow = _box(width=240)
    narrow.set_line("測試者", authored)
    raw_cursor = max(
        narrow.current_page.start_reveal,
        narrow.current_page.end_reveal - 3,
    )
    narrow.body.set_reveal(raw_cursor)
    plain_cursor = narrow.body.plain_cursor_for_reveal()

    wide = _box(width=620)
    wide.set_line("測試者", authored)
    wide.restore_page(
        narrow.page_index,
        raw_cursor,
        plain_cursor,
    )

    assert wide.body.plain_cursor_for_reveal() == plain_cursor
    assert wide.body.reveal_chars != raw_cursor


def test_instant_text_flips_to_a_fully_visible_next_page() -> None:
    box = _box(text_speed=0)
    box.set_line("測試者", "甲乙丙丁" * 80)
    box.update(0.0, InputState())
    assert box.fully_revealed() is True

    assert box.advance_page() is True
    assert box.fully_revealed() is True
    assert box.body.reveal_chars == box.current_page.end_reveal


def test_scene_manual_pages_are_one_input_each_and_do_not_replay_state() -> None:
    from world_gal_game.core.story_graph import Choice, Effect, Line, Scene
    from world_gal_game.dev.driver import GameDriver

    driver = GameDriver(pack="demo_pack")
    try:
        second_text = "這是分頁後的下一句。" * 60
        dialogue = _open_scene(
            driver,
            Scene(
                id="paged_manual_contract",
                lines=[
                    Line(
                        text="甲乙丙丁" * 110,
                        effects=[
                            Effect(
                                kind="increment_flag",
                                target="paged_effect_count",
                                value=1,
                            ),
                        ],
                    ),
                    Line(text=second_text),
                ],
                choices=[
                    Choice(
                        id="confirm",
                        text="作出決定",
                        effects=[
                            Effect(
                                kind="set_flag",
                                target="paged_choice_selected",
                                value=True,
                            ),
                        ],
                    ),
                ],
            ),
        )
        box = dialogue.box
        assert box.page_count > 1
        assert driver.app.state.events.flags["paged_effect_count"] == 1
        story_index = driver.app.state.story.current_line_index
        history_depth = dialogue._history.depth()
        model_history = driver.app.state.dialogue_history.model_dump(
            mode="json",
        )
        read_log = driver.app.state.read_log.model_dump(mode="json")
        assert dialogue.describe()["adv_page"] == {
            "index": 0,
            "count": box.page_count,
            "current_fully_revealed": False,
            "has_more": True,
        }

        # One input completes only the current page.
        driver.press_space(count=1, frames_between=1)
        assert box.page_index == 0
        assert box.fully_revealed() is True
        assert driver.app.state.story.current_line_index == story_index
        assert dialogue._history.depth() == history_depth
        assert (
            driver.app.state.dialogue_history.model_dump(mode="json")
            == model_history
        )
        assert driver.app.state.read_log.model_dump(mode="json") == read_log

        # The next input flips exactly one visual page.
        driver.press_space(count=1, frames_between=1)
        assert box.page_index == 1
        assert driver.app.state.story.current_line_index == story_index
        assert dialogue._history.depth() == history_depth
        assert (
            driver.app.state.dialogue_history.model_dump(mode="json")
            == model_history
        )
        assert driver.app.state.read_log.model_dump(mode="json") == read_log

        while box.has_more_pages():
            box.force_reveal()
            driver.press_space(count=1, frames_between=1)
        box.force_reveal()
        assert driver.app.state.events.flags["paged_effect_count"] == 1
        assert dialogue._history.depth() == history_depth
        assert (
            driver.app.state.dialogue_history.model_dump(mode="json")
            == model_history
        )
        assert driver.app.state.read_log.model_dump(mode="json") == read_log

        # Only leaving the final page presents the next authored line.
        driver.press_space(count=1, frames_between=1)
        assert dialogue._current_line.text == second_text
        assert driver.app.state.events.flags["paged_effect_count"] == 1
        assert dialogue._history.depth() == history_depth + 1
        assert dialogue.box.page_count > 1

        # The input that presents a choice cannot also select it.
        while dialogue.box.has_more_pages():
            dialogue.box.force_reveal()
            driver.press_space(count=1, frames_between=1)
        dialogue.box.force_reveal()
        driver.press_space(count=1, frames_between=1)
        assert dialogue.choices.visible is True
        assert "paged_choice_selected" not in driver.app.state.events.flags

        # Rollback returns to the already-read final page without replaying FX.
        driver.press_key(pygame.K_BACKSPACE)
        driver.advance_frames(1)
        assert dialogue._current_line.text == second_text
        assert dialogue.box.page_count > 1
        assert dialogue.box.page_index == dialogue.box.page_count - 1
        assert dialogue.box.fully_revealed() is True
        assert driver.app.state.events.flags["paged_effect_count"] == 1
    finally:
        driver.quit()


def test_enter_after_three_line_yaml_literal_advances_once() -> None:
    from world_gal_game.core.story_graph import Line, Scene
    from world_gal_game.dev.driver import GameDriver

    driver = GameDriver(pack="demo_pack")
    try:
        second_text = "下一句。"
        dialogue = _open_scene(
            driver,
            Scene(
                id="trailing_newline_advance_contract",
                lines=[
                    Line(text="第一行\n第二行\n第三行\n"),
                    Line(text=second_text),
                ],
            ),
        )
        dialogue.box.force_reveal()
        assert dialogue.box.page_count == 1
        assert dialogue.box.has_more_pages() is False

        driver.press_key(pygame.K_RETURN)
        driver.advance_frames(1)

        assert dialogue._current_line.text == second_text
        assert dialogue.box.page_index == 0
        assert dialogue.box.page_count == 1
    finally:
        driver.quit()


@pytest.mark.parametrize(
    "tail",
    [
        pytest.param(" \t", id="ascii"),
        pytest.param("\u00a0", id="non-breaking-space"),
        pytest.param("\u3000", id="ideographic-space"),
        pytest.param("\r", id="carriage-return"),
    ],
)
def test_enter_ignores_resolved_invisible_terminal_whitespace_tail(
    tail: str,
) -> None:
    from world_gal_game.core.story_graph import Line, Scene
    from world_gal_game.dev.driver import GameDriver

    driver = GameDriver(pack="demo_pack")
    try:
        first_text = f"第一行\n第二行\n第三行\n{tail}"
        second_text = "下一句。"
        dialogue = _open_scene(
            driver,
            Scene(
                id="terminal_whitespace_advance_contract",
                lines=[Line(text=first_text), Line(text=second_text)],
            ),
        )
        visible_end = dialogue.box.body.terminal_visible_end_reveal()
        dialogue.box.body.set_reveal(visible_end)
        plain_cursor = dialogue.box.body.plain_cursor_for_reveal()

        assert visible_end < dialogue.box.body.total_chars()
        assert plain_cursor < len(first_text)
        assert dialogue.box.fully_revealed() is True
        assert dialogue.box.body.reveal_chars == visible_end
        assert dialogue.box.body.plain_cursor_for_reveal() == plain_cursor

        driver.press_key(pygame.K_RETURN)
        driver.advance_frames(1)

        assert dialogue._current_line.text == second_text
    finally:
        driver.quit()


def test_terminal_whitespace_tail_does_not_bypass_pending_wait() -> None:
    from world_gal_game.core.story_graph import Line, Scene
    from world_gal_game.dev.driver import GameDriver

    driver = GameDriver(pack="demo_pack")
    try:
        first_text = "第一行\n第二行\n第三行\n [w=5]\t"
        second_text = "等待之後的下一句。"
        dialogue = _open_scene(
            driver,
            Scene(
                id="terminal_whitespace_wait_contract",
                lines=[Line(text=first_text), Line(text=second_text)],
            ),
        )
        body = dialogue.box.body
        visible_end = body.terminal_visible_end_reveal()
        wait_plain = next(iter(body._doc.waits()))
        wait_reveal = body.reveal_cursor_for_plain(wait_plain)
        body.set_reveal(visible_end)

        assert visible_end < wait_reveal < body.total_chars()
        assert dialogue.box.fully_revealed() is False

        driver.press_key(pygame.K_RETURN)
        driver.advance_frames(1)

        assert dialogue._current_line.text == first_text
        assert wait_reveal in body._waits_done
        assert body.reveal_chars == body.total_chars()

        driver.press_key(pygame.K_RETURN)
        driver.advance_frames(1)
        assert dialogue._current_line.text == second_text
    finally:
        driver.quit()


def test_auto_pages_before_waiting_for_voice_on_final_page() -> None:
    from world_gal_game.core.story_graph import Line, Scene
    from world_gal_game.dev.driver import GameDriver

    driver = GameDriver(pack="demo_pack")
    try:
        dialogue = _open_scene(
            driver,
            Scene(
                id="paged_auto_contract",
                lines=[
                    Line(
                        text="甲乙丙丁" * 110,
                        voice="probe.ogg",
                    ),
                    Line(text="Auto 最後才進入這一句。"),
                ],
            ),
        )
        box = dialogue.box
        assert box.page_count > 1
        box.force_reveal()
        dialogue.auto_play_enabled = True
        delay = (
            driver.cfg.auto_play_delay
            / max(0.1, driver.cfg.auto_play_speed)
        )
        driver.app.assets.voice_busy = lambda: True
        dialogue._auto_play_timer = delay
        story_index = driver.app.state.story.current_line_index

        driver.advance_frames(1)

        assert box.page_index == 1
        assert driver.app.state.story.current_line_index == story_index

        box.reveal_last_page()
        box.force_reveal()
        dialogue._auto_play_timer = delay
        driver.advance_frames(1)
        assert driver.app.state.story.current_line_index == story_index
        assert dialogue._auto_play_timer == 0.0

        driver.app.assets.voice_busy = lambda: False
        dialogue._auto_play_timer = delay
        driver.advance_frames(1)
        assert dialogue._current_line.text == "Auto 最後才進入這一句。"
    finally:
        driver.quit()


def test_skip_and_resume_keep_pages_inside_one_authored_presentation() -> None:
    from world_gal_game.core.story_graph import Choice, Effect, Line, Scene
    from world_gal_game.dev.driver import GameDriver

    driver = GameDriver(pack="demo_pack")
    try:
        driver.cfg.skip_unread_only = False
        dialogue = _open_scene(
            driver,
            Scene(
                id="paged_skip_resume_contract",
                lines=[
                    Line(
                        text="甲乙丙丁" * 110,
                        effects=[
                            Effect(
                                kind="increment_flag",
                                target="paged_resume_effect_count",
                                value=1,
                            ),
                        ],
                    ),
                    Line(text="Skip 應停在選項以前。"),
                ],
                choices=[
                    Choice(
                        id="stop",
                        text="仍由玩家選擇",
                        effects=[
                            Effect(
                                kind="set_flag",
                                target="paged_skip_selected",
                                value=True,
                            ),
                        ],
                    ),
                ],
            ),
        )
        box = dialogue.box
        story_index = driver.app.state.story.current_line_index
        history_depth = dialogue._history.depth()

        dialogue._trigger_skip()
        assert box.page_index == 0 and box.fully_revealed()
        assert driver.app.state.story.current_line_index == story_index
        assert dialogue._history.depth() == history_depth

        dialogue._trigger_skip()
        assert box.page_index == box.page_count - 1
        assert box.fully_revealed()
        assert driver.app.state.story.current_line_index == story_index
        assert dialogue._history.depth() == history_depth

        # Resume a partial final page without re-running the line effect.
        page = box.page_index
        partial = max(
            box.current_page.start_reveal,
            box.current_page.end_reveal - 3,
        )
        box.body.set_reveal(partial)
        resume = dialogue.export_resume_context()
        assert resume is not None
        assert resume["text_page_index"] == page
        plain = resume["text_plain_cursor"]
        box.force_reveal()

        assert dialogue.restore_resume_context(resume) is True
        assert dialogue.box.page_index == page
        assert dialogue.box.body.plain_cursor_for_reveal() == plain
        assert (
            driver.app.state.events.flags["paged_resume_effect_count"]
            == 1
        )

        dialogue.box.force_reveal()
        dialogue._trigger_skip()
        assert dialogue.choices.visible is True
        assert "paged_skip_selected" not in driver.app.state.events.flags
    finally:
        driver.quit()


def test_wrapped_nvl_resume_uses_authored_cursor_before_first_redraw() -> None:
    from world_gal_game.core.story_graph import Effect, Line, Scene
    from world_gal_game.dev.driver import GameDriver

    driver = GameDriver(pack="demo_pack")
    try:
        driver.cfg.nvl_mode = True
        dialogue = _open_scene(
            driver,
            Scene(
                id="wrapped_nvl_resume_contract",
                lines=[
                    Line(
                        text="甲乙丙丁" * 100,
                        effects=[
                            Effect(
                                kind="increment_flag",
                                target="wrapped_nvl_effect_count",
                                value=1,
                            ),
                        ],
                    ),
                ],
            ),
        )
        body = dialogue.box.entries[-1].body
        authored_cursor = 100
        body.set_reveal(
            body.reveal_cursor_for_plain(authored_cursor),
        )
        resume = dialogue.export_resume_context()
        assert resume is not None
        assert resume["text_plain_cursor"] == authored_cursor

        body.force_reveal()
        assert dialogue.restore_resume_context(resume) is True
        restored = dialogue.box.entries[-1].body

        assert (
            restored.plain_cursor_for_reveal()
            == authored_cursor
        )
        assert (
            driver.app.state.events.flags["wrapped_nvl_effect_count"]
            == 1
        )
    finally:
        driver.quit()
