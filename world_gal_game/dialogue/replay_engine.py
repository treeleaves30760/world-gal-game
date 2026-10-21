"""Presentation-trace playback for faithful, read-only scene recollection.

This intentionally implements the small ``DialogueEngine`` surface consumed
by :class:`~world_gal_game.scenes.dialogue_scene.DialogueScene`, but it never
evaluates conditions, applies effects, invokes an LLM, or follows authored
transitions.  It redraws the presentations captured during the first completed
viewing of one scene and stops at that scene's recorded boundary.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from ..core.game_state import GameState
from ..core.read_log import (
    REPLAY_VISUAL_FX_KINDS,
    ReadLog,
    ReplayArchiveEntry,
)
from ..core.story_graph import (
    StoryGraph,
    authored_story_audio_refs,
    resolve_authored_line,
)
from ..plugins.builtin_effects import VISUAL_FX_QUEUE
from .dialogue_engine import (
    REPLAY_FX_PAYLOAD_KEY,
    REPLAY_HUD_META_KEY,
    REPLAY_HUD_PAYLOAD_KEY,
    ScenePresentation,
    presentation_from_dict,
)


_AUDIO_SILENCE_REFS = frozenset({"none", "silence", "-"})


def _audio_ref_is_authored(
    value: object,
    authored: frozenset[str],
) -> bool:
    return (
        value is None
        or (
            isinstance(value, str)
            and (
                value.strip().lower() in _AUDIO_SILENCE_REFS
                or value in authored
            )
        )
    )


def _builtin_replay_directives(effects: Iterable[object]) -> list[dict[str, Any]]:
    """Evaluate only current engine-owned visual handlers on a throwaway state.

    This derives the exact canonical queue dictionaries that authored builtin
    effects produce without dispatching arbitrary plugin code or mutating the
    live/sandbox save. Plugin-generated replay FX have no generic provenance
    protocol yet and therefore fail closed at persistence/replay boundaries.
    """
    from ..plugins.registry import EFFECT_REGISTRY

    scratch = GameState()
    for effect in effects:
        kind = getattr(effect, "kind", None)
        if kind not in REPLAY_VISUAL_FX_KINDS:
            continue
        entry = EFFECT_REGISTRY.get(kind)
        if entry is None or entry.plugin_id != "builtin":
            continue
        try:
            entry.fn(scratch, effect)
        except Exception:
            # Runtime effect dispatch is isolated too. An authored directive
            # that the current handler cannot produce grants no replay authority.
            continue
    queue = scratch.meta.get(VISUAL_FX_QUEUE)
    if not isinstance(queue, list):
        return []
    return [
        deepcopy(directive)
        for directive in queue
        if isinstance(directive, dict)
        and directive.get("fx") in REPLAY_VISUAL_FX_KINDS
    ]


def _possible_on_end_targets(scene: object) -> set[str]:
    targets: set[str] = set()
    for effect in getattr(scene, "on_end", ()) or ():
        kind = getattr(effect, "kind", None)
        target = getattr(effect, "target", None)
        if kind == "play_scene" and isinstance(target, str) and target:
            targets.add(target)
        elif kind == "play_scene_branch":
            if isinstance(target, str) and target:
                targets.add(target)
            value = getattr(effect, "value", None)
            if isinstance(value, list):
                for case in value:
                    candidate = case.get("target") if isinstance(case, dict) else None
                    if isinstance(candidate, str) and candidate:
                        targets.add(candidate)
    return targets


def _inbound_replay_fx_variants(
    story: StoryGraph,
    scene_id: str,
) -> list[list[dict[str, Any]]]:
    """Return exact authored FX sequences that can precede a scene's first step."""
    variants: list[list[dict[str, Any]]] = [[]]

    def _add(effects: Iterable[object]) -> None:
        candidate = _builtin_replay_directives(effects)
        if candidate not in variants:
            variants.append(candidate)

    for source in story.scenes.values():
        for choice in source.choices:
            if choice.next_scene == scene_id:
                _add(choice.effects)
        if scene_id in _possible_on_end_targets(source):
            _add(source.on_end)
    return variants


