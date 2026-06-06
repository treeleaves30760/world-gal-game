"""Branching story / scene graph.

Scenes are nodes containing a sequence of dialogue lines and optional
choices. Choices have conditions and effects; effects update flags,
affection, and trigger transitions to other scenes.

The graph is intentionally data-driven: scenes are loaded from YAML so
content authors can write scenes without touching Python.
"""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .portrait_spec import PortraitSpec, Slot


class Effect(BaseModel):
    """A single state change produced by a line or choice.

    ``kind`` is open-ended ``str`` rather than a ``Literal[...]`` enum:
    plugins extend the set of valid kinds at load time by registering
    handlers into :data:`world_gal_game.plugins.EFFECT_REGISTRY`. The
    model accepts any non-empty string; the engine reports unknown kinds
    as an ``error`` dict from :meth:`GameState.apply` rather than failing
    pydantic validation, so YAML-driven content can ship side-by-side
    with plugin-provided kinds.

    The engine's :mod:`world_gal_game.validator` cross-checks pack YAML
    against the registry at load time and surfaces "did you mean"
    suggestions for typos.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str
    target: str = ""
    value: Any = None
    stat: str | None = None

    @field_validator("kind")
    @classmethod
    def _kind_nonempty(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Effect.kind must be a non-empty string")
        return v


class Condition(BaseModel):
    """A predicate over the current game state.

    Like :class:`Effect`, ``kind`` is an open-ended ``str``; the set of
    valid kinds is whatever :data:`world_gal_game.plugins.CONDITION_REGISTRY`
    has registered at the time :meth:`GameState.evaluate` runs. Unknown
    kinds evaluate to ``False`` (i.e. "predicate unsatisfied") with a
    warning logged, rather than raising.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str
    target: str = ""
    value: Any = None
    stat: str | None = None

    @field_validator("kind")
    @classmethod
    def _kind_nonempty(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Condition.kind must be a non-empty string")
        return v


class Choice(BaseModel):
    """A branch the player can pick at a decision point."""

    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    requires: list[Condition] = Field(default_factory=list)
    forbids: list[Condition] = Field(default_factory=list)
    effects: list[Effect] = Field(default_factory=list)
    next_scene: str | None = None
    hidden_if_locked: bool = False


class Line(BaseModel):
    """A single line of dialogue or narration."""

    model_config = ConfigDict(extra="forbid")

    speaker: str | None = None       # None = narration
    text: str
    portrait: str | PortraitSpec | None = None   # old: path; new: spec dict
    portraits: list[PortraitSpec] = Field(default_factory=list)  # multi-slot; overrides portrait when non-empty
    # Author-friendly slot for the SIMPLE portrait forms (a bare ``expression:``
    # or a string ``portrait:`` file path), which otherwise always centre. A
    # spec ``portrait:`` carries its own ``slot`` and ignores this. None / unset
    # = centre, so every legacy line is byte-identical.
    portrait_pos: Slot | None = None
    expression: str | None = None    # e.g. "smile", "scared"
    cg: str | None = None            # full-screen CG to display
    sfx: str | None = None
    bgm: str | None = None
    ambient: str | None = None       # switch the looping environment bed
    voice: str | None = None         # per-line voice clip (played on a reserved channel)
    effects: list[Effect] = Field(default_factory=list)
    requires: list[Condition] = Field(default_factory=list)
    llm_speaker: bool = False        # if true, generate text via LLM
    llm_directive: str | None = None # extra prompt directive for the LLM


class Scene(BaseModel):
    """A scene = a list of lines + an optional set of choices at the end."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str = ""
    location: str | None = None
    background: str | None = None
    bgm: str | None = None
    ambient: str | None = None       # looping environment bed (room tone / rain)
    cg: str | None = None            # scene-wide CG; individual line.cg overrides
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
