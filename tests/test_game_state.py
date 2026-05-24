"""GameState: condition evaluation + effect application."""
import pytest

from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import Condition, Effect
from world_gal_game.core.map_system import Location, SceneHook


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


def test_set_flag_if_unset_keeps_first_truthy_value():
    s = GameState()
    out1 = s.apply(Effect(kind="set_flag_if_unset", target="first_met", value="qingyi"))
    out2 = s.apply(Effect(kind="set_flag_if_unset", target="first_met", value="yuening"))
    assert out1["set"] is True
    assert out2["set"] is False
    assert s.events.get_flag("first_met") == "qingyi"


def test_scene_hooks_support_full_conditions_when_state_is_supplied():
    s = GameState()
    s.map.add_location(Location(
        id="cafeteria",
        name="學餐",
        scene_hooks=[
            SceneHook(
                scene_id="low_affection",
                requires=[Condition(kind="affection_lt", target="alice", value=10)],
            ),
            SceneHook(
                scene_id="friend_lunch",
                requires=[Condition(kind="affection_gte", target="alice", value=20)],
            ),
        ],
    ))
    s.map.move_to("cafeteria")
    s.affection.register("alice")
    s.affection.adjust("alice", 25)

    hooks = s.map.available_scenes(
        time_of_day=s.time.time_of_day.value,
        flags=s.events.flags,
        played_scenes=s.story.played,
        state=s,
    )
    assert [h.scene_id for h in hooks] == ["friend_lunch"]


def test_scene_hooks_with_full_conditions_are_locked_without_state():
    s = GameState()
    s.map.add_location(Location(
        id="cafeteria",
        name="學餐",
        scene_hooks=[
            SceneHook(
                scene_id="friend_lunch",
                requires=[Condition(kind="affection_gte", target="alice", value=20)],
            ),
        ],
    ))
    s.map.move_to("cafeteria")
    s.affection.register("alice")
    s.affection.adjust("alice", 25)

    hooks = s.map.available_scenes(
        time_of_day=s.time.time_of_day.value,
        flags=s.events.flags,
        played_scenes=s.story.played,
    )
    assert hooks == []


# ----------------------------------------------------------------------
# Presentation effects (camera / screen FX).
#
# These handlers must run inside the pure-Python apply() path: they return a
# dict AND record a directive onto the private state.meta["__visual_fx__"]
# queue, and must never import or touch pygame (DialogueScene drains the queue
# and drives the actual animation). The "__" prefix keeps the queue out of
# saves (see _serialize_meta).

VISUAL_FX_QUEUE = "__visual_fx__"


def test_apply_camera_pan_returns_dict_and_queues():
    s = GameState()
    out = s.apply(Effect(kind="camera_pan",
                         value={"x": 80, "y": -30, "duration": 0.5}))
    assert out["kind"] == "camera_pan"
    assert out["x"] == 80 and out["y"] == -30
    queue = s.meta[VISUAL_FX_QUEUE]
    assert queue[-1]["fx"] == "camera_pan"
    assert queue[-1]["x"] == 80 and queue[-1]["duration"] == 0.5


def test_apply_camera_zoom_returns_dict_and_queues():
    s = GameState()
    out = s.apply(Effect(kind="camera_zoom", value={"scale": 1.5}))
    assert out["kind"] == "camera_zoom"
    assert out["scale"] == 1.5
    assert s.meta[VISUAL_FX_QUEUE][-1] == {
        "fx": "camera_zoom", "scale": 1.5, "duration": 0.6, "easing": None}


def test_apply_screen_shake_returns_dict_and_queues():
    s = GameState()
    out = s.apply(Effect(kind="screen_shake",
                         value={"intensity": 14, "duration": 0.3}))
    assert out["kind"] == "screen_shake"
    assert out["intensity"] == 14
    assert s.meta[VISUAL_FX_QUEUE][-1]["fx"] == "screen_shake"


def test_apply_screen_flash_returns_dict_and_queues():
    s = GameState()
    out = s.apply(Effect(kind="screen_flash",
                         value={"color": [255, 240, 200], "duration": 0.25}))
    assert out["kind"] == "screen_flash"
    d = s.meta[VISUAL_FX_QUEUE][-1]
    assert d["fx"] == "screen_flash" and d["color"] == [255, 240, 200]


def test_apply_screen_tint_persistent_and_clear():
    s = GameState()
    s.apply(Effect(kind="screen_tint",
                   value={"color": [80, 0, 0], "duration": 0.5}))
    s.apply(Effect(kind="screen_tint", value={"clear": True}))
    q = s.meta[VISUAL_FX_QUEUE]
    assert q[0]["fx"] == "screen_tint" and q[0]["color"] == [80, 0, 0]
    assert q[1] == {"fx": "screen_tint", "clear": True}


def test_screen_tint_duration_zero_marks_persist_path():
    s = GameState()
    out = s.apply(Effect(kind="screen_tint",
                         value={"color": [0, 0, 0], "duration": 0,
                                "persist": True}))
    assert out["persist"] is True
    assert s.meta[VISUAL_FX_QUEUE][-1]["duration"] == 0


def test_visual_fx_effects_accumulate_in_one_queue():
    s = GameState()
    s.apply_all([
        Effect(kind="camera_zoom", value={"scale": 1.2}),
        Effect(kind="screen_shake", value={"intensity": 8}),
        Effect(kind="screen_flash", value={}),
    ])
    kinds = [d["fx"] for d in s.meta[VISUAL_FX_QUEUE]]
    assert kinds == ["camera_zoom", "screen_shake", "screen_flash"]


def test_visual_fx_queue_stripped_from_save_dump():
    s = GameState()
    s.apply(Effect(kind="screen_shake", value={"intensity": 10}))
    assert VISUAL_FX_QUEUE in s.meta                 # present live...
    dumped = s.model_dump(mode="json")
    assert VISUAL_FX_QUEUE not in dumped.get("meta", {})  # ...stripped on save


def test_visual_fx_handlers_do_not_import_pygame():
    # The whole point of the queue is that apply() stays pygame-free. Drop
    # pygame from sys.modules, apply each kind, and assert it was not imported.
    import sys
    had = {k: v for k, v in sys.modules.items()
           if k == "pygame" or k.startswith("pygame.")}
    for k in list(had):
        del sys.modules[k]
    try:
        s = GameState()
        for kind, val in [
            ("camera_pan", {"x": 1, "y": 2}),
            ("camera_zoom", {"scale": 2.0}),
            ("screen_shake", {"intensity": 5}),
            ("screen_flash", {}),
            ("screen_tint", {"color": [1, 2, 3]}),
        ]:
            s.apply(Effect(kind=kind, value=val))
        assert "pygame" not in sys.modules
    finally:
        sys.modules.update(had)


def test_visual_fx_effects_tolerate_missing_value():
    # eff.value defaulting to None must not raise (handlers coerce to {}).
    s = GameState()
    for kind in ("camera_pan", "camera_zoom", "screen_shake",
                 "screen_flash", "screen_tint"):
        out = s.apply(Effect(kind=kind))
        assert out["kind"] == kind
    assert len(s.meta[VISUAL_FX_QUEUE]) == 5
