"""Product interaction, layout, and bounded-rendering contracts for replay."""
from __future__ import annotations

import dataclasses
import math
import os

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from world_gal_game.core.chapter_spec import ChapterManifest
from world_gal_game.core.localization import Localization
from world_gal_game.core.map_system import Location, MapSystem
from world_gal_game.core.story_graph import Line, Scene as StoryScene
from world_gal_game.dev.driver import GameDriver
from world_gal_game.dialogue.dialogue_engine import DialogueEngine
from world_gal_game.dialogue.replay_engine import (
    PresentationTraceDialogueEngine,
)
from world_gal_game.npc.npc_base import NPC
from world_gal_game.scenes.scene_replay_scene import SceneReplayScene
from world_gal_game.ui.input import InputRouter, InputState
from world_gal_game.ui.input_bindings import InputAction
from world_gal_game.ui.system_chrome import ChromeRequest


def _install_scenes(
    driver: GameDriver,
    count: int,
    *,
    prefix: str = "replay_product",
) -> list[str]:
    state = driver.app.state
    state.story.scenes.clear()
    state.story.current_scene = None
    state.story.current_line_index = 0
    state.story.played.clear()
    state.read_log.scenes.clear()
    state.read_log.lines.clear()
    scene_ids: list[str] = []
    for index in range(count):
        scene_id = f"{prefix}_{index:04d}"
        state.story.add_scene(
            StoryScene(
                id=scene_id,
                title=f"第 {index + 1:03d} 段回想場景",
                lines=[Line(text=f"回想內容 {index + 1}")],
            ),
        )
        scene_ids.append(scene_id)
    # The real app snapshots the loaded pack story for replay provenance.
    # These product tests replace that story with synthetic authored scenes,
    # so refresh the snapshot instead of weakening runtime validation.
    state.meta["__pack_story_catalog__"] = state.story.model_copy(deep=True)
    engine = DialogueEngine(state)
    for scene_id in scene_ids:
        assert engine.start_scene(scene_id).kind == "line"
        assert engine.next_line().kind == "end"
    return scene_ids


def _scene(
    driver: GameDriver,
    *,
    count: int = 12,
    on_close=None,
) -> tuple[SceneReplayScene, list[str]]:
    scene_ids = _install_scenes(driver, count)
    scene = SceneReplayScene(driver.app.ctx)
    scene.enter(on_close=on_close)
    return scene, scene_ids


def _action(*actions: str) -> InputState:
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
@pytest.mark.parametrize("text_scale", [1.0, 1.4], ids=["text-100", "text-140"])
def test_replay_layout_is_contained_readable_and_tappable(
    size: tuple[int, int],
    text_scale: float,
) -> None:
    driver = GameDriver(pack="demo_pack", screen_size=size)
    try:
        driver.app.config.text_scale = text_scale
        scene, _scene_ids = _scene(driver)
        canvas = pygame.Rect((0, 0), size)

        assert canvas.contains(scene._panel_rect)
        assert scene._panel_rect.contains(scene._header_rect)
        assert scene._panel_rect.contains(scene._viewport)
        assert scene._panel_rect.contains(scene._footer_rect)
        assert scene._header_rect.contains(scene._title_rect)
        assert scene._header_rect.contains(scene._close_rect)
        assert not scene._title_rect.colliderect(scene._close_rect)
        assert scene._header_rect.bottom <= scene._viewport.top
        assert scene._viewport.bottom <= scene._footer_rect.top
        assert scene._close_rect.width >= 44
        assert scene._close_rect.height >= 44
        assert scene._row_height >= 44
        assert scene._row_height >= scene._minimum_target
        assert (
            scene._row_pad_y * 2
            + scene._row_title_line_h
            + scene._text_gap
            + scene._row_meta_line_h
            <= scene._row_height
        )
        assert (
            scene._chip_size + scene._row_pad_y * 2
            <= scene._row_height
        )
        close_font = scene.ctx.fonts.get(scene._close_size, bold=True)
        assert close_font.size(scene.close_btn.label)[0] <= scene._close_rect.width

        target = pygame.Surface(size, pygame.SRCALPHA)
        scene.draw(target)
        assert scene._row_rects
        assert all(
            scene._viewport.contains(rect)
            and rect.width >= 44
            and rect.height >= scene._minimum_target
            for rect, _scene_id in scene._row_rects
        )
        visible_capacity = math.ceil(
            scene._viewport.height
            / (scene._row_height + scene._row_gap),
        )
        assert scene.describe()["drawn_rows"] <= visible_capacity + 4
    finally:
        driver.quit()


