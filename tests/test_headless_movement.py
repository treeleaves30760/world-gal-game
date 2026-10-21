"""Headless movement must use the same travel semantics as the GUI."""
from __future__ import annotations

from world_gal_game.config import EngineConfig
from world_gal_game.core.game_state import GameState
from world_gal_game.core.map_system import Exit, Location, SceneHook
from world_gal_game.core.story_graph import Line, Scene
from world_gal_game.core.time_system import TimeOfDay
from world_gal_game.dialogue.dialogue_engine import DialogueEngine
from world_gal_game.headless import HeadlessSession
from world_gal_game.npc.llm_brain import EchoBrain
from world_gal_game.npc.npc_base import NPCRegistry


def _headless(state: GameState) -> HeadlessSession:
    return HeadlessSession(
        config=EngineConfig(),
        state=state,
        npcs=NPCRegistry(),
        brain=EchoBrain(),
        dialogue=DialogueEngine(state),
        meta={},
    )


def _session(
    exit_obj: Exit,
    *,
    destination_hooks: list[SceneHook] | None = None,
) -> HeadlessSession:
    state = GameState()
    state.map.add_location(Location(
        id="start",
        name="起點",
        exits=[exit_obj],
    ))
    state.map.add_location(Location(
        id="destination",
        name="終點",
        scene_hooks=destination_hooks or [],
    ))
    state.map.move_to("start")
    state.story.add_scene(Scene(
        id="arrival_scene",
        lines=[Line(text="抵達。")],
    ))
    return _headless(state)


def test_headless_default_travel_cost_keeps_time_unchanged():
    session = _session(Exit(target="destination"))
    before = session.state.time.model_dump()

    result = session.move_to("destination")

    assert result == {"ok": True, "moved_to": "destination"}
    assert session.state.time.model_dump() == before


def test_headless_positive_travel_cost_advances_exact_number_of_phases():
    session = _session(Exit(target="destination", travel_cost=3))

    result = session.move_to("destination")

    assert result["ok"] is True
    assert session.state.time.time_of_day is TimeOfDay.EVENING


def test_headless_checks_arrival_hooks_after_travel_time_advances():
    session = _session(
        Exit(target="destination", travel_cost=2),
        destination_hooks=[SceneHook(
            scene_id="arrival_scene",
            trigger="auto",
            requires_time=["afternoon"],
        )],
    )

    result = session.move_to("destination")

    assert session.state.time.time_of_day is TimeOfDay.AFTERNOON
    assert result["scene_triggered"] == "arrival_scene"
    assert result["presentation"]["scene_id"] == "arrival_scene"


def test_headless_rejects_exit_outside_its_authored_time_window():
    session = _session(Exit(
        target="destination",
        requires_time=["night"],
    ))
    before = session.state.time.model_dump()

    result = session.move_to("destination")

    assert result["ok"] is False
    assert session.state.map.current_location_id == "start"
    assert session.state.time.model_dump() == before

    session.state.time.set_phase(TimeOfDay.NIGHT)
    assert session.move_to("destination")["ok"] is True


def test_headless_direct_move_can_initialize_an_empty_current_location():
    """Preserve MapSystem's legacy current=None direct-entry behaviour."""
    state = GameState()
    state.map.add_location(Location(id="destination", name="終點"))
    session = _headless(state)
    before_time = state.time.model_dump()

    assert state.map.current_location_id is None
    assert state.map.can_move_to(
        "destination",
        state.events.flags,
        time_of_day=state.time.time_of_day.value,
    ) is True

    result = session.move_to("destination")

    assert result == {"ok": True, "moved_to": "destination"}
    assert state.map.current_location_id == "destination"
    assert state.map.visited == {"destination"}
    assert state.time.model_dump() == before_time


def test_headless_distant_travel_is_explicit_and_atomic():
    session = _session(Exit(target="middle", travel_cost=1))
    session.state.map.add_location(Location(
        id="middle",
        name="中繼",
        exits=[Exit(target="destination", travel_cost=1)],
    ))
    session.meta["travel"] = {"scope": "graph"}

    assert session.move_to("destination")["ok"] is False
    option = next(
        row for row in session.affordances()["travel_options"]
        if row["target"] == "destination"
    )
    assert option["path"] == ["start", "middle", "destination"]
    assert option["cost"] == 2
    assert option["arrival"]["time_of_day"] == "afternoon"

    result = session.move_to("destination", allow_distant=True)

    assert result["ok"] is True
    assert result["moved_to"] == "destination"
    assert session.state.map.current_location_id == "destination"
    assert "middle" not in session.state.map.visited
    assert session.state.time.time_of_day is TimeOfDay.AFTERNOON


