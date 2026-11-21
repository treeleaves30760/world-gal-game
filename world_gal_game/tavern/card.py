"""Character cards: *who a character is and how they talk*, in tavern form.

A :class:`CharacterCard` is a superset of the community Character Card spec
(V1 flat JSON, V2 ``chara_card_v2``, V3 ``chara_card_v3``) that tavern-style
front-ends exchange as ``.json`` files or as PNGs with the card embedded in a
text chunk. Anything written for those front-ends drops straight into a pack
(``content/tavern/cards/``) and anything authored here exports back out.

On top of the spec fields the engine adds the things that actually decide
whether generated dialogue sounds like a person or like a language model:

- :class:`SpeechProfile` — diction, sentence length, particles, how the
  character addresses others, what they would never say;
- ``secrets`` / ``knowledge`` / ``goals`` — *private* information that only the
  agent playing this character is ever shown (see ``tavern.stage``), which is
  what gives a multi-agent scene real information asymmetry;
- ``affection_voices`` — how the voice shifts as the relationship deepens;
- ``expression_aliases`` — the words a writer might use for each portrait
  expression, so free-form emotion words resolve to real art.

Engine-only fields round-trip through the spec's ``extensions`` bag under the
``world_gal_game`` key, so exporting to a tavern front-end and re-importing
loses nothing.

Cards are *content*, and tavern cards are routinely downloaded from strangers.
Loading one never executes anything: the card is text that ends up inside a
prompt. Macros inside a card can only touch tavern-local variables, never game
state (see ``tavern.macros``).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..core.story_graph import Condition
from . import png_meta

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..npc.npc_base import NPC

#: Key under a tavern card's ``extensions`` where engine-only fields live.
EXTENSION_KEY = "world_gal_game"

Role = Literal["system", "user", "assistant"]

LorePosition = Literal[
    "before_char",       # world info (before) marker
    "after_char",        # world info (after) marker
    "before_examples",   # just above the example dialogue
    "after_examples",    # just below the example dialogue
    "an_top",            # top of the author's note
    "an_bottom",         # bottom of the author's note
    "at_depth",          # inside chat history, N messages from the end
    "outlet",            # only via {{outlet::name}}
]

LoreLogic = Literal["and_any", "and_all", "not_any", "not_all"]

# Numeric enums the most common tavern front-end stores in ``extensions``.
_ST_POSITION = {
    0: "before_char", 1: "after_char", 2: "an_top", 3: "an_bottom",
    4: "at_depth", 5: "before_examples", 6: "after_examples", 7: "outlet",
}
_ST_POSITION_REV = {v: k for k, v in _ST_POSITION.items()}
_ST_LOGIC = {0: "and_any", 1: "not_all", 2: "not_any", 3: "and_all"}
_ST_LOGIC_REV = {v: k for k, v in _ST_LOGIC.items()}
_ST_ROLE = {0: "system", 1: "user", 2: "assistant"}
_ST_ROLE_REV = {v: k for k, v in _ST_ROLE.items()}


def _conditions(value: Any) -> list[Condition]:
    """Accept the same shorthand strings scene YAML accepts for conditions."""
    from ..dialogue.script_loader import _to_conditions
    if value is None:
        return []
    if isinstance(value, list) and all(isinstance(v, Condition) for v in value):
        return value
    return _to_conditions(value)


def _str_list(value: Any) -> list[str]:
    """Coerce ``None`` / a scalar / a comma string into a list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v is not None and str(v) != ""]
    return [str(value)]


