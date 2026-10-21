"""Read log: tracks which dialogue lines the player has already seen.

Persisted with the save file so skip-mode can distinguish new from old text.
"""
from __future__ import annotations

from copy import deepcopy
import math
from pathlib import PurePosixPath, PureWindowsPath
import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from .story_graph import SoundCaptionSpec


_MAX_REPLAY_ENTRIES = 4096
_MAX_REPLAY_PRESENTATIONS = 4096
_MAX_REPLAY_CHOICES = 128
_MAX_REPLAY_FX_PER_PRESENTATION = 64
_MAX_REPLAY_TEXT = 100_000
_MAX_REPLAY_ASSET_PATH = 1024
_MAX_REPLAY_LINE_COUNT = 100_000
_MAX_REPLAY_TOTAL_PRESENTATIONS = 20_000
_LINE_LOC_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
REPLAY_VISUAL_FX_KINDS = frozenset({
    "camera_pan",
    "camera_zoom",
    "screen_shake",
    "screen_flash",
    "screen_tint",
    "screen_blur",
    "set_background",
    "show_cg",
    "hide_cg",
    "transition",
    "set_weather",
    "clear_weather",
    "portrait_emote",
})


def _safe_replay_asset_path(value: str | None) -> bool:
    if value is None:
        return True
    if not isinstance(value, str) or not value or len(value) > _MAX_REPLAY_ASSET_PATH:
        return False
    if "\x00" in value:
        return False
    if value.lower() in {"none", "silence", "-"}:
        return True
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        return False
    return ".." not in posix.parts and ".." not in windows.parts


def _bounded_finite(value: Any, *, low: float, high: float) -> bool:
    return (
        type(value) in {int, float}
        and math.isfinite(float(value))
        and low <= float(value) <= high
    )


def _safe_json_shape(value: Any, *, depth: int = 0) -> bool:
    """Bound plugin-facing replay metadata from user-edited save files."""
    if depth > 8:
        return False
    if value is None or isinstance(value, bool):
        return True
    if type(value) in {int, float}:
        return math.isfinite(float(value)) and abs(float(value)) <= 1_000_000
    if isinstance(value, str):
        return len(value) <= 4096 and "\x00" not in value
    if isinstance(value, list):
        return len(value) <= 128 and all(
            _safe_json_shape(item, depth=depth + 1) for item in value
        )
    if isinstance(value, dict):
        return len(value) <= 128 and all(
            isinstance(key, str)
            and len(key) <= 256
            and _safe_json_shape(item, depth=depth + 1)
            for key, item in value.items()
        )
    return False


def _safe_plugin_metadata(value: Any, *, depth: int = 0) -> bool:
    if not _safe_json_shape(value, depth=depth):
        return False
    if isinstance(value, list):
        return all(_safe_plugin_metadata(item, depth=depth + 1) for item in value)
    if not isinstance(value, dict):
        return True
    path_words = ("path", "file", "image", "model", "mask", "asset")
    for key, item in value.items():
        if (
            isinstance(item, str)
            and any(word in key.lower() for word in path_words)
            and not _safe_replay_asset_path(item)
        ):
            return False
        if not _safe_plugin_metadata(item, depth=depth + 1):
            return False
    return True


def _validate_replay_fx(directive: dict[str, Any]) -> None:
    fx = directive.get("fx")
    if fx not in REPLAY_VISUAL_FX_KINDS or not _safe_plugin_metadata(directive):
        raise ValueError("replay archive visual effect is invalid")
    duration = directive.get("duration")
    if duration is not None and not _bounded_finite(duration, low=0, high=60):
        raise ValueError("replay archive visual effect duration is invalid")
    if fx == "camera_pan" and any(
        not _bounded_finite(directive.get(axis, 0), low=-100_000, high=100_000)
        for axis in ("x", "y")
    ):
        raise ValueError("replay archive camera pan is invalid")
    if fx == "camera_zoom" and not _bounded_finite(
        directive.get("scale", 1), low=0.01, high=4
    ):
        raise ValueError("replay archive camera zoom is invalid")
    if fx == "screen_shake" and not _bounded_finite(
        directive.get("intensity", 12), low=0, high=1000
    ):
        raise ValueError("replay archive screen shake is invalid")
    if fx == "screen_blur" and not _bounded_finite(
        directive.get("radius", 0), low=0, high=128
    ):
        raise ValueError("replay archive screen blur is invalid")
    for key in ("path",):
        if key in directive and not _safe_replay_asset_path(directive.get(key)):
            raise ValueError("replay archive visual asset path is unsafe")
    transition = directive.get("transition")
    if isinstance(transition, dict):
        mask = transition.get("mask")
        if mask is not None and not _safe_replay_asset_path(mask):
            raise ValueError("replay archive transition mask is unsafe")


