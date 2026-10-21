"""Dialogue engine: walks through scenes one line at a time.

The engine is *cooperative*: the UI calls .next_line() to advance a line,
or .choose(choice_id) to pick a choice at a decision point. State is held
inside GameState (not in the engine itself), so the engine is safe to
recreate between requests in a web server.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import math
from typing import Any, Callable, Sequence

import re

from ..core.game_state import GameState
from ..core.event_log import DialogueHistory
from ..core.cg_gallery import canonical_cg_path
from ..core.map_system import MapSystem
from ..core.story_graph import (
    Choice,
    ChoiceCardSpec,
    Line,
    Scene,
    SoundCaptionSpec,
    StoryGraph,
    resolve_authored_line,
)
from ..core.portrait_spec import PortraitSpec
from ..core.text_interpolation import interpolate
from .richtext import MAX_INLINE_WAIT_SECONDS, parse, strip_markup
from ..ui.easing import EASING_NAMES


_DIALOGUE_OP_RE = re.compile(r"\[\[([a-zA-Z_][a-zA-Z0-9_]*)(?::([^\]]*))?\]\]")
# Private transaction depth read by persistence plugins. A dialogue effect can
# fire hooks (notably TIME_ADVANCE) before the engine has advanced its story
# cursor and returned the new presentation. Saving in that gap produces a
# half-committed state. The counter makes nested start_scene() calls safe and
# is stripped from saves with all other ``__`` meta bridges.
DIALOGUE_TRANSACTION_KEY = "__dialogue_transaction__"
REPLAY_HUD_META_KEY = "__replay_hud__"
REPLAY_FX_PAYLOAD_KEY = "_replay_fx"
REPLAY_HUD_PAYLOAD_KEY = "_replay_hud"
REPLAY_PENDING_FX_META_KEY = "__replay_pending_fx__"

# Resume data comes from a user-writable JSON file and feeds pygame allocation
# sizes / renderer coordinates.  Merely requiring a finite number is not
# sufficient: a finite 1e308 zoom still overflows ``int(width * zoom)`` and a
# huge blur radius can lock up or crash the renderer.  These are deliberately
# generous presentation limits, not authoring recommendations.
_RESUME_MIN_CAMERA_ZOOM = 0.01
_RESUME_MAX_CAMERA_ZOOM = 4.0
_RESUME_MAX_CAMERA_PAN = 100_000.0
_RESUME_MAX_BACKGROUND_BLUR = 128.0
_RESUME_MIN_PORTRAIT_SCALE = 0.01
_RESUME_MAX_PORTRAIT_SCALE = 4.0
_RESUME_MAX_PORTRAIT_OFFSET = 100_000
_RESUME_MAX_TEXT_REVEAL_CURSOR = 10_000_000
_RESUME_MAX_TEXT_WAIT_COUNT = 10_000
_RESUME_MAX_TEXT_WAIT_SECONDS = MAX_INLINE_WAIT_SECONDS
_RESUME_MAX_VOICE_SECONDS = 24 * 60 * 60
_RESUME_MAX_VISUAL_TWEEN_SECONDS = 60.0
_RESUME_MAX_WEATHER_PARTICLES = 5_000
_RESUME_MAX_LINE_TEXT_CHARS = 100_000
_RESUME_MAX_TRANSCRIPT_ENTRIES = 4_096
_RESUME_MAX_TRANSCRIPT_CHARS = 1_000_000
_AUDIO_SILENCE_SENTINELS = frozenset({"none", "silence", "-"})


def _is_audio_silence_ref(value: str | None) -> bool:
    return (
        isinstance(value, str)
        and value.strip().lower() in _AUDIO_SILENCE_SENTINELS
    )


@contextmanager
def _dialogue_transaction(state: GameState):
    depth = int(state.meta.get(DIALOGUE_TRANSACTION_KEY, 0) or 0)
    state.meta[DIALOGUE_TRANSACTION_KEY] = depth + 1
    try:
        yield
    finally:
        if depth:
            state.meta[DIALOGUE_TRANSACTION_KEY] = depth
        else:
            state.meta.pop(DIALOGUE_TRANSACTION_KEY, None)


def _apply_dialogue_ops(text: str, state: GameState) -> str:
    """Strip `[[op:arg]]` directives and fire registered handlers.

    The handler's return value (when a string) replaces the directive
    inline; otherwise the directive is removed entirely. Unknown ops
    pass through untouched so plugin authors can spot missing handlers
    in the rendered text (rather than silently dropping them).
    """
    from ..plugins.registry import DIALOGUE_OP_REGISTRY
    from ..plugins.errors import isolate

    def _sub(m: re.Match) -> str:
        name = m.group(1)
        arg = m.group(2) or ""
        entry = DIALOGUE_OP_REGISTRY.get(name)
        if entry is None:
            return m.group(0)  # leave [[unknown:foo]] in place
        repl_holder: list[str] = []
        with isolate(entry.plugin_id, f"dialogue_op:{name}"):
            result = entry.fn(state, arg)
            if isinstance(result, str):
                repl_holder.append(result)
        return repl_holder[0] if repl_holder else ""

    return _DIALOGUE_OP_RE.sub(_sub, text)


def _present_choice_card(
    card: ChoiceCardSpec | None,
    state: GameState,
    *,
    key_prefix: str = "",
    translate: Callable[[str, str], str] | None = None,
) -> ChoiceCardSpec | None:
    """Interpolate renderer-facing card copy without mutating authored data."""
    if card is None:
        return None
    data = card.model_dump(mode="python")
    for key in ("title", "subtitle", "badge", "image_alt", "action"):
        source = data[key]
        if translate is not None and source:
            source = translate(f"{key_prefix}.card.{key}", source)
        data[key] = interpolate(source, state)
    data["details"] = [
        {
            "label": interpolate(
                (
                    translate(
                        f"{key_prefix}.card.detail.{index}.label",
                        detail["label"],
                    )
                    if translate is not None
                    else detail["label"]
                ),
                state,
            ),
            "text": interpolate(
                (
                    translate(
                        f"{key_prefix}.card.detail.{index}.text",
                        detail["text"],
                    )
                    if translate is not None
                    else detail["text"]
                ),
                state,
            ),
        }
        for index, detail in enumerate(data["details"])
    ]
    data["tags"] = [
        interpolate(
            (
                translate(f"{key_prefix}.card.tag.{index}", tag)
                if translate is not None
                else tag
            ),
            state,
        )
        for index, tag in enumerate(data["tags"])
    ]
    return ChoiceCardSpec.model_validate(data)


@dataclass
class LinePresentation:
    """What the UI needs to render a single line."""

    speaker: str | None
    text: str                       # raw markup (parsed by the UI layer)
    plain_text: str = ""            # markup stripped, for headless / history
    portrait: "str | PortraitSpec | None" = None
    portraits: list = field(default_factory=list)   # list[PortraitSpec], multi-character staging
    portrait_pos: str | None = None                 # slot for the simple portrait forms
    expression: str | None = None
    cg: str | None = None
    background: str | None = None
    bgm: str | None = None
    ambient: str | None = None
    sfx: str | None = None
    voice: str | None = None
    sound_captions: list[SoundCaptionSpec] = field(default_factory=list)
    scene_id: str | None = None
    line_index: int = 0
    total_lines: int = 0
    effects_applied: list[dict[str, Any]] = field(default_factory=list)
    is_llm_generated: bool = False
    # Stable authored identity used by every non-visual speaker lookup.  The
    # localized ``speaker`` above is display copy only: using it as an NPC key
    # breaks portraits, name colours, voice sliders, and speaking-slot routing
    # as soon as a catalog translates a character name.  Kept at the end to
    # preserve the positional constructor contract for embedding clients.
    speaker_id: str | None = None
    # Stable authored line identity. ``line_index`` remains useful display and
    # legacy evidence, while persistence/provenance resolves this id first so a
    # pack update may insert or reorder unrelated lines without rebinding the
    # saved presentation to different content.
    loc_id: str | None = None


@dataclass
class ChoiceOption:
    id: str
    text: str
    enabled: bool
    # When a visible choice is locked, ``reason`` is a concise human-readable
    # phrase explaining why (the unmet condition, e.g. "需要 與白若雪的好感度 ≥
    # 40"), so the UI can render it instead of a silent ghost button. Empty for
    # an enabled choice. Computed from the failing requires/forbids.
    reason: str = ""
    # Optional, presentation-only enrichment. ``text`` remains the functional
    # label and fallback for clients that do not render cards.
    card: ChoiceCardSpec | None = None


@dataclass
class ScenePresentation:
    """State returned by next() — either a line or a choice point or end."""

    kind: str   # "line", "choice", "end", "transition"
    line: LinePresentation | None = None
    choices: list[ChoiceOption] = field(default_factory=list)
    next_scene: str | None = None
    scene_id: str | None = None
    title: str | None = None
    background: str | None = None


@dataclass(frozen=True)
class _ResumeAssetAuthority:
    """Pack-authored refs that a user-writable resume may reconstruct."""

    backgrounds: frozenset[str]
    cgs: frozenset[str]
    music: frozenset[str]
    ambient: frozenset[str]
    portrait_paths: frozenset[str]
    portrait_specs: tuple[PortraitSpec, ...]
    weather: tuple[dict[str, Any], ...]


def _portrait_to_data(value: str | PortraitSpec | None):
    """Encode a presentation portrait without losing its concrete type."""
    if isinstance(value, PortraitSpec):
        return {
            "_type": "portrait_spec",
            "value": value.model_dump(mode="json"),
        }
    if value is None or isinstance(value, str):
        return value
    raise TypeError(
        f"unsupported presentation portrait type: {type(value).__name__}"
    )


def _portrait_from_data(value) -> str | PortraitSpec | None:
    if value is None or isinstance(value, str):
        return value
    if (isinstance(value, dict)
            and value.get("_type") == "portrait_spec"
            and isinstance(value.get("value"), dict)):
        spec = PortraitSpec.model_validate(value["value"])
        _validate_resume_portrait_spec(spec)
        return spec
    raise ValueError("invalid serialized presentation portrait")


def _bounded_resume_number(
    value: object,
    label: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    """Decode one renderer-facing number without overflow or unsafe ranges."""
    if type(value) not in (int, float):
        raise ValueError(f"dialogue resume {label} must be numeric")
    try:
        number = float(value)
    except (ArithmeticError, TypeError, ValueError) as exc:
        raise ValueError(
            f"dialogue resume {label} is outside the numeric range"
        ) from exc
    if not math.isfinite(number):
        raise ValueError(f"dialogue resume {label} must be finite")
    if number < minimum or number > maximum:
        raise ValueError(
            f"dialogue resume {label} must be between "
            f"{minimum:g} and {maximum:g}"
        )
    return number


def _validate_resume_portrait_spec(spec: PortraitSpec) -> None:
    """Bound portrait geometry before it reaches pygame.Rect/scaling paths."""
    _bounded_resume_number(
        spec.scale,
        "portrait.scale",
        minimum=_RESUME_MIN_PORTRAIT_SCALE,
        maximum=_RESUME_MAX_PORTRAIT_SCALE,
    )
    for axis, value in zip(("x", "y"), spec.offset, strict=True):
        if type(value) is not int or abs(value) > _RESUME_MAX_PORTRAIT_OFFSET:
            raise ValueError(
                f"dialogue resume portrait.offset.{axis} must be an integer "
                f"between {-_RESUME_MAX_PORTRAIT_OFFSET} and "
                f"{_RESUME_MAX_PORTRAIT_OFFSET}"
            )


def presentation_to_dict(pres: ScenePresentation) -> dict[str, Any]:
    """Return a JSON-native snapshot of an already-rendered presentation.

    Save/load and rollback must redraw the presentation that the player
    actually saw, not ask :class:`DialogueEngine` to render the source line
    again. Re-rendering would repeat effects, dialogue operators, hooks,
    read-log writes, and event-history entries. The explicit representation
    also preserves :class:`PortraitSpec` rather than letting ``json.dump``
    stringify it.
    """
    line = pres.line
    line_data = None
    if line is not None:
        line_data = {
            "speaker": line.speaker,
            "speaker_id": line.speaker_id,
            "text": line.text,
            "plain_text": line.plain_text,
            "portrait": _portrait_to_data(line.portrait),
            "portraits": [_portrait_to_data(p) for p in line.portraits],
            "portrait_pos": line.portrait_pos,
            "expression": line.expression,
            "cg": line.cg,
            "background": line.background,
            "bgm": line.bgm,
            "ambient": line.ambient,
            "sfx": line.sfx,
            "voice": line.voice,
            "sound_captions": [
                caption.model_dump(mode="json")
                for caption in line.sound_captions
            ],
            "scene_id": line.scene_id,
            "line_index": line.line_index,
            "loc_id": line.loc_id,
            "total_lines": line.total_lines,
            "effects_applied": line.effects_applied,
            "is_llm_generated": line.is_llm_generated,
        }
    return {
        "kind": pres.kind,
        "line": line_data,
        "choices": [
            {
                "id": choice.id,
                "text": choice.text,
                "enabled": choice.enabled,
                "reason": choice.reason,
                **(
                    {"card": choice.card.model_dump(mode="json")}
                    if choice.card is not None
                    else {}
                ),
            }
            for choice in pres.choices
        ],
        "next_scene": pres.next_scene,
        "scene_id": pres.scene_id,
        "title": pres.title,
        "background": pres.background,
    }


def replay_presentation_to_dict(pres: ScenePresentation) -> dict[str, Any]:
    """Return the compact, redraw-only form persisted by recollection mode.

    ``effects_applied`` is intentionally excluded: replay redraws evaluated
    output and must never reapply those mutations.  Omitting null/default
    fields keeps a large visual novel's archive proportional to authored text
    instead of paying for dozens of JSON keys on every line;
    :func:`presentation_from_dict` supplies the same renderer defaults.
    """
    data = presentation_to_dict(pres)
    line = data.get("line")
    if isinstance(line, dict):
        line.pop("effects_applied", None)
        if line.get("is_llm_generated") is False:
            line.pop("is_llm_generated", None)
        for key in tuple(line):
            if line[key] is None or line[key] == []:
                line.pop(key)
    for choice in data.get("choices", []):
        if not choice.get("reason"):
            choice.pop("reason", None)
    for key in tuple(data):
        if key == "kind":
            continue
        if data[key] is None or data[key] == []:
            data.pop(key)
    return data


def presentation_from_dict(data: dict[str, Any]) -> ScenePresentation:
    """Rebuild a presentation produced by :func:`presentation_to_dict`."""
    if not isinstance(data, dict):
        raise ValueError("serialized presentation must be a mapping")

    def _optional_str(mapping: dict[str, Any], key: str) -> str | None:
        value = mapping.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"serialized presentation {key} must be a string")
        if isinstance(value, str) and len(value) > _RESUME_MAX_LINE_TEXT_CHARS:
            raise ValueError(f"serialized presentation {key} is too long")
        return value

    def _str(mapping: dict[str, Any], key: str,
             default: str = "") -> str:
        value = mapping.get(key, default)
        if not isinstance(value, str):
            raise ValueError(f"serialized presentation {key} must be a string")
        if len(value) > _RESUME_MAX_LINE_TEXT_CHARS:
            raise ValueError(f"serialized presentation {key} is too long")
        return value

    def _int(mapping: dict[str, Any], key: str, default: int = 0) -> int:
        value = mapping.get(key, default)
        if type(value) is not int:
            raise ValueError(f"serialized presentation {key} must be an integer")
        return value

    def _optional_loc_id(
        mapping: dict[str, Any],
        key: str = "loc_id",
    ) -> str | None:
        value = mapping.get(key)
        if value is None:
            return None
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value)
            is None
        ):
            raise ValueError(
                f"serialized presentation {key} is not a valid line identity"
            )
        return value

    kind = data.get("kind")
    if kind not in {"line", "choice", "end", "transition"}:
        raise ValueError(f"invalid serialized presentation kind: {kind!r}")

    line_data = data.get("line")
    line = None
    if line_data is not None:
        if not isinstance(line_data, dict):
            raise ValueError("serialized presentation line must be a mapping")
        raw_portraits = line_data.get("portraits", [])
        if not isinstance(raw_portraits, list) or len(raw_portraits) > 128:
            raise ValueError("serialized presentation portraits must be a list")
        portrait_pos = _optional_str(line_data, "portrait_pos")
        if portrait_pos not in (None, "left", "center", "right"):
            raise ValueError("serialized presentation portrait_pos is invalid")
        effects_applied = line_data.get("effects_applied", [])
        if (
            not isinstance(effects_applied, list)
            or len(effects_applied) > 10_000
        ):
            raise ValueError(
                "serialized presentation effects_applied must be a list"
            )
        is_llm_generated = line_data.get("is_llm_generated", False)
        if not isinstance(is_llm_generated, bool):
            raise ValueError(
                "serialized presentation is_llm_generated must be boolean"
            )
        raw_sound_captions = line_data.get("sound_captions", [])
        if (
            not isinstance(raw_sound_captions, list)
            or len(raw_sound_captions) > 4
        ):
            raise ValueError(
                "serialized presentation sound_captions must be a list "
                "with at most four items"
            )
        try:
            sound_captions = [
                SoundCaptionSpec.model_validate(item)
                for item in raw_sound_captions
            ]
        except Exception as exc:
            raise ValueError(
                "serialized presentation sound_captions are invalid"
            ) from exc
        line = LinePresentation(
            speaker=_optional_str(line_data, "speaker"),
            text=_str(line_data, "text"),
            speaker_id=_optional_str(line_data, "speaker_id"),
            plain_text=_str(line_data, "plain_text"),
            portrait=_portrait_from_data(line_data.get("portrait")),
            portraits=[_portrait_from_data(p) for p in raw_portraits],
            portrait_pos=portrait_pos,
            expression=_optional_str(line_data, "expression"),
            cg=_optional_str(line_data, "cg"),
            background=_optional_str(line_data, "background"),
            bgm=_optional_str(line_data, "bgm"),
            ambient=_optional_str(line_data, "ambient"),
            sfx=_optional_str(line_data, "sfx"),
            voice=_optional_str(line_data, "voice"),
            sound_captions=sound_captions,
            scene_id=_optional_str(line_data, "scene_id"),
            line_index=_int(line_data, "line_index"),
            loc_id=_optional_loc_id(line_data),
            total_lines=_int(line_data, "total_lines"),
            effects_applied=list(effects_applied),
            is_llm_generated=is_llm_generated,
        )

    raw_choices = data.get("choices", [])
    if not isinstance(raw_choices, list) or len(raw_choices) > 1_024:
        raise ValueError("serialized presentation choices must be a list")
    choices: list[ChoiceOption] = []
    for item in raw_choices:
        if not isinstance(item, dict):
            raise ValueError("serialized choice must be a mapping")
        enabled = item.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError("serialized choice enabled must be boolean")
        choices.append(ChoiceOption(
            id=_str(item, "id"),
            text=_str(item, "text"),
            enabled=enabled,
            reason=_str(item, "reason"),
            card=(
                ChoiceCardSpec.model_validate(item["card"])
                if item.get("card") is not None
                else None
            ),
        ))

    if kind == "line":
        if line is None:
            raise ValueError("serialized line presentation requires line data")
        if choices:
            raise ValueError("serialized line presentation cannot carry choices")
    elif kind == "choice":
        if line is not None:
            raise ValueError("serialized choice presentation cannot carry a line")
        if not choices:
            raise ValueError("serialized choice presentation requires choices")
    elif line is not None or choices:
        raise ValueError(
            f"serialized {kind} presentation cannot carry line/choice data"
        )

    return ScenePresentation(
        kind=kind,
        line=line,
        choices=choices,
        next_scene=_optional_str(data, "next_scene"),
        scene_id=_optional_str(data, "scene_id"),
        title=_optional_str(data, "title"),
        background=_optional_str(data, "background"),
    )


def _sound_caption_data(line: Line) -> list[dict[str, Any]]:
    return [
        caption.model_dump(mode="json")
        for caption in line.sound_captions
    ]


def _localization_identity(localization: object | None) -> tuple[str, str]:
    """Return the presentation locale and source-catalog identity."""
    if localization is None:
        return "", ""
    locale = getattr(localization, "effective", "")
    digest = getattr(localization, "source_digest", "")
    return (
        locale if isinstance(locale, str) else "",
        digest if isinstance(digest, str) else "",
    )


def _resolve_authored_speaker_id(
    resolver: Callable[[str], str | None] | None,
    authored_speaker: str | None,
) -> str | None:
    """Return a stable id from canonical authored speaker copy.

    Localized speaker labels are intentionally not accepted here.  Besides
    being unstable across catalogs, a translation may collide with another
    NPC's source-language name.  Optional resolvers are an embedding seam and
    must not be able to make dialogue presentation or save compatibility fail.
    """
    if not authored_speaker:
        return None
    if resolver is not None:
        try:
            resolved = resolver(authored_speaker)
        except Exception:
            resolved = None
        if isinstance(resolved, str) and resolved.strip():
            return resolved.strip()
    # Non-NPC speakers (the protagonist token, a radio announcer, etc.) still
    # need a locale-stable identity so display copy never becomes a settings or
    # staging key.  The authored token is the backwards-compatible fallback.
    return authored_speaker


def _localized_story_copy(
    localization: object | None,
    key: str,
    source: str,
    *,
    scene_id: str,
) -> str:
    if localization is None:
        return source
    resolver = getattr(localization, "story", None)
    if not callable(resolver):
        return source
    try:
        value = resolver(key, source, scene_id=scene_id)
    except TypeError:
        # Compatibility with small embedding/test localization providers that
        # implemented the pre-scope ``story(key, source)`` protocol.
        try:
            value = resolver(key, source)
        except Exception:
            return source
    except Exception:
        return source
    return value if isinstance(value, str) else source


def _line_localization_key(scene: Scene, line: Line, index: int) -> str:
    return f"{scene.id}.line.{line.loc_id or index}"


def _localized_line_speaker(
    scene: Scene,
    line: Line,
    index: int,
    state: GameState,
    localization: object | None,
) -> str | None:
    if line.speaker is None:
        return None
    key = f"{_line_localization_key(scene, line, index)}.speaker"
    source = _localized_story_copy(
        localization,
        key,
        line.speaker,
        scene_id=scene.id,
    )
    return interpolate(source, state)


def _localized_line_captions(
    scene: Scene,
    line: Line,
    index: int,
    localization: object | None,
) -> list[SoundCaptionSpec]:
    """Resolve captions through their strict renderer-facing model."""
    prefix = _line_localization_key(scene, line, index)
    resolved: list[SoundCaptionSpec] = []
    for caption_index, caption in enumerate(line.sound_captions):
        text = _localized_story_copy(
            localization,
            f"{prefix}.sound_caption.{caption_index}",
            caption.text,
            scene_id=scene.id,
        )
        try:
            resolved.append(SoundCaptionSpec.model_validate({
                **caption.model_dump(mode="python"),
                "text": text,
            }))
        except Exception:
            # A catalog can be structurally sound yet exceed the narrower
            # caption presentation limit. Fail this one cue back to source.
            resolved.append(caption)
    return resolved


def _strip_dialogue_ops_without_execution(text: str) -> str:
    """Rebuild copy for a new locale without firing narrative handlers."""
    try:
        from ..plugins.registry import DIALOGUE_OP_REGISTRY
    except Exception:
        return text

    def _sub(match: re.Match) -> str:
        # Runtime leaves unknown directives visible for author diagnostics.
        # Known directives may mutate state, so a resume rebuild removes them
        # without invoking their handler or attempting to synthesize output.
        return "" if match.group(1) in DIALOGUE_OP_REGISTRY else match.group(0)

    return _DIALOGUE_OP_RE.sub(_sub, text)


def _validate_resume_sound_caption_provenance(
    raw: object,
    presentation: ScenePresentation,
    authored_scene: Scene,
    *,
    label: str,
    localization: object | None = None,
    state: GameState | None = None,
) -> None:
    line = presentation.line
    if line is None:
        return
    if not isinstance(raw, dict) or not isinstance(raw.get("line"), dict):
        raise ValueError(f"{label} sound captions require line data")
    raw_line = raw["line"]
    authored_index, authored_line = resolve_authored_line(
        authored_scene,
        loc_id=line.loc_id,
        line_index=line.line_index,
        label=f"{label} sound captions",
    )
    expected = [
        caption.model_dump(mode="json")
        for caption in _localized_line_captions(
            authored_scene,
            authored_line,
            authored_index,
            localization,
        )
    ]
    if "sound_captions" not in raw_line:
        # Early v1 resume envelopes predate this additive field. Hydrate the
        # decoded presentation from story state so accepting that legacy shape
        # cannot be used to suppress an authored accessibility cue.
        line.sound_captions = [
            SoundCaptionSpec.model_validate(caption)
            for caption in expected
        ]
        return
    if raw_line["sound_captions"] != expected:
        raise ValueError(f"{label} sound captions do not match the story line")


def resume_presentations_from_dict(
    resume: object,
    *,
    story_scene: str | None,
    story_line_index: int | None,
    story_line_count: int | None = None,
    story_lines: Sequence[Line] | None = None,
    authored_backgrounds: frozenset[str] | None = None,
    story_background: str | None = None,
    authored_scene: Scene | None = None,
    authored_assets: _ResumeAssetAuthority | None = None,
    authored_npcs: object = None,
    provenance_state: GameState | None = None,
    localization: object | None = None,
) -> tuple[ScenePresentation, ScenePresentation | None]:
    """Validate and decode a v1 dialogue-resume envelope.

    The envelope is presentation-only, but it must describe the exact scene and
    cursor already present in ``GameState``.  Keeping this check pure and shared
    prevents the app's pre-flight validation from drifting from the scene's
    eventual restore validation.
    """
    if not isinstance(resume, dict):
        raise ValueError("resume context must be a mapping")
    if (type(resume.get("version")) is not int
            or resume.get("version") != 1
            or resume.get("screen") != "dialogue"):
        raise ValueError("unsupported dialogue resume context")
    if not isinstance(story_scene, str) or not story_scene:
        raise ValueError("dialogue resume requires an active story scene")
    effective_locale, source_digest = _localization_identity(localization)
    saved_locale = resume.get("locale")
    if saved_locale is not None and saved_locale != effective_locale:
        raise ValueError("dialogue resume locale does not match the session")
    saved_digest = resume.get("locale_source_digest")
    if (
        saved_digest is not None
        and source_digest
        and saved_digest != source_digest
    ):
        raise ValueError(
            "dialogue resume source catalog does not match the session"
        )
    resume_index = resume.get("story_line_index")
    if (type(story_line_index) is not int
            or type(resume_index) is not int
            or resume.get("story_scene") != story_scene
            or resume_index != story_line_index):
        raise ValueError("dialogue resume does not match the story cursor")
    if story_line_count is not None and (
            type(story_line_count) is not int or story_line_count < 0):
        raise ValueError("dialogue resume requires a valid scene line count")

    has_cursor_loc = "story_cursor_loc_id" in resume
    has_cursor_end = "story_cursor_at_end" in resume
    if has_cursor_loc != has_cursor_end:
        raise ValueError("dialogue resume cursor evidence is incomplete")
    has_cursor_evidence = has_cursor_loc and has_cursor_end
    if has_cursor_evidence:
        cursor_loc_id = resume.get("story_cursor_loc_id")
        cursor_at_end = resume.get("story_cursor_at_end")
        if not isinstance(cursor_at_end, bool):
            raise ValueError("dialogue resume cursor boundary must be boolean")
        if cursor_loc_id is not None and (
            not isinstance(cursor_loc_id, str)
            or re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}",
                cursor_loc_id,
            ) is None
        ):
            raise ValueError("dialogue resume cursor loc_id is invalid")
        if story_line_count is not None:
            if cursor_at_end:
                if (
                    cursor_loc_id is not None
                    or story_line_index != story_line_count
                ):
                    raise ValueError(
                        "dialogue resume cursor boundary does not match the story"
                    )
            else:
                if not 0 <= story_line_index < story_line_count:
                    raise ValueError(
                        "dialogue resume cursor line does not match the story"
                    )
                if story_lines is not None:
                    authored_cursor = story_lines[story_line_index]
                    if authored_cursor.loc_id != cursor_loc_id:
                        raise ValueError(
                            "dialogue resume cursor loc_id does not match the story"
                        )

    envelope_scene = resume.get("scene_id")
    if envelope_scene != story_scene:
        raise ValueError("dialogue resume envelope has a mismatched scene")

    current = presentation_from_dict(resume["presentation"])
    if current.kind not in ("line", "choice"):
        raise ValueError("dialogue resume must contain a line or choice")

    last_data = resume.get("last_line")
    if last_data is None:
        last_line = None
    elif isinstance(last_data, dict):
        last_line = presentation_from_dict(last_data)
    else:
        raise ValueError("dialogue resume last_line must be a mapping or null")

    def _require_scene(pres: ScenePresentation, label: str) -> None:
        if pres.scene_id != story_scene:
            raise ValueError(f"{label} has a mismatched presentation scene")
        if pres.line is not None and pres.line.scene_id != story_scene:
            raise ValueError(f"{label} has a mismatched line scene")

    _require_scene(current, "presentation")
    if current.kind == "line":
        # _present_current advances the authoritative cursor immediately after
        # producing a line presentation.
        line = current.line
        if line is None:  # defensive; presentation_from_dict already rejects it
            raise ValueError("dialogue resume line data is missing")
        stable_cursor = bool(
            has_cursor_evidence
            and (
                resume.get("story_cursor_at_end") is True
                or resume.get("story_cursor_loc_id") is not None
            )
        )
        if stable_cursor and line.loc_id is not None:
            if (
                resume.get("story_cursor_at_end") is not True
                and line.loc_id == resume.get("story_cursor_loc_id")
            ):
                raise ValueError(
                    "dialogue resume presentation equals the next cursor line"
                )
        elif line.line_index + 1 != story_line_index:
            raise ValueError("dialogue resume line does not match the cursor")
        if story_line_count is not None and (
                line.line_index < 0
                or line.line_index >= story_line_count
                or line.total_lines != story_line_count):
            raise ValueError("dialogue resume line does not match the scene")
    elif (story_line_count is not None
          and story_line_index != story_line_count):
        # Choices become available only after _present_current has walked
        # every authored line (including conditionally-hidden trailing lines).
        # Without this boundary check, a forged/stale envelope could surface a
        # choice at cursor 0 and skip the scene's lines and their effects.
        raise ValueError("dialogue resume choice is not at the choice boundary")

    if last_line is not None:
        if last_line.kind != "line":
            raise ValueError("dialogue resume last_line must be a line")
        _require_scene(last_line, "last_line")
        if story_line_count is not None:
            line = last_line.line
            stable_last_line = bool(
                line is not None
                and line.loc_id is not None
                and has_cursor_evidence
                and (
                    resume.get("story_cursor_at_end") is True
                    or resume.get("story_cursor_loc_id") is not None
                )
            )
            if (line is None
                    or line.line_index < 0
                    or line.line_index >= story_line_count
                    or line.total_lines != story_line_count
                    or (
                        stable_last_line
                        and resume.get("story_cursor_at_end") is not True
                        and line.loc_id == resume.get("story_cursor_loc_id")
                    )
                    or (
                        not stable_last_line
                        and line.line_index >= story_line_index
                    )):
                raise ValueError(
                    "dialogue resume last_line does not match the scene"
                )

    if story_lines is not None:
        if story_line_count is not None and len(story_lines) != story_line_count:
            raise ValueError("dialogue resume story line evidence is inconsistent")
        caption_scene = authored_scene or Scene(
            id=story_scene,
            lines=list(story_lines),
        )
        _validate_resume_sound_caption_provenance(
            resume["presentation"],
            current,
            caption_scene,
            label="dialogue resume presentation",
            localization=localization,
            state=provenance_state,
        )
        if last_line is not None:
            _validate_resume_sound_caption_provenance(
                last_data,
                last_line,
                caption_scene,
                label="dialogue resume last_line",
                localization=localization,
                state=provenance_state,
            )

    if authored_backgrounds is not None:
        _validate_resume_background_provenance(
            resume,
            current,
            last_line,
            story_background=story_background,
            authored_backgrounds=authored_backgrounds,
        )
    if authored_scene is not None and authored_assets is not None:
        _validate_resume_authored_assets(
            resume,
            current,
            last_line,
            authored_scene=authored_scene,
            authority=authored_assets,
            authored_npcs=authored_npcs,
            state=provenance_state,
            localization=localization,
        )

    _validate_resume_presentation_state(resume)
    _validate_resume_weather(resume.get("weather"))
    _validate_resume_visual_timeline(
        resume,
        authored_backgrounds=authored_backgrounds,
    )
    _validate_resume_text_completion(resume, current, last_line)
    _validate_resume_nvl_transcript_tail(
        resume,
        current,
        last_line,
        authored_scene=authored_scene,
    )
    _validate_resume_wait_timeline(resume, current)
    _validate_resume_voice_playback(
        resume,
        current,
        story_lines,
        authored_scene=authored_scene,
    )

    return current, last_line


def resume_presentations_for_state(
    resume: object,
    state: GameState,
    *,
    authored_story: StoryGraph | None = None,
    authored_npcs: object = None,
    authored_map: MapSystem | None = None,
    localization: object | None = None,
) -> tuple[ScenePresentation, ScenePresentation | None]:
    """Validate/decode ``resume`` against a candidate state without mutation."""
    story = getattr(state, "story", None)
    story_scene = getattr(story, "current_scene", None)
    story_index = getattr(story, "current_line_index", None)
    scene = (
        story.get(story_scene)
        if story is not None and isinstance(story_scene, str) and story_scene
        else None
    )
    if scene is None:
        raise ValueError("dialogue resume requires a known active story scene")
    if authored_story is None:
        bridged_story = getattr(state, "meta", {}).get(
            "__pack_story_catalog__",
        )
        authored_story = (
            bridged_story
            if isinstance(bridged_story, StoryGraph)
            else story
        )
    authored_scene = (
        authored_story.get(story_scene)
        if isinstance(authored_story, StoryGraph)
        else None
    )
    if authored_scene is None:
        raise ValueError(
            "dialogue resume scene is not present in the authored story"
        )
    if authored_npcs is None:
        authored_npcs = getattr(state, "meta", {}).get("__npc_registry__")
    if authored_map is None:
        bridged_map = getattr(state, "meta", {}).get("__pack_map_catalog__")
        authored_map = (
            bridged_map
            if isinstance(bridged_map, MapSystem)
            else getattr(state, "map", None)
        )
    authority = _build_resume_asset_authority(
        authored_story,
        authored_npcs,
        authored_map,
    )
    _validate_effective_resume_audio(
        resume,
        state,
        authority,
    )
    return resume_presentations_from_dict(
        resume,
        story_scene=story_scene,
        story_line_index=story_index,
        story_line_count=len(authored_scene.lines),
        story_lines=authored_scene.lines,
        authored_backgrounds=authority.backgrounds,
        story_background=authored_scene.background,
        authored_scene=authored_scene,
        authored_assets=authority,
        authored_npcs=authored_npcs,
        provenance_state=state,
        localization=localization,
    )


def resume_matches_state(
    resume: object,
    state: GameState,
    *,
    authored_story: StoryGraph | None = None,
    authored_npcs: object = None,
    authored_map: MapSystem | None = None,
    localization: object | None = None,
) -> bool:
    """Fail-closed predicate for persistence/UI boundaries.

    Save JSON is user-writable. No malformed envelope — including pathological
    numeric values or a third-party pydantic/backend failure — may escape this
    boundary and leave the caller half-way through a state swap.
    """
    try:
        resume_presentations_for_state(
            resume,
            state,
            authored_story=authored_story,
            authored_npcs=authored_npcs,
            authored_map=authored_map,
            localization=localization,
        )
    except Exception:
        return False
    return True


def rebind_resume_choice_presentations(
    resume: object,
    state: GameState,
    *,
    localization: object | None = None,
    speaker_id_resolver: Callable[[str], str | None] | None = None,
) -> object:
    """Rebind saved presentation copy without replaying narrative work.

    Scene/line/choice identity comes from the rebound authored StoryGraph.
    Speaker names, captions, choice labels/cards and, when the locale changed,
    line copy are reconstructed for the active locale. Effects, conditions,
    dialogue-op handlers, hooks, read logs and history writes are never run.
    """
    if not isinstance(resume, dict):
        return resume
    updated = deepcopy(resume)
    presentation = updated.get("presentation")
    scene_id = updated.get("story_scene")
    scene = (
        state.story.get(scene_id)
        if isinstance(scene_id, str) and scene_id
        else None
    )
    if scene is None:
        return updated

    has_cursor_loc = "story_cursor_loc_id" in updated
    has_cursor_end = "story_cursor_at_end" in updated
    if has_cursor_loc != has_cursor_end:
        raise ValueError("dialogue resume cursor evidence is incomplete")
    if has_cursor_loc:
        saved_at_end = updated.get("story_cursor_at_end")
        saved_loc_id = updated.get("story_cursor_loc_id")
        if not isinstance(saved_at_end, bool):
            raise ValueError("dialogue resume cursor boundary must be boolean")
        if saved_loc_id is not None and (
            not isinstance(saved_loc_id, str)
            or re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}",
                saved_loc_id,
            ) is None
        ):
            raise ValueError("dialogue resume cursor loc_id is invalid")
        rebound_index = state.story.current_line_index
        if saved_at_end:
            if saved_loc_id is not None or rebound_index != len(scene.lines):
                raise ValueError(
                    "dialogue resume cursor boundary does not match the story"
                )
        else:
            saved_index = updated.get("story_line_index")
            current_index, current_line = resolve_authored_line(
                scene,
                loc_id=saved_loc_id,
                line_index=saved_index,
                label="dialogue resume cursor",
            )
            if current_index != rebound_index:
                raise ValueError(
                    "dialogue resume cursor does not match rebound story state"
                )
            saved_loc_id = current_line.loc_id
        updated["story_line_index"] = rebound_index
        updated["story_cursor_loc_id"] = saved_loc_id
        updated["story_cursor_at_end"] = saved_at_end

    effective_locale, source_digest = _localization_identity(localization)
    saved_locale = updated.get("locale")
    if not isinstance(saved_locale, str):
        source_locale = getattr(localization, "source", "")
        saved_locale = source_locale if isinstance(source_locale, str) else ""
    saved_digest = updated.get("locale_source_digest")
    presentation_changed = bool(localization is not None) and (
        saved_locale != effective_locale
        or (
            isinstance(saved_digest, str)
            and bool(saved_digest)
            and bool(source_digest)
            and saved_digest != source_digest
        )
    )
    if localization is not None:
        updated["locale"] = effective_locale
        updated["locale_source_digest"] = source_digest

    def _rebind_line(raw: object) -> None:
        if not isinstance(raw, dict) or raw.get("kind") != "line":
            return
        raw_line = raw.get("line")
        if not isinstance(raw_line, dict):
            return
        index, authored = resolve_authored_line(
            scene,
            loc_id=raw_line.get("loc_id"),
            line_index=raw_line.get("line_index"),
            label="dialogue resume presentation",
        )
        raw_line["line_index"] = index
        raw_line["total_lines"] = len(scene.lines)
        raw_line["loc_id"] = authored.loc_id
        existing_speaker_id = raw_line.get("speaker_id")
        if speaker_id_resolver is not None or not (
            isinstance(existing_speaker_id, str) and existing_speaker_id
        ):
            raw_line["speaker_id"] = _resolve_authored_speaker_id(
                speaker_id_resolver,
                authored.speaker,
            )
        raw_line["speaker"] = _localized_line_speaker(
            scene,
            authored,
            index,
            state,
            localization,
        )
        raw_line["sound_captions"] = [
            caption.model_dump(mode="json")
            for caption in _localized_line_captions(
                scene,
                authored,
                index,
                localization,
            )
        ]
        if presentation_changed and raw_line.get("is_llm_generated") is not True:
            text = _localized_story_copy(
                localization,
                _line_localization_key(scene, authored, index),
                authored.text,
                scene_id=scene.id,
            )
            text = interpolate(text, state)
            text = _strip_dialogue_ops_without_execution(text)
            raw_line["text"] = text
            raw_line["plain_text"] = strip_markup(text)
        raw["title"] = _localized_story_copy(
            localization,
            f"{scene.id}.title",
            scene.title,
            scene_id=scene.id,
        )

    _rebind_line(presentation)
    _rebind_line(updated.get("last_line"))

    if isinstance(presentation, dict):
        presentation["title"] = _localized_story_copy(
            localization,
            f"{scene.id}.title",
            scene.title,
            scene_id=scene.id,
        )
        if presentation.get("kind") == "choice":
            raw_choices = presentation.get("choices")
            authored_by_id = {choice.id: choice for choice in scene.choices}
            if isinstance(raw_choices, list):
                for row in raw_choices:
                    if not isinstance(row, dict):
                        continue
                    choice = authored_by_id.get(row.get("id"))
                    if choice is None:
                        continue
                    prefix = f"{scene.id}.choice.{choice.id}"
                    if presentation_changed:
                        choice_text = _localized_story_copy(
                            localization,
                            prefix,
                            choice.text,
                            scene_id=scene.id,
                        )
                        row["text"] = interpolate(choice_text, state)
                    card = _present_choice_card(
                        choice.card,
                        state,
                        key_prefix=prefix,
                        translate=(
                            lambda key, source: _localized_story_copy(
                                localization,
                                key,
                                source,
                                scene_id=scene.id,
                            )
                        ),
                    )
                    if card is None:
                        row.pop("card", None)
                    else:
                        row["card"] = card.model_dump(mode="json")

    if presentation_changed:
        # Text length, page boundaries and inline-wait positions belong to the
        # old language. Restart only the typewriter timeline; narrative state
        # and visual/audio authority remain at the exact saved cursor.
        updated["nvl_transcript"] = []
        updated["text_fully_revealed"] = False
        updated["text_page_fully_revealed"] = False
        for key in (
            "text_reveal_cursor",
            "text_plain_cursor",
            "text_page_index",
            "text_wait_timeline",
        ):
            updated.pop(key, None)
        if "voice_playback" in updated:
            updated["voice_playback"] = None
    return updated


def rebind_dialogue_history_localization(
    authored_story: StoryGraph,
    saved: DialogueHistory,
    state: GameState,
    *,
    localization: object | None = None,
    speaker_id_resolver: Callable[[str], str | None] | None = None,
) -> DialogueHistory:
    """Re-localize identity-bearing scrollback rows without replaying lines.

    Legacy rows without a line index remain untouched. New rows carry both an
    index and optional ``loc_id``; a changed/mismatched stable id fails that row
    closed to its saved copy instead of guessing after source edits.
    """
    rebound = saved.model_copy(deep=True)
    effective_locale, source_digest = _localization_identity(localization)
    source_locale = getattr(localization, "source", "")
    if not isinstance(source_locale, str):
        source_locale = ""

    for row in rebound.lines:
        scene_id = row.get("scene_id")
        if not isinstance(scene_id, str):
            continue
        scene = authored_story.get(scene_id)
        if scene is None:
            continue
        try:
            index, line = resolve_authored_line(
                scene,
                loc_id=row.get("loc_id"),
                line_index=row.get("line_index"),
                label="dialogue history line",
            )
        except (TypeError, ValueError):
            continue

        saved_locale = row.get("locale")
        if not isinstance(saved_locale, str):
            saved_locale = source_locale
        saved_digest = row.get("locale_source_digest")
        presentation_changed = bool(localization is not None) and (
            saved_locale != effective_locale
            or (
                isinstance(saved_digest, str)
                and bool(saved_digest)
                and bool(source_digest)
                and saved_digest != source_digest
            )
        )
        if presentation_changed and row.get("is_llm_generated") is not True:
            text = _localized_story_copy(
                localization,
                _line_localization_key(scene, line, index),
                line.text,
                scene_id=scene.id,
            )
            text = interpolate(text, state)
            text = _strip_dialogue_ops_without_execution(text)
            row["text"] = strip_markup(text)
        row["speaker"] = _localized_line_speaker(
            scene,
            line,
            index,
            state,
            localization,
        )
        # Saved history is user-writable and display names are translated.
        # Re-authorize the mechanical identity from the current story line when
        # the host exposes its NPC resolver. Resolver-less embedding callers
        # keep an existing stable field, or hydrate a legacy row from the
        # authored token.
        existing_speaker_id = row.get("speaker_id")
        if speaker_id_resolver is not None or not (
            isinstance(existing_speaker_id, str) and existing_speaker_id
        ):
            row["speaker_id"] = _resolve_authored_speaker_id(
                speaker_id_resolver,
                line.speaker,
            )
        row["sound_captions"] = [
            caption.model_dump(mode="json")
            for caption in _localized_line_captions(
                scene,
                line,
                index,
                localization,
            )
        ]
        row["loc_id"] = line.loc_id
        row["line_index"] = index
        row["locale"] = effective_locale
        row["locale_source_digest"] = source_digest
    return rebound


def _validate_resume_presentation_state(resume: dict[str, Any]) -> None:
    """Validate the UI-only portion of a v1 dialogue-resume envelope.

    ``presentation_from_dict`` protects the narrative presentation itself.
    These fields drive pygame/asset reconstruction after app pre-flight, so
    malformed values must be rejected before ``DialogueScene.enter`` is queued;
    otherwise a valid state load can fail asynchronously on the next frame.
    Missing fields remain accepted for backward-compatible v1 envelopes and
    simply use the scene's defaults.
    """
    def _optional_str(key: str) -> None:
        value = resume.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"dialogue resume {key} must be a string or null")

    for key in (
        "background",
        "cg",
        "current_bgm",
        "current_ambient",
        "locale",
        "locale_source_digest",
    ):
        _optional_str(key)
    locale = resume.get("locale")
    if isinstance(locale, str) and len(locale) > 64:
        raise ValueError("dialogue resume locale is too long")
    digest = resume.get("locale_source_digest")
    if isinstance(digest, str) and digest and (
        len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise ValueError("dialogue resume locale source digest is invalid")

    for key in (
        "background_overridden",
        "cg_overridden",
        "ui_hidden",
        "text_fully_revealed",
        "text_page_fully_revealed",
    ):
        if key in resume and not isinstance(resume[key], bool):
            raise ValueError(f"dialogue resume {key} must be boolean")

    speaking = resume.get("speaking_slot")
    if speaking not in (None, "left", "center", "right"):
        raise ValueError("dialogue resume speaking_slot is invalid")

    camera = resume.get("camera")
    if camera is not None:
        if not isinstance(camera, dict):
            raise ValueError("dialogue resume camera must be a mapping")
        _bounded_resume_number(
            camera.get("zoom", 1.0),
            "camera.zoom",
            minimum=_RESUME_MIN_CAMERA_ZOOM,
            maximum=_RESUME_MAX_CAMERA_ZOOM,
        )
        for axis in ("pan_x", "pan_y"):
            _bounded_resume_number(
                camera.get(axis, 0.0),
                f"camera.{axis}",
                minimum=-_RESUME_MAX_CAMERA_PAN,
                maximum=_RESUME_MAX_CAMERA_PAN,
            )

    tint = resume.get("tint")
    if tint is not None:
        if not isinstance(tint, dict):
            raise ValueError("dialogue resume tint must be a mapping")
        color = tint.get("color")
        if (not isinstance(color, list) or len(color) not in (3, 4)
                or any(type(channel) is not int for channel in color)):
            raise ValueError("dialogue resume tint.color is invalid")
        if any(channel < 0 or channel > 255 for channel in color):
            raise ValueError("dialogue resume tint.color is out of range")
        alpha = tint.get("alpha", 0)
        if type(alpha) is not int or not 0 <= alpha <= 255:
            raise ValueError("dialogue resume tint.alpha must be an integer")

    if "background_blur" in resume:
        _bounded_resume_number(
            resume["background_blur"],
            "background_blur",
            minimum=0.0,
            maximum=_RESUME_MAX_BACKGROUND_BLUR,
        )

    for cursor_key in ("text_reveal_cursor", "text_plain_cursor"):
        cursor = resume.get(cursor_key)
        if cursor is not None and (
                type(cursor) is not int
                or cursor < 0
                or cursor > _RESUME_MAX_TEXT_REVEAL_CURSOR):
            raise ValueError(
                f"dialogue resume {cursor_key} is outside the safe range"
            )
    page_index = resume.get("text_page_index")
    if page_index is not None and (
            type(page_index) is not int
            or page_index < 0
            or page_index > _RESUME_MAX_TEXT_REVEAL_CURSOR):
            raise ValueError(
                "dialogue resume text_page_index is outside the safe range"
            )

    transcript = resume.get("nvl_transcript")
    if transcript is not None:
        if (
            not isinstance(transcript, list)
            or len(transcript) > _RESUME_MAX_TRANSCRIPT_ENTRIES
        ):
            raise ValueError("dialogue resume nvl_transcript must be a list")
        transcript_chars = 0
        for entry in transcript:
            if not isinstance(entry, dict):
                raise ValueError(
                    "dialogue resume transcript entry must be a mapping"
                )
            speaker = entry.get("speaker")
            if speaker is not None and not isinstance(speaker, str):
                raise ValueError(
                    "dialogue resume transcript speaker must be a string"
                )
            speaker_id = entry.get("speaker_id")
            if speaker_id is not None and not isinstance(speaker_id, str):
                raise ValueError(
                    "dialogue resume transcript speaker_id must be a string"
                )
            text = entry.get("text")
            if not isinstance(text, str):
                raise ValueError(
                    "dialogue resume transcript text must be a string"
                )
            if (
                (isinstance(speaker, str) and len(speaker) > 4_096)
                or (isinstance(speaker_id, str) and len(speaker_id) > 4_096)
                or len(text) > _RESUME_MAX_LINE_TEXT_CHARS
            ):
                raise ValueError(
                    "dialogue resume transcript entry is too long"
                )
            transcript_chars += len(text) + (
                len(speaker) if isinstance(speaker, str) else 0
            ) + (
                len(speaker_id) if isinstance(speaker_id, str) else 0
            )
            if transcript_chars > _RESUME_MAX_TRANSCRIPT_CHARS:
                raise ValueError(
                    "dialogue resume transcript is too large"
                )

    slots = resume.get("slots")
    if slots is None:
        return
    if not isinstance(slots, dict):
        raise ValueError("dialogue resume slots must be a mapping")
    known_slots = {"left", "center", "right"}
    if any(slot not in known_slots for slot in slots):
        raise ValueError("dialogue resume contains an unknown portrait slot")
    for slot in known_slots:
        raw = slots.get(slot)
        if raw is None:
            continue
        if not isinstance(raw, dict):
            raise ValueError(f"dialogue resume slot {slot} must be a mapping")
        if "spec" in raw:
            if set(raw) != {"spec"}:
                raise ValueError(
                    f"dialogue resume slot {slot} spec descriptor is invalid"
                )
            if not isinstance(raw["spec"], dict):
                raise ValueError(
                    f"dialogue resume slot {slot} spec must be a mapping"
                )
            try:
                spec = PortraitSpec.model_validate(raw["spec"])
            except Exception as exc:
                raise ValueError(
                    f"dialogue resume slot {slot} has an invalid spec"
                ) from exc
            _validate_resume_portrait_spec(spec)
            if spec.slot != slot:
                raise ValueError(
                    f"dialogue resume slot {slot} contains a mismatched spec"
                )
            continue
        if raw.get("kind") != "legacy":
            raise ValueError(
                f"dialogue resume slot {slot} has an invalid descriptor"
            )
        if not set(raw).issubset({
            "kind", "portrait", "speaker", "speaker_id", "expression", "slot",
        }):
            raise ValueError(
                f"dialogue resume slot {slot} legacy descriptor is invalid"
            )
        for key in ("portrait", "speaker", "speaker_id", "expression"):
            value = raw.get(key)
            if value is not None and not isinstance(value, str):
                raise ValueError(
                    f"dialogue resume slot {slot} {key} must be a string"
                )
        saved_slot = raw.get("slot")
        if saved_slot is not None and saved_slot != slot:
            raise ValueError(
                f"dialogue resume slot {slot} has a mismatched slot id"
            )


def _validate_resume_wait_timeline(
    resume: dict[str, Any],
    current: ScenePresentation,
) -> None:
    """Validate additive inline-wait progress against rendered line markup."""
    if "text_wait_timeline" not in resume:
        return
    timeline = resume.get("text_wait_timeline")
    if timeline is None:
        return
    if current.kind != "line" or current.line is None:
        raise ValueError("dialogue resume choice cannot carry a wait timeline")
    if (
        not isinstance(timeline, dict)
        or set(timeline) != {"consumed_plain_positions", "active"}
    ):
        raise ValueError("dialogue resume text_wait_timeline is invalid")

    document = parse(current.line.text)
    plain_text = document.plain_text()
    waits = {
        position: duration
        for position, duration in document.waits().items()
        if position < len(plain_text)
    }
    plain_cursor = resume.get("text_plain_cursor")
    if (
        type(plain_cursor) is not int
        or plain_cursor < 0
        or plain_cursor > len(plain_text)
    ):
        raise ValueError(
            "dialogue resume wait timeline requires a valid plain cursor"
        )

    consumed = timeline.get("consumed_plain_positions")
    if (
        not isinstance(consumed, list)
        or len(consumed) > _RESUME_MAX_TEXT_WAIT_COUNT
        or any(type(position) is not int for position in consumed)
    ):
        raise ValueError("dialogue resume consumed waits are invalid")
    if consumed != sorted(set(consumed)):
        raise ValueError("dialogue resume consumed waits are not canonical")
    known_positions = set(waits)
    if any(position not in known_positions for position in consumed):
        raise ValueError("dialogue resume contains an unknown consumed wait")
    if any(position > plain_cursor for position in consumed):
        raise ValueError("dialogue resume consumed wait is ahead of the cursor")
    if any(
        position not in consumed
        for position in known_positions
        if position < plain_cursor
    ):
        raise ValueError("dialogue resume omits an elapsed wait")
    if (
        resume.get("text_fully_revealed") is True
        and set(consumed) != known_positions
    ):
        raise ValueError("dialogue resume completed text omits a consumed wait")

    active = timeline.get("active")
    if active is None:
        return
    if (
        not isinstance(active, dict)
        or set(active) != {"plain_position", "remaining_seconds"}
    ):
        raise ValueError("dialogue resume active wait is invalid")
    plain_position = active.get("plain_position")
    if type(plain_position) is not int:
        raise ValueError("dialogue resume active wait position is invalid")
    remaining = _bounded_resume_number(
        active.get("remaining_seconds"),
        "active wait remaining_seconds",
        minimum=0.0,
        maximum=_RESUME_MAX_TEXT_WAIT_SECONDS,
    )
    authored_duration = waits.get(plain_position)
    if (
        remaining <= 0.0
        or authored_duration is None
        or not math.isfinite(authored_duration)
        or remaining > authored_duration
        or plain_position != plain_cursor
        or plain_position not in consumed
        or resume.get("text_fully_revealed") is True
        or resume.get("text_page_fully_revealed") is True
    ):
        raise ValueError("dialogue resume active wait is inconsistent")


def _validate_resume_voice_playback(
    resume: dict[str, Any],
    current: ScenePresentation,
    story_lines: Sequence[Line] | None,
    *,
    authored_scene: Scene | None = None,
) -> None:
    """Validate an additive logical voice checkpoint for the current line.

    The checkpoint deliberately contains no asset path. Its audio source is the
    authoritative authored line at the saved cursor, not a user-writable path
    copied into persistence metadata. A present ``null`` means this save knew
    that no owned clip should resume; a missing key retains legacy v1's
    restart-from-zero compatibility.
    """
    if "voice_playback" not in resume:
        return
    checkpoint = resume.get("voice_playback")
    if checkpoint is None:
        return
    line = current.line if current.kind == "line" else None
    if line is None or not isinstance(line.voice, str) or not line.voice:
        raise ValueError(
            "dialogue resume voice playback requires a voiced line"
        )
    if authored_scene is not None:
        _, authored_line = resolve_authored_line(
            authored_scene,
            loc_id=line.loc_id,
            line_index=line.line_index,
            label="dialogue resume voice playback",
        )
        if authored_line.voice != line.voice:
            raise ValueError(
                "dialogue resume voice playback does not match the story line"
            )
    elif story_lines is not None:
        index = line.line_index
        if not 0 <= index < len(story_lines):
            raise ValueError(
                "dialogue resume voice playback has an invalid line index"
            )
        if story_lines[index].voice != line.voice:
            raise ValueError(
                "dialogue resume voice playback does not match the story line"
            )
    if not isinstance(checkpoint, dict):
        raise ValueError("dialogue resume voice_playback is invalid")
    version = checkpoint.get("version")
    common = {
        "version",
        "state",
        "position_seconds",
        "duration_seconds",
        "fallback",
    }
    required = (
        common
        if version == 1
        else common | {"pcm_sha256", "mixer_format"}
    )
    if set(checkpoint) != required:
        raise ValueError("dialogue resume voice_playback is invalid")
    if type(version) is not int or version not in (1, 2):
        raise ValueError(
            "dialogue resume voice_playback version is unsupported"
        )
    state = checkpoint.get("state")
    if state not in ("playing", "completed", "stopped"):
        raise ValueError("dialogue resume voice_playback state is invalid")
    if checkpoint.get("fallback") != "silence":
        raise ValueError("dialogue resume voice_playback fallback is invalid")
    if version == 2:
        pcm_sha256 = checkpoint.get("pcm_sha256")
        if (
            not isinstance(pcm_sha256, str)
            or len(pcm_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in pcm_sha256
            )
        ):
            raise ValueError(
                "dialogue resume voice_playback PCM identity is invalid"
            )
        mixer_format = checkpoint.get("mixer_format")
        if (
            not isinstance(mixer_format, list)
            or len(mixer_format) != 3
            or any(type(value) is not int for value in mixer_format)
            or not 1 <= mixer_format[0] <= 768_000
            or abs(mixer_format[1]) not in (8, 16, 24, 32)
            or not 1 <= mixer_format[2] <= 32
        ):
            raise ValueError(
                "dialogue resume voice_playback mixer format is invalid"
            )
    duration = _bounded_resume_number(
        checkpoint.get("duration_seconds"),
        "voice_playback.duration_seconds",
        minimum=0.000001,
        maximum=_RESUME_MAX_VOICE_SECONDS,
    )
    position = _bounded_resume_number(
        checkpoint.get("position_seconds"),
        "voice_playback.position_seconds",
        minimum=0.0,
        maximum=_RESUME_MAX_VOICE_SECONDS,
    )
    if position > duration:
        raise ValueError(
            "dialogue resume voice_playback position exceeds duration"
        )
    if state == "playing" and position >= duration:
        raise ValueError(
            "dialogue resume playing voice has already completed"
        )
    if state == "completed" and position != duration:
        raise ValueError(
            "dialogue resume completed voice has an incomplete position"
        )


def _validate_resume_json_value(
    value: object,
    *,
    label: str,
    depth: int = 0,
) -> None:
    """Bound plugin-facing JSON before it can allocate renderer resources."""
    if depth > 4:
        raise ValueError(f"dialogue resume {label} is nested too deeply")
    if value is None or isinstance(value, (bool, str)):
        if isinstance(value, str) and len(value) > 4096:
            raise ValueError(f"dialogue resume {label} string is too long")
        return
    if type(value) in (int, float):
        _bounded_resume_number(
            value,
            label,
            minimum=-1_000_000.0,
            maximum=1_000_000.0,
        )
        return
    if isinstance(value, list):
        if len(value) > 256:
            raise ValueError(f"dialogue resume {label} list is too large")
        for index, item in enumerate(value):
            _validate_resume_json_value(
                item,
                label=f"{label}[{index}]",
                depth=depth + 1,
            )
        return
    if isinstance(value, dict):
        if len(value) > 64:
            raise ValueError(f"dialogue resume {label} mapping is too large")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                raise ValueError(f"dialogue resume {label} key is invalid")
            _validate_resume_json_value(
                item,
                label=f"{label}.{key}",
                depth=depth + 1,
            )
        return
    raise ValueError(f"dialogue resume {label} is not JSON-native")


def _validate_resume_weather(weather: object) -> None:
    """Validate a weather reconstruction descriptor before backend spawn."""
    if weather is None:
        return
    if not isinstance(weather, dict) or set(weather) != {
        "fx", "backend", "params", "fade",
    }:
        raise ValueError("dialogue resume weather directive is invalid")
    if weather.get("fx") != "set_weather":
        raise ValueError("dialogue resume weather effect is invalid")
    backend = weather.get("backend")
    if not isinstance(backend, str) or not backend or len(backend) > 128:
        raise ValueError("dialogue resume weather backend is invalid")
    params = weather.get("params")
    if not isinstance(params, dict):
        raise ValueError("dialogue resume weather params must be a mapping")
    _validate_resume_json_value(params, label="weather.params")
    count = params.get("count")
    if count is not None and (
        type(count) is not int
        or count < 0
        or count > _RESUME_MAX_WEATHER_PARTICLES
    ):
        raise ValueError("dialogue resume weather particle count is unsafe")
    alpha = params.get("alpha")
    if alpha is not None and (
        type(alpha) is not int or not 0 <= alpha <= 255
    ):
        raise ValueError("dialogue resume weather alpha is invalid")
    _bounded_resume_number(
        weather.get("fade"),
        "weather.fade",
        minimum=0.0,
        maximum=_RESUME_MAX_VISUAL_TWEEN_SECONDS,
    )


def _iter_story_effects(story: StoryGraph):
    for scene in story.scenes.values():
        yield from scene.on_end
        for line in scene.lines:
            yield from line.effects
        for choice in scene.choices:
            yield from choice.effects


def _npc_for(npcs: object, identity: str | None):
    if not identity or npcs is None:
        return None
    by_name = getattr(npcs, "by_name", None)
    if callable(by_name):
        try:
            npc = by_name(identity)
        except Exception:
            npc = None
        if npc is not None:
            return npc
    get = getattr(npcs, "get", None)
    if callable(get):
        try:
            return get(identity)
        except Exception:
            return None
    return None


def _apply_authored_npc_portrait_backend(
    spec: PortraitSpec,
    npcs: object,
) -> PortraitSpec:
    """Mirror DialogueScene's deterministic NPC backend enrichment."""
    if spec.backend and spec.backend != "static":
        return spec
    npc = _npc_for(npcs, spec.character)
    if npc is None:
        return spec
    backend = getattr(npc, "portrait_backend", "static") or "static"
    if backend == "static":
        return spec
    still = spec.image
    if not still:
        portrait_for = getattr(npc, "portrait_for", None)
        if callable(portrait_for):
            try:
                still = portrait_for(spec.expression)
            except Exception:
                still = None
    npc_args = dict(getattr(npc, "portrait_backend_args", {}) or {})
    return spec.model_copy(
        update={
            "backend": backend,
            "backend_args": {**npc_args, **(spec.backend_args or {})},
            "image": still,
        },
        deep=True,
    )


