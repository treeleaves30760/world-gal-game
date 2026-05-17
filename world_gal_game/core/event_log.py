"""Event log and flag system.

The event log records significant story events (scene played, choice taken,
location visited, NPC interaction). Flags are boolean / numeric state that
later scenes and the story graph can query.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventEntry(BaseModel):
    """A single recorded event."""

    timestamp: str = Field(default_factory=_now)
    kind: str  # "scene", "choice", "location", "dialogue", "system", "unlock", "custom"
    title: str
    summary: str = ""
    location: str | None = None
    actors: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)


class EventLog(BaseModel):
    """Holds the log of events and the player's flag dictionary."""

    entries: list[EventEntry] = Field(default_factory=list)
    flags: dict[str, Any] = Field(default_factory=dict)

    def record(self, kind: str, title: str, summary: str = "",
               *, location: str | None = None,
               actors: list[str] | None = None,
               data: dict[str, Any] | None = None) -> EventEntry:
        entry = EventEntry(
            kind=kind,
            title=title,
            summary=summary,
            location=location,
            actors=actors or [],
            data=data or {},
        )
        self.entries.append(entry)
        return entry

    def set_flag(self, key: str, value: Any = True) -> None:
        self.flags[key] = value

    def get_flag(self, key: str, default: Any = False) -> Any:
        return self.flags.get(key, default)

    def has_flag(self, key: str) -> bool:
        v = self.flags.get(key, None)
        return bool(v)

    def increment(self, key: str, delta: int = 1) -> int:
        current = int(self.flags.get(key, 0))
        new_val = current + delta
        self.flags[key] = new_val
        return new_val

    def recent(self, n: int = 10) -> list[EventEntry]:
        return list(self.entries[-n:])

    def filter(self, *, kind: str | None = None,
               actor: str | None = None,
               location: str | None = None) -> list[EventEntry]:
        results = self.entries
        if kind:
            results = [e for e in results if e.kind == kind]
        if actor:
            results = [e for e in results if actor in e.actors]
        if location:
            results = [e for e in results if e.location == location]
        return list(results)


class DialogueHistory(BaseModel):
    """A bounded ring buffer of every line shown to the player.

    Separate from the EventLog so we can keep arbitrarily long story-event
    entries while capping per-line history (which can grow huge).
    """

    lines: list[dict[str, Any]] = Field(default_factory=list)
    max_lines: int = 500

    def push(self, *, speaker: str | None, text: str,
             scene_id: str | None = None,
             portrait: str | None = None) -> None:
        self.lines.append({
            "speaker": speaker, "text": text,
            "scene_id": scene_id, "portrait": portrait,
        })
        if len(self.lines) > self.max_lines:
            # Drop the oldest 10% so we don't churn on every push.
            self.lines = self.lines[-self.max_lines:]

    def recent(self, n: int = 50) -> list[dict[str, Any]]:
        return list(self.lines[-n:])

    def clear(self) -> None:
        self.lines.clear()
