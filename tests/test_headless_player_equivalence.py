"""Headless player actions must obey the same boundaries as the GUI.

``start_scene`` and ``apply`` intentionally remain developer operations.
The player-equivalent ``examine`` and ``gift`` operations are narrower:
they may only act on interactions visible at the current location and time.
"""

from __future__ import annotations

from typing import Any

import pytest

from world_gal_game.config import EngineConfig
from world_gal_game.core.game_state import GameState
from world_gal_game.core.inventory import Item
from world_gal_game.core.map_system import Exit, Location, NPCPresence, SceneHook
from world_gal_game.core.story_graph import Effect, Line, Scene
from world_gal_game.core.time_system import TimeOfDay
from world_gal_game.dialogue.dialogue_engine import DialogueEngine
from world_gal_game.headless import HeadlessSession
from world_gal_game.npc.llm_brain import EchoBrain
from world_gal_game.npc.npc_base import NPC, NPCRegistry


def _scene(scene_id: str, title: str | None = None) -> Scene:
    return Scene(
        id=scene_id,
        title=title or scene_id,
        lines=[Line(text=f"{scene_id} 第一行")],
    )


def _session() -> HeadlessSession:
    state = GameState()
    registry = NPCRegistry()
    for npc in (
        NPC(
            id="present_npc",
            name="在場角色",
            likes=["茉莉花茶"],
        ),
        NPC(id="later_npc", name="稍後才在場"),
        NPC(id="remote_npc", name="別處角色"),
    ):
        registry.add(npc)
    state.meta["__npc_registry__"] = registry

    state.items.add(
        Item(
            id="jasmine_tea",
            name="茉莉花茶",
            category="gift",
            gift_modifier={"present_npc": 12},
            consumed_on_gift=True,
        )
    )

    state.map.add_location(
        Location(
            id="current",
            name="目前位置",
            exits=[Exit(target="elsewhere")],
            npcs=[
                NPCPresence(
                    npc_id="present_npc",
                    times=[TimeOfDay.MORNING.value],
                ),
                NPCPresence(
                    npc_id="later_npc",
                    times=[TimeOfDay.NOON.value],
                ),
            ],
            scene_hooks=[
                SceneHook(
                    scene_id="examine_here",
                    trigger="examine",
                ),
                SceneHook(
                    scene_id="locked_examine",
                    trigger="examine",
                    requires_flags=["can_examine_locked"],
                ),
                # Invalid authored references must not turn a player action into
                # an uncaught DialogueEngine KeyError.
                SceneHook(
                    scene_id="dangling_examine",
                    trigger="examine",
                ),
                # This hook is deliberately after examine hooks. Ambient
                # re-checking must skip them and find this auto hook.
                SceneHook(
                    scene_id="auto_after_dialogue",
                    trigger="auto",
                    requires_flags=["dialogue_unlocked_auto"],
                ),
                SceneHook(
                    scene_id="enter_at_noon",
                    trigger="enter",
                    requires_time=[TimeOfDay.NOON.value],
                ),
            ],
        )
    )
    state.map.add_location(
        Location(
            id="elsewhere",
            name="其他位置",
            npcs=[NPCPresence(npc_id="remote_npc")],
            scene_hooks=[
                SceneHook(
                    scene_id="remote_examine",
                    trigger="examine",
                ),
            ],
        )
    )
    state.map.move_to("current")

    for scene_id, title in (
        ("examine_here", "查看眼前線索"),
        ("locked_examine", "尚未開放的線索"),
        ("auto_after_dialogue", "對話後事件"),
        ("enter_at_noon", "正午事件"),
        ("remote_examine", "別處線索"),
        ("arbitrary_scene", "任意開發場景"),
    ):
        state.story.add_scene(_scene(scene_id, title))
    state.story.add_scene(
        Scene(
            id="unlocking_dialogue",
            title="解鎖事件的對話",
            lines=[Line(text="對話最後一行")],
            on_end=[
                Effect(
                    kind="set_flag",
                    target="dialogue_unlocked_auto",
                    value=True,
                ),
            ],
        )
    )

    return HeadlessSession(
        config=EngineConfig(seed=1937),
        state=state,
        npcs=registry,
        brain=EchoBrain(),
        dialogue=DialogueEngine(state),
        meta={},
        pack="headless-player-fixture",
    )


