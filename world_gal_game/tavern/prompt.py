"""Prompt assembly and the prompt inspector.

:func:`build_prompt` turns *a character card + a preset + the conversation so
far* into the chat messages sent to a model, following the conventions of
tavern Chat Completion presets:

- blocks are emitted in the preset's order; marker blocks are filled from the
  card, the persona, the world book and the scene;
- a card's own ``system_prompt`` / ``post_history_instructions`` replace the
  preset's main / post-history text (``{{original}}`` splices the preset text
  back in);
- ``in_chat`` blocks, at-depth world-book entries, the author's note and the
  card's depth prompt are injected *inside* the history, counted in messages
  from the end — the later an instruction sits, the more it is heeded;
- when a context budget is set, the oldest history goes first, then examples.

Every build also produces a :class:`PromptBuild` report: one row per block with
its size and whether it was sent, the world-book decision log, what was
trimmed, and any macros that did not resolve. That report is the answer to
"what did the model actually see?", and it is what ``world-gal-game tavern
prompt`` prints.

The builder knows nothing about providers. It returns plain
``{"role", "content"}`` messages, system-role messages included wherever the
preset put them; each backend adapts that to its API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .card import CharacterCard, Lorebook
from .lorebook import LoreActivation, activate
from .macros import MacroContext, expand
from .preset import MARKERS, Preset, ensure_engine_blocks
from .style import StyleGuide, normalize_locale
from .tokens import estimate_tokens


@dataclass
class ChatMessage:
    """One message of the conversation as *this* speaker sees it."""

    role: str                 # "user" | "assistant" | "system"
    content: str
    name: str = ""


@dataclass
class CastView:
    """Someone else in the scene, as far as the speaker is allowed to know."""

    name: str
    role: str = ""
    note: str = ""            # the speaker's own stance toward them


@dataclass
class SceneContext:
    """The public situation: where, when, who, and how things stand."""

    location: str = ""
    time: str = ""
    premise: str = ""
    cast: list[CastView] = field(default_factory=list)
    relationship: str = ""           # how the speaker treats the player right now
    affection: int | None = None
    affection_label: str = ""
    recent_events: list[str] = field(default_factory=list)


@dataclass
class PrivateBrief:
    """What only this speaker is told about the scene."""

    objective: str = ""
    tactic: str = ""
    hidden: list[str] = field(default_factory=list)


@dataclass
class AuthorNote:
    content: str = ""
    depth: int = 4
    role: str = "system"


@dataclass
class PromptInputs:
    card: CharacterCard
    preset: Preset
    style: StyleGuide
    user_name: str = ""
    persona: str = ""                              # the player's public description
    history: list[ChatMessage] = field(default_factory=list)
    lorebooks: list[Lorebook] = field(default_factory=list)
    scene: SceneContext | None = None
    private: PrivateBrief | None = None
    direction: str = ""                            # this turn's private note
    retake: str = ""                               # why the last attempt was sent back
    output_format: str = ""                        # how to shape the reply
    expressions: dict[str, list[str]] = field(default_factory=dict)  # key -> aliases
    author_note: AuthorNote | None = None
    group: list[str] = field(default_factory=list)
    state: Any = None
    rng: Any = None                                # a seeded Random
    variables: dict[str, Any] = field(default_factory=dict)
    global_variables: dict[str, Any] = field(default_factory=dict)
    values: dict[str, str] = field(default_factory=dict)
    resolver: Callable[[str, list[str]], str | None] | None = None
    generation_type: str = "normal"
    #: False builds the prompt without the card's private fields.
    include_private: bool = True
    #: How another speaker's line is written in the transcript, e.g.
    #: ``"{name}：「{text}」"``. Example dialogue is rendered the same way so
    #: the examples and the live history look alike.
    speech_format: str = ""
    #: Shown between example blocks ("another time").
    example_separator: str = ""


@dataclass
class BlockReport:
    id: str
    name: str
    role: str
    position: str            # "relative" or "in_chat@N"
    tokens: int = 0
    status: str = "sent"     # sent | empty | disabled | trigger | overridden
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "name": self.name, "role": self.role,
                               "position": self.position, "tokens": self.tokens,
                               "status": self.status}
        if self.note:
            out["note"] = self.note
        return out


@dataclass
class PromptBuild:
    messages: list[dict[str, str]]
    blocks: list[BlockReport]
    lore: LoreActivation
    tokens: int = 0
    budget: int = 0
    dropped_history: int = 0
    dropped_examples: int = 0
    unknown_macros: list[str] = field(default_factory=list)
    banned: list[str] = field(default_factory=list)
    sources: list[list[str]] = field(default_factory=list)   # block ids per message

    def to_dict(self, *, include_messages: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "tokens_estimate": self.tokens,
            "budget": self.budget,
            "blocks": [block.to_dict() for block in self.blocks],
            "lore": self.lore.to_dict(),
            "dropped_history": self.dropped_history,
            "dropped_examples": self.dropped_examples,
            "unknown_macros": list(self.unknown_macros),
            "banned": list(self.banned),
        }
        if include_messages:
            out["messages"] = [dict(message) for message in self.messages]
            out["sources"] = [list(src) for src in self.sources]
        return out

    def render(self, *, full: bool = False) -> str:
        """The inspector view: blocks, world book, totals, optionally the text."""
        mark = {"sent": "+", "empty": " ", "disabled": "x", "trigger": "x",
                "overridden": "~"}
        lines = [f"prompt: ~{self.tokens} tokens (estimate)"
                 + (f", budget {self.budget}" if self.budget else "")]
        lines.append("  st  tokens  role       position     block")
        for block in self.blocks:
            note = f"  ({block.note})" if block.note else ""
            lines.append(f"  [{mark.get(block.status, '?')}] {block.tokens:>6}  "
                         f"{block.role:<9}  {block.position:<11}  "
                         f"{block.id}{note}")
        if self.lore.log:
            lines.append(f"world book: ~{self.lore.tokens} tokens"
                         + (f" of {self.lore.budget}" if self.lore.budget else ""))
            for decision in self.lore.log:
                detail = f" — {decision.detail}" if decision.detail else ""
                key = f" [{decision.key}]" if decision.key else ""
                lines.append(f"  {decision.status:<18} {decision.entry}{key}{detail}")
        if self.dropped_history or self.dropped_examples:
            lines.append(f"trimmed: {self.dropped_history} history messages, "
                         f"{self.dropped_examples} example messages")
        if self.unknown_macros:
            lines.append("unknown macros (left as written): "
                         + ", ".join(self.unknown_macros))
        if full:
            lines.append("")
            for message, source in zip(self.messages, self.sources):
                lines.append(f"--- {message['role']} ({', '.join(source)}) ---")
                lines.append(message["content"])
        return "\n".join(lines)


# ----------------------------------------------------------------------
# Section labels

_L: dict[str, dict[str, str]] = {
    "zh-Hant": {
        "sep": "、", "colon": "：", "lq": "「", "rq": "」",
        "identity": "{name}{age}{role}。",
        "age": "，{age} 歲", "role": "，{role}",
        "likes": "喜歡", "dislikes": "不喜歡", "topics": "樂意聊",
        "speech_title": "【{char}怎麼說話】",
        "diction": "用字", "sentence": "句子", "self_ref": "自稱",
        "address": "稱呼", "address_item": "叫{who}「{what}」",
        "particles": "語氣詞（偶爾才用，不是每句）",
        "fillers": "口頭習慣（偶爾才用）",
        "catchphrases": "招牌句（整場戲最多一次）",
        "habits": "習慣", "when": "{mood}的時候",
        "avoid_topics": "會避開的話題", "never_says": "{char}不會說",
        "private_title": "【只有{char}自己知道】",
        "private_note": "（別人不知道這些。{char}不會主動說出口；被問到會躲、會岔開，"
                        "真的沒有退路才露一點。）",
        "backstory_title": "【{char}的過去】",
        "knowledge_title": "【{char}知道的事】",
        "goals_title": "【{char}一直想要的】",
        "scene_goal_title": "【這場戲裡{char}想要的】",
        "objective": "想要", "tactic": "做法",
        "scene_title": "【現在】", "location": "地點", "time": "時間",
        "premise": "情境", "present": "在場",
        "views_title": "{char}對他們的看法", "relation": "和{user}的關係",
        "affection": "（好感 {value}{label}）", "recent": "最近發生的事",
        "persona_title": "【{user}】",
        "expr_title": "【表情】",
        "expr_list": "{char}有這些表情：{items}",
        "expr_rule": "每次開口，先用單獨一行 <sprite:情緒> 標出當下的表情。"
                     "情緒寫兩到五個字就好（例如 <sprite:{sample}>）；"
                     "上面沒有合適的，可以寫別的情緒詞。標籤裡只寫情緒，不寫動作。",
        "style_title": "【台詞寫法】",
        "note_title": "［導演給{char}的悄悄話］",
        "retake_title": "［上一版被退回，原因如下。重來時換一個反應，不是換句話說］",
    },
    "zh-Hans": {
        "sep": "、", "colon": "：", "lq": "「", "rq": "」",
        "identity": "{name}{age}{role}。",
        "age": "，{age} 岁", "role": "，{role}",
        "likes": "喜欢", "dislikes": "不喜欢", "topics": "乐意聊",
        "speech_title": "【{char}怎么说话】",
        "diction": "用字", "sentence": "句子", "self_ref": "自称",
        "address": "称呼", "address_item": "叫{who}「{what}」",
        "particles": "语气词（偶尔才用，不是每句）",
        "fillers": "口头习惯（偶尔才用）",
        "catchphrases": "招牌句（整场戏最多一次）",
        "habits": "习惯", "when": "{mood}的时候",
        "avoid_topics": "会避开的话题", "never_says": "{char}不会说",
        "private_title": "【只有{char}自己知道】",
        "private_note": "（别人不知道这些。{char}不会主动说出口；被问到会躲、会岔开，"
                        "真的没有退路才露一点。）",
        "backstory_title": "【{char}的过去】",
        "knowledge_title": "【{char}知道的事】",
        "goals_title": "【{char}一直想要的】",
        "scene_goal_title": "【这场戏里{char}想要的】",
        "objective": "想要", "tactic": "做法",
        "scene_title": "【现在】", "location": "地点", "time": "时间",
        "premise": "情境", "present": "在场",
        "views_title": "{char}对他们的看法", "relation": "和{user}的关系",
        "affection": "（好感 {value}{label}）", "recent": "最近发生的事",
        "persona_title": "【{user}】",
        "expr_title": "【表情】",
        "expr_list": "{char}有这些表情：{items}",
        "expr_rule": "每次开口，先用单独一行 <sprite:情绪> 标出当下的表情。"
                     "情绪写两到五个字就好（例如 <sprite:{sample}>）；"
                     "上面没有合适的，可以写别的情绪词。标签里只写情绪，不写动作。",
        "style_title": "【台词写法】",
        "note_title": "［导演给{char}的悄悄话］",
        "retake_title": "［上一版被退回，原因如下。重来时换一个反应，不是换句话说］",
    },
    "en": {
        "sep": ", ", "colon": ": ", "lq": "“", "rq": "”",
        "identity": "{name}{age}{role}.",
        "age": ", {age}", "role": ", {role}",
        "likes": "Likes", "dislikes": "Dislikes", "topics": "Happy to talk about",
        "speech_title": "[How {char} talks]",
        "diction": "Diction", "sentence": "Sentences", "self_ref": "Says \"I\" as",
        "address": "Address", "address_item": "calls {who} {what}",
        "particles": "Particles (now and then, not every line)",
        "fillers": "Verbal habits (now and then)",
        "catchphrases": "Signature lines (once a scene at most)",
        "habits": "Habits", "when": "When {mood}",
        "avoid_topics": "Steers away from", "never_says": "{char} would never say",
        "private_title": "[Only {char} knows]",
        "private_note": "(Nobody else knows this. {char} does not volunteer it; when "
                        "asked, {char} dodges or changes the subject, and lets a little "
                        "out only when cornered.)",
        "backstory_title": "[{char}'s past]",
        "knowledge_title": "[What {char} knows]",
        "goals_title": "[What {char} has long wanted]",
        "scene_goal_title": "[What {char} wants in this scene]",
        "objective": "Wants", "tactic": "Approach",
        "scene_title": "[Now]", "location": "Place", "time": "Time",
        "premise": "Situation", "present": "Present",
        "views_title": "How {char} sees them", "relation": "With {user}",
        "affection": " (affection {value}{label})", "recent": "Recently",
        "persona_title": "[{user}]",
        "expr_title": "[Expressions]",
        "expr_list": "{char} has these expressions: {items}",
        "expr_rule": "Each time {char} speaks, first put <sprite:emotion> on a line of "
                     "its own for the expression right now. One or two words is enough "
                     "(for example <sprite:{sample}>); if none of the above fits, write "
                     "another emotion word. Only the emotion goes in the tag, no actions.",
        "style_title": "[How to write the line]",
        "note_title": "[The director, quietly, to {char}]",
        "retake_title": "[The last take was sent back for the reasons below. Give a "
                        "different reaction, not the same one reworded]",
    },
}
_L["ja"] = _L["en"]


def labels_for(locale: str) -> dict[str, str]:
    return _L.get(normalize_locale(locale)) or _L["en"]


# ----------------------------------------------------------------------
# Marker rendering


class _Renderer:
    def __init__(self, inputs: PromptInputs, ctx: MacroContext,
                 lore: LoreActivation) -> None:
        self.i = inputs
        self.ctx = ctx
        self.lore = lore
        self.L = labels_for(inputs.style.locale or inputs.preset.locale)
        self.char = inputs.card.name
        self.user = inputs.user_name

    def _t(self, key: str, **fmt: Any) -> str:
        return self.L[key].format(char=self.char, user=self.user, **fmt)

    def _x(self, text: str) -> str:
        return expand(text, self.ctx).strip()

    def _join(self, items: list[str]) -> str:
        return self.L["sep"].join(item for item in items if item)

    def _kv(self, key: str, value: str) -> str:
        return f"{self.L[key]}{self.L['colon']}{value}"

    def _bullets(self, items: list[str]) -> list[str]:
        return [f"- {self._x(item)}" for item in items if item and item.strip()]

    # -- tavern markers --------------------------------------------------

    def char_description(self) -> str:
        card = self.i.card
        parts: list[str] = []
        if card.role or card.age is not None:
            parts.append(self.L["identity"].format(
                name=card.name,
                age=self.L["age"].format(age=card.age) if card.age is not None else "",
                role=self.L["role"].format(role=card.role) if card.role else ""))
        description = self._x(card.description)
        if description:
            parts.append(description)
        for key, values in (("likes", card.likes), ("dislikes", card.dislikes),
                            ("topics", card.topics)):
            if values:
                parts.append(self._kv(key, self._join(values)))
        return "\n".join(parts)

    def char_personality(self) -> str:
        if not self.i.card.personality.strip():
            return ""
        return self._x(self.i.preset.personality_format)

    def scenario(self) -> str:
        if not self.i.card.scenario.strip():
            return ""
        return self._x(self.i.preset.scenario_format)

    def persona(self) -> str:
        text = self._x(self.i.persona)
        if not text:
            return ""
        return f"{self._t('persona_title')}\n{text}"

    def world_info(self, position: str) -> str:
        text = self.lore.text(position)
        if not text:
            return ""
        fmt = self.i.preset.wi_format or "{0}"
        return fmt.replace("{0}", text) if "{0}" in fmt else text

    # -- engine markers --------------------------------------------------

    def speech_profile(self) -> str:
        speech = self.i.card.speech
        if speech.is_empty():
            return ""
        L = self.L
        lines = [self._t("speech_title")]
        for key, value in (("diction", speech.diction),
                           ("sentence", speech.sentence_length),
                           ("self_ref", speech.self_reference)):
            if value.strip():
                lines.append(self._kv(key, self._x(value)))
        if speech.address:
            items = [L["address_item"].format(who=self._x(who), what=self._x(what))
                     for who, what in speech.address.items()]
            lines.append(self._kv("address", self._join(items)))
        for key, values in (("particles", speech.particles),
                            ("fillers", speech.fillers),
                            ("catchphrases", speech.catchphrases)):
            if values:
                lines.append(self._kv(key, self._join([self._x(v) for v in values])))
        if speech.habits:
            lines.append(f"{L['habits']}{L['colon']}".rstrip())
            lines.extend(self._bullets(speech.habits))
        for mood, how in speech.when.items():
            lines.append(f"{L['when'].format(mood=mood)}{L['colon']}{self._x(how)}")
        if speech.avoid_topics:
            lines.append(self._kv("avoid_topics", self._join(speech.avoid_topics)))
        if speech.never_says:
            quoted = "".join(f"{L['lq']}{phrase}{L['rq']}" for phrase in speech.never_says)
            lines.append(f"{self._t('never_says')}{L['colon']}{quoted}")
        return "\n".join(lines)

    def private_knowledge(self) -> str:
        if not self.i.include_private:
            return ""
        card = self.i.card
        private = self.i.private or PrivateBrief()
        lines: list[str] = []
        secrets = [*card.secrets, *private.hidden]
        if secrets:
            lines.append(self._t("private_title"))
            lines.append(self._t("private_note"))
            lines.extend(self._bullets(secrets))
        if card.backstory.strip():
            lines.append(self._t("backstory_title"))
            lines.append(self._x(card.backstory))
        if card.knowledge:
            lines.append(self._t("knowledge_title"))
            lines.extend(self._bullets(card.knowledge))
        if card.goals:
            lines.append(self._t("goals_title"))
            lines.extend(self._bullets(card.goals))
        if private.objective.strip() or private.tactic.strip():
            lines.append(self._t("scene_goal_title"))
            if private.objective.strip():
                lines.append(self._kv("objective", self._x(private.objective)))
            if private.tactic.strip():
                lines.append(self._kv("tactic", self._x(private.tactic)))
        return "\n".join(lines)

    def scene_context(self) -> str:
        scene = self.i.scene
        if scene is None:
            return ""
        L = self.L
        lines: list[str] = []
        where = [self._kv(key, value) for key, value in
                 (("location", scene.location), ("time", scene.time)) if value]
        if where:
            lines.append("　".join(where) if L["sep"] == "、" else "; ".join(where))
        if scene.premise.strip():
            lines.append(self._kv("premise", self._x(scene.premise)))
        if scene.cast:
            present = []
            for member in scene.cast:
                present.append(f"{member.name}（{member.role}）" if member.role and
                               L["sep"] == "、" else
                               (f"{member.name} ({member.role})" if member.role
                                else member.name))
            lines.append(self._kv("present", self._join(present)))
            views = [f"- {member.name}{L['colon']}{self._x(member.note)}"
                     for member in scene.cast if member.note.strip()]
            if views:
                lines.append(f"{self._t('views_title')}{L['colon']}".rstrip())
                lines.extend(views)
        if scene.relationship.strip() or scene.affection is not None:
            relation = self._x(scene.relationship)
            if scene.affection is not None:
                label = f" {scene.affection_label}" if scene.affection_label else ""
                relation += L["affection"].format(value=scene.affection, label=label)
            lines.append(f"{self._t('relation')}{L['colon']}{relation}")
        if scene.recent_events:
            lines.append(f"{L['recent']}{L['colon']}".rstrip())
            lines.extend(f"- {event}" for event in scene.recent_events if event)
        if not lines:
            return ""
        return "\n".join([self._t("scene_title"), *lines])

    def vn_directives(self) -> str:
        expressions = self.i.expressions
        if not expressions:
            return ""
        L = self.L
        items = []
        sample = ""
        for key, aliases in expressions.items():
            if aliases:
                items.append(f"{key}（{self._join(aliases[:3])}）" if L["sep"] == "、"
                             else f"{key} ({self._join(aliases[:3])})")
                sample = sample or aliases[0]
            else:
                items.append(key)
        sample = sample or next(iter(expressions))
        return "\n".join([
            self._t("expr_title"),
            self._t("expr_list", items=self._join(items)),
            self._t("expr_rule", sample=sample),
        ])

    def style_guide(self) -> str:
        rules = self.i.style.render_dialogue_rules()
        if not rules:
            return ""
        return f"{self._t('style_title')}\n{rules}"

    def direction(self) -> str:
        parts: list[str] = []
        if self.i.direction.strip():
            parts.append(f"{self._t('note_title')}\n{self._x(self.i.direction)}")
        if self.i.retake.strip():
            parts.append(f"{self._t('retake_title')}\n{self.i.retake.strip()}")
        if self.i.output_format.strip():
            parts.append(self._x(self.i.output_format))
        return "\n\n".join(parts)

    def marker(self, block_id: str) -> str:
        table: dict[str, Callable[[], str]] = {
            "charDescription": self.char_description,
            "charPersonality": self.char_personality,
            "scenario": self.scenario,
            "personaDescription": self.persona,
            "worldInfoBefore": lambda: self.world_info("before_char"),
            "worldInfoAfter": lambda: self.world_info("after_char"),
            "speechProfile": self.speech_profile,
            "privateKnowledge": self.private_knowledge,
            "sceneContext": self.scene_context,
            "vnDirectives": self.vn_directives,
            "styleGuide": self.style_guide,
            "direction": self.direction,
        }
        render = table.get(block_id)
        return render() if render else ""


# ----------------------------------------------------------------------
# Assembly


@dataclass
class _Out:
    role: str
    content: str
    sources: list[str]
    origin: str = ""          # "history" | "example" | "" (for trimming)


@dataclass
class _Injection:
    depth: int
    order: int
    role: str
    content: str
    source: str


def _example_messages(inputs: PromptInputs, ctx: MacroContext,
                      lore: LoreActivation) -> list[_Out]:
    card = inputs.card
    blocks = card.example_blocks(known_names=tuple(inputs.group))
    out: list[_Out] = []
    before = lore.text("before_examples")
    if before:
        out.append(_Out("system", before, ["worldInfo:before_examples"], "example"))
    header = expand(inputs.preset.new_example_chat_prompt, ctx).strip()
    separator = inputs.example_separator.strip()
    grouped = len(inputs.group) > 1
    quote_open = inputs.style.quote_open
    for number, block in enumerate(blocks):
        # One header for the whole set; later blocks only get a light break,
        # so the model is not told five times what it is looking at.
        lead = header if number == 0 else separator
        if lead:
            out.append(_Out("system", lead, ["dialogueExamples"], "example"))
        for turn in block:
            text = expand(turn.text, ctx).strip()
            if not text:
                continue
            if turn.speaker == "char":
                out.append(_Out("assistant", text, ["dialogueExamples"], "example"))
                continue
            name = inputs.user_name if turn.speaker == "user" else turn.name
            if inputs.speech_format and name:
                bare = text[len(quote_open):-len(inputs.style.quote_close)] \
                    if text.startswith(quote_open) and text.endswith(
                        inputs.style.quote_close) else text
                shown = inputs.speech_format.format(name=name, text=bare)
            elif (grouped or turn.speaker == "other") and name:
                shown = f"{name}：{text}"
            else:
                shown = text
            out.append(_Out("user", shown, ["dialogueExamples"], "example"))
    after = lore.text("after_examples")
    if after:
        out.append(_Out("system", after, ["worldInfo:after_examples"], "example"))
    return out


def _with_injections(history: list[_Out], injections: list[_Injection]) -> list[_Out]:
    """Splice depth injections into the history (depth 0 = after the last)."""
    if not injections:
        return list(history)
    count = len(history)
    slots: dict[int, list[_Injection]] = {}
    for injection in injections:
        position = max(0, count - injection.depth)
        slots.setdefault(position, []).append(injection)
    out: list[_Out] = []
    for position in range(count + 1):
        for injection in sorted(slots.get(position, []), key=lambda j: j.order):
            out.append(_Out(injection.role, injection.content, [injection.source]))
        if position < count:
            out.append(history[position])
    return out


def _merge(messages: list[_Out], *, squash_system: bool) -> list[_Out]:
    """Join neighbouring messages of one role into a single message."""
    merged: list[_Out] = []
    for message in messages:
        if not message.content.strip():
            continue
        previous = merged[-1] if merged else None
        same = previous is not None and previous.role == message.role
        if same and (message.role != "system" or squash_system):
            joiner = "\n\n" if message.role == "system" else "\n"
            previous.content = f"{previous.content}{joiner}{message.content}"
            previous.sources = [*previous.sources,
                                *(s for s in message.sources if s not in previous.sources)]
        else:
            merged.append(_Out(message.role, message.content, list(message.sources),
                               message.origin))
    return merged


def build_prompt(inputs: PromptInputs) -> PromptBuild:
    """Assemble the chat messages for one generation (see module docstring)."""
    preset = ensure_engine_blocks(inputs.preset)
    card = inputs.card
    history = [m for m in inputs.history if m.content and m.content.strip()]

    def last_of(role: str) -> str:
        for message in reversed(history):
            if message.role == role:
                return message.content
        return ""

    ctx = MacroContext(
        char=card.name, user=inputs.user_name, card=card, persona=inputs.persona,
        group=list(inputs.group) or [card.name],
        variables=inputs.variables, global_variables=inputs.global_variables,
        rng=inputs.rng, values={k.casefold(): v for k, v in inputs.values.items()},
        resolver=inputs.resolver,
        last_message=history[-1].content if history else "",
        last_user_message=last_of("user"), last_char_message=last_of("assistant"),
        last_message_id=len(history) - 1 if history else None,
        pick_seed=f"{card.id or card.name}|{len(history)}",
        generation_type=inputs.generation_type,
        max_prompt=preset.max_context or None,
    )

    # World book: the pack's book plus this character's own.
    books = [book for book in inputs.lorebooks if book is not None]
    if card.character_book is not None:
        books.append(card.character_book)
    scene = inputs.scene
    extra = "\n".join(part for part in (
        scene.premise if scene else "", scene.location if scene else "",
        inputs.direction) if part)
    lore = activate(
        books, messages=[m.content for m in history], state=inputs.state,
        rng=inputs.rng, extra_text=extra, expand=lambda text: expand(text, ctx),
    ) if books else LoreActivation()
    ctx.outlets = lore.outlets()
    renderer = _Renderer(inputs, ctx, lore)

    reports: list[BlockReport] = []
    before: list[_Out] = []          # everything ahead of the history marker
    after: list[_Out] = []           # everything behind it
    injections: list[_Injection] = []
    examples: list[_Out] = []
    seen_history = False
    history_report: BlockReport | None = None

    for block in preset.blocks:
        position = f"in_chat@{block.depth}" if block.position == "in_chat" else "relative"
        report = BlockReport(id=block.id, name=block.name or block.id, role=block.role,
                             position=position)
        reports.append(report)
        if not block.enabled:
            report.status = "disabled"
            continue
        if block.triggers and inputs.generation_type not in block.triggers:
            report.status, report.note = "trigger", "not sent for this generation type"
            continue

        if block.id == "chatHistory":
            seen_history = True
            history_report = report
            continue
        if block.id == "dialogueExamples":
            examples = _example_messages(inputs, ctx, lore)
            report.role = "mixed"
            report.tokens = sum(estimate_tokens(m.content) for m in examples)
            report.status = "sent" if examples else "empty"
            report.note = f"{len(examples)} messages" if examples else ""
            (after if seen_history else before).extend(examples)
            continue

        is_marker = block.marker or block.id in MARKERS
        if is_marker and not block.content.strip():
            content = renderer.marker(block.id)
        else:
            content = expand(block.content, ctx).strip()
            if block.id == "main" and card.system_prompt.strip() \
                    and not block.forbid_overrides:
                ctx.original = content
                content = expand(card.system_prompt, ctx).strip()
                report.status, report.note = "overridden", "card system_prompt"
            elif block.id == "jailbreak" and card.post_history_instructions.strip() \
                    and not block.forbid_overrides:
                ctx.original = content
                content = expand(card.post_history_instructions, ctx).strip()
                report.status, report.note = "overridden", "card post_history_instructions"
            ctx.original = ""
        if not content:
            if report.status != "overridden":
                report.status = "empty"
            continue
        report.tokens = estimate_tokens(content)
        if block.position == "in_chat":
            injections.append(_Injection(block.depth, block.order, block.role, content,
                                         block.id))
        else:
            (after if seen_history else before).append(_Out(block.role, content,
                                                            [block.id]))

    # In-chat extras: at-depth lore, the author's note (wrapped in its lore),
    # and the card's depth prompt.
    for entry in lore.at("at_depth"):
        if entry.content.strip():
            injections.append(_Injection(entry.depth, entry.order, entry.role,
                                         entry.content.strip(), "worldInfo:at_depth"))
    note = inputs.author_note or AuthorNote()
    note_parts = [lore.text("an_top"), expand(note.content, ctx).strip(),
                  lore.text("an_bottom")]
    note_text = "\n".join(part for part in note_parts if part)
    if note_text:
        injections.append(_Injection(note.depth, 100, note.role, note_text, "authorNote"))
        reports.append(BlockReport(id="authorNote", name="Author's Note", role=note.role,
                                   position=f"in_chat@{note.depth}",
                                   tokens=estimate_tokens(note_text)))
    if card.depth_prompt is not None and card.depth_prompt.prompt.strip():
        text = expand(card.depth_prompt.prompt, ctx).strip()
        if text:
            injections.append(_Injection(card.depth_prompt.depth, 90,
                                         card.depth_prompt.role, text, "charDepthPrompt"))
            reports.append(BlockReport(
                id="charDepthPrompt", name="Character Note", role=card.depth_prompt.role,
                position=f"in_chat@{card.depth_prompt.depth}",
                tokens=estimate_tokens(text)))

    if len(inputs.group) > 1 and preset.group_nudge_prompt.strip():
        nudge = expand(preset.group_nudge_prompt, ctx).strip()
        if nudge:
            after.append(_Out("system", nudge, ["groupNudge"]))
            reports.append(BlockReport(id="groupNudge", name="Group Nudge", role="system",
                                       position="relative",
                                       tokens=estimate_tokens(nudge)))

    history_out = [_Out(m.role, m.content.strip(), ["chatHistory"], "history")
                   for m in history]
    chat_start = expand(preset.new_chat_prompt, ctx).strip()

    def assemble(hist: list[_Out], head: list[_Out]) -> list[_Out]:
        middle = _with_injections(hist, injections)
        opener = [_Out("system", chat_start, ["newChat"])] if chat_start else []
        return _merge([*head, *opener, *middle, *after],
                      squash_system=preset.squash_system_messages)

    # Budget: drop the oldest history first, then examples from the end.
    dropped_history = dropped_examples = 0
    final = assemble(history_out, before)
    total = sum(estimate_tokens(m.content) + 4 for m in final)
    budget = preset.max_context - preset.max_tokens if preset.max_context else 0
    if budget > 0:
        head = list(before)
        while total > budget and len(history_out) > 1:
            history_out = history_out[1:]
            dropped_history += 1
            final = assemble(history_out, head)
            total = sum(estimate_tokens(m.content) + 4 for m in final)
        while total > budget and any(m.origin == "example" for m in head):
            for index in range(len(head) - 1, -1, -1):
                if head[index].origin == "example":
                    del head[index]
                    dropped_examples += 1
                    break
            final = assemble(history_out, head)
            total = sum(estimate_tokens(m.content) + 4 for m in final)

    if history_report is not None:
        history_report.role = "mixed"
        history_report.tokens = sum(estimate_tokens(m.content) for m in history_out)
        history_report.status = "sent" if history_out else "empty"
        history_report.note = f"{len(history_out)} messages" + (
            f", {dropped_history} oldest dropped" if dropped_history else "")
    if dropped_examples:
        for report in reports:
            if report.id == "dialogueExamples":
                report.note += f", {dropped_examples} dropped for budget"

    return PromptBuild(
        messages=[{"role": m.role, "content": m.content} for m in final],
        blocks=reports, lore=lore, tokens=total, budget=max(0, budget),
        dropped_history=dropped_history, dropped_examples=dropped_examples,
        unknown_macros=list(ctx.unknown), banned=list(ctx.banned),
        sources=[list(m.sources) for m in final],
    )