def test_text_scale_reflows_rows_without_losing_focus_identity() -> None:
    driver = GameDriver(pack="demo_pack")
    try:
        scene, scene_ids = _scene(driver, count=24)
        for _ in range(9):
            scene.update(0.0, _action(InputAction.NAV_DOWN))
        focused = scene.describe()["focus"]["scene_id"]
        assert focused == scene_ids[9]
        normal_height = scene._row_height

        driver.app.config.text_scale = 1.4
        scene.resume()

        assert scene._row_height > normal_height
        assert scene.describe()["focus"]["scene_id"] == focused
        focused_rect = scene._row_screen_rect(scene._rows[scene._focus])
        assert scene._viewport.contains(focused_rect)
    finally:
        driver.quit()


def test_replay_list_uses_story_insertion_order_not_title_sort() -> None:
    driver = GameDriver(pack="demo_pack")
    try:
        state = driver.app.state
        state.story.scenes.clear()
        state.read_log.scenes.clear()
        authored = [
            ("third_authored", "甲標題"),
            ("first_authored", "丙標題"),
            ("second_authored", "乙標題"),
        ]
        for scene_id, title in authored:
            state.story.add_scene(
                StoryScene(
                    id=scene_id,
                    title=title,
                    lines=[Line(text=title)],
                ),
            )
        engine = DialogueEngine(state)
        for scene_id, _title in authored:
            assert engine.start_scene(scene_id).kind == "line"
            assert engine.next_line().kind == "end"

        scene = SceneReplayScene(driver.app.ctx)
        scene.enter()

        assert scene.describe()["replayable"] == [
            scene_id for scene_id, _title in authored
        ]
    finally:
        driver.quit()


def test_replay_rows_use_human_title_and_authoritative_metadata_only() -> None:
    driver = GameDriver(pack="demo_pack")
    try:
        state = driver.app.state
        state.story.scenes.clear()
        state.story.current_scene = None
        state.story.current_line_index = 0
        state.story.played.clear()
        state.read_log.scenes.clear()
        state.read_log.lines.clear()
        state.read_log.replay_archive.clear()
        driver.app.ctx.npcs.npcs.clear()
        driver.app.ctx.npcs.add(NPC(
            id="npc_ruoxue",
            name="白若雪",
            route_id="route_ruoxue",
            is_heroine=True,
        ))

        authored_map = MapSystem()
        authored_map.add_location(Location(
            id="campus_gate",
            name="櫻丘校門",
        ))
        state.map = authored_map.model_copy(deep=True)
        state.meta["__pack_map_catalog__"] = authored_map
        state.meta["__chapters__"] = ChapterManifest.from_items([
            {
                "id": "chapter_arrival",
                "title": "第一章・初來乍到",
                "route": "common",
                "order": 10,
                "scenes": ["arrival_scene"],
            },
            {
                "id": "chapter_ruoxue",
                "title": "第二章・雨後相逢",
                "route": "route_ruoxue",
                "order": 20,
                "scenes": ["meet_ruoxue"],
            },
        ])
        scenes = [
            StoryScene(
                id="arrival_scene",
                title="場景原始標題",
                location="campus_gate",
                lines=[Line(loc_id="arrival_gate_line", text="抵達校門。")],
            ),
            StoryScene(
                id="meet_ruoxue",
                title="雨中的女孩",
                location="campus_gate",
                route="route_ruoxue",
                lines=[Line(loc_id="meet_ruoxue_line", text="她撐著傘。")],
            ),
        ]
        for story_scene in scenes:
            state.story.add_scene(story_scene)
        state.meta["__pack_story_catalog__"] = state.story.model_copy(deep=True)

        engine = DialogueEngine(state)
        for story_scene in scenes:
            assert engine.start_scene(story_scene.id).kind == "line"
            assert engine.next_line().kind == "end"
        state.read_log.replay_archive["arrival_scene"].title = "珍藏的抵達"
        state.read_log.replay_archive["meet_ruoxue"].title = "meet_ruoxue"

        scene = SceneReplayScene(driver.app.ctx)
        scene.enter()
        description = scene.describe()

        assert [row.title for row in scene._rows] == [
            "珍藏的抵達",
            "雨中的女孩",
        ]
        assert [list(row.metadata) for row in scene._rows] == [
            ["第一章・初來乍到", "櫻丘校門", "共通線"],
            ["第二章・雨後相逢", "櫻丘校門", "白若雪"],
        ]
        assert description["player_text"] == [
            "珍藏的抵達",
            "第一章・初來乍到 · 櫻丘校門 · 共通線",
            "雨中的女孩",
            "第二章・雨後相逢 · 櫻丘校門 · 白若雪",
        ]
        assert [item["scene_id"] for item in description["metadata"]] == [
            "arrival_scene",
            "meet_ruoxue",
        ]
        visible_copy = "\n".join(description["player_text"])
        for internal in (
            "arrival_scene",
            "meet_ruoxue",
            "arrival_gate_line",
            "meet_ruoxue_line",
            "campus_gate",
            "chapter_arrival",
            "chapter_ruoxue",
            "route_ruoxue",
            "npc_ruoxue",
        ):
            assert internal not in visible_copy
    finally:
        driver.quit()


