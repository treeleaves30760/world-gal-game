"""NPC system.

The `Brain` interface is kept here so a future LLM-backed brain can be
wired in without touching the rest of the engine. As of this release no
live LLM brain ships; `EchoBrain` is a deterministic placeholder.
"""

from .npc_base import NPC, NPCRegistry, NPCMemory
from .llm_brain import LLMBrain, EchoBrain, build_llm_provider, default_brain

__all__ = [
    "NPC",
    "NPCRegistry",
    "NPCMemory",
    "LLMBrain",
    "EchoBrain",
    "build_llm_provider",
    "default_brain",
]