def _build_resume_asset_authority(
    story: StoryGraph,
    npcs: object = None,
    authored_map: MapSystem | None = None,
) -> _ResumeAssetAuthority:
    """Build decoder/plugin authority exclusively from current pack data."""
    backgrounds: set[str] = set()
    cgs: set[str] = set()
    music: set[str] = set()
    ambient: set[str] = set()
    portrait_paths: set[str] = set()
    portrait_specs: list[PortraitSpec] = []
    weather: list[dict[str, Any]] = []

    def _add(refs: set[str], ref: object) -> None:
        if isinstance(ref, str) and ref:
            refs.add(ref)

    def _add_spec(spec: PortraitSpec) -> None:
        if not any(spec == existing for existing in portrait_specs):
            portrait_specs.append(spec.model_copy(deep=True))

    for scene in story.scenes.values():
        _add(backgrounds, scene.background)
        _add(cgs, scene.cg)
        _add(music, scene.bgm)
        _add(ambient, scene.ambient)
        for line in scene.lines:
            _add(cgs, line.cg)
            _add(music, line.bgm)
            _add(ambient, line.ambient)
            if isinstance(line.portrait, str):
                _add(portrait_paths, line.portrait)
            elif isinstance(line.portrait, PortraitSpec):
                raw_spec = line.portrait.model_copy(deep=True)
                _add_spec(raw_spec)
                spec = _apply_authored_npc_portrait_backend(raw_spec, npcs)
                if (
                    spec.slot == "center"
                    and line.portrait_pos in ("left", "right")
                ):
                    spec = spec.model_copy(
                        update={"slot": line.portrait_pos},
                        deep=True,
                    )
                _add_spec(spec)
            for raw_spec in line.portraits:
                _add_spec(raw_spec)
                _add_spec(
                    _apply_authored_npc_portrait_backend(raw_spec, npcs)
                )

            npc = _npc_for(npcs, line.speaker)
            backend = (
                getattr(npc, "portrait_backend", "static") or "static"
                if npc is not None
                else "static"
            )
            if not isinstance(line.portrait, PortraitSpec) and backend != "static":
                still = (
                    line.portrait
                    if isinstance(line.portrait, str)
                    else getattr(npc, "portrait_for", lambda _value: None)(
                        line.expression
                    )
                )
                spec = PortraitSpec(
                    character=npc.id,
                    expression=line.expression or "default",
                    backend=backend,
                    backend_args=dict(
                        getattr(npc, "portrait_backend_args", {}) or {}
                    ),
                    image=still,
                    slot=(
                        line.portrait_pos
                        if line.portrait_pos in ("left", "right")
                        else "center"
                    ),
                )
                _add_spec(spec)

    all_npcs = getattr(npcs, "all", None)
    if callable(all_npcs):
        try:
            npc_values = all_npcs()
        except Exception:
            npc_values = ()
        for npc in npc_values:
            _add(portrait_paths, getattr(npc, "portrait", None))
            portrait_set = getattr(npc, "portrait_set", None)
            if isinstance(portrait_set, dict):
                for path in portrait_set.values():
                    _add(portrait_paths, path)

    if isinstance(authored_map, MapSystem):
        for location in authored_map.locations.values():
            _add(ambient, location.ambient)

    for effect in _iter_story_effects(story):
        kind = getattr(effect, "kind", None)
        target = getattr(effect, "target", None)
        if kind == "set_background":
            _add(backgrounds, target)
        elif kind == "show_cg" and isinstance(target, str):
            _add(cgs, canonical_cg_path(target))
        elif kind == "set_weather" and isinstance(target, str) and target:
            try:
                params = (
                    dict(effect.value)
                    if isinstance(effect.value, dict)
                    else {}
                )
                fade = float(params.pop("fade", 0.0) or 0.0)
                directive = json.loads(json.dumps(
                    {
                        "fx": "set_weather",
                        "backend": target,
                        "params": params,
                        "fade": fade,
                    },
                    ensure_ascii=False,
                    allow_nan=False,
                ))
            except (ArithmeticError, TypeError, ValueError):
                continue
            if directive not in weather:
                weather.append(directive)

    return _ResumeAssetAuthority(
        backgrounds=frozenset(backgrounds),
        cgs=frozenset(cgs),
        music=frozenset(music),
        ambient=frozenset(ambient),
        portrait_paths=frozenset(portrait_paths),
        portrait_specs=tuple(portrait_specs),
        weather=tuple(weather),
    )


