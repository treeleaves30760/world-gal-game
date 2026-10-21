"""Story-first destination offers layered over exact TravelPlan quotes."""
from __future__ import annotations

from unittest.mock import patch

from world_gal_game.core.game_state import GameState
from world_gal_game.core.guided_destinations import (
    GuidedDestinationResolver,
    guided_story_enabled,
    guided_story_progress,
)
from world_gal_game.core.map_system import Exit, Location, SceneHook
from world_gal_game.core.story_graph import Line, Scene
from world_gal_game.core.time_system import TimeOfDay
from world_gal_game.core.travel import TravelService


def _state() -> GameState:
    state = GameState()
    state.map.add_location(Location(
        id="start",
        name="起點",
        exits=[
            Exit(target="current_story"),
            Exit(target="future_story"),
            Exit(target="examine_only"),
            Exit(target="unmet_story"),
            Exit(target="hidden_story"),
        ],
    ))
    state.map.add_location(Location(
        id="current_story",
        name="現在事件",
        scene_hooks=[SceneHook(
            scene_id="current_scene",
            trigger="auto",
        )],
    ))
    state.map.add_location(Location(
        id="future_story",
        name="未來事件",
        scene_hooks=[SceneHook(
            scene_id="future_scene",
            trigger="enter",
            requires_time=[TimeOfDay.AFTERNOON.value],
        )],
    ))
    state.map.add_location(Location(
        id="examine_only",
        name="調查事件",
        scene_hooks=[SceneHook(
            scene_id="examine_scene",
            trigger="examine",
        )],
    ))
    state.map.add_location(Location(
        id="unmet_story",
        name="未解鎖事件",
        scene_hooks=[SceneHook(
            scene_id="later_scene",
            trigger="auto",
            requires_flags=["later_arc"],
        )],
    ))
    state.map.add_location(Location(
        id="hidden_story",
        name="隱藏地點",
        tags=["story_only"],
        scene_hooks=[SceneHook(
            scene_id="hidden_scene",
            trigger="auto",
        )],
    ))
    for scene_id in (
        "current_scene",
        "future_scene",
        "examine_scene",
        "later_scene",
        "hidden_scene",
    ):
        state.story.add_scene(Scene(
            id=scene_id,
            title=f"title:{scene_id}",
            lines=[Line(text=scene_id)],
        ))
    state.map.move_to("start")
    return state


def _meta(*, wait_locked: bool = False) -> dict:
    meta = {
        "travel": {
            "scope": "graph",
            "requires_flags": ["map_ready"],
            "minimum_cost": 0,
            "hidden_tags": ["story_only"],
            "guided_story": {
                "future_horizon_phases": 6,
                "max_primary": 3,
                "markers": {
                    "current_scene": {
                        "npc_ids": ["heroine"],
                        "teaser": "不劇透的事件提示。",
                    },
                },
            },
        },
    }
    if wait_locked:
        meta["exploration_guidance"] = {
            "advance_time": {
                "forbids_flags": ["cannot_wait"],
                "locked_reason": "先完成眼前事件。",
            },
        }
    return meta


def _offers(state: GameState, meta: dict) -> dict:
    return {
        offer.target: offer
        for offer in GuidedDestinationResolver(state, meta).resolve()
    }


def test_pack_authored_progress_selects_first_incomplete_stage() -> None:
    state = _state()
    state.events.set_flag("map_ready", True)
    meta = _meta()
    meta["travel"]["guided_story"]["progress"] = {
        "label": "新生指南",
        "stages": [
            {"label": "第一輪", "flags": ["met_a", "met_b"]},
            {"label": "第二輪", "flags": ["met_c"]},
        ],
    }

    first = guided_story_progress(meta, state.events.flags)
    assert first is not None
    assert first.as_dict() == {
        "label": "新生指南",
        "stage": "第一輪",
        "completed": 0,
        "total": 2,
        "stage_index": 1,
        "stage_count": 2,
        "complete": False,
    }

    state.events.set_flag("met_a", True)
    assert guided_story_progress(
        meta,
        state.events.flags,
    ).completed == 1
    state.events.set_flag("met_b", True)
    second = guided_story_progress(meta, state.events.flags)
    assert second is not None
    assert second.stage == "第二輪"
    assert second.completed == 0

    state.events.set_flag("met_c", True)
    complete = guided_story_progress(meta, state.events.flags)
    assert complete is not None
    assert complete.stage == "第二輪"
    assert complete.complete is True