def test_headless_distant_travel_cannot_bypass_pack_graph_gate():
    session = _session(Exit(target="middle", travel_cost=1))
    session.state.map.add_location(Location(
        id="middle",
        name="中繼",
        exits=[Exit(target="destination", travel_cost=1)],
    ))
    session.meta["travel"] = {
        "scope": "graph",
        "requires_flags": ["map_primer_seen"],
        "minimum_cost": 1,
    }
    before = session.state.model_dump(mode="json")

    blocked = session.move_to("destination", allow_distant=True)

    assert blocked == {"ok": False, "error": "can't move to destination"}
    assert session.state.model_dump(mode="json") == before
    assert session.affordances()["travel_options"] == []

    session.state.events.set_flag("map_primer_seen", True)
    moved = session.move_to("destination", allow_distant=True)

    assert moved["ok"] is True
    assert moved["moved_to"] == "destination"
    assert session.state.time.time_of_day is TimeOfDay.AFTERNOON


def test_headless_guided_destinations_match_exact_current_and_future_events():
    session = _session(
        Exit(target="destination"),
        destination_hooks=[SceneHook(
            scene_id="arrival_scene",
            trigger="auto",
            requires_time=["afternoon"],
        )],
    )
    session.meta["travel"] = {
        "scope": "graph",
        "minimum_cost": 1,
        "guided_story": {
            "future_horizon_phases": 6,
            "max_primary": 3,
            "markers": {
                "arrival_scene": {
                    "npc_ids": ["heroine"],
                    "teaser": "午後見。",
                },
            },
        },
    }

    offer = next(
        row for row in session.affordances()["guided_destinations"]
        if row["target"] == "destination"
    )

    # Leaving now arrives at noon; waiting once and then paying the one-slot
    # map action arrives in the authored afternoon window.
    assert offer["visibility"] == "primary"
    assert offer["status"] == "future"
    assert offer["action"] == "wait"
    assert offer["future"]["wait_phases"] == 1
    assert offer["travel"]["cost"] == 1
    assert offer["travel"]["arrival"]["time_of_day"] == "afternoon"
    assert offer["event"] == {
        "scene_id": "arrival_scene",
        "title": "arrival_scene",
        "npc_ids": ["heroine"],
        "teaser": "午後見。",
    }


def test_headless_exposes_only_direct_guidance_before_graph_gate():
    state = GameState()
    state.map.add_location(Location(
        id="start",
        name="起點",
        exits=[
            Exit(target="direct_story"),
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
        name="中繼",
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
    session = _headless(state)
    session.meta["travel"] = {
        "scope": "graph",
        "requires_flags": ["map_ready"],
        "minimum_cost": 1,
        "guided_story": {
            "direct_before_graph": True,
            "require_marker": True,
            "markers": {
                "direct_scene": {},
                "distant_scene": {},
            },
        },
    }

    affordances = session.affordances()
    offers = {
        row["target"]: row
        for row in affordances["guided_destinations"]
    }

    assert affordances["travel_options"] == []
    assert offers["direct_story"]["available"] is True
    assert offers["direct_story"]["travel"]["path"] == [
        "start",
        "direct_story",
    ]
    assert offers["direct_story"]["travel"]["cost"] == 0
    assert offers["distant_story"]["available"] is False
    assert offers["distant_story"]["action"] == "none"
    assert offers["distant_story"]["travel"] is None


def test_headless_marks_unfinished_guided_map_handoff() -> None:
    state = GameState()
    state.map.add_location(Location(id="start", name="起點"))
    state.map.move_to("start")
    state.story.add_scene(Scene(
        id="guided_closure",
        lines=[Line(text="這一站完成。")],
    ))
    session = _headless(state)
    session.meta["travel"] = {
        "scope": "graph",
        "guided_story": {
            "auto_reopen": True,
            "progress": {
                "stages": [
                    {"label": "導引", "flags": ["guide_done"]},
                ],
            },
        },
    }

    assert session.start_scene("guided_closure")["presentation"]["kind"] == (
        "line"
    )
    ended = session.next_line()["presentation"]

    assert ended["kind"] == "end"
    assert ended["next_surface"] == "map"
    assert ended["map_handoff"] == "guided_story"
