"""Current pack geography must replace stale location data from saves."""
from __future__ import annotations

from world_gal_game.core.map_system import (
    Exit,
    Location,
    MapSystem,
    rebind_authored_map_catalog,
)


def _authored() -> MapSystem:
    result = MapSystem()
    result.add_location(Location(
        id="start",
        name="Current start",
        map_x=10,
        exits=[Exit(target="new_place", travel_cost=2)],
    ))
    result.add_location(Location(
        id="new_place",
        name="New in this pack version",
        map_x=80,
    ))
    result.move_to("start")
    return result


def test_rebind_uses_current_catalog_and_saved_progress() -> None:
    authored = _authored()
    saved = MapSystem()
    saved.add_location(Location(
        id="start",
        name="Stale start",
        map_x=99,
        exits=[Exit(target="removed_place")],
    ))
    saved.add_location(Location(id="removed_place", name="Removed"))
    saved.current_location_id = "start"
    saved.visited = {"start", "removed_place"}

    rebound = rebind_authored_map_catalog(authored, saved)

    assert set(rebound.locations) == {"start", "new_place"}
    assert rebound.locations["start"].name == "Current start"
    assert rebound.locations["start"].map_x == 10
    assert rebound.locations["start"].exits[0].target == "new_place"
    assert rebound.current_location_id == "start"
    assert rebound.visited == {"start"}

    # The rebound catalogue is safe to mutate without changing either input.
    rebound.locations["start"].name = "runtime mutation"
    assert authored.locations["start"].name == "Current start"
    assert saved.locations["start"].name == "Stale start"


def test_removed_saved_position_falls_back_to_authored_start() -> None:
    authored = _authored()
    saved = MapSystem(
        locations={
            "removed": Location(id="removed", name="Removed"),
        },
        current_location_id="removed",
        visited={"removed"},
    )

    rebound = rebind_authored_map_catalog(authored, saved)

    assert rebound.current_location_id == "start"
    assert rebound.visited == {"start"}


def test_explicit_pre_location_state_remains_without_location() -> None:
    authored = _authored()
    saved = MapSystem(
        current_location_id=None,
        visited={"start", "removed"},
    )

    rebound = rebind_authored_map_catalog(authored, saved)

    assert rebound.current_location_id is None
    assert rebound.visited == {"start"}
