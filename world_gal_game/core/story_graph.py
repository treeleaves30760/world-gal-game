"""Branching story / scene graph.

Scenes are nodes containing a sequence of dialogue lines and optional
choices. Choices have conditions and effects; effects update flags,
affection, and trigger transitions to other scenes.

The graph is intentionally data-driven: scenes are loaded from YAML so
content authors can write scenes without touching Python.
"""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


class Effect(BaseModel):
    """A single state change produced by a line or choice."""

    kind: Literal[
        "affection",         # change a character's affection
        "stat",              # change a character's arbitrary stat
        "set_flag",          # set a flag
        "increment_flag",    # add to a numeric flag
        "advance_time",      # advance time-of-day
        "move_to",           # move player to a location
        "unlock_location",   # set the requires_flags satisfaction
        "play_scene",        # transition to another scene
        "end_scene",         # end the current scene
        "log_event",         # add an arbitrary event log entry
        "give_item",         # add an item to the player's inventory
        "take_item",         # remove an item from the player's inventory
        "gift",              # give an item to an NPC (consumes it)
        "use_item",          # consume an item; apply its use_effects
        "gain_resource",     # +N to a resource (e.g. money, energy)
        "spend_resource",    # -N from a resource (fails if not enough)
        "set_resource",      # set a resource to an absolute value
        "buy_item",          # spend currency, gain item (from a shop)
        "sell_item",         # consume item, gain currency
    ]
    target: str = ""
    value: Any = None
    stat: str | None = None


class Condition(BaseModel):
    """A predicate over the current game state."""

    kind: Literal[
        "flag",            # flag must be truthy
        "not_flag",        # flag must be falsy
        "flag_eq",         # flag must equal value
        "affection_gte",   # affection >= value for target
        "affection_lt",
        "time_in",         # time-of-day is in value (list)
        "visited",         # location has been visited
        "scene_played",    # scene has been played
        "has_item",        # player has item; value = min count (default 1)
        "achievement",     # achievement has been unlocked
        "resource_gte",    # resource value >= value
        "resource_lt",
        "resource_eq",
    ]
    target: str = ""
    value: Any = None
    stat: str | None = None


class Choice(BaseModel):
    """A branch the player can pick at a decision point."""

    id: str
    text: str
    requires: list[Condition] = Field(default_factory=list)
    forbids: list[Condition] = Field(default_factory=list)
    effects: list[Effect] = Field(default_factory=list)
    next_scene: str | None = None
    hidden_if_locked: bool = False


class Line(BaseModel):
    """A single line of dialogue or narration."""

    speaker: str | None = None       # None = narration
    text: str
    portrait: str | None = None      # path to character portrait
    expression: str | None = None    # e.g. "smile", "scared"
    cg: str | None = None            # full-screen CG to display
    sfx: str | None = None
    bgm: str | None = None
    effects: list[Effect] = Field(default_factory=list)
    requires: list[Condition] = Field(default_factory=list)
    llm_speaker: bool = False        # if true, generate text via LLM
    llm_directive: str | None = None # extra prompt directive for the LLM


class Scene(BaseModel):
    """A scene = a list of lines + an optional set of choices at the end."""

    id: str
    title: str = ""
    location: str | None = None
    background: str | None = None
    bgm: str | None = None
    lines: list[Line] = Field(default_factory=list)
    choices: list[Choice] = Field(default_factory=list)
    on_end: list[Effect] = Field(default_factory=list)
    requires: list[Condition] = Field(default_factory=list)
    once: bool = True
    tags: list[str] = Field(default_factory=list)
    route: str | None = None         # which heroine route this belongs to


class StoryNode(BaseModel):
    """Wrapper recording a scene and metadata for traversal."""

    scene: Scene
    played: bool = False


class StoryGraph(BaseModel):
    """The collection of all scenes and tracking of which have played."""

    scenes: dict[str, Scene] = Field(default_factory=dict)
    played: set[str] = Field(default_factory=set)
    current_scene: str | None = None
    current_line_index: int = 0

    model_config = {"arbitrary_types_allowed": True}

    def add_scene(self, scene: Scene) -> None:
        self.scenes[scene.id] = scene

    def get(self, scene_id: str) -> Scene | None:
        return self.scenes.get(scene_id)

    def start(self, scene_id: str) -> Scene:
        if scene_id not in self.scenes:
            raise KeyError(f"Unknown scene: {scene_id}")
        self.current_scene = scene_id
        self.current_line_index = 0
        return self.scenes[scene_id]

    def mark_played(self, scene_id: str) -> None:
        self.played.add(scene_id)

    def is_played(self, scene_id: str) -> bool:
        return scene_id in self.played
