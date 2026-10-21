"""Pure distant-route planning without changing legacy direct movement."""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from world_gal_game.core.map_system import Exit, Location, MapSystem
from world_gal_game.core.time_system import DayOfWeek, TimeOfDay, TimeSystem
from world_gal_game.core.travel import (
    TravelMoment,
    TravelPlan,
    plan_travel,
    project_arrival,
)


def _map(*locations: Location, current: str = "a") -> MapSystem:
    result = MapSystem()
    for location in locations:
        result.add_location(location)
    result.move_to(current)
    return result


def test_distant_plan_does_not_change_legacy_direct_can_move_to() -> None:
    map_system = _map(
        Location(id="a", name="A", exits=[Exit(target="b")]),
        Location(id="b", name="B", exits=[Exit(target="c")]),
        Location(id="c", name="C"),
    )
    departure = TimeSystem()

    assert map_system.can_move_to(
        "c", {}, time_of_day=TimeOfDay.MORNING.value
    ) is False

    plan = map_system.plan_travel("c", flags={}, departure=departure)

    assert isinstance(plan, TravelPlan)
    assert plan.location_ids == ("a", "b", "c")
    assert plan.hop_count == 2
    assert plan.is_direct is False
    # Planning did not make the legacy direct API permissive.
    assert map_system.can_move_to(
        "c", {}, time_of_day=TimeOfDay.MORNING.value
    ) is False


def test_plan_checks_each_edge_at_that_legs_projected_departure_time() -> None:
    map_system = _map(
        Location(
            id="a",
            name="A",
            exits=[
                Exit(target="wrong", travel_cost=1),
                Exit(target="timed", travel_cost=2),
            ],
        ),
        Location(
            id="wrong",
            name="Wrong",
            exits=[Exit(
                target="goal",
                travel_cost=1,
                requires_time=[TimeOfDay.MORNING.value],
            )],
        ),
        Location(
            id="timed",
            name="Timed",
            exits=[Exit(
                target="goal",
                travel_cost=1,
                requires_time=[TimeOfDay.AFTERNOON.value],
            )],
        ),
        Location(id="goal", name="Goal"),
    )

    plan = plan_travel(
        map_system,
        "goal",
        flags={},
        departure=TimeSystem(phase_index=0),
    )

    assert plan is not None
    assert plan.location_ids == ("a", "timed", "goal")
    assert plan.total_cost == 3
    assert plan.legs[0].departure.time_of_day is TimeOfDay.MORNING
    assert plan.legs[0].arrival.time_of_day is TimeOfDay.AFTERNOON
    assert plan.legs[1].departure.time_of_day is TimeOfDay.AFTERNOON
    assert plan.arrival.time_of_day is TimeOfDay.EVENING


def test_planner_does_not_insert_implicit_waiting() -> None:
    map_system = _map(
        Location(id="a", name="A", exits=[Exit(target="b")]),
        Location(
            id="b",
            name="B",
            exits=[Exit(
                target="goal",
                requires_time=[TimeOfDay.NIGHT.value],
            )],
        ),
        Location(id="goal", name="Goal"),
    )

    assert map_system.plan_travel(
        "goal",
        flags={},
        departure=TimeSystem(phase_index=0),
    ) is None


@pytest.mark.parametrize("reverse_exits", [False, True])
def test_equal_routes_have_deterministic_lexicographic_tie_break(
    reverse_exits: bool,
) -> None:
    exits = [
        Exit(target="zeta", travel_cost=1),
        Exit(target="alpha", travel_cost=1),
    ]
    if reverse_exits:
        exits.reverse()
    map_system = _map(
        Location(id="a", name="A", exits=exits),
        Location(
            id="zeta",
            name="Zeta",
            exits=[Exit(target="goal", travel_cost=1)],
        ),
        Location(
            id="alpha",
            name="Alpha",
            exits=[Exit(target="goal", travel_cost=1)],
        ),
        Location(id="goal", name="Goal"),
    )

    plan = map_system.plan_travel(
        "goal", flags={}, departure=TimeSystem()
    )

    assert plan is not None
    assert plan.location_ids == ("a", "alpha", "goal")
    assert plan.total_cost == 2


def test_route_objective_prefers_cost_then_hops_before_path_name() -> None:
    map_system = _map(
        Location(
            id="a",
            name="A",
            exits=[
                Exit(target="alpha", travel_cost=1),
                Exit(target="zeta", travel_cost=2),
            ],
        ),
        Location(
            id="alpha",
            name="Alpha",
            exits=[Exit(target="middle", travel_cost=1)],
        ),
        Location(
            id="middle",
            name="Middle",
            exits=[Exit(target="goal", travel_cost=0)],
        ),
        Location(
            id="zeta",
            name="Zeta",
            exits=[Exit(target="goal", travel_cost=0)],
        ),
        Location(id="goal", name="Goal"),
    )

    plan = map_system.plan_travel(
        "goal", flags={}, departure=TimeSystem()
    )

    assert plan is not None
    # Both routes cost 2; the two-hop route beats lexicographically earlier
    # alpha -> middle because hop count is the second objective.
    assert plan.location_ids == ("a", "zeta", "goal")


