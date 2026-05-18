"""Unit tests for Exit travel restrictions: flags, time, and travel_cost.

These tests instantiate Exit/Location/MapSystem directly so they don't
depend on any game pack — coverage that the previously deleted
test_qa_map suite used to provide.
"""
from __future__ import annotations

import pytest

from world_gal_game.core.map_system import Exit, Location, MapSystem


# ---------------------------------------------------------------------------
# Exit.is_available — flag and time gating
# ---------------------------------------------------------------------------


def test_exit_default_available():
    e = Exit(target="park")
    assert e.is_available(time_of_day="afternoon", flags={}) is True


def test_exit_requires_flag_blocks_when_missing():
    e = Exit(target="secret_lab", requires_flags=["got_keycard"])
    assert e.is_available("noon", flags={}) is False
    assert e.is_available("noon", flags={"got_keycard": True}) is True


def test_exit_requires_multiple_flags_all_must_match():
    e = Exit(target="vault", requires_flags=["got_keycard", "alarm_off"])
    assert e.is_available("noon", flags={"got_keycard": True}) is False
    assert e.is_available("noon", flags={
        "got_keycard": True, "alarm_off": True}) is True


def test_exit_forbids_flag_blocks_when_set():
    e = Exit(target="library", forbids_flags=["library_closed"])
    assert e.is_available("noon", flags={}) is True
    assert e.is_available("noon", flags={"library_closed": True}) is False


def test_exit_requires_time_blocks_outside_window():
    e = Exit(target="back_alley", requires_time=["night", "midnight"])
    assert e.is_available("afternoon", flags={}) is False
    assert e.is_available("night", flags={}) is True
    assert e.is_available("midnight", flags={}) is True


def test_exit_time_and_flag_gates_combined():
    e = Exit(target="rooftop",
             requires_time=["evening", "night"],
             requires_flags=["roof_unlocked"],
             forbids_flags=["raining"])
    assert e.is_available("noon", flags={"roof_unlocked": True}) is False
    assert e.is_available("evening", flags={}) is False
    assert e.is_available("evening",
                          flags={"roof_unlocked": True}) is True
    assert e.is_available("evening",
                          flags={"roof_unlocked": True,
                                 "raining": True}) is False


def test_exit_unavailable_reason_time_only():
    e = Exit(target="back_alley", requires_time=["night"])
    assert e.unavailable_reason("noon") == "night才能進入"
    assert e.unavailable_reason("night") is None


def test_exit_unavailable_reason_multi_time():
    e = Exit(target="back_alley", requires_time=["evening", "night"])
    assert "evening" in e.unavailable_reason("noon")
    assert "night" in e.unavailable_reason("noon")


# ---------------------------------------------------------------------------
# travel_cost field defaults
# ---------------------------------------------------------------------------


def test_exit_travel_cost_default_zero():
    e = Exit(target="park")
    assert e.travel_cost == 0


def test_exit_travel_cost_explicit():
    e = Exit(target="next_town", travel_cost=3)
    assert e.travel_cost == 3


# ---------------------------------------------------------------------------
# Location.is_accessible — requires/forbids_flags on the location itself
# ---------------------------------------------------------------------------


def test_location_default_accessible():
    loc = Location(id="park", name="公園")
    assert loc.is_accessible({}) is True


def test_location_requires_flag():
    loc = Location(id="secret_room", name="秘密室",
                   requires_flags=["found_key"])
    assert loc.is_accessible({}) is False
    assert loc.is_accessible({"found_key": True}) is True


def test_location_forbids_flag():
    loc = Location(id="cafeteria", name="餐廳",
                   forbids_flags=["cafeteria_closed"])
    assert loc.is_accessible({}) is True
    assert loc.is_accessible({"cafeteria_closed": True}) is False


# ---------------------------------------------------------------------------
# MapSystem.can_move_to — integrates Exit + Location gates
# ---------------------------------------------------------------------------


def _two_room_world() -> MapSystem:
    a = Location(id="a", name="A",
                 exits=[Exit(target="b", requires_flags=["bridge_repaired"])])
    b = Location(id="b", name="B",
                 exits=[Exit(target="a")],
                 forbids_flags=["b_locked"])
    m = MapSystem(locations={"a": a, "b": b},
                  current_location_id="a")
    return m


def test_can_move_to_respects_exit_gate():
    m = _two_room_world()
    assert m.can_move_to("b", flags={}) is False
    assert m.can_move_to("b", flags={"bridge_repaired": True}) is True


def test_can_move_to_respects_destination_lock():
    m = _two_room_world()
    assert m.can_move_to("b", flags={"bridge_repaired": True,
                                     "b_locked": True}) is False


def test_can_move_to_rejects_unreachable_target():
    m = _two_room_world()
    # No exit from a to a non-existent loc.
    assert m.can_move_to("c", flags={}) is False
