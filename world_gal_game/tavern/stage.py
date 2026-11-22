"""The stage: several agents act a scene out, one character each.

Dialogue written by a single model has a single author's blind spots: every
character knows everything, wants the scene to succeed, answers the question
that was asked, and talks in the same voice. The stage removes the single
author. A scene is *played*:

- every character is its own agent, prompted from its own card, shown only
  what that character could know — its own secrets and its own goal for the
  scene, never anyone else's;
- a **director** agent (optional) sees everything, picks who speaks next and
  hands that actor one playable note; it writes no lines;
- a **narrator** agent writes narration from the protagonist's point of view
  and may change background / music / CG with directive tags;
- each line is checked by the de-slop linter as it arrives and sent back to
  the agent that wrote it when it trips (a *retake*), with the reason.

The result is turned into an ordinary scene (:meth:`Stage.to_scene`) for
``PackEditor`` — or, when *restaging* an existing scene, into text-only
``update_line`` edits that leave the scene's structure, effects and ids alone.

The stage is a **pure state machine with no I/O**. :meth:`Stage.next_request`
returns the next thing some agent has to answer (role, speaker, chat messages)
and :meth:`Stage.submit` takes the answer. Whoever pumps it decides where the
answers come from: an API backend, a scripted test double, a human at a
prompt, or an outer coding agent that hands each request to a sub-agent of its
own. Its whole state serialises to JSON, so a run can stop and resume between
any two requests.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..core.game_state import seeded_rng
from .assets import AssetIndex
from .card import CharacterCard
from .expressions import ExpressionResolver
from .pack import TavernPack
from .preset import Preset
from .prompt import (AuthorNote, CastView, ChatMessage, PrivateBrief, PromptBuild,
                     PromptInputs, SceneContext, build_prompt)
from .reply import ParsedLine, parse_reply, render_turn, strip_private
from .scripts import repair_slip, to_traditional
from .slop import (SlopHit, SlopLinter, SlopReport, lines_from_scene, normalize_line,
                   repeats_earlier)
from .stage_text import stage_text
from .style import StyleGuide

PLAYER = "player"
NARRATOR = "narrator"
END = "end"

_KV_RE = re.compile(
    r"^\s*[-*]?\s*(下一個|下一个|next|speaker|提示|指示|note|direction|節拍|节拍|beat|"
    r"理由|why|reason)\s*[:：]\s*(.*)$", re.IGNORECASE)
_KV_KEYS = {
    "下一個": "next", "下一个": "next", "next": "next", "speaker": "next",
    "提示": "note", "指示": "note", "note": "note", "direction": "note",
    "節拍": "beat", "节拍": "beat", "beat": "beat",
    "理由": "why", "why": "why", "reason": "why",
}
_MARKUP_RE = re.compile(r"\[/?[A-Za-z][^\]\n]*\]")
_TOKEN_RE = re.compile(r"\{[^{}\n]{1,60}\}")
_OP_RE = re.compile(r"\[\[[^\]\n]+\]\]")
_TITLE_RE = re.compile(r"[《〈『][^》〉』\n]{1,24}[》〉』]")
_NUMBER_RE = re.compile(r"\d+(?:[.:：]\d+)?")
_SENT_END = "。！？!?…～~—.」』"


# ----------------------------------------------------------------------
# Brief


class CastSpec(BaseModel):
    """One character in the scene, with what only they (and the director) know."""

    model_config = ConfigDict(extra="forbid")

    id: str
    objective: str = ""                 # what they want out of this scene
    tactic: str = ""                    # how they go about it
    hidden: list[str] = Field(default_factory=list)   # scene-specific secrets
    affection: int | None = None        # override the live value for this scene


class BeatSpec(BaseModel):
    """One dramatic step of the scene. Only the director sees beats."""

    model_config = ConfigDict(extra="forbid")

    goal: str
    must_include: list[str] = Field(default_factory=list)
    max_turns: int = Field(default=8, ge=1)
    notes: dict[str, str] = Field(default_factory=dict)   # cast id -> private note


class PlayerSpec(BaseModel):
    """How the protagonist takes part."""

    model_config = ConfigDict(extra="forbid")

    #: ``actor`` = an agent plays them; ``silent`` = they do not speak (their
    #: side is narration and choices); ``human`` = a person types their lines.
    mode: Literal["actor", "silent", "human"] = "actor"
    objective: str = ""
    tactic: str = ""


class ChoiceOption(BaseModel):
    """A choice at the end of the scene. Mechanics are authored, wording is not."""

    model_config = ConfigDict(extra="forbid")

    id: str
    intent: str = ""                    # the attitude this option stands for
    text: str = ""                      # fixed wording (skips generation)
    effects: list[Any] = Field(default_factory=list)
    requires: list[Any] = Field(default_factory=list)
    forbids: list[Any] = Field(default_factory=list)
    next_scene: str | None = None


class ScriptStep(BaseModel):
    """One existing line being restaged."""

    model_config = ConfigDict(extra="forbid")

    line_index: int
    speaker_id: str                     # cast id, PLAYER or NARRATOR
    speaker: str = ""                   # authored speaker label
    source: str                         # the authored text
    rewrite: bool = True
    quoted: bool = True                 # the authored line was wrapped in quotes
    keep: list[str] = Field(default_factory=list)
    skip_reason: str = ""


class SceneBrief(BaseModel):
    """What the scene is, who is in it, and where it has to go."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str = ""
    location: str | None = None
    time: str = ""
    background: str | None = None
    bgm: str | None = None
    ambient: str | None = None
    route: str | None = None
    tags: list[str] = Field(default_factory=list)
    once: bool | None = None
    requires: list[Any] = Field(default_factory=list)
    on_end: list[Any] = Field(default_factory=list)
    premise: str = ""                   # the public situation
    #: Effects applied to a scratch game state before staging (set the flags
    #: and affection the scene assumes), so world-book gates and each
    #: character's manner match the moment the scene belongs to.
    setup: list[Any] = Field(default_factory=list)
    cast: list[CastSpec] = Field(default_factory=list)
    beats: list[BeatSpec] = Field(default_factory=list)
    player: PlayerSpec = Field(default_factory=PlayerSpec)
    order: Literal["director", "natural", "round_robin"] = "director"
    narration: Literal["none", "light", "normal"] = "light"
    opening: str = ""                   # fixed opening narration (one row per line)
    ending: str = ""                    # how it should stop (director only)
    max_turns: int = Field(default=24, ge=1)
    lines_per_turn: int = Field(default=2, ge=1, le=6)
    #: An actor may add an action row at most once every this many of their
    #: turns (1 = every turn, 0 = never). Left alone, a model tags a gesture
    #: onto every line and half the scene becomes fidgeting.
    action_every: int = Field(default=2, ge=0, le=8)
    #: How many turns in a row one speaker may take before the stage hands the
    #: turn to someone else (0 = no limit).
    max_consecutive: int = Field(default=2, ge=0, le=8)
    retakes: int = Field(default=2, ge=0, le=5)
    choices: list[ChoiceOption] = Field(default_factory=list)
    choice_max_chars: int = Field(default=16, ge=4)
    note: str = ""                      # author's note, injected near the end
    note_depth: int = Field(default=2, ge=0)
    preset: str = ""
    seed: int | None = None
    into_file: str | None = None
    #: Filled by :meth:`Stage.restage`; never authored by hand.
    script: list[ScriptStep] = Field(default_factory=list)
    restage_of: str = ""

    @field_validator("cast", mode="before")
    @classmethod
    def _cast(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [{"id": item} if isinstance(item, str) else item for item in value]
        return value

    @field_validator("beats", mode="before")
    @classmethod
    def _beats(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [{"goal": item} if isinstance(item, str) else item for item in value]
        return value

    @classmethod
    def load(cls, path: Path) -> "SceneBrief":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"{Path(path).name}: a brief must be a mapping")
        raw.setdefault("id", Path(path).stem)
        return cls.model_validate(raw)


# ----------------------------------------------------------------------
# Runtime records


class Turn(BaseModel):
    """One line of the transcript (one text box of the finished scene)."""

    kind: Literal["dialogue", "narration", "player"]
    speaker_id: str
    speaker: str = ""
    text: str
    take: int = 0                       # lines from one reply share a take
    expression_word: str = ""
    expression: str | None = None       # resolved key ("" = base portrait)
    expression_source: str = ""
    bg: str = ""
    bgm: str = ""
    cg: str = ""
    sfx: str = ""
    beat: int = 0
    note: str = ""                      # the direction that produced it
    attempts: int = 1
    hits: list[dict[str, Any]] = Field(default_factory=list)
    source_index: int | None = None     # restage: authored line index
    source_text: str = ""
    quoted: bool = True
    fallback: bool = False              # restage: kept the authored line
    fixed: bool = False                 # authored / human text, not generated


class StageRequest(BaseModel):
    """Something an agent has to answer before the stage can go on."""

    id: str
    role: Literal["director", "actor", "narrator", "player", "choices", "human"]
    speaker_id: str = ""
    speaker: str = ""
    messages: list[dict[str, str]] = Field(default_factory=list)
    attempt: int = 1
    purpose: str = ""
    #: Hints for the backend; none are binding.
    params: dict[str, Any] = Field(default_factory=dict)
    model_hint: str = ""


@dataclass
class _Cue:
    speaker_id: str
    note: str = ""
    step: ScriptStep | None = None


@dataclass
class _Candidate:
    text: str
    lines: list[ParsedLine]
    hits: list[SlopHit]

    def rank(self) -> tuple[int, float]:
        return (sum(1 for hit in self.hits if hit.hard),
                sum(hit.weight for hit in self.hits))


# ----------------------------------------------------------------------
# Context


@dataclass
class StageContext:
    """The pack-side facts a stage reads: cards, characters, state, media."""

    tavern: TavernPack
    npcs: Any = None
    state: Any = None
    assets: AssetIndex = field(default_factory=AssetIndex)

    @classmethod
    def from_session(cls, session: Any) -> "StageContext":
        """Build from a live :class:`~world_gal_game.headless.HeadlessSession`."""
        from .pack import tavern_for
        root = session.config.pack_root(session.pack)
        tavern = tavern_for(session.state, root, session.npcs, session.meta)
        return cls(tavern=tavern, npcs=session.npcs, state=session.state,
                   assets=AssetIndex.from_state(session.state))

    @classmethod
    def from_pack(cls, pack: str, *, seed: int | None = None) -> "StageContext":
        """Load ``pack`` headlessly and build a context from it."""
        from ..config import EngineConfig
        from ..headless import HeadlessSession
        session = HeadlessSession.open(EngineConfig(seed=seed), pack=pack)
        return cls.from_session(session)

    # -- lookups ---------------------------------------------------------

    def npc(self, character_id: str) -> Any:
        return self.npcs.get(character_id) if self.npcs is not None else None

    def apply_setup(self, effects: list[Any]) -> list[dict[str, Any]]:
        """Apply a brief's ``setup`` effects to this context's game state.

        Goes through ``GameState.apply`` like every other state change. Only
        call this on a state you own (the CLI's private session); a warm
        session positions its own state with ordinary ops instead.
        """
        from ..dialogue.script_loader import _to_effects
        if self.state is None or not effects:
            return []
        return [self.state.apply(effect) for effect in _to_effects(list(effects))]

    def location_name(self, location_id: str | None) -> str:
        if not location_id:
            return ""
        locations = getattr(getattr(self.state, "map", None), "locations", {}) or {}
        location = locations.get(location_id)
        return getattr(location, "name", "") or location_id

    def location_background(self, location_id: str | None) -> str | None:
        locations = getattr(getattr(self.state, "map", None), "locations", {}) or {}
        location = locations.get(location_id or "")
        return getattr(location, "background", None)

    def time_label(self) -> str:
        time = getattr(self.state, "time", None)
        try:
            return time.time_label() if time is not None else ""
        except Exception:
            return ""

    def affection(self, character_id: str) -> tuple[int | None, str]:
        tracker = getattr(self.state, "affection", None)
        if tracker is None or character_id not in getattr(tracker, "characters", {}):
            return None, ""
        try:
            return int(tracker.get(character_id)), str(tracker.level_label(character_id))
        except Exception:
            return None, ""

    def entity_names(self) -> list[str]:
        """Proper nouns a rewrite must not lose: people, places, items."""
        names: list[str] = []
        if self.npcs is not None:
            names += [npc.name for npc in self.npcs.all()]
        locations = getattr(getattr(self.state, "map", None), "locations", {}) or {}
        names += [loc.name for loc in locations.values()]
        items = getattr(getattr(self.state, "items", None), "items", {}) or {}
        names += [getattr(item, "name", "") for item in items.values()]
        # World-book keys are the nouns the author marked as mattering.
        book = getattr(self.tavern, "lorebook", None)
        for entry in getattr(book, "entries", None) or []:
            names += [key for key in entry.keys if key and not key.startswith("/")]
        return sorted({name for name in names if name and len(name) >= 2},
                      key=len, reverse=True)

    def macro_values(self) -> dict[str, str]:
        values: dict[str, str] = {}
        state = self.state
        if state is None:
            return values
        label = self.time_label()
        if label:
            values["timeofday"] = label
            values["time"] = label
        day = getattr(getattr(state, "time", None), "day", None)
        if day is not None:
            values["day"] = str(day)
        current = getattr(getattr(state, "map", None), "current", None)
        if current is not None:
            values["location"] = getattr(current, "name", "") or ""
        chapter = getattr(state, "current_chapter", None)
        if chapter:
            values["chapter"] = str(chapter)
        return values

    def macro_resolver(self, name: str, args: list[str]) -> str | None:
        state = self.state
        if state is None or not args:
            return None
        try:
            if name == "flag":
                value = state.events.get_flag(args[0], "")
                return "" if value is False else str(value)
            if name == "affection":
                return str(state.affection.get(args[0]))
            if name == "affectionlevel":
                return str(state.affection.level_label(args[0]))
            if name == "resource":
                return str(state.resources.get(args[0]))
        except Exception:
            return ""
        return None


# ----------------------------------------------------------------------
# Stage


class StageError(RuntimeError):
    """The stage was driven incorrectly (wrong request id, bad brief, …)."""


class Stage:
    """Play one scene. See the module docstring for the model."""

    def __init__(self, brief: SceneBrief, ctx: StageContext, *,
                 preset: Preset | None = None, seed: int | None = None,
                 chat: bool = False) -> None:
        self.brief = brief
        self.ctx = ctx
        self.chat = chat
        self.preset = preset or ctx.tavern.preset(brief.preset or None)
        self.style: StyleGuide = ctx.tavern.style
        self.T = stage_text(self.style.locale)
        self.rng = seeded_rng(seed if seed is not None else brief.seed)
        self.user = ctx.tavern.player_name or "Player"
        self._cjk = self.style.locale.startswith(("zh", "ja"))
        self._sep = "、" if self._cjk else ", "

        self.cards: dict[str, CharacterCard] = {}
        for member in brief.cast:
            card = ctx.tavern.card(member.id)
            if card is None:
                raise StageError(
                    f"cast member '{member.id}' has no character or card in this pack")
            self.cards[member.id] = card
        if not self.cards and not brief.script:
            raise StageError("a brief needs at least one cast member")
        self.cards[PLAYER] = ctx.tavern.player_card()
        self._cast = {member.id: member for member in brief.cast}

        names = [card.name for cid, card in self.cards.items() if cid != PLAYER]
        self.linter = SlopLinter(self.style, never_says=ctx.tavern.never_says(),
                                 names=names, player_name=self.user)
        self._resolvers: dict[str, ExpressionResolver | None] = {}

        self.turns: list[Turn] = []
        self.phase = "direct"
        self.beat = 0
        self.beat_turns = 0
        self.takes = 0
        self.script_pos = 0
        self.variables: dict[str, Any] = {}
        self.choice_texts: dict[str, str] = {c.id: c.text for c in brief.choices
                                             if c.text}
        self.events: list[dict[str, Any]] = []
        self._cue: _Cue | None = None
        self._pending: StageRequest | None = None
        self._attempt = 1
        self._retake = ""
        self._candidates: list[_Candidate] = []
        self._counter = 0
        self._opened = False
        self._closing_done = False
        self._chat_queue: list[str] = []
        self._last_build: PromptBuild | None = None
        self._rr = 0
        #: Total hit weight on one take that sends it back (a hard hit always does).
        self.retake_threshold = 2.5

    # ------------------------------------------------------------------
    # construction helpers

    @classmethod
    def restage(cls, scene: Any, ctx: StageContext, *, narration: bool = False,
                only_flagged: bool = False, preset: Preset | None = None,
                seed: int | None = None, retakes: int = 2) -> "Stage":
        """Build a stage that re-performs an existing scene line by line.

        Structure is preserved exactly: one rewritten line per authored line,
        same order, same speakers. Only lines whose speaker has a card are
        rewritten; narration is rewritten only when ``narration`` is true;
        lines carrying rich-text markup are left as authored. With
        ``only_flagged`` only lines the linter complains about are redone.
        """
        tavern = ctx.tavern
        style = tavern.style
        linter = SlopLinter(style, never_says=tavern.never_says(),
                            player_name=tavern.player_name)
        flagged: set[int] = set()
        if only_flagged:
            report = linter.lint(lines_from_scene(scene))
            flagged = {hit.line for hit in report.hits if hit.line is not None}
        names = ctx.entity_names()
        player_labels = {tavern.player_name, "{player_name}", tavern.persona.speaker}
        steps: list[ScriptStep] = []
        cast_ids: list[str] = []
        for index, line in enumerate(scene.lines):
            text = line.text or ""
            speaker = line.speaker
            if speaker is None:
                speaker_id = NARRATOR
            elif speaker in player_labels:
                speaker_id = PLAYER
            else:
                card = tavern.card(speaker)
                speaker_id = card.id if card is not None and card.id else ""
            reason = ""
            persona = tavern.persona
            if speaker is not None and not speaker_id:
                reason = "speaker has no card"
            elif speaker_id == PLAYER and not (persona.description.strip()
                                               or not persona.speech.is_empty()):
                reason = "no persona for the protagonist"
            elif speaker_id == NARRATOR and not narration:
                reason = "narration not selected"
            elif getattr(line, "llm_speaker", False):
                reason = "llm_speaker line"
            elif _MARKUP_RE.search(text):
                reason = "carries rich-text markup"
            elif only_flagged and index not in flagged:
                reason = "not flagged"
            stripped = text.strip()
            quoted = stripped.startswith(style.quote_open) and stripped.endswith(
                style.quote_close)
            inner = stripped[len(style.quote_open):-len(style.quote_close)] if quoted \
                else stripped
            if not reason and speaker is not None and quoted and (
                    style.quote_open in inner or style.quote_close in inner):
                reason = "mixed speech and narration"
            if not reason and len(inner) <= 3:
                reason = "too short to need it"
            keep = _keep_tokens(text, names)
            steps.append(ScriptStep(
                line_index=index, speaker_id=speaker_id or (speaker or NARRATOR),
                speaker=speaker or "", source=inner if quoted else stripped,
                rewrite=not reason, quoted=quoted, keep=keep, skip_reason=reason))
            if speaker_id and speaker_id not in (PLAYER, NARRATOR) \
                    and speaker_id not in cast_ids:
                cast_ids.append(speaker_id)
        brief = SceneBrief(
            id=f"{scene.id}__restage", title=scene.title or scene.id,
            location=scene.location, premise="", cast=[CastSpec(id=c) for c in cast_ids],
            order="natural", narration="none", script=steps, restage_of=scene.id,
            retakes=retakes, max_turns=max(1, len(steps)),
        )
        return cls(brief, ctx, preset=preset, seed=seed)

    # ------------------------------------------------------------------
    # small helpers

    @property
    def done(self) -> bool:
        return self.phase == "done"

    @property
    def is_restage(self) -> bool:
        return bool(self.brief.script)

    def _name(self, speaker_id: str) -> str:
        if speaker_id == NARRATOR:
            return ""
        card = self.cards.get(speaker_id)
        return card.name if card else speaker_id

    def _pov(self) -> str:
        if self.style.pov == "first":
            return self.T["pov_first"]
        if self.style.pov == "second":
            return self.T["pov_second"]
        return self.user

    def _speakers(self) -> list[str]:
        """Cast ids that may be cued to speak (the player when an agent plays them)."""
        ids = [member.id for member in self.brief.cast]
        if self.brief.player.mode == "actor" and not self.chat:
            ids.append(PLAYER)
        return ids

    def _resolver(self, speaker_id: str) -> ExpressionResolver | None:
        if speaker_id in self._resolvers:
            return self._resolvers[speaker_id]
        resolver: ExpressionResolver | None = None
        npc = self.ctx.npc(speaker_id)
        keys = list(getattr(npc, "portrait_set", {}) or {})
        if keys:
            card = self.cards.get(speaker_id)
            resolver = ExpressionResolver(
                speaker_id, keys, aliases=card.expression_aliases if card else None,
                memo=self.ctx.tavern.memo)
        self._resolvers[speaker_id] = resolver
        return resolver

    def _next_id(self) -> str:
        self._counter += 1
        return f"{self.brief.id}:{self._counter}"

    def _log(self, kind: str, **data: Any) -> None:
        self.events.append({"event": kind, "turn": len(self.turns), **data})

    def _own_takes(self, speaker_id: str) -> list[list[Turn]]:
        """One speaker's turns, grouped by take, oldest first."""
        takes: dict[int, list[Turn]] = {}
        for turn in self.turns:
            if turn.speaker_id == speaker_id:
                takes.setdefault(turn.take, []).append(turn)
        return [takes[key] for key in sorted(takes)]

    def _action_allowed(self, speaker_id: str) -> bool:
        """Whether this speaker may add an action row on their next turn."""
        every = self.brief.action_every
        if every <= 0:
            return False
        recent = self._own_takes(speaker_id)[-(every - 1):] if every > 1 else []
        return not any(turn.kind == "narration" for take in recent for turn in take)

    def _streak(self) -> tuple[str, int]:
        """Who has been speaking, and for how many turns in a row."""
        speaker, count, last_take = "", 0, None
        for turn in reversed(self.turns):
            if turn.speaker_id == NARRATOR or turn.take == last_take:
                continue
            if speaker and turn.speaker_id != speaker:
                break
            speaker, count, last_take = turn.speaker_id, count + 1, turn.take
        return speaker, count

    def _missing_must(self) -> list[str]:
        """What the current beat has to surface and nobody has said yet.

        A word counts once it is spoken, or written by the narrator. An
        actor's own action row does not count: "she brings it up" is not the
        same as a gesture near it.
        """
        beats = self.brief.beats
        if not beats or self.beat >= len(beats) or not beats[self.beat].must_include:
            return []
        said = "\n".join(turn.text for turn in self.turns if turn.beat == self.beat
                         and (turn.kind != "narration" or turn.speaker_id == NARRATOR))
        return [word for word in beats[self.beat].must_include if word not in said]

    def _owed(self) -> list[str]:
        """Everything the remaining beats still have to surface."""
        later = [word for beat in self.brief.beats[self.beat + 1:]
                 for word in beat.must_include]
        return [*self._missing_must(), *later]

    #: How a subjectless action row tends to begin (Traditional and Simplified).
    _ACTION_OPENERS = (
        "把", "將", "将", "隨手", "随手", "順著", "顺着", "用", "往", "朝", "從", "从",
        "抬", "低", "轉", "转", "伸", "拿", "看", "指", "盯", "沒", "没", "走", "坐",
        "站", "笑", "點", "点", "搖", "摇", "皺", "皱", "輕輕", "轻轻", "默默", "慢慢",
        "假裝", "假装", "繼續", "继续", "目光", "視線", "视线", "在",
    )

    def _repair_action(self, speaker_id: str, text: str) -> str:
        """Give an action row its subject and its full stop.

        Models often answer a direction by echoing it as a bare verb phrase
        ("把紙翻到背面"). In the finished scene that row is narration, so it
        has to say who is doing it.
        """
        text = text.strip()
        if not self._cjk or not text:
            return text
        if speaker_id == PLAYER:
            # The protagonist's own row: it is theirs unless it opens on
            # somebody else.
            pov = self._pov()
            others = [name for cid, card in self.cards.items() if cid != PLAYER
                      for name in (card.name, card.nickname) if name]
            if pov not in text[:6] and self.user not in text \
                    and not text.startswith(("她", "他", "它", *others)):
                text = pov + text
        elif text.startswith(self._ACTION_OPENERS):
            card = self.cards.get(speaker_id)
            names = [n for n in (card.name, card.nickname) if n] if card else []
            if names and not any(name in text for name in names):
                text = names[0] + text
        if text[-1] not in "。！？!?…～」』）":
            text += "。"
        return text

    # ------------------------------------------------------------------
    # what each agent gets to see

    def _transcript_text(self, *, numbered: bool = False, tail: int | None = None) -> str:
        turns = self.turns[-tail:] if tail else self.turns
        if not turns:
            return self.T["nothing_yet"]
        lines = []
        start = len(self.turns) - len(turns)
        for offset, turn in enumerate(turns):
            if turn.kind == "narration":
                text = self.T["narration_line"].format(text=turn.text)
            else:
                text = self.T["speech_line"].format(name=turn.speaker, text=turn.text)
            lines.append(f"{start + offset + 1}. {text}" if numbered else text)
        return "\n".join(lines)

    def _history_for(self, speaker_id: str) -> list[ChatMessage]:
        """The conversation from one speaker's seat: own lines are theirs."""
        uses_sprite = self._resolver(speaker_id) is not None
        messages: list[ChatMessage] = []
        last_take = -1
        for turn in self.turns:
            own = turn.speaker_id == speaker_id
            if turn.kind == "narration":
                text = self.T["narration_line"].format(text=turn.text)
                messages.append(ChatMessage("assistant" if own else "user", text))
            elif own:
                sprite = turn.expression_word if (uses_sprite and turn.take != last_take) \
                    else ""
                messages.append(ChatMessage(
                    "assistant", render_turn(turn.text, sprite=sprite, style=self.style),
                    turn.speaker))
                last_take = turn.take
            else:
                messages.append(ChatMessage(
                    "user", self.T["speech_line"].format(name=turn.speaker,
                                                         text=turn.text),
                    turn.speaker))
        card_name = self._name(speaker_id)
        if not messages:
            messages.append(ChatMessage("user", self.T["cue_open"].format(char=card_name)))
        elif messages[-1].role == "assistant":
            messages.append(ChatMessage("user",
                                        self.T["cue_again"].format(char=card_name)))
        return messages

    def _scene_context(self, speaker_id: str) -> SceneContext:
        brief = self.brief
        card = self.cards[speaker_id]
        cast: list[CastView] = []
        for other_id, other in self.cards.items():
            if other_id in (speaker_id, PLAYER):
                continue          # the player is described by the persona block
            note = (card.relationships.get(other_id)
                    or card.relationships.get(other.name) or "")
            cast.append(CastView(name=other.name, role=other.role, note=note))
        context = SceneContext(
            location=self.ctx.location_name(brief.location),
            # A written scene states its own time; only a live chat reads the clock.
            time=brief.time or (self.ctx.time_label() if self.chat else ""),
            premise=brief.premise, cast=cast)
        if speaker_id != PLAYER:
            member = self._cast.get(speaker_id)
            value, label = self.ctx.affection(speaker_id)
            if member is not None and member.affection is not None:
                value, label = member.affection, ""
            context.affection, context.affection_label = value, label
            context.relationship = card.voice_for_affection(value) or (
                card.relationships.get(PLAYER) or card.relationships.get(self.user) or "")
        return context

    def _private(self, speaker_id: str) -> PrivateBrief:
        if speaker_id == PLAYER:
            player = self.brief.player
            return PrivateBrief(objective=player.objective, tactic=player.tactic)
        member = self._cast.get(speaker_id)
        if member is None:
            return PrivateBrief()
        return PrivateBrief(objective=member.objective, tactic=member.tactic,
                            hidden=list(member.hidden))

    def _beat_note(self, speaker_id: str) -> str:
        beats = self.brief.beats
        if not beats or self.beat >= len(beats):
            return ""
        return beats[self.beat].notes.get(speaker_id, "")

    # ------------------------------------------------------------------
    # requests

    def _actor_request(self, cue: _Cue) -> StageRequest:
        speaker_id = cue.speaker_id
        card = self.cards[speaker_id]
        is_player = speaker_id == PLAYER
        resolver = self._resolver(speaker_id)
        T = self.T
        direction_parts = [cue.note, self._beat_note(speaker_id)]
        step = cue.step
        if step is not None:
            direction_parts.append(T["restage_speech"].format(
                source=step.source, char=card.name))
            if step.keep:
                direction_parts.append(T["restage_keep"].format(
                    tokens=self._sep.join(step.keep)))
            upcoming = self._next_source(step)
            if upcoming:
                direction_parts.append(T["restage_next"].format(next=upcoming))
        missing = self._missing_must() if (step is None and not is_player) else []
        if missing:
            beat = self.brief.beats[self.beat]
            last_chance = (self.beat_turns >= beat.max_turns - 2
                           or self.takes >= self.brief.max_turns - 2)
            if last_chance:
                direction_parts.append(T["must_mention"].format(
                    items=self._sep.join(missing)))
        action_ok = step is None and self._action_allowed(speaker_id)
        if is_player:
            rule = T["player_action_ok"].format(pov=self._pov()) if action_ok \
                else T["action_no"]
            output = T["player_format"].format(char=card.name, user=self.user,
                                               pov=self._pov(), action_rule=rule)
        else:
            rule = T["actor_action_ok"].format(char=card.name) if action_ok \
                else T["action_no"]
            output = T["actor_format"].format(
                char=card.name, user=self.user, action_rule=rule,
                sprite_line=T["sprite_line"] if resolver is not None else "")
        if step is not None:
            output += "\n" + T["restage_one_line"]
        note = AuthorNote(content=self.brief.note, depth=self.brief.note_depth) \
            if self.brief.note.strip() else None
        inputs = PromptInputs(
            card=card, preset=self.preset, style=self.style, user_name=self.user,
            persona="" if is_player else self.ctx.tavern.persona.description,
            history=self._history_for(speaker_id),
            lorebooks=[self.ctx.tavern.lorebook] if self.ctx.tavern.lorebook else [],
            scene=self._scene_context(speaker_id), private=self._private(speaker_id),
            direction="\n".join(part for part in direction_parts if part and part.strip()),
            retake=self._retake, output_format=output,
            expressions=resolver.describe() if resolver is not None else {},
            author_note=note,
            group=[c.name for cid, c in self.cards.items() if cid != PLAYER],
            speech_format=T["speech_line"], example_separator=T["example_sep"],
            state=self.ctx.state, rng=self.rng, variables=self.variables,
            values=self.ctx.macro_values(), resolver=self.ctx.macro_resolver,
            generation_type="regenerate" if self._attempt > 1 else "normal",
        )
        build = build_prompt(inputs)
        self._last_build = build
        return StageRequest(
            id=self._next_id(), role="player" if is_player else "actor",
            speaker_id=speaker_id, speaker=card.name, messages=build.messages,
            attempt=self._attempt, model_hint=card.model_hint,
            purpose=f"{card.name} speaks" + (f" (take {self._attempt})"
                                             if self._attempt > 1 else ""),
            params={"effort": self.preset.effort or "low",
                    "max_tokens": self.preset.max_tokens,
                    "temperature": self.preset.temperature,
                    "top_p": self.preset.top_p,
                    "tokens_estimate": build.tokens})

    def _next_source(self, step: ScriptStep) -> str:
        for later in self.brief.script[self.script_pos + 1:self.script_pos + 3]:
            if later.speaker_id not in (step.speaker_id, NARRATOR):
                return f"{later.speaker}：{later.source}" if later.speaker else later.source
        return ""

    def _narrator_system(self) -> str:
        T = self.T
        text = T["narrator_system"].format(
            user=self.user, pov=self._pov(), rules=self.style.render_narration_rules())
        if self.is_restage:
            return text
        assets = self.ctx.assets
        blocks: list[str] = []
        current_bg = assets.label_for("bg", self._current("bg")) or T["none"]
        current_bgm = assets.label_for("bgm", self._current("bgm")) or T["none"]
        if len(assets.all("bg")) > 1:
            blocks.append(T["narrator_bg"].format(items=assets.describe("bg"),
                                                  current=current_bg))
        if assets.all("bgm"):
            blocks.append(T["narrator_bgm"].format(items=assets.describe("bgm"),
                                                   current=current_bgm))
        if assets.all("cg"):
            blocks.append(T["narrator_cg"].format(items=assets.describe("cg")))
        if blocks:
            text += "\n\n" + T["narrator_assets_title"] + "\n" + "\n\n".join(blocks)
        return text

    def _current(self, kind: str) -> str | None:
        """The background / music in effect at the end of the transcript."""
        value = {"bg": self.brief.background
                 or self.ctx.location_background(self.brief.location),
                 "bgm": self.brief.bgm}.get(kind)
        for turn in self.turns:
            written = getattr(turn, kind, "")
            if written:
                asset = self.ctx.assets.resolve(kind, written)
                if asset is not None:
                    value = asset.path
        return value

    def _narrator_request(self, cue: _Cue) -> StageRequest:
        T = self.T
        step = cue.step
        if step is not None:
            cue_text = T["restage_narration"].format(source=step.source, user=self.user)
            if step.keep:
                cue_text += "\n" + T["restage_keep"].format(
                    tokens=self._sep.join(step.keep))
        else:
            cue_text = cue.note or (T["narrator_open_cue"] if not self.turns
                                    else T["narrator_default_cue"]).format(user=self.user)
        if self._retake:
            cue_text += "\n\n" + self._retake
        user = T["narrator_user"].format(
            premise=self.brief.premise or T["none"],
            transcript=self._transcript_text(tail=14), cue=cue_text)
        return StageRequest(
            id=self._next_id(), role="narrator", speaker_id=NARRATOR,
            messages=[{"role": "system", "content": self._narrator_system()},
                      {"role": "user", "content": user}],
            attempt=self._attempt, purpose="narration",
            params={"effort": self.preset.effort or "low",
                    "max_tokens": self.preset.max_tokens})

    def _director_request(self) -> StageRequest:
        T = self.T
        brief = self.brief
        cast_lines = []
        for speaker_id in self._speakers():
            card = self.cards[speaker_id]
            private = self._private(speaker_id)
            details = []
            if private.objective:
                details.append(T["director_wants"].format(text=private.objective))
            if private.tactic:
                details.append(T["director_tactic"].format(text=private.tactic))
            for secret in [*card.secrets, *private.hidden]:
                details.append(T["director_secret"].format(text=secret))
            cast_lines.append(T["director_cast_line"].format(
                id=speaker_id, name=card.name, details="; ".join(details)
                if self._sep == ", " else "；".join(details)))
        beat_lines = []
        for index, beat in enumerate(brief.beats):
            must = T["director_must"].format(
                items=self._sep.join(beat.must_include)) if beat.must_include else ""
            beat_lines.append(T["director_beat"].format(
                n=index + 1, goal=beat.goal, must=must,
                mark=T["director_now"] if index == self.beat else ""))
        system = T["director_system"] + "\n\n" + T["director_brief"].format(
            premise=brief.premise or T["none"], cast="\n".join(cast_lines),
            beats="\n".join(beat_lines) or T["none"],
            ending=brief.ending or T["default_ending"])
        options = [*self._speakers()]
        if brief.narration != "none":
            options.append(NARRATOR)
        options.append(END)
        beat_max = brief.beats[self.beat].max_turns if self.beat < len(brief.beats) \
            else brief.max_turns
        status: list[str] = []
        left = brief.max_turns - self.takes
        if left <= 3:
            status.append(T["director_left"].format(left=left))
        missing = self._missing_must()
        if missing:
            status.append(T["director_missing"].format(items=self._sep.join(missing)))
        streak_id, streak = self._streak()
        if brief.max_consecutive and streak >= brief.max_consecutive \
                and len(self._speakers()) > 1:
            status.append(T["director_streak"].format(name=self._name(streak_id),
                                                      n=streak))
        if PLAYER in self._speakers() and self.takes >= 2 and not any(
                turn.kind == "player" for turn in self.turns):
            status.append(T["director_silent"].format(name=self._name(PLAYER)))
        user = T["director_user"].format(
            turn=self.takes + 1, max_turns=brief.max_turns, beat_turns=self.beat_turns,
            beat_max=beat_max, transcript=self._transcript_text(numbered=True),
            options="｜".join(options),
            status="\n".join(status) + "\n\n" if status else "")
        return StageRequest(
            id=self._next_id(), role="director",
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            purpose="direction",
            params={"effort": "medium", "max_tokens": self.preset.max_tokens})

    def _choices_request(self) -> StageRequest:
        T = self.T
        wanted = [c for c in self.brief.choices if c.id not in self.choice_texts]
        options = "\n".join(T["choices_option"].format(id=c.id, intent=c.intent or c.id)
                            for c in wanted)
        persona = self.ctx.tavern.persona.description.strip() or T["none"]
        user = T["choices_user"].format(
            user=self.user, persona=persona, transcript=self._transcript_text(tail=12),
            options=options, limit=self.brief.choice_max_chars)
        return StageRequest(
            id=self._next_id(), role="choices",
            messages=[{"role": "system",
                       "content": T["choices_system"].format(user=self.user)},
                      {"role": "user", "content": user}],
            purpose="player choices", params={"effort": "low"})

    def _human_request(self) -> StageRequest:
        return StageRequest(id=self._next_id(), role="human", speaker_id=PLAYER,
                            speaker=self.user, purpose="the player's line")

    # ------------------------------------------------------------------
    # who speaks next (when no director decides)

    def _pick_speaker(self) -> str:
        speakers = self._speakers()
        if not speakers:
            return NARRATOR
        last = next((t for t in reversed(self.turns) if t.kind != "narration"), None)
        last_id = last.speaker_id if last else ""
        if self.brief.order == "round_robin":
            speaker = speakers[self._rr % len(speakers)]
            self._rr += 1
            return speaker
        # Natural order: someone who was just named answers; otherwise roll
        # each other speaker's talkativeness and pick among those who pass.
        if last is not None:
            for speaker_id in speakers:
                if speaker_id == last_id:
                    continue
                card = self.cards[speaker_id]
                names = [card.name, card.nickname] if card.nickname else [card.name]
                if any(name and name in last.text for name in names):
                    return speaker_id
        others = [s for s in speakers if s != last_id] or speakers
        willing = [s for s in others
                   if self.rng.random() < self.cards[s].talkativeness]
        return self.rng.choice(willing or others)

    def _pick_cue(self) -> _Cue:
        narration = self.brief.narration
        if narration != "none" and not self.turns:
            return _Cue(NARRATOR)
        if narration == "normal" and self.takes and self.takes % 4 == 0 \
                and self.turns[-1].kind != "narration":
            return _Cue(NARRATOR)
        return _Cue(self._pick_speaker())

    def _chat_responders(self) -> list[str]:
        """After the player speaks: who answers, in order."""
        cast = [member.id for member in self.brief.cast]
        last = self.turns[-1].text if self.turns else ""
        named = [cid for cid in cast
                 if any(n and n in last for n in (self.cards[cid].name,
                                                  self.cards[cid].nickname))]
        if named:
            return named
        willing = [cid for cid in cast
                   if self.rng.random() < self.cards[cid].talkativeness]
        return willing[:2] or [self.rng.choice(cast)]

    # ------------------------------------------------------------------
    # the machine

    def _should_end(self) -> bool:
        if self.takes >= self.brief.max_turns:
            return True
        return bool(self.brief.beats) and self.beat >= len(self.brief.beats)

    def _finish(self) -> None:
        # A director cannot write, so an ending the author spelled out is the
        # narrator's to deliver; without a director the narrator always closes.
        wanted = bool(self.brief.ending.strip()) if self.brief.order == "director" \
            else True
        if (not self._closing_done and not self.chat and not self.is_restage
                and wanted and self.brief.narration != "none" and self.turns
                and self.turns[-1].speaker_id != NARRATOR):
            self._closing_done = True
            ending = self.brief.ending.strip()
            self._cue = _Cue(NARRATOR, note=self.T["narrator_close_cue"].format(
                ending=ending) if ending else self.T["default_ending"])
            self.phase = "act"
            return
        self.phase = "choices" if self.brief.choices else "done"

    def _open(self) -> None:
        self._opened = True
        for row in self.brief.opening.splitlines():
            if row.strip():
                self.turns.append(Turn(kind="narration", speaker_id=NARRATOR,
                                       text=row.strip(), fixed=True, take=self.takes))
        if self.chat:
            # A single character's greeting opens a chat, as in a tavern.
            cast = [member.id for member in self.brief.cast]
            if len(cast) == 1 and self.cards[cast[0]].first_mes.strip():
                self._add_reply_turns(cast[0], self._parse(cast[0], self.cards[
                    cast[0]].first_mes), note="", attempts=1, hits=[], fixed=True)
                self.takes += 1

    def _plan(self) -> StageRequest | None:
        if self.phase == "direct":
            if not self._opened:
                self._open()
            if self.is_restage:
                return self._plan_script()
            if self.chat:
                return self._plan_chat()
            if self._should_end():
                self._finish()
                return None
            if self.brief.order == "director":
                if not self.turns and self.brief.narration != "none":
                    # Someone has to say where we are before anyone acts in it.
                    self._cue = _Cue(NARRATOR)
                    self.phase = "act"
                    return None
                return self._director_request()
            self._cue = self._pick_cue()
            self.phase = "act"
            return None
        if self.phase == "act":
            cue = self._cue
            assert cue is not None
            if cue.speaker_id == NARRATOR:
                return self._narrator_request(cue)
            return self._actor_request(cue)
        if self.phase == "choices":
            if any(c.id not in self.choice_texts for c in self.brief.choices):
                return self._choices_request()
            self.phase = "done"
        return None

    def _plan_script(self) -> StageRequest | None:
        script = self.brief.script
        while self.script_pos < len(script):
            step = script[self.script_pos]
            if step.rewrite and (step.speaker_id in self.cards
                                 or step.speaker_id == NARRATOR):
                self._cue = _Cue(step.speaker_id, step=step)
                self.phase = "act"
                return None
            self._add_source_turn(step)
            self.script_pos += 1
        self.phase = "done"
        return None

    def _plan_chat(self) -> StageRequest | None:
        if self._chat_queue:
            self._cue = _Cue(self._chat_queue.pop(0))
            self.phase = "act"
            return None
        return self._human_request()

    def next_request(self) -> StageRequest | None:
        """The request waiting for an answer, or ``None`` when the scene is done."""
        if self._pending is not None:
            return self._pending
        for _ in range(10_000):
            if self.phase == "done":
                return None
            request = self._plan()
            if request is not None:
                self._pending = request
                return request
        raise StageError("stage did not converge")

    def submit(self, request_id: str, text: str) -> dict[str, Any]:
        """Answer the pending request. Returns what the stage made of it."""
        request = self._pending
        if request is None:
            raise StageError("no request is pending")
        if request.id != request_id:
            raise StageError(f"request id mismatch: pending '{request.id}', "
                             f"got '{request_id}'")
        self._pending = None
        text = text or ""
        if request.role == "director":
            return self._on_director(text)
        if request.role == "choices":
            return self._on_choices(text)
        if request.role == "human":
            return self._on_human(text)
        return self._on_performance(request, text)

    # -- director ----------------------------------------------------------

    def _match_speaker(self, raw: str) -> str | None:
        value = raw.strip().strip("<>＜＞「」[]【】").strip()
        folded = value.casefold()
        if not folded:
            return None
        if folded in (END, "結束", "结束", "完", "收", "stop", "finish"):
            return END
        if folded in (NARRATOR, "旁白", "敘述", "叙述", "narration"):
            return NARRATOR
        speakers = self._speakers()
        if folded in (PLAYER, "主角", "玩家") or value == self.user:
            return PLAYER if PLAYER in speakers else NARRATOR
        for speaker_id in speakers:
            card = self.cards[speaker_id]
            if folded == speaker_id.casefold() or value in (card.name, card.nickname):
                return speaker_id
        for speaker_id in speakers:
            card = self.cards[speaker_id]
            if speaker_id.casefold() in folded or (card.name and card.name in value):
                return speaker_id
        return None

    def _on_director(self, text: str) -> dict[str, Any]:
        public, _thoughts = strip_private(text)
        fields: dict[str, str] = {}
        stripped = public.strip()
        if stripped.startswith("{"):
            try:
                loaded = json.loads(stripped)
                if isinstance(loaded, dict):
                    fields = {_KV_KEYS.get(str(k).casefold(), str(k)): str(v)
                              for k, v in loaded.items()}
            except ValueError:
                fields = {}
        if not fields:
            for row in public.splitlines():
                match = _KV_RE.match(row)
                if match:
                    key = _KV_KEYS.get(match.group(1).casefold(), match.group(1))
                    fields.setdefault(key, match.group(2).strip())
        beats = self.brief.beats
        wants_next = fields.get("beat", "").strip().casefold().startswith(
            ("next", "下", "換", "换", "done", "完"))
        missing = self._missing_must()
        # A beat that still owes something gets two turns of grace before the
        # stage gives up on it; a director's "next" alone cannot skip it.
        forced = bool(beats) and self.beat < len(beats) \
            and self.beat_turns >= beats[self.beat].max_turns + (2 if missing else 0)
        if beats and self.beat < len(beats) and (forced or (wants_next and not missing)):
            self.beat += 1
            self.beat_turns = 0
        elif wants_next and missing:
            self._log("beat_held", beat=self.beat, missing=missing)
        speaker = self._match_speaker(fields.get("next", ""))
        note = fields.get("note", "").strip().strip("<>＜＞")
        pinned = False          # the stage chose this speaker itself; do not reassign
        if speaker is None:
            self._log("director_unparsed", raw=public.strip()[:200])
            self._cue = self._pick_cue()
        elif speaker == END and self._owed() and self.takes < self.brief.max_turns:
            # Not yet: the beats still owe the scene something.
            owed = self._owed()
            self._log("end_refused", beat=self.beat, missing=owed)
            cast = [member.id for member in self.brief.cast]
            self._cue = _Cue(cast[0] if cast else self._pick_speaker(),
                             note=self.T["must_mention"].format(
                                 items=self._sep.join(owed)))
            pinned = True
        elif speaker == END:
            self._log("director_end", why=fields.get("why", ""))
            self._finish()
            return {"accepted": True, "role": "director", "next": END}
        elif speaker == NARRATOR and self.brief.narration == "none":
            self._cue = _Cue(self._pick_speaker(), note="")
        else:
            self._cue = _Cue(speaker, note=note)
        cap = self.brief.max_consecutive
        streak_id, streak = self._streak()
        chosen = self._cue.speaker_id
        if cap and not pinned and chosen != NARRATOR and chosen == streak_id \
                and streak >= cap:
            others = [s for s in self._speakers() if s != chosen]
            if others:
                # Whoever has been quiet longest takes the turn, with no note:
                # the one written was for somebody else.
                spoken = [t.speaker_id for t in self.turns]
                others.sort(key=lambda s: len(spoken) - 1 - spoken[::-1].index(s)
                            if s in spoken else -1)
                self._log("director_overruled", wanted=chosen, gave=others[0],
                          streak=streak)
                self._cue = _Cue(others[0], note="")
        self._log("direction", next=self._cue.speaker_id, note=self._cue.note,
                  beat=self.beat, why=fields.get("why", ""))
        self.phase = "act"
        return {"accepted": True, "role": "director", "next": self._cue.speaker_id,
                "note": self._cue.note, "beat": self.beat}

    # -- performances ------------------------------------------------------

    def _parse(self, speaker_id: str, text: str) -> list[ParsedLine]:
        names = [card.name for card in self.cards.values()]
        if speaker_id == NARRATOR:
            parsed = parse_reply(text, default_speaker=None, known_speakers=names,
                                 style=self.style, bare_is_speech=False)
        else:
            parsed = parse_reply(text, default_speaker=self._name(speaker_id),
                                 known_speakers=names, style=self.style)
        return parsed.lines

    def _public_text(self, speaker_id: str, lines: list[ParsedLine]) -> str:
        own = self._name(speaker_id)
        rows = []
        for line in lines:
            if line.kind == "narration":
                rows.append(self.T["narration_line"].format(text=line.text))
            elif line.speaker == own:
                rows.append(f"{self.style.quote_open}{line.text}{self.style.quote_close}")
            else:
                rows.append(self.T["speech_line"].format(name=line.speaker,
                                                         text=line.text))
        return "\n".join(rows)

    def _judge(self, cue: _Cue, text: str) -> _Candidate:
        speaker_id = cue.speaker_id
        own = self._name(speaker_id)
        lines = self._parse(speaker_id, text)
        if self.style.locale == "zh-Hant":
            # One or two leaked characters are not worth a request: fix them
            # here. A line that has really drifted is still sent back below.
            for index, line in enumerate(lines):
                mended = repair_slip(line.text)
                if mended is not None:
                    self._log("script_repaired", speaker=speaker_id, was=line.text,
                              now=mended)
                    lines[index] = replace(line, text=mended)
        others = [card.name for cid, card in self.cards.items() if cid != speaker_id]
        hits = self.linter.lint_turn(
            self._public_text(speaker_id, lines),
            speaker=None if speaker_id == NARRATOR else own, others=others,
            max_lines=self.brief.lines_per_turn + 1,
            # A restaged scene keeps its authored structure, parallel variants
            # of one line included, so only fresh scenes are checked for loops.
            previous=[] if cue.step is not None else [t.text for t in self.turns],
            require_speech=speaker_id != NARRATOR and not self.chat)
        strays = [line for line in lines if line.kind == "dialogue"
                  and line.speaker != (own or None)]
        if strays:
            who = strays[0].speaker or "?"
            label, hint = self.linter._msg.get("fmt.speaks_for_other", ("", ""))
            if not any(hit.rule == "fmt.speaks_for_other" for hit in hits):
                hits.append(SlopHit(rule="fmt.speaks_for_other",
                                    label=label.format(who=who), hint=hint,
                                    weight=6.0, hard=True))
        step = cue.step
        if step is not None and step.keep:
            body = "".join(line.text for line in lines)
            missing = [token for token in step.keep if token not in body]
            if missing:
                hits.append(SlopHit(rule="keep.missing", hard=True, weight=6.0,
                                    label=self.T["keep_missing"].format(
                                        tokens=self._sep.join(missing))))
        if step is not None:
            # A rewrite far shorter than its source has dropped something the
            # scene needed, however natural it sounds.
            source = len(normalize_line(step.source))
            now = len(normalize_line("".join(
                line.text for line in lines
                if line.kind == ("narration" if speaker_id == NARRATOR else "dialogue"))))
            if source >= 8 and 0 < now < source * 0.5:
                hits.append(SlopHit(rule="restage.lost", hard=True, weight=6.0,
                                    label=self.T["restage_lost"].format(source=source,
                                                                        now=now)))
        return _Candidate(text=text, lines=lines, hits=hits)

    def _on_performance(self, request: StageRequest, text: str) -> dict[str, Any]:
        cue = self._cue
        assert cue is not None
        public, _thoughts = strip_private(text)
        candidate = self._judge(cue, public)
        self._candidates.append(candidate)
        retake = self.linter.needs_retake(candidate.hits, self.retake_threshold)
        if retake and self._attempt <= self.brief.retakes:
            self._retake = self.linter.retake_note(candidate.hits)
            self._log("retake", speaker=cue.speaker_id, attempt=self._attempt,
                      hits=[hit.rule for hit in candidate.hits], text=public.strip())
            self._attempt += 1
            return {"accepted": False, "retake": True, "attempt": self._attempt,
                    "hits": [hit.to_dict() for hit in candidate.hits],
                    "note": self._retake}
        best = min(self._candidates, key=lambda c: c.rank())
        attempts = len(self._candidates)
        added = self._add_reply_turns(cue.speaker_id, best.lines, note=cue.note,
                                      attempts=attempts, hits=best.hits, step=cue.step)
        self._candidates = []
        self._attempt = 1
        self._retake = ""
        self._cue = None
        self.takes += 1
        self.beat_turns += 1
        if cue.step is not None:
            self.script_pos += 1
        beats = self.brief.beats
        if (self.brief.order != "director" and beats and self.beat < len(beats)
                and self.beat_turns >= beats[self.beat].max_turns):
            self.beat += 1
            self.beat_turns = 0
        self.phase = "direct"
        if self._closing_done:
            self.phase = "choices" if self.brief.choices else "done"
        return {"accepted": True, "retake": False, "attempts": attempts,
                "turns": [turn.model_dump(exclude_defaults=True) for turn in added],
                "hits": [hit.to_dict() for hit in best.hits], "done": self.done}

    def _salvage(self, speaker_id: str, lines: list[ParsedLine]) -> list[ParsedLine]:
        """Drop the rows of an accepted take that are still plainly broken.

        Retakes run out. When they do, the least bad take is kept, and it can
        still hold a row that repeats an earlier line word for word or slips
        into the other script. A Simplified slip in Traditional text is
        converted character by character; a repeated row is dropped. A take
        that is nothing but
        such rows leaves nothing behind.
        """
        seen = [normalize_line(turn.text) for turn in self.turns]
        repaired: list[ParsedLine] = []
        kept: list[ParsedLine] = []
        dropped: list[str] = []
        for line in lines:
            if self.style.locale == "zh-Hant" and self.linter.other_script(line.text):
                fixed_text = to_traditional(line.text)
                self._log("script_repaired", speaker=speaker_id, was=line.text,
                          now=fixed_text)
                line = replace(line, text=fixed_text)
            if line.kind == "narration":
                # Compare the row as it will be written, subject included.
                line = replace(line, text=self._repair_action(speaker_id, line.text))
            repaired.append(line)
            key = normalize_line(line.text)
            # Exact repeats only: dropping a row is not reversible, so a line
            # that merely resembles an earlier one is left to the retake.
            broken = repeats_earlier(key, seen, cjk=self._cjk, near=False) \
                or bool(self.linter.other_script(line.text))
            if broken:
                dropped.append(line.text)
            else:
                kept.append(line)
                seen.append(key)
        if not dropped:
            return repaired
        # A turn that only repeats what was already said adds nothing: better
        # that it leaves no rows than that the scene says a line twice.
        self._log("rows_dropped", speaker=speaker_id, rows=dropped)
        return kept

    def _add_reply_turns(self, speaker_id: str, lines: list[ParsedLine], *, note: str,
                         attempts: int, hits: list[SlopHit],
                         step: ScriptStep | None = None, fixed: bool = False
                         ) -> list[Turn]:
        own = self._name(speaker_id)
        resolver = self._resolver(speaker_id) if speaker_id != NARRATOR else None
        take = self.takes
        added: list[Turn] = []
        spoken = 0
        acted = False
        hit_dicts = [hit.to_dict() for hit in hits]
        if speaker_id != NARRATOR and not fixed and step is None:
            lines = self._salvage(speaker_id, lines)
        speaks = any(line.kind == "dialogue" and line.speaker == own for line in lines)
        # An action row the speaker was not offered is dropped, unless it is
        # all they gave (better a gesture than an empty turn).
        action_ok = (speaker_id == NARRATOR or fixed or not speaks
                     or self._action_allowed(speaker_id))
        previous_key = next((t.expression for t in reversed(self.turns)
                             if t.speaker_id == speaker_id and t.expression is not None),
                            None)
        if step is not None:
            turn = self._restage_turn(speaker_id, lines, step, attempts, hits)
            self.turns.append(turn)
            return [turn]
        for line in lines:
            if line.kind == "dialogue":
                if speaker_id == NARRATOR or line.speaker != own:
                    continue                  # never keep words put in another mouth
                if spoken >= self.brief.lines_per_turn:
                    continue
                spoken += 1
                turn = Turn(kind="player" if speaker_id == PLAYER else "dialogue",
                            speaker_id=speaker_id, speaker=own, text=line.text,
                            take=take, expression_word=line.sprite, beat=self.beat,
                            note=note, attempts=attempts, fixed=fixed)
                if resolver is not None:
                    if line.sprite:
                        resolution = resolver.resolve(line.sprite)
                        turn.expression_source = resolution.source
                        turn.expression = resolution.key if resolution.resolved \
                            else previous_key
                    else:
                        turn.expression = previous_key
                    previous_key = turn.expression
            else:
                if speaker_id != NARRATOR and (acted or not action_ok):
                    continue                  # one action row per actor take
                acted = True
                text = line.text if speaker_id == NARRATOR or fixed \
                    else self._repair_action(speaker_id, line.text)
                turn = Turn(kind="narration", speaker_id=speaker_id, text=text,
                            take=take, beat=self.beat, note=note, attempts=attempts,
                            fixed=fixed)
            for key in ("bg", "bgm", "cg", "sfx"):
                setattr(turn, key, getattr(line, key))
            added.append(turn)
        if added:
            added[0].hits = hit_dicts
        self.turns.extend(added)
        return added

    def _restage_turn(self, speaker_id: str, lines: list[ParsedLine], step: ScriptStep,
                      attempts: int, hits: list[SlopHit]) -> Turn:
        """Collapse a restaged reply to exactly one line, or keep the original."""
        own = self._name(speaker_id)
        if speaker_id == NARRATOR:
            parts = [line.text for line in lines if line.kind == "narration"]
        else:
            parts = [line.text for line in lines
                     if line.kind == "dialogue" and line.speaker == own]
        text = _join_parts(parts, cjk=self.style.locale.startswith(("zh", "ja")))
        fallback = not text or any(hit.hard for hit in hits)
        word = next((line.sprite for line in lines if line.sprite), "")
        turn = Turn(
            kind="narration" if speaker_id == NARRATOR
            else ("player" if speaker_id == PLAYER else "dialogue"),
            speaker_id=speaker_id, speaker=step.speaker or own,
            text=step.source if fallback else text, take=self.takes,
            expression_word="" if fallback else word, attempts=attempts,
            hits=[hit.to_dict() for hit in hits], source_index=step.line_index,
            source_text=step.source, quoted=step.quoted, fallback=fallback)
        if fallback:
            self._log("restage_fallback", line=step.line_index,
                      hits=[hit.rule for hit in hits])
        return turn

    def _add_source_turn(self, step: ScriptStep) -> None:
        kind = "narration" if step.speaker_id == NARRATOR else (
            "player" if step.speaker_id == PLAYER else "dialogue")
        self.turns.append(Turn(
            kind=kind, speaker_id=step.speaker_id, speaker=step.speaker,
            text=step.source, take=self.takes, source_index=step.line_index,
            source_text=step.source, quoted=step.quoted, fixed=True))
        self.takes += 1

    # -- the player and the choices ----------------------------------------

    def _on_human(self, text: str) -> dict[str, Any]:
        body = text.strip()
        if not body:
            return {"accepted": False, "role": "human", "reason": "empty"}
        lines = parse_reply(body, default_speaker=self.user, style=self.style).lines
        added: list[Turn] = []
        for line in lines:
            kind = "player" if line.kind == "dialogue" else "narration"
            added.append(Turn(kind=kind, speaker_id=PLAYER,
                              speaker=self.user if kind == "player" else "",
                              text=line.text, take=self.takes, fixed=True))
        self.turns.extend(added)
        self.takes += 1
        if self.chat:
            self._chat_queue = self._chat_responders()
        self.phase = "direct"
        return {"accepted": True, "role": "human",
                "turns": [turn.model_dump(exclude_defaults=True) for turn in added]}

    def _on_choices(self, text: str) -> dict[str, Any]:
        public, _thoughts = strip_private(text)
        known = {c.id for c in self.brief.choices}
        for row in public.splitlines():
            row = row.strip().lstrip("-*・").strip()
            if not row:
                continue
            parts = re.split(r"\s*[｜|]\s*", row, maxsplit=2)
            if len(parts) < 2:
                parts = re.split(r"\s*[:：]\s*", row, maxsplit=1)
            if len(parts) >= 2 and parts[0].strip() in known:
                wording = parts[1].strip().strip("「」\"“”")
                if wording:
                    self.choice_texts.setdefault(parts[0].strip(), wording)
        missing = [c.id for c in self.brief.choices if c.id not in self.choice_texts]
        for choice in self.brief.choices:
            # Never leave a choice without wording: fall back to its intent.
            self.choice_texts.setdefault(choice.id, choice.intent or choice.id)
        if missing:
            self._log("choices_unparsed", missing=missing)
        self.phase = "done"
        return {"accepted": True, "role": "choices", "choices": dict(self.choice_texts),
                "fell_back": missing}

    # ------------------------------------------------------------------
    # chat conveniences

    def swipe(self) -> bool:
        """Discard the last generated take so it is performed again."""
        if not self.turns or self._pending is not None:
            return False
        last = self.turns[-1]
        if last.fixed:
            return False
        take, speaker_id = last.take, last.speaker_id
        while self.turns and self.turns[-1].take == take \
                and self.turns[-1].speaker_id == speaker_id:
            self.turns.pop()
        self.takes = max(0, self.takes - 1)
        self._cue = _Cue(speaker_id)
        self.phase = "act"
        return True

    def inspect(self) -> dict[str, Any] | None:
        """The prompt report of the most recent character request."""
        return self._last_build.to_dict(include_messages=False) \
            if self._last_build else None

    # ------------------------------------------------------------------
    # results

    def transcript(self) -> list[dict[str, Any]]:
        return [turn.model_dump(exclude_defaults=True) for turn in self.turns]

    def _line_for(self, turn: Turn, warnings: list[str]) -> dict[str, Any]:
        style = self.style
        line: dict[str, Any] = {}
        if turn.kind == "narration":
            line["text"] = turn.text
        else:
            if turn.kind == "player":
                persona = self.ctx.tavern.persona
                if not persona.as_narration:
                    line["speaker"] = persona.speaker or self.user
            else:
                line["speaker"] = turn.speaker
            line["text"] = f"{style.quote_open}{turn.text}{style.quote_close}"
            if turn.expression:
                line["expression"] = turn.expression
        effects: list[dict[str, Any]] = []
        assets = self.ctx.assets
        if turn.bg:
            asset = assets.resolve("bg", turn.bg)
            if asset is not None:
                effects.append({"kind": "set_background", "target": asset.path})
            else:
                warnings.append(f"unknown background '{turn.bg}' dropped")
        if turn.bgm:
            asset = assets.resolve("bgm", turn.bgm)
            if asset is not None:
                line["bgm"] = asset.path
            else:
                warnings.append(f"unknown music '{turn.bgm}' dropped")
        if turn.cg == "off":
            effects.append({"kind": "hide_cg"})
        elif turn.cg:
            asset = assets.resolve("cg", turn.cg)
            if asset is not None:
                line["cg"] = asset.path
            else:
                warnings.append(f"unknown CG '{turn.cg}' dropped")
        if turn.sfx:
            asset = assets.resolve("sfx", turn.sfx)
            if asset is not None:
                line["sfx"] = asset.path
            else:
                warnings.append(f"unknown sound '{turn.sfx}' dropped")
        if effects:
            line["effects"] = effects
        return line

    def to_scene(self) -> tuple[dict[str, Any], list[str]]:
        """The finished scene as a ``PackEditor.add_scene`` payload + warnings."""
        brief = self.brief
        warnings: list[str] = []
        scene: dict[str, Any] = {"id": brief.id}
        if brief.title:
            scene["title"] = brief.title
        if brief.location:
            scene["location"] = brief.location
        background = brief.background or self.ctx.location_background(brief.location)
        if background:
            scene["background"] = background
        for key in ("bgm", "ambient", "route"):
            value = getattr(brief, key)
            if value:
                scene[key] = value
        if brief.tags:
            scene["tags"] = list(brief.tags)
        if brief.once is not None:
            scene["once"] = brief.once
        if brief.requires:
            scene["requires"] = list(brief.requires)
        scene["lines"] = [self._line_for(turn, warnings) for turn in self.turns]
        if brief.choices:
            choices = []
            for option in brief.choices:
                choice: dict[str, Any] = {
                    "id": option.id,
                    "text": self.choice_texts.get(option.id) or option.intent or option.id,
                }
                for key in ("effects", "requires", "forbids"):
                    value = getattr(option, key)
                    if value:
                        choice[key] = list(value)
                if option.next_scene:
                    choice["next_scene"] = option.next_scene
                choices.append(choice)
            scene["choices"] = choices
        if brief.on_end:
            scene["on_end"] = list(brief.on_end)
        return scene, warnings

    def to_edits(self, *, expressions: bool = False) -> list[dict[str, Any]]:
        """Restage result as ``edit.update_line`` ops (changed lines only)."""
        if not self.is_restage:
            raise StageError("to_edits() is only for a restaged scene")
        style = self.style
        edits: list[dict[str, Any]] = []
        for turn in self.turns:
            if turn.source_index is None or turn.fixed or turn.fallback:
                continue
            if turn.text.strip() == turn.source_text.strip():
                continue
            text = f"{style.quote_open}{turn.text}{style.quote_close}" if turn.quoted \
                else turn.text
            updates: dict[str, Any] = {"text": text}
            if expressions and turn.expression:
                updates["expression"] = turn.expression
            edits.append({"op": "edit.update_line", "scene_id": self.brief.restage_of,
                          "line_index": turn.source_index, "updates": updates})
        return edits

    def review(self) -> SlopReport:
        """Lint the transcript as it stands."""
        rows = [{"speaker": None if t.kind == "narration" else t.speaker,
                 "text": t.text if t.kind == "narration"
                 else f"{self.style.quote_open}{t.text}{self.style.quote_close}"}
                for t in self.turns]
        return self.linter.lint(lines_from_scene({"id": self.brief.id, "lines": rows}))

    def summary(self) -> dict[str, Any]:
        retaken = sum(1 for turn in self.turns if turn.attempts > 1)
        report = self.review()
        out: dict[str, Any] = {
            "id": self.brief.id, "done": self.done, "phase": self.phase,
            "lines": len(self.turns), "takes": self.takes, "retaken_lines": retaken,
            "slop": {"score": round(report.score, 1), "grade": report.grade,
                     "by_rule": report.by_rule()},
        }
        if self.is_restage:
            changed = len(self.to_edits())
            out["restage"] = {
                "scene": self.brief.restage_of, "changed": changed,
                "kept_original": sum(1 for t in self.turns if t.fallback),
                "skipped": {step.skip_reason: 0 for step in self.brief.script
                            if step.skip_reason},
            }
            for step in self.brief.script:
                if step.skip_reason:
                    out["restage"]["skipped"][step.skip_reason] += 1
        return out

    # ------------------------------------------------------------------
    # persistence

    def to_dict(self) -> dict[str, Any]:
        state = self.rng.getstate()
        cue = self._cue
        return {
            "version": 1,
            "brief": self.brief.model_dump(exclude_defaults=False),
            "chat": self.chat,
            "preset": self.preset.id,
            "phase": self.phase, "beat": self.beat, "beat_turns": self.beat_turns,
            "takes": self.takes, "script_pos": self.script_pos,
            "turns": [turn.model_dump() for turn in self.turns],
            "variables": self.variables, "choice_texts": self.choice_texts,
            "events": self.events,
            "cue": None if cue is None else {
                "speaker_id": cue.speaker_id, "note": cue.note,
                "step": cue.step.line_index if cue.step else None},
            "pending": self._pending.model_dump() if self._pending else None,
            "attempt": self._attempt, "retake": self._retake,
            "candidates": [c.text for c in self._candidates],
            "counter": self._counter, "opened": self._opened,
            "closing_done": self._closing_done, "chat_queue": self._chat_queue,
            "rr": self._rr,
            "rng": [state[0], list(state[1]), state[2]],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], ctx: StageContext, *,
                  preset: Preset | None = None) -> "Stage":
        brief = SceneBrief.model_validate(data["brief"])
        if preset is None:
            try:
                preset = ctx.tavern.preset(data.get("preset") or None)
            except KeyError:
                preset = None
        stage = cls(brief, ctx, preset=preset, chat=bool(data.get("chat", False)))
        stage.phase = data.get("phase", "direct")
        stage.beat = int(data.get("beat", 0))
        stage.beat_turns = int(data.get("beat_turns", 0))
        stage.takes = int(data.get("takes", 0))
        stage.script_pos = int(data.get("script_pos", 0))
        stage.turns = [Turn.model_validate(item) for item in data.get("turns", [])]
        stage.variables = dict(data.get("variables") or {})
        stage.choice_texts = dict(data.get("choice_texts") or {})
        stage.events = list(data.get("events") or [])
        cue = data.get("cue")
        if cue:
            step = None
            if cue.get("step") is not None:
                step = next((s for s in brief.script
                             if s.line_index == cue["step"]), None)
            stage._cue = _Cue(cue["speaker_id"], cue.get("note", ""), step)
        pending = data.get("pending")
        stage._pending = StageRequest.model_validate(pending) if pending else None
        stage._attempt = int(data.get("attempt", 1))
        stage._retake = str(data.get("retake", ""))
        if stage._cue is not None:
            stage._candidates = [stage._judge(stage._cue, text)
                                 for text in data.get("candidates") or []]
        stage._counter = int(data.get("counter", 0))
        stage._opened = bool(data.get("opened", False))
        stage._closing_done = bool(data.get("closing_done", False))
        stage._chat_queue = list(data.get("chat_queue") or [])
        stage._rr = int(data.get("rr", 0))
        rng = data.get("rng")
        if rng:
            stage.rng.setstate((rng[0], tuple(rng[1]), rng[2]))
        return stage


def _keep_tokens(text: str, entity_names: list[str]) -> list[str]:
    """Wording a rewrite must carry over verbatim."""
    tokens: list[str] = []
    for pattern in (_TOKEN_RE, _OP_RE, _TITLE_RE, _NUMBER_RE):
        tokens += pattern.findall(text)
    covered = "".join(tokens)
    for name in entity_names:
        if name in text and name not in covered:
            tokens.append(name)
            covered += name
    return list(dict.fromkeys(tokens))


def _join_parts(parts: list[str], *, cjk: bool) -> str:
    """Join the pieces of one reply back into a single line."""
    out = ""
    for part in (p.strip() for p in parts):
        if not part:
            continue
        if not out:
            out = part
        elif cjk:
            out += part if out[-1] in _SENT_END else "，" + part
        else:
            out += " " + part
    return out
