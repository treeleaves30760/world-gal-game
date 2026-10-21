"""Shared travel transaction and truthful projected-arrival previews."""
from __future__ import annotations

from dataclasses import replace

import pytest

import world_gal_game.core.travel as travel_module
from world_gal_game.core.game_state import GameState
from world_gal_game.core.map_system import (
    Exit,
    Location,
    NPCPresence,
    SceneHook,
)
from world_gal_game.core.story_graph import Condition
from world_gal_game.core.time_system import TimeOfDay
from world_gal_game.core.travel import (
    TravelService,
    clone_for_travel_preview,
)
from world_gal_game.core.story_graph import Line, Scene
from world_gal_game.plugins import (
    PluginContext,
    PluginManager,
    hook,
    restore,
    snapshot,
)
from world_gal_game.plugins.context import HookEvent
from world_gal_game.plugins.registry import (
    CONDITION_REGISTRY,
    EFFECT_REGISTRY,
    ConditionEntry,
)


class _HookRecorder:
    def __init__(self, state: GameState | None = None) -> None:
        self.state = state
        self.events: list[tuple[str, dict]] = []
        self.state_seen: list[tuple[str, str | None, TimeOfDay]] = []

    def fire_hook(self, event: str, **payload) -> None:
        self.events.append((event, payload))
        if self.state is not None and event in (
            HookEvent.PLAYER_MOVE,
            HookEvent.TIME_ADVANCE,
        ):
            self.state_seen.append((
                event,
                self.state.map.current_location_id,
                self.state.time.time_of_day,
            ))


def _arrival_state(*, distant: bool = False) -> GameState:
    state = GameState()
    if distant:
        state.map.add_location(Location(
            id="start",
            name="起點",
            exits=[Exit(target="middle", travel_cost=1)],
        ))
        state.map.add_location(Location(
            id="middle",
            name="中繼",
            exits=[Exit(target="destination", travel_cost=1)],
        ))
    else:
        state.map.add_location(Location(
            id="start",
            name="起點",
            exits=[Exit(target="destination", travel_cost=2)],
        ))
    state.map.add_location(Location(
        id="destination",
        name="終點",
        npcs=[NPCPresence(
            npc_id="heroine",
            times=["afternoon"],
        )],
        scene_hooks=[SceneHook(
            scene_id="arrival_scene",
            trigger="auto",
            requires_time=["afternoon"],
        )],
    ))
    state.story.add_scene(Scene(
        id="arrival_scene",
        lines=[Line(text="抵達。")],
    ))
    state.map.move_to("start")
    return state


def test_preview_uses_arrival_time_for_npc_and_event_without_mutation():
    state = _arrival_state()
    before = state.model_dump(mode="json")
    plan = TravelService.plan(state, "destination")

    assert plan is not None
    preview = TravelService.preview(state, plan)

    assert plan.departure.time_of_day is TimeOfDay.MORNING
    assert plan.arrival.time_of_day is TimeOfDay.AFTERNOON
    assert preview.npc_ids == ("heroine",)
    assert preview.scene_triggered == "arrival_scene"
    assert state.model_dump(mode="json") == before


def test_schedule_minimum_cost_is_charged_once_for_zero_cost_route():
    state = GameState()
    state.map.add_location(Location(
        id="start",
        name="起點",
        exits=[Exit(target="middle")],
    ))
    state.map.add_location(Location(
        id="middle",
        name="中繼",
        exits=[Exit(target="destination")],
    ))
    state.map.add_location(Location(
        id="destination",
        name="終點",
        scene_hooks=[SceneHook(
            scene_id="noon_story",
            trigger="auto",
            requires_time=[TimeOfDay.NOON.value],
        )],
    ))
    state.story.add_scene(Scene(
        id="noon_story",
        lines=[Line(text="中午抵達。")],
    ))
    state.map.move_to("start")

    plan = TravelService.plan(
        state,
        "destination",
        allow_distant=True,
        minimum_cost=1,
    )

    assert plan is not None
    assert plan.location_ids == ("start", "middle", "destination")
    assert plan.route_cost == 0
    assert plan.minimum_cost == 1
    assert plan.total_cost == 1
    assert plan.arrival.time_of_day is TimeOfDay.NOON
    assert plan.arrival_hook_id == "noon_story"

    result = TravelService.commit(state, plan)

    assert result.ok is True
    assert result.scene_triggered == "noon_story"
    assert state.time.time_of_day is TimeOfDay.NOON
    assert state.map.current_location_id == "destination"