def _authored_background_refs(story: StoryGraph) -> frozenset[str]:
    """Compatibility helper for background-only callers/tests."""
    return _build_resume_asset_authority(story).backgrounds


def _validate_resume_background_provenance(
    resume: dict[str, Any],
    current: ScenePresentation,
    last_line: ScenePresentation | None,
    *,
    story_background: str | None,
    authored_backgrounds: frozenset[str],
) -> None:
    """Bind every restorable background path to freshly-authored pack data."""
    saved_background = resume.get("background")
    if (
        saved_background is not None
        and saved_background not in authored_backgrounds
    ):
        raise ValueError("dialogue resume background is not authored")

    for presentation, label in (
        (current, "presentation"),
        (last_line, "last_line"),
    ):
        if presentation is None:
            continue
        if presentation.background != story_background:
            raise ValueError(
                f"dialogue resume {label} background does not match the story"
            )
        if (
            presentation.line is not None
            and presentation.line.background != story_background
        ):
            raise ValueError(
                f"dialogue resume {label} line background does not match the story"
            )


def _audio_ref_is_authored(
    value: object,
    authored: frozenset[str],
) -> bool:
    return (
        value is None
        or _is_audio_silence_ref(value if isinstance(value, str) else None)
        or (isinstance(value, str) and value in authored)
    )