class LoreEntry(BaseModel):
    """One world-book entry: text injected when its keys come up in the chat."""

    model_config = ConfigDict(extra="forbid")

    id: str = ""
    name: str = ""
    keys: list[str] = Field(default_factory=list)
    secondary_keys: list[str] = Field(default_factory=list)
    logic: LoreLogic = "and_any"
    content: str = ""
    enabled: bool = True
    constant: bool = False            # always on, no key needed
    order: int = 100                  # lower = earlier in its block
    priority: int | None = None       # budget: lower is dropped first
    position: LorePosition = "before_char"
    depth: int = 4                    # for at_depth
    role: Role = "system"             # for at_depth
    outlet: str = ""                  # for position=outlet
    probability: int = Field(default=100, ge=0, le=100)
    case_sensitive: bool = False
    whole_word: bool = False          # Latin-script keys only; ignored for CJK
    group: str = ""                   # inclusion group: one winner per group
    scan_depth: int | None = None     # override the book's scan depth
    exclude_recursion: bool = False   # not triggered by other entries' text
    prevent_recursion: bool = False   # its text does not trigger others
    # Engine extension: gate an entry on game state, with the same condition
    # vocabulary scenes use. This is what lets one world book follow a route.
    requires: list[Condition] = Field(default_factory=list)
    forbids: list[Condition] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("keys", "secondary_keys", mode="before")
    @classmethod
    def _keys(cls, value: Any) -> list[str]:
        return _str_list(value)

    @field_validator("requires", "forbids", mode="before")
    @classmethod
    def _conds(cls, value: Any) -> list[Condition]:
        return _conditions(value)

    @field_validator("id", mode="before")
    @classmethod
    def _id(cls, value: Any) -> str:
        return "" if value is None else str(value)

    # -- tavern interchange ------------------------------------------------

    @classmethod
    def from_tavern(cls, raw: dict[str, Any]) -> "LoreEntry":
        """Build from a card-book entry *or* a standalone world-file entry."""
        ext = raw.get("extensions") if isinstance(raw.get("extensions"), dict) else {}

        def pick(*names: str, default: Any = None) -> Any:
            for source in (raw, ext):
                for name in names:
                    if name in source and source[name] is not None:
                        return source[name]
            return default

        position: Any = raw.get("position", ext.get("position"))
        if isinstance(ext.get("position"), int):
            position = ext["position"]          # the numeric one is authoritative
        if isinstance(position, int):
            position = _ST_POSITION.get(position, "before_char")
        if position not in _ST_POSITION_REV:
            position = "before_char"

        logic: Any = pick("selectiveLogic", "selective_logic", default=0)
        if isinstance(logic, int):
            logic = _ST_LOGIC.get(logic, "and_any")
        if logic not in _ST_LOGIC_REV:
            logic = "and_any"

        role: Any = pick("role", default=0)
        if isinstance(role, int):
            role = _ST_ROLE.get(role, "system")
        if role not in _ST_ROLE_REV:
            role = "system"

        secondary = _str_list(pick("secondary_keys", "keysecondary", default=[]))
        selective = raw.get("selective")
        if selective is False:
            secondary = []   # the spec says secondary keys only count when selective

        probability = pick("probability", default=100)
        if pick("useProbability", default=True) is False:
            probability = 100
        try:
            probability = max(0, min(100, int(probability)))
        except (TypeError, ValueError):
            probability = 100

        enabled = raw.get("enabled")
        if enabled is None:
            enabled = not bool(raw.get("disable", False))

        def as_int(value: Any, default: int | None) -> int | None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        known = {
            "position", "depth", "probability", "useProbability", "selectiveLogic",
            "selective_logic", "group", "role", "scan_depth", "match_whole_words",
            "case_sensitive", "exclude_recursion", "prevent_recursion", "outlet_name",
        }
        return cls(
            id=str(pick("id", "uid", default="") or ""),
            name=str(pick("name", "comment", default="") or ""),
            keys=_str_list(pick("keys", "key", default=[])),
            secondary_keys=secondary,
            logic=logic,
            content=str(raw.get("content") or ""),
            enabled=bool(enabled),
            constant=bool(raw.get("constant", False)),
            order=as_int(pick("insertion_order", "order", default=100), 100) or 0,
            priority=as_int(raw.get("priority"), None),
            position=position,
            depth=as_int(pick("depth", default=4), 4) or 0,
            role=role,
            outlet=str(pick("outlet_name", "outletName", default="") or ""),
            probability=probability,
            case_sensitive=bool(pick("case_sensitive", "caseSensitive", default=False)),
            whole_word=bool(pick("match_whole_words", "matchWholeWords", default=False)),
            group=str(pick("group", default="") or ""),
            scan_depth=as_int(pick("scan_depth", "scanDepth", default=None), None),
            exclude_recursion=bool(pick("exclude_recursion", "excludeRecursion",
                                        default=False)),
            prevent_recursion=bool(pick("prevent_recursion", "preventRecursion",
                                        default=False)),
            extensions={k: v for k, v in ext.items() if k not in known},
        )

    def to_tavern(self) -> dict[str, Any]:
        """Serialise as a card-book entry, keeping the front-end extensions."""
        ext = dict(self.extensions)
        ext.update({
            "position": _ST_POSITION_REV[self.position],
            "depth": self.depth,
            "probability": self.probability,
            "useProbability": self.probability < 100,
            "selectiveLogic": _ST_LOGIC_REV[self.logic],
            "group": self.group,
            "role": _ST_ROLE_REV[self.role],
            "scan_depth": self.scan_depth,
            "match_whole_words": self.whole_word,
            "case_sensitive": self.case_sensitive,
            "exclude_recursion": self.exclude_recursion,
            "prevent_recursion": self.prevent_recursion,
        })
        if self.outlet:
            ext["outlet_name"] = self.outlet
        if self.requires or self.forbids:
            ext[EXTENSION_KEY] = {
                "requires": [c.model_dump(exclude_none=True) for c in self.requires],
                "forbids": [c.model_dump(exclude_none=True) for c in self.forbids],
            }
        out: dict[str, Any] = {
            "keys": list(self.keys),
            "content": self.content,
            "extensions": ext,
            "enabled": self.enabled,
            "insertion_order": self.order,
            "case_sensitive": self.case_sensitive,
            "name": self.name,
            "comment": self.name,
            "selective": bool(self.secondary_keys),
            "secondary_keys": list(self.secondary_keys),
            "constant": self.constant,
            # The V2 spec only knows two positions; the precise one is in ext.
            "position": "after_char" if self.position == "after_char" else "before_char",
        }
        if self.priority is not None:
            out["priority"] = self.priority
        if self.id:
            out["id"] = int(self.id) if self.id.isdigit() else self.id
        return out


