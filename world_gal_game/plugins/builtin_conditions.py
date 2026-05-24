"""Builtin condition handlers.

Each engine-shipped condition kind (16 of them) is
registered here via ``@condition("kind", plugin_id="builtin")``.
:meth:`GameState.evaluate` dispatches through :data:`CONDITION_REGISTRY`
so third-party plugins use the same registration path the engine does.

Return-bool semantics mirror the original ``GameState.evaluate`` exactly
so the 20+ existing tests run unchanged.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .registry import condition
from .condition_args import (
    FlagArgs, NotFlagArgs, FlagEqArgs, AffectionGteArgs, AffectionLtArgs,
    TimeInArgs, VisitedArgs, ScenePlayedArgs, HasItemArgs, AchievementArgs,
    ResourceGteArgs, ResourceLtArgs, ResourceEqArgs, QuestActiveArgs,
    QuestCompletedArgs, ObjectiveCompletedArgs,
)

if TYPE_CHECKING:
    from ..core.game_state import GameState
    from ..core.story_graph import Condition


BUILTIN = "builtin"


# ----------------------------------------------------------------------
# Flag-based

@condition("flag", plugin_id=BUILTIN, args=FlagArgs,
           description="Flag is truthy.",
           signature={"target": "flag_name"})
def cond_flag(state: "GameState", cond: "Condition") -> bool:
    return bool(state.events.get_flag(cond.target))


@condition("not_flag", plugin_id=BUILTIN, args=NotFlagArgs,
           description="Flag is falsy (or unset).",
           signature={"target": "flag_name"})
def cond_not_flag(state: "GameState", cond: "Condition") -> bool:
    return not bool(state.events.get_flag(cond.target))


@condition("flag_eq", plugin_id=BUILTIN, args=FlagEqArgs,
           description="Flag equals value.",
           signature={"target": "flag_name", "value": "any"})
def cond_flag_eq(state: "GameState", cond: "Condition") -> bool:
    return state.events.get_flag(cond.target) == cond.value


# ----------------------------------------------------------------------
# Affection

@condition("affection_gte", plugin_id=BUILTIN, args=AffectionGteArgs,
           description="Character's affection (on stat) is >= value.",
           signature={"target": "character_id", "value": "int",
                   "stat": "str? (axis, default 'affection')"})
def cond_affection_gte(state: "GameState", cond: "Condition") -> bool:
    stat = cond.stat or "affection"
    return state.affection.get(cond.target, stat) >= int(cond.value)


@condition("affection_lt", plugin_id=BUILTIN, args=AffectionLtArgs,
           description="Character's affection (on stat) is < value.",
           signature={"target": "character_id", "value": "int",
                   "stat": "str? (axis, default 'affection')"})
def cond_affection_lt(state: "GameState", cond: "Condition") -> bool:
    stat = cond.stat or "affection"
    return state.affection.get(cond.target, stat) < int(cond.value)


# ----------------------------------------------------------------------
# Time / location / scene

@condition("time_in", plugin_id=BUILTIN, args=TimeInArgs,
           description="Current time-of-day is one of the values in the list.",
           signature={"value": "list[str] (e.g. ['morning', 'noon'])"})
def cond_time_in(state: "GameState", cond: "Condition") -> bool:
    vals = cond.value if isinstance(cond.value, list) else [cond.value]
    return state.time.time_of_day.value in vals


@condition("visited", plugin_id=BUILTIN, args=VisitedArgs,
           description="Location has been visited at least once.",
           signature={"target": "location_id"})
def cond_visited(state: "GameState", cond: "Condition") -> bool:
    return cond.target in state.map.visited


@condition("scene_played", plugin_id=BUILTIN, args=ScenePlayedArgs,
           description="Scene has been played at least once.",
           signature={"target": "scene_id"})
def cond_scene_played(state: "GameState", cond: "Condition") -> bool:
    return state.story.is_played(cond.target)


# ----------------------------------------------------------------------
# Inventory / achievements

@condition("has_item", plugin_id=BUILTIN, args=HasItemArgs,
           description="Player has the item (>= value, default 1).",
           signature={"target": "item_id", "value": "int (count, default 1)"})
def cond_has_item(state: "GameState", cond: "Condition") -> bool:
    need = int(cond.value) if cond.value is not None else 1
    return state.inventory.has(cond.target, need)


@condition("achievement", plugin_id=BUILTIN, args=AchievementArgs,
           description="Achievement has been unlocked.",
           signature={"target": "achievement_id"})
def cond_achievement(state: "GameState", cond: "Condition") -> bool:
    return state.achievements.is_unlocked(cond.target)


# ----------------------------------------------------------------------
# Resources

@condition("resource_gte", plugin_id=BUILTIN, args=ResourceGteArgs,
           description="Resource value >= value.",
           signature={"target": "resource_id", "value": "int"})
def cond_resource_gte(state: "GameState", cond: "Condition") -> bool:
    return state.resources.get(cond.target) >= int(cond.value or 0)


@condition("resource_lt", plugin_id=BUILTIN, args=ResourceLtArgs,
           description="Resource value < value.",
           signature={"target": "resource_id", "value": "int"})
def cond_resource_lt(state: "GameState", cond: "Condition") -> bool:
    return state.resources.get(cond.target) < int(cond.value or 0)


@condition("resource_eq", plugin_id=BUILTIN, args=ResourceEqArgs,
           description="Resource value == value.",
           signature={"target": "resource_id", "value": "int"})
def cond_resource_eq(state: "GameState", cond: "Condition") -> bool:
    return state.resources.get(cond.target) == int(cond.value or 0)


# ----------------------------------------------------------------------
# Quests

@condition("quest_active", plugin_id=BUILTIN, args=QuestActiveArgs,
           description="Quest is currently active.",
           signature={"target": "quest_id"})
def cond_quest_active(state: "GameState", cond: "Condition") -> bool:
    return state.quests.is_active(cond.target)


@condition("quest_completed", plugin_id=BUILTIN, args=QuestCompletedArgs,
           description="Quest has been completed.",
           signature={"target": "quest_id"})
def cond_quest_completed(state: "GameState", cond: "Condition") -> bool:
    return state.quests.is_completed(cond.target)


@condition("objective_completed", plugin_id=BUILTIN, args=ObjectiveCompletedArgs,
           description="A specific objective on a quest is done.",
           signature={"target": "quest_id", "stat": "objective_id"})
def cond_objective_completed(state: "GameState", cond: "Condition") -> bool:
    return state.quests.objective_completed(cond.target, cond.stat or "")


__all__ = [
    "cond_flag", "cond_not_flag", "cond_flag_eq",
    "cond_affection_gte", "cond_affection_lt",
    "cond_time_in", "cond_visited", "cond_scene_played",
    "cond_has_item", "cond_achievement",
    "cond_resource_gte", "cond_resource_lt", "cond_resource_eq",
    "cond_quest_active", "cond_quest_completed", "cond_objective_completed",
]
