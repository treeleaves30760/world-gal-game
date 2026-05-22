"""LLM brain interface for NPCs.

This module defines the abstract `LLMBrain` and a deterministic
`EchoBrain` placeholder. The actual LLM-backed brain (e.g. ClaudeBrain)
is not yet implemented; EchoBrain is a deterministic placeholder — the
interface below is the seam where it will plug in.

The dialogue engine takes an optional `llm_provider` callable, so
content with `llm_speaker: true` lines gracefully falls back to
`line.text` when no live brain is wired up. Games written today should
just rely on hand-authored `line.text`.
"""
from __future__ import annotations

from typing import Any, Callable

from .npc_base import NPC, NPCRegistry


class LLMBrain:
    """Abstract brain. Future ClaudeBrain / LocalLLMBrain implement this."""

    def respond(self, *, npc: NPC, system_prompt: str,
                user_context: str, history: list[dict[str, str]] | None = None) -> str:
        raise NotImplementedError


class EchoBrain(LLMBrain):
    """Deterministic placeholder so the engine can boot without an LLM.

    Returns a stub line. Real games should drive NPC speech through
    hand-authored `line.text` in scene YAML until a real brain is wired
    back in.
    """

    def respond(self, *, npc: NPC, system_prompt: str,
                user_context: str, history: list[dict[str, str]] | None = None) -> str:
        return f"（{npc.name} 看了你一眼，沒有立刻回答。）"


def build_llm_provider(registry: NPCRegistry, brain: LLMBrain) -> Callable[..., str]:
    """Adapt a Brain to the callable DialogueEngine expects.

    Kept for future use. While no live brain is wired up, the engine
    passes `llm_provider=None` to DialogueEngine and llm_speaker lines
    fall back to `line.text`.
    """

    def provider(*, speaker: str, directive: str, state, scene) -> str:
        npc = registry.get(speaker)
        if npc is None:
            return f"（{speaker} 沒有出現。）"
        loc = state.map.current
        loc_label = loc.name if loc else "（不明地點）"
        time_label = state.time.time_of_day.label
        recent = [f"[{e.kind}] {e.title}" for e in state.events.recent(8)]
        system = npc.system_prompt(
            player_name=state.player.name,
            affection=state.affection.get(npc.id),
            location=loc_label,
            time_of_day=time_label,
            recent_events=recent,
        )
        scene_title = scene.title or scene.id
        user = (
            f"場景：{scene_title}\n"
            f"地點：{loc_label}\n"
            f"時刻：{time_label}\n"
            f"玩家剛剛的行動/輸入：{directive or '（玩家在等你開口）'}\n\n"
            f"請以 {npc.name} 的身份說一段自然的對白，1~3 句。"
        )
        history: list[dict[str, str]] = []
        for e in state.events.filter(actor=npc.id)[-6:]:
            history.append({"role": "user", "content": e.title})
        try:
            reply = brain.respond(
                npc=npc, system_prompt=system, user_context=user,
                history=history,
            )
        except Exception as e:
            return f"（{npc.name} 看起來分心了。）[brain-error: {e}]"
        npc.append_memory(
            f"{state.player.name} 在「{loc_label}」遇見我，我回了：{reply}"
        )
        state.events.record(
            kind="dialogue", title=f"{npc.name}：{reply[:40]}",
            location=state.map.current_location_id,
            actors=[npc.id],
            data={"speaker": npc.id, "scene": scene.id, "reply": reply},
        )
        return reply

    return provider


def default_brain() -> LLMBrain:
    """Until a real LLM brain ships, this is always EchoBrain.

    Future re-integration: detect ANTHROPIC_API_KEY (or other provider
    credentials) and return the matching brain. The rest of the engine
    needs no changes.
    """
    return EchoBrain()