class Lorebook(BaseModel):
    """A world book (世界書): keyword-triggered background knowledge."""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    description: str = ""
    scan_depth: int = Field(default=4, ge=0)        # recent messages scanned
    token_budget: int = Field(default=1024, ge=0)   # 0 = unlimited
    recursive: bool = False
    entries: list[LoreEntry] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_tavern(cls, raw: dict[str, Any]) -> "Lorebook":
        """Accept a card's ``character_book`` or a standalone world-info file.

        The standalone format stores ``entries`` as ``{"0": {...}, "1": {...}}``;
        the card format stores a list. Both are handled.
        """
        entries_raw = raw.get("entries") or []
        if isinstance(entries_raw, dict):
            def sort_key(item: tuple[str, Any]) -> tuple[int, str]:
                key = str(item[0])
                return (int(key), "") if key.isdigit() else (1 << 30, key)
            entries_raw = [v for _k, v in sorted(entries_raw.items(), key=sort_key)]
        entries: list[LoreEntry] = []
        for item in entries_raw:
            if not isinstance(item, dict):
                continue
            entry = LoreEntry.from_tavern(item)
            ours = entry.extensions.pop(EXTENSION_KEY, None)
            if isinstance(ours, dict):
                entry.requires = _conditions(ours.get("requires"))
                entry.forbids = _conditions(ours.get("forbids"))
            entries.append(entry)

        def as_int(value: Any, default: int) -> int:
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                return default

        return cls(
            name=str(raw.get("name") or ""),
            description=str(raw.get("description") or ""),
            scan_depth=as_int(raw.get("scan_depth"), 4),
            token_budget=as_int(raw.get("token_budget"), 1024),
            recursive=bool(raw.get("recursive_scanning", False)),
            entries=entries,
            extensions=dict(raw.get("extensions") or {})
            if isinstance(raw.get("extensions"), dict) else {},
        )

    def to_tavern(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "scan_depth": self.scan_depth,
            "token_budget": self.token_budget,
            "recursive_scanning": self.recursive,
            "extensions": dict(self.extensions),
            "entries": [entry.to_tavern() for entry in self.entries],
        }

    @classmethod
    def load(cls, path: Path) -> "Lorebook":
        """Load ``.yaml`` (native) or ``.json`` (tavern world file / card book)."""
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            raw = json.loads(text)
            if not isinstance(raw, dict):
                raise ValueError(f"{path.name}: world book JSON must be an object")
            return cls.from_tavern(raw)
        raw = yaml.safe_load(text) or {}
        if isinstance(raw, list):
            raw = {"entries": raw}
        if not isinstance(raw, dict):
            raise ValueError(f"{path.name}: world book YAML must be a mapping or a list")
        if "recursive_scanning" in raw or isinstance(raw.get("entries"), dict):
            return cls.from_tavern(raw)
        return cls.model_validate(raw)


