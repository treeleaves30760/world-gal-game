"""Keyboard, gamepad, pointer, and focus access for the world map."""
from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

import world_gal_game.core.travel as travel_module
from world_gal_game.core.game_state import GameState
from world_gal_game.core.map_system import Exit, Location, SceneHook
from world_gal_game.core.story_graph import Line, Scene
from world_gal_game.core.travel import TravelService
from world_gal_game.scenes.map_scene import MapScene
from world_gal_game.ui.input import InputState


@pytest.fixture(scope="module")
def driver():
    from world_gal_game.dev.driver import GameDriver

    instance = GameDriver(pack="demo_pack")
    instance.new_game()
    instance.skip_dialogue(max_frames=800)
    yield instance
    instance.quit()


@pytest.fixture
def world_map(driver):
    moved: list[str] = []
    closed: list[bool] = []
    driver.app.state.map.move_to("town_square")
    scene = MapScene(driver.app.ctx)
    scene.enter(
        on_close=lambda: closed.append(True),
        on_move_to=moved.append,
    )
    return scene, moved, closed


def _key(key: int) -> InputState:
    event = pygame.event.Event(
        pygame.KEYDOWN,
        {"key": key, "mod": 0, "unicode": ""},
    )
    return InputState.collect([event])


def _hat(x: int, y: int) -> InputState:
    event = pygame.event.Event(
        pygame.JOYHATMOTION,
        {"joy": 0, "instance_id": 0, "hat": 0, "value": (x, y)},
    )
    return InputState.collect([event])


def _button(index: int) -> InputState:
    event = pygame.event.Event(
        pygame.JOYBUTTONDOWN,
        {"joy": 0, "instance_id": 0, "button": index},
    )
    return InputState.collect([event])


def test_overview_reachability_never_builds_full_travel_quotes(
    driver,
    monkeypatch,
) -> None:
    ctx = driver.app.ctx
    original_meta = ctx.meta
    ctx.state.map.move_to("town_square")
    before = ctx.state.model_dump(mode="json")
    route_calls = 0
    original_route = TravelService.route

    def count_route(*args, **kwargs):
        nonlocal route_calls
        route_calls += 1
        return original_route(*args, **kwargs)

    def forbidden(*_args, **_kwargs):
        raise AssertionError(
            "map overview reachability must not build arrival quotes"
        )

    try:
        # The demo pack has no guided layer; this isolates the ordinary
        # reachability pass whose work must stay independent of preview hooks.
        ctx.meta = {}
        monkeypatch.setattr(
            TravelService,
            "route",
            staticmethod(count_route),
        )
        monkeypatch.setattr(
            TravelService,
            "plan",
            staticmethod(forbidden),
        )
        monkeypatch.setattr(travel_module, "_clone_state", forbidden)
        monkeypatch.setattr(
            travel_module,
            "_clone_preview_state",
            forbidden,
        )

        scene = MapScene(ctx)
        scene.enter(on_close=lambda: None, on_move_to=lambda _target: None)

        assert route_calls > 0
        assert scene.describe()["reachable"]
        assert ctx.state.model_dump(mode="json") == before
    finally:
        ctx.meta = original_meta


def test_arrow_and_wasd_navigation_follow_geometry_and_wrap(world_map) -> None:
    scene, _moved, _closed = world_map

    # The initial anchor is the player's current location. Right selects the
    # visually aligned park rather than the more steeply diagonal alley.
    scene.update(0.016, _key(pygame.K_RIGHT))
    assert scene.map_view is not None
    assert scene.map_view.focused_id == "park"
    assert scene.describe()["focus"]["node"] == "park"

    # Left picks the nearest aligned node, excluding the current marker.
    scene.update(0.016, _key(pygame.K_a))
    assert scene.map_view.focused_id == "starting_room"

    # Moving farther left wraps deterministically to the opposite edge.
    scene.update(0.016, _key(pygame.K_LEFT))
    assert scene.map_view.focused_id == "park"

    # Starting a fresh map and moving down selects the lower map node.
    scene.enter(on_close=lambda: None, on_move_to=lambda _loc_id: None)
    scene.update(0.016, _key(pygame.K_s))
    assert scene.map_view.focused_id == "shop_alley"