def _authored_replay_fx_variants(
    story: StoryGraph,
    scene_id: str,
    payload: dict[str, Any],
    position: int,
) -> list[list[dict[str, Any]]]:
    scene = story.get(scene_id)
    if scene is None:
        return [[]]
    local: list[dict[str, Any]] = []
    if payload.get("kind") == "line":
        raw_line = payload.get("line")
        line_index = raw_line.get("line_index") if isinstance(raw_line, dict) else None
        loc_id = raw_line.get("loc_id") if isinstance(raw_line, dict) else None
        try:
            _, authored_line = resolve_authored_line(
                scene,
                loc_id=loc_id,
                line_index=line_index,
                label="replay trace line",
            )
        except (TypeError, ValueError):
            return []
        local = _builtin_replay_directives(authored_line.effects)
    if position != 0:
        return [local]
    return [
        [*inbound, *local]
        for inbound in _inbound_replay_fx_variants(story, scene_id)
    ]


def _replay_fx_are_authored(
    story: StoryGraph,
    scene_id: str,
    payload: dict[str, Any],
    position: int,
) -> bool:
    raw = payload.get(REPLAY_FX_PAYLOAD_KEY)
    if raw is None:
        return True
    if not isinstance(raw, list):
        return False
    return any(
        raw == expected
        for expected in _authored_replay_fx_variants(
            story,
            scene_id,
            payload,
            position,
        )
    )


def rebind_authored_replay_visual_fx(
    saved: ReadLog,
    authored_story: StoryGraph,
) -> ReadLog:
    """Rebind replay line identity and strip unauthorised transient FX.

    Stable ``loc_id`` is authoritative while historical ``line_index`` and
    ``total_lines`` remain untouched for display/audit. Legacy positional rows
    are hydrated once against the current catalogue. A deleted/ambiguous stable
    line invalidates the entry; a removed scene remains inert for compatibility.
    The evaluated dialogue copy remains useful when an authored visual effect
    disappears, so only that unauthorised transient is stripped.
    """
    rebound = saved.model_copy(deep=True)
    for scene_id, entry in list(rebound.replay_archive.items()):
        scene = authored_story.get(scene_id)
        if scene is None:
            continue
        valid_identity = True
        for position, payload in enumerate(entry.presentations):
            if payload.get("kind") == "line":
                raw_line = payload.get("line")
                if not isinstance(raw_line, dict):
                    valid_identity = False
                    break
                try:
                    _, source = resolve_authored_line(
                        scene,
                        loc_id=raw_line.get("loc_id"),
                        line_index=raw_line.get("line_index"),
                        label="replay archive line",
                    )
                except (TypeError, ValueError):
                    valid_identity = False
                    break
                # Additive hydration gives legacy clear-data archives stable
                # identity without rewriting their historical display index.
                raw_line["loc_id"] = source.loc_id
            if not _replay_fx_are_authored(
                authored_story,
                scene_id,
                payload,
                position,
            ):
                payload.pop(REPLAY_FX_PAYLOAD_KEY, None)
        if not valid_identity:
            rebound.replay_archive.pop(scene_id, None)
    return rebound


