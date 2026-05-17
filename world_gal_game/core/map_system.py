"""Map and location system (RPG Maker style).

The world is modeled as a graph of locations. Each location has:
- A display name, description, and background image.
- A list of exits (locations directly reachable from here).
- A list of NPCs that may be present (optionally gated by time-of-day).
- A list of scene hooks (scenes that may trigger when entering / examining).
- Optional conditions controlling when the location itself becomes accessible.

The MapSystem holds the catalogue of locations plus the player's current
position. Higher-level subsystems (the game loop, story graph) decide what
happens when the player moves; this module just models geometry + presence.
"""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class NPCPresence(BaseModel):
    """An NPC's presence at a location, optionally time-gated."""

    npc_id: str
    times: list[str] = Field(default_factory=list)  # e.g. ["morning","afternoon"]; empty = anytime
    weekdays: list[str] = Field(default_factory=list)  # empty = any day
    requires_flags: list[str] = Field(default_factory=list)
    forbids_flags: list[str] = Field(default_factory=list)

    def is_present(self, time_of_day: str, weekday: str,
                   flags: dict[str, Any]) -> bool:
        if self.times and time_of_day not in self.times:
            return False
        if self.weekdays and weekday not in self.weekdays:
            return False
        for f in self.requires_flags:
            if not flags.get(f):
                return False
        for f in self.forbids_flags:
            if flags.get(f):
                return False
        return True


class SceneHook(BaseModel):
    """A scene that may auto-trigger or be available at a location."""

    scene_id: str
    trigger: str = "examine"   # "enter", "examine", "auto", "night_only"
    requires_flags: list[str] = Field(default_factory=list)
    forbids_flags: list[str] = Field(default_factory=list)
    requires_time: list[str] = Field(default_factory=list)
    once: bool = True


class Location(BaseModel):
    """A single place in the world."""

    id: str
    name: str
    region: str = ""
    description: str = ""
    background: str | None = None   # path to background image
    map_x: int = 0   # position on the world map view
    map_y: int = 0
    exits: list[str] = Field(default_factory=list)
    npcs: list[NPCPresence] = Field(default_factory=list)
    scene_hooks: list[SceneHook] = Field(default_factory=list)
    requires_flags: list[str] = Field(default_factory=list)
    forbids_flags: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)   # e.g. ["haunted","indoor"]

    def is_accessible(self, flags: dict[str, Any]) -> bool:
        for f in self.requires_flags:
            if not flags.get(f):
                return False
        for f in self.forbids_flags:
            if flags.get(f):
                return False
        return True


class MapSystem(BaseModel):
    """Catalogue of locations + player position."""

    locations: dict[str, Location] = Field(default_factory=dict)
    current_location_id: str | None = None
    visited: set[str] = Field(default_factory=set)

    model_config = {"arbitrary_types_allowed": True}

    def add_location(self, loc: Location) -> None:
        self.locations[loc.id] = loc

    def get(self, loc_id: str) -> Location | None:
        return self.locations.get(loc_id)

    @property
    def current(self) -> Location | None:
        if self.current_location_id is None:
            return None
        return self.locations.get(self.current_location_id)

    def can_move_to(self, loc_id: str, flags: dict[str, Any]) -> bool:
        cur = self.current
        if cur is None:
            return loc_id in self.locations
        if loc_id not in cur.exits:
            return False
        target = self.locations.get(loc_id)
        if target is None:
            return False
        return target.is_accessible(flags)

    def move_to(self, loc_id: str) -> Location:
        if loc_id not in self.locations:
            raise KeyError(f"Unknown location: {loc_id}")
        self.current_location_id = loc_id
        self.visited.add(loc_id)
        return self.locations[loc_id]

    def available_exits(self, flags: dict[str, Any]) -> list[Location]:
        cur = self.current
        if cur is None:
            return []
        out: list[Location] = []
        for eid in cur.exits:
            loc = self.locations.get(eid)
            if loc and loc.is_accessible(flags):
                out.append(loc)
        return out

    def present_npcs(self, time_of_day: str, weekday: str,
                     flags: dict[str, Any]) -> list[str]:
        cur = self.current
        if cur is None:
            return []
        return [p.npc_id for p in cur.npcs
                if p.is_present(time_of_day, weekday, flags)]

    def available_scenes(self, *, time_of_day: str,
                         flags: dict[str, Any],
                         played_scenes: set[str]) -> list[SceneHook]:
        cur = self.current
        if cur is None:
            return []
        out: list[SceneHook] = []
        for h in cur.scene_hooks:
            if h.once and h.scene_id in played_scenes:
                continue
            if h.requires_time and time_of_day not in h.requires_time:
                continue
            if any(not flags.get(f) for f in h.requires_flags):
                continue
            if any(flags.get(f) for f in h.forbids_flags):
                continue
            out.append(h)
        return out