def _validate_effective_resume_audio(
    resume: object,
    state: GameState,
    authority: _ResumeAssetAuthority,
) -> None:
    """Cover legacy envelopes that derive audio refs from saved GameState."""
    if not isinstance(resume, dict):
        return
    for key, allowed in (
        ("current_bgm", authority.music),
        ("current_ambient", authority.ambient),
    ):
        value = (
            resume.get(key)
            if key in resume
            else getattr(state, "meta", {}).get(key)
        )
        if not _audio_ref_is_authored(value, allowed):
            raise ValueError(
                f"dialogue resume {key} is not authored by the current pack"
            )


def _validate_resume_authored_assets(
    resume: dict[str, Any],
    current: ScenePresentation,
    last_line: ScenePresentation | None,
    *,
    authored_scene: Scene,
    authority: _ResumeAssetAuthority,
    authored_npcs: object = None,
    state: GameState | None,
    localization: object | None = None,
) -> None:
    """Bind every decoder/plugin-facing resume field to current pack data."""

    def _line_assets(
        presentation: ScenePresentation,
        label: str,
    ) -> None:
        line = presentation.line
        if line is None:
            return
        authored_index, authored = resolve_authored_line(
            authored_scene,
            loc_id=line.loc_id,
            line_index=line.line_index,
            label=f"dialogue resume {label}",
        )
        expected_cg = authored.cg or authored_scene.cg
        expected_speaker = (
            _localized_line_speaker(
                authored_scene,
                authored,
                authored_index,
                state,
                localization,
            )
            if state is not None
            else authored.speaker
        )
        if authored_npcs is not None:
            expected_npc = _npc_for(authored_npcs, authored.speaker)
            expected_speaker_id = (
                getattr(expected_npc, "id", None)
                if expected_npc is not None
                else authored.speaker
            )
            if line.speaker_id is None:
                # Early v1 envelopes predate the additive stable identity.
                # Hydrate from authored NPC data; do not reject a valid old
                # save or fall back to localized display copy later.
                line.speaker_id = expected_speaker_id
            elif line.speaker_id not in {
                expected_speaker_id,
                authored.speaker,
            }:
                raise ValueError(
                    f"dialogue resume {label} speaker_id does not match "
                    "the authored line"
                )
            else:
                # A resolver-less producer may have persisted the authored
                # source token. Canonicalize it now that the NPC registry is
                # available to the restoring host.
                line.speaker_id = expected_speaker_id
        exact_fields = (
            ("speaker", line.speaker, expected_speaker),
            ("portrait", line.portrait, authored.portrait),
            ("portraits", line.portraits, authored.portraits),
            ("portrait_pos", line.portrait_pos, authored.portrait_pos),
            ("expression", line.expression, authored.expression),
            ("cg", line.cg, expected_cg),
            ("background", line.background, authored_scene.background),
            ("sfx", line.sfx, authored.sfx),
            # Voice provenance is unconditional. A forged save cannot delete
            # voice_playback and thereby turn an arbitrary path into a legacy
            # restart-from-zero request.
            ("voice", line.voice, authored.voice),
        )
        for field_name, actual, expected in exact_fields:
            if actual != expected:
                raise ValueError(
                    f"dialogue resume {label} {field_name} "
                    "does not match the authored line"
                )
        if not _audio_ref_is_authored(line.bgm, authority.music):
            raise ValueError(f"dialogue resume {label} BGM is not authored")
        if not _audio_ref_is_authored(line.ambient, authority.ambient):
            raise ValueError(
                f"dialogue resume {label} ambient is not authored"
            )
        if authored.bgm is not None and line.bgm != authored.bgm:
            raise ValueError(
                f"dialogue resume {label} BGM does not match the authored line"
            )
        if authored.ambient is not None and line.ambient != authored.ambient:
            raise ValueError(
                f"dialogue resume {label} ambient does not match the authored line"
            )
        if "current_bgm" in resume and line.bgm != resume.get("current_bgm"):
            raise ValueError(
                f"dialogue resume {label} BGM mismatches its audio state"
            )
        if (
            "current_ambient" in resume
            and line.ambient != resume.get("current_ambient")
        ):
            raise ValueError(
                f"dialogue resume {label} ambient mismatches its audio state"
            )

    _line_assets(current, "presentation")
    if last_line is not None:
        _line_assets(last_line, "last_line")

    cg = resume.get("cg")
    if cg is not None and cg not in authority.cgs:
        raise ValueError("dialogue resume CG is not authored by the current pack")
    target_line = current.line or (
        last_line.line if last_line is not None else None
    )
    if (
        "cg" in resume
        and resume.get("cg_overridden") is not True
        and target_line is not None
        and cg != target_line.cg
    ):
        raise ValueError("dialogue resume CG does not match the authored line")
    if not _audio_ref_is_authored(
        resume.get("current_bgm"),
        authority.music,
    ):
        raise ValueError("dialogue resume current_bgm is not authored")
    if not _audio_ref_is_authored(
        resume.get("current_ambient"),
        authority.ambient,
    ):
        raise ValueError("dialogue resume current_ambient is not authored")

    weather = resume.get("weather")
    if weather is not None and not any(
        weather == authored for authored in authority.weather
    ):
        raise ValueError(
            "dialogue resume weather is not an authored directive"
        )

    slots = resume.get("slots")
    if isinstance(slots, dict):
        for slot, raw in slots.items():
            if not isinstance(raw, dict):
                continue
            if isinstance(raw.get("spec"), dict):
                try:
                    spec = PortraitSpec.model_validate(raw["spec"])
                except Exception as exc:
                    raise ValueError(
                        f"dialogue resume slot {slot} spec is invalid"
                    ) from exc
                if not any(
                    spec == authored for authored in authority.portrait_specs
                ):
                    raise ValueError(
                        f"dialogue resume slot {slot} spec is not authored"
                    )
            elif raw.get("kind") == "legacy":
                portrait = raw.get("portrait")
                if (
                    portrait is not None
                    and portrait not in authority.portrait_paths
                ):
                    raise ValueError(
                        f"dialogue resume slot {slot} portrait is not authored"
                    )

    if current.kind == "choice":
        authored_choices = {
            choice.id: choice for choice in authored_scene.choices
        }
        ids = [choice.id for choice in current.choices]
        if len(ids) != len(set(ids)) or any(
            choice_id not in authored_choices for choice_id in ids
        ):
            raise ValueError(
                "dialogue resume choices contain an unknown or duplicate id"
            )
        if len(ids) > len(authored_choices):
            raise ValueError("dialogue resume has too many choices")
        visible_id_set = set(ids)
        authored_subsequence = [
            choice.id
            for choice in authored_scene.choices
            if choice.id in visible_id_set
        ]
        if ids != authored_subsequence:
            raise ValueError(
                "dialogue resume choices do not preserve authored order"
            )
        for choice in current.choices:
            card = choice.card
            if card is None or card.image is None:
                continue
            authored_card = authored_choices[choice.id].card
            if authored_card is None or card.image != authored_card.image:
                raise ValueError(
                    "dialogue resume choice card image is not authored"
                )


