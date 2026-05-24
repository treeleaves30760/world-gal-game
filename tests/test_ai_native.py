"""AI-Coding-Native runtime: trace, diff, snapshot/restore, new run_script ops,
affordances, and the determinism seed contract (Phase 3)."""
from __future__ import annotations

import pathlib

import pytest

import world_gal_game
from world_gal_game.config import EngineConfig, _PERSISTED_SETTING_FIELDS
from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import Effect
from world_gal_game.headless import HeadlessSession


@pytest.fixture
def sess():
    return HeadlessSession.open(EngineConfig(seed=123), pack="demo_pack")


# ----- new run_script ops -------------------------------------------------

def test_apply_op_runs_effect_and_reports_diff(sess):
    results = sess.run_script([
        {"op": "apply", "effect": {"kind": "affection",
                                   "target": "heroine_1", "value": 6}},
    ])
    r = results[0]
    assert r["ok"] is True
    assert r["result"]["new"] == 6
    # The per-step diff pinpoints exactly what changed.
    assert any("heroine_1" in path for path in r.get("diff", {}))


def test_check_op_evaluates_condition(sess):
    sess.run_script([{"op": "apply",
                      "effect": {"kind": "affection", "target": "h", "value": 4}}])
    res = sess.run_script([
        {"op": "check", "condition": {"kind": "affection_gte",
                                      "target": "h", "value": 3}},
        {"op": "check", "condition": {"kind": "affection_gte",
                                      "target": "h", "value": 99}},
    ])
    assert res[0]["result"] is True
    assert res[1]["result"] is False


def test_assert_op_reports_pass_and_fail(sess):
    res = sess.run_script([
        {"op": "set_flag", "key": "x", "value": True},
        {"op": "assert", "flag": "x"},
        {"op": "assert", "flag": "nope"},
    ])
    assert res[1]["ok"] is True
    assert res[2]["ok"] is False
    assert res[2]["actual"] in (None, False)


# ----- snapshot / restore branch exploration ------------------------------

def test_snapshot_restore_round_trips(sess):
    res = sess.run_script([
        {"op": "snapshot", "name": "base"},
        {"op": "apply", "effect": {"kind": "affection",
                                   "target": "heroine_1", "value": 10}},
        {"op": "assert", "affection": "heroine_1", "gte": 10},
        {"op": "restore", "name": "base"},
        {"op": "assert", "affection": "heroine_1", "gte": 10},
    ])
    assert res[2]["ok"] is True            # before restore
    assert res[3]["ok"] is True            # restore succeeded
    assert res[4]["ok"] is False           # reverted after restore


def test_restore_preserves_transient_meta_bridges(sess):
    # The live plugin manager / npc registry must survive a restore.
    assert "__plugin_manager__" in sess.state.meta
    snap = sess.snapshot()
    assert "__plugin_manager__" not in snap.get("meta", {})  # stripped in snapshot
    sess.state.events.set_flag("temp", True)
    sess.restore(snap)
    assert "__plugin_manager__" in sess.state.meta            # re-attached
    assert not sess.state.events.get_flag("temp")             # state reverted


def test_restore_missing_snapshot_errors(sess):
    res = sess.run_script([{"op": "restore", "name": "ghost"}])
    assert res[0]["ok"] is False


# ----- execution trace ----------------------------------------------------

def test_trace_records_applied_effects(sess):
    sess.run_script([
        {"op": "apply", "effect": {"kind": "set_flag", "target": "a"}},
        {"op": "apply", "effect": {"kind": "affection",
                                   "target": "heroine_1", "value": 2}},
    ])
    kinds = [e.get("kind") for e in sess.transcript if e["event"] == "effect"]
    assert "set_flag" in kinds and "affection" in kinds
    # entries are ordered with a monotonic seq
    seqs = [e["seq"] for e in sess.transcript]
    assert seqs == sorted(seqs)


def test_trace_detaches_after_run(sess):
    from world_gal_game.plugins import HOOK_REGISTRY
    from world_gal_game.dev.trace import OWNER
    sess.run_script([{"op": "set_flag", "key": "z"}])
    # No leaked trace hooks remain registered globally.
    leaked = [e for ev in HOOK_REGISTRY.list_events()
              for e in HOOK_REGISTRY.handlers_for(ev) if e.plugin_id == OWNER]
    assert leaked == []


# ----- affordances --------------------------------------------------------

def test_affordances_reports_action_space(sess):
    aff = sess.affordances()
    assert set(aff) >= {"location", "exits", "choices", "scenes_available",
                        "applicable_effects", "applicable_conditions"}
    assert "affection" in aff["applicable_effects"]
    assert "affection_gte" in aff["applicable_conditions"]


# ----- determinism seed contract ------------------------------------------

def test_rng_deterministic_for_same_seed():
    a, b = GameState(), GameState()
    a.meta["__seed__"] = 7
    b.meta["__seed__"] = 7
    assert [a.rng().random() for _ in range(5)] == [b.rng().random() for _ in range(5)]


def test_rng_differs_for_different_seed():
    a, b = GameState(), GameState()
    a.meta["__seed__"] = 1
    b.meta["__seed__"] = 2
    assert [a.rng().random() for _ in range(5)] != [b.rng().random() for _ in range(5)]


def test_seed_threads_from_config():
    s = HeadlessSession.open(EngineConfig(seed=99), pack="demo_pack")
    assert s.state.meta.get("__seed__") == 99
    # and it stays out of saves/snapshots (transient __ key)
    assert "__seed__" not in s.snapshot().get("meta", {})


def test_seed_is_not_a_persisted_user_setting():
    assert "seed" not in _PERSISTED_SETTING_FIELDS


def test_engine_uses_no_uncontrolled_random():
    """Engine code must route randomness through GameState.rng() (seedable),
    never the global ``random`` module — the determinism invariant."""
    root = pathlib.Path(world_gal_game.__file__).parent
    offenders = []
    for py in root.rglob("*.py"):
        for line in py.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("import random") or s.startswith("from random "):
                if py.name != "game_state.py":  # the controlled rng() helper
                    offenders.append(str(py.relative_to(root)))
                    break
    assert offenders == [], f"uncontrolled random import in: {offenders}"


# ----- diff helper --------------------------------------------------------

def test_diff_reports_leaf_changes():
    from world_gal_game.dev.diff import diff
    out = diff({"a": 1, "b": {"c": 2}}, {"a": 1, "b": {"c": 3}, "d": 4})
    assert out["b.c"] == {"from": 2, "to": 3}
    assert out["d"] == {"from": None, "to": 4}
