"""Branching story / scene graph.

Scenes are nodes containing a sequence of dialogue lines and optional
choices. Choices have conditions and effects; effects update flags,
affection, and trigger transitions to other scenes.

The graph is intentionally data-driven: scenes are loaded from YAML so
content authors can write scenes without touching Python.
"""
from __future__ import annotations

import math
import unicodedata
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from .event_log import DialogueHistory
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


class ChoiceCardDetail(BaseModel):
    """One comparable fact row inside an optional visual choice card."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1, max_length=16)
    text: str = Field(min_length=1, max_length=160)

    @field_validator("label", "text")
    @classmethod
    def _normalise_copy(cls, value: str) -> str:
        text = " ".join(value.split())
        if not text:
            raise ValueError("choice card copy must not be blank")
        return text


class ChoiceCardSpec(BaseModel):
    """Optional, presentation-only metadata for a comparable visual choice.

    ``Choice.text`` remains the authoritative functional label used by
    headless clients, accessibility tooling, and the legacy list renderer.
    This model only enriches that label when every visible option in a
    decision supplies a card.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1, max_length=64)
    subtitle: str = Field(default="", max_length=120)
    badge: str = Field(default="", max_length=48)
    image: str | None = Field(default=None, max_length=512)
    image_alt: str = Field(default="", max_length=160)
    image_fit: Literal["cover", "contain"] = "cover"
    image_focus_y: float = Field(default=0.5, ge=0.0, le=1.0)
    image_zoom: float = Field(default=1.0, ge=1.0, le=2.0)
    accent: tuple[StrictInt, StrictInt, StrictInt] | None = None
    details: list[ChoiceCardDetail] = Field(
        default_factory=list,
        max_length=3,
    )
    tags: list[str] = Field(default_factory=list, max_length=3)
    action: str = Field(default="", max_length=64)

    @field_validator("title")
    @classmethod
    def _normalise_title(cls, value: str) -> str:
        text = " ".join(value.split())
        if not text:
            raise ValueError("choice card title must not be blank")
        return text

    @field_validator("subtitle", "badge", "image_alt", "action")
    @classmethod
    def _normalise_copy(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("image")
    @classmethod
    def _normalise_image(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = value.strip()
        if not path:
            raise ValueError("choice card image must not be blank")
        return path

    @field_validator("image_focus_y", "image_zoom", mode="before")
    @classmethod
    def _strict_finite_number(cls, value: object) -> float:
        if type(value) not in (int, float):
            raise ValueError(
                "choice card crop values must be finite numbers"
            )
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(
                "choice card crop values must be finite numbers"
            )
        return number

    @field_validator("accent")
    @classmethod
    def _valid_accent(
        cls,
        value: tuple[int, int, int] | None,
    ) -> tuple[int, int, int] | None:
        if value is None:
            return None
        if (
            len(value) != 3
            or any(type(channel) is not int for channel in value)
            or any(channel < 0 or channel > 255 for channel in value)
        ):
            raise ValueError(
                "choice card accent must contain three integers from 0 to 255"
            )
        return value

    @field_validator("tags")
    @classmethod
    def _valid_tags(cls, values: list[str]) -> list[str]:
        tags = [" ".join(value.split()) for value in values]
        if any(not tag for tag in tags):
            raise ValueError("choice card tags must not be blank")
        if any(len(tag) > 32 for tag in tags):
            raise ValueError("choice card tags must be at most 32 characters")
        if len(set(tags)) != len(tags):
            raise ValueError("choice card tags must be unique")
        return tags

    @model_validator(mode="after")
    def _image_has_alternative(self) -> "ChoiceCardSpec":
        if self.image and not self.image_alt:
            raise ValueError(
                "choice card image_alt is required when image is provided"
            )
        return self


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
    card: ChoiceCardSpec | None = None


class SoundCaptionSpec(BaseModel):
    """Authored text alternative for one meaningful non-speech audio cue."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["ambient", "sfx", "music", "silence"]
    text: str = Field(min_length=1, max_length=240)

    @field_validator("text", mode="before")
    @classmethod
    def _normalise_text(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("sound caption text must be a string")
        if any(unicodedata.category(char).startswith("C") for char in value):
            raise ValueError("sound caption text must not contain control characters")
        text = " ".join(value.split())
        if not text:
            raise ValueError("sound caption text must not be blank")
        return text


class Line(BaseModel):
    """A single line of dialogue or narration."""

    model_config = ConfigDict(extra="forbid")

    speaker: str | None = None       # None = narration
    text: str
    # Optional stable localization identity.  Index-based keys remain the
    # backward-compatible fallback, but translated production scenes can pin a
    # line so inserting unrelated copy does not shift every downstream key.
    loc_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
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
    ambient: str | None = None       # switch bed; none/silence/- stops it
    voice: str | None = None         # per-line voice clip (played on a reserved channel)
    sound_captions: list[SoundCaptionSpec] = Field(
        default_factory=list,
        max_length=4,
    )
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
    ambient: str | None = None       # looping bed; none/silence/- means silence
    cg: str | None = None            # scene-wide CG; individual line.cg overrides
    lines: list[Line] = Field(default_factory=list)
    choices: list[Choice] = Field(default_factory=list)
    on_end: list[Effect] = Field(default_factory=list)
    requires: list[Condition] = Field(default_factory=list)
    once: bool = True
    tags: list[str] = Field(default_factory=list)
    route: str | None = None         # which heroine route this belongs to

    @model_validator(mode="after")
    def _line_loc_ids_are_unique(self) -> "Scene":
        seen: set[str] = set()
        for line in self.lines:
            loc_id = line.loc_id
            if loc_id is None:
                continue
            if loc_id in seen:
                raise ValueError(
                    f"duplicate line loc_id {loc_id!r} in scene {self.id!r}"
                )
            seen.add(loc_id)
        return self


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


def resolve_authored_line(
    scene: Scene,
    *,
    loc_id: str | None,
    line_index: int,
    label: str = "line",
) -> tuple[int, Line]:
    """Resolve saved/runtime line evidence against an authored scene.

    A present ``loc_id`` is authoritative and never falls back to the numeric
    index.  The index is accepted only for legacy/unidentified lines.  The
    duplicate check is deliberately repeated at this trust boundary because a
    plugin can mutate ``Scene.lines`` after pydantic construction.
    """
    if loc_id is not None:
        matches = [
            (index, line)
            for index, line in enumerate(scene.lines)
            if line.loc_id == loc_id
        ]
        if len(matches) != 1:
            raise ValueError(f"{label} loc_id is unknown or ambiguous")
        return matches[0]
    if type(line_index) is not int or not 0 <= line_index < len(scene.lines):
        raise ValueError(f"{label} line index is invalid")
    return line_index, scene.lines[line_index]


def authored_story_audio_refs(
    authored: StoryGraph,
    authored_map: object = None,
) -> tuple[frozenset[str], frozenset[str]]:
    """Return BGM/ambient paths executable by current authored content.

    Save-state ``meta`` and replay traces both carry effective audio inherited
    from an earlier line or scene. Those values are progress evidence, not an
    authority to introduce a new decoder path. Build the allowlists solely
    from the freshly loaded story catalogue, plus authored location ambience
    (a scene may inherit ``Location.ambient`` without naming it itself).

    ``authored_map`` is intentionally duck-typed here: importing
    :class:`MapSystem` would create a cycle because ``map_system`` imports this
    module's :class:`Condition` model.
    """
    music: set[str] = set()
    ambient: set[str] = set()

    def _add(target: set[str], value: object) -> None:
        if isinstance(value, str) and value:
            target.add(value)

    for scene in authored.scenes.values():
        _add(music, scene.bgm)
        _add(ambient, scene.ambient)
        for line in scene.lines:
            _add(music, line.bgm)
            _add(ambient, line.ambient)

    locations = getattr(authored_map, "locations", None)
    if isinstance(locations, dict):
        for location in locations.values():
            _add(ambient, getattr(location, "ambient", None))

    return frozenset(music), frozenset(ambient)


def rebind_authored_audio_meta(
    authored: StoryGraph,
    saved_meta: dict[str, Any],
    authored_map: object = None,
    *,
    has_resume_context: bool = False,
    resume_context: object = None,
) -> dict[str, Any]:
    """Clear effective save audio that current pack content cannot produce.

    This is primarily the compatibility boundary for legacy saves without a
    dialogue-resume sidecar. A future line with no explicit audio directive
    inherits ``meta.current_*``; retaining an arbitrary candidate value there
    would otherwise feed it to a decoder after the load preflight had ended.
    """
    music, ambient = authored_story_audio_refs(authored, authored_map)
    rebound = dict(saved_meta)
    removed: set[str] = set()

    def _allowed(value: object, refs: frozenset[str]) -> bool:
        return (
            value is None
            or (
                isinstance(value, str)
                and (
                    value.strip().lower() in {"none", "silence", "-"}
                    or value in refs
                )
            )
        )

    for key, refs in (
        ("current_bgm", music),
        ("current_ambient", ambient),
    ):
        if key in rebound and not _allowed(rebound[key], refs):
            rebound.pop(key, None)
            removed.add(key)

    if has_resume_context:
        raw_resume = resume_context if isinstance(resume_context, dict) else {}
        for key in ("current_bgm", "current_ambient"):
            if key in raw_resume:
                # The shared resume validator will prove this explicit value
                # against current authored authority. Mirroring it into meta
                # prevents a different candidate carry from taking over on the
                # next line after a successful restore.
                rebound[key] = raw_resume[key]
            elif key in removed:
                # A minimal-v1 sidecar derives missing audio from state meta.
                # Silently converting forged evidence to ``None`` would turn a
                # malformed resume into a different, apparently valid screen.
                raise ValueError(
                    f"dialogue resume inherits unauthorised {key}"
                )
    return rebound


def rebind_authored_story_catalog(
    authored: StoryGraph,
    saved: StoryGraph,
) -> StoryGraph:
    """Install current story definitions while retaining safe save progress.

    A save serializes a complete ``StoryGraph`` for schema round-tripping, but
    that copy is user-writable and therefore cannot authorize executable
    narrative data on load.  Lines, media references, conditions, effects,
    choices, transitions, and presentation metadata all come exclusively from
    ``authored``.  Only the played-scene set and active cursor are imported
    from ``saved``.

    Played ids removed from the current catalogue are discarded.  For an
    active scene, ``current_line_index`` points at the *next* authored line.
    When that saved line carries ``loc_id``, the stable id is authoritative and
    is rebound to its position in the current catalogue.  Positional fallback
    is used only for a legacy line without ``loc_id``.  A saved end/choice
    boundary stays a boundary even when the current release appended lines.
    Removed scenes, deleted stable ids, ambiguous ids, and stale positional
    cursors raise ``ValueError`` so the load entry point can reject the
    candidate atomically. Pack migrations remain the supported place to rename
    scene or line identities.

    Runtime/plugin scenes are trusted only when the caller has explicitly
    registered them in the runtime ``authored`` catalogue passed here.  Merely
    inserting a scene into serialized save JSON never expands authority.
    """
    rebound = authored.model_copy(deep=True)
    rebound.played = {
        scene_id for scene_id in saved.played
        if scene_id in rebound.scenes
    }

    current_scene = saved.current_scene
    current_line_index = saved.current_line_index
    if current_scene is None:
        if current_line_index != 0:
            raise ValueError(
                "saved story without an active scene must use line index 0"
            )
        rebound.current_scene = None
        rebound.current_line_index = 0
        return rebound

    scene = rebound.scenes.get(current_scene)
    saved_scene = saved.scenes.get(current_scene)
    if scene is None or saved_scene is None:
        raise ValueError(
            f"saved story cursor references unknown scene {current_scene!r}"
        )
    if (
        type(current_line_index) is not int
        or not 0 <= current_line_index <= len(saved_scene.lines)
    ):
        raise ValueError(
            "saved story cursor is outside its saved scene"
        )

    # ``len(saved_scene.lines)`` is a semantic boundary, not a line position.
    # Keep it at the current authored boundary so an update cannot make an old
    # choice/save checkpoint unexpectedly execute newly appended content.
    if current_line_index == len(saved_scene.lines):
        rebound.current_scene = current_scene
        rebound.current_line_index = len(scene.lines)
        return rebound

    saved_next = saved_scene.lines[current_line_index]
    current_line_index, _ = resolve_authored_line(
        scene,
        loc_id=saved_next.loc_id,
        line_index=current_line_index,
        label="saved story cursor stable line id",
    )
    rebound.current_scene = current_scene
    rebound.current_line_index = current_line_index
    return rebound


def rebind_authored_choice_presentations(
    authored: StoryGraph,
    saved: StoryGraph,
) -> StoryGraph:
    """Compatibility alias for :func:`rebind_authored_story_catalog`.

    The older name described the pre-security-boundary behaviour, where only
    choice cards were refreshed.  Loaders must now treat every story
    definition as authored authority; keeping this wrapper avoids breaking
    third-party imports while applying the stricter contract consistently.
    """
    return rebind_authored_story_catalog(authored, saved)


def rebind_authored_dialogue_history_voices(
    authored: StoryGraph,
    saved: DialogueHistory,
) -> DialogueHistory:
    """Clear scrollback voice refs not authorized by the current story.

    Dialogue history is player progress, but its optional ``voice`` value is
    later sent directly to the audio backend by the scrollback screen.  Keep
    all history copy while replacing any non-string or unknown voice ref with
    ``None``.  The allowlist is built solely from current authored lines,
    including runtime/plugin scenes only when they were registered in the
    supplied authored catalogue.
    """
    authored_voices = {
        line.voice
        for scene in authored.scenes.values()
        for line in scene.lines
        if isinstance(line.voice, str) and line.voice
    }
    rebound = saved.model_copy(deep=True)
    for row in rebound.lines:
        scene_id = row.get("scene_id")
        loc_id = row.get("loc_id")
        line_index = row.get("line_index")
        scene = (
            authored.get(scene_id)
            if isinstance(scene_id, str) and scene_id
            else None
        )
        if loc_id is not None and not isinstance(loc_id, str):
            row["voice"] = None
            continue
        if loc_id is not None and scene is None:
            row["voice"] = None
            continue
        if scene is not None and (
            loc_id is not None or type(line_index) is int
        ):
            try:
                rebound_index, authored_line = resolve_authored_line(
                    scene,
                    loc_id=loc_id if isinstance(loc_id, str) else None,
                    line_index=line_index,
                    label="dialogue history line",
                )
            except (TypeError, ValueError):
                # Stable identity never degrades to a positional/global
                # guess. Preserve historical copy but remove the decoder ref.
                if loc_id is not None:
                    row["voice"] = None
                    continue
            else:
                row["line_index"] = rebound_index
                row["loc_id"] = authored_line.loc_id
                row["voice"] = authored_line.voice
                continue

        voice = row.get("voice")
        if voice is not None and (
            not isinstance(voice, str) or voice not in authored_voices
        ):
            row["voice"] = None
    return rebound