def _validate_replay_sound_captions(value: object) -> None:
    if not isinstance(value, list) or len(value) > 4:
        raise ValueError("replay archive sound captions are invalid")
    for item in value:
        if not isinstance(item, dict) or set(item) != {"kind", "text"}:
            raise ValueError("replay archive sound caption has invalid fields")
        try:
            caption = SoundCaptionSpec.model_validate(item)
        except Exception as exc:
            raise ValueError("replay archive sound caption is invalid") from exc
        # Runtime archives are produced from already-normalised authored
        # models. Do not silently canonicalise user-edited replay evidence.
        if caption.model_dump(mode="json") != item:
            raise ValueError("replay archive sound caption is not canonical")


def _validate_replay_presentation(
    item: dict[str, Any],
    *,
    scene_id: str | None = None,
) -> None:
    allowed_top = {
        "kind",
        "line",
        "choices",
        "scene_id",
        "title",
        "background",
        "_replay_fx",
        "_replay_hud",
    }
    if set(item) - allowed_top:
        raise ValueError("replay archive presentation has unknown fields")
    kind = item.get("kind")
    if kind not in {"line", "choice"}:
        raise ValueError("replay archive contains an invalid presentation")
    payload_scene = item.get("scene_id")
    if not isinstance(payload_scene, str) or not payload_scene:
        raise ValueError("replay archive presentation scene_id is invalid")
    if scene_id is not None and payload_scene != scene_id:
        raise ValueError("replay archive contains a foreign presentation")
    title = item.get("title")
    if title is not None and (not isinstance(title, str) or len(title) > 4096):
        raise ValueError("replay archive presentation title is invalid")
    if not _safe_replay_asset_path(item.get("background")):
        raise ValueError("replay archive background path is unsafe")

    line = item.get("line")
    choices = item.get("choices", [])
    if kind == "line":
        if not isinstance(line, dict) or choices:
            raise ValueError("replay archive line presentation is invalid")
        allowed_line = {
            "speaker",
            "speaker_id",
            "text",
            "plain_text",
            "portrait",
            "portraits",
            "portrait_pos",
            "expression",
            "cg",
            "background",
            "bgm",
            "ambient",
            "sfx",
            "voice",
            "sound_captions",
            "scene_id",
            "line_index",
            "loc_id",
            "total_lines",
            "is_llm_generated",
        }
        required_line = {
            "text",
            "plain_text",
            "scene_id",
            "line_index",
            "total_lines",
        }
        if set(line) - allowed_line or not required_line.issubset(line):
            raise ValueError("replay archive line has unknown or missing fields")
        _validate_replay_sound_captions(line.get("sound_captions", []))
        raw_portraits = line.get("portraits", [])
        if not isinstance(raw_portraits, list) or len(raw_portraits) > 8:
            raise ValueError("replay archive has too many portraits")
        try:
            # Reuse the save/resume decoder's type checks (portrait specs,
            # cards, booleans) before applying stricter replay bounds below.
            from ..dialogue.dialogue_engine import presentation_from_dict

            decoded = presentation_from_dict(item)
        except Exception as exc:
            raise ValueError("replay archive line presentation is invalid") from exc
        nested_scene = getattr(decoded.line, "scene_id", None)
        if nested_scene != payload_scene:
            raise ValueError("replay archive line belongs to another scene")
        if (
            decoded.line.line_index < 0
            or decoded.line.total_lines <= 0
            or decoded.line.total_lines > _MAX_REPLAY_LINE_COUNT
            or decoded.line.line_index >= decoded.line.total_lines
        ):
            raise ValueError("replay archive line cursor is invalid")
        for text in (
            decoded.line.speaker,
            decoded.line.speaker_id,
            decoded.line.text,
            decoded.line.plain_text,
        ):
            if text is not None and len(text) > _MAX_REPLAY_TEXT:
                raise ValueError("replay archive text is too large")
        for identity in (
            decoded.line.portrait_pos,
            decoded.line.expression,
        ):
            if identity is not None and len(identity) > 512:
                raise ValueError("replay archive line identity is too large")
        for path in (
            decoded.line.cg,
            decoded.line.background,
            decoded.line.bgm,
            decoded.line.ambient,
            decoded.line.sfx,
            decoded.line.voice,
        ):
            if not _safe_replay_asset_path(path):
                raise ValueError("replay archive line asset path is unsafe")
        portraits = [decoded.line.portrait, *decoded.line.portraits]
        for portrait in portraits:
            if isinstance(portrait, str) and not _safe_replay_asset_path(portrait):
                raise ValueError("replay archive portrait path is unsafe")
            image = getattr(portrait, "image", None)
            if image is not None and not _safe_replay_asset_path(image):
                raise ValueError("replay archive portrait asset is unsafe")
            params = getattr(portrait, "backend_args", None)
            if params is not None and not _safe_plugin_metadata(params):
                raise ValueError("replay archive portrait params are unsafe")
    else:
        if line is not None or not isinstance(choices, list) or not choices:
            raise ValueError("replay archive choice presentation is invalid")
        if len(choices) > 128:
            raise ValueError("replay archive has too many choices")
        allowed_choice = {"id", "text", "enabled", "reason", "card"}
        for raw_choice in choices:
            if (
                not isinstance(raw_choice, dict)
                or set(raw_choice) - allowed_choice
                or not {"id", "text", "enabled"}.issubset(raw_choice)
            ):
                raise ValueError("replay archive choice has invalid fields")
        try:
            from ..dialogue.dialogue_engine import presentation_from_dict

            decoded = presentation_from_dict(item)
        except Exception as exc:
            raise ValueError("replay archive choice presentation is invalid") from exc
        if len(decoded.choices) > 128:
            raise ValueError("replay archive has too many choices")
        for choice in decoded.choices:
            if (
                len(choice.id) > 512
                or len(choice.text) > _MAX_REPLAY_TEXT
                or len(choice.reason) > 4096
            ):
                raise ValueError("replay archive choice text is too large")
            card = choice.card
            if card is not None and card.image is not None:
                if not _safe_replay_asset_path(card.image):
                    raise ValueError("replay archive choice image is unsafe")

    replay_fx = item.get("_replay_fx", [])
    if (
        not isinstance(replay_fx, list)
        or len(replay_fx) > _MAX_REPLAY_FX_PER_PRESENTATION
        or not all(isinstance(directive, dict) for directive in replay_fx)
    ):
        raise ValueError("replay archive visual effects are invalid")
    for directive in replay_fx:
        _validate_replay_fx(directive)
    replay_hud = item.get("_replay_hud")
    if replay_hud is not None:
        if not isinstance(replay_hud, dict):
            raise ValueError("replay archive HUD snapshot must be a mapping")
        if set(replay_hud) - {"chapter_title", "date_label", "time_of_day"}:
            raise ValueError("replay archive HUD snapshot has unknown fields")
        for key in ("chapter_title", "date_label"):
            label = replay_hud.get(key)
            if label is not None and (
                not isinstance(label, str) or len(label) > 512
            ):
                raise ValueError("replay archive HUD label is invalid")
        time_of_day = replay_hud.get("time_of_day")
        if time_of_day not in {
            None,
            "morning",
            "noon",
            "afternoon",
            "evening",
            "night",
            "midnight",
        }:
            raise ValueError("replay archive time-of-day snapshot is invalid")


