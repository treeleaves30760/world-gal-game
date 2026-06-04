"""NPC base classes.

An NPC is more than a portrait + dialogue: it carries a persona, a memory
of recent interactions, and (optionally) an LLM brain that turns the
current state into the next utterance.
"""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field


from ..core.shop import Shop


class NPCMemory(BaseModel):
    """Bounded short-term memory the LLM can read."""

    events: list[str] = Field(default_factory=list)
    max_entries: int = 25

    def remember(self, text: str) -> None:
        self.events.append(text)
        if len(self.events) > self.max_entries:
            self.events = self.events[-self.max_entries:]

    def as_block(self) -> str:
        if not self.events:
            return "（這個角色目前對玩家還沒有特殊回憶）"
        return "\n".join(f"- {e}" for e in self.events)


class NPC(BaseModel):
    """One NPC."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    # Optional speaker-name-plate colour (the per-character name colouring
    # commercial VNs use). A "#rgb" / "#rrggbb" / named-colour string parsed by
    # the rich-text colour parser; None keeps the theme accent. Purely cosmetic.
    name_color: str | None = None
    role: str = ""
    age: int | None = None
    portrait: str | None = None
    portrait_set: dict[str, str] = Field(default_factory=dict)  # expression -> path
    # Resting-animation backend applied to *this character's* portraits even
    # when a scene line only names an expression (no explicit PortraitSpec).
    # "static" = the unchanged still blit. "breath" makes the existing flat
    # portrait gently breathe with no extra art; "layered" needs eye/mouth
    # layer PNGs declared in portrait_backend_args. Routed through the same
    # @portrait_backend registry, so an unregistered name degrades to static.
    portrait_backend: str = "static"
    portrait_backend_args: dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    persona: str = ""              # how they speak / behave
    voice: str = ""                # tone, speech style
    backstory: str = ""
    secrets: list[str] = Field(default_factory=list)
    likes: list[str] = Field(default_factory=list)
    dislikes: list[str] = Field(default_factory=list)
    affiliated_location: str | None = None
    associated_ghost_story: str | None = None
    route_id: str | None = None
    is_heroine: bool = False
    memory: NPCMemory = Field(default_factory=NPCMemory)
    tags: list[str] = Field(default_factory=list)
    llm_brain: bool = False
    llm_model_hint: str | None = None
    safe_topics: list[str] = Field(default_factory=list)
    # When set, the NPC is a merchant: opening "shop" on them surfaces a
    # ShopScene overlay. Strings reference world_gal_game.core.shop.Shop.
    # The forward reference is resolved by the content loader.
    shop: "Shop | None" = None   # noqa: F821 -- resolved post-import

    def portrait_for(self, expression: str | None) -> str | None:
        if expression and expression in self.portrait_set:
            return self.portrait_set[expression]
        return self.portrait

    def system_prompt(self, *, player_name: str,
                      affection: int, location: str,
                      time_of_day: str, recent_events: list[str]) -> str:
        """Compose a system prompt for the LLM to act as this NPC."""
        likes = "、".join(self.likes) or "（無）"
        dislikes = "、".join(self.dislikes) or "（無）"
        recents = "\n".join(f"- {e}" for e in recent_events[-8:]) or "（無）"
        return f"""你正在扮演一個 Gal-Game 中的角色。請完全進入角色扮演，使用第一人稱說話。
不要解釋自己是 AI、不要破壞第四面牆、不要列出選項，回覆 1~3 句自然口語的中文對白即可，
必要時可在動作前後加上以「（）」包起的肢體語言或表情描寫，例如「（撥了撥頭髮）」。

【角色設定】
姓名：{self.name}
身份：{self.role}
性格 / 說話風格：{self.persona or self.voice}
背景：{self.backstory}
喜歡：{likes}
討厭：{dislikes}

【角色內心，請依此驅動你的反應】
{self.description}

【現在的場景】
地點：{location}
時間：{time_of_day}
玩家姓名：{player_name}
你對玩家目前的好感度（0~150）：{affection}
最近發生的事：
{recents}
這個角色記得的對玩家的事：
{self.memory.as_block()}

【說話規則】
- 只輸出該角色「現在」會說的對白，不要敘述劇情、不要加旁白標題。
- 對白可帶情緒；好感越高越親密、越敢開玩笑；越低越客氣或冷淡。
- 若玩家提及這個角色的禁忌或祕密，要表現出明顯不安或閃躲，不可直接揭密。
- 若處於深夜場合且這個角色與某段鬼故事有關，可流露不自然或令人毛骨悚然的細節，但仍保持 1~3 句。
"""

    def append_memory(self, line: str) -> None:
        self.memory.remember(line)


class NPCRegistry(BaseModel):
    npcs: dict[str, NPC] = Field(default_factory=dict)

    def add(self, npc: NPC) -> None:
        self.npcs[npc.id] = npc

    def get(self, npc_id: str) -> NPC | None:
        return self.npcs.get(npc_id)

    def by_name(self, name: str) -> NPC | None:
        """Resolve a dialogue ``speaker`` (a display name like "林青衣") back to
        its NPC. The registry is keyed by id, but lines carry the display name,
        so match on ``.name`` first and fall back to an id lookup (a pack may
        use the id as the speaker)."""
        if not name:
            return None
        for npc in self.npcs.values():
            if npc.name == name:
                return npc
        return self.npcs.get(name)

    def all(self) -> list[NPC]:
        return list(self.npcs.values())

    def heroines(self) -> list[NPC]:
        return [n for n in self.npcs.values() if n.is_heroine]