def _persistent_state(session: HeadlessSession) -> dict[str, Any]:
    return session.state.model_dump(mode="json")


def _assert_rejected_without_state_change(
    session: HeadlessSession,
    result: dict[str, Any],
    before: dict[str, Any],
) -> None:
    assert result["ok"] is False
    assert result.get("error")
    assert _persistent_state(session) == before


def test_examine_starts_an_available_examine_hook_at_current_location() -> None:
    session = _session()

    result = session.examine("examine_here")

    assert result["ok"] is True
    assert result["presentation"]["scene_id"] == "examine_here"
    assert result["presentation"]["kind"] == "line"
    assert session.state.story.current_scene == "examine_here"


@pytest.mark.parametrize(
    ("scene_id", "prepare"),
    [
        ("locked_examine", lambda _session: None),
        (
            "enter_at_noon",
            lambda session: session.state.time.set_phase(TimeOfDay.NOON),
        ),
        ("remote_examine", lambda _session: None),
        ("arbitrary_scene", lambda _session: None),
        ("dangling_examine", lambda _session: None),
        ("missing_scene", lambda _session: None),
    ],
)
def test_examine_rejects_unavailable_wrong_trigger_remote_or_arbitrary_scene(
    scene_id: str,
    prepare,
) -> None:
    session = _session()
    prepare(session)
    before = _persistent_state(session)

    result = session.examine(scene_id)

    _assert_rejected_without_state_change(session, result, before)
    assert session.state.story.current_scene is None


def test_examine_rechecks_hook_conditions_and_once_semantics() -> None:
    session = _session()
    before = _persistent_state(session)

    blocked = session.examine("locked_examine")

    _assert_rejected_without_state_change(session, blocked, before)

    session.state.events.set_flag("can_examine_locked", True)
    assert session.examine("locked_examine")["ok"] is True

    # Complete the one-shot scene, then verify that an arbitrary replay cannot
    # be smuggled through the player-equivalent examine operation.
    session.next_line()
    before_replay = _persistent_state(session)
    replay = session.examine("locked_examine")

    _assert_rejected_without_state_change(
        session,
        replay,
        before_replay,
    )


def test_gift_uses_builtin_effect_for_present_npc_and_owned_item() -> None:
    session = _session()
    session.state.inventory.add("jasmine_tea", 1)

    result = session.gift("present_npc", "jasmine_tea")

    assert result["ok"] is True
    assert result["result"]["kind"] == "gift"
    assert result["result"]["item"] == "jasmine_tea"
    assert result["result"]["target"] == "present_npc"
    assert result["result"]["delta"] == 12
    assert result["result"]["new"] == 12
    assert session.state.inventory.count("jasmine_tea") == 0
    assert session.state.affection.get("present_npc") == 12
    event = session.state.events.recent(1)[0]
    assert event.title == "送禮 · 茉莉花茶 → present_npc"
    assert event.data["delta"] == 12


@pytest.mark.parametrize(
    "npc_id",
    ["later_npc", "remote_npc", "missing_npc"],
)
def test_gift_rejects_npc_not_actually_present_now(npc_id: str) -> None:
    session = _session()
    session.state.inventory.add("jasmine_tea", 1)
    before = _persistent_state(session)

    result = session.gift(npc_id, "jasmine_tea")

    _assert_rejected_without_state_change(session, result, before)
    assert session.state.inventory.count("jasmine_tea") == 1
    assert session.state.affection.get(npc_id) == 0