class SpeechProfile(BaseModel):
    """How a character actually talks — the part adjectives cannot carry.

    Every field is optional prose or a short list. They are rendered into the
    acting prompt as observations ("usually", "when nervous"), never as rules
    to perform in every line: a quirk stated as a rule gets performed in every
    sentence, which is its own tell.
    """

    model_config = ConfigDict(extra="forbid")

    diction: str = ""                  # spoken register: word choice, formality
    sentence_length: str = ""          # typical length and rhythm
    self_reference: str = ""           # how they say "I"
    address: dict[str, str] = Field(default_factory=dict)   # who -> what they call them
    particles: list[str] = Field(default_factory=list)      # sentence-final particles
    fillers: list[str] = Field(default_factory=list)        # hesitation / discourse words
    catchphrases: list[str] = Field(default_factory=list)   # rare, memorable turns
    habits: list[str] = Field(default_factory=list)         # behavioural verbal habits
    when: dict[str, str] = Field(default_factory=dict)      # mood -> how speech changes
    never_says: list[str] = Field(default_factory=list)     # out-of-character phrases
    avoid_topics: list[str] = Field(default_factory=list)   # what they steer away from

    @field_validator("particles", "fillers", "catchphrases", "habits",
                     "never_says", "avoid_topics", mode="before")
    @classmethod
    def _lists(cls, value: Any) -> list[str]:
        return _str_list(value)

    def is_empty(self) -> bool:
        return not any(getattr(self, name) for name in type(self).model_fields)


class AffectionVoice(BaseModel):
    """How the character's manner changes at or above an affection value."""

    model_config = ConfigDict(extra="forbid")

    min: int = 0
    note: str


