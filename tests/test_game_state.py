"""GameState: condition evaluation + effect application."""
import pytest

from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import Condition, Effect
from world_gal_game.core.map_system import Location


def _state_with_two_locs() -> GameState:
    s = GameState()
    s.map.add_location(Location(id="home", name="家", exits=["park"]))
    s.map.add_location(Location(id="park", name="公園", exits=["home"]))
    s.map.move_to("home")
    return s


def test_evaluate_flag_conditions():
    s = GameState()
    s.events.set_flag("a", True)
    s.events.set_flag("b", False)
    assert s.evaluate(Condition(kind="flag", target="a")) is True
    assert s.evaluate(Condition(kind="flag", target="b")) is False
    assert s.evaluate(Condition(kind="not_flag", target="b")) is True
    s.events.set_flag("c", 7)
    assert s.evaluate(Condition(kind="flag_eq", target="c", value=7)) is True
    assert s.evaluate(Condition(kind="flag_eq", target="c", value=8)) is False


def test_apply_affection_and_unlock_threshold():
    from world_gal_game.core.affection import AffectionThreshold
    s = GameState()
    s.affection.register("alice", thresholds=[
        AffectionThreshold(name="friend", value=25, unlocks=["friend_mode"]),
    ])
    out = s.apply(Effect(kind="affection", target="alice", value=30))
    assert out["new"] == 30
    assert out["unlocked"] == ["friend_mode"]


def test_apply_move_to_updates_state_and_records_event():
    s = _state_with_two_locs()
    out = s.apply(Effect(kind="move_to", target="park"))
    assert out["to"] == "park"
    assert s.map.current.id == "park"
    assert any(e.kind == "location" for e in s.events.entries)


def test_apply_log_event_appears_in_log():
    s = GameState()
    s.apply(Effect(kind="log_event", target="title", value="summary"))
    last = s.events.recent(1)[0]
    assert last.title == "title"
    assert last.summary == "summary"


def test_apply_advance_time():
    s = GameState()
    assert s.time.time_of_day.value == "morning"
    s.apply(Effect(kind="advance_time", value=1))
    assert s.time.time_of_day.value == "noon"
