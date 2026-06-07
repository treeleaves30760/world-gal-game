"""Ending evaluation (mirrors tests/test_achievements.py)."""
from world_gal_game.core.clear_data import ClearData
from world_gal_game.core.endings import Ending, EndingTracker
from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import Condition, Effect


def test_ending_unlocks_when_flag_set():
    s = GameState()
    ending = Ending(id="ending_x", title="X", requires=[
        Condition(kind="flag", target="ending_x"),
    ])
    s.endings.register(ending)
    # Not yet
    s.apply(Effect(kind="set_flag", target="other"))
    assert "ending_x" not in s.endings.unlocked
    # Flag set → unlocked on next apply_all (because apply_all reevaluates).
    out = s.apply_all([Effect(kind="set_flag", target="ending_x")])
    assert "ending_x" in s.endings.unlocked
    # The re-eval also surfaces the unlock in apply_all's result list and the
    # event log (kind="unlock").
    assert any(d.get("kind") == "ending" and d.get("id") == "ending_x"
               for d in out)
    assert any(e.kind == "unlock" and e.data.get("ending") == "ending_x"
               for e in s.events.entries)


def test_hidden_ending_invisible_until_unlocked():
    t = EndingTracker()
    t.register(Ending(id="a", title="visible", hidden=False))
    t.register(Ending(id="b", title="secret", hidden=True))
    visible_ids = [e.id for e in t.visible_to_player()]
    assert "a" in visible_ids
    assert "b" not in visible_ids
    # Once unlocked, the hidden one becomes visible.
    t.unlocked["b"] = "2026-01-01"
    visible_ids = [e.id for e in t.visible_to_player()]
    assert "b" in visible_ids


def test_ending_with_forbids():
    s = GameState()
    s.endings.register(Ending(
        id="a", title="A",
        requires=[Condition(kind="flag", target="ending_a")],
        forbids=[Condition(kind="flag", target="cheated")],
    ))
    s.apply_all([
        Effect(kind="set_flag", target="cheated"),
        Effect(kind="set_flag", target="ending_a"),
    ])
    assert "a" not in s.endings.unlocked


def test_ending_round_trip_via_route_id():
    e = Ending(id="ending_h1", title="湖畔的承諾", route_id="heroine_1",
               hidden=True)
    restored = Ending.model_validate(e.model_dump())
    assert restored.route_id == "heroine_1"
    assert restored.hidden is True


# --------------------------------------------------------------------------
# Fix 3: EndingTracker.get -> ClearData.cleared_routes -> cleared_route gate.
# Regression: without EndingTracker.get, record_from_state could never read an
# ending's route_id, so cleared_routes stayed empty and NG+ gates never fired.
# --------------------------------------------------------------------------
def test_ending_tracker_get_returns_registered_ending():
    t = EndingTracker()
    t.register(Ending(id="ending_x", title="X", route_id="route_x"))
    assert t.get("ending_x").route_id == "route_x"
    assert t.get("missing") is None


def test_clear_data_records_route_from_unlocked_ending():
    """Clearing a route's ending records its route_id into cleared_routes."""
    s = GameState()
    s.endings.register(Ending(id="ending_qingyi_lover", title="湖畔",
                              route_id="qingyi"))
    s.endings.unlocked["ending_qingyi_lover"] = "2026-01-01T00:00:00"
    cd = ClearData()
    changed = cd.record_from_state(s)
    assert changed is True
    assert "qingyi" in cd.cleared_routes
    assert "ending_qingyi_lover" in cd.endings_seen


def test_cleared_route_condition_true_on_ng_plus():
    """End-to-end: an ending cleared in a prior run satisfies the
    cleared_route gate on a fresh New Game+ state."""
    prev = GameState()
    prev.endings.register(Ending(id="ending_qingyi", title="戀人",
                                 route_id="qingyi"))
    prev.endings.unlocked["ending_qingyi"] = "2026-01-01T00:00:00"
    cd = ClearData()
    cd.record_from_state(prev)

    fresh = GameState()
    fresh.meta["__clear_data__"] = cd
    assert fresh.evaluate(Condition(kind="cleared_route", target="qingyi"))
    assert not fresh.evaluate(Condition(kind="cleared_route", target="other"))
