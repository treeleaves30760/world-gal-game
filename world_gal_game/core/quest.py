"""Quest tracking subsystem.

Quests are narrative objectives given by the story to the player.
Each Quest holds a list of Objectives; the QuestTracker owns all quests
registered from the content pack.
"""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

QuestStatus = Literal["inactive", "active", "completed", "failed"]


class Objective(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    optional: bool = False
    completed: bool = False
    # Not shown in UI until completed (hides spoiler-y sub-goals).
    hidden: bool = False


class Quest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    description: str = ""
    giver: str | None = None       # NPC id or location id, narrative only
    objectives: list[Objective] = []
    rewards_text: str = ""          # displayed after completion
    # Not listed in UI while inactive and hidden=True.
    hidden: bool = False
    status: QuestStatus = "inactive"


class QuestTracker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quests: dict[str, Quest] = Field(default_factory=dict)

    # ------------------------------------------------------------------
    # Registration

    def register(self, quest: Quest) -> None:
        """Add a quest definition. Idempotent on re-load."""
        self.quests[quest.id] = quest

    # ------------------------------------------------------------------
    # State transitions

    def start(self, quest_id: str) -> bool:
        """Transition inactive -> active. Returns True if state changed."""
        q = self.quests.get(quest_id)
        if q is None or q.status != "inactive":
            return False
        q.status = "active"
        return True

    def complete_objective(self, quest_id: str, obj_id: str) -> bool:
        """Mark a single objective done. Returns True if it was found & changed."""
        q = self.quests.get(quest_id)
        if q is None:
            return False
        for obj in q.objectives:
            if obj.id == obj_id:
                if obj.completed:
                    return False
                obj.completed = True
                return True
        return False

    def complete(self, quest_id: str) -> bool:
        """Directly mark quest completed. Returns True if state changed."""
        q = self.quests.get(quest_id)
        if q is None or q.status == "completed":
            return False
        q.status = "completed"
        return True

    def fail(self, quest_id: str) -> bool:
        """Mark quest failed. Returns True if state changed."""
        q = self.quests.get(quest_id)
        if q is None or q.status in ("completed", "failed"):
            return False
        q.status = "failed"
        return True

    # ------------------------------------------------------------------
    # Queries

    def active(self) -> list[Quest]:
        return [q for q in self.quests.values() if q.status == "active"]

    def completed(self) -> list[Quest]:
        return [q for q in self.quests.values() if q.status == "completed"]

    def is_active(self, quest_id: str) -> bool:
        q = self.quests.get(quest_id)
        return q is not None and q.status == "active"

    def is_completed(self, quest_id: str) -> bool:
        q = self.quests.get(quest_id)
        return q is not None and q.status == "completed"

    def objective_completed(self, quest_id: str, obj_id: str) -> bool:
        q = self.quests.get(quest_id)
        if q is None:
            return False
        for obj in q.objectives:
            if obj.id == obj_id:
                return obj.completed
        return False

    # ------------------------------------------------------------------
    # Internal helper

    def _all_required_done(self, quest_id: str) -> bool:
        """True when every non-optional objective is completed."""
        q = self.quests.get(quest_id)
        if q is None:
            return False
        return all(obj.completed for obj in q.objectives if not obj.optional)
