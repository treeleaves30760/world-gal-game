"""Bounded resolution/settings matrix for the stock high-contrast UI."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from world_gal_game.config import EngineConfig
from world_gal_game.core.game_state import GameState
from world_gal_game.core.localization import Localization
from world_gal_game.npc.npc_base import NPCRegistry
from world_gal_game.scenes.base import SceneContext
from world_gal_game.scenes.settings_scene import SettingsScene
from world_gal_game.ui.assets import AssetManager
from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.theme import Theme
from world_gal_game.ui.widgets.dialogue_box import DialogueBox


@pytest.fixture(scope="module", autouse=True)
def _pygame() -> None:
    pygame.display.init()
    pygame.font.init()
    pygame.display.set_mode((1, 1))
    yield
    pygame.quit()


def _context(
    size: tuple[int, int],
    root: Path,
    *,
    text_scale: float,
    reduce_motion: bool,
) -> SceneContext:
    config = EngineConfig(
        screen_size=size,
        writable_root_override=root,
        font_candidates=(),
        text_scale=text_scale,
        reduce_motion=reduce_motion,
        visual_accessibility_mode="high_contrast",
    )
    fonts = FontRegistry(
        candidates=(),
        scale=max(1.0, size[1] / 720.0),
    )
    return SceneContext(
        config=config,
        state=GameState(),
        npcs=NPCRegistry(),
        brain=None,  # type: ignore[arg-type]
        dialogue=None,  # type: ignore[arg-type]
        assets=AssetManager(pack_root=root),
        fonts=fonts,
        theme=Theme(),
        localization=Localization(),
        screen_size=size,
    )


def _dialogue_box(ctx: SceneContext) -> DialogueBox:
    width, height = ctx.screen_size
    layout_density = max(0.8, height / 720.0)
    chrome_density = max(0.01, float(ctx.fonts.scale))
    margin = max(16, int(round(32 * layout_density)))
    body_lines = 2 if height < 540 else 3
    metrics = DialogueBox.measure(
        ctx.fonts,
        ctx.theme,  # type: ignore[arg-type]
        text_scale=ctx.config.text_scale,
        density=layout_density,
        body_font_size=ctx.config.font_size_dialogue,
        speaker_font_size=ctx.config.font_size_speaker,
        body_lines=body_lines,
    )
    box = DialogueBox(
        pygame.Rect(
            margin,
            height - metrics.preferred_height - margin,
            width - margin * 2,
            metrics.preferred_height,
        ),
        fonts=ctx.fonts,
        theme=ctx.theme,  # type: ignore[arg-type]
        text_speed=0.0,
        text_scale=ctx.config.text_scale,
        density=chrome_density,
        metrics=metrics,
        motion=ctx.motion,
    )
    box.set_line("A", "ACCESS")
    box.force_reveal()
    return box


def _contains_white_glyph(
    surface: pygame.Surface,
    box: DialogueBox,
) -> bool:
    text_width, text_height = box.fonts.get(
        box.metrics.body_font_size,
    ).size("ACCESS")
    probe = pygame.Rect(
        box.body.rect.topleft,
        (
            min(box.body.rect.width, text_width + 4),
            min(box.body.rect.height, text_height + 4),
        ),
    )
    return any(
        surface.get_at((x, y))[:3] == (255, 255, 255)
        for y in range(probe.top, probe.bottom)
        for x in range(probe.left, probe.right)
    )


@pytest.mark.parametrize(
    "size",
    [(375, 667), (640, 360), (1280, 720), (1920, 1080), (3840, 2160)],
)
@pytest.mark.parametrize("text_scale", [1.0, 1.4])
@pytest.mark.parametrize("reduce_motion", [False, True])
def test_high_contrast_stock_settings_and_dialogue_matrix(
    size: tuple[int, int],
    text_scale: float,
    reduce_motion: bool,
    tmp_path: Path,
) -> None:
    ctx = _context(
        size,
        tmp_path,
        text_scale=text_scale,
        reduce_motion=reduce_motion,
    )
    viewport = pygame.Rect((0, 0), size)
    surface = pygame.Surface(size, pygame.SRCALPHA)

    assert ctx.visual is not None
    assert ctx.visual.high_contrast is True
    assert ctx.motion.reduced is reduce_motion
    assert ctx.theme.bg_panel == (0, 0, 0, 255)
    assert ctx.theme.text == (255, 255, 255)

    settings = SettingsScene(ctx)
    settings.enter(on_close=lambda: None, on_controls=lambda: None)
    expected_choice = "標準" if text_scale == 1.0 else "特大"
    text_scale_index = settings._button_ids.index("accessibility:text_scale")
    settings._focus = text_scale_index
    focused = settings._buttons[text_scale_index][0]
    assert focused.role == "choice"
    assert focused.label == f"文字大小：{expected_choice}"
    assert focused.selected is True
    settings._ensure_focus_visible()
    settings._sync_focus_state()

    assert viewport.contains(settings._panel_rect)
    assert settings._panel_rect.contains(settings._title_rect)
    assert settings._panel_rect.contains(settings.close_btn.rect)
    assert settings._panel_rect.contains(settings._tabs_rect)
    assert settings._panel_rect.contains(settings._body_rect)
    assert settings._panel_rect.contains(settings._footer_rect)
    assert all(
        tab.role == "tab" and settings._tabs_rect.contains(tab.rect)
        for _category, tab in settings._tab_buttons
    )
    assert sum(tab.selected for _category, tab in settings._tab_buttons) == 1
    assert settings._body_rect.contains(focused.rect)

    # First isolate the stock panel fallback over arbitrary authored art.
    surface.fill((91, 23, 147, 255))
    settings._panel.draw(surface)
    assert surface.get_at(settings._panel_rect.center) == (0, 0, 0, 255)

    surface.fill((91, 23, 147, 255))
    settings.draw(surface)
    assert focused.focused is True
    assert (
        surface.get_at((focused.rect.centerx, focused.rect.top + 1))[:3]
        == (ctx.theme.accent_warm[:3])
    )
    selected_inset = focused.rect.inflate(-8, -8)
    assert (
        surface.get_at((selected_inset.centerx, selected_inset.top))[:3]
        == (ctx.theme.accent_alt[:3])
    )

    box = _dialogue_box(ctx)
    expected_lines = 2 if size[1] < 540 else 3
    expected_body_size = round(ctx.config.font_size_dialogue * text_scale)
    name_surface = ctx.fonts.render(
        "A",
        box.metrics.speaker_font_size,
        ctx.theme.text,
        bold=True,
    )
    plate_pad_x = max(8, int(round(20 * box.metrics.density)))
    plate_rect = pygame.Rect(
        box.rect.x + max(10, int(round(30 * box.metrics.density))),
        box.rect.y - box.metrics.nameplate_height // 2,
        name_surface.get_width() + plate_pad_x * 2,
        box.metrics.nameplate_height,
    )

    assert viewport.contains(box.rect)
    assert viewport.contains(plate_rect)
    assert box.rect.contains(box.body.rect)
    assert box.rect.contains(box.footer_rect)
    assert box.body.rect.bottom < box.footer_rect.top
    assert box.metrics.body_lines == expected_lines
    assert box.metrics.body_font_size == expected_body_size
    assert box.motion.reduced is reduce_motion

    # The hint is static in reduced mode and may pulse in standard motion.
    box._hint_t = 0.25
    assert box._hint_alpha() == (200 if reduce_motion else 160)

    surface.fill((91, 23, 147, 255))
    box.draw(surface)
    assert surface.get_at(box.rect.center) == (0, 0, 0, 255)
    assert _contains_white_glyph(surface, box)
    box.body._ensure_layout()
    assert {run.color for row in box.body._rows for run in row} == {(255, 255, 255)}
