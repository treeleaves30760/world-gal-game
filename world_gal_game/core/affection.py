"""Affection / relationship tracking system.

Tracks how each character feels about the player. Supports multiple
stat axes per character (affection, trust, fear, etc.) so a ghost-story
gal-game can model both romance and dread on the same NPC.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from pydantic import BaseModel, Field


class AffectionThreshold(BaseModel):
    """A named threshold that unlocks content when crossed."""

    name: str
    value: int
    unlocks: list[str] = Field(default_factory=list)


class CharacterAffection(BaseModel):
    """Per-character affection profile."""

    character_id: str
    stats: dict[str, int] = Field(default_factory=lambda: {"affection": 0})
    thresholds: list[AffectionThreshold] = Field(default_factory=list)
    unlocked: set[str] = Field(default_factory=set)

    model_config = {"arbitrary_types_allowed": True}

    def get(self, stat: str = "affection") -> int:
        return self.stats.get(stat, 0)

    def adjust(self, delta: int, stat: str = "affection") -> int:
        new_val = self.stats.get(stat, 0) + delta
        self.stats[stat] = new_val
        return new_val

    def set_value(self, value: int, stat: str = "affection") -> None:
        self.stats[stat] = value

    def check_thresholds(self, stat: str = "affection") -> list[str]:
        """Return newly unlocked content keys after threshold checks."""
        newly_unlocked: list[str] = []
        current = self.stats.get(stat, 0)
        for th in self.thresholds:
            if current >= th.value:
                for u in th.unlocks:
                    if u not in self.unlocked:
                        self.unlocked.add(u)
                        newly_unlocked.append(u)
        return newly_unlocked


class AffectionTracker(BaseModel):
    """Top-level tracker for all character affections."""

    characters: dict[str, CharacterAffection] = Field(default_factory=dict)

    def register(self, character_id: str,
                 stats: dict[str, int] | None = None,
                 thresholds: list[AffectionThreshold] | None = None) -> CharacterAffection:
        if character_id in self.characters:
            return self.characters[character_id]
        ca = CharacterAffection(
            character_id=character_id,
            stats=stats or {"affection": 0},
            thresholds=thresholds or [],
        )
        self.characters[character_id] = ca
        return ca

    def get(self, character_id: str, stat: str = "affection") -> int:
        if character_id not in self.characters:
            return 0
        return self.characters[character_id].get(stat)

    def adjust(self, character_id: str, delta: int,
               stat: str = "affection") -> tuple[int, list[str]]:
        """Adjust a character's stat. Returns (new_value, newly_unlocked)."""
        if character_id not in self.characters:
            self.register(character_id)
        ca = self.characters[character_id]
        new_val = ca.adjust(delta, stat)
        unlocked = ca.check_thresholds(stat)
        return new_val, unlocked

    def all_stats(self) -> dict[str, dict[str, int]]:
        return {cid: dict(ca.stats) for cid, ca in self.characters.items()}

    # Class-level slot for a Localization instance set by the App at
    # startup. Default is None → fall back to bundled labels.
    _localization: object | None = None

    def level_label(self, character_id: str, stat: str = "affection") -> str:
        """Human-readable label for the current affection level.

        If a Localization has been bound via ``bind_localization`` (the App
        does this at startup), affection-stat labels come from there.
        Other stats fall back to a generic high/low scale.
        """
        v = self.get(character_id, stat)
        if stat == "affection" and self._localization is not None:
            return self._localization.affection_label(v)
        if stat == "affection":
            # Hard-coded fallback (matches the default in localization.py).
            if v < 0:    return "敵意"
            if v < 10:   return "陌生"
            if v < 25:   return "認識"
            if v < 50:   return "朋友"
            if v < 80:   return "好友"
            if v < 120:  return "心動"
            return "戀人"
        # Generic scale for non-affection stats (trust, fear, ...).
        if v < 0:   return "極低"
        if v < 10:  return "低"
        if v < 25:  return "普通"
        if v < 50:  return "高"
        if v < 80:  return "很高"
        if v < 120: return "極高"
        return "滿"

    def bind_localization(self, localization) -> None:
        """Bind a Localization instance so level_label uses pack overrides."""
        type(self)._localization = localization