@pytest.mark.parametrize(
    ("localization", "titles", "route_label"),
    [
        (
            Localization(),
            ["未命名回憶 01", "未命名回憶 02", "未命名回憶 03"],
            "共通線",
        ),
        (
            Localization.from_meta({
                "locale": {
                    "ui": {
                        "scene_replay_common_route": "Common Route",
                        "scene_replay_untitled": "Untitled Memory {index:02d}",
                    },
                },
            }),
            ["Untitled Memory 01", "Untitled Memory 02", "Untitled Memory 03"],
            "Common Route",
        ),
    ],
    ids=["zh", "en"],
)
def test_replay_unsafe_or_missing_titles_use_localized_stable_numbers(
    localization: Localization,
    titles: list[str],
    route_label: str,
) -> None:
    driver = GameDriver(pack="demo_pack")
    try:
        state = driver.app.state
        state.story.scenes.clear()
        state.story.current_scene = None
        state.story.current_line_index = 0
        state.story.played.clear()
        state.read_log.scenes.clear()
        state.read_log.lines.clear()
        state.read_log.replay_archive.clear()
        state.map = MapSystem()
        state.map.add_location(Location(
            id="campus_gate",
            name="campus_gate",
        ))
        state.meta["__pack_map_catalog__"] = state.map.model_copy(deep=True)
        scene_ids = ["blank_memory", "asset_memory", "slug_memory"]
        state.meta["__chapters__"] = ChapterManifest.from_items([{
            "id": "chapter_one",
            "title": "chapter_one",
            "route": "common",
            "scenes": scene_ids,
        }])
        authored = [
            ("blank_memory", ""),
            ("asset_memory", "assets/ui/memory_card.png"),
            ("slug_memory", "developer_facing_title"),
        ]
        for index, (scene_id, title) in enumerate(authored):
            state.story.add_scene(StoryScene(
                id=scene_id,
                title=title,
                location="campus_gate",
                lines=[Line(loc_id=f"memory_line_{index}", text="回憶。")],
            ))
        state.meta["__pack_story_catalog__"] = state.story.model_copy(deep=True)
        engine = DialogueEngine(state)
        for scene_id, _title in authored:
            assert engine.start_scene(scene_id).kind == "line"
            assert engine.next_line().kind == "end"
            state.read_log.replay_archive[scene_id].title = scene_id

        ctx = dataclasses.replace(driver.app.ctx, localization=localization)
        scene = SceneReplayScene(ctx)
        scene.enter()
        description = scene.describe()

        assert [row.title for row in scene._rows] == titles
        assert [row.metadata for row in scene._rows] == [
            (route_label,),
            (route_label,),
            (route_label,),
        ]
        visible_copy = "\n".join(description["player_text"])
        for forbidden in (
            *scene_ids,
            "assets/ui/memory_card.png",
            "developer_facing_title",
            "chapter_one",
            "campus_gate",
            "memory_line_0",
            "memory_line_1",
            "memory_line_2",
        ):
            assert forbidden not in visible_copy
    finally:
        driver.quit()


@pytest.mark.parametrize(
    "size",
    [(1280, 720), (1920, 1080), (3840, 2160)],
    ids=["720p", "1080p", "2160p"],
)
def test_replay_player_copy_scales_while_click_identity_stays_scene_id(
    size: tuple[int, int],
) -> None:
    driver = GameDriver(pack="demo_pack", screen_size=size)
    try:
        scene, scene_ids = _scene(driver, count=3, on_close=lambda: None)
        target = pygame.Surface(size, pygame.SRCALPHA)
        scene.draw(target)
        description = scene.describe()

        assert description["player_text"] == [
            "第 001 段回想場景",
            "第 002 段回想場景",
            "第 003 段回想場景",
        ]
        assert [item["scene_id"] for item in description["metadata"]] == scene_ids
        assert all(
            scene_id not in "\n".join(description["player_text"])
            for scene_id in scene_ids
        )

        second_rect, second_id = scene._row_rects[1]
        assert second_id == scene_ids[1]
        scene.update(
            0.0,
            InputState(mouse_clicked=True, mouse_pos=second_rect.center),
        )
        assert scene.describe()["replay_scene_id"] == scene_ids[1]
    finally:
        driver.quit()


