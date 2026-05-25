"""Tests for the goal-directed planner (``world_gal_game.dev.planner``).

Exercises the backward-search primitive on ``demo_pack``: a reachable goal
(navigation + a choice that sets ``quest_started``) must return a non-empty op
path that *replays* to the goal on a fresh session, and an impossible goal must
terminate within its node cap with ``found=False``.
"""
from __future__ import annotations

from world_gal_game.config import EngineConfig
from world_gal_game.dev.planner import Planner, PlanResult
from world_gal_game.headless import HeadlessSession


# Reachable purely by navigation + choices: enter the prologue, walk to the
# town square (which auto-triggers meet_heroine), and accept the quest. No
# affection injection is needed, so this goal is guaranteed-reachable.
REACHABLE_GOAL = {"flag": "quest_started"}
SETUP = [{"op": "start_scene", "scene": "prologue"}]


def test_find_path_reachable_goal() -> None:
    result = Planner("demo_pack").find_path(REACHABLE_GOAL, setup=SETUP)

    assert isinstance(result, PlanResult)
    assert result.found is True
    assert result.path, "a reachable goal must return a non-empty op path"
    assert result.depth == len(result.path)
    assert result.nodes_explored > 0
    # Every op in the path is a well-formed search op.
    assert all(isinstance(op, dict) and "op" in op for op in result.path)


def test_found_path_replays_on_fresh_session() -> None:
    """The returned path must actually drive a clean session to the goal."""
    result = Planner("demo_pack").find_path(REACHABLE_GOAL, setup=SETUP)
    assert result.found is True

    sess = HeadlessSession.open(EngineConfig(seed=42), pack="demo_pack")
    sess.run_script(SETUP + result.path)
    assert sess.assert_expect(REACHABLE_GOAL)["ok"] is True


def test_impossible_goal_terminates_bounded() -> None:
    """An unsatisfiable goal stops at the node cap and reports found=False."""
    result = Planner("demo_pack").find_path(
        {"flag": "this_flag_never_exists_zzz"},
        setup=SETUP,
        max_nodes=400,
    )

    assert result.found is False
    assert result.path == []
    assert result.depth == 0
    # Bounded: it explored some nodes but did not exceed the cap.
    assert result.nodes_explored > 0
    assert result.nodes_explored <= 400


def test_plan_result_forbids_extra_fields() -> None:
    """PlanResult is a strict pydantic v2 model (extra='forbid')."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PlanResult(found=False, goal={}, path=[], depth=0,
                   nodes_explored=0, surprise="x")
