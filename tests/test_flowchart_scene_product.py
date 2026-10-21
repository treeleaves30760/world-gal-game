"""Product interaction and responsive-layout contracts for FlowchartScene."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from world_gal_game.dev.driver import GameDriver
from world_gal_game.dialogue.dialogue_engine import (
    LinePresentation,
    ScenePresentation,
    replay_presentation_to_dict,
)
from world_gal_game.scenes.flowchart_scene import FlowchartScene
from world_gal_game.ui.input import InputState
from world_gal_game.ui.input_bindings import InputAction


def _input(*actions: str) -> InputState:
    return InputState(
        actions_pressed=set(actions),
        mouse_pos=(-1000, -1000),
    )


def _key(key: int) -> InputState:
    return InputState(
        events=[pygame.event.Event(
            pygame.KEYDOWN,
            key=key,
            mod=0,
            unicode="",
        )],
        mouse_pos=(-1000, -1000),
    )


def _hat(x: int, y: int) -> InputState:
    return InputState(
        events=[pygame.event.Event(
            pygame.JOYHATMOTION,
            joy=0,
            instance_id=0,
            hat=0,
            value=(x, y),
        )],
        mouse_pos=(-1000, -1000),
    )


def _gamepad_button(button: int) -> InputState:
    return InputState(
        events=[pygame.event.Event(
            pygame.JOYBUTTONDOWN,
            joy=0,
            instance_id=0,
            button=button,
        )],
        mouse_pos=(-1000, -1000),
    )


def _read_all_chapters(driver: GameDriver) -> list:
    state = driver.app.state
    manifest = state.meta["__chapters__"]
    chapters = manifest.ordered()
    state.read_log.scenes.clear()
    state.read_log.lines.clear()
    state.read_log.replay_archive.clear()
    for chapter in chapters:
        scene_id = chapter.entry_scene or chapter.scenes[0]
        state.read_log.begin_replay_record(scene_id, chapter.title)
        state.read_log.record_replay_presentation(
            scene_id,
            replay_presentation_to_dict(
                ScenePresentation(
                    kind="line",
                    line=LinePresentation(
                        speaker=None,
                        text="流程圖互動測試",
                        plain_text="流程圖互動測試",
                        scene_id=scene_id,
                        line_index=0,
                        total_lines=1,
                    ),
                    scene_id=scene_id,
                    title=chapter.title,
                ),
            ),
        )
        state.read_log.mark_scene_done(scene_id)
    state.current_chapter = chapters[0].id
    return chapters


def _scene(
    driver: GameDriver,
    *,
    replay: bool = True,
) -> tuple[FlowchartScene, list[str], list[str]]:
    closes: list[str] = []
    jumps: list[str] = []
    scene = FlowchartScene(driver.app.ctx)
    scene.enter(
        on_close=lambda: closes.append("close"),
        on_jump=(jumps.append if replay else None),
    )
    return scene, closes, jumps


@pytest.mark.parametrize(
    "size",
    [
        (640, 360),
        (800, 450),
        (1280, 720),
        (1920, 1080),
        (3840, 2160),
    ],
    ids=["360p", "450p", "720p", "1080p", "4k"],
)
def test_flowchart_geometry_stays_contained_and_tappable(
    size: tuple[int, int],
) -> None:
    driver = GameDriver(pack="demo_pack", screen_size=size)
    try:
        _read_all_chapters(driver)
        scene, _closes, _jumps = _scene(driver)
        canvas = pygame.Rect((0, 0), size)

        assert canvas.contains(scene._panel_rect)
        assert scene._panel_rect.contains(scene._header_rect)
        assert scene._panel_rect.contains(scene._view_rect)
        assert scene._header_rect.contains(scene._title_rect)
        assert scene._header_rect.contains(scene._close_rect)
        assert not scene._title_rect.colliderect(scene._close_rect)
        if scene._hint_text:
            assert scene._header_rect.contains(scene._hint_rect)
            assert not scene._hint_rect.colliderect(scene._title_rect)
            assert not scene._hint_rect.colliderect(scene._close_rect)
        assert scene._header_rect.bottom <= scene._view_rect.top
        assert scene._view_rect.height >= size[1] // 3
        assert scene._close_rect.width >= 44
        assert scene._close_rect.height >= 44
        assert scene._hits
        assert all(rect.height >= 44 for rect, _scene_id in scene._hits)
        assert scene.ctx.fonts.get(
            scene._title_size,
            bold=True,
        ).size(scene._title_label)[0] <= scene._title_rect.width
        assert (
            scene.ctx.fonts.get(
                scene._card_title_size,
                bold=True,
            ).get_linesize()
            * 2
            + scene.ctx.fonts.get(scene._card_sub_size).get_linesize()
            + scene._sp(24)
            <= scene._card_h
        )

        focused = scene._hit_screen_rect(scene._focused_hit)
        assert scene._view_rect.contains(focused)
        target = pygame.Surface(size, pygame.SRCALPHA)
        scene.draw(target)
    finally:
        driver.quit()


def test_keyboard_focus_auto_scrolls_and_confirm_replays_focused_card() -> None:
    driver = GameDriver(pack="demo_pack", screen_size=(640, 360))
    try:
        _read_all_chapters(driver)
        scene, _closes, jumps = _scene(driver)
        assert scene._focused_hit == 0
        assert scene._scroll_x == 0

        for _ in range(3):
            scene.update(0.0, _key(pygame.K_TAB))

        assert scene._focused_hit == 3
        assert scene._scroll_x > 0
        assert scene._view_rect.contains(
            scene._hit_screen_rect(scene._focused_hit),
        )
        description = scene.describe()
        assert description["focus"] == {
            "target": "chapter",
            "hit_index": 3,
            "chapter_id": scene._hit_chapter_ids[3],
            "scene_id": scene._hits[3][1],
            "visible": True,
        }
        assert description["focus"]["chapter_id"] in description["visible_cards"]

        expected = scene._hits[3][1]
        scene.update(0.0, _key(pygame.K_RETURN))
        assert jumps == [expected]
    finally:
        driver.quit()


def test_gamepad_dpad_moves_card_focus_and_a_confirms() -> None:
    driver = GameDriver(pack="demo_pack")
    try:
        _read_all_chapters(driver)
        scene, _closes, jumps = _scene(driver)
        assert scene._focused_hit == 0

        scene.update(0.0, _hat(1, 0))
        assert scene._focused_hit == 1
        expected = scene._hits[1][1]

        scene.update(0.0, _gamepad_button(0))
        assert jumps == [expected]
    finally:
        driver.quit()


@pytest.mark.parametrize("touch_release", [False, True], ids=["mouse", "touch"])
def test_pointer_and_touch_tap_replay_the_hit_card(
    touch_release: bool,
) -> None:
    driver = GameDriver(pack="demo_pack")
    try:
        _read_all_chapters(driver)
        scene, _closes, jumps = _scene(driver)
        target = scene._hit_screen_rect(scene._focused_hit)
        expected = scene._hits[scene._focused_hit][1]

        scene.update(
            0.0,
            InputState(
                mouse_clicked=True,
                mouse_pos=target.center,
                touch_released=touch_release,
                touch_mode=touch_release,
            ),
        )

        assert jumps == [expected]
    finally:
        driver.quit()


def test_semantic_back_and_close_focus_are_both_operable() -> None:
    driver = GameDriver(pack="demo_pack")
    try:
        _read_all_chapters(driver)
        scene, closes, _jumps = _scene(driver)
        scene.update(0.0, _input(InputAction.BACK))
        assert closes == ["close"]

        scene, closes, _jumps = _scene(driver)
        # Current chapter is the first hit, so reverse Tab reaches Close.
        scene.update(0.0, _input(InputAction.FOCUS_PREVIOUS))
        assert scene.describe()["focus"] == {"target": "close"}
        assert scene.close_btn.focused is True
        scene.update(0.0, _input(InputAction.CONFIRM_ADVANCE))
        assert closes == ["close"]
    finally:
        driver.quit()


def test_browse_only_chart_exposes_close_as_its_only_action() -> None:
    driver = GameDriver(pack="demo_pack")
    try:
        _read_all_chapters(driver)
        scene, closes, jumps = _scene(driver, replay=False)
        description = scene.describe()

        assert description["replay_enabled"] is False
        assert description["interactive_cards"] == 0
        assert description["focus"] == {"target": "close"}
        scene.update(0.0, _input(InputAction.CONFIRM_ADVANCE))
        assert closes == ["close"]
        assert jumps == []
    finally:
        driver.quit()


def test_draw_has_a_persistent_high_contrast_card_focus_ring() -> None:
    driver = GameDriver(pack="demo_pack")
    try:
        _read_all_chapters(driver)
        scene, _closes, _jumps = _scene(driver)
        target = pygame.Surface(driver.app.ctx.screen_size, pygame.SRCALPHA)
        scene.draw(target)
        focus_rect = scene._hit_screen_rect(scene._focused_hit).inflate(4, 4)
        ring_pixel = target.get_at((focus_rect.centerx, focus_rect.top))

        assert tuple(ring_pixel[:3]) == tuple(scene.ctx.theme.accent_warm[:3])
    finally:
        driver.quit()
