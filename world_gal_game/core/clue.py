"""Clue / journal system.

Provides a curated, forward-looking "what should I be doing / what do I
know" surface. Distinct from the chronological :class:`EventLog`:

- ``EventLog`` records every event that happened, in order, dense.
- ``ClueTracker`` shows only entries the game pack has authored as
  guidance ("提示"), filtered by current state.

A :class:`Clue` is a journal entry the game pack authors in
``content/clues.yaml``. Each entry has:

- ``requires``: list of Conditions; ALL must be true for the clue to
  "unlock" (enter the journal).
- ``forbids``: list of Conditions; ANY being true makes the clue
  "resolved" — it stays in the journal but moves to a resolved section.
- ``category``: free-form bucket label (e.g. "主線", "角色", "都市傳說").
- ``priority``: higher integers float to the top of their section.

Once a clue has been unlocked it stays in the player's journal forever
(via the ``seen`` set), so resolved clues remain readable as a record of
what the player has learned.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .story_graph import Condition


class Clue(BaseModel):
    """A single journal entry authored by the game pack."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    text: str = ""
    # Free-form bucket. Render groups clues by this label.
    category: str = "其他"
    # Show in the active section when ALL of these conditions are true.
    requires: list[Condition] = Field(default_factory=list)
    # When ANY of these are true, mark the clue as resolved (greyed out)
    # but keep it in the journal.
    forbids: list[Condition] = Field(default_factory=list)
    # Higher is more important; sort key inside each section.
    priority: int = 0
    # A "record" entry (a 異聞錄 page, not a forward hint): it surfaces *after*
    # the thing it describes is done, so the journal tags it 已收錄 (in a warm
    # collected tone) instead of the forward-looking 進行中.
    record: bool = False


class ClueTracker(BaseModel):
    """Catalogue of clues + which ones the player has unlocked."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    clues: dict[str, Clue] = Field(default_factory=dict)
    # Clue ids the player has unlocked at least once. Once unlocked,
    # entries stay in the journal forever (so resolved clues remain
    # readable as a record of what the player learned).
    seen: set[str] = Field(default_factory=set)
    # Subset of `seen` the player has not viewed yet — used to badge the
    # journal button with a "new" indicator until they open it.
    unread: set[str] = Field(default_factory=set)

    def register(self, clue: Clue) -> None:
        self.clues[clue.id] = clue

    def get(self, clue_id: str) -> Clue | None:
        return self.clues.get(clue_id)

    # ------------------------------------------------------------------
    # State-driven helpers. These take a GameState — kept untyped here to
    # avoid a circular import with game_state.py.
    # ------------------------------------------------------------------

    def is_active(self, clue: Clue, state) -> bool:
        """True when the clue is currently relevant (gates open, not yet
        resolved)."""
        if not state.evaluate_all(clue.requires):
            return False
        if not state.evaluate_none(clue.forbids):
            return False
        return True

    def is_resolved(self, clue: Clue, state) -> bool:
        """True when the clue is in the journal but no longer actionable.

        A clue is resolved if it was ever seen and either:
        - its forbids list is currently triggered (its hook closed), or
        - its requires no longer hold (player rolled state back via load).

        Only the forbids path is the common case — the requires regression
        only happens with loaded saves.
        """
        if clue.id not in self.seen:
            return False
        if not state.evaluate_none(clue.forbids):
            return True
        if not state.evaluate_all(clue.requires):
            return True
        return False

    def refresh(self, state) -> list[Clue]:
        """Scan every clue and unlock any whose requires are now satisfied.

        Returns the newly-unlocked clues (so the caller can fire a toast
        or other notification).
        """
        newly: list[Clue] = []
        for c in self.clues.values():
            if c.id in self.seen:
                continue
            if not state.evaluate_all(c.requires):
                continue
            if not state.evaluate_none(c.forbids):
                # Requires + forbids both true: skip — the player never
                # got a window to learn it (e.g. they loaded into a state
                # past this point). Don't pretend it's new.
                continue
            self.seen.add(c.id)
            self.unread.add(c.id)
            newly.append(c)
        return newly

    def journal(self, state) -> list[tuple[Clue, str]]:
        """Return every seen clue paired with its current status.

        Status is one of: ``"active"`` (visible in current section) or
        ``"resolved"`` (greyed out / archive section).
        Sorted by status (active first) then category then descending
        priority then id for deterministic display.
        """
        out: list[tuple[Clue, str]] = []
        for cid in self.seen:
            c = self.clues.get(cid)
            if c is None:
                continue
            status = "active" if self.is_active(c, state) else "resolved"
            out.append((c, status))
        out.sort(key=lambda t: (
            0 if t[1] == "active" else 1,
            t[0].category,
            -t[0].priority,
            t[0].id,
        ))
        return out

    def mark_read(self, clue_id: str) -> None:
        self.unread.discard(clue_id)

    def mark_all_read(self) -> None:
        self.unread.clear()

    def unread_count(self) -> int:
        return len(self.unread)