def test_gift_rejects_item_the_player_does_not_hold() -> None:
    session = _session()
    before = _persistent_state(session)

    result = session.gift("present_npc", "jasmine_tea")

    _assert_rejected_without_state_change(session, result, before)
    assert session.state.affection.get("present_npc") == 0


def test_gift_rejects_locked_item_and_omits_it_from_affordances() -> None:
    session = _session()
    session.state.items.add(
        Item(
            id="locked_keepsake",
            name="不可失去的紀念品",
            category="quest",
            locked=True,
            consumed_on_gift=True,
        )
    )
    session.state.inventory.add("locked_keepsake", 1)

    assert all(
        action["item"] != "locked_keepsake" for action in session.affordances()["gift"]
    )
    before = _persistent_state(session)

    result = session.gift("present_npc", "locked_keepsake")

    _assert_rejected_without_state_change(session, result, before)
    assert session.state.inventory.count("locked_keepsake") == 1
    assert session.state.affection.get("present_npc") == 0


def test_builtin_gift_effect_itself_rejects_locked_item() -> None:
    session = _session()
    session.state.items.add(
        Item(
            id="locked_keepsake",
            name="不可失去的紀念品",
            category="quest",
            locked=True,
            consumed_on_gift=True,
        )
    )
    session.state.inventory.add("locked_keepsake", 1)
    before = _persistent_state(session)

    result = session.state.apply(
        Effect(
            kind="gift",
            target="present_npc",
            stat="locked_keepsake",
        )
    )

    assert result["kind"] == "gift"
    assert result["error"] == "item is locked"
    assert _persistent_state(session) == before


def test_builtin_sell_effect_itself_rejects_locked_item() -> None:
    session = _session()
    session.state.items.add(
        Item(
            id="locked_keepsake",
            name="不可失去的紀念品",
            category="quest",
            value=500,
            locked=True,
        )
    )
    session.state.inventory.add("locked_keepsake", 1)
    before = _persistent_state(session)

    result = session.state.apply(
        Effect(
            kind="sell_item",
            target="locked_keepsake",
            stat="money",
        )
    )

    assert result["kind"] == "sell_item"
    assert result["error"] == "item is locked"
    assert _persistent_state(session) == before


@pytest.mark.parametrize("count", [-1, 0, 2])
def test_gift_rejects_counts_other_than_one(count: int) -> None:
    session = _session()
    session.state.inventory.add("jasmine_tea", 2)
    before = _persistent_state(session)

    result = session.gift("present_npc", "jasmine_tea", count=count)

    _assert_rejected_without_state_change(session, result, before)


def test_affordances_list_only_structured_player_examine_and_gift_actions() -> None:
    session = _session()
    session.state.inventory.add("jasmine_tea", 1)

    affordances = session.affordances()

    assert affordances["examine"] == [
        {
            "op": "examine",
            "scene": "examine_here",
            "title": "查看眼前線索",
        },
    ]
    assert len(affordances["gift"]) == 1
    gift = affordances["gift"][0]
    assert gift["op"] == "gift"
    assert gift["npc"] == "present_npc"
    assert gift["item"] == "jasmine_tea"
    assert gift["count"] == 1
    assert gift["npc_name"] == "在場角色"
    assert gift["item_name"] == "茉莉花茶"

    assert {row["scene"] for row in affordances["examine"]}.isdisjoint(
        {
            "locked_examine",
            "dangling_examine",
            "auto_after_dialogue",
            "enter_at_noon",
            "remote_examine",
            "arbitrary_scene",
        }
    )
    assert {row["npc"] for row in affordances["gift"]} == {"present_npc"}


def test_dialogue_hides_and_rejects_exploration_examine_and_gift_actions() -> None:
    session = _session()
    session.state.inventory.add("jasmine_tea", 1)
    session.start_scene("arbitrary_scene")

    affordances = session.affordances()
    assert affordances["examine"] == []
    assert affordances["gift"] == []

    before = _persistent_state(session)
    _assert_rejected_without_state_change(
        session,
        session.examine("examine_here"),
        before,
    )
    _assert_rejected_without_state_change(
        session,
        session.gift("present_npc", "jasmine_tea"),
        before,
    )


