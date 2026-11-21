"""Prompt presets: the ordered list of blocks a prompt is assembled from.

A tavern "preset" is a recipe: which blocks go into the prompt, in what order,
with what role, and whether a block sits at a fixed place or is injected a
given number of messages from the end of the chat. Some blocks carry their own
text (the main prompt, the post-history instruction); others are *markers* the
engine fills in (the character description, the world book, the chat history).

:class:`Preset` models that recipe and reads the Chat Completion presets that
tavern front-ends export (``prompts`` + ``prompt_order``), so a preset someone
already tuned can be dropped into ``content/tavern/presets/``.

The engine adds a few marker blocks of its own — the speech profile, the
character's private knowledge, the scene context, the expression list, the
style guide and the per-turn direction. A preset imported from elsewhere does
not know about them, so :func:`ensure_engine_blocks` slots any that are missing
into sensible places (switch that off with ``engine_blocks: false``).

Sampler fields (``temperature`` and friends) are carried for the backends that
accept them. Current Claude models do not take sampling parameters at all, so
the Anthropic backend ignores them and uses ``effort`` instead.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .card import Role
from .style import normalize_locale

BlockPosition = Literal["relative", "in_chat"]

#: Markers tavern presets define; the engine fills them.
TAVERN_MARKERS = (
    "worldInfoBefore", "personaDescription", "charDescription", "charPersonality",
    "scenario", "worldInfoAfter", "dialogueExamples", "chatHistory",
)
#: Markers the engine adds.
ENGINE_MARKERS = (
    "speechProfile", "privateKnowledge", "sceneContext", "vnDirectives",
    "styleGuide", "direction",
)
MARKERS = frozenset(TAVERN_MARKERS + ENGINE_MARKERS)


class PromptBlock(BaseModel):
    """One entry of a preset."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str = ""
    role: Role = "system"
    content: str = ""
    marker: bool = False
    enabled: bool = True
    position: BlockPosition = "relative"
    depth: int = Field(default=4, ge=0)       # in_chat: messages from the end
    order: int = 100                          # in_chat: order within one depth
    #: Only send for these generation types (empty = always).
    triggers: list[str] = Field(default_factory=list)
    #: A card's own system prompt / post-history text may not replace this.
    forbid_overrides: bool = False