@pytest.mark.parametrize(
    ("allow_distant", "destination_id"),
    [
        (False, "destination"),
        (True, "destination"),
        (False, "missing"),
        (True, "missing"),
    ],
)
def test_lightweight_route_matches_full_plan_reachability_without_clone(
    monkeypatch,
    allow_distant,
    destination_id,
):
    state = _arrival_state(distant=allow_distant)
    before = state.model_dump(mode="json")
    quoted = TravelService.plan(
        state,
        destination_id,
        allow_distant=allow_distant,
        minimum_cost=3,
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("route probes must not clone or hash state")

    monkeypatch.setattr(travel_module, "_clone_state", forbidden)
    monkeypatch.setattr(travel_module, "_clone_preview_state", forbidden)
    monkeypatch.setattr(travel_module, "_state_revision", forbidden)

    route = TravelService.route(
        state,
        destination_id,
        allow_distant=allow_distant,
        minimum_cost=3,
    )

    assert (route is not None) is (quoted is not None)
    assert TravelService.can_reach(
        state,
        destination_id,
        allow_distant=allow_distant,
        minimum_cost=3,
    ) is (quoted is not None)
    if route is not None and quoted is not None:
        assert route.location_ids == quoted.location_ids
        assert route.legs == quoted.legs
        assert route.total_cost == quoted.total_cost
        assert route.minimum_cost == quoted.minimum_cost
        assert route.arrival == quoted.arrival
        assert route.arrival_hook_id is None
        assert route.state_revision is None
    assert state.model_dump(mode="json") == before


def test_preview_clone_shares_authored_catalogues_but_isolates_progress():
    state = _arrival_state()
    state.meta["regular_context"] = {"visits": ["start"]}
    state.meta["__plugin:test__"] = {"count": 2}
    authored_map = state.map.model_copy(deep=True)
    authored_story = state.story.model_copy(deep=True)
    authored_endings = state.endings.model_copy(deep=True)
    state.meta["__pack_map_catalog__"] = authored_map
    state.meta["__pack_story_catalog__"] = authored_story
    state.meta["__pack_ending_catalog__"] = authored_endings

    projected = clone_for_travel_preview(state)

    assert projected is not state
    assert projected.map is not state.map
    assert projected.story is not state.story
    assert projected.endings is not state.endings
    assert projected.map.locations is not state.map.locations
    assert projected.story.scenes is not state.story.scenes
    assert projected.endings.endings is not state.endings.endings
    assert (
        projected.map.locations["start"]
        is state.map.locations["start"]
    )
    assert (
        projected.story.scenes["arrival_scene"]
        is state.story.scenes["arrival_scene"]
    )
    assert projected.meta["__pack_map_catalog__"] is authored_map
    assert projected.meta["__pack_story_catalog__"] is authored_story
    assert projected.meta["__pack_ending_catalog__"] is authored_endings

    projected.events.set_flag("projected_only", True)
    projected.map.visited.add("projected")
    projected.story.played.add("projected")
    projected.endings.unlocked["projected"] = "now"
    projected.meta["regular_context"]["visits"].append("projected")
    projected.meta["__plugin:test__"]["count"] = 99

    assert state.events.get_flag("projected_only") is False
    assert "projected" not in state.map.visited
    assert "projected" not in state.story.played
    assert "projected" not in state.endings.unlocked
    assert state.meta["regular_context"] == {"visits": ["start"]}
    assert state.meta["__plugin:test__"] == {"count": 2}


def test_missing_arrival_scene_is_never_advertised_as_guaranteed():
    state = _arrival_state()
    state.story.scenes.pop("arrival_scene")

    plan = TravelService.plan(state, "destination")

    assert plan is not None
    assert plan.arrival_hook_id is None
    assert TravelService.preview(state, plan).scene_triggered is None


def test_preview_contains_condition_handler_mutations_in_projected_state():
    """Even an impure plugin condition must not mutate the live save.

    Conditions are expected to be predicates, but plugins are isolated
    extensions and can be buggy. Opening a travel preview (and then cancelling)
    must still be side-effect free.
    """
    condition_kind = "test_travel_preview_mutation_probe"

    def mutating_condition(projected_state, _condition):
        projected_state.events.set_flag("preview_probe_was_evaluated", True)
        return True

    CONDITION_REGISTRY.register(ConditionEntry(
        kind=condition_kind,
        fn=mutating_condition,
        plugin_id="test_travel_service",
    ))
    try:
        state = GameState()
        state.map.add_location(Location(
            id="start",
            name="起點",
            exits=[Exit(target="destination")],
        ))
        state.map.add_location(Location(
            id="destination",
            name="終點",
            scene_hooks=[SceneHook(
                scene_id="arrival_scene",
                trigger="auto",
                requires=[Condition(kind=condition_kind)],
            )],
        ))
        state.story.add_scene(Scene(
            id="arrival_scene",
            lines=[Line(text="抵達。")],
        ))
        state.map.move_to("start")
        plan = TravelService.plan(state, "destination")
        assert plan is not None
        before = state.model_dump(mode="json")

        preview = TravelService.preview(state, plan)

        assert preview.scene_triggered == "arrival_scene"
        assert state.events.get_flag("preview_probe_was_evaluated") is False
        assert state.model_dump(mode="json") == before
    finally:
        CONDITION_REGISTRY.unregister(condition_kind)


@pytest.mark.parametrize("invalidation", ["eligibility", "played"])
def test_commit_rejects_quote_when_advertised_hook_changed(invalidation):
    """A confirmed trip may not silently replace or remove its shown event."""
    state = GameState()
    state.map.add_location(Location(
        id="start",
        name="起點",
        exits=[Exit(target="destination", travel_cost=1)],
    ))
    state.map.add_location(Location(
        id="destination",
        name="終點",
        scene_hooks=[SceneHook(
            scene_id="advertised_scene",
            trigger="auto",
            requires_flags=["offer_open"],
        )],
    ))
    state.story.add_scene(Scene(
        id="advertised_scene",
        lines=[Line(text="抵達。")],
    ))
    state.map.move_to("start")
    state.events.set_flag("offer_open", True)
    plan = TravelService.plan(state, "destination")
    assert plan is not None
    preview = TravelService.preview(state, plan)
    assert preview.scene_triggered == "advertised_scene"

    if invalidation == "eligibility":
        state.events.set_flag("offer_open", False)
    else:
        state.story.played.add("advertised_scene")
    before_commit = state.model_dump(mode="json")

    result = TravelService.commit(state, plan)

    assert result.ok is False
    assert result.moved_to is None
    assert result.scene_triggered is None
    assert result.error is not None
    assert "重新確認" in result.error
    assert state.model_dump(mode="json") == before_commit


def test_commit_moves_and_advances_once_through_lifecycle_hooks():
    state = _arrival_state()
    recorder = _HookRecorder(state)
    state.meta["__plugin_manager__"] = recorder
    plan = TravelService.plan(state, "destination")

    assert plan is not None
    result = TravelService.commit(state, plan)

    assert result.ok is True
    assert result.moved_to == "destination"
    assert result.scene_triggered == "arrival_scene"
    assert state.map.current_location_id == "destination"
    assert state.time.time_of_day is TimeOfDay.AFTERNOON
    event_names = [event for event, _payload in recorder.events]
    assert event_names.count(HookEvent.PLAYER_MOVE) == 1
    assert event_names.count(HookEvent.TIME_ADVANCE) == 1
    assert recorder.state_seen == [
        (
            HookEvent.PLAYER_MOVE,
            "destination",
            TimeOfDay.AFTERNOON,
        ),
        (
            HookEvent.TIME_ADVANCE,
            "destination",
            TimeOfDay.AFTERNOON,
        ),
    ]


def test_commit_preserves_effect_hook_order_on_detached_candidate():
    """Travel dispatch matches GameState.apply without exposing partial state."""
    registry_snapshot = snapshot()
    try:
        state = _arrival_state()
        manager = PluginManager(engine_version="0.1.0")
        live_context = PluginContext(state=state, manager=manager)
        manager.set_context(live_context)
        state.meta["__plugin_manager__"] = manager
        observed: list[
            tuple[str, str | None, str | None, TimeOfDay]
        ] = []
        hook_states: list[GameState] = []

        for event in (
            HookEvent.EFFECT_BEFORE_APPLY,
            HookEvent.PLAYER_MOVE,
            HookEvent.EFFECT_AFTER_APPLY,
            HookEvent.TIME_ADVANCE,
        ):
            @hook(event, plugin_id="travel_hook_order")
            def observe(
                ctx,
                eff=None,
                *,
                _event=event,
                **_payload,
            ):
                hook_states.append(ctx.state)
                ctx.scratch["travel_hook_count"] = (
                    int(ctx.scratch.get("travel_hook_count", 0)) + 1
                )
                observed.append((
                    _event,
                    eff.kind if eff is not None else None,
                    ctx.state.map.current_location_id,
                    ctx.state.time.time_of_day,
                ))

        plan = TravelService.plan(state, "destination")

        assert plan is not None
        assert observed == []
        assert live_context.scratch == {}
        result = TravelService.commit(state, plan)

        assert result.ok is True
        assert observed == [
            (
                HookEvent.EFFECT_BEFORE_APPLY,
                "move_to",
                "start",
                TimeOfDay.MORNING,
            ),
            (
                HookEvent.PLAYER_MOVE,
                None,
                "destination",
                TimeOfDay.MORNING,
            ),
            (
                HookEvent.EFFECT_AFTER_APPLY,
                "move_to",
                "destination",
                TimeOfDay.MORNING,
            ),
            (
                HookEvent.EFFECT_BEFORE_APPLY,
                "advance_time",
                "destination",
                TimeOfDay.MORNING,
            ),
            (
                HookEvent.TIME_ADVANCE,
                None,
                "destination",
                TimeOfDay.AFTERNOON,
            ),
            (
                HookEvent.EFFECT_AFTER_APPLY,
                "advance_time",
                "destination",
                TimeOfDay.AFTERNOON,
            ),
        ]
        assert hook_states
        assert all(hook_state is not state for hook_state in hook_states)
        assert live_context.scratch == {"travel_hook_count": 6}
    finally:
        restore(registry_snapshot)


def test_demo_step_counter_plan_is_pure_and_commit_counts_one_move():
    """The reference effect.after_apply hook observes atomic travel commits."""
    from world_gal_game.config import EngineConfig
    from world_gal_game.headless import HeadlessSession

    registry_snapshot = snapshot()
    try:
        session = HeadlessSession.open(
            EngineConfig(seed=17),
            pack="demo_pack",
        )
        live_slot = {"count": 7}
        session.state.meta["__plugin:step_counter__"] = live_slot

        def step_count() -> int:
            slot = session.state.meta.get("__plugin:step_counter__")
            return int(slot.get("count", 0)) if isinstance(slot, dict) else 0

        assert step_count() == 7
        plan = TravelService.plan(session.state, "town_square")

        assert plan is not None
        assert step_count() == 7
        assert TravelService.preview(
            session.state,
            plan,
        ).plan is plan
        assert step_count() == 7

        result = TravelService.commit(session.state, plan)

        assert result.ok is True
        assert session.state.map.current_location_id == "town_square"
        assert step_count() == 8
        assert session.state.meta["__plugin:step_counter__"] is live_slot
    finally:
        restore(registry_snapshot)


def test_failed_commit_discards_candidate_plugin_state(
    monkeypatch,
):
    """External hook output is not reversible; live plugin state still is."""
    registry_snapshot = snapshot()
    try:
        state = _arrival_state()
        manager = PluginManager(engine_version="0.1.0")
        live_context = PluginContext(state=state, manager=manager)
        manager.set_context(live_context)
        state.meta["__plugin_manager__"] = manager
        external_observations: list[str] = []

        @hook(HookEvent.EFFECT_AFTER_APPLY, plugin_id="travel_rollback")
        def mutate_candidate_plugin_state(ctx, eff=None, result=None):
            if (
                eff is None
                or eff.kind != "move_to"
                or (isinstance(result, dict) and "error" in result)
            ):
                return
            slot = ctx.get_plugin_state("travel_rollback")
            slot["count"] = int(slot.get("count", 0)) + 1
            ctx.scratch["travel_rollback"] = True
            external_observations.append(ctx.state.map.current_location_id)

        plan = TravelService.plan(state, "destination")
        assert plan is not None
        before = state.model_dump(mode="json")

        original = EFFECT_REGISTRY.require("advance_time")

        def fail_advance(_state, _effect):
            return {
                "kind": "advance_time",
                "error": "forced advance failure",
            }

        monkeypatch.setitem(
            EFFECT_REGISTRY._entries,
            "advance_time",
            replace(original, fn=fail_advance),
        )

        result = TravelService.commit(state, plan)

        assert result.ok is False
        assert result.error == "forced advance failure"
        assert state.model_dump(mode="json") == before
        assert "__plugin:travel_rollback__" not in state.meta
        assert live_context.scratch == {}
        # Hooks are arbitrary Python: module globals, I/O, and similar opaque
        # side effects cannot be rolled back. The hook nevertheless saw only
        # the detached destination state, never a partial live GameState.
        assert external_observations == ["destination"]
    finally:
        restore(registry_snapshot)


def test_advance_time_failure_rolls_back_all_persistent_travel_state(
    monkeypatch,
):
    """A failed second effect cannot leave the first half of travel committed."""
    state = _arrival_state()
    recorder = _HookRecorder(state)
    state.meta["__plugin_manager__"] = recorder
    plan = TravelService.plan(state, "destination")
    assert plan is not None
    before = state.model_dump(mode="json")

    original = EFFECT_REGISTRY.require("advance_time")

    def fail_advance(_state, _effect):
        return {"kind": "advance_time", "error": "forced advance failure"}

    monkeypatch.setitem(
        EFFECT_REGISTRY._entries,
        "advance_time",
        replace(original, fn=fail_advance),
    )

    result = TravelService.commit(state, plan)

    assert result.ok is False
    assert result.error == "forced advance failure"
    assert result.moved_to is None
    assert result.scene_triggered is None
    assert state.model_dump(mode="json") == before
    assert recorder.events == []


def test_stale_plan_fails_without_any_state_change():
    state = _arrival_state()
    plan = TravelService.plan(state, "destination")
    assert plan is not None
    state.time.advance(1)
    before = state.model_dump(mode="json")

    result = TravelService.commit(state, plan)

    assert result.ok is False
    assert "時段已改變" in (result.error or "")
    assert state.model_dump(mode="json") == before


def test_atomic_distant_commit_skips_intermediate_visit_and_hooks():
    state = _arrival_state(distant=True)
    middle = state.map.get("middle")
    assert middle is not None
    middle.scene_hooks.append(SceneHook(
        scene_id="must_not_fire",
        trigger="auto",
    ))
    plan = TravelService.plan(
        state,
        "destination",
        allow_distant=True,
    )

    assert plan is not None
    assert plan.location_ids == ("start", "middle", "destination")
    result = TravelService.commit(state, plan)

    assert result.ok is True
    assert result.scene_triggered == "arrival_scene"
    assert state.map.current_location_id == "destination"
    assert "middle" not in state.map.visited
    assert "destination" in state.map.visited


def test_direct_planning_does_not_substitute_an_indirect_cheaper_route():
    state = GameState()
    state.map.add_location(Location(
        id="start",
        name="起點",
        exits=[
            Exit(target="destination", travel_cost=3),
            Exit(target="middle", travel_cost=0),
        ],
    ))
    state.map.add_location(Location(
        id="middle",
        name="中繼",
        exits=[Exit(target="destination", travel_cost=0)],
    ))
    state.map.add_location(Location(id="destination", name="終點"))
    state.map.move_to("start")

    direct = TravelService.plan(state, "destination")
    distant = TravelService.plan(
        state,
        "destination",
        allow_distant=True,
    )

    assert direct is not None and direct.location_ids == (
        "start",
        "destination",
    )
    assert direct.total_cost == 3
    assert distant is not None and distant.location_ids == (
        "start",
        "middle",
        "destination",
    )
    assert distant.total_cost == 0


def test_direct_self_move_without_authored_exit_is_rejected():
    """TravelService direct mode retains MapSystem.can_move_to semantics."""
    state = GameState()
    state.map.add_location(Location(id="start", name="起點"))
    state.map.move_to("start")
    before = state.model_dump(mode="json")

    assert state.map.can_move_to(
        "start",
        state.events.flags,
        time_of_day=state.time.time_of_day.value,
    ) is False
    assert TravelService.plan(state, "start") is None
    assert state.model_dump(mode="json") == before


def test_authored_direct_self_exit_honors_its_time_gate_and_cost():
    """An explicit self-loop remains a real authored exit, not a free no-op."""
    state = GameState()
    state.map.add_location(Location(
        id="start",
        name="起點",
        exits=[Exit(
            target="start",
            travel_cost=2,
            requires_time=["night"],
        )],
    ))
    state.map.move_to("start")

    assert TravelService.plan(state, "start") is None

    state.time.set_phase(TimeOfDay.NIGHT)
    plan = TravelService.plan(state, "start")
    assert plan is not None
    assert plan.hop_count == 1
    assert plan.total_cost == 2

    result = TravelService.commit(state, plan)

    assert result.ok is True
    assert result.moved_to == "start"
    assert state.map.current_location_id == "start"
    assert state.time.day == 2
    assert state.time.time_of_day is TimeOfDay.MORNING