def _validate_resume_visual_timeline(
    resume: dict[str, Any],
    *,
    authored_backgrounds: frozenset[str] | None = None,
) -> None:
    """Validate reconstructible tween clocks in an additive v1 sidecar."""
    if "visual_timeline" not in resume:
        return
    timeline = resume.get("visual_timeline")
    base_required = {
        "version",
        "background_fade",
        "camera",
        "tint",
        "background_blur",
        "weather",
        "unreconstructible_fallback",
    }
    if not isinstance(timeline, dict):
        raise ValueError("dialogue resume visual_timeline is invalid")
    version = timeline.get("version")
    if type(version) is not int or version not in (1, 2):
        raise ValueError("dialogue resume visual_timeline version is unsupported")
    required = (
        base_required
        if version == 1
        else base_required | {"portrait_backend_phase"}
    )
    if set(timeline) != required:
        raise ValueError("dialogue resume visual_timeline is invalid")
    if (
        version == 2
        and timeline.get("portrait_backend_phase") != "restart"
    ):
        raise ValueError(
            "dialogue resume portrait backend phase fallback is invalid"
        )
    if timeline.get("unreconstructible_fallback") != "settle":
        raise ValueError("dialogue resume visual fallback is invalid")

    def _timing(raw: dict[str, Any], label: str) -> tuple[float, float]:
        duration = _bounded_resume_number(
            raw.get("duration_seconds"),
            f"{label}.duration_seconds",
            minimum=0.000001,
            maximum=_RESUME_MAX_VISUAL_TWEEN_SECONDS,
        )
        elapsed = _bounded_resume_number(
            raw.get("elapsed_seconds"),
            f"{label}.elapsed_seconds",
            minimum=0.0,
            maximum=_RESUME_MAX_VISUAL_TWEEN_SECONDS,
        )
        if elapsed >= duration:
            raise ValueError(f"dialogue resume {label} is already complete")
        return elapsed, duration

    def _easing(raw: dict[str, Any], label: str) -> None:
        if raw.get("easing") not in EASING_NAMES:
            raise ValueError(f"dialogue resume {label} easing is invalid")

    background = timeline.get("background_fade")
    if background is not None:
        if not isinstance(background, dict) or set(background) != {
            "from", "to", "elapsed_seconds", "duration_seconds", "easing",
        }:
            raise ValueError("dialogue resume background fade is invalid")
        old_path = background.get("from")
        new_path = background.get("to")
        if old_path is not None and not isinstance(old_path, str):
            raise ValueError("dialogue resume background fade source is invalid")
        if not isinstance(new_path, str) or new_path != resume.get("background"):
            raise ValueError("dialogue resume background fade target mismatches")
        if authored_backgrounds is not None and (
            (old_path is not None and old_path not in authored_backgrounds)
            or new_path not in authored_backgrounds
        ):
            raise ValueError(
                "dialogue resume background fade path is not authored"
            )
        _timing(background, "background_fade")
        _easing(background, "background_fade")

    camera = timeline.get("camera")
    if camera is not None:
        if not isinstance(camera, dict) or set(camera) != {
            "from", "to", "elapsed_seconds", "duration_seconds", "easing",
        }:
            raise ValueError("dialogue resume camera timeline is invalid")
        for endpoint_name in ("from", "to"):
            endpoint = camera.get(endpoint_name)
            if not isinstance(endpoint, list) or len(endpoint) != 3:
                raise ValueError("dialogue resume camera endpoint is invalid")
            _bounded_resume_number(
                endpoint[0],
                f"camera.{endpoint_name}.zoom",
                minimum=_RESUME_MIN_CAMERA_ZOOM,
                maximum=_RESUME_MAX_CAMERA_ZOOM,
            )
            for axis, value in zip(("pan_x", "pan_y"), endpoint[1:]):
                _bounded_resume_number(
                    value,
                    f"camera.{endpoint_name}.{axis}",
                    minimum=-_RESUME_MAX_CAMERA_PAN,
                    maximum=_RESUME_MAX_CAMERA_PAN,
                )
        _timing(camera, "camera")
        _easing(camera, "camera")

    tint = timeline.get("tint")
    if tint is not None:
        if not isinstance(tint, dict) or set(tint) != {
            "color", "max_alpha", "elapsed_seconds", "duration_seconds", "easing",
        }:
            raise ValueError("dialogue resume tint timeline is invalid")
        color = tint.get("color")
        if (
            not isinstance(color, list)
            or len(color) != 3
            or any(type(channel) is not int or not 0 <= channel <= 255
                   for channel in color)
        ):
            raise ValueError("dialogue resume tint timeline color is invalid")
        max_alpha = tint.get("max_alpha")
        if type(max_alpha) is not int or not 0 <= max_alpha <= 255:
            raise ValueError("dialogue resume tint target alpha is invalid")
        _timing(tint, "tint")
        _easing(tint, "tint")

    blur = timeline.get("background_blur")
    if blur is not None:
        if not isinstance(blur, dict) or set(blur) != {
            "from", "to", "elapsed_seconds", "duration_seconds",
        }:
            raise ValueError("dialogue resume background blur timeline is invalid")
        for endpoint in ("from", "to"):
            _bounded_resume_number(
                blur.get(endpoint),
                f"background_blur.{endpoint}",
                minimum=0.0,
                maximum=_RESUME_MAX_BACKGROUND_BLUR,
            )
        _timing(blur, "background_blur")

    weather = timeline.get("weather")
    if weather is not None:
        if not isinstance(weather, dict) or set(weather) != {
            "lifecycle",
            "base_alpha",
            "current_alpha",
            "elapsed_seconds",
            "duration_seconds",
            "backend_phase",
        }:
            raise ValueError("dialogue resume weather timeline is invalid")
        if resume.get("weather") is None:
            raise ValueError("dialogue resume weather timeline has no backend")
        if weather.get("lifecycle") not in ("active", "clearing"):
            raise ValueError("dialogue resume weather lifecycle is invalid")
        if weather.get("backend_phase") != "restart":
            raise ValueError("dialogue resume weather phase fallback is invalid")
        for alpha_name in ("base_alpha", "current_alpha"):
            alpha = weather.get(alpha_name)
            if type(alpha) is not int or not 0 <= alpha <= 255:
                raise ValueError(
                    f"dialogue resume weather {alpha_name} is invalid"
                )
        _timing(weather, "weather")


