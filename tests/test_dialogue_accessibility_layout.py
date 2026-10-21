"""Legibility contracts for persistent dialogue controls."""
from __future__ import annotations

import pygame
import pytest


@pytest.mark.parametrize(
    ("size", "minimum_text_height", "expected_bar_height"),
    [
        ((1280, 720), 17, 44),
        ((1920, 1080), 25, 54),
        ((960, 540), 17, 44),
        ((800, 450), 17, 44),
    ],
)
def test_quick_bar_and_status_hud_meet_canvas_text_floor(
    size,
    minimum_text_height,
    expected_bar_height,
):
    from world_gal_game.dev.driver import GameDriver
    from world_gal_game.scenes.dialogue_scene import DialogueScene

    driver = GameDriver(pack="demo_pack", screen_size=size)
    try:
        driver.new_game()
        scene = driver.app.manager.current
        assert isinstance(scene, DialogueScene)

        bar = scene._quick_bar
        buttons = [button for button, _active in bar._entries]
        assert buttons
        assert all(button.font_size == 16 for button in buttons)
        assert all(button.rect.height == expected_bar_height for button in buttons)
        assert all(button.rect.width >= 44 for button in buttons)
        assert pygame.Rect((0, 0), size).contains(bar.rect)
        assert driver.app.fonts.get(16, bold=True).get_linesize() \
            >= minimum_text_height

        scene.draw(driver.app.screen)
        chapter_size, date_size = scene._status_hud_font_sizes
        assert (chapter_size, date_size) == (18, 17)
        assert driver.app.fonts.get(chapter_size).get_linesize() \
            >= minimum_text_height
        assert driver.app.fonts.get(date_size).get_linesize() \
            >= minimum_text_height
        assert pygame.Rect((0, 0), size).contains(scene._status_hud_rect)
    finally:
        driver.quit()