@pytest.mark.parametrize("confirm_key", [pygame.K_RETURN, pygame.K_SPACE])
def test_confirm_requests_preview_without_moving(
    world_map,
    confirm_key: int,
) -> None:
    scene, moved, _closed = world_map
    current_before = scene.ctx.state.map.current_location_id

    scene.update(0.016, _key(pygame.K_RIGHT))
    focused = scene.map_view.focused_id
    scene.update(0.016, _key(confirm_key))

    assert moved == [focused]
    assert focused != current_before
    # MapScene only requests the existing preview/confirmation route. It does
    # not mutate map state or consume time itself.
    assert scene.ctx.state.map.current_location_id == current_before


def test_gamepad_dpad_a_and_b_match_keyboard(world_map) -> None:
    scene, moved, closed = world_map

    scene.update(0.016, _hat(0, -1))
    assert scene.map_view is not None
    assert scene.map_view.focused_id == "shop_alley"

    scene.update(0.016, _button(0))
    assert moved == ["shop_alley"]

    scene.update(0.016, _button(1))
    assert closed == [True]


def test_pointer_motion_synchronises_persistent_focus(world_map) -> None:
    scene, _moved, _closed = world_map
    assert scene.map_view is not None
    target_rect = scene.map_view._node_rects["starting_room"]
    motion = pygame.event.Event(
        pygame.MOUSEMOTION,
        {"pos": target_rect.center, "rel": (1, 0), "buttons": (0, 0, 0)},
    )

    scene.update(0.016, InputState(
        events=[motion],
        mouse_pos=target_rect.center,
    ))
    assert scene.map_view.focused_id == "starting_room"

    # A frame with the pointer elsewhere but no motion does not erase focus.
    scene.update(0.016, InputState(mouse_pos=(0, 0)))
    assert scene.map_view.focused_id == "starting_room"


def test_clicking_current_marker_is_informational_not_an_implicit_close(
    world_map,
) -> None:
    scene, moved, closed = world_map
    current_id = scene.ctx.state.map.current_location_id
    assert current_id is not None

    scene._on_node_click(current_id)

    assert moved == []
    assert closed == []


def test_only_destination_keeps_focus_when_navigation_wraps(
    world_map,
) -> None:
    scene, _moved, _closed = world_map
    assert scene.map_view is not None
    scene.map_view.set_data(
        nodes=[
            {"id": "origin", "map_x": 0, "map_y": 0},
            {"id": "only", "map_x": 10, "map_y": 0},
        ],
        current_id="origin",
        reachable_ids={"only"},
    )

    scene.map_view.move_focus((1, 0))
    assert scene.map_view.focused_id == "only"
    scene.map_view.move_focus((1, 0))
    assert scene.map_view.focused_id == "only"


def test_focus_ring_and_all_world_map_targets_are_at_least_44px(
    world_map,
    driver,
) -> None:
    scene, _moved, _closed = world_map
    assert scene.map_view is not None
    assert scene.close_btn is not None
    assert scene.close_btn.rect.width >= 44
    assert scene.close_btn.rect.height >= 44
    assert all(
        rect.width >= 44 and rect.height >= 44
        for rect in scene.map_view._node_rects.values()
    )
    assert all(
        scene.map_view.rect.contains(rect)
        for rect in scene.map_view._node_rects.values()
    )

    scene.update(0.016, _key(pygame.K_RIGHT))
    focused = scene.map_view.focused_id
    assert focused is not None
    rect = scene.map_view._node_rects[focused]
    surface = pygame.Surface(driver.app.ctx.screen_size)
    scene.draw(surface)
    ring = rect.inflate(-2, -2)
    assert surface.get_at((ring.centerx, ring.top))[:3] == (
        scene.ctx.theme.accent_warm[:3]
    )