def test_legacy_read_scene_without_a_complete_trace_fails_closed() -> None:
    driver = GameDriver(pack="demo_pack")
    try:
        state = driver.app.state
        state.story.scenes.clear()
        state.read_log.scenes.clear()
        state.read_log.replay_archive.clear()
        state.story.add_scene(
            StoryScene(
                id="legacy_memory",
                title="舊存檔回想",
                lines=[Line(text="沒有 presentation trace")],
            ),
        )
        state.read_log.mark_scene_done("legacy_memory")

        scene = SceneReplayScene(driver.app.ctx)
        scene.enter()

        assert scene.describe()["total"] == 0
        assert scene.describe()["replayable"] == []
        assert scene._start_replay("legacy_memory") is False
        assert scene.describe()["replaying"] is False
    finally:
        driver.quit()


def test_player_replay_is_driven_by_the_archived_trace_adapter() -> None:
    driver = GameDriver(pack="demo_pack")
    try:
        scene, scene_ids = _scene(driver, count=1)
        assert scene._start_replay(scene_ids[0]) is True

        assert scene._replay_ctx is not None
        assert isinstance(
            scene._replay_ctx.dialogue,
            PresentationTraceDialogueEngine,
        )
        assert scene._replay_ctx.state is not driver.app.state
    finally:
        driver.quit()


def test_500_rows_are_virtualized_without_a_full_content_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    size = (1280, 720)
    driver = GameDriver(pack="demo_pack", screen_size=size)
    try:
        scene, _scene_ids = _scene(driver, count=500)
        assert scene._content_height > size[1] * 30
        assert not hasattr(scene, "_scroll")

        real_surface = pygame.Surface
        allocations: list[tuple[int, int]] = []

        def bounded_surface(dimensions, *args, **kwargs):
            dimensions = tuple(int(value) for value in dimensions)
            allocations.append(dimensions)
            assert dimensions[0] <= size[0]
            assert dimensions[1] <= size[1]
            return real_surface(dimensions, *args, **kwargs)

        target = real_surface(size, pygame.SRCALPHA)
        monkeypatch.setattr(pygame, "Surface", bounded_surface)
        scene.draw(target)

        description = scene.describe()
        visible_capacity = math.ceil(
            scene._viewport.height
            / (scene._row_height + scene._row_gap),
        )
        assert description["drawn_rows"] <= visible_capacity + 4
        assert description["drawn_rows"] < 20
        assert all(height != scene._content_height for _width, height in allocations)
        retained = [scene._chip_surface, *scene._stock_card_cache.values()]
        assert all(
            surface is None
            or (
                surface.get_width() <= scene._viewport.width
                and surface.get_height() <= scene._row_height
            )
            for surface in retained
        )
    finally:
        driver.quit()


def test_keyboard_focus_pages_auto_scrolls_and_confirm_replays() -> None:
    driver = GameDriver(pack="demo_pack", screen_size=(640, 360))
    try:
        scene, scene_ids = _scene(driver, count=40)
        assert scene.describe()["focus"]["scene_id"] == scene_ids[0]

        scene.update(0.0, _key(pygame.K_PAGEDOWN))
        after_page = scene.describe()
        assert after_page["scroll"]["y"] > 0
        assert after_page["focus"]["visible"] is True

        expected = after_page["focus"]["scene_id"]
        scene.update(0.0, _key(pygame.K_RETURN))
        assert scene.describe()["replaying"] is True
        assert scene.describe()["replay_scene_id"] == expected
    finally:
        driver.quit()


def test_tab_close_focus_and_confirm_are_fully_operable() -> None:
    driver = GameDriver(pack="demo_pack")
    try:
        closed: list[str] = []
        scene, _scene_ids = _scene(
            driver,
            count=4,
            on_close=lambda: closed.append("close"),
        )
        scene.update(0.0, _action(InputAction.FOCUS_PREVIOUS))
        assert scene.describe()["focus"] == {"target": "close"}
        assert scene.close_btn.focused is True

        scene.update(0.0, _action(InputAction.CONFIRM_ADVANCE))
        assert closed == ["close"]
        assert scene.describe()["replaying"] is False
    finally:
        driver.quit()