def _validate_resume_text_completion(
    resume: dict[str, Any],
    current: ScenePresentation,
    last_line: ScenePresentation | None,
) -> None:
    """Tie whole-line completion flags to the authored-text cursor."""
    plain_cursor = resume.get("text_plain_cursor")
    if plain_cursor is None:
        return
    target = current if current.kind == "line" else last_line
    line = target.line if target is not None else None
    if line is None:
        return
    plain_length = len(parse(line.text).plain_text())
    if type(plain_cursor) is not int or not 0 <= plain_cursor <= plain_length:
        raise ValueError("dialogue resume plain cursor exceeds the line")
    if "text_fully_revealed" in resume:
        expected = plain_cursor == plain_length
        if resume.get("text_fully_revealed") is not expected:
            raise ValueError(
                "dialogue resume completion flag mismatches the plain cursor"
            )


def _validate_resume_nvl_transcript_tail(
    resume: dict[str, Any],
    current: ScenePresentation,
    last_line: ScenePresentation | None,
    *,
    authored_scene: Scene | None = None,
) -> None:
    """Tie the NVL body restored by a timeline to its saved presentation."""
    transcript = resume.get("nvl_transcript")
    if transcript is None or transcript == []:
        return
    if not isinstance(transcript, list):
        # The generic presentation validator reports the public shape error.
        return
    target = current if current.kind == "line" else last_line
    line = target.line if target is not None else None
    if line is None:
        raise ValueError(
            "dialogue resume NVL transcript has no presentation line"
        )
    tail = transcript[-1]
    if not isinstance(tail, dict):
        raise ValueError(
            "dialogue resume NVL transcript tail mismatches the presentation"
        )
    expected = {"speaker": line.speaker, "text": line.text}
    if any(tail.get(key) != value for key, value in expected.items()):
        raise ValueError(
            "dialogue resume NVL transcript tail mismatches the presentation"
        )
    saved_speaker_id = tail.get("speaker_id")
    allowed_speaker_ids = {line.speaker_id}
    if authored_scene is not None:
        _, authored_line = resolve_authored_line(
            authored_scene,
            loc_id=line.loc_id,
            line_index=line.line_index,
            label="dialogue resume NVL transcript",
        )
        allowed_speaker_ids.add(authored_line.speaker)
    if (
        saved_speaker_id is not None
        and saved_speaker_id not in allowed_speaker_ids
    ):
        raise ValueError(
            "dialogue resume NVL transcript speaker_id mismatches "
            "the presentation"
        )