def test_keyboard_and_gamepad_can_toggle_guided_map_chrome(driver) -> None:
    original_meta = driver.app.ctx.meta
    try:
        driver.app.ctx.meta = {
            "travel": {
                "scope": "graph",
                "guided_story": {
                    "future_horizon_phases": 4,
                    "max_primary": 3,
                },
            },
        }
        scene = MapScene(driver.app.ctx)
        scene.enter(on_close=lambda: None, on_move_to=lambda _target: None)
        assert scene.describe()["view_mode"] == "story_only"
        assert scene.describe()["focus"]["chrome"] is None
        assert scene.map_view is not None
        assert scene.map_view.legend_visible is False

        scene.update(0.016, _key(pygame.K_TAB))
        assert scene.describe()["focus"]["chrome"] == "顯示一般探索"
        scene.update(0.016, _key(pygame.K_RETURN))
        assert scene.describe()["view_mode"] == "all_reachable"
        focused = scene.describe()["focus"]["node"]
        assert focused is not None
        assert scene.describe()["markers"][focused] == "ordinary"
        assert focused in scene.describe()["reachable"]
        assert scene.map_view is not None
        assert scene.map_view.detail_node_id == focused
        assert scene.map_view.legend_visible is True
        assert scene.map_view.legend_rect.bottom <= scene.map_view.rect.top
        assert scene.describe()["focus"]["chrome"] is None

        # Controller Y / triangle reaches the same toggle. Confirming it hides
        # the ordinary layer and clears the now-invisible map focus.
        scene.update(0.016, _button(3))
        assert scene.describe()["focus"]["chrome"] == "只看故事地點"
        scene.update(0.016, _button(0))
        assert scene.describe()["view_mode"] == "story_only"
        assert scene.describe()["focus"]["chrome"] is None
        assert scene.describe()["focus"]["node"] is None
        assert scene.map_view.detail_node_id is None
        assert scene.map_view.legend_visible is False
    finally:
        driver.app.ctx.meta = original_meta


def test_app_world_map_confirm_opens_destination_preview_before_travel(
    driver,
) -> None:
    from world_gal_game.scenes.destination_picker import DestinationPickerScene

    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.app.state.map.move_to("town_square")
    current_before = driver.app.state.map.current_location_id

    # Use the production wiring: spatial map -> picker detail preview.
    driver.app._open_map()
    driver.advance_frames(1)
    assert isinstance(driver.app.manager.current, MapScene)

    driver.press_key(pygame.K_RIGHT)
    driver.advance_frames(1)
    world = driver.app.manager.current
    assert isinstance(world, MapScene)
    focused = world.describe()["focus"]["node"]
    assert focused is not None

    driver.press_key(pygame.K_RETURN)
    driver.advance_frames(1)
    preview = driver.app.manager.current
    assert isinstance(preview, DestinationPickerScene)
    assert preview.describe()["selected"] == focused
    assert preview.describe()["detail_only"] is True
    assert driver.app.state.map.current_location_id == current_before

    # Detail cancel reveals the same map; map close returns to exploration.
    driver.press_key(pygame.K_ESCAPE)
    driver.advance_frames(1)
    assert isinstance(driver.app.manager.current, MapScene)
    driver.press_key(pygame.K_ESCAPE)
    driver.advance_frames(1)


def test_confirmed_map_trip_consumes_once_and_opens_arrival_story(
    driver,
) -> None:
    from world_gal_game.scenes.destination_picker import DestinationPickerScene
    from world_gal_game.scenes.dialogue_scene import DialogueScene

    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    app = driver.app
    state = app.state
    state.map.move_to("town_square")
    target_id = "map_contract_destination"
    scene_id = "map_contract_arrival"
    state.story.add_scene(Scene(
        id=scene_id,
        title="Arrival contract",
        lines=[Line(text="The promised arrival story starts.")],
    ))
    state.map.current.exits.append(Exit(
        target=target_id,
        travel_cost=1,
    ))
    state.map.add_location(Location(
        id=target_id,
        name="Contract destination",
        map_x=95,
        map_y=95,
        scene_hooks=[SceneHook(
            scene_id=scene_id,
            trigger="enter",
        )],
    ))
    app.meta["travel"] = {
        "scope": "graph",
        "minimum_cost": 1,
    }
    expected_time = state.time.model_copy(deep=True)
    expected_time.advance(1)

    app._open_map()
    app.manager.commit_pending()
    app._preview_from_world_map(target_id)
    app.manager.commit_pending()
    preview = app.manager.current
    assert isinstance(preview, DestinationPickerScene)
    assert preview.describe()["selected"] == target_id
    assert [type(scene).__name__ for scene in app.manager.stack()] == [
        "ExplorationScene",
        "MapScene",
        "DestinationPickerScene",
    ]

    preview._confirm()
    app.manager.commit_pending()

    assert state.map.current_location_id == target_id
    assert state.time.model_dump() == expected_time.model_dump()
    assert state.story.current_scene == scene_id
    assert isinstance(app.manager.current, DialogueScene)
    assert [type(scene).__name__ for scene in app.manager.stack()] == [
        "ExplorationScene",
        "DialogueScene",
    ]