def test_arrival_projection_rolls_day_and_week_without_mutating_clock() -> None:
    departure = TimeSystem(
        day=7,
        weekday_index=6,
        phase_index=5,
    )
    before = departure.model_dump()

    arrival = project_arrival(departure, 2)

    assert arrival.day == 8
    assert arrival.day_of_week is DayOfWeek.MON
    assert arrival.time_of_day is TimeOfDay.NOON
    assert departure.model_dump() == before


def test_plan_is_immutable_and_planning_is_side_effect_free() -> None:
    map_system = _map(
        Location(
            id="a",
            name="A",
            exits=[Exit(target="b", travel_cost=2)],
        ),
        Location(id="b", name="B"),
    )
    departure = TimeSystem()
    map_before = map_system.model_dump()
    time_before = departure.model_dump()

    plan = map_system.plan_travel(
        "b", flags={}, departure=departure
    )

    assert plan is not None
    assert map_system.model_dump() == map_before
    assert departure.model_dump() == time_before
    with pytest.raises(FrozenInstanceError):
        plan.total_cost = 99  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        plan.arrival.day = 99  # type: ignore[misc]


def test_flag_and_location_gates_apply_to_every_distant_leg() -> None:
    map_system = _map(
        Location(id="a", name="A", exits=[Exit(
            target="b",
            requires_flags=["bridge_open"],
        )]),
        Location(id="b", name="B", exits=[Exit(target="goal")]),
        Location(
            id="goal",
            name="Goal",
            forbids_flags=["campus_closed"],
        ),
    )

    assert map_system.plan_travel(
        "goal", flags={}, departure=TimeSystem()
    ) is None
    assert map_system.plan_travel(
        "goal",
        flags={"bridge_open": True, "campus_closed": True},
        departure=TimeSystem(),
    ) is None
    assert map_system.plan_travel(
        "goal",
        flags={"bridge_open": True},
        departure=TimeSystem(),
    ) is not None


def test_current_destination_returns_zero_leg_plan() -> None:
    map_system = _map(Location(id="a", name="A"))
    departure = TravelMoment(day=3, weekday_index=2, phase_index=4)

    plan = map_system.plan_travel(
        "a", flags={}, departure=departure
    )

    assert plan == TravelPlan(
        origin_id="a",
        destination_id="a",
        path_location_ids=("a",),
        legs=(),
        total_cost=0,
        departure=departure,
        arrival=departure,
    )


def test_unknown_unreachable_and_zero_cost_cycle_are_safe() -> None:
    map_system = _map(
        Location(id="a", name="A", exits=[Exit(target="b")]),
        Location(
            id="b",
            name="B",
            exits=[Exit(target="a")],
        ),
        Location(id="island", name="Island"),
    )

    assert map_system.plan_travel(
        "missing", flags={}, departure=TimeSystem()
    ) is None
    assert map_system.plan_travel(
        "island", flags={}, departure=TimeSystem()
    ) is None


def test_negative_travel_cost_is_rejected_at_model_boundary() -> None:
    with pytest.raises(ValidationError):
        Exit(target="past", travel_cost=-1)


def test_parallel_exits_follow_legacy_first_match_semantics() -> None:
    map_system = _map(
        Location(
            id="a",
            name="A",
            exits=[
                Exit(target="goal", travel_cost=3),
                Exit(target="goal", travel_cost=1),
            ],
        ),
        Location(id="goal", name="Goal"),
    )

    plan = map_system.plan_travel(
        "goal", flags={}, departure=TimeSystem()
    )

    assert plan is not None
    assert plan.total_cost == 3
    assert plan.legs[0].exit_ordinal == 0
    assert plan.legs[0].travel_cost == 3


def test_later_parallel_exit_cannot_bypass_blocked_first_match() -> None:
    map_system = _map(
        Location(
            id="a",
            name="A",
            exits=[
                Exit(
                    target="goal",
                    requires_flags=["first_exit_open"],
                ),
                Exit(target="goal"),
            ],
        ),
        Location(id="goal", name="Goal"),
    )

    assert map_system.can_move_to(
        "goal", {}, time_of_day=TimeOfDay.MORNING.value
    ) is False
    assert map_system.plan_travel(
        "goal", flags={}, departure=TimeSystem()
    ) is None


def test_negative_projection_is_rejected() -> None:
    with pytest.raises(ValueError, match=">= 0"):
        project_arrival(TimeSystem(), -1)
