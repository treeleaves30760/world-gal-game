"""Ending system.

Endings are declarative records loaded from ``content/endings.yaml``. Each
ending names one or more conditions that, when all satisfied, unlock it.
Conditions reuse the existing :class:`Condition` model, so any predicate the
story graph can check (flag, affection >= N, scene played, ...) can drive an
ending — typically an ``ending_*`` flag set at the close of a route.

The :class:`EndingTracker` lives on the GameState and is re-checked every time
the engine applies an effect, mirroring :class:`AchievementTracker`; nothing
else has to know about it. Newly unlocked endings get appended to the event
log and surface in the endings / completion screen.

Example YAML::

    endings:
      - id: ending_heroine_1_lover
        title: "湖畔的承諾"
        description: "與女主角的故事，走到了戀人結局。"
        icon: assets/ui/ending_heroine_1_lover.png
        route_id: heroine_1
        hidden: true
        requires:
          - {kind: flag, target: ending_heroine_1}
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from .story_graph import Condition


class Ending(BaseModel):
    id: str
    title: str
    description: str = ""
    icon: str | None = None
    # Which heroine / story route this ending belongs to (groups the UI).
    route_id: str | None = None
    hidden: bool = False
    requires: list[Condition] = Field(default_factory=list)
    # Optional secondary clauses; conditions in `forbids` MUST be false.
    forbids: list[Condition] = Field(default_factory=list)


class EndingTracker(BaseModel):
    endings: dict[str, Ending] = Field(default_factory=dict)
    unlocked: dict[str, str] = Field(default_factory=dict)  # id -> ISO timestamp
    seen: set[str] = Field(default_factory=set)              # acknowledged by UI

    def register(self, ending: Ending) -> None:
        self.endings[ending.id] = ending

    def is_unlocked(self, ending_id: str) -> bool:
        return ending_id in self.unlocked

    def all(self) -> list[Ending]:
        return list(self.endings.values())

    def visible_to_player(self) -> list[Ending]:
        """Hidden endings are invisible until unlocked."""
        return [e for e in self.endings.values()
                if not e.hidden or e.id in self.unlocked]

    def check(self, state, *, now: str | None = None) -> list[Ending]:
        """Evaluate all endings; return newly unlocked ones.

        Pass the GameState as ``state``; the tracker reuses its
        ``evaluate_all`` / ``evaluate_none`` to check predicates.
        """
        from datetime import datetime, timezone
        ts = now or datetime.now(timezone.utc).isoformat()
        new: list[Ending] = []
        for ending in self.endings.values():
            if ending.id in self.unlocked:
                continue
            if ending.requires and not state.evaluate_all(ending.requires):
                continue
            if ending.forbids and not state.evaluate_none(ending.forbids):
                continue
            self.unlocked[ending.id] = ts
            new.append(ending)
        return new

    def mark_seen(self, ending_id: str) -> None:
        self.seen.add(ending_id)

    def newly_unlocked(self) -> list[Ending]:
        """Unlocked endings the player hasn't acknowledged yet."""
        return [self.endings[i] for i in self.unlocked
                if i in self.endings and i not in self.seen]