def test_live_remapped_keyboard_bindings_drive_and_label_the_scene() -> None:
    driver = GameDriver(pack="demo_pack")
    try:
        overrides = {
            InputAction.NAV_DOWN: ["j"],
            InputAction.CONFIRM_ADVANCE: ["c"],
            InputAction.BACK: ["v"],
        }
        driver.app.config.input_bindings = overrides
        scene, scene_ids = _scene(driver, count=6)
        router = InputRouter(binding_overrides=overrides)

        down = router.collect([
            pygame.event.Event(
                pygame.KEYDOWN,
                key=pygame.K_j,
                mod=0,
                unicode="j",
            ),
        ])
        scene.update(0.0, down)
        assert scene.describe()["focus"]["scene_id"] == scene_ids[1]

        router.collect([
            pygame.event.Event(pygame.KEYUP, key=pygame.K_j, mod=0),
        ])
        confirm = router.collect([
            pygame.event.Event(
                pygame.KEYDOWN,
                key=pygame.K_c,
                mod=0,
                unicode="c",
            ),
        ])
        scene.update(0.0, confirm)
        assert scene.describe()["replay_scene_id"] == scene_ids[1]
        assert "V" in scene.close_btn.label
    finally:
        driver.quit()


def test_gamepad_dpad_a_and_b_cover_selection_replay_and_back() -> None:
    driver = GameDriver(pack="demo_pack")
    try:
        closed: list[str] = []
        scene, scene_ids = _scene(
            driver,
            count=5,
            on_close=lambda: closed.append("close"),
        )
        scene.update(0.0, _hat(0, -1))
        assert scene.describe()["focus"]["scene_id"] == scene_ids[1]

        scene.update(0.0, _gamepad_button(0))
        assert scene.describe()["replay_scene_id"] == scene_ids[1]

        scene.update(0.0, _gamepad_button(1))
        assert scene.describe()["replaying"] is False
        assert closed == []
        scene.update(0.0, _gamepad_button(1))
        assert closed == ["close"]
    finally:
        driver.quit()


def test_pointer_hover_click_wheel_and_close_event_consumption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = GameDriver(pack="demo_pack", screen_size=(640, 360))
    try:
        closed: list[str] = []
        scene, scene_ids = _scene(
            driver,
            count=40,
            on_close=lambda: closed.append("close"),
        )
        target = pygame.Surface((640, 360), pygame.SRCALPHA)
        scene.draw(target)
        second = next(
            rect for rect, scene_id in scene._row_rects
            if scene_id == scene_ids[1]
        )
        scene.update(
            0.0,
            InputState(
                events=[pygame.event.Event(
                    pygame.MOUSEMOTION,
                    pos=second.center,
                    rel=(1, 0),
                    buttons=(False, False, False),
                )],
                mouse_pos=second.center,
            ),
        )
        assert scene.describe()["focus"]["scene_id"] == scene_ids[1]

        scene.update(
            0.0,
            InputState(
                mouse_wheel=-1,
                mouse_pos=scene._viewport.center,
            ),
        )
        assert scene.describe()["scroll"]["y"] > 0
        assert scene.describe()["focus"]["visible"] is True

        starts: list[str] = []
        monkeypatch.setattr(
            scene,
            "_start_replay",
            lambda scene_id: starts.append(scene_id) or True,
        )
        scene.update(
            0.0,
            InputState(
                mouse_clicked=True,
                mouse_pos=scene._close_rect.center,
            ),
        )
        assert closed == ["close"]
        assert starts == []
    finally:
        driver.quit()


def test_pointer_click_replays_the_current_visible_row() -> None:
    driver = GameDriver(pack="demo_pack")
    try:
        scene, scene_ids = _scene(driver, count=10)
        target = pygame.Surface((1280, 720), pygame.SRCALPHA)
        scene.draw(target)
        third = next(
            rect for rect, scene_id in scene._row_rects
            if scene_id == scene_ids[2]
        )

        scene.update(
            0.0,
            InputState(mouse_clicked=True, mouse_pos=third.center),
        )

        assert scene.describe()["replay_scene_id"] == scene_ids[2]
    finally:
        driver.quit()


