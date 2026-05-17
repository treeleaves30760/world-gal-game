"""Achievement system.

Achievements are declarative records loaded from ``content/achievements.yaml``.
Each achievement names one or more conditions that, when all satisfied,
unlock it. Conditions reuse the existing :class:`Condition` model, so any
predicate the story graph can check (flag, affection >= N, scene played,
location visited, ...) can drive an achievement.

The :class:`AchievementTracker` lives on the GameState and is re-checked
every time the engine applies an effect; nothing else has to know about
it. Newly unlocked achievements get appended to the event log so they
show up alongside other story beats.

Example YAML::

    achievements:
      - id: ach_first_meeting
        title: "第一次見面"
        description: "與校園中任何一位女主角第一次說上話。"
        icon: assets/ui/ach_meet.png
        hidden: false
        requires:
          - {kind: flag, target: met_qingyi}

      - id: ach_qingyi_lover
        title: "舊書與晚風"
        description: "與林青衣的故事，走到了結局。"
        icon: assets/ui/ach_qingyi_lover.png
        hidden: true
        requires:
          - {kind: flag, target: ending_qingyi}
"""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field

from .story_graph import Condition


class Achievement(BaseModel):
    id: str
    title: str
    description: str = ""
    icon: str | None = None
    hidden: bool = False
    requires: list[Condition] = Field(default_factory=list)
    # Optional secondary clauses; conditions in `forbids` MUST be false.
    forbids: list[Condition] = Field(default_factory=list)


class AchievementTracker(BaseModel):
    achievements: dict[str, Achievement] = Field(default_factory=dict)
    unlocked: dict[str, str] = Field(default_factory=dict)  # id -> ISO timestamp
    seen: set[str] = Field(default_factory=set)              # acknowledged by UI

    def register(self, ach: Achievement) -> None:
        self.achievements[ach.id] = ach

    def is_unlocked(self, ach_id: str) -> bool:
        return ach_id in self.unlocked

    def all(self) -> list[Achievement]:
        return list(self.achievements.values())

    def visible_to_player(self) -> list[Achievement]:
        """Hidden achievements are invisible until unlocked."""
        return [a for a in self.achievements.values()
                if not a.hidden or a.id in self.unlocked]

    def check(self, state, *, now: str | None = None) -> list[Achievement]:
        """Evaluate all achievements; return newly unlocked ones.

        Pass the GameState as ``state``; the tracker reuses its
        ``evaluate_all`` / ``evaluate_none`` to check predicates.
        """
        from datetime import datetime, timezone
        ts = now or datetime.now(timezone.utc).isoformat()
        new: list[Achievement] = []
        for ach in self.achievements.values():
            if ach.id in self.unlocked:
                continue
            if ach.requires and not state.evaluate_all(ach.requires):
                continue
            if ach.forbids and not state.evaluate_none(ach.forbids):
                continue
            self.unlocked[ach.id] = ts
            new.append(ach)
        return new

    def mark_seen(self, ach_id: str) -> None:
        self.seen.add(ach_id)

    def newly_unlocked(self) -> list[Achievement]:
        """Unlocked achievements the player hasn't acknowledged yet."""
        return [self.achievements[i] for i in self.unlocked
                if i in self.achievements and i not in self.seen]