def test_dialogue_hides_and_rejects_player_movement_and_map_travel() -> None:
    session = _session()
    session.start_scene("arbitrary_scene")

    affordances = session.affordances()
    assert affordances["map"]["available"] is False
    assert affordances["map"]["reason"]
    assert affordances["travel_options"] == []
    assert affordances["guided_destinations"] == []
    assert affordances["exits"] == [
        {
            "target": "elsewhere",
            "label": None,
            "available": False,
            "blocked_reason": "finish the current dialogue before moving",
        },
    ]

    before = _persistent_state(session)
    result = session.move_to("elsewhere", allow_distant=True)

    _assert_rejected_without_state_change(session, result, before)
    assert session.state.map.current_location_id == "current"


def test_dialogue_end_rechecks_current_location_enter_and_auto_hooks() -> None:
    session = _session()
    first = session.start_scene("unlocking_dialogue")
    assert first["presentation"]["scene_id"] == "unlocking_dialogue"

    result = session.next_line()

    assert session.state.events.flags["dialogue_unlocked_auto"] is True
    assert "unlocking_dialogue" in session.state.story.played
    assert result["presentation"]["scene_id"] == "auto_after_dialogue"
    assert result["presentation"]["kind"] == "line"
    assert session.state.story.current_scene == "auto_after_dialogue"


def test_advance_time_rechecks_current_location_enter_and_auto_hooks() -> None:
    session = _session()
    assert session.state.time.time_of_day is TimeOfDay.MORNING

    result = session.advance_time()

    assert result["ok"] is True
    assert result["phases_advanced"] == 1
    assert session.state.time.time_of_day is TimeOfDay.NOON
    assert result["presentation"]["scene_id"] == "enter_at_noon"
    assert result["presentation"]["kind"] == "line"
    assert session.state.story.current_scene == "enter_at_noon"


def test_advance_time_stops_at_first_intermediate_ambient_hook() -> None:
    session = _session()
    assert session.state.time.time_of_day is TimeOfDay.MORNING

    result = session.advance_time(phases=2)

    # GUI waiting advances one phase and immediately opens the noon event.
    # A batched headless wait must not skip that player-visible boundary and
    # land silently in the afternoon.
    assert result["ok"] is True
    assert result["phases_advanced"] == 1
    assert session.state.time.time_of_day is TimeOfDay.NOON
    assert result["presentation"]["scene_id"] == "enter_at_noon"
    assert session.state.story.current_scene == "enter_at_noon"


def test_dialogue_hides_and_rejects_advance_time() -> None:
    session = _session()
    session.start_scene("arbitrary_scene")
    before = _persistent_state(session)

    affordance = session.affordances()["advance_time"]
    assert affordance["available"] is False
    assert affordance["reason"]

    result = session.advance_time(phases=2)

    _assert_rejected_without_state_change(session, result, before)
    assert result["phases_advanced"] == 0
    assert session.state.time.time_of_day is TimeOfDay.MORNING


def test_run_script_dispatches_player_examine_and_gift_ops() -> None:
    examine_session = _session()
    [examined] = examine_session.run_script(
        [
            {"op": "examine", "scene": "examine_here"},
        ]
    )

    assert examined["ok"] is True
    assert examined["op"] == "examine"
    assert examined["presentation"]["scene_id"] == "examine_here"

    gift_session = _session()
    gift_session.state.inventory.add("jasmine_tea", 1)
    [gifted] = gift_session.run_script(
        [
            {
                "op": "gift",
                "npc": "present_npc",
                "item": "jasmine_tea",
            },
        ]
    )

    assert gifted["ok"] is True
    assert gifted["op"] == "gift"
    assert gifted["result"]["kind"] == "gift"
    assert gifted["result"]["delta"] == 12
    assert gifted["changed"] is True
