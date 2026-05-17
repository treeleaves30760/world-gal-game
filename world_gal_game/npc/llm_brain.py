"""LLM brain for NPCs.

The engine deliberately keeps the LLM behind a `Brain` interface so it can
be swapped out: real Claude API in production, a simple deterministic
"echo" brain for testing, or a local LLM via httpx.

A "brain" turns (NPC persona + game state + recent dialogue) into the next
line of NPC dialogue. The engine.dialogue.DialogueEngine accepts a
callable as `llm_provider`; this module's build_llm_provider wraps a Brain
into that callable.
"""
from __future__ import annotations

import os
from typing import Any, Callable

from .npc_base import NPC, NPCRegistry


class LLMBrain:
    """Interface every brain implements."""

    def respond(self, *, npc: NPC, system_prompt: str,
                user_context: str, history: list[dict[str, str]] | None = None) -> str:
        raise NotImplementedError


class EchoBrain(LLMBrain):
    """Deterministic fallback for offline / testing."""

    def respond(self, *, npc: NPC, system_prompt: str,
                user_context: str, history: list[dict[str, str]] | None = None) -> str:
        return f"（{npc.name} 看了你一眼，沒有立刻回答。）"


class ClaudeBrain(LLMBrain):
    """Anthropic Claude brain. Requires ANTHROPIC_API_KEY in env."""

    def __init__(self, model: str = "claude-haiku-4-5-20251001",
                 max_tokens: int = 256, temperature: float = 0.9):
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from anthropic import Anthropic
            except ImportError as e:
                raise RuntimeError("Install anthropic: pip install anthropic") from e
            self._client = Anthropic()
        return self._client

    def respond(self, *, npc: NPC, system_prompt: str,
                user_context: str, history: list[dict[str, str]] | None = None) -> str:
        client = self._get_client()
        messages: list[dict[str, Any]] = []
        if history:
            for h in history:
                if h.get("role") in ("user", "assistant"):
                    messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": user_context})
        result = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=system_prompt,
            messages=messages,
        )
        # Anthropic SDK returns content blocks.
        chunks: list[str] = []
        for block in result.content:
            if getattr(block, "type", None) == "text":
                chunks.append(block.text)
        text = "".join(chunks).strip()
        return text or f"（{npc.name} 沒有說話。）"


def build_llm_provider(registry: NPCRegistry, brain: LLMBrain) -> Callable[..., str]:
    """Adapt a Brain to the callable DialogueEngine expects.

    The dialogue engine calls llm_provider(speaker=..., directive=...,
    state=..., scene=...) when it hits a line marked llm_speaker=True.
    """

    def provider(*, speaker: str, directive: str, state, scene) -> str:
        npc = registry.get(speaker)
        if npc is None:
            return f"（{speaker} 沒有出現。）"

        # Build the system + user prompts.
        loc = state.map.current
        loc_label = loc.name if loc else "（不明地點）"
        time_label = state.time.time_of_day.label
        recent = [
            f"[{e.kind}] {e.title}" for e in state.events.recent(8)
        ]
        system = npc.system_prompt(
            player_name=state.player.name,
            affection=state.affection.get(npc.id),
            location=loc_label,
            time_of_day=time_label,
            recent_events=recent,
        )

        # Construct the user message: scene context + directive.
        scene_title = scene.title or scene.id
        user = (
            f"場景：{scene_title}\n"
            f"地點：{loc_label}\n"
            f"時刻：{time_label}\n"
            f"玩家剛剛的行動/輸入：{directive or '（玩家在等你開口）'}\n\n"
            f"請以 {npc.name} 的身份說一段自然的對白，1~3 句。"
        )

        # History: pull the last few dialogue lines.
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
        # Remember this exchange.
        npc.append_memory(f"{state.player.name} 在「{loc_label}」遇見我，我回了：{reply}")
        state.events.record(
            kind="dialogue", title=f"{npc.name}：{reply[:40]}",
            location=state.map.current_location_id,
            actors=[npc.id],
            data={"speaker": npc.id, "scene": scene.id, "reply": reply},
        )
        return reply

    return provider


def default_brain() -> LLMBrain:
    """Auto-select Claude if an API key is set, otherwise Echo."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return ClaudeBrain()
    return EchoBrain()
