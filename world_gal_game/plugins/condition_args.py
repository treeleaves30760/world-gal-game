"""Typed argument models for the builtin conditions.

Mirror of :mod:`effect_args` for the condition registry. Each model validates
the ``(target, value, stat)`` triple of a
:class:`~world_gal_game.core.story_graph.Condition` for one ``kind``, and is
used only for JSON-Schema export and build/lint-time validation — never on the
tolerant ``GameState.evaluate`` path (unknown/ill-formed conditions evaluate to
``False`` with a logged warning, they do not raise).
"""
from __future__ import annotations

from typing import Any

from .effect_args import ArgModel, ReqStr


# ----------------------------------------------------------------------
# Flag-based

class FlagArgs(ArgModel):
    """flag: flag is truthy."""

    target: ReqStr                 # flag_name


class NotFlagArgs(ArgModel):
    """not_flag: flag is falsy (or unset)."""

    target: ReqStr                 # flag_name


class FlagEqArgs(ArgModel):
    """flag_eq: flag equals value."""

    target: ReqStr                 # flag_name
    value: Any = None              # compared with == (any type)


# ----------------------------------------------------------------------
# Affection

class AffectionGteArgs(ArgModel):
    """affection_gte: character's affection (on stat) is >= value."""

    target: ReqStr                 # character_id
    value: int                     # threshold (handler does int(cond.value))
    stat: str | None = None        # axis, default 'affection'


class AffectionLtArgs(ArgModel):
    """affection_lt: character's affection (on stat) is < value."""

    target: ReqStr                 # character_id
    value: int                     # threshold
    stat: str | None = None        # axis, default 'affection'


# ----------------------------------------------------------------------
# Time / location / scene

class TimeInArgs(ArgModel):
    """time_in: current time-of-day is one of the values."""

    # Handler wraps a non-list value in a list, so a single string is valid too.
    value: list[str] | str


class VisitedArgs(ArgModel):
    """visited: location has been visited at least once."""

    target: ReqStr                 # location_id


class ScenePlayedArgs(ArgModel):
    """scene_played: scene has been played at least once."""

    target: ReqStr                 # scene_id


# ----------------------------------------------------------------------
# Inventory / achievements

class HasItemArgs(ArgModel):
    """has_item: player has the item (>= value, default 1)."""

    target: ReqStr                 # item_id
    value: int | None = None       # count, default 1


class AchievementArgs(ArgModel):
    """achievement: achievement has been unlocked."""

    target: ReqStr                 # achievement_id


# ----------------------------------------------------------------------
# Resources

class ResourceGteArgs(ArgModel):
    """resource_gte: resource value >= value."""

    target: ReqStr                 # resource_id
    value: int | None = None       # threshold


class ResourceLtArgs(ArgModel):
    """resource_lt: resource value < value."""

    target: ReqStr                 # resource_id
    value: int | None = None       # threshold


class ResourceEqArgs(ArgModel):
    """resource_eq: resource value == value."""

    target: ReqStr                 # resource_id
    value: int | None = None       # threshold


# ----------------------------------------------------------------------
# Quests

class QuestActiveArgs(ArgModel):
    """quest_active: quest is currently active."""

    target: ReqStr                 # quest_id


class QuestCompletedArgs(ArgModel):
    """quest_completed: quest has been completed."""

    target: ReqStr                 # quest_id


class ObjectiveCompletedArgs(ArgModel):
    """objective_completed: a specific objective on a quest is done."""

    target: ReqStr                 # quest_id
    stat: str | None = None        # objective_id


__all__ = [
    "FlagArgs", "NotFlagArgs", "FlagEqArgs",
    "AffectionGteArgs", "AffectionLtArgs",
    "TimeInArgs", "VisitedArgs", "ScenePlayedArgs",
    "HasItemArgs", "AchievementArgs",
    "ResourceGteArgs", "ResourceLtArgs", "ResourceEqArgs",
    "QuestActiveArgs", "QuestCompletedArgs", "ObjectiveCompletedArgs",
]