class DialogueEngine:
    def __init__(
        self,
        state: GameState,
        *,
        llm_provider=None,
        speaker_id_resolver: Callable[[str], str | None] | None = None,
        localization=None,
    ):
        self.state = state
        self.llm_provider = llm_provider   # optional callable for LLM lines
        self.speaker_id_resolver = speaker_id_resolver
        self.localization = localization

    @staticmethod
    def _line_key(scene: Scene, line: Line, idx: int) -> str:
        token = line.loc_id or str(idx)
        return f"{scene.id}.line.{token}"

    def _story_text(self, key: str, source: str) -> str:
        localization = self.localization
        if localization is None:
            return source
        try:
            translated = localization.story(key, source)
        except Exception:
            return source
        return translated if isinstance(translated, str) else source

    def _scene_title(self, scene: Scene) -> str:
        source = scene.title or scene.id
        return self._story_text(f"{scene.id}.title", source)

    def _speaker_id(self, *candidates: str | None) -> str | None:
        """Resolve authored speaker identity, never localized display copy.

        Callers pass the canonical line token first.  Keeping this helper
        tolerant of more than one candidate preserves the embedding API, but
        runtime localization deliberately supplies only ``Line.speaker``: a
        translation can equal another NPC's source name and must never acquire
        that character's mechanical identity.
        """
        for candidate in candidates:
            resolved = _resolve_authored_speaker_id(
                self.speaker_id_resolver,
                candidate,
            )
            if resolved is not None:
                return resolved
        return None

    def _replay_hud_snapshot(self, scene: Scene | None) -> dict[str, Any]:
        """Capture player-visible chapter/date labels for recollection.

        The regular dialogue HUD derives these labels from mutable game state.
        Re-evaluating them years later would put a first-year memory under the
        current fourth-year date, so the evaluated labels travel with each
        archived presentation just like interpolated dialogue text.
        """
        chapter_title = None
        chapter_id = self.state.current_chapter
        manifest = self.state.meta.get("__chapters__")
        try:
            if chapter_id and manifest is not None:
                chapter = next(
                    (item for item in manifest.chapters if item.id == chapter_id),
                    None,
                )
                if chapter is not None:
                    chapter_title = chapter.title or chapter_id
        except Exception:
            chapter_title = None

        date_label = None
        hide_date = False
        tags = tuple(getattr(scene, "tags", ()) or ()) if scene else ()
        for tag in tags:
            if tag == "hud-hide-date":
                hide_date = True
                break
            if tag.startswith("hud-date:"):
                authored = tag[len("hud-date:"):].strip()
                if authored:
                    date_label = authored
                    break
        if not hide_date and date_label is None:
            try:
                date_label = self.state.time.label()
            except Exception:
                date_label = None
        return {
            "chapter_title": chapter_title,
            "date_label": None if hide_date else date_label,
            "time_of_day": getattr(
                getattr(self.state.time, "time_of_day", None),
                "value",
                None,
            ),
        }

    def _replay_fx_since(self, start: int) -> list[dict[str, Any]]:
        from ..core.read_log import REPLAY_VISUAL_FX_KINDS
        from ..plugins.builtin_effects import VISUAL_FX_QUEUE

        queue = self.state.meta.get(VISUAL_FX_QUEUE)
        if not isinstance(queue, list) or len(queue) <= start:
            return []
        return [
            deepcopy(directive)
            for directive in queue[start:]
            if isinstance(directive, dict)
            and directive.get("fx") in REPLAY_VISUAL_FX_KINDS
        ]

    def _visual_fx_queue_length(self) -> int:
        from ..plugins.builtin_effects import VISUAL_FX_QUEUE

        queue = self.state.meta.get(VISUAL_FX_QUEUE)
        return len(queue) if isinstance(queue, list) else 0

    # ---------- public API ----------------------------------------------------

    def current_scene(self) -> Scene | None:
        sid = self.state.story.current_scene
        if sid is None:
            return None
        return self.state.story.get(sid)

    def start_scene(self, scene_id: str) -> ScenePresentation:
        inherited_fx = self.state.meta.pop(REPLAY_PENDING_FX_META_KEY, [])
        scene = self.state.story.start(scene_id)
        # Recollection is a presentation archive, not a second evaluation of
        # the authored graph against some future state.  Start a trace before
        # the first line is evaluated so conditional visibility, interpolated
        # copy, LLM output, and the original choice boundary can be preserved.
        # Completed traces are immutable; ReadLog deliberately ignores a
        # natural revisit after the first completed viewing.
        self.state.read_log.begin_replay_record(
            scene.id,
            self._scene_title(scene),
        )
        self.state.events.record(
            kind="scene", title=self._scene_title(scene),
            location=scene.location, data={"scene": scene_id},
        )
        if scene.bgm:
            self.state.meta["current_bgm"] = scene.bgm
            # Scene-level BGM is the common case (most packs set bgm per scene,
            # not per line), so unlock it here too — otherwise the music room
            # would miss almost every track. Line-level cg/bgm unlock in
            # _present_line complements this.
            if not _is_audio_silence_ref(scene.bgm):
                self.state.music_room.unlock(scene.bgm)
        # Scene-level ambient bed carries until another scene changes it. When a
        # scene sets none, fall back to its location's default room-tone, so every
        # scene at a place inherits that place's ambient with no per-scene authoring
        # (the 演出 audit's "silence is the loudest tell in a daily" fix).
        if scene.ambient:
            self.state.meta["current_ambient"] = scene.ambient
        elif scene.location:
            loc = self.state.map.locations.get(scene.location)
            if loc is not None and loc.ambient:
                self.state.meta["current_ambient"] = loc.ambient
        if scene.cg:
            self.state.cg_gallery.unlock(scene.cg)
        presentation = self._present_current()
        if (
            isinstance(inherited_fx, list)
            and inherited_fx
            and presentation.scene_id == scene.id
        ):
            self.state.read_log.prepend_replay_visual_fx(
                scene.id,
                inherited_fx,
            )
        return presentation

    def next_line(self) -> ScenePresentation:
        """Render the next line, or surface choices / end.

        ``_present_current`` already advances the index after rendering a
        line, so this method must NOT pre-advance — doing so would skip
        every second line. (Earlier versions had this bug; the smoke
        scripts hid it by calling next() with a high count and breaking
        on end.)
        """
        scene = self.current_scene()
        if scene is None:
            return ScenePresentation(kind="end")
        return self._present_current()

    def choose(self, choice_id: str) -> ScenePresentation:
        with _dialogue_transaction(self.state):
            return self._choose_in_transaction(choice_id)

    def _choose_in_transaction(self, choice_id: str) -> ScenePresentation:
        scene = self.current_scene()
        if scene is None:
            return ScenePresentation(kind="end")
        choice = next((c for c in scene.choices if c.id == choice_id), None)
        if choice is None:
            return self._present_current()
        if not self._choice_available(choice):
            return self._present_current()
        # Record the selected path before effects or a chained start can move
        # the story cursor to another scene.  An effect failure leaves the
        # trace incomplete, which the replay library never exposes.
        self.state.read_log.record_replay_choice(scene.id, choice.id)
        fx_start = self._visual_fx_queue_length()
        from ..plugins import fire_event
        from ..plugins.context import HookEvent
        fire_event(self.state, HookEvent.DIALOGUE_CHOICE_MADE,
                   scene_id=scene.id, choice_id=choice.id)
        # Apply effects.
        effects_out = self.state.apply_all(choice.effects)
        # Interpolate the recorded choice text so the event log shows the
        # resolved player-name / resource values at decision time.
        choice_text = interpolate(
            self._story_text(
                f"{scene.id}.choice.{choice.id}",
                choice.text,
            ),
            self.state,
        )
        self.state.events.record(
            kind="choice", title=choice_text,
            location=scene.location,
            data={"scene": scene.id, "choice": choice.id, "effects": effects_out},
        )
        # Transition to next scene (the current scene is "done": a choice
        # that hands off to a sequel scene counts as completing this one).
        if choice.next_scene:
            self.state.story.mark_played(scene.id)
            self.state.read_log.mark_scene_done(scene.id)
            inherited_fx = self._replay_fx_since(fx_start)
            if inherited_fx:
                self.state.meta[REPLAY_PENDING_FX_META_KEY] = inherited_fx
            return self.start_scene(choice.next_scene)
        # Otherwise end this scene.
        return self._end_current_scene()

    # ---------- skip / auto-play helpers --------------------------------------

    def is_current_read(self) -> bool:
        """Return True if the line the engine is *about to present* is already
        in the read log.  Useful for skip-mode to decide whether to pause."""
        scene = self.current_scene()
        if scene is None:
            return False
        idx = self.state.story.current_line_index
        # Peek past any invisible lines (mirrors _present_current logic).
        while idx < len(scene.lines) and not self._line_visible(scene.lines[idx]):
            idx += 1
        if idx >= len(scene.lines):
            # At the choice/end boundary — treat as unread so UI pauses.
            return False
        return self.state.read_log.is_read(
            scene.id,
            idx,
            loc_id=scene.lines[idx].loc_id,
        )

    def skip_to_next_unread(self) -> "ScenePresentation | None":
        """Auto-advance through already-read lines; stop at the first unread
        line or any choice point (choices always require player input).

        Returns the first unread ScenePresentation, or None when the scene ends
        without finding unread content.
        """
        while True:
            scene = self.current_scene()
            if scene is None:
                return None
            idx = self.state.story.current_line_index
            # Skip invisible lines same as _present_current.
            while idx < len(scene.lines) and not self._line_visible(scene.lines[idx]):
                idx += 1
            self.state.story.current_line_index = idx

            # Reached end-of-lines -> choices or end (always stop skipping).
            if idx >= len(scene.lines):
                pres = self.next_line()
                return None if pres.kind == "end" else pres

            # Unread line: stop and present it.
            if not self.state.read_log.is_read(
                scene.id,
                idx,
                loc_id=scene.lines[idx].loc_id,
            ):
                return self.next_line()

            # Already-read line: advance past it without pausing.
            # We call next_line() to apply effects and push history, then
            # continue the loop to check the *following* line.
            pres = self.next_line()
            # Stop at any non-line result.  end -> None; choice/transition -> return.
            if pres.kind == "end":
                return None
            if pres.kind in ("choice", "transition"):
                return pres

    def skip_all(self) -> "ScenePresentation | None":
        """Skip-all mode: race through *every* remaining line (read or not),
        stopping only at a choice point or the end of the scene.

        This is the unread-inclusive sibling of :meth:`skip_to_next_unread`.
        Like that method it advances line-by-line through the engine (so each
        line's effects fire and history/read-log update) but never pauses on
        an unread line — only a choice or transition halts the skip. Returning
        the *final* presentation lets the scene render once at the stopping
        point rather than re-rendering every skipped line (matching the
        existing skip helper's contract).

        Returns the choice / transition presentation reached, or ``None`` when
        the scene ends without one.
        """
        while True:
            scene = self.current_scene()
            if scene is None:
                return None
            pres = self.next_line()
            if pres.kind == "end":
                return None
            if pres.kind in ("choice", "transition"):
                return pres
            # kind == "line": keep skipping.

    # ---------- internals -----------------------------------------------------

    def _choice_available(self, choice: Choice) -> bool:
        if not self.state.evaluate_all(choice.requires):
            return False
        if not self.state.evaluate_none(choice.forbids):
            return False
        return True

    def _choice_lock_reason(self, choice: Choice) -> str:
        """A concise human-readable phrase explaining why ``choice`` is locked.

        Collects the choice's *failing* gates — a ``requires`` condition that is
        unmet, or a ``forbids`` condition that is currently hit — and renders
        them via :mod:`condition_text`. Returns "" for an available choice.
        Isolated: any failure degrades to an empty reason (the choice still
        renders, just without the why), never raising on the present path.
        """
        try:
            failed_requires = [c for c in choice.requires
                               if not self.state.evaluate(c)]
            hit_forbids = [c for c in choice.forbids
                           if self.state.evaluate(c)]
            if not failed_requires and not hit_forbids:
                return ""
            from .condition_text import summarize_lock
            return summarize_lock(failed_requires, hit_forbids, self.state)
        except Exception:
            return ""

    def _line_visible(self, line: Line) -> bool:
        return self.state.evaluate_all(line.requires)

    def _present_line(self, scene: Scene, line: Line, idx: int) -> LinePresentation:
        line_key = self._line_key(scene, line, idx)
        text = self._story_text(line_key, line.text)
        is_llm = False
        if line.llm_speaker and self.llm_provider is not None:
            try:
                text = self.llm_provider(
                    speaker=line.speaker or "narrator",
                    directive=line.llm_directive or "",
                    state=self.state,
                    scene=scene,
                ) or line.text
                is_llm = True
            except Exception as e:
                text = line.text + f"\n[LLM 失敗: {e}]"
        # Apply {token} interpolation after LLM resolution so LLM-generated
        # text can also embed state variables.
        text = interpolate(text, self.state)
        # Parse + fire any [[op:arg]] dialogue directives. ``text`` is now the
        # raw markup string the UI renders; ``plain`` strips rich-text tags for
        # headless / history so no [tag] leaks into clean-text consumers.
        text = _apply_dialogue_ops(text, self.state)
        plain = strip_markup(text)
        effects_out: list[dict[str, Any]] = []
        if line.effects:
            effects_out = self.state.apply_all(line.effects)
        if line.bgm:
            self.state.meta["current_bgm"] = line.bgm
        if line.ambient:
            self.state.meta["current_ambient"] = line.ambient
        # Mark as read *before* pushing to history (order doesn't matter for
        # the ReadLog, but it's consistent with "user has now seen this").
        self.state.read_log.mark_line(scene.id, idx, loc_id=line.loc_id)
        # Auto-unlock the CG / BGM this line shows so the gallery and music
        # room can offer them later. Scene-level cg / bgm unlock similarly in
        # start_scene(); the viewer scenes themselves stay read-only.
        if line.cg:
            self.state.cg_gallery.unlock(line.cg)
        if line.bgm and not _is_audio_silence_ref(line.bgm):
            self.state.music_room.unlock(line.bgm)
        # Interpolate speaker too so lines spoken by the player can use
        # `speaker: "{player_name}"` and render the chosen name.
        speaker_rendered = _localized_line_speaker(
            scene,
            line,
            idx,
            self.state,
            self.localization,
        )
        # Resolve identity from authored content, before localized display copy
        # enters the presentation.  A translated name is never authority.
        speaker_id = self._speaker_id(line.speaker)
        localized_captions = _localized_line_captions(
            scene,
            line,
            idx,
            self.localization,
        )
        # Push to dialogue history so the scrollback overlay can show it
        # later. History stores clean text (no rich-text markup) — the
        # scrollback isn't a styled renderer.
        history_locale, history_source_digest = _localization_identity(
            self.localization,
        )
        self.state.dialogue_history.push(
            speaker=speaker_rendered, text=plain,
            scene_id=scene.id,
            line_index=idx,
            loc_id=line.loc_id,
            locale=history_locale,
            locale_source_digest=history_source_digest,
            is_llm_generated=is_llm,
            portrait=line.portrait,
            voice=line.voice,
            sound_captions=[
                caption.model_dump(mode="json")
                for caption in localized_captions
            ],
            speaker_id=speaker_id,
        )
        return LinePresentation(
            speaker=speaker_rendered,
            text=text,
            speaker_id=speaker_id,
            plain_text=plain,
            portrait=line.portrait,
            portraits=list(line.portraits),
            portrait_pos=line.portrait_pos,
            expression=line.expression,
            cg=line.cg or scene.cg,
            background=scene.background,
            bgm=line.bgm or self.state.meta.get("current_bgm"),
            ambient=line.ambient or self.state.meta.get("current_ambient"),
            sfx=line.sfx,
            voice=line.voice,
            sound_captions=localized_captions,
            scene_id=scene.id,
            line_index=idx,
            loc_id=line.loc_id,
            total_lines=len(scene.lines),
            effects_applied=effects_out,
            is_llm_generated=is_llm,
        )

    def _present_current(self) -> ScenePresentation:
        # DialogueScene normally consumes this queue immediately after every
        # returned presentation.  Headless clients do not, so remember the
        # starting length and archive only directives produced by this step.
        fx_start = self._visual_fx_queue_length()
        with _dialogue_transaction(self.state):
            presentation = self._present_current_in_transaction()
            scene_id = presentation.scene_id
            if scene_id and presentation.kind in {"line", "choice"}:
                payload = replay_presentation_to_dict(presentation)
                replay_fx = self._replay_fx_since(fx_start)
                if replay_fx:
                    payload[REPLAY_FX_PAYLOAD_KEY] = replay_fx
                payload[REPLAY_HUD_PAYLOAD_KEY] = self._replay_hud_snapshot(
                    self.state.story.get(scene_id)
                )
                self.state.read_log.record_replay_presentation(
                    scene_id,
                    payload,
                )
            return presentation

    def _present_current_in_transaction(self) -> ScenePresentation:
        scene = self.current_scene()
        if scene is None:
            return ScenePresentation(kind="end")
        idx = self.state.story.current_line_index

        # Skip any non-visible lines.
        while idx < len(scene.lines) and not self._line_visible(scene.lines[idx]):
            idx += 1
        self.state.story.current_line_index = idx

        if idx < len(scene.lines):
            # We're showing a line.
            line = scene.lines[idx]
            # Fire dialogue.before_line so plugins can preview / mutate state
            # (e.g. trigger a cinematic effect on a specific line).
            from ..plugins import fire_event
            from ..plugins.context import HookEvent
            fire_event(self.state, HookEvent.DIALOGUE_BEFORE_LINE,
                       scene_id=scene.id, line_index=idx, line=line)
            pres = self._present_line(scene, line, idx)
            fire_event(self.state, HookEvent.DIALOGUE_AFTER_LINE,
                       scene_id=scene.id, line_index=idx, line=pres)
            # advance pointer past this line so the next call goes to the next
            self.state.story.current_line_index = idx + 1
            return ScenePresentation(
                kind="line", line=pres,
                scene_id=scene.id, title=self._scene_title(scene),
                background=scene.background,
            )

        # We've consumed all lines — surface choices or end.
        #
        # Present visible choices in their AUTHORED order — do NOT hoist the
        # available ones above the locked ones. Reordering by availability is an
        # active UX hazard at a pivotal decision: if the only currently-enabled
        # option is a give-up / bad-ending branch (the heroine routes still
        # locked behind affection), availability-first would float that worst
        # option to the top of the menu. Keeping the author's order means a
        # locked heroine option renders in place — greyed, with its lock reason
        # — and the give-up option stays where the author put it (typically
        # last). ``hidden_if_locked`` choices are still dropped entirely; only a
        # legitimately-visible locked choice reaches the list (carrying a
        # concise reason so the UI shows *why*, not a silent ghost button).
        options: list[ChoiceOption] = []
        for c in scene.choices:
            enabled = self._choice_available(c)
            if not enabled and c.hidden_if_locked:
                continue
            options.append(ChoiceOption(
                id=c.id,
                text=interpolate(
                    self._story_text(
                        f"{scene.id}.choice.{c.id}",
                        c.text,
                    ),
                    self.state,
                ),
                enabled=enabled,
                reason="" if enabled else self._choice_lock_reason(c),
                card=_present_choice_card(
                    c.card,
                    self.state,
                    key_prefix=f"{scene.id}.choice.{c.id}",
                    translate=self._story_text,
                ),
            ))
        if options:
            return ScenePresentation(
                kind="choice", choices=options,
                scene_id=scene.id, title=self._scene_title(scene),
                background=scene.background,
            )
        return self._end_current_scene()

    def _end_current_scene(self) -> ScenePresentation:
        scene = self.current_scene()
        if scene is None:
            return ScenePresentation(kind="end")
        # apply on_end effects, mark played, surface next transition (if any).
        fx_start = self._visual_fx_queue_length()
        end_results = self.state.apply_all(scene.on_end)
        self.state.story.mark_played(scene.id)
        self.state.read_log.mark_scene_done(scene.id)
        self.state.story.current_scene = None
        self.state.story.current_line_index = 0
        # Look for a play_scene effect to suggest a transition.
        next_scene = None
        for r in end_results:
            if r.get("kind") == "play_scene":
                next_scene = r.get("scene")
        inherited_fx = self._replay_fx_since(fx_start)
        if next_scene and inherited_fx:
            self.state.meta[REPLAY_PENDING_FX_META_KEY] = inherited_fx
        return ScenePresentation(
            kind="transition" if next_scene else "end",
            next_scene=next_scene,
            scene_id=scene.id,
            title=self._scene_title(scene),
        )