class Preset(BaseModel):
    """An ordered prompt recipe plus generation settings."""

    model_config = ConfigDict(extra="forbid")

    id: str = "custom"
    name: str = ""
    description: str = ""
    locale: str = ""
    blocks: list[PromptBlock] = Field(default_factory=list)
    wi_format: str = "{0}"
    scenario_format: str = "{{scenario}}"
    personality_format: str = "{{personality}}"
    new_chat_prompt: str = ""
    new_example_chat_prompt: str = ""
    group_nudge_prompt: str = ""
    squash_system_messages: bool = False
    #: Trim history (then examples) to fit this many estimated tokens. 0 = off.
    max_context: int = Field(default=0, ge=0)
    max_tokens: int = Field(default=0, ge=0)           # 0 = backend default
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    effort: str = ""                                    # low|medium|high|xhigh|max
    #: Add the engine's marker blocks when the preset lacks them.
    engine_blocks: bool = True
    extensions: dict[str, Any] = Field(default_factory=dict)

    def block(self, block_id: str) -> PromptBlock | None:
        for block in self.blocks:
            if block.id == block_id:
                return block
        return None

    # ------------------------------------------------------------------

    @classmethod
    def builtin(cls, locale: str = "zh-Hant") -> "Preset":
        """The engine's default acting preset for ``locale``."""
        key = normalize_locale(locale)
        text = _BUILTIN_TEXT.get(key) or _BUILTIN_TEXT["en"]
        blocks = [
            PromptBlock(id="main", name=text["main_name"], content=text["main"]),
            PromptBlock(id="worldInfoBefore", name="World Info (before)", marker=True),
            PromptBlock(id="charDescription", name="Char Description", marker=True),
            PromptBlock(id="charPersonality", name="Char Personality", marker=True),
            PromptBlock(id="speechProfile", name="Speech Profile", marker=True),
            PromptBlock(id="privateKnowledge", name="Private Knowledge", marker=True),
            PromptBlock(id="scenario", name="Scenario", marker=True),
            PromptBlock(id="worldInfoAfter", name="World Info (after)", marker=True),
            PromptBlock(id="personaDescription", name="Persona Description", marker=True),
            PromptBlock(id="sceneContext", name="Scene Context", marker=True),
            PromptBlock(id="vnDirectives", name="Expressions", marker=True),
            PromptBlock(id="dialogueExamples", name="Chat Examples", marker=True),
            PromptBlock(id="chatHistory", name="Chat History", marker=True),
            PromptBlock(id="jailbreak", name="Post-History Instructions", content=""),
            PromptBlock(id="styleGuide", name="Style Guide", marker=True),
            PromptBlock(id="direction", name="Direction", marker=True),
        ]
        return cls(
            id="galgame", name=text["name"], description=text["description"],
            locale=key, blocks=blocks,
            new_example_chat_prompt=text["new_example_chat_prompt"],
            new_chat_prompt=text["new_chat_prompt"],
        )

    @classmethod
    def from_tavern(cls, obj: dict[str, Any], *, name: str = "") -> "Preset":
        """Read a tavern Chat Completion preset (``prompts`` + ``prompt_order``)."""
        if not isinstance(obj, dict):
            raise ValueError("preset must be a JSON object")
        prompts = obj.get("prompts")
        if not isinstance(prompts, list):
            raise ValueError(
                "preset has no 'prompts' array — this looks like a Text Completion "
                "preset; only Chat Completion presets are supported")
        by_id: dict[str, dict[str, Any]] = {}
        for raw in prompts:
            if isinstance(raw, dict) and raw.get("identifier"):
                by_id[str(raw["identifier"])] = raw

        order: list[tuple[str, bool]] = []
        prompt_order = obj.get("prompt_order")
        if isinstance(prompt_order, list) and prompt_order:
            chosen = None
            for group in prompt_order:
                if isinstance(group, dict) and group.get("character_id") == 100001:
                    chosen = group
            if chosen is None:
                chosen = next((g for g in reversed(prompt_order) if isinstance(g, dict)),
                              None)
            for item in (chosen or {}).get("order") or []:
                if isinstance(item, dict) and item.get("identifier"):
                    order.append((str(item["identifier"]), bool(item.get("enabled", True))))
        if not order:
            order = [(ident, bool(raw.get("enabled", True))) for ident, raw in by_id.items()]

        def role_of(raw: dict[str, Any]) -> Role:
            role = raw.get("role") or "system"
            return role if role in ("system", "user", "assistant") else "system"

        blocks: list[PromptBlock] = []
        for ident, enabled in order:
            raw = by_id.get(ident, {})
            triggers = raw.get("injection_trigger") or []
            try:
                depth = max(0, int(raw.get("injection_depth", 4) or 0))
                inj_order = int(raw.get("injection_order", 100) or 0)
            except (TypeError, ValueError):
                depth, inj_order = 4, 100
            blocks.append(PromptBlock(
                id=ident,
                name=str(raw.get("name") or ident),
                role=role_of(raw),
                content=str(raw.get("content") or ""),
                marker=bool(raw.get("marker", False)) or ident in MARKERS,
                enabled=enabled,
                position="in_chat" if raw.get("injection_position") == 1 else "relative",
                depth=depth,
                order=inj_order,
                triggers=[str(t) for t in triggers if isinstance(t, str)],
                forbid_overrides=bool(raw.get("forbid_overrides", False)),
            ))

        def number(key: str) -> float | None:
            value = obj.get(key)
            return float(value) if isinstance(value, (int, float)) else None

        def text(key: str, default: str = "") -> str:
            value = obj.get(key)
            return value if isinstance(value, str) else default

        top_k = obj.get("top_k")
        return cls(
            id=name or "imported",
            name=name or text("name", "imported"),
            blocks=blocks,
            wi_format=text("wi_format", "{0}") or "{0}",
            scenario_format=text("scenario_format", "{{scenario}}") or "{{scenario}}",
            personality_format=text("personality_format", "{{personality}}")
            or "{{personality}}",
            new_chat_prompt=text("new_chat_prompt"),
            new_example_chat_prompt=text("new_example_chat_prompt"),
            group_nudge_prompt=text("group_nudge_prompt"),
            squash_system_messages=bool(obj.get("squash_system_messages", False)),
            max_context=int(obj.get("openai_max_context") or 0)
            if isinstance(obj.get("openai_max_context"), (int, float)) else 0,
            max_tokens=int(obj.get("openai_max_tokens") or 0)
            if isinstance(obj.get("openai_max_tokens"), (int, float)) else 0,
            temperature=number("temperature"),
            top_p=number("top_p"),
            top_k=int(top_k) if isinstance(top_k, (int, float)) and top_k else None,
            frequency_penalty=number("frequency_penalty"),
            presence_penalty=number("presence_penalty"),
        )

    @classmethod
    def load(cls, path: Path) -> "Preset":
        """Load ``.yaml`` (native) or ``.json`` (tavern or native)."""
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            raw = json.loads(text)
        else:
            raw = yaml.safe_load(text)
        if not isinstance(raw, dict):
            raise ValueError(f"{path.name}: preset must be a mapping")
        if "prompts" in raw:
            preset = cls.from_tavern(raw, name=path.stem)
        else:
            preset = cls.model_validate(raw)
        if preset.id in ("", "custom", "imported"):
            preset.id = path.stem
        if not preset.name:
            preset.name = path.stem
        return preset


