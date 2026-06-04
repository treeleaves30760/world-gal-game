"""Typed argument models for the builtin effects.

Each model validates the flat ``(target, value, stat)`` triple of an
:class:`~world_gal_game.core.story_graph.Effect` for one ``kind``. The models
serve two read-only purposes:

1. **JSON-Schema export** — ``capability_manifest`` emits ``model_json_schema()``
   so any agent (not just Python) can validate pack edits offline.
2. **Build / lint-time validation** — ``validator`` checks authored effects
   against these models and emits *warnings* (never hard errors).

They are deliberately **NOT** used on the runtime ``GameState.apply`` path,
which stays tolerant (a bad effect degrades to an ``{"error": ...}`` dict, never
a crash). Field types mirror what each handler in ``builtin_effects.py``
actually does — e.g. handlers that read ``int(eff.value or N)`` model ``value``
as ``int | None`` so an omitted value is valid, matching shipping content like a
bare ``- kind: advance_time``.
"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field


class ArgModel(BaseModel):
    """Base for effect/condition arg models.

    ``extra='ignore'`` because the outer ``Effect``/``Condition`` already
    enforce ``extra='forbid'``; here we validate only the meaningful subset of
    (target/value/stat) for one kind, ignoring fields that kind does not use
    (so a stray ``target`` on, say, ``advance_time`` is dropped, not rejected).
    """

    model_config = ConfigDict(extra="ignore")


# A required, non-empty string argument (an id the handler dereferences).
ReqStr = Annotated[str, Field(min_length=1)]


# ----------------------------------------------------------------------
# Affection / stats

class AffectionArgs(ArgModel):
    """affection: adjust a character's affection on an axis."""

    target: ReqStr                 # character_id
    value: int                     # delta (handler does int(eff.value))
    stat: str | None = None        # axis name, default 'affection'


class StatArgs(ArgModel):
    """stat: adjust an arbitrary character stat (axis via stat)."""

    target: ReqStr                 # character_id
    value: int                     # delta
    stat: str | None = None        # axis name, default 'affection'


# ----------------------------------------------------------------------
# Flags

class SetFlagArgs(ArgModel):
    """set_flag: set a flag to value (defaults to True)."""

    target: ReqStr                 # flag_name
    value: Any = None              # any type; None -> True


class SetFlagIfUnsetArgs(ArgModel):
    """set_flag_if_unset: set a flag only when currently falsy / unset."""

    target: ReqStr                 # flag_name
    value: Any = None              # any type; None -> True


class IncrementFlagArgs(ArgModel):
    """increment_flag: add value (default 1) to a numeric flag."""

    target: ReqStr                 # flag_name
    value: int | None = None       # delta, default 1


# ----------------------------------------------------------------------
# Time / location

class AdvanceTimeArgs(ArgModel):
    """advance_time: advance time-of-day by N phases (default 1)."""

    value: int | None = None       # phases, default 1


class MoveToArgs(ArgModel):
    """move_to: move the player to a location id."""

    target: ReqStr                 # location_id


class UnlockLocationArgs(ArgModel):
    """unlock_location: set the implicit unlock flag for a location."""

    target: ReqStr                 # location_id


# ----------------------------------------------------------------------
# Scene control

class PlaySceneArgs(ArgModel):
    """play_scene: transition to another scene (consumed by DialogueEngine)."""

    target: ReqStr                 # scene_id


class EndSceneArgs(ArgModel):
    """end_scene: end the current scene. Takes no arguments."""


class LogEventArgs(ArgModel):
    """log_event: add a custom entry to the event log."""

    target: ReqStr                 # title
    value: str | None = None       # summary


# ----------------------------------------------------------------------
# Inventory

class GiveItemArgs(ArgModel):
    """give_item: add an item to inventory."""

    target: ReqStr                 # item_id
    value: int | None = None       # count, default 1


class TakeItemArgs(ArgModel):
    """take_item: remove an item from inventory."""

    target: ReqStr                 # item_id
    value: int | None = None       # count, default 1


class UseItemArgs(ArgModel):
    """use_item: consume one of an item and apply its use_effects."""

    target: ReqStr                 # item_id


# ----------------------------------------------------------------------
# Resources

class GainResourceArgs(ArgModel):
    """gain_resource: add (or subtract, with negative value) to a resource."""

    target: ReqStr                 # resource_id
    value: int | None = None       # delta


class SpendResourceArgs(ArgModel):
    """spend_resource: spend an amount of a resource; fails if insufficient."""

    target: ReqStr                 # resource_id
    value: int | None = None       # positive amount


class SetResourceArgs(ArgModel):
    """set_resource: set a resource to an absolute value."""

    target: ReqStr                 # resource_id
    value: int | None = None       # absolute