def test_progress_waits_for_authored_stage_wrap_after_count_reaches_total() -> None:
    state = _state()
    state.events.set_flag("map_ready", True)
    meta = _meta()
    meta["travel"]["guided_story"]["progress"] = {
        "label": "新生指南",
        "stages": [
            {
                "label": "第一輪",
                # ``flags`` stays as the migration alias while current packs
                # move to the explicit counted/completion split.
                "flags": ["met_a", "met_b"],
                "counted_flags": ["met_a", "met_b"],
                "completion_flags": ["first_wrap_done"],
            },
            {
                "label": "第二輪",
                "counted_flags": ["met_c"],
                "completion_flags": ["second_wrap_done"],
            },
        ],
    }

    state.events.set_flag("met_a", True)
    state.events.set_flag("met_b", True)
    waiting = guided_story_progress(meta, state.events.flags)
    assert waiting is not None
    assert waiting.stage == "第一輪"
    assert waiting.completed == waiting.total == 2
    assert waiting.complete is False

    state.events.set_flag("first_wrap_done", True)
    second = guided_story_progress(meta, state.events.flags)
    assert second is not None
    assert second.stage == "第二輪"
    assert second.completed == 0

    state.events.set_flag("met_c", True)
    still_waiting = guided_story_progress(meta, state.events.flags)
    assert still_waiting is not None
    assert still_waiting.stage == "第二輪"
    assert still_waiting.complete is False

    state.events.set_flag("second_wrap_done", True)
    complete = guided_story_progress(meta, state.events.flags)
    assert complete is not None
    assert complete.complete is True


def test_guided_policy_waits_for_graph_gate() -> None:
    state = _state()
    meta = _meta()

    with patch(
        "world_gal_game.core.guided_destinations.travel_state_revision",
        side_effect=AssertionError("disabled guidance must not hash state"),
    ):
        assert guided_story_enabled(meta, state.events.flags) is False
        resolver = GuidedDestinationResolver(state, meta)
        assert resolver.enabled is False
        assert resolver.resolve() == ()

    state.events.set_flag("map_ready", True)
    assert guided_story_enabled(meta, state.events.flags) is True
    assert resolver.resolve()
    assert resolver.enabled is True


def _direct_before_graph_state() -> GameState:
    state = GameState()
    state.map.add_location(Location(
        id="start",
        name="起點",
        exits=[
            Exit(target="direct_story", travel_cost=2),
            Exit(target="middle"),
        ],
    ))
    state.map.add_location(Location(
        id="direct_story",
        name="眼前的指引",
        scene_hooks=[SceneHook(
            scene_id="direct_scene",
            trigger="auto",
        )],
    ))
    state.map.add_location(Location(
        id="middle",
        name="中繼點",
        exits=[Exit(target="distant_story")],
    ))
    state.map.add_location(Location(
        id="distant_story",
        name="遠方事件",
        scene_hooks=[SceneHook(
            scene_id="distant_scene",
            trigger="auto",
        )],
    ))
    for scene_id in ("direct_scene", "distant_scene"):
        state.story.add_scene(Scene(
            id=scene_id,
            title=scene_id,
            lines=[Line(text=scene_id)],
        ))
    state.map.move_to("start")
    return state


def _direct_before_graph_meta() -> dict:
    return {
        "travel": {
            "scope": "graph",
            "requires_flags": ["map_ready"],
            "minimum_cost": 3,
            "guided_story": {
                "direct_before_graph": True,
                "require_marker": True,
                "markers": {
                    "direct_scene": {},
                    "distant_scene": {},
                },
            },
        },
    }


def test_direct_before_graph_cannot_offer_a_distant_story_route() -> None:
    state = _direct_before_graph_state()
    meta = _direct_before_graph_meta()
    resolver = GuidedDestinationResolver(state, meta)

    assert guided_story_enabled(meta, state.events.flags) is True
    assert resolver.enabled is True

    primary = {offer.target: offer for offer in resolver.primary()}
    assert set(primary) == {"direct_story"}
    direct = primary["direct_story"]
    assert direct.available is True
    assert direct.action == "travel"
    assert direct.plan is not None
    assert direct.plan.is_direct is True
    assert direct.plan.location_ids == ("start", "direct_story")
    # The graph-map floor is suppressed before its gate, but an authored
    # direct exit cost remains authoritative.
    assert direct.plan.minimum_cost == 0
    assert direct.plan.route_cost == 2
    assert direct.plan.total_cost == 2

    all_offers = {offer.target: offer for offer in resolver.resolve()}
    distant = all_offers["distant_story"]
    assert distant.visibility == "secondary"
    assert distant.status == "locked"
    assert distant.action == "none"
    assert distant.available is False
    assert distant.plan is None

    result = TravelService.commit(state, direct.plan)
    assert result.ok is True
    assert result.scene_triggered == "direct_scene"
    assert state.map.current_location_id == "direct_story"


