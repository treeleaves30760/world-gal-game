"""Keyboard, gamepad, pointer, and touch access for destination cards."""
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
from world_gal_game.scenes.destination_picker import (
    DestinationPickerScene,
    _REGION_HEADER_H,
)
from world_gal_game.ui.input import InputState


@pytest.fixture(scope="module")
def driver():
    from world_gal_game.dev.driver import GameDriver

    instance = GameDriver(pack="demo_pack")
    instance.new_game()
    instance.skip_dialogue(max_frames=800)
    # The square has three exits, which gives the picker a real card row to
    # navigate without coupling these scene-level tests to app transitions.
    instance.app.state.map.move_to("town_square")
    yield instance
    instance.quit()


@pytest.fixture
def picker(driver):
    moved: list[str] = []
    closed: list[bool] = []
    scene = DestinationPickerScene(driver.app.ctx)
    scene.enter(
        on_close=lambda: closed.append(True),
        on_move_to=moved.append,
    )
    assert len(scene._cards) >= 3
    return scene, moved, closed


def _key(key: int) -> InputState:
    event = pygame.event.Event(
        pygame.KEYDOWN, {"key": key, "mod": 0, "unicode": ""}
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


def _finger(x: float, y: float) -> pygame.event.Event:
    return pygame.event.Event(pygame.FINGERDOWN, {
        "x": x,
        "y": y,
        "dx": 0.0,
        "dy": 0.0,
        "finger_id": 0,
        "touch_id": 0,
        "pressure": 1.0,
    })


def test_detail_only_quotes_exactly_the_forced_destination_once(
    driver,
    monkeypatch,
) -> None:
    ctx = driver.app.ctx
    original_meta = ctx.meta
    ctx.state.map.move_to("town_square")
    before = ctx.state.model_dump(mode="json")
    plan_calls = 0
    full_clone_calls = 0
    preview_clone_calls = 0
    original_plan = TravelService.plan
    original_full_clone = travel_module._clone_state
    original_preview_clone = travel_module._clone_preview_state

    def count_plan(*args, **kwargs):
        nonlocal plan_calls
        plan_calls += 1
        return original_plan(*args, **kwargs)

    def count_full_clone(*args, **kwargs):
        nonlocal full_clone_calls
        full_clone_calls += 1
        return original_full_clone(*args, **kwargs)

    def count_preview_clone(*args, **kwargs):
        nonlocal preview_clone_calls
        preview_clone_calls += 1
        return original_preview_clone(*args, **kwargs)

    try:
        ctx.meta = {}
        monkeypatch.setattr(
            TravelService,
            "plan",
            staticmethod(count_plan),
        )
        monkeypatch.setattr(
            travel_module,
            "_clone_state",
            count_full_clone,
        )
        monkeypatch.setattr(
            travel_module,
            "_clone_preview_state",
            count_preview_clone,
        )

        detail = DestinationPickerScene(ctx)
        detail.enter(
            initial_destination="park",
            detail_only=True,
            on_close=lambda: None,
            on_travel=lambda _plan: None,
        )

        assert detail.describe()["selected"] == "park"
        assert {dest.loc.id for dest in detail._dests} == {"park"}
        assert plan_calls == 1
        assert full_clone_calls == 0
        assert preview_clone_calls == 1
        assert ctx.state.model_dump(mode="json") == before

        # Unknown/current/hidden marker ids are rejected before a quote or
        # condition-preview clone is attempted.
        plan_calls = 0
        full_clone_calls = 0
        preview_clone_calls = 0
        invalid = DestinationPickerScene(ctx)
        invalid.enter(
            initial_destination="missing",
            detail_only=True,
            on_close=lambda: None,
        )
        assert invalid._dests == []
        assert plan_calls == 0
        assert full_clone_calls == 0
        assert preview_clone_calls == 0
        assert ctx.state.model_dump(mode="json") == before
    finally:
        ctx.meta = original_meta


def test_arrow_keys_traverse_cards_and_confirm_executes(picker) -> None:
    scene, moved, _closed = picker
    card_ids = [dest.loc.id for dest, _button in scene._cards]

    # The first direction establishes focus without skipping the first card.
    scene.update(0.016, _key(pygame.K_RIGHT))
    assert scene.focused_card_id == card_ids[0]
    assert scene.describe()["focus"]["card"] == card_ids[0]

    scene.update(0.016, _key(pygame.K_RIGHT))
    assert scene.focused_card_id == card_ids[1]
    scene.update(0.016, _key(pygame.K_LEFT))
    assert scene.focused_card_id == card_ids[0]

    scene.update(0.016, _key(pygame.K_RETURN))
    assert scene.describe()["selected"] == card_ids[0]
    assert scene.focused_preview_index == 1
    assert scene.describe()["focus"]["preview"] == "取消"

    scene.update(0.016, _key(pygame.K_LEFT))
    assert scene.focused_preview_index == 0
    scene.update(0.016, _key(pygame.K_RETURN))
    assert moved == [card_ids[0]]


def test_preview_buttons_navigate_and_cancel_returns_to_cards(picker) -> None:
    scene, _moved, closed = picker

    scene.update(0.016, _key(pygame.K_DOWN))
    focused = scene.focused_card_id
    scene.update(0.016, _key(pygame.K_RETURN))
    assert scene.focused_preview_index == 1

    scene.update(0.016, _key(pygame.K_LEFT))
    assert scene.focused_preview_index == 0
    scene.update(0.016, _key(pygame.K_RIGHT))
    assert scene.focused_preview_index == 1
    scene.update(0.016, _key(pygame.K_RETURN))
    assert scene.describe()["selected"] is None
    assert scene.focused_card_id == focused
    assert closed == []

    # Escape/B closes one layer at a time: preview first, picker second.
    scene.update(0.016, _key(pygame.K_RETURN))
    scene.update(0.016, _key(pygame.K_ESCAPE))
    assert scene.describe()["selected"] is None
    assert closed == []
    scene.update(0.016, _key(pygame.K_ESCAPE))
    assert closed == [True]


def test_gamepad_dpad_confirm_and_cancel_match_keyboard(picker) -> None:
    scene, _moved, closed = picker
    first_id = scene._cards[0][0].loc.id

    scene.update(0.016, _hat(1, 0))
    assert scene.focused_card_id == first_id
    scene.update(0.016, _button(0))  # A / cross
    assert scene.describe()["selected"] == first_id
    assert scene.focused_preview_index == 1

    scene.update(0.016, _hat(-1, 0))
    assert scene.focused_preview_index == 0
    scene.update(0.016, _hat(1, 0))
    assert scene.focused_preview_index == 1
    scene.update(0.016, _button(1))  # B / circle
    assert scene.describe()["selected"] is None
    assert closed == []


def test_keyboard_and_gamepad_can_focus_and_activate_header_filters(
    picker,
) -> None:
    scene, _moved, _closed = picker
    assert scene.describe()["focus"]["header"] is None
    reachable_before = scene.describe()["filters"]["reachable"]

    # Tab first focuses close, then the reachable filter in this fixture.
    scene.update(0.016, _key(pygame.K_TAB))
    assert scene.describe()["focus"]["header"] == "關閉 (Esc)"
    scene.update(0.016, _key(pygame.K_TAB))
    assert scene.describe()["focus"]["header"] == "只看現在可去"
    scene.update(0.016, _key(pygame.K_RETURN))
    assert scene.describe()["filters"]["reachable"] is not reachable_before
    assert scene.describe()["focus"]["header"] == "只看現在可去"

    # Controller Y / triangle synthesizes Tab; D-pad down returns to cards.
    scene.update(0.016, _button(3))
    assert scene.describe()["focus"]["header"] == "只看有角色"
    scene.update(0.016, _hat(0, -1))
    assert scene.describe()["focus"]["header"] is None
    assert scene.focused_card_id is not None


def test_mouse_and_touch_card_activation_are_preserved(picker, driver) -> None:
    scene, _moved, _closed = picker
    first = scene._cards[0][0]
    rect = scene._card_screen_rect(first)

    scene.update(0.016, InputState(
        mouse_pos=rect.center,
        mouse_clicked=True,
    ))
    assert scene.describe()["selected"] == first.loc.id

    scene._close_preview()
    sw, sh = driver.app.ctx.screen_size
    touch = InputState.collect(
        [_finger(rect.centerx / sw, rect.centery / sh)],
        transform=lambda point: (int(point[0]), int(point[1])),
        window_size=(sw, sh),
    )
    scene.update(0.016, touch)
    assert scene.describe()["selected"] == first.loc.id


def test_focus_is_drawn_and_all_picker_targets_are_at_least_44px(
    picker, driver,
) -> None:
    scene, _moved, _closed = picker
    assert _REGION_HEADER_H >= 44
    assert all(button.rect.height >= 44 for button in scene._buttons)
    assert all(dest.crect.height >= 44 for dest, _button in scene._cards)

    scene.update(0.016, _key(pygame.K_RIGHT))
    focused = scene._cards[0][0]
    card_rect = scene._card_screen_rect(focused)
    surface = pygame.Surface(driver.app.ctx.screen_size)
    scene.draw(surface)
    ring = card_rect.inflate(-2, -2)
    assert surface.get_at((ring.centerx, ring.top))[:3] == (
        scene.ctx.theme.accent_warm[:3]
    )

    scene.update(0.016, _key(pygame.K_RETURN))
    assert all(button.rect.height >= 44 for button in scene._preview_buttons)
    surface.fill((0, 0, 0))
    scene.draw(surface)
    preview_ring = scene._preview_buttons[1].rect.inflate(-2, -2)
    assert surface.get_at((preview_ring.centerx, preview_ring.top))[:3] == (
        scene.ctx.theme.accent_warm[:3]
    )


def test_graph_scope_lists_and_confirms_a_distant_destination(driver) -> None:
    state = driver.app.state
    original_map = state.map.model_copy(deep=True)
    original_travel = driver.app.ctx.meta.get("travel")
    moved: list[str] = []
    quoted = []
    try:
        state.map.locations.clear()
        state.map.visited.clear()
        state.map.current_location_id = None
        state.map.add_location(Location(
            id="a",
            name="甲地",
            exits=[Exit(target="b", travel_cost=1)],
        ))
        state.map.add_location(Location(
            id="b",
            name="中繼",
            exits=[Exit(target="c", travel_cost=1)],
        ))
        state.map.add_location(Location(id="c", name="遠方"))
        state.map.move_to("a")
        driver.app.ctx.meta["travel"] = {"scope": "graph"}

        scene = DestinationPickerScene(driver.app.ctx)
        scene.enter(on_move_to=moved.append, on_travel=quoted.append)
        distant = next(d for d in scene._dests if d.loc.id == "c")

        assert distant.available is True
        assert distant.plan is not None
        assert distant.plan.location_ids == ("a", "b", "c")
        assert distant.cost == 2

        scene._open_preview("c")
        scene._confirm()
        assert moved == []
        assert quoted == [distant.plan]
    finally:
        state.map = original_map
        if original_travel is None:
            driver.app.ctx.meta.pop("travel", None)
        else:
            driver.app.ctx.meta["travel"] = original_travel


def test_guided_picker_defaults_to_story_offers_and_future_wait_action(
    driver,
) -> None:
    ctx = driver.app.ctx
    original_state = ctx.state
    original_meta = ctx.meta
    waits: list[bool] = []
    quoted: list[object] = []
    try:
        state = GameState()
        state.map.add_location(Location(
            id="start",
            name="起點",
            exits=[
                Exit(target="current"),
                Exit(target="future"),
                Exit(target="neutral"),
            ],
        ))
        state.map.add_location(Location(
            id="current",
            name="現在故事",
            scene_hooks=[SceneHook(
                scene_id="current_scene",
                trigger="auto",
                requires_time=["noon"],
            )],
        ))
        state.map.add_location(Location(
            id="future",
            name="稍後故事",
            scene_hooks=[SceneHook(
                scene_id="future_scene",
                trigger="enter",
                requires_time=["afternoon"],
            )],
        ))
        state.map.add_location(Location(id="neutral", name="一般地點"))
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
                },
            },
        }

        picker = DestinationPickerScene(ctx)
        picker.enter(
            on_wait=lambda: waits.append(True),
            on_travel=quoted.append,
        )

        assert {d.loc.id for d in picker._dests} == {"current", "future"}
        described = picker.describe()["guided_offers"]
        assert described["current"]["status"] == "current"
        assert described["future"]["status"] == "future"
        assert described["future"]["future"]["wait_phases"] == 1

        picker._open_preview("future")
        assert picker.describe()["focus"]["preview"] == "取消"
        picker._move_preview_focus((-1, 0))
        assert picker.describe()["focus"]["preview"] == (
            "等待至第 1 天 中午（共 1 時段）"
        )
        assert picker._activate_preview_focus() is True
        assert waits == [True]
        # A future quote belongs to the projected state and must never be sent
        # down the travel commit callback from the current state.
        assert quoted == []

        # The same action derives a longer wait from the future quote, then
        # invokes the legacy one-phase callback once per phase. Pointer and
        # keyboard activation both route through the same Button callback.
        future_hook = state.map.locations["future"].scene_hooks[0]
        future_hook.requires_time = ["night"]
        waits.clear()

        def advance_one_phase() -> bool:
            waits.append(True)
            state.time.advance(1)
            return False

        picker.on_wait = advance_one_phase
        picker._rebuild()
        future = next(d for d in picker._dests if d.loc.id == "future")
        assert future.offer is not None
        assert future.offer.future is not None
        assert future.offer.future.wait_phases == 3
        picker._open_preview("future")
        assert picker._preview_buttons[0].label == (
            "等待至第 1 天 傍晚（共 3 時段）"
        )
        action = picker._preview_buttons[0]
        picker.update(0.016, InputState(
            mouse_pos=action.rect.center,
            mouse_clicked=True,
        ))
        assert waits == [True, True, True]
        assert state.time.time_of_day.value == "evening"
        assert (
            picker.describe()["guided_offers"]["future"]["action"]
            == "travel"
        )

        # Restore the opening moment for the filter/locked-state assertions
        # below; rebuilding presentation never mutates this clock itself.
        state.time.phase_index = 0
        picker._rebuild()

        picker._toggle_guided_all()
        assert {d.loc.id for d in picker._dests} == {
            "current",
            "future",
            "neutral",
        }
        assert (
            picker.describe()["guided_offers"]["neutral"]["visibility"]
            == "secondary"
        )

        # Primary offers remain visible even under restrictive convenience
        # filters.  In particular, locking the wait action must not make the
        # only explained story marker disappear.
        picker._toggle_guided_all()
        state.events.set_flag("cannot_wait", True)
        ctx.meta["exploration_guidance"] = {
            "advance_time": {
                "forbids_flags": ["cannot_wait"],
                "locked_reason": "先完成眼前事件。",
            },
        }
        picker._filter_reachable = True
        picker._filter_with_npc = True
        picker._rebuild()
        locked = next(d for d in picker._dests if d.loc.id == "future")
        assert locked.offer is not None
        assert locked.offer.status == "locked"
        assert locked.available is False
        picker._open_preview("future")
        assert picker._preview_buttons[0].enabled is False
        assert picker.describe()["focus"]["preview"] == "取消"
    finally:
        ctx.state = original_state
        ctx.meta = original_meta