# ----------------------------------------------------------------------
# Shopping / gifting

class BuyItemArgs(ArgModel):
    """buy_item: spend currency, gain one item."""

    target: ReqStr                 # item_id
    stat: str | None = None        # currency_id, default 'money'
    value: int | None = None       # price


class SellItemArgs(ArgModel):
    """sell_item: remove one item, gain currency."""

    target: ReqStr                 # item_id
    stat: str | None = None        # currency_id, default 'money'
    value: int | None = None       # price; defaults to item.value


class GiftArgs(ArgModel):
    """gift: give an item (carried in ``stat``) to an NPC (``target``)."""

    target: ReqStr                 # npc_id
    stat: str | None = None        # item_id (required in practice)
    value: int | None = None       # count, default 1


# ----------------------------------------------------------------------
# Quests

class StartQuestArgs(ArgModel):
    """start_quest: activate a quest."""

    target: ReqStr                 # quest_id


class CompleteObjectiveArgs(ArgModel):
    """complete_objective: mark one objective on a quest done."""

    target: ReqStr                 # quest_id
    stat: str | None = None        # objective_id


class CompleteQuestArgs(ArgModel):
    """complete_quest: mark a quest completed."""

    target: ReqStr                 # quest_id


class FailQuestArgs(ArgModel):
    """fail_quest: mark a quest failed."""

    target: ReqStr                 # quest_id


# ----------------------------------------------------------------------
# Presentation: camera + screen FX. ``value`` is a directive dict; nested
# models give the schema real structure (handlers read value.get(...)).

class CameraPanValue(ArgModel):
    x: float = 0.0
    y: float = 0.0
    duration: float = 0.6
    easing: str | None = None


class CameraPanArgs(ArgModel):
    """camera_pan: pan the camera to an offset (source px) over a duration."""

    value: CameraPanValue | None = None


class CameraZoomValue(ArgModel):
    scale: float = 1.0
    duration: float = 0.6
    easing: str | None = None


class CameraZoomArgs(ArgModel):
    """camera_zoom: zoom to a scale (1.0 = neutral) over a duration."""

    value: CameraZoomValue | None = None


class ScreenShakeValue(ArgModel):
    intensity: float = 12.0
    duration: float = 0.4
    easing: str | None = None


class ScreenShakeArgs(ArgModel):
    """screen_shake: shake the whole frame with a decaying jitter."""

    value: ScreenShakeValue | None = None


class ScreenFlashValue(ArgModel):
    color: list[int] = Field(default_factory=lambda: [255, 255, 255])
    duration: float = 0.3
    max_alpha: int = 255
    easing: str | None = None


class ScreenFlashArgs(ArgModel):
    """screen_flash: flash a colour overlay that fades out over a duration."""

    value: ScreenFlashValue | None = None


class ScreenTintValue(ArgModel):
    color: list[int] | None = Field(default_factory=lambda: [0, 0, 0])
    duration: float = 0.5
    max_alpha: int = 120
    persist: bool = False
    clear: bool = False
    easing: str | None = None


class ScreenTintArgs(ArgModel):
    """screen_tint: persistent colour tint; ``clear=true`` removes it."""

    value: ScreenTintValue | None = None


class ScreenBlurValue(ArgModel):
    radius: float = 8.0
    duration: float = 0.5
    clear: bool = False
    easing: str | None = None


class ScreenBlurArgs(ArgModel):
    """screen_blur: persistent depth-of-field blur on the background layer
    (portraits / CG stay sharp); ``clear=true`` (or ``radius=0``) removes it."""

    value: ScreenBlurValue | None = None


# ----------------------------------------------------------------------
# Presentation: scene transitions. A transition animates the hand-off from the
# previous on-screen frame to the new one. The nested ``TransitionValue`` is
# shared by every effect that changes what is on screen, so an agent learns one
# vocabulary and reuses it for backgrounds, CGs, and stand-alone beats.

class TransitionValue(ArgModel):
    """How to animate a visual change (see ui.transitions.SceneTransition).

    ``style`` is one of ui.transitions.SCENE_TRANSITION_STYLES (cut / dissolve /
    fade / wipe_* / slide_* / iris_* / blinds_* / pixellate / mask). ``color``
    is the curtain colour for ``fade``; ``mask`` is an image path for the
    ``mask`` (image-dissolve) style; ``easing`` is an ui.easing curve name.
    """

    style: str = "dissolve"
    duration: float = 0.6
    easing: str | None = None
    color: list[int] = Field(default_factory=lambda: [0, 0, 0])
    mask: str | None = None        # image path, only used by style="mask"


class SetBackgroundArgs(ArgModel):
    """set_background: change the background mid-scene, with an optional
    transition. ``target`` is the image path; ``value`` is a TransitionValue."""

    target: ReqStr                 # background image path
    value: TransitionValue | None = None


