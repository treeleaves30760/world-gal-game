"""Dialogue integration contracts for semantic sound captions."""
from __future__ import annotations

import pygame
import pytest
import yaml

from world_gal_game.core.story_graph import Choice, Line, Scene
from world_gal_game.dev.driver import GameDriver
from world_gal_game.scenes.dialogue_scene import DialogueScene
from world_gal_game.ui.input import InputState
from world_gal_game.ui.nvl_box import NVLBox
from world_gal_game.ui.widgets.dialogue_box import DialogueBox


def _caption(kind: str = "sfx", text: str = "門外傳來敲門聲") -> dict:
    return {"kind": kind, "text": text}


def _dialogue_scene(
    driver: GameDriver,
    scene_id: str,
    lines: list[Line],
    *,
    choices: list[Choice] | None = None,
) -> DialogueScene:
    driver.app.state.story.add_scene(Scene(
        id=scene_id,
        lines=lines,
        choices=choices or [],
    ))
    scene = DialogueScene(driver.app.ctx)
    scene.enter(scene_id=scene_id)
    return scene


def test_caption_visibility_obeys_setting_choice_hidden_and_covered_gates() -> None:
    driver = GameDriver(pack="demo_pack")
    try:
        scene = _dialogue_scene(
            driver,
            "caption_visibility_probe",
            [Line(text="先聽見聲音。", sound_captions=[_caption()])],
        )

        assert scene.describe()["sound_captions"] == [
            _caption(),
        ]
        assert scene.describe()["sound_captions_visible"] is True

        driver.app.config.show_sound_captions = False
        assert scene._sound_captions_visible() is False
        driver.app.config.show_sound_captions = True

        scene.choices.visible = True
        assert scene._sound_captions_visible() is False
        scene.choices.visible = False

        scene._ui_hidden = True
        assert scene._sound_captions_visible() is False
        scene._ui_hidden = False

        scene.pause()
        assert scene._sound_captions_visible() is False
        scene.resume()
        assert scene._sound_captions_visible() is True
    finally:
        driver.quit()


def test_caption_overlay_does_not_change_adv_page_count() -> None:
    driver = GameDriver(pack="demo_pack")
    try:
        scene = _dialogue_scene(
            driver,
            "caption_pagination_probe",
            [Line(
                text="這是一段用來確認視覺分頁完全不受聲音字幕影響的正文。" * 45,
                sound_captions=[_caption(text="雨點持續敲打窗面")],
            )],
        )
        assert isinstance(scene.box, DialogueBox)
        before = (
            scene.box.page_count,
            scene.box.page_index,
            driver.app.state.story.current_line_index,
        )
        assert before[0] > 1

        scene._sound_caption_overlay.set_captions([
            _caption("ambient", "走廊另一端傳來規律的腳步聲"),
            _caption("music", "鋼琴主題逐漸浮現"),
        ])
        scene._sound_caption_overlay.update(
            1.0,
            InputState(advance_dialogue=True, confirm=True),
        )
        scene._sound_caption_overlay.draw(driver.app.screen)

        assert (
            scene.box.page_count,
            scene.box.page_index,
            driver.app.state.story.current_line_index,
        ) == before
    finally:
        driver.quit()


def test_one_enter_advances_exactly_one_logical_line_with_captions() -> None:
    driver = GameDriver(pack="demo_pack")
    try:
        scene = _dialogue_scene(
            driver,
            "caption_advance_probe",
            [
                Line(text="第一句。", sound_captions=[_caption()]),
                Line(
                    text="第二句。",
                    sound_captions=[_caption("silence", "敲門聲停止")],
                ),
                Line(text="第三句。"),
            ],
        )
        assert isinstance(scene.box, DialogueBox)
        assert scene.box.page_count == 1
        scene.box.force_reveal()
        before_index = driver.app.state.story.current_line_index

        scene.update(
            0.0,
            InputState(advance_dialogue=True, confirm=True),
        )

        assert driver.app.state.story.current_line_index == before_index + 1
        assert scene.describe()["current_line"]["text"] == "第二句。"
        assert scene.describe()["sound_captions"] == [
            _caption("silence", "敲門聲停止"),
        ]
        assert scene.box.page_count == 1
    finally:
        driver.quit()