def test_future_wait_stops_at_first_intermediate_ambient_scene(
    driver,
    monkeypatch,
) -> None:
    """A multi-phase CTA cannot jump over an event at the current location."""
    app = driver.app
    ctx = app.ctx
    original_state = app.state
    original_meta = app.meta
    started: list[str] = []
    try:
        state = GameState()
        state.map.add_location(Location(
            id="start",
            name="起點",
            exits=[Exit(target="future")],
            scene_hooks=[SceneHook(
                scene_id="intermediate_scene",
                trigger="auto",
                requires_time=["noon"],
            )],
        ))
        state.map.add_location(Location(
            id="future",
            name="稍後故事",
            scene_hooks=[SceneHook(
                scene_id="future_scene",
                trigger="enter",
                requires_time=["night"],
            )],
        ))
        for scene_id in ("intermediate_scene", "future_scene"):
            state.story.add_scene(Scene(
                id=scene_id,
                title=scene_id,
                lines=[Line(text=scene_id)],
            ))
        state.map.move_to("start")
        meta = {
            "travel": {
                "scope": "graph",
                "minimum_cost": 1,
                "guided_story": {
                    "future_horizon_phases": 6,
                    "max_primary": 3,
                },
            },
        }
        app.state = state
        app.meta = meta
        ctx.state = state
        ctx.meta = meta
        monkeypatch.setattr(app, "_start_dialogue", started.append)

        picker = DestinationPickerScene(ctx)
        picker.enter(on_wait=app._advance_time)
        future = next(d for d in picker._dests if d.loc.id == "future")
        assert future.offer is not None
        assert future.offer.future is not None
        assert future.offer.future.wait_phases == 3

        picker._open_preview("future")
        picker._move_preview_focus((-1, 0))
        assert picker._activate_preview_focus() is True

        assert state.time.time_of_day.value == "noon"
        assert started == ["intermediate_scene"]
        refreshed = picker.describe()["guided_offers"]["future"]
        assert refreshed["future"]["wait_phases"] == 2
    finally:
        app.state = original_state
        app.meta = original_meta
        ctx.state = original_state
        ctx.meta = original_meta