class ReplayArchiveEntry(BaseModel):
    """First-view presentation trace for one completed story scene.

    Recollection mode must never re-evaluate authored conditions against the
    player's *current* state: doing that can change interpolated text, expose a
    branch that was not seen, or run through a newly available chained scene.
    The archive therefore stores the already-evaluated presentation payloads
    returned by :class:`DialogueEngine`, plus the choice ids the player
    actually selected.  Playback redraws these payloads without applying
    effects.

    Incomplete entries travel with mid-dialogue saves but are not offered by
    the replay library.  Legacy saves have no entries and fail closed: a scene
    becomes replayable after it is next viewed from its beginning on a version
    that records this format.
    """

    format_version: Literal[1] = 1
    scene_id: str
    title: str = ""
    presentations: list[dict[str, Any]] = Field(default_factory=list)
    selected_choices: list[str] = Field(default_factory=list)
    complete: bool = False
    overflowed: bool = False
    invalid: bool = False

    @field_validator("scene_id")
    @classmethod
    def _valid_scene_id(cls, value: str) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > 512
        ):
            raise ValueError("replay archive scene_id must be a non-empty string")
        return value

    @field_validator("title")
    @classmethod
    def _valid_title(cls, value: str) -> str:
        if not isinstance(value, str) or len(value) > 4096:
            raise ValueError("replay archive title is invalid")
        return value

    @field_validator("presentations")
    @classmethod
    def _bounded_presentations(
        cls,
        value: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if len(value) > _MAX_REPLAY_PRESENTATIONS:
            raise ValueError("replay archive presentation limit exceeded")
        if not all(isinstance(item, dict) for item in value):
            raise ValueError("replay archive presentations must be mappings")
        for item in value:
            _validate_replay_presentation(item)
        return value

    @field_validator("selected_choices")
    @classmethod
    def _bounded_choices(cls, value: list[str]) -> list[str]:
        if len(value) > _MAX_REPLAY_CHOICES:
            raise ValueError("replay archive choice limit exceeded")
        if not all(isinstance(item, str) and item for item in value):
            raise ValueError("replay archive choices must be non-empty strings")
        return value

    def has_complete_choice_evidence(self) -> bool:
        """Whether every archived decision names an enabled seen option."""
        choice_steps = [
            item for item in self.presentations if item.get("kind") == "choice"
        ]
        if len(choice_steps) != len(self.selected_choices):
            return False
        for item, selected_id in zip(choice_steps, self.selected_choices):
            raw_choices = item.get("choices", [])
            if not isinstance(raw_choices, list):
                return False
            selected = next(
                (
                    choice
                    for choice in raw_choices
                    if isinstance(choice, dict)
                    and choice.get("id") == selected_id
                ),
                None,
            )
            if selected is None or selected.get("enabled") is not True:
                return False
        return True

    @model_validator(mode="after")
    def _complete_entry_has_choice_evidence(self) -> "ReplayArchiveEntry":
        for item in self.presentations:
            _validate_replay_presentation(item, scene_id=self.scene_id)
        line_indices: list[int] = []
        total_lines: set[int] = set()
        choice_positions: list[int] = []
        for position, item in enumerate(self.presentations):
            if item.get("kind") == "choice":
                choice_positions.append(position)
                continue
            line = item["line"]
            line_indices.append(line["line_index"])
            total_lines.add(line["total_lines"])
        if any(
            right <= left
            for left, right in zip(line_indices, line_indices[1:])
        ):
            raise ValueError("replay archive line indices must increase")
        if len(total_lines) > 1:
            raise ValueError("replay archive line totals are inconsistent")
        if len(choice_positions) > 1 or (
            choice_positions and choice_positions[0] != len(self.presentations) - 1
        ):
            raise ValueError("replay archive choice must be the terminal step")
        if self.complete and (self.overflowed or self.invalid):
            raise ValueError("invalid replay archive cannot be complete")
        if self.complete and not self.has_complete_choice_evidence():
            raise ValueError("completed replay archive has incomplete choice evidence")
        return self


class ReadLog(BaseModel):
    """Already-seen line record that travels with the save file."""

    # Legacy lines use ``scene_id:line_index``. Identity-bearing authored lines
    # use ``scene_id:@loc_id`` so insertions/reordering do not move read state.
    # Both forms stay in the same additive field, preserving old save payloads.
    lines: set[str] = Field(default_factory=set)
    # Scene ids the player has read completely.
    scenes: set[str] = Field(default_factory=set)
    # Exact first-view presentation traces.  This is deliberately separate
    # from ``scenes``: cross-playthrough clear data can prove that a scene was
    # read, but it cannot reconstruct the flags / choices / interpolated text
    # that were on screen at the time.
    replay_archive: dict[str, ReplayArchiveEntry] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}

    # Pydantic v2 keeps set as set in model_dump(); we must serialise to list
    # so json.dumps works without a custom encoder.
    @field_serializer("lines", "scenes")
    def _serialize_set(self, v: set[str]) -> list[str]:
        return sorted(v)

    @field_validator("replay_archive", mode="before")
    @classmethod
    def _bounded_archive(cls, value):
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("replay_archive must be a mapping")
        if len(value) > _MAX_REPLAY_ENTRIES:
            raise ValueError("replay archive entry limit exceeded")
        return value

    @model_validator(mode="after")
    def _archive_keys_match_entries(self) -> "ReadLog":
        total_presentations = sum(
            len(entry.presentations) for entry in self.replay_archive.values()
        )
        if total_presentations > _MAX_REPLAY_TOTAL_PRESENTATIONS:
            raise ValueError("replay archive global presentation limit exceeded")
        for scene_id, entry in self.replay_archive.items():
            if (
                not isinstance(scene_id, str)
                or not scene_id
                or len(scene_id) > 512
                or scene_id != entry.scene_id
            ):
                raise ValueError("replay archive key does not match scene_id")
        return self

    @staticmethod
    def line_key(
        scene_id: str,
        line_index: int,
        *,
        loc_id: str | None = None,
    ) -> str:
        if loc_id is not None:
            if (
                not isinstance(loc_id, str)
                or _LINE_LOC_ID_RE.fullmatch(loc_id) is None
            ):
                raise ValueError(
                    "line loc_id must match "
                    "[A-Za-z0-9][A-Za-z0-9_-]{0,63}"
                )
            return f"{scene_id}:@{loc_id}"
        return f"{scene_id}:{line_index}"

    # Compatibility alias for embedding/plugin callers that adopted the
    # private helper before stable line identity became a persistence contract.
    _line_read_key = line_key

    def mark_line(
        self,
        scene_id: str,
        line_index: int,
        *,
        loc_id: str | None = None,
    ) -> bool:
        """Record that a line has been seen. Returns True on first encounter."""
        key = self.line_key(scene_id, line_index, loc_id=loc_id)
        is_new = key not in self.lines
        self.lines.add(key)
        return is_new

    def is_read(
        self,
        scene_id: str,
        line_index: int,
        *,
        loc_id: str | None = None,
    ) -> bool:
        """Return True if this line has been seen before."""
        key = self.line_key(scene_id, line_index, loc_id=loc_id)
        return key in self.lines

    def mark_scene_done(self, scene_id: str) -> None:
        """Mark a whole scene as completed."""
        self.scenes.add(scene_id)
        entry = self.replay_archive.get(scene_id)
        if (
            entry is not None
            and entry.presentations
            and not entry.overflowed
            and not entry.invalid
            and entry.has_complete_choice_evidence()
        ):
            entry.complete = True

    # ---- faithful recollection archive ---------------------------------

    def begin_replay_record(self, scene_id: str, title: str = "") -> bool:
        """Begin a first-view trace, returning whether a new trace was made.

        A completed trace is immutable: naturally revisiting a scene later
        must not rewrite the remembered first path using the new playthrough's
        state.  An incomplete trace is reset only when the authored scene is
        explicitly started again; save/load resume does not call this method
        and therefore continues the in-progress trace.
        """
        existing = self.replay_archive.get(scene_id)
        if existing is not None and existing.complete:
            return False
        if existing is None and len(self.replay_archive) >= _MAX_REPLAY_ENTRIES:
            return False
        try:
            entry = ReplayArchiveEntry(scene_id=scene_id, title=title)
        except (TypeError, ValueError):
            # Recollection is additive. A pack scene that exceeds the archive
            # envelope must still play normally; it simply has no replay row.
            return False
        self.replay_archive[scene_id] = entry
        return True

    def record_replay_presentation(
        self,
        scene_id: str,
        presentation: dict[str, Any],
    ) -> bool:
        """Append one evaluated line/choice payload to an active trace.

        Duplicate consecutive payloads are ignored.  This matters at a choice
        boundary where an invalid or disabled selection asks the engine to
        present the same options again.
        """
        entry = self.replay_archive.get(scene_id)
        if entry is None or entry.complete or entry.overflowed or entry.invalid:
            return False
        kind = presentation.get("kind") if isinstance(presentation, dict) else None
        if kind not in {"line", "choice"}:
            return False
        payload = deepcopy(presentation)
        try:
            _validate_replay_presentation(payload, scene_id=scene_id)
        except (TypeError, ValueError):
            entry.invalid = True
            return False
        if entry.presentations and entry.presentations[-1] == payload:
            return False
        if len(entry.presentations) >= _MAX_REPLAY_PRESENTATIONS:
            entry.overflowed = True
            return False
        total_presentations = sum(
            len(item.presentations) for item in self.replay_archive.values()
        )
        if total_presentations >= _MAX_REPLAY_TOTAL_PRESENTATIONS:
            entry.overflowed = True
            return False
        entry.presentations.append(payload)
        return True

    def record_replay_choice(self, scene_id: str, choice_id: str) -> bool:
        """Record the choice actually taken at the current archived boundary."""
        entry = self.replay_archive.get(scene_id)
        if (
            entry is None
            or entry.complete
            or entry.overflowed
            or entry.invalid
            or not choice_id
            or not entry.presentations
            or entry.presentations[-1].get("kind") != "choice"
            or len(entry.selected_choices) >= _MAX_REPLAY_CHOICES
        ):
            return False
        raw_choices = entry.presentations[-1].get("choices", [])
        selected = next(
            (
                choice
                for choice in raw_choices
                if isinstance(choice, dict) and choice.get("id") == choice_id
            ),
            None,
        )
        if selected is None or selected.get("enabled") is not True:
            return False
        entry.selected_choices.append(choice_id)
        return True

    def prepend_replay_visual_fx(
        self,
        scene_id: str,
        directives: list[dict[str, Any]],
    ) -> bool:
        """Attach transition FX inherited by a chained scene's first display.

        Choice/on-end effects execute before ``start_scene(next)``.  The live
        renderer consumes those directives together with the next scene's
        first line, so the archive must assign them to that first presentation
        rather than to the episode that has already ended.
        """
        entry = self.replay_archive.get(scene_id)
        if (
            entry is None
            or entry.complete
            or entry.overflowed
            or entry.invalid
            or not entry.presentations
            or not directives
        ):
            return False
        try:
            copied = [deepcopy(directive) for directive in directives]
            for directive in copied:
                _validate_replay_fx(directive)
        except (TypeError, ValueError):
            entry.invalid = True
            return False
        first = entry.presentations[0]
        existing = first.get("_replay_fx", [])
        if not isinstance(existing, list):
            entry.invalid = True
            return False
        combined = [*copied, *existing]
        if len(combined) > _MAX_REPLAY_FX_PER_PRESENTATION:
            entry.overflowed = True
            return False
        first["_replay_fx"] = combined
        return True

    def replay_entry(self, scene_id: str) -> ReplayArchiveEntry | None:
        """Return a complete, non-empty trace or ``None`` (fail closed)."""
        entry = self.replay_archive.get(scene_id)
        if (
            entry is None
            or not entry.complete
            or entry.overflowed
            or entry.invalid
            or not entry.presentations
        ):
            return None
        return entry

    def replayable_scene_ids(self) -> set[str]:
        """Scene ids backed by complete presentation evidence."""
        return {
            scene_id
            for scene_id, entry in self.replay_archive.items()
            if (
                entry.complete
                and not entry.overflowed
                and not entry.invalid
                and bool(entry.presentations)
            )
        }

    @classmethod
    def model_validate(cls, obj, **kwargs):  # type: ignore[override]
        # Accept both list and set for the set fields during deserialisation.
        if isinstance(obj, dict):
            for key in ("lines", "scenes"):
                if key in obj and isinstance(obj[key], list):
                    obj = {**obj, key: set(obj[key])}
        return super().model_validate(obj, **kwargs)