def test_caption_draws_after_screen_effects_on_unshaken_surface() -> None:
    driver = GameDriver(pack="demo_pack")
    try:
        scene = _dialogue_scene(
            driver,
            "caption_stability_probe",
            [Line(text="畫面晃動。", sound_captions=[_caption()])],
        )
        events: list[str] = []
        target = driver.app.screen

        class Effect:
            def __init__(self, name: str) -> None:
                self.name = name

            def draw(self, surface: pygame.Surface) -> None:
                assert surface is target
                events.append(self.name)

        scene._tint = Effect("tint")  # type: ignore[assignment]
        scene._flashes = [Effect("flash")]  # type: ignore[list-item]
        scene._active_shake_offset = lambda: (9, 4)  # type: ignore[method-assign]
        original_draw = scene._sound_caption_overlay.draw

        def caption_draw(surface: pygame.Surface) -> None:
            assert surface is target
            events.append("caption")
            original_draw(surface)

        scene._sound_caption_overlay.draw = caption_draw  # type: ignore[method-assign]

        scene.draw(target)

        assert events == ["tint", "flash", "caption"]
    finally:
        driver.quit()


def test_high_contrast_keeps_screen_effects_below_reading_chrome() -> None:
    driver = GameDriver(pack="demo_pack")
    try:
        driver.app.config.visual_accessibility_mode = "high_contrast"
        scene = _dialogue_scene(
            driver,
            "high_contrast_effect_order_probe",
            [Line(text="高對比正文。", sound_captions=[_caption()])],
        )
        assert isinstance(scene.box, DialogueBox)
        events: list[str] = []

        class Effect:
            def __init__(self, name: str) -> None:
                self.name = name

            def draw(self, _surface: pygame.Surface) -> None:
                events.append(self.name)

        scene._tint = Effect("tint")  # type: ignore[assignment]
        scene._flashes = [Effect("flash")]  # type: ignore[list-item]
        original_box_draw = scene.box.draw
        original_caption_draw = scene._sound_caption_overlay.draw

        def box_draw(surface: pygame.Surface) -> None:
            events.append("dialogue")
            original_box_draw(surface)

        def caption_draw(surface: pygame.Surface) -> None:
            events.append("caption")
            original_caption_draw(surface)

        scene.box.draw = box_draw  # type: ignore[method-assign]
        scene._sound_caption_overlay.draw = caption_draw  # type: ignore[method-assign]
        scene.draw(driver.app.screen)

        assert events == ["tint", "flash", "dialogue", "caption"]
    finally:
        driver.quit()


@pytest.mark.parametrize("size", [(640, 360), (3840, 2160)], ids=["360p", "4k"])
@pytest.mark.parametrize("text_scale", [1.0, 1.4], ids=["standard", "large"])
@pytest.mark.parametrize("nvl_mode", [False, True], ids=["adv", "nvl"])
@pytest.mark.parametrize("cue_count", [1, 4], ids=["one-cue", "four-cues"])
def test_caption_uses_measured_dialogue_safe_area(
    size: tuple[int, int],
    text_scale: float,
    nvl_mode: bool,
    cue_count: int,
) -> None:
    driver = GameDriver(pack="demo_pack", screen_size=size)
    try:
        driver.app.config.text_scale = text_scale
        driver.app.config.nvl_mode = nvl_mode
        scene = _dialogue_scene(
            driver,
            "caption_geometry_probe",
            [Line(
                text="正文保留自己的閱讀區域。",
                sound_captions=[
                    _caption("sfx", f"第 {index + 1} 則聲音提示")
                    for index in range(cue_count)
                ],
            )],
        )
        # Exercise both top-right functional badges at their maximum stack.
        scene.auto_play_enabled = True
        scene._skip_active = True

        scene.draw(driver.app.screen)

        overlay = scene._sound_caption_overlay
        caption = overlay.rect
        status = scene._status_hud_rect
        quickbar = scene._quick_bar.rect
        assert len(overlay.caption_lines) == cue_count
        assert all(lines and all(line for line in lines)
                   for lines in overlay.caption_lines)
        for cue, lines in zip(overlay.captions, overlay.caption_lines):
            assert cue.text in " ".join(lines)
        assert overlay.safe_rect.contains(caption)
        assert not caption.colliderect(status)
        assert all(
            not caption.colliderect(rect)
            for rect in scene._mode_indicator_rects
        )
        assert not caption.colliderect(quickbar)
        assert not status.colliderect(quickbar)

        if nvl_mode:
            assert isinstance(scene.box, NVLBox)
            content = scene.box.content_rect
            assert content.height >= scene.box.minimum_content_height
            assert not caption.colliderect(content)
            assert not status.colliderect(content)
            assert all(
                not rect.colliderect(content)
                for rect in scene._mode_indicator_rects
            )
        else:
            assert isinstance(scene.box, DialogueBox)
            assert not caption.colliderect(scene.box.rect)
            assert not status.colliderect(scene.box.rect)
    finally:
        driver.quit()


