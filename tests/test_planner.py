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


def test_checkpoint_preserves_real_choice_boundary_without_early_choice() -> None:
    """The final line and following choice can share one story cursor."""
    from world_gal_game.core.story_graph import Choice, Line, Scene

    planner = Planner("demo_pack")
    sess = HeadlessSession.open(EngineConfig(seed=42), pack="demo_pack")
    sess.state.story.add_scene(Scene(
        id="planner_choice_boundary_probe",
        lines=[Line(text="先讀完這一行。")],
        choices=[Choice(id="continue_probe", text="現在才可選")],
    ))

    sess.start_scene("planner_choice_boundary_probe")
    line_key = Planner._state_key(sess)
    assert sess.affordances()["choices"] == []
    assert not any(
        op["op"] == "choose"
        for op in planner._actions(
            sess,
            explore_moves=False,
            explore_scenes=False,
            explore_examines=False,
        )
    )

    sess.next_line()
    choice_key = Planner._state_key(sess)
    assert sess.state.story.current_line_index == 1
    assert choice_key != line_key
    checkpoint = Planner._checkpoint(sess)

    # Raw GameState restore intentionally clears presentation state. The
    # planner checkpoint restores both halves so its frontier remains at the
    # exact action boundary it actually reached.
    sess.restore(checkpoint[0])
    assert sess.affordances()["choices"] == []
    Planner._restore_checkpoint(sess, checkpoint)
    assert [row["id"] for row in sess.affordances()["choices"]] == [
        "continue_probe",
    ]


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


def test_state_key_distinguishes_runtime_dimensions() -> None:
    """The visited key must separate states that differ only in inventory /
    resources / affection / time — the dimensions the old key ignored."""
    from world_gal_game.core.affection import CharacterAffection

    sess = HeadlessSession.open(EngineConfig(seed=42), pack="demo_pack")
    keys = [Planner._state_key(sess)]

    sess.state.inventory.counts["qa_coin"] = 1
    keys.append(Planner._state_key(sess))

    sess.state.resources.values["qa_mana"] = 7
    keys.append(Planner._state_key(sess))

    sess.state.affection.characters["qa_npc"] = CharacterAffection(
        character_id="qa_npc", stats={"affection": 10})
    keys.append(Planner._state_key(sess))

    sess.state.time.day += 1
    keys.append(Planner._state_key(sess))

    # Each mutation produced a distinct key (no collapsing).
    assert len(set(keys)) == len(keys)
    # And re-reading an unchanged state is stable (loops still collapse).
    assert Planner._state_key(sess) == keys[-1]


def test_plan_result_forbids_extra_fields() -> None:
    """PlanResult is a strict pydantic v2 model (extra='forbid')."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PlanResult(found=False, goal={}, path=[], depth=0,
                   nodes_explored=0, surprise="x")