class PresentationTraceDialogueEngine:
    """Dialogue-compatible cursor over one validated replay archive entry."""

    def __init__(
        self,
        state: GameState,
        entry: ReplayArchiveEntry,
        *,
        authored_story: StoryGraph | None = None,
        authored_map: object = None,
        authored_npcs: object = None,
    ) -> None:
        if not entry.complete or not entry.presentations:
            raise ValueError("replay trace must be complete and non-empty")
        self.state = state
        self.llm_provider = None
        self.speaker_id_resolver = None
        self.scene_id = entry.scene_id
        self.title = entry.title
        self._payloads = [deepcopy(payload) for payload in entry.presentations]
        self._steps = [
            presentation_from_dict(payload)
            for payload in self._payloads
        ]
        if any(
            step.kind not in {"line", "choice"}
            or step.scene_id not in (None, self.scene_id)
            for step in self._steps
        ):
            raise ValueError("replay trace contains a foreign presentation")

        choice_steps = [step for step in self._steps if step.kind == "choice"]
        if len(choice_steps) != len(entry.selected_choices):
            raise ValueError("replay trace choice evidence is incomplete")
        self._selected_choices = list(entry.selected_choices)
        for step, selected_id in zip(choice_steps, self._selected_choices):
            selected = next(
                (choice for choice in step.choices if choice.id == selected_id),
                None,
            )
            if selected is None or not selected.enabled:
                raise ValueError("replay trace selected choice is invalid")
        story_authority = authored_story or state.story
        self._authored_story = story_authority
        self._validate_authored_provenance(
            story_authority,
            authored_map=authored_map if authored_map is not None else state.map,
            authored_npcs=authored_npcs,
        )

        self._cursor = 0
        self._choice_cursor = 0
        self._current: ScenePresentation | None = None
        self._waiting_choice = False
        self._started = False
        self._done = False

    def _validate_authored_provenance(
        self,
        story: StoryGraph,
        *,
        authored_map: object = None,
        authored_npcs: object = None,
    ) -> None:
        """Reject save-edited presentation structure not owned by the pack."""
        scene = story.get(self.scene_id)
        if scene is None:
            raise ValueError("replay trace scene is no longer authored")
        authored_music, authored_ambient = authored_story_audio_refs(
            story,
            authored_map,
        )
        authored_choice_ids = [choice.id for choice in scene.choices]
        unknown_audio = object()
        effective_bgm: str | None | object = (
            scene.bgm if scene.bgm is not None else unknown_audio
        )
        effective_ambient: str | None | object = (
            scene.ambient if scene.ambient is not None else unknown_audio
        )
        for position, step in enumerate(self._steps):
            payload = self._payloads[position]
            if not _replay_fx_are_authored(
                story,
                self.scene_id,
                payload,
                position,
            ):
                raise ValueError("replay trace visual effect is not authored")
            if step.background != scene.background:
                raise ValueError("replay trace background is not authored")
            if step.kind == "choice":
                ids = [choice.id for choice in step.choices]
                if len(ids) != len(set(ids)) or any(
                    choice_id not in authored_choice_ids for choice_id in ids
                ):
                    raise ValueError("replay trace contains an unauthored choice")
                positions = [authored_choice_ids.index(choice_id) for choice_id in ids]
                if positions != sorted(positions):
                    raise ValueError("replay trace choice order is not authored")
                authored = {choice.id: choice for choice in scene.choices}
                for choice in step.choices:
                    source_card = authored[choice.id].card
                    if (choice.card is None) != (source_card is None):
                        raise ValueError("replay trace choice card is not authored")
                    if (
                        choice.card is not None
                        and choice.card.image != source_card.image
                    ):
                        raise ValueError("replay trace choice image is not authored")
                continue

            line = step.line
            if line is None:
                raise ValueError("replay trace line is no longer authored")
            _, source = resolve_authored_line(
                scene,
                loc_id=line.loc_id,
                line_index=line.line_index,
                label="replay trace line",
            )
            if line.loc_id is None and line.total_lines != len(scene.lines):
                raise ValueError("replay trace line count no longer matches")
            if authored_npcs is not None:
                by_name = getattr(authored_npcs, "by_name", None)
                get_npc = getattr(authored_npcs, "get", None)

                def _resolve_npc(speaker: object) -> object:
                    if not isinstance(speaker, str) or not speaker:
                        return None
                    resolved = None
                    if callable(by_name):
                        try:
                            resolved = by_name(speaker)
                        except Exception:
                            resolved = None
                    if resolved is None and callable(get_npc):
                        try:
                            resolved = get_npc(speaker)
                        except Exception:
                            resolved = None
                    return resolved

                source_npc = _resolve_npc(source.speaker)
                replay_npc = _resolve_npc(line.speaker)
                source_id = (
                    getattr(source_npc, "id", None)
                    if source_npc is not None
                    else source.speaker
                )
                replay_id = getattr(replay_npc, "id", None)
                if replay_npc is not None and (
                    source_npc is None or replay_id != source_id
                ):
                    raise ValueError(
                        "replay trace speaker selects an unauthorised NPC"
                    )
                if line.speaker_id is None:
                    # Archives produced before stable speaker identity existed
                    # remain replayable.  Bind the decoded presentation to the
                    # current authored NPC, never to historical display copy.
                    line.speaker_id = source_id
                elif line.speaker_id not in {source_id, source.speaker}:
                    raise ValueError(
                        "replay trace speaker_id selects an unauthorised NPC"
                    )
                else:
                    line.speaker_id = source_id
            if source.bgm is not None:
                effective_bgm = source.bgm
            elif effective_bgm is unknown_audio:
                # The scene inherited audio from the episode before this
                # archive. Its first observed effective value is the only
                # evidence available; once anchored, later no-directive lines
                # must carry that same value.
                if not _audio_ref_is_authored(line.bgm, authored_music):
                    raise ValueError("replay trace inherited BGM is not authored")
                effective_bgm = line.bgm
            if source.ambient is not None:
                effective_ambient = source.ambient
            elif effective_ambient is unknown_audio:
                if not _audio_ref_is_authored(line.ambient, authored_ambient):
                    raise ValueError(
                        "replay trace inherited ambient is not authored"
                    )
                effective_ambient = line.ambient
            if (
                line.portrait != source.portrait
                or line.portraits != source.portraits
                or line.portrait_pos != source.portrait_pos
                or line.expression != source.expression
                or line.sfx != source.sfx
                or line.voice != source.voice
                or line.sound_captions != source.sound_captions
                or line.cg != (source.cg or scene.cg)
                or line.background != scene.background
            ):
                raise ValueError("replay trace presentation is not authored")
            if line.bgm != effective_bgm:
                raise ValueError("replay trace BGM is not authored")
            if line.ambient != effective_ambient:
                raise ValueError("replay trace ambient is not authored")

    def current_scene(self):
        if self._done or not self._started:
            return None
        return self.state.story.get(self.scene_id)

    def start_scene(self, scene_id: str) -> ScenePresentation:
        if scene_id != self.scene_id:
            raise KeyError(f"replay trace does not contain scene: {scene_id}")
        if self.state.story.get(scene_id) is None:
            raise KeyError(f"Unknown scene: {scene_id}")
        self._cursor = 0
        self._choice_cursor = 0
        self._current = None
        self._waiting_choice = False
        self._started = True
        self._done = False
        self.state.story.current_scene = scene_id
        self.state.story.current_line_index = 0
        return self._take_next()

    def next_line(self) -> ScenePresentation:
        if not self._started or self._done:
            return ScenePresentation(kind="end", scene_id=self.scene_id)
        if self._waiting_choice and self._current is not None:
            return self._current
        return self._take_next()

    def choose(self, choice_id: str) -> ScenePresentation:
        if not self._waiting_choice or self._current is None:
            return self.next_line()
        selected_id = self._selected_choices[self._choice_cursor]
        if choice_id != selected_id:
            return self._current
        self._waiting_choice = False
        self._choice_cursor += 1
        return self._take_next()

    def is_current_read(self) -> bool:
        return self._started and not self._done

    def skip_to_next_unread(self) -> ScenePresentation | None:
        return self._skip_to_boundary()

    def skip_all(self) -> ScenePresentation | None:
        return self._skip_to_boundary()

    def _skip_to_boundary(self) -> ScenePresentation | None:
        if self._waiting_choice and self._current is not None:
            return self._current
        while not self._done:
            presentation = self._take_next()
            if presentation.kind == "choice":
                return presentation
            if presentation.kind == "end":
                return None
        return None

    def _take_next(self) -> ScenePresentation:
        if self._cursor >= len(self._steps):
            return self._finish()

        payload = self._payloads[self._cursor]
        presentation = deepcopy(self._steps[self._cursor])
        self._cursor += 1
        if presentation.scene_id is None:
            presentation.scene_id = self.scene_id
        if presentation.title is None:
            presentation.title = self.title

        if presentation.kind == "choice":
            selected_id = self._selected_choices[self._choice_cursor]
            # The remembered menu remains visible, but only the option the
            # player actually chose can advance.  Other once-visible options
            # are disabled so replay can never branch into unseen material.
            for choice in presentation.choices:
                if choice.id != selected_id:
                    choice.enabled = False
                    choice.reason = "回想會維持當時的選擇"
            self._waiting_choice = True
        elif presentation.line is not None:
            scene = self._authored_story.get(self.scene_id)
            if scene is None:
                raise ValueError("replay trace scene is no longer authored")
            authored_index, _ = resolve_authored_line(
                scene,
                loc_id=presentation.line.loc_id,
                line_index=presentation.line.line_index,
                label="replay trace line",
            )
            self.state.story.current_line_index = authored_index + 1

        raw_fx = payload.get(REPLAY_FX_PAYLOAD_KEY, [])
        if isinstance(raw_fx, list):
            replay_fx = [
                deepcopy(directive)
                for directive in raw_fx
                if isinstance(directive, dict)
                and directive.get("fx") in REPLAY_VISUAL_FX_KINDS
            ]
            if replay_fx:
                queue = self.state.meta.setdefault(VISUAL_FX_QUEUE, [])
                if isinstance(queue, list):
                    queue.extend(replay_fx)
        replay_hud = payload.get(REPLAY_HUD_PAYLOAD_KEY)
        if isinstance(replay_hud, dict):
            self.state.meta[REPLAY_HUD_META_KEY] = deepcopy(replay_hud)

        self._current = presentation
        return presentation

    def _finish(self) -> ScenePresentation:
        self._done = True
        self._waiting_choice = False
        self._current = None
        self.state.story.current_scene = None
        self.state.story.current_line_index = 0
        return ScenePresentation(
            kind="end",
            scene_id=self.scene_id,
            title=self.title,
        )