def test_resume_reflows_caption_from_live_text_scale_without_advancing() -> None:
    driver = GameDriver(pack="demo_pack", screen_size=(1280, 720))
    try:
        driver.app.config.text_scale = 1.0
        scene = _dialogue_scene(
            driver,
            "caption_resume_reflow_probe",
            [
                Line(
                    text="設定開啟前仍停留在這句。",
                    sound_captions=[
                        _caption("ambient", "雨聲沿著屋簷逐漸靠近"),
                    ],
                ),
                Line(text="下一句不應被提早顯示。"),
            ],
        )
        scene.draw(driver.app.screen)
        overlay = scene._sound_caption_overlay
        before_font = overlay._font_size
        before_rect = overlay.rect.copy()
        before_story_index = driver.app.state.story.current_line_index
        before_page = (scene.box.page_index, scene.box.page_count)

        scene.pause()
        driver.app.config.text_scale = 1.4
        scene.resume()

        assert overlay.text_scale == 1.4
        assert overlay._font_size > before_font
        assert overlay.rect != before_rect
        assert driver.app.state.story.current_line_index == before_story_index
        assert (scene.box.page_index, scene.box.page_count) == before_page
        assert scene.describe()["current_line"]["text"] == "設定開啟前仍停留在這句。"
    finally:
        driver.quit()


@pytest.mark.parametrize(
    "line_ending",
    [
        pytest.param("\n", id="lf"),
        pytest.param("\r\n", id="crlf"),
    ],
)
def test_yaml_three_line_literal_with_caption_advances_on_one_enter(
    line_ending: str,
) -> None:
    source = """
id: caption_yaml_trailing_lf_probe
lines:
  - text: |
      第一行
      第二行
      第三行
    sound_captions:
      - kind: sfx
        text: 遠處傳來鐘聲
  - text: 下一句。
"""
    authored = Scene.model_validate(
        yaml.safe_load(source.replace("\n", line_ending)),
    )
    assert authored.lines[0].text.endswith("\n")
    assert "\r" not in authored.lines[0].text

    driver = GameDriver(pack="demo_pack")
    try:
        driver.app.state.story.add_scene(authored)
        driver.app._start_dialogue(authored.id)
        driver.app.manager.commit_pending()
        driver.advance_frames(1)
        scene = driver.app.manager.current
        assert isinstance(scene, DialogueScene)
        assert isinstance(scene.box, DialogueBox)
        scene.box.force_reveal()
        assert scene.box.page_count == 1
        assert scene.box.has_more_pages() is False
        assert scene.describe()["sound_captions"] == [
            _caption("sfx", "遠處傳來鐘聲"),
        ]
        before_index = driver.app.state.story.current_line_index

        driver.press_key(pygame.K_RETURN)
        driver.advance_frames(1)

        assert scene.describe()["current_line"]["text"] == "下一句。"
        assert driver.app.state.story.current_line_index == before_index + 1
        assert scene.box.page_index == 0
        assert scene.box.page_count == 1
    finally:
        driver.quit()
