"""Directed-opening guidance stays consistent across GUI and agent surfaces."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pygame
import pytest


os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


@pytest.fixture
def driver():
    from world_gal_game.dev.driver import GameDriver

    instance = GameDriver(pack="demo_pack")
    yield instance
    instance.quit()


def _enter_exploration(driver):
    driver.new_game()
    driver.skip_dialogue(max_frames=800)
    driver.advance_frames(5)
    assert type(driver.app.manager.current).__name__ == "ExplorationScene"
    return driver.app.manager.current


def _install_guided_handoff_state(driver, *, auto_reopen: object = True):
    """Create an isolated, unfinished guided stage at an empty location."""
    from world_gal_game.core.map_system import Location

    app = driver.app
    state = app.state
    location = Location(id="guided_handoff_test", name="導引測試地點")
    state.map.add_location(location)
    state.map.move_to(location.id)
    app.meta["exploration_guidance"] = {}
    app.meta["travel"] = {
        "scope": "graph",
        "guided_story": {
            "auto_reopen": auto_reopen,
            "progress": {
                "label": "測試指南",
                "stages": [
                    {"label": "測試階段", "flags": ["guide_done"]},
                ],
            },
        },
    }
    return app, location


def _finish_current_authored_scene(app, scene_id: str) -> None:
    """Drive one real DialogueScene until it hands off to another surface."""
    for _ in range(30):
        dialogue = app.manager.current
        if (
            type(dialogue).__name__ != "DialogueScene"
            or dialogue.scene_id != scene_id
        ):
            return
        dialogue.box.force_reveal()
        dialogue._advance()
        app.manager.commit_pending()
    pytest.fail(f"dialogue did not finish: {scene_id}")


def test_gui_wait_action_is_visible_explained_and_noninteractive(driver):
    scene = _enter_exploration(driver)
    reason = "先去完成新生報到。"
    driver.app.meta["exploration_guidance"] = {
        "advance_time": {
            "requires_flags": ["orientation_done"],
            "locked_reason": reason,
        },
    }
    scene._rebuild_widgets()

    wait = next(
        widget for widget in driver._widget_catalogue()
        if widget["label"] == "等下個時段"
    )
    assert wait["enabled"] is False
    assert wait["has_on_click"] is False
    assert wait["tooltip"] == reason
    assert scene.describe()["objective"] == "從廣場開始"

    before = driver.app.state.time.model_dump()
    driver.click(tuple(wait["rect_center"]))
    driver.advance_frames(2)
    assert driver.app.state.time.model_dump() == before
    assert scene._hovered_action_hint is not None
    assert scene._hovered_action_hint[1] == reason

    # The callback itself is guarded too; disabling only the button would
    # leave keyboard/dev paths able to bypass the authored sequence.
    driver.app._advance_time()
    assert driver.app.state.time.model_dump() == before


def test_gui_wait_action_unlocks_when_required_flag_changes(driver):
    scene = _enter_exploration(driver)
    driver.app.meta["exploration_guidance"] = {
        "advance_time": {
            "requires_flags": ["orientation_done"],
            "locked_reason": "先完成目前目標。",
        },
    }
    scene._rebuild_widgets()
    driver.app.state.events.set_flag("orientation_done", True)
    driver.advance_frames(2)

    wait = next(
        widget for widget in driver._widget_catalogue()
        if widget["label"] == "等下個時段"
    )
    assert wait["enabled"] is True
    assert wait["has_on_click"] is True
    assert wait["tooltip"] is None


def test_gui_map_cta_shortcut_and_app_guard_share_one_policy(driver):
    from world_gal_game.scenes.map_scene import MapScene

    scene = _enter_exploration(driver)
    reason = "先完成集合，指南才會打開校園頁。"
    driver.app.meta["exploration_guidance"] = {
        "map": {
            "requires_flags": ["orientation_done"],
            "locked_reason": reason,
        },
    }
    scene._rebuild_widgets()

    assert scene._travel_btn is not None
    assert scene._travel_btn.enabled is False
    assert scene._travel_btn.tooltip == reason
    assert scene.describe()["map"] == {
        "available": False,
        "enabled": False,
        "reason": reason,
        "label": scene._travel_btn.label,
    }
    driver.move_mouse(scene._travel_btn.rect.center)
    driver.advance_frames(1)
    assert scene._travel_action_hint() == (scene._travel_btn, reason)

    stack_before = [type(item).__name__ for item in driver.app.manager.stack()]
    scene._request_map()
    driver.app.manager.commit_pending()
    assert [type(item).__name__ for item in driver.app.manager.stack()] == (
        stack_before
    )

    # The app-level guard protects menu/plugin/dev callers that bypass the
    # exploration widget and shortcut.
    driver.app._open_map()
    driver.app.manager.commit_pending()
    assert [type(item).__name__ for item in driver.app.manager.stack()] == (
        stack_before
    )
    driver.app._open_world_map()
    driver.app.manager.commit_pending()
    assert [type(item).__name__ for item in driver.app.manager.stack()] == (
        stack_before
    )
    driver.app._preview_from_world_map("square")
    driver.app.manager.commit_pending()
    assert [type(item).__name__ for item in driver.app.manager.stack()] == (
        stack_before
    )

    driver.app._open_menu()
    driver.app.manager.commit_pending()
    menu = driver.app.manager.current
    assert type(menu).__name__ == "MenuScene"
    assert menu._buttons[0].enabled is False
    assert menu._buttons[0].tooltip == reason
    driver.app.manager.pop()
    driver.app.manager.commit_pending()

    driver.app.state.events.set_flag("orientation_done", True)
    scene._rebuild_widgets()
    assert scene._travel_btn.enabled is True
    assert scene._travel_btn.tooltip is None
    scene._request_map()
    driver.app.manager.commit_pending()
    assert isinstance(driver.app.manager.current, MapScene)


def test_gui_locked_map_preserves_reachable_zero_cost_local_action(driver):
    from world_gal_game.core.map_system import Exit, Location

    scene = _enter_exploration(driver)
    reason = "先完成集合，指南才會打開校園頁。"
    driver.app.meta["exploration_guidance"] = {
        "map": {
            "requires_flags": ["orientation_done"],
            "locked_reason": reason,
        },
    }
    state = driver.app.state
    destination = Location(id="tutorial_step", name="集合地點")
    state.map.add_location(destination)
    assert state.map.current is not None
    state.map.current.exits.append(Exit(
        target=destination.id,
        travel_cost=0,
    ))
    local = next(
        (
            (exit_obj, location)
            for exit_obj, location, available, _reason
            in state.map.all_exits_with_status(
                state.events.flags,
                state.time.time_of_day.value,
            )
            if available and exit_obj.travel_cost == 0
        ),
        None,
    )
    assert local is not None
    _exit_obj, destination = local

    scene._rebuild_widgets()
    action = next(
        widget
        for widget in driver._widget_catalogue()
        if widget.get("label") == f"前往{destination.name}"
    )
    assert action["enabled"] is True
    assert scene._travel_btn is not None
    assert scene._travel_btn.enabled is False
    assert scene._travel_btn.tooltip == reason

    before_time = state.time.model_dump()
    driver.click(tuple(action["rect_center"]))
    driver.advance_frames(4)
    assert state.map.current_location_id == destination.id
    assert state.time.model_dump() == before_time


def test_flag_locked_exit_uses_authored_description_as_reason():
    from world_gal_game.core.map_system import Exit, Location, MapSystem

    map_system = MapSystem()
    map_system.add_location(Location(
        id="start",
        name="Start",
        exits=[Exit(
            target="later",
            requires_flags=["orientation_done"],
            description="先完成新生報到",
        )],
    ))
    map_system.add_location(Location(id="later", name="Later"))
    map_system.move_to("start")

    [(exit_obj, location, available, reason)] = (
        map_system.all_exits_with_status({}, "morning")
    )
    assert exit_obj.target == "later"
    assert location.id == "later"
    assert available is False
    assert reason == "先完成新生報到"


def test_pack_can_default_destination_picker_to_reachable_only(driver):
    from world_gal_game.core.map_system import Exit, Location

    scene = _enter_exploration(driver)
    state = driver.app.state
    state.map.add_location(Location(id="locked_later", name="稍後開放"))
    state.map.current.exits.append(Exit(
        target="locked_later",
        requires_flags=["orientation_done"],
        description="先完成目前目標",
    ))
    driver.app.meta["travel_default_reachable_only"] = True
    scene._rebuild_widgets()

    visible_count = len(state.map.available_exits(
        state.events.flags,
        state.time.time_of_day.value,
    ))
    travel = next(
        item for item in driver._widget_catalogue()
        if str(item.get("label") or "").startswith("外出（")
    )
    assert travel["label"] == f"外出（{visible_count} 個地點）"

    driver.click(tuple(travel["rect_center"]))
    driver.advance_frames(4)
    world_map = driver.app.manager.current
    assert type(world_map).__name__ == "MapScene"
    assert "locked_later" not in world_map.describe()["markers"]

    # The secondary list still honours the pack default when opened by a host
    # or tool for list-mode accessibility.
    from world_gal_game.scenes.destination_picker import DestinationPickerScene

    app = driver.app
    app.manager.push(
        DestinationPickerScene(app.ctx),
        on_close=app.manager.pop,
        on_move_to=app._move_from_map,
        on_travel=app._travel_from_map_plan,
        on_wait=app._advance_time,
        on_world_map=app._open_world_map,
    )
    driver.advance_frames(4)
    picker = app.manager.current
    assert picker.describe()["filters"]["reachable"] is True
    assert "locked_later" not in {
        item["id"] for item in picker.describe()["destinations"]
    }

    picker._toggle_reachable()
    assert "locked_later" in {
        item["id"] for item in picker.describe()["destinations"]
    }


def test_future_wait_closes_detail_before_ambient_dialogue_push(driver):
    """Waiting from a map preview must not pop the newly unlocked scene."""
    from world_gal_game.core.map_system import (
        Exit,
        Location,
        SceneHook,
    )
    from world_gal_game.core.story_graph import Line, Scene
    from world_gal_game.core.time_system import TimeOfDay
    from world_gal_game.scenes.destination_picker import (
        DestinationPickerScene,
    )
    from world_gal_game.scenes.dialogue_scene import DialogueScene
    from world_gal_game.scenes.map_scene import MapScene

    _enter_exploration(driver)
    app = driver.app
    state = app.state
    state.time.set_phase(TimeOfDay.MORNING)
    state.story.add_scene(Scene(
        id="wait_unlocked_ambient",
        title="等待後觸發",
        lines=[Line(text="中午到了。")],
    ))
    state.story.add_scene(Scene(
        id="wait_future_destination",
        title="稍後的目的地",
        lines=[Line(text="下午才能抵達。")],
    ))
    state.map.add_location(Location(
        id="wait_start",
        name="等待起點",
        exits=[Exit(target="wait_future", travel_cost=1)],
        scene_hooks=[SceneHook(
            scene_id="wait_unlocked_ambient",
            trigger="auto",
            requires_time=["noon"],
        )],
    ))
    state.map.add_location(Location(
        id="wait_future",
        name="稍後地點",
        scene_hooks=[SceneHook(
            scene_id="wait_future_destination",
            trigger="enter",
            requires_time=["afternoon"],
        )],
    ))
    state.map.move_to("wait_start")
    app.meta["exploration_guidance"] = {}
    app.meta["travel"] = {
        "scope": "graph",
        "minimum_cost": 1,
        "guided_story": {
            "future_horizon_phases": 2,
            "max_primary": 3,
            "require_marker": True,
            "markers": {"wait_future_destination": {}},
        },
    }

    app._open_map()
    app.manager.commit_pending()
    assert isinstance(app.manager.current, MapScene)
    app._preview_from_world_map("wait_future")
    app.manager.commit_pending()
    picker = app.manager.current
    assert isinstance(picker, DestinationPickerScene)
    assert picker.describe()["selected"] == "wait_future"
    assert (
        picker.describe()["guided_offers"]["wait_future"]["action"]
        == "wait"
    )

    picker._confirm()
    app.manager.commit_pending()

    assert state.time.time_of_day is TimeOfDay.NOON
    assert isinstance(app.manager.current, DialogueScene)
    assert state.story.current_scene == "wait_unlocked_ambient"
    assert [type(scene).__name__ for scene in app.manager.stack()] == [
        "ExplorationScene",
        "MapScene",
        "DialogueScene",
    ]


def test_headless_affordance_and_execution_share_wait_policy():
    from world_gal_game.config import EngineConfig
    from world_gal_game.headless import HeadlessSession

    session = HeadlessSession.open(EngineConfig(), pack="demo_pack")
    session.meta["exploration_guidance"] = {
        "advance_time": {
            "requires_flags": ["orientation_done"],
            "locked_reason": "先完成新生報到。",
        },
    }
    before = session.state.time.model_dump()

    assert session.affordances()["advance_time"] == {
        "available": False,
        "reason": "先完成新生報到。",
    }
    result = session.advance_time()
    assert result["ok"] is False
    assert session.state.time.model_dump() == before

    session.set_flag("orientation_done", True)
    assert session.affordances()["advance_time"] == {
        "available": True,
        "reason": None,
    }
    result = session.advance_time()
    assert result["ok"] is True
    assert session.state.time.model_dump() != before


def test_headless_affordance_exposes_map_policy():
    from world_gal_game.config import EngineConfig
    from world_gal_game.headless import HeadlessSession

    session = HeadlessSession.open(EngineConfig(), pack="demo_pack")
    session.meta["exploration_guidance"] = {
        "map": {
            "requires_flags": ["orientation_done"],
            "locked_reason": "先完成集合。",
        },
    }

    assert session.affordances()["map"] == {
        "available": False,
        "reason": "先完成集合。",
    }
    assert session.affordances()["travel_options"] == []
    assert session.affordances()["guided_destinations"] == []
    local_target = next(
        row["target"]
        for row in session.affordances()["exits"]
        if row["available"]
    )
    before = session.state.map.current_location_id
    snapshot = session.snapshot()
    assert session.move_to(local_target)["ok"] is True
    assert session.state.map.current_location_id == local_target
    session.restore(snapshot)
    assert session.state.map.current_location_id == before

    blocked = session.move_to(local_target, allow_distant=True)
    assert blocked["ok"] is False
    assert blocked["error"] == "先完成集合。"
    assert session.state.map.current_location_id == before

    session.set_flag("orientation_done", True)
    assert session.affordances()["map"] == {
        "available": True,
        "reason": None,
    }
    assert session.move_to(local_target, allow_distant=True)["ok"] is True


def test_authored_move_effect_is_not_blocked_by_map_ui_guidance():
    from world_gal_game.config import EngineConfig
    from world_gal_game.headless import HeadlessSession

    session = HeadlessSession.open(EngineConfig(), pack="demo_pack")
    session.meta["exploration_guidance"] = {
        "map": {
            "requires_flags": ["orientation_done"],
            "locked_reason": "先完成集合。",
        },
    }
    target = next(
        row["target"]
        for row in session.affordances()["exits"]
        if row["available"]
    )

    result = session.apply_effect({
        "kind": "move_to",
        "target": target,
    })
    assert result["ok"] is True
    assert session.state.map.current_location_id == target


def test_button_tracks_disabled_pointer_without_activating(driver):
    scene = _enter_exploration(driver)
    driver.app.meta["exploration_guidance"] = {
        "advance_time": {
            "requires_flags": ["orientation_done"],
            "locked_reason": "先完成目前目標。",
        },
    }
    scene._rebuild_widgets()
    button = next(b for b in scene._buttons if b.label == "等下個時段")

    class Input:
        mouse_pos = button.rect.center
        mouse_clicked = True

    called: list[bool] = []
    button.on_click = lambda: called.append(True)
    button.update(1 / 60, Input())
    assert button._pointer_over is True
    assert button._hover is False
    assert called == []


def test_quick_menu_hover_does_not_swallow_keyboard_advance():
    from world_gal_game.ui.widgets.quick_menu import QuickMenuBar

    bar = object.__new__(QuickMenuBar)
    bar.visible = True
    bar.rect = pygame.Rect(100, 100, 300, 40)

    keyboard = SimpleNamespace(
        mouse_pos=(200, 120),
        mouse_clicked=False,
        advance_dialogue=True,
    )
    click = SimpleNamespace(
        mouse_pos=(200, 120),
        mouse_clicked=True,
        advance_dialogue=True,
    )
    assert bar.consumed(keyboard) is False
    assert bar.consumed(click) is True


def test_completed_map_tutorial_can_reveal_real_map_automatically(driver):
    from world_gal_game.core.story_graph import Line, Scene
    from world_gal_game.scenes.map_scene import MapScene

    _enter_exploration(driver)
    app = driver.app
    tutorial = Scene(
        id="map_reveal_tutorial",
        lines=[Line(text="The campus map is ready.")],
    )
    app.state.story.add_scene(tutorial)
    app.meta["travel"] = {
        "spatial_map": {
            "auto_open_after_scenes": [tutorial.id],
        },
    }

    app._start_dialogue(tutorial.id)
    app.manager.commit_pending()
    dialogue = app.manager.current
    dialogue.box.force_reveal()
    dialogue._advance()
    app.manager.commit_pending()

    assert isinstance(app.manager.current, MapScene)
    assert [type(scene).__name__ for scene in app.manager.stack()] == [
        "ExplorationScene",
        "MapScene",
    ]


def test_unfinished_guided_story_closure_reopens_map(driver):
    from world_gal_game.core.story_graph import Line, Scene
    from world_gal_game.scenes.map_scene import MapScene

    _enter_exploration(driver)
    app, _location = _install_guided_handoff_state(driver)
    closure = Scene(
        id="guided_ordinary_closure",
        lines=[Line(text="這一站告一段落。")],
    )
    app.state.story.add_scene(closure)

    app._start_dialogue(closure.id)
    app.manager.commit_pending()
    _finish_current_authored_scene(app, closure.id)

    assert isinstance(app.manager.current, MapScene)
    assert [type(scene).__name__ for scene in app.manager.stack()] == [
        "ExplorationScene",
        "MapScene",
    ]


def test_guided_story_runs_immediate_ambient_chain_before_reopening_map(
    driver,
):
    from world_gal_game.core.map_system import SceneHook
    from world_gal_game.core.story_graph import Effect, Line, Scene
    from world_gal_game.scenes.dialogue_scene import DialogueScene
    from world_gal_game.scenes.map_scene import MapScene

    _enter_exploration(driver)
    app, location = _install_guided_handoff_state(driver)
    first = Scene(
        id="guided_chain_first",
        lines=[Line(text="先完成眼前的事。")],
        on_end=[Effect(
            kind="set_flag",
            target="guided_chain_ready",
            value=True,
        )],
    )
    ambient = Scene(
        id="guided_chain_ambient",
        lines=[Line(text="同一地點還有下一段。")],
    )
    app.state.story.add_scene(first)
    app.state.story.add_scene(ambient)
    location.scene_hooks.append(SceneHook(
        scene_id=ambient.id,
        trigger="auto",
        requires_flags=["guided_chain_ready"],
        once=True,
    ))

    app._start_dialogue(first.id)
    app.manager.commit_pending()
    _finish_current_authored_scene(app, first.id)

    assert isinstance(app.manager.current, DialogueScene)
    assert app.manager.current.scene_id == ambient.id
    assert not any(
        isinstance(scene, MapScene) for scene in app.manager.stack()
    )

    _finish_current_authored_scene(app, ambient.id)
    assert isinstance(app.manager.current, MapScene)


def test_explicit_scene_map_handoff_stays_ahead_of_ambient_hooks(driver):
    from world_gal_game.core.map_system import SceneHook
    from world_gal_game.core.story_graph import Effect, Line, Scene
    from world_gal_game.scenes.map_scene import MapScene

    _enter_exploration(driver)
    app, location = _install_guided_handoff_state(driver)
    explicit = Scene(
        id="explicit_guided_handoff",
        lines=[Line(text="這一幕明確指定先回地圖。")],
        on_end=[Effect(
            kind="set_flag",
            target="explicit_ambient_ready",
            value=True,
        )],
    )
    ambient = Scene(
        id="explicit_deferred_ambient",
        lines=[Line(text="這一段必須等下一次地點檢查。")],
    )
    app.state.story.add_scene(explicit)
    app.state.story.add_scene(ambient)
    location.scene_hooks.append(SceneHook(
        scene_id=ambient.id,
        trigger="auto",
        requires_flags=["explicit_ambient_ready"],
        once=True,
    ))
    app.meta["travel"]["spatial_map"] = {
        "auto_open_after_scenes": [explicit.id],
    }

    app._start_dialogue(explicit.id)
    app.manager.commit_pending()
    _finish_current_authored_scene(app, explicit.id)

    assert isinstance(app.manager.current, MapScene)
    assert ambient.id not in app.state.story.played
    assert app.state.story.current_scene is None


def test_completed_guided_progress_does_not_force_map(driver):
    from world_gal_game.core.story_graph import Effect, Line, Scene
    from world_gal_game.scenes.exploration import ExplorationScene
    from world_gal_game.scenes.map_scene import MapScene

    _enter_exploration(driver)
    app, _location = _install_guided_handoff_state(driver)
    final = Scene(
        id="guided_final_closure",
        lines=[Line(text="指南的最後一站完成。")],
        on_end=[Effect(kind="set_flag", target="guide_done", value=True)],
    )
    app.state.story.add_scene(final)

    app._start_dialogue(final.id)
    app.manager.commit_pending()
    _finish_current_authored_scene(app, final.id)

    assert isinstance(app.manager.current, ExplorationScene)
    assert not any(
        isinstance(scene, MapScene) for scene in app.manager.stack()
    )


def test_guided_story_without_auto_reopen_keeps_legacy_handoff(
    driver,
):
    from world_gal_game.core.story_graph import Line, Scene
    from world_gal_game.scenes.exploration import ExplorationScene
    from world_gal_game.scenes.map_scene import MapScene

    _enter_exploration(driver)
    app, _location = _install_guided_handoff_state(driver)
    del app.meta["travel"]["guided_story"]["auto_reopen"]
    closure = Scene(
        id="guided_legacy_closure",
        lines=[Line(text="舊 pack 維持原本交接。")],
    )
    app.state.story.add_scene(closure)

    app._start_dialogue(closure.id)
    app.manager.commit_pending()
    _finish_current_authored_scene(app, closure.id)

    assert isinstance(app.manager.current, ExplorationScene)
    assert not any(
        isinstance(scene, MapScene) for scene in app.manager.stack()
    )


def test_guided_story_does_not_open_map_under_new_modal_overlay(driver):
    from world_gal_game.core.story_graph import Effect, Line, Scene
    from world_gal_game.scenes.map_scene import MapScene

    _enter_exploration(driver)
    app, _location = _install_guided_handoff_state(driver)
    closure = Scene(
        id="guided_modal_closure",
        lines=[Line(text="先顯示章節卡。")],
        on_end=[Effect(kind="set_chapter", target="ch1_arrival")],
    )
    app.state.story.add_scene(closure)

    app._start_dialogue(closure.id)
    app.manager.commit_pending()
    _finish_current_authored_scene(app, closure.id)

    assert type(app.manager.current).__name__ == "ChapterCardScene"
    assert not any(
        isinstance(scene, MapScene) for scene in app.manager.stack()
    )


def test_map_reveal_uses_final_scene_id_after_authored_chain(driver):
    from world_gal_game.core.story_graph import Effect, Line, Scene
    from world_gal_game.scenes.map_scene import MapScene

    _enter_exploration(driver)
    app = driver.app
    final = Scene(
        id="map_chain_final",
        lines=[Line(text="Now open the map.")],
    )
    first = Scene(
        id="map_chain_first",
        lines=[Line(text="Finish the explanation first.")],
        on_end=[Effect(kind="play_scene", target=final.id)],
    )
    app.state.story.add_scene(first)
    app.state.story.add_scene(final)
    app.meta["travel"] = {
        "spatial_map": {
            "auto_open_after_scenes": [final.id],
        },
    }

    app._start_dialogue(first.id)
    app.manager.commit_pending()
    dialogue = app.manager.current
    dialogue.box.force_reveal()
    dialogue._advance()
    assert dialogue.scene_id == final.id
    dialogue.box.force_reveal()
    dialogue._advance()
    app.manager.commit_pending()

    assert isinstance(app.manager.current, MapScene)