def test_direct_before_graph_restores_full_policy_when_gate_opens() -> None:
    state = _direct_before_graph_state()
    meta = _direct_before_graph_meta()
    resolver = GuidedDestinationResolver(state, meta)

    assert {
        offer.target for offer in resolver.primary()
    } == {"direct_story"}

    state.events.set_flag("map_ready", True)
    primary = {offer.target: offer for offer in resolver.primary()}

    assert set(primary) == {"direct_story", "distant_story"}
    direct = primary["direct_story"]
    assert direct.plan is not None
    assert direct.plan.minimum_cost == 3
    assert direct.plan.total_cost == 3

    distant = primary["distant_story"]
    assert distant.plan is not None
    assert distant.plan.is_direct is False
    assert distant.plan.location_ids == (
        "start",
        "middle",
        "distant_story",
    )
    assert distant.plan.minimum_cost == 3
    assert distant.plan.total_cost == 3


def test_direct_before_graph_requires_a_boolean_opt_in() -> None:
    state = _direct_before_graph_state()
    meta = _direct_before_graph_meta()
    meta["travel"]["guided_story"]["direct_before_graph"] = "true"

    assert guided_story_enabled(meta, state.events.flags) is False
    assert GuidedDestinationResolver(state, meta).resolve() == ()


def test_current_exact_arrival_event_is_primary_and_committable() -> None:
    state = _state()
    state.events.set_flag("map_ready", True)

    offer = _offers(state, _meta())["current_story"]

    assert offer.visibility == "primary"
    assert offer.status == "current"
    assert offer.action == "travel"
    assert offer.available is True
    assert offer.plan is not None
    assert offer.plan.arrival_hook_id == "current_scene"
    assert offer.event is not None
    assert offer.event.scene_id == "current_scene"
    assert offer.event.npc_ids == ("heroine",)
    assert offer.event.teaser == "不劇透的事件提示。"

    result = TravelService.commit(state, offer.plan)
    assert result.ok is True
    assert result.scene_triggered == "current_scene"


def test_future_offer_finds_earliest_departure_without_implicit_wait() -> None:
    state = _state()
    state.events.set_flag("map_ready", True)
    before = state.model_dump(mode="json")

    offer = _offers(state, _meta())["future_story"]

    assert offer.visibility == "primary"
    assert offer.status == "future"
    assert offer.action == "wait"
    assert offer.available is True
    assert offer.future is not None
    assert offer.future.wait_phases == 2
    assert offer.future.departure.time_of_day is TimeOfDay.AFTERNOON
    assert offer.plan is not None
    assert offer.plan.arrival_hook_id == "future_scene"
    # Advisory future discovery never advances or otherwise mutates live play.
    assert state.model_dump(mode="json") == before


def test_single_target_resolution_matches_full_offer_semantics() -> None:
    state = _state()
    state.events.set_flag("map_ready", True)
    meta = _meta()
    before = state.model_dump(mode="json")
    expected = _offers(state, meta)

    for target in (
        "current_story",
        "future_story",
        "examine_only",
        "unmet_story",
    ):
        resolver = GuidedDestinationResolver(state, meta)
        with patch.object(
            resolver,
            "_resolve_location",
            wraps=resolver._resolve_location,
        ) as resolve_location:
            observed = resolver.resolve_target(target)
        assert observed is not None
        assert observed.as_dict() == expected[target].as_dict()
        resolve_location.assert_called_once_with(target)

    resolver = GuidedDestinationResolver(state, meta)
    assert resolver.resolve_target("start") is None
    assert resolver.resolve_target("hidden_story") is None
    assert resolver.resolve_target("missing") is None
    assert state.model_dump(mode="json") == before


def test_retained_resolver_drops_future_cache_after_clock_change() -> None:
    state = _state()
    state.events.set_flag("map_ready", True)
    resolver = GuidedDestinationResolver(state, _meta())

    first = {
        offer.target: offer for offer in resolver.resolve()
    }["future_story"]
    assert first.status == "future"
    assert first.future is not None
    assert first.future.wait_phases == 2

    state.time.advance(2)
    second = {
        offer.target: offer for offer in resolver.resolve()
    }["future_story"]

    assert second.status == "current"
    assert second.action == "travel"
    assert second.plan is not None
    assert second.plan.departure.time_of_day is TimeOfDay.AFTERNOON
    assert second.plan.state_revision == second.state_revision


