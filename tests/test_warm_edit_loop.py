"""Warm structural-edit loop: edit.* ops, transactions, atomic batches, impact.

These cover the P0 "hot edit loop" — an agent understands, edits, and verifies a
pack inside one warm session, getting the YAML diff *and* the world-model impact
(new dead-ends / unreachable endings / undeclared flags) back in the same
response, with all-or-nothing batching. Everything runs against a *copy* of
demo_pack in a tmp dir, so the bundled pack is never mutated.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from world_gal_game.config import EngineConfig
from world_gal_game.dev.session_server import SessionServer
from world_gal_game.dev.world_model import world_delta, world_snapshot
from world_gal_game.headless import HeadlessSession

REPO = Path(__file__).resolve().parents[1]
DEMO = REPO / "games" / "demo_pack"


@pytest.fixture()
def pack_copy(tmp_path: Path) -> Path:
    """A throwaway copy of demo_pack the tests are free to edit."""
    dest = tmp_path / "demo_copy"
    shutil.copytree(DEMO, dest)
    return dest


def _open(pack_copy: Path) -> HeadlessSession:
    return HeadlessSession.open(EngineConfig(seed=7), pack=str(pack_copy))


# ----------------------------------------------------------------------
# world_model snapshot / delta (pure)


def test_world_snapshot_shape(pack_copy: Path):
    snap = world_snapshot(pack_copy)
    assert set(snap) >= {
        "scenes", "reachable_scenes", "endings", "reachable_endings",
        "dead_ends", "undeclared_flags", "counts",
    }
    # demo_pack ships several scenes and at least one reachable ending.
    assert snap["counts"]["scenes"] > 0
    assert snap["reachable_endings"]


def test_world_delta_clean_when_identical(pack_copy: Path):
    snap = world_snapshot(pack_copy)
    delta = world_delta(snap, snap)
    assert delta["clean"] is True
    assert delta["regressions"] == []


# ----------------------------------------------------------------------
# autocommit edit -> diff + impact in one response


def test_add_orphan_scene_flags_regression(pack_copy: Path):
    sess = _open(pack_copy)
    r = sess.edit("edit.add_scene", {"scene": {
        "id": "qa_orphan", "title": "QA Orphan",
        "lines": [{"speaker": "narrator", "text": "nobody routes here"}],
    }})
    assert r["ok"] is True and r["changed"] is True
    assert r["diff"]                      # a YAML diff came back
    impact = r["impact"]
    assert "qa_orphan" in impact["scenes_added"]
    # A new scene nothing links to is an orphan -> regression, clean False.
    assert impact["clean"] is False
    assert any(reg["kind"] == "orphan_scenes" and "qa_orphan" in reg["items"]
               for reg in impact["regressions"])


def test_wiring_a_choice_resolves_the_orphan(pack_copy: Path):
    sess = _open(pack_copy)
    intro = sess.meta.get("intro_scene")
    assert intro, "demo_pack should declare an intro_scene"
    sess.edit("edit.add_scene", {"scene": {
        "id": "qa_target", "title": "QA Target",
        "lines": [{"speaker": "narrator", "text": "reached"}],
    }})
    # Wire a reachable scene to it; the orphan should become reachable.
    r = sess.edit("edit.add_choice", {"scene_id": intro, "choice": {
        "id": "qa_go", "text": "go to QA target", "next_scene": "qa_target",
    }})
    assert r["ok"] is True
    impact = r["impact"]
    assert "qa_target" in impact["newly_reachable_scenes"]
    assert impact["clean"] is True


def test_bad_payload_returns_structured_error_no_write(pack_copy: Path):
    sess = _open(pack_copy)
    before = world_snapshot(pack_copy)
    r = sess.edit("edit.add_scene", {"scene": {"title": "no id here"}})
    assert r["ok"] is False and r["changed"] is False
    assert r["error"]["op"] == "add_scene"
    # Nothing was written: the world model is unchanged.
    assert world_snapshot(pack_copy) == before


# ----------------------------------------------------------------------
# transactions: stage many, one aggregate impact, or discard


def test_transaction_commit_aggregates_impact(pack_copy: Path):
    sess = _open(pack_copy)
    intro = sess.meta.get("intro_scene")
    assert sess.begin_edit()["ok"]
    s1 = sess.edit("edit.add_scene", {"scene": {
        "id": "tx_a", "title": "TX A",
        "lines": [{"speaker": "narrator", "text": "a"}]}})
    assert s1.get("staged") is True and "impact" not in s1
    sess.edit("edit.add_choice", {"scene_id": intro, "choice": {
        "id": "tx_go", "text": "to A", "next_scene": "tx_a"}})
    commit = sess.commit_edit()
    assert commit["ok"] is True and commit["changed"] is True
    assert "tx_a" in commit["impact"]["scenes_added"]
    # tx_a is wired from the intro, so it lands reachable -> no orphan.
    assert commit["impact"]["clean"] is True


def test_transaction_rollback_discards(pack_copy: Path):
    sess = _open(pack_copy)
    before = world_snapshot(pack_copy)
    sess.begin_edit()
    sess.edit("edit.add_scene", {"scene": {
        "id": "tx_discard", "title": "X",
        "lines": [{"speaker": "narrator", "text": "x"}]}})
    rb = sess.rollback_edit()
    assert rb["ok"] is True and rb["discarded"] is True
    assert world_snapshot(pack_copy) == before


def test_reload_makes_new_scene_playable(pack_copy: Path):
    sess = _open(pack_copy)
    sess.edit("edit.add_scene", {"scene": {
        "id": "qa_play", "title": "QA Play",
        "lines": [{"speaker": "narrator", "text": "playable"}]}})
    # Before reload the live session doesn't know the scene.
    assert "qa_play" not in sess.state.story.scenes
    sess.reload_content()
    assert "qa_play" in sess.state.story.scenes
    started = sess.start_scene("qa_play")
    assert started.get("ok", True)


# ----------------------------------------------------------------------
# uniform envelope: every runtime op reports `changed`


def test_runtime_ops_report_changed(pack_copy: Path):
    sess = _open(pack_copy)
    results = sess.run_script([
        {"op": "set_flag", "key": "qa_probe", "value": True},
        {"op": "inspect"},
    ])
    by_op = {r["op"]: r for r in results}
    assert by_op["set_flag"]["changed"] is True
    assert by_op["inspect"]["changed"] is False


# ----------------------------------------------------------------------
# SessionServer atomic batch: all-or-nothing across state + edits


def test_atomic_batch_rolls_back_on_failure(pack_copy: Path):
    sess = _open(pack_copy)
    server = SessionServer(sess)
    before = world_snapshot(pack_copy)
    # First op is a valid edit; second is invalid (missing id) -> whole batch
    # must roll back, leaving disk untouched.
    line = ('{"ops": ['
            '{"op": "edit.add_scene", "scene": {"id": "atomic_ok", "title": "ok",'
            ' "lines": [{"speaker": "narrator", "text": "ok"}]}},'
            '{"op": "edit.add_scene", "scene": {"title": "no id"}}'
            '], "atomic": true}')
    resp = server.handle(line)
    import json
    obj = json.loads(resp)
    assert obj["ok"] is False
    assert obj["atomic"] == "rolled_back"
    assert world_snapshot(pack_copy) == before


def test_atomic_batch_commits_on_success(pack_copy: Path):
    sess = _open(pack_copy)
    server = SessionServer(sess)
    intro = sess.meta.get("intro_scene")
    import json
    line = json.dumps({"ops": [
        {"op": "edit.add_scene", "scene": {
            "id": "atomic_two", "title": "two",
            "lines": [{"speaker": "narrator", "text": "two"}]}},
        {"op": "edit.add_choice", "scene_id": intro, "choice": {
            "id": "atomic_go", "text": "go", "next_scene": "atomic_two"}},
    ], "atomic": True})
    obj = json.loads(server.handle(line))
    assert obj["ok"] is True
    assert obj["atomic"] == "committed"
    assert "atomic_two" in obj["impact"]["scenes_added"]
    assert "atomic_two" in world_snapshot(pack_copy)["scenes"]


# ----------------------------------------------------------------------
# P2: quest / clue / achievement / resource mutators (warm + direct)


def test_pack_editor_collection_mutators_roundtrip(pack_copy: Path):
    from world_gal_game.content_loader import load_pack
    from world_gal_game.dev.pack_editor import PackEditor

    editor = PackEditor(pack_copy)  # immediate mode
    editor.add_quest({"id": "qa_quest", "title": "QA Quest",
                      "objectives": [{"id": "o1", "text": "do it"}]})
    editor.add_clue({"id": "qa_clue", "title": "QA Clue",
                     "requires": [{"kind": "flag", "target": "prologue_done"}]})
    editor.add_achievement({"id": "qa_ach", "title": "QA Ach",
                            "requires": [{"kind": "flag", "target": "prologue_done"}]})
    editor.add_resource({"id": "qa_gold", "name": "Gold", "starting": 5})
    # The pack still loads and every new entry is registered.
    state, _npcs, _meta = load_pack(pack_copy / "content")
    assert "qa_quest" in state.quests.quests
    assert "qa_clue" in state.clues.clues
    assert "qa_ach" in state.achievements.achievements
    assert state.resources.get("qa_gold") == 5


def test_edit_add_achievement_flags_undeclared_dependency(pack_copy: Path):
    sess = _open(pack_copy)
    r = sess.edit("edit.add_achievement", {"achievement": {
        "id": "qa_secret", "title": "Secret",
        "requires": [{"kind": "flag", "target": "qa_never_declared"}]}})
    assert r["ok"] is True and r["changed"] is True
    impact = r["impact"]
    # The new achievement reads a flag nothing declares -> impact catches it,
    # and the achievements count ticks up by one.
    assert "qa_never_declared" in impact["new_undeclared_flags"]
    assert impact["counts_delta"].get("achievements") == 1


def test_edit_add_quest_duplicate_id_is_structured_error(pack_copy: Path):
    sess = _open(pack_copy)
    sess.edit("edit.add_quest", {"quest": {"id": "dup_q", "title": "A"}})
    r = sess.edit("edit.add_quest", {"quest": {"id": "dup_q", "title": "B"}})
    assert r["ok"] is False
    assert r["error"]["field"] == "id"
