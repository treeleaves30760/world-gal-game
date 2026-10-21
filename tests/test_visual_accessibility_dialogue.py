"""Live high-contrast contracts for ADV/NVL reading text."""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.nvl_box import NVLBox
from world_gal_game.ui.theme import Theme
from world_gal_game.ui.visual import VisualPolicy, VisualTheme
from world_gal_game.ui.widgets.dialogue_box import DialogueBox
from world_gal_game.ui.widgets.rich_text_view import RichText


@pytest.fixture(scope="module", autouse=True)
def _pygame_init() -> None:
    pygame.init()
    pygame.display.set_mode((1, 1))
    yield
    pygame.quit()


@pytest.fixture
def fonts() -> FontRegistry:
    return FontRegistry(("Arial", "DejaVu Sans"))


def _live_theme() -> tuple[SimpleNamespace, VisualTheme]:
    config = SimpleNamespace(visual_accessibility_mode="standard")
    authored = Theme(
        text=(221, 211, 201),
        bg_panel=(31, 27, 43, 210),
        radius_m=0,
        radius_l=0,
    )
    return config, VisualTheme(authored, VisualPolicy.from_config(config))


def _run_colors(body: RichText) -> set[tuple]:
    body._ensure_layout()
    return {run.color for row in body._rows for run in row}


def test_adv_live_switch_preserves_page_reveal_waits_and_markup(fonts) -> None:
    config, theme = _live_theme()
    box = DialogueBox(
        pygame.Rect(20, 120, 600, 220),
        fonts=fonts,
        theme=theme,  # type: ignore[arg-type]
        text_speed=10.0,
    )
    box.set_line("測試", "A[w=1][color=#123456]B[/color]C")
    box.body.set_reveal(1)
    surface = pygame.Surface((640, 360), pygame.SRCALPHA)
    box.draw(surface)

    document = box.body._doc
    page_index = box.page_index
    page_count = box.page_count
    row_window = box.body._row_window
    wait_timeline = box.body.export_wait_timeline()
    assert _run_colors(box.body) == {(221, 211, 201), (18, 52, 86)}

    config.visual_accessibility_mode = "high_contrast"
    box.draw(surface)

    assert box.body._doc is document
    assert box.body.plain_text() == "ABC"
    assert box.body.reveal_chars == 1
    assert box.body.export_wait_timeline() == wait_timeline
    assert box.page_index == page_index
    assert box.page_count == page_count
    assert box.body._row_window == row_window
    assert _run_colors(box.body) == {(255, 255, 255)}

    config.visual_accessibility_mode = "standard"
    box.draw(surface)
    assert box.body._doc is document
    assert _run_colors(box.body) == {(221, 211, 201), (18, 52, 86)}


def test_nvl_live_switch_updates_every_accumulated_entry_without_reset(fonts) -> None:
    config, theme = _live_theme()
    box = NVLBox(
        pygame.Rect(20, 20, 600, 320),
        fonts=fonts,
        theme=theme,  # type: ignore[arg-type]
        text_speed=10.0,
    )
    box.set_line("甲", "first [color=#123456]mark[/color]")
    box.set_line("乙", "second [color=#654321]mark[/color]")
    box.entries[-1].body.set_reveal(2)
    surface = pygame.Surface((640, 360), pygame.SRCALPHA)
    box.draw(surface)

    documents = [entry.body._doc for entry in box.entries]
    reveals = [entry.body.reveal_chars for entry in box.entries]
    config.visual_accessibility_mode = "high_contrast"
    box.draw(surface)

    assert box.line_count == 2
    assert all(
        entry.body._doc is document
        for entry, document in zip(box.entries, documents, strict=True)
    )
    assert [entry.body.reveal_chars for entry in box.entries] == reveals
    assert all(_run_colors(entry.body) == {(255, 255, 255)} for entry in box.entries)


def test_high_contrast_adv_nameplate_uses_opaque_text_plane(fonts) -> None:
    config, theme = _live_theme()
    config.visual_accessibility_mode = "high_contrast"
    rect = pygame.Rect(20, 120, 600, 220)
    box = DialogueBox(
        rect,
        fonts=fonts,
        theme=theme,  # type: ignore[arg-type]
        text_speed=0.0,
    )
    box.set_line("A", "line", speaker_color=(90, 90, 90))
    surface = pygame.Surface((640, 360), pygame.SRCALPHA)
    surface.fill((180, 20, 160, 255))
    box.draw(surface)

    name = fonts.render("A", box.metrics.speaker_font_size, theme.text, bold=True)
    plate_width = name.get_width() + 40
    plate_left = rect.x + 30
    plate_top = rect.y - box.metrics.nameplate_height // 2
    probe = (
        plate_left + plate_width - 4,
        plate_top + box.metrics.nameplate_height // 2,
    )
    assert surface.get_at(probe) == (0, 0, 0, 255)