def test_touch_tap_replays_but_drag_scroll_never_activates() -> None:
    driver = GameDriver(pack="demo_pack", screen_size=(640, 360))
    try:
        scene, _scene_ids = _scene(driver, count=40)
        point = scene._viewport.center
        scene.update(
            0.0,
            InputState(
                touch_started=True,
                touch_active=True,
                touch_mode=True,
                mouse_pos=point,
            ),
        )
        scene.update(
            0.0,
            InputState(
                touch_active=True,
                touch_mode=True,
                touch_delta=(0, -140),
                mouse_pos=(point[0], point[1] - 140),
            ),
        )
        scene.update(
            0.0,
            InputState(
                touch_released=True,
                touch_mode=True,
                mouse_clicked=False,
                mouse_pos=(point[0], point[1] - 140),
            ),
        )
        assert scene.describe()["scroll"]["y"] > 0
        assert scene.describe()["replaying"] is False

        target = pygame.Surface((640, 360), pygame.SRCALPHA)
        scene.draw(target)
        rect, expected = scene._row_rects[len(scene._row_rects) // 2]
        scene.update(
            0.0,
            InputState(
                touch_released=True,
                touch_mode=True,
                mouse_clicked=True,
                mouse_pos=rect.center,
            ),
        )
        assert scene.describe()["replay_scene_id"] == expected
    finally:
        driver.quit()


def test_reduced_motion_keeps_an_immediate_core_owned_focus_ring() -> None:
    driver = GameDriver(pack="demo_pack")
    try:
        driver.app.config.reduce_motion = True
        scene, _scene_ids = _scene(driver, count=5)
        assert scene.ctx.motion.reduced is True
        target = pygame.Surface((1280, 720), pygame.SRCALPHA)
        scene.draw(target)
        focused = scene._row_screen_rect(scene._rows[scene._focus])
        pixel = target.get_at((focused.centerx, focused.top + 1))

        assert tuple(pixel[:3]) == tuple(scene.ctx.theme.accent_warm[:3])
    finally:
        driver.quit()


class _RecordingChrome:
    def __init__(self) -> None:
        self.calls: list[ChromeRequest] = []

    def paint(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        request: ChromeRequest,
    ) -> bool:
        assert request.size == rect.size
        self.calls.append(request)
        return False


def test_scene_replay_requests_finite_pack_chrome_roles_with_stock_fallback() -> None:
    driver = GameDriver(pack="demo_pack")
    try:
        _install_scenes(driver, 8)
        stock_scene = SceneReplayScene(driver.app.ctx)
        stock_scene.enter()
        stock = pygame.Surface((1280, 720), pygame.SRCALPHA)
        stock_scene.draw(stock)

        chrome = _RecordingChrome()
        themed_ctx = dataclasses.replace(driver.app.ctx, ui_chrome=chrome)
        themed_scene = SceneReplayScene(themed_ctx)
        themed_scene.enter()
        fallback = pygame.Surface((1280, 720), pygame.SRCALPHA)
        themed_scene.draw(fallback)

        assert {
            "backdrop",
            "panel",
            "divider",
            "button",
            "card",
            "footer",
            "focus",
        } <= {request.part for request in chrome.calls}
        assert {request.screen for request in chrome.calls} == {"scene_replay"}
        assert all(
            request.size[0] > 0
            and request.size[1] > 0
            and request.density > 0
            for request in chrome.calls
        )
        assert pygame.image.tobytes(stock, "RGBA") == pygame.image.tobytes(
            fallback,
            "RGBA",
        )
    finally:
        driver.quit()


def test_describe_exposes_focus_visible_scroll_and_bounded_draw_state() -> None:
    driver = GameDriver(pack="demo_pack", screen_size=(640, 360))
    try:
        scene, scene_ids = _scene(driver, count=30)
        target = pygame.Surface((640, 360), pygame.SRCALPHA)
        scene.draw(target)
        scene.update(0.0, _action(InputAction.PAGE_DOWN))
        scene.draw(target)
        description = scene.describe()

        assert description["total"] == 30
        assert description["replayable"] == scene_ids
        assert description["focus"]["target"] == "scene"
        assert description["focus"]["visible"] is True
        assert description["focus"]["scene_id"] in description[
            "visible_scene_ids"
        ]
        assert description["scroll"]["y"] > 0
        assert description["scroll"]["limit"] > 0
        assert description["viewport"] == {
            "x": scene._viewport.x,
            "y": scene._viewport.y,
            "width": scene._viewport.width,
            "height": scene._viewport.height,
        }
        assert 0 < description["drawn_rows"] < description["total"]
    finally:
        driver.quit()