def ensure_engine_blocks(preset: Preset) -> Preset:
    """Return ``preset`` with any missing engine marker blocks slotted in.

    Character-side blocks go right after the last character definition block,
    scene-side blocks just before the examples / history, and the style guide
    and direction at the very end, where an instruction is heeded most.
    """
    if not preset.engine_blocks:
        return preset
    have = {block.id for block in preset.blocks}
    missing = [marker for marker in ENGINE_MARKERS if marker not in have]
    if not missing:
        return preset
    blocks = list(preset.blocks)

    def index_of(*ids: str, last: bool = False) -> int | None:
        found = [i for i, block in enumerate(blocks) if block.id in ids]
        if not found:
            return None
        return found[-1] if last else found[0]

    def insert(at: int | None, block: PromptBlock) -> None:
        if at is None:
            blocks.append(block)
        else:
            blocks.insert(at, block)

    char_side = [m for m in ("speechProfile", "privateKnowledge") if m in missing]
    anchor = index_of("charPersonality", "charDescription", last=True)
    at = None if anchor is None else anchor + 1
    if at is None:
        at = index_of("dialogueExamples", "chatHistory")
    for offset, marker in enumerate(char_side):
        insert(None if at is None else at + offset,
               PromptBlock(id=marker, name=marker, marker=True))

    scene_side = [m for m in ("sceneContext", "vnDirectives") if m in missing]
    at = index_of("dialogueExamples", "chatHistory")
    for offset, marker in enumerate(scene_side):
        insert(None if at is None else at + offset,
               PromptBlock(id=marker, name=marker, marker=True))

    for marker in ("styleGuide", "direction"):
        if marker in missing:
            blocks.append(PromptBlock(id=marker, name=marker, marker=True))
    return preset.model_copy(update={"blocks": blocks})


_BUILTIN_TEXT: dict[str, dict[str, str]] = {
    "zh-Hant": {
        "name": "Galgame 排演",
        "description": "引擎內建：多演員排演用的角色提示詞，重點在口語、留白、不說破。",
        "main_name": "主提示詞",
        "main": (
            "這是一場多人排演，目的是寫出視覺小說裡的對白。"
            "場上每個角色各由一位演員負責，你只演「{{char}}」。\n\n"
            "你只知道{{char}}知道的事，只看得到{{char}}看得到的東西。"
            "別的角色心裡在想什麼、這場戲要往哪裡走，你都不知道，也不用替他們安排。\n"
            "{{char}}有自己想要的東西，也有不想被知道的事。照{{char}}的立場反應就好："
            "不必配合對方，不必把話說完，也不必讓這場戲有結論。"
        ),
        "new_example_chat_prompt": "［以下是{{char}}平常說話的樣子。只用來抓語氣，內容不要照抄。］",
        "new_chat_prompt": "［排演開始］",
    },
    "zh-Hans": {
        "name": "Galgame 排演",
        "description": "引擎内建：多演员排演用的角色提示词，重点在口语、留白、不说破。",
        "main_name": "主提示词",
        "main": (
            "这是一场多人排演，目的是写出视觉小说里的对白。"
            "场上每个角色各由一位演员负责，你只演「{{char}}」。\n\n"
            "你只知道{{char}}知道的事，只看得到{{char}}看得到的东西。"
            "别的角色心里在想什么、这场戏要往哪里走，你都不知道，也不用替他们安排。\n"
            "{{char}}有自己想要的东西，也有不想被知道的事。照{{char}}的立场反应就好："
            "不必配合对方，不必把话说完，也不必让这场戏有结论。"
        ),
        "new_example_chat_prompt": "［以下是{{char}}平常说话的样子。只用来抓语气，内容不要照抄。］",
        "new_chat_prompt": "［排演开始］",
    },
    "en": {
        "name": "Galgame rehearsal",
        "description": "Built-in acting preset for a multi-actor rehearsal: spoken, "
                       "understated, nothing spelled out.",
        "main_name": "Main Prompt",
        "main": (
            "This is a rehearsal with several actors, held to write the dialogue of a "
            "visual novel. Each character on stage is played by a different actor; you "
            "play only {{char}}.\n\n"
            "You know only what {{char}} knows and see only what {{char}} can see. You "
            "don't know what the others are thinking or where the scene is headed, and "
            "you don't arrange it for them.\n"
            "{{char}} wants things and has things to hide. React from {{char}}'s "
            "position: no need to cooperate, to finish the thought, or to bring the "
            "scene to a conclusion."
        ),
        "new_example_chat_prompt": "[How {{char}} usually talks. Use it for the voice "
                                   "only; do not reuse the content.]",
        "new_chat_prompt": "[Rehearsal begins]",
    },
}
_BUILTIN_TEXT["ja"] = _BUILTIN_TEXT["en"]