class ShowCgArgs(ArgModel):
    """show_cg: display a full-screen CG, with an optional transition.
    ``target`` is the CG image path; ``value`` is a TransitionValue."""

    target: ReqStr                 # CG image path
    value: TransitionValue | None = None


class HideCgArgs(ArgModel):
    """hide_cg: remove the active CG, with an optional transition.
    Takes no target; ``value`` is a TransitionValue."""

    value: TransitionValue | None = None


class TransitionArgs(ArgModel):
    """transition: play a stand-alone transition beat over the current frame
    (e.g. a fade to black and back) without otherwise changing the scene."""

    value: TransitionValue | None = None


# ----------------------------------------------------------------------
# Presentation: ambient / weather overlays (the @ambient_backend category).

class WeatherValue(ArgModel):
    """Common ambient-overlay params (a backend may read further custom keys).

    ``extra='ignore'`` (from ArgModel) means backend-specific keys an author
    adds are accepted by the lint pass and still passed through verbatim at
    runtime (the handler forwards the raw dict, not this model).
    """

    count: int | None = None       # particle quantity
    seed: int | None = None        # deterministic RNG seed
    alpha: int | None = None       # 0-255 overall overlay opacity
    speed: float | None = None
    wind: float | None = None
    size: float | None = None
    color: list[int] | None = None
    fade: float | None = None      # fade-in seconds (0 = instant)


class SetWeatherArgs(ArgModel):
    """set_weather: turn on an ambient overlay. ``target`` is the registered
    backend name (rain / snow / petals / ...); ``value`` is its params."""

    target: ReqStr                 # ambient backend name
    value: WeatherValue | None = None


class ClearWeatherArgs(ArgModel):
    """clear_weather: remove the active ambient overlay. Optional ``value``
    carries a ``fade`` (fade-out seconds)."""

    value: WeatherValue | None = None


# ----------------------------------------------------------------------
# Presentation: in-place portrait emotes (jump / shake / nod / bounce).

class PortraitEmoteValue(ArgModel):
    """How to play a one-shot in-place portrait accent."""

    emote: str = "jump"            # jump / shake / nod / bounce
    duration: float = 0.45         # seconds
    intensity: float | None = None  # px amplitude (kind-specific default)


class PortraitEmoteArgs(ArgModel):
    """portrait_emote: play a one-shot accent on a settled portrait.

    ``target`` is the slot ('left'/'center'/'right') or the character name whose
    slot to animate; ``value`` selects the emote and its timing."""

    target: ReqStr                 # slot name or character name
    value: PortraitEmoteValue | None = None


# ----------------------------------------------------------------------
# Presentation: full-screen movie playback (OP / ED / cutscene).

class PlayMovieValue(ArgModel):
    """How to play a movie (see scenes.movie_scene.MoviePlayerScene)."""

    kind: str = "auto"             # auto / image_sequence / video / <plugin>
    fps: float = 24.0              # image-sequence frame rate
    loop: bool = False
    skippable: bool = True


class PlayMovieArgs(ArgModel):
    """play_movie: push a full-screen movie overlay. ``target`` is the movie
    path — a frame folder (image sequence) or a video file (desktop video
    plugin); ``value`` selects the player and playback options."""

    target: ReqStr                 # frame folder or video file path
    value: PlayMovieValue | None = None


__all__ = [
    "ArgModel", "ReqStr",
    "AffectionArgs", "StatArgs",
    "SetFlagArgs", "SetFlagIfUnsetArgs", "IncrementFlagArgs",
    "AdvanceTimeArgs", "MoveToArgs", "UnlockLocationArgs",
    "PlaySceneArgs", "EndSceneArgs", "LogEventArgs",
    "GiveItemArgs", "TakeItemArgs", "UseItemArgs",
    "GainResourceArgs", "SpendResourceArgs", "SetResourceArgs",
    "BuyItemArgs", "SellItemArgs", "GiftArgs",
    "StartQuestArgs", "CompleteObjectiveArgs", "CompleteQuestArgs",
    "FailQuestArgs",
    "CameraPanArgs", "CameraZoomArgs", "ScreenShakeArgs",
    "ScreenFlashArgs", "ScreenTintArgs",
    "CameraPanValue", "CameraZoomValue", "ScreenShakeValue",
    "ScreenFlashValue", "ScreenTintValue",
    "TransitionValue", "SetBackgroundArgs", "ShowCgArgs", "HideCgArgs",
    "TransitionArgs",
    "WeatherValue", "SetWeatherArgs", "ClearWeatherArgs",
    "PortraitEmoteValue", "PortraitEmoteArgs",
    "PlayMovieValue", "PlayMovieArgs",
]