class DepthPrompt(BaseModel):
    """A per-character note injected inside the chat history (角色備註)."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = ""
    depth: int = Field(default=4, ge=0)
    role: Role = "system"


class ExampleTurn(BaseModel):
    """One line of a parsed example dialogue."""

    speaker: Literal["char", "user", "other"]
    name: str
    text: str


_EXAMPLE_SPLIT_RE = re.compile(r"^\s*<START>\s*$", re.IGNORECASE | re.MULTILINE)
_SPEAKER_LINE_RE = re.compile(r"^\s*(\{\{(?:char|user)\}\}|[^\s:：]{1,24})\s*[:：]\s?(.*)$",
                              re.IGNORECASE)


class CharacterCard(BaseModel):
    """A tavern character card plus the engine's acting extensions."""

    model_config = ConfigDict(extra="forbid")

    # Identity inside a pack: equals the NPC id the card describes.
    id: str = ""

    # ---- community card spec (V1 / V2 / V3) ------------------------------
    name: str
    nickname: str = ""
    description: str = ""
    personality: str = ""
    scenario: str = ""
    first_mes: str = ""
    mes_example: str = ""
    alternate_greetings: list[str] = Field(default_factory=list)
    system_prompt: str = ""                # replaces the preset's main prompt
    post_history_instructions: str = ""    # replaces the preset's post-history block
    creator_notes: str = ""
    creator: str = ""
    character_version: str = ""
    tags: list[str] = Field(default_factory=list)
    character_book: Lorebook | None = None
    depth_prompt: DepthPrompt | None = None
    talkativeness: float = Field(default=0.5, ge=0.0, le=1.0)
    extensions: dict[str, Any] = Field(default_factory=dict)

    # ---- engine extensions ------------------------------------------------
    role: str = ""                         # public one-liner others may know
    age: int | None = None
    backstory: str = ""                    # the character's own past (private)
    likes: list[str] = Field(default_factory=list)
    dislikes: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)       # happy to talk about
    speech: SpeechProfile = Field(default_factory=SpeechProfile)
    secrets: list[str] = Field(default_factory=list)      # never shown to other agents
    knowledge: list[str] = Field(default_factory=list)    # what they know (private)
    goals: list[str] = Field(default_factory=list)        # long-running wants (private)
    relationships: dict[str, str] = Field(default_factory=dict)  # id/name -> stance
    affection_voices: list[AffectionVoice] = Field(default_factory=list)
    expression_aliases: dict[str, list[str]] = Field(default_factory=dict)
    model_hint: str = ""                   # optional per-character model override

    # Where this card came from ("npc", "file:<name>", ...). Never serialised.
    source: str = Field(default="", exclude=True)

    @field_validator("tags", "alternate_greetings", "likes", "dislikes", "topics",
                     "secrets", "knowledge", "goals", mode="before")
    @classmethod
    def _lists(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            return [value] if value.strip() else []
        return _str_list(value)

    @field_validator("expression_aliases", mode="before")
    @classmethod
    def _aliases(cls, value: Any) -> dict[str, list[str]]:
        if not value:
            return {}
        return {str(k): _str_list(v) for k, v in dict(value).items()}

    @field_validator("talkativeness", mode="before")
    @classmethod
    def _talk(cls, value: Any) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.5

    # ------------------------------------------------------------------
    # Example dialogue

    def example_blocks(self, known_names: tuple[str, ...] = ()) -> list[list[ExampleTurn]]:
        """Parse ``mes_example`` into blocks of attributed turns.

        Blocks are separated by ``<START>`` lines. Inside a block a line that
        starts with ``{{char}}:`` / ``{{user}}:`` / ``Name:`` opens a new turn
        and following unprefixed lines continue it. Text before the first
        speaker label in a block is attributed to the character.

        A ``Name:`` prefix only counts as a speaker change when the name is
        one we know (the character, ``{{user}}``, a common player alias, a
        name in ``known_names``) or it labels at least two lines — otherwise
        prose such as ``他說：「好」`` would be split into a bogus speaker.
        """
        if not self.mes_example.strip():
            return []
        names_char = {self.name.casefold(), "{{char}}"}
        if self.nickname:
            names_char.add(self.nickname.casefold())
        names_user = {"{{user}}", "user", "you", "你", "玩家", "主角"}
        names_other = {name.casefold() for name in known_names if name}
        label_counts: dict[str, int] = {}
        for line in self.mes_example.splitlines():
            match = _SPEAKER_LINE_RE.match(line)
            if match:
                folded = match.group(1).casefold()
                label_counts[folded] = label_counts.get(folded, 0) + 1
        blocks: list[list[ExampleTurn]] = []
        for chunk in _EXAMPLE_SPLIT_RE.split(self.mes_example):
            turns: list[ExampleTurn] = []
            current: ExampleTurn | None = None
            for line in chunk.splitlines():
                if not line.strip():
                    if current is not None:
                        current.text += "\n"
                    continue
                match = _SPEAKER_LINE_RE.match(line)
                label = match.group(1) if match else ""
                folded = label.casefold()
                is_speaker = bool(match) and (
                    folded in names_char or folded in names_user
                    or folded in names_other
                    or (_looks_like_name(label) and label_counts.get(folded, 0) >= 2)
                )
                if match and is_speaker:
                    if folded in names_char:
                        kind, name = "char", self.name
                    elif folded in names_user:
                        kind, name = "user", "{{user}}"
                    else:
                        kind, name = "other", label
                    current = ExampleTurn(speaker=kind, name=name, text=match.group(2))
                    turns.append(current)
                elif current is None:
                    current = ExampleTurn(speaker="char", name=self.name, text=line.strip())
                    turns.append(current)
                else:
                    current.text += ("\n" if current.text else "") + line.strip()
            for turn in turns:
                turn.text = turn.text.strip()
            turns = [turn for turn in turns if turn.text]
            if turns:
                blocks.append(turns)
        return blocks

    def example_lines(self) -> list[str]:
        """Just the character's own example lines, in order."""
        return [turn.text for block in self.example_blocks()
                for turn in block if turn.speaker == "char"]

    def voice_for_affection(self, value: int | None) -> str:
        """The ``affection_voices`` note that applies at ``value`` (or "")."""
        if value is None or not self.affection_voices:
            return ""
        best: AffectionVoice | None = None
        for voice in self.affection_voices:
            if voice.min <= value and (best is None or voice.min >= best.min):
                best = voice
        return best.note if best else ""

    def public_blurb(self) -> str:
        """What *other* characters may know about this one: name and role only."""
        bits = [self.name]
        if self.role:
            bits.append(f"（{self.role}）")
        return "".join(bits)

    # ------------------------------------------------------------------
    # Merge

    def merged_over(self, base: "CharacterCard") -> "CharacterCard":
        """Return ``base`` overlaid with every field this card actually sets.

        Used to layer an authored card file over the card derived from the
        pack's ``characters.yaml`` entry, so a card file only needs to say what
        the NPC definition does not.
        """
        data = base.model_dump()
        mine = self.model_dump()
        for key in self.model_fields_set:
            if key == "source":
                continue
            value = mine[key]
            if key == "speech":
                merged_speech = dict(data["speech"])
                for skey in self.speech.model_fields_set:
                    merged_speech[skey] = value[skey]
                data["speech"] = merged_speech
            elif value in ("", None, [], {}) and key != "talkativeness":
                continue
            else:
                data[key] = value
        merged = type(self).model_validate(data)
        merged.source = self.source or base.source
        return merged

    # ------------------------------------------------------------------
    # Tavern interchange

    @classmethod
    def from_tavern(cls, obj: dict[str, Any], *, card_id: str = "") -> "CharacterCard":
        """Build from a V1 / V2 / V3 card object (tolerant of unknown keys)."""
        if not isinstance(obj, dict):
            raise ValueError("character card must be a JSON object")
        data = obj.get("data") if isinstance(obj.get("data"), dict) else obj
        name = str(data.get("name") or obj.get("name") or "").strip()
        if not name:
            raise ValueError("character card has no name")
        ext_all = dict(data.get("extensions") or {}) \
            if isinstance(data.get("extensions"), dict) else {}
        ours = ext_all.pop(EXTENSION_KEY, {})
        if not isinstance(ours, dict):
            ours = {}

        book_raw = data.get("character_book")
        book = Lorebook.from_tavern(book_raw) if isinstance(book_raw, dict) else None

        depth_raw = ext_all.pop("depth_prompt", None)
        depth_prompt = None
        if isinstance(depth_raw, dict) and str(depth_raw.get("prompt") or "").strip():
            role = depth_raw.get("role", "system")
            if isinstance(role, int):
                role = _ST_ROLE.get(role, "system")
            if role not in _ST_ROLE_REV:
                role = "system"
            try:
                depth = max(0, int(depth_raw.get("depth", 4)))
            except (TypeError, ValueError):
                depth = 4
            depth_prompt = DepthPrompt(prompt=str(depth_raw["prompt"]), depth=depth,
                                       role=role)

        talk = ext_all.pop("talkativeness", data.get("talkativeness", 0.5))

        def text(key: str) -> str:
            value = data.get(key)
            return value if isinstance(value, str) else ""

        fields: dict[str, Any] = dict(
            id=card_id or str(ours.get("id") or ""),
            name=name,
            nickname=text("nickname"),
            description=text("description"),
            personality=text("personality"),
            scenario=text("scenario"),
            first_mes=text("first_mes"),
            mes_example=text("mes_example"),
            alternate_greetings=[g for g in (data.get("alternate_greetings") or [])
                                 if isinstance(g, str)],
            system_prompt=text("system_prompt"),
            post_history_instructions=text("post_history_instructions"),
            creator_notes=text("creator_notes") or text("creatorcomment"),
            creator=text("creator"),
            character_version=text("character_version"),
            tags=[t for t in (data.get("tags") or []) if isinstance(t, str)],
            character_book=book,
            depth_prompt=depth_prompt,
            talkativeness=talk,
            extensions=ext_all,
        )
        # Engine-only fields ride in extensions[EXTENSION_KEY].
        for key in ("role", "age", "backstory", "likes", "dislikes", "topics",
                    "secrets", "knowledge", "goals", "relationships",
                    "affection_voices", "expression_aliases", "model_hint", "speech"):
            if key in ours and ours[key] not in (None, "", [], {}):
                fields[key] = ours[key]
        card = cls.model_validate(fields)
        card.source = "tavern"
        return card

    def _engine_extension(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.id:
            out["id"] = self.id
        for key in ("role", "age", "backstory", "likes", "dislikes", "topics",
                    "secrets", "knowledge", "goals", "relationships",
                    "expression_aliases", "model_hint"):
            value = getattr(self, key)
            if value not in (None, "", [], {}):
                out[key] = value
        if self.affection_voices:
            out["affection_voices"] = [v.model_dump() for v in self.affection_voices]
        if not self.speech.is_empty():
            out["speech"] = self.speech.model_dump(exclude_defaults=True)
        return out

    def to_tavern(self, spec: Literal["v2", "v3"] = "v2") -> dict[str, Any]:
        """Serialise as a ``chara_card_v2`` or ``chara_card_v3`` object."""
        ext = dict(self.extensions)
        ext["talkativeness"] = self.talkativeness
        if self.depth_prompt is not None:
            ext["depth_prompt"] = self.depth_prompt.model_dump()
        ours = self._engine_extension()
        if ours:
            ext[EXTENSION_KEY] = ours
        data: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "personality": self.personality,
            "scenario": self.scenario,
            "first_mes": self.first_mes,
            "mes_example": self.mes_example,
            "creator_notes": self.creator_notes,
            "system_prompt": self.system_prompt,
            "post_history_instructions": self.post_history_instructions,
            "alternate_greetings": list(self.alternate_greetings),
            "tags": list(self.tags),
            "creator": self.creator,
            "character_version": self.character_version,
            "extensions": ext,
        }
        if self.character_book is not None:
            data["character_book"] = self.character_book.to_tavern()
        if spec == "v3":
            data["nickname"] = self.nickname
            data["group_only_greetings"] = []
            return {"spec": "chara_card_v3", "spec_version": "3.0", "data": data}
        return {"spec": "chara_card_v2", "spec_version": "2.0", "data": data}

    # ------------------------------------------------------------------
    # Files

    def to_native(self) -> dict[str, Any]:
        """A YAML-friendly dict: set fields only, in a readable order."""
        dumped = self.model_dump(exclude_defaults=True, exclude_none=True)
        ordered: dict[str, Any] = {}
        for key in type(self).model_fields:
            if key in dumped and key != "source":
                ordered[key] = dumped[key]
        ordered.setdefault("name", self.name)
        return ordered

    @classmethod
    def load(cls, path: Path, *, card_id: str = "") -> "CharacterCard":
        """Load a card from ``.yaml`` / ``.yml`` / ``.json`` / ``.png``.

        The card's ``id`` is, in order: the ``card_id`` argument, an ``id``
        stored in the file, or the file's stem.
        """
        path = Path(path)
        suffix = path.suffix.lower()
        fallback_id = card_id or path.stem
        if suffix == ".png":
            obj = png_meta.read_card_json(path.read_bytes())
            card = cls.from_tavern(obj, card_id=card_id)
        elif suffix == ".json":
            obj = json.loads(path.read_text(encoding="utf-8"))
            card = cls.from_tavern(obj, card_id=card_id)
        elif suffix in (".yaml", ".yml"):
            obj = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(obj, dict):
                raise ValueError(f"{path.name}: a card file must be a mapping")
            if "spec" in obj or isinstance(obj.get("data"), dict):
                card = cls.from_tavern(obj, card_id=card_id)
            else:
                card = cls.model_validate(obj)
                if card_id:
                    card.id = card_id
        else:
            raise ValueError(f"{path.name}: unsupported card format "
                             "(expected .yaml, .json or .png)")
        if not card.id:
            card.id = fallback_id
        card.source = f"file:{path.name}"
        return card

    def save(self, path: Path, *, spec: Literal["v2", "v3"] = "v2",
             image: bytes | None = None) -> Path:
        """Write the card as YAML (native), JSON (tavern) or PNG (embedded)."""
        path = Path(path)
        suffix = path.suffix.lower()
        path.parent.mkdir(parents=True, exist_ok=True)
        if suffix in (".yaml", ".yml"):
            path.write_text(dump_yaml(self.to_native()), encoding="utf-8")
        elif suffix == ".json":
            path.write_text(json.dumps(self.to_tavern(spec), ensure_ascii=False, indent=2)
                            + "\n", encoding="utf-8")
        elif suffix == ".png":
            base = image if image is not None else png_meta.minimal_png()
            v3 = self.to_tavern("v3") if spec == "v3" else None
            path.write_bytes(png_meta.embed_card_json(base, v2=self.to_tavern("v2"), v3=v3))
        else:
            raise ValueError(f"{path.name}: unsupported card format "
                             "(expected .yaml, .json or .png)")
        return path


def _looks_like_name(label: str) -> bool:
    """Whether an example-dialogue prefix is plausibly a speaker name.

    Guards against treating ordinary prose with a colon ("Note: ...",
    "10:30") as a speaker change.
    """
    if not label or label.isdigit():
        return False
    if any(ch in label for ch in "「」『』（）()[]{}<>，。！？,.!?"):
        return False
    return True


class _BlockDumper(yaml.SafeDumper):
    """YAML dumper that writes multi-line strings as readable ``|`` blocks."""


def _represent_str(dumper: yaml.SafeDumper, data: str):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_BlockDumper.add_representer(str, _represent_str)


def dump_yaml(data: Any) -> str:
    """Dump ``data`` as human-editable YAML (unicode kept, block scalars)."""
    return yaml.dump(data, Dumper=_BlockDumper, allow_unicode=True,
                     sort_keys=False, default_flow_style=False, width=100)


def card_from_npc(npc: "NPC") -> CharacterCard:
    """Derive a card from a pack's ``characters.yaml`` entry.

    Every NPC has an effective card even when the pack ships no card file:
    the existing ``description`` / ``persona`` / ``voice`` / ``backstory`` /
    ``secrets`` / ``likes`` / ``dislikes`` fields map onto the card. A card
    file layered on top (:meth:`CharacterCard.merged_over`) then only has to
    add what those fields cannot express — chiefly example dialogue and a
    speech profile.
    """
    speech = SpeechProfile(diction=(npc.voice or "").strip())
    card = CharacterCard(
        id=npc.id,
        name=npc.name,
        description=(npc.description or "").strip(),
        personality=(npc.persona or "").strip(),
        role=(npc.role or "").strip(),
        age=npc.age,
        backstory=(npc.backstory or "").strip(),
        likes=list(npc.likes),
        dislikes=list(npc.dislikes),
        topics=list(npc.safe_topics),
        secrets=list(npc.secrets),
        tags=list(npc.tags),
        speech=speech,
        model_hint=npc.llm_model_hint or "",
    )
    card.source = "npc"
    return card