def test_wait_policy_turns_future_marker_into_explained_lock() -> None:
    state = _state()
    state.events.set_flag("map_ready", True)
    state.events.set_flag("cannot_wait", True)

    offer = _offers(state, _meta(wait_locked=True))["future_story"]

    assert offer.visibility == "primary"
    assert offer.status == "locked"
    assert offer.action == "none"
    assert offer.available is False
    assert offer.blocked_reason == "先完成眼前事件。"


def test_examine_and_unmet_later_events_are_secondary_not_false_promises() -> None:
    state = _state()
    state.events.set_flag("map_ready", True)

    offers = _offers(state, _meta())

    examine = offers["examine_only"]
    assert examine.visibility == "secondary"
    assert examine.status == "neutral"
    assert examine.event is None
    assert examine.plan is not None
    assert examine.plan.available_hook_ids == ("examine_scene",)
    assert examine.plan.arrival_hook_id is None

    unmet = offers["unmet_story"]
    assert unmet.visibility == "secondary"
    assert unmet.status == "neutral"
    assert unmet.event is None


def test_hidden_locations_do_not_leak_into_either_tier() -> None:
    state = _state()
    state.events.set_flag("map_ready", True)

    offers = _offers(state, _meta())

    assert "hidden_story" not in offers


def test_require_marker_is_an_explicit_primary_allowlist() -> None:
    state = _state()
    state.events.set_flag("map_ready", True)
    meta = _meta()
    meta["travel"]["guided_story"]["require_marker"] = True

    offers = _offers(state, meta)

    marked = offers["current_story"]
    assert marked.visibility == "primary"
    assert marked.event is not None
    assert marked.event.scene_id == "current_scene"

    unmarked = offers["future_story"]
    assert unmarked.visibility == "secondary"
    assert unmarked.status == "neutral"
    assert unmarked.event is None


def test_unmarked_current_event_does_not_hide_marked_future_event() -> None:
    state = _state()
    state.events.set_flag("map_ready", True)
    state.map.add_location(Location(
        id="staged_story",
        name="分時段事件",
        scene_hooks=[
            SceneHook(
                scene_id="unmarked_morning",
                trigger="auto",
                requires_time=[TimeOfDay.MORNING.value],
            ),
            SceneHook(
                scene_id="marked_afternoon",
                trigger="auto",
                requires_time=[TimeOfDay.AFTERNOON.value],
            ),
        ],
    ))
    state.map.locations["start"].exits.append(Exit(target="staged_story"))
    for scene_id in ("unmarked_morning", "marked_afternoon"):
        state.story.add_scene(Scene(
            id=scene_id,
            title=scene_id,
            lines=[Line(text=scene_id)],
        ))
    meta = _meta()
    guided = meta["travel"]["guided_story"]
    guided["require_marker"] = True
    guided["markers"]["marked_afternoon"] = {
        "teaser": "稍晚會發生的主線。",
    }

    offer = _offers(state, meta)["staged_story"]

    assert offer.visibility == "primary"
    assert offer.status == "future"
    assert offer.action == "wait"
    assert offer.future is not None
    assert offer.future.wait_phases == 2
    assert offer.event is not None
    assert offer.event.scene_id == "marked_afternoon"


def test_primary_budget_is_reported_but_never_runtime_truncates() -> None:
    state = _state()
    state.events.set_flag("map_ready", True)
    state.map.add_location(Location(
        id="fourth_story",
        name="第四事件",
        scene_hooks=[SceneHook(scene_id="fourth_scene", trigger="auto")],
    ))
    state.story.add_scene(Scene(
        id="fourth_scene",
        title="第四事件",
        lines=[Line(text="第四。")],
    ))
    state.map.locations["start"].exits.append(Exit(target="fourth_story"))
    meta = _meta()
    meta["travel"]["guided_story"]["max_primary"] = 1

    resolver = GuidedDestinationResolver(state, meta)

    assert resolver.max_primary == 1
    assert len(resolver.primary()) == 3


def test_serialized_offer_keeps_exact_path_cost_arrival_and_revision() -> None:
    state = _state()
    state.events.set_flag("map_ready", True)

    offer = _offers(state, _meta())["current_story"].as_dict()

    assert offer["travel"]["path"] == ["start", "current_story"]
    assert offer["travel"]["cost"] == 0
    assert offer["travel"]["arrival"]["time_of_day"] == "morning"
    assert offer["travel"]["scene"] == "current_scene"
    assert len(offer["state_revision"]) == 64