def test_guided_world_map_shows_only_current_and_primary_story_markers(
    driver,
) -> None:
    ctx = driver.app.ctx
    original_state = ctx.state
    original_meta = ctx.meta
    previews: list[str] = []
    try:
        state = GameState()
        state.map.add_location(Location(
            id="start",
            name="起點",
            map_x=0,
            map_y=0,
            exits=[
                Exit(target="current"),
                Exit(target="future"),
                Exit(target="neutral"),
            ],
        ))
        state.map.add_location(Location(
            id="current",
            name="現在故事",
            map_x=10,
            map_y=0,
            scene_hooks=[SceneHook(
                scene_id="current_scene",
                trigger="auto",
                requires_time=["noon"],
            )],
        ))
        state.map.add_location(Location(
            id="future",
            name="稍後故事",
            map_x=20,
            map_y=0,
            scene_hooks=[SceneHook(
                scene_id="future_scene",
                trigger="enter",
                requires_time=["afternoon"],
            )],
        ))
        state.map.add_location(Location(
            id="neutral",
            name="一般地點",
            map_x=30,
            map_y=0,
        ))
        for scene_id in ("current_scene", "future_scene"):
            state.story.add_scene(Scene(
                id=scene_id,
                title=scene_id,
                lines=[Line(text=scene_id)],
            ))
        state.map.move_to("start")
        ctx.state = state
        ctx.meta = {
            "travel": {
                "scope": "graph",
                "minimum_cost": 1,
                "guided_story": {
                    "future_horizon_phases": 6,
                    "max_primary": 3,
                    "progress": {
                        "label": "新生指南",
                        "stages": [{
                            "label": "第一輪",
                            "flags": ["met_current", "met_future"],
                        }],
                    },
                },
            },
        }

        scene = MapScene(ctx)
        scene.enter(on_close=lambda: None, on_move_to=previews.append)

        assert scene.map_view is not None
        assert {node["id"] for node in scene.map_view.nodes} == {
            "start",
            "current",
            "future",
        }
        assert scene.describe()["guided_offers"]["current"]["status"] == "current"
        assert scene.describe()["guided_offers"]["future"]["status"] == "future"
        future_offer = scene.describe()["guided_offers"]["future"]
        wait_phases = future_offer["future"]["wait_phases"]
        future_node = next(
            node for node in scene.map_view.nodes
            if node["id"] == "future"
        )
        assert (
            f"等待 {wait_phases} 時段後可出發"
            in future_node["marker_label"]
        )
        assert "稍後可安排" not in future_node["marker_label"]
        assert "顯示出發時機" in scene._hint_text()
        assert scene.describe()["guide_progress"] == {
            "label": "新生指南",
            "stage": "第一輪",
            "completed": 0,
            "total": 2,
            "stage_index": 1,
            "stage_count": 1,
            "complete": False,
        }
        assert "第一輪 0 / 2" in scene._hint_text()
        assert "neutral" not in scene.describe()["guided_offers"]
        assert scene.describe()["view_mode"] == "story_only"
        assert scene.mode_btn is not None
        assert scene.mode_btn.visible is True
        assert scene.mode_btn.rect.height >= 44

        before_toggle = state.model_dump(mode="json")
        scene._toggle_ordinary()
        assert scene.describe()["view_mode"] == "all_reachable"
        assert {node["id"] for node in scene.map_view.nodes} == {
            "start",
            "current",
            "future",
            "neutral",
        }
        assert scene.describe()["markers"]["neutral"] == "ordinary"
        assert scene.describe()["focus"]["node"] == "neutral"
        assert scene.map_view.detail_node_id == "neutral"
        assert state.model_dump(mode="json") == before_toggle

        scene._toggle_ordinary()
        assert scene.describe()["view_mode"] == "story_only"
        assert "neutral" not in {
            node["id"] for node in scene.map_view.nodes
        }
        assert scene.describe()["focus"]["node"] is None
        assert scene.map_view.detail_node_id is None

        state.events.set_flag("met_current", True)
        scene.resume()
        assert scene.describe()["guide_progress"]["completed"] == 1
        assert "第一輪 1 / 2" in scene._hint_text()

        scene.map_view._focused_id = "future"
        before = state.model_dump(mode="json")
        scene.update(0.016, _key(pygame.K_RETURN))

        assert previews == ["future"]
        assert state.model_dump(mode="json") == before
    finally:
        ctx.state = original_state
        ctx.meta = original_meta
