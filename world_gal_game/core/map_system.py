"""Map and location system (RPG Maker style).

The world is modeled as a graph of locations. Each location has:
- A display name, description, and background image (with time-of-day variants).
- A list of exits (Exit objects, supporting one-way / conditional / described exits).
- A list of NPCs that may be present (optionally gated by time-of-day).
- A list of scene hooks (scenes that may trigger when entering / examining).
- Optional conditions controlling when the location itself becomes accessible.

Locations belong to a Region (e.g. "campus", "town") for map-overlay grouping.
The MapSystem holds the catalogue of locations + regions + the player's current
position.
"""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .story_graph import Condition


class NPCPresence(BaseModel):
    """An NPC's presence at a location, optionally time-gated."""

    model_config = ConfigDict(extra="forbid")

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
    """A scene that may auto-trigger or be available at a location.

    ``requires_flags`` / ``forbids_flags`` are kept for the common, terse
    flag-only case.  ``requires`` / ``forbids`` accept the same full
    condition objects as scene lines and choices, so hooks can be gated by
    affection, visited locations, quest state, or plugin-provided
    conditions without inventing new hook-specific fields.
    """

    model_config = ConfigDict(extra="forbid")

    scene_id: str
    trigger: str = "examine"   # "enter", "examine", "auto", "night_only"
    requires_flags: list[str] = Field(default_factory=list)
    forbids_flags: list[str] = Field(default_factory=list)
    requires_time: list[str] = Field(default_factory=list)
    requires: list[Condition] = Field(default_factory=list)
    forbids: list[Condition] = Field(default_factory=list)
    once: bool = True


class Exit(BaseModel):
    """A directed edge from one location to another.

    Supports one-way exits, conditional exits gated by flags or time,
    and an optional description shown in the UI (e.g. "夜晚才能進入").
    """

    model_config = ConfigDict(extra="forbid")

    target: str                          # destination location id
    label: str | None = None             # overrides default "→ 地點名" display text
    description: str | None = None       # supplemental hint shown in exploration UI
    one_way: bool = False                # when True the engine does not create a reverse exit
    requires_flags: list[str] = Field(default_factory=list)
    forbids_flags: list[str] = Field(default_factory=list)
    requires_time: list[str] = Field(default_factory=list)  # e.g. ["night","midnight"]
    # Time-of-day phases consumed by walking this exit. 0 (default) means
    # local move within the same area — players can wander campus without
    # the clock ticking. Long trips (cross-region, off-campus) opt in.
    travel_cost: int = 0

    def is_available(self, time_of_day: str, flags: dict[str, Any]) -> bool:
        """Return True when this exit is usable given the current state."""
        if self.requires_time and time_of_day not in self.requires_time:
            return False
        for f in self.requires_flags:
            if not flags.get(f):
                return False
        for f in self.forbids_flags:
            if flags.get(f):
                return False
        return True

    def unavailable_reason(self, time_of_day: str) -> str | None:
        """Human-readable reason why this exit is unavailable (time constraint only)."""
        if self.requires_time and time_of_day not in self.requires_time:
            times_str = "、".join(self.requires_time)
            return f"{times_str}才能進入"
        return None


class Region(BaseModel):
    """A named group of locations shown as a distinct area on the map overlay."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    color: tuple[int, int, int] | None = None   # RGB used to tint region nodes on the map


class Location(BaseModel):
    """A single place in the world."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    region: str | None = None
    description: str = ""
    background: str | None = None                          # default fallback bg path
    backgrounds: dict[str, str] = Field(default_factory=dict)  # time-of-day -> path
    ambient: str | None = None    # default looping room-tone for scenes here that
                                  # set no ambient of their own (DialogueEngine fallback)
    map_x: int = 0   # position on the world map view
    map_y: int = 0
    exits: list[Exit] = Field(default_factory=list)
    npcs: list[NPCPresence] = Field(default_factory=list)

    @field_validator("exits", mode="before")
    @classmethod
    def _coerce_exits(cls, v: list) -> list:
        """Allow shorthand string exits: "target_id" -> Exit(target="target_id")."""
        out = []
        for item in v:
            if isinstance(item, str):
                out.append({"target": item})
            else:
                out.append(item)
        return out
    scene_hooks: list[SceneHook] = Field(default_factory=list)
    requires_flags: list[str] = Field(default_factory=list)
    forbids_flags: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)   # e.g. ["haunted","indoor"]

    def background_for(self, time_of_day: str) -> str | None:
        """Return the time-of-day-specific bg path, falling back to the default."""
        return self.backgrounds.get(time_of_day) or self.background

    def is_accessible(self, flags: dict[str, Any]) -> bool:
        for f in self.requires_flags:
            if not flags.get(f):
                return False
        for f in self.forbids_flags:
            if flags.get(f):
                return False
        return True

    @property
    def exit_targets(self) -> list[str]:
        """Convenience: list of target location IDs (all exits, ignoring conditions)."""
        return [e.target for e in self.exits]


class MapSystem(BaseModel):
    """Catalogue of locations + regions + player position."""

    locations: dict[str, Location] = Field(default_factory=dict)
    regions: dict[str, Region] = Field(default_factory=dict)
    current_location_id: str | None = None
    visited: set[str] = Field(default_factory=set)

    model_config = {"arbitrary_types_allowed": True}

    def add_location(self, loc: Location) -> None:
        self.locations[loc.id] = loc

    def add_region(self, region: Region) -> None:
        self.regions[region.id] = region

    def get(self, loc_id: str) -> Location | None:
        return self.locations.get(loc_id)

    @property
    def current(self) -> Location | None:
        if self.current_location_id is None:
            return None
        return self.locations.get(self.current_location_id)

    def can_move_to(self, loc_id: str, flags: dict[str, Any],
                    time_of_day: str = "") -> bool:
        cur = self.current
        if cur is None:
            return loc_id in self.locations
        # Find an exit whose target matches
        exit_obj = next((e for e in cur.exits if e.target == loc_id), None)
        if exit_obj is None:
            return False
        target = self.locations.get(loc_id)
        if target is None:
            return False
        if not target.is_accessible(flags):
            return False
        if time_of_day:
            return exit_obj.is_available(time_of_day, flags)
        # Legacy path: flag-only check (no time gating applied)
        for f in exit_obj.requires_flags:
            if not flags.get(f):
                return False
        for f in exit_obj.forbids_flags:
            if flags.get(f):
                return False
        return True

    def move_to(self, loc_id: str) -> Location:
        if loc_id not in self.locations:
            raise KeyError(f"Unknown location: {loc_id}")
        self.current_location_id = loc_id
        self.visited.add(loc_id)
        return self.locations[loc_id]

    def available_exits(self, flags: dict[str, Any],
                        time_of_day: str = "") -> list[Location]:
        """Return locations reachable from current position.

        Exits that are flag-blocked are excluded.  Time-blocked exits (requires_time)
        are also excluded when time_of_day is provided.
        """
        cur = self.current
        if cur is None:
            return []
        out: list[Location] = []
        for exit_obj in cur.exits:
            loc = self.locations.get(exit_obj.target)
            if loc is None or not loc.is_accessible(flags):
                continue
            # Check exit-level flag conditions
            if any(not flags.get(f) for f in exit_obj.requires_flags):
                continue
            if any(flags.get(f) for f in exit_obj.forbids_flags):
                continue
            if time_of_day and exit_obj.requires_time:
                if time_of_day not in exit_obj.requires_time:
                    continue
            out.append(loc)
        return out

    def all_exits_with_status(
        self, flags: dict[str, Any], time_of_day: str
    ) -> list[tuple[Exit, Location, bool, str | None]]:
        """Return all exits from current location with availability info.

        Each tuple: (exit_obj, target_location, is_available, reason_if_not)
        Exits whose target location doesn't exist are omitted.
        """
        cur = self.current
        if cur is None:
            return []
        out = []
        for exit_obj in cur.exits:
            loc = self.locations.get(exit_obj.target)
            if loc is None:
                continue
            available = exit_obj.is_available(time_of_day, flags) and loc.is_accessible(flags)
            reason = None
            if not available:
                reason = exit_obj.unavailable_reason(time_of_day)
                if reason is None and not loc.is_accessible(flags):
                    reason = "條件不足"
            out.append((exit_obj, loc, available, reason))
        return out

    def npcs_present_at(self, loc: "Location | None", time_of_day: str,
                        weekday: str, flags: dict[str, Any]) -> list[str]:
        """NPC ids present at a *specific* location right now.

        Generalizes :meth:`present_npcs` to any location (not just the
        current one) so a travel UI can preview who is at a destination
        before the player commits to going there.
        """
        if loc is None:
            return []
        return [p.npc_id for p in loc.npcs
                if p.is_present(time_of_day, weekday, flags)]

    def present_npcs(self, time_of_day: str, weekday: str,
                     flags: dict[str, Any]) -> list[str]:
        return self.npcs_present_at(self.current, time_of_day, weekday, flags)

    def scenes_available_at(self, loc: "Location | None", *,
                            time_of_day: str,
                            flags: dict[str, Any],
                            played_scenes: set[str],
                            state: Any | None = None) -> list[SceneHook]:
        """Scene hooks currently available at a *specific* location.

        Generalizes :meth:`available_scenes` to any location so a travel UI
        can flag "something new here" on a destination before the player
        goes. ``state`` is optional for backwards compatibility; when
        supplied, hook-level ``requires`` / ``forbids`` are evaluated through
        ``GameState.evaluate``; when omitted, hooks that declare full
        conditions are treated as locked rather than guessed from the
        partial flag snapshot.
        """
        if loc is None:
            return []
        out: list[SceneHook] = []
        for h in loc.scene_hooks:
            if h.once and h.scene_id in played_scenes:
                continue
            if h.requires_time and time_of_day not in h.requires_time:
                continue
            if any(not flags.get(f) for f in h.requires_flags):
                continue
            if any(flags.get(f) for f in h.forbids_flags):
                continue
            if h.requires or h.forbids:
                if state is None:
                    continue
                if not state.evaluate_all(h.requires):
                    continue
                if not state.evaluate_none(h.forbids):
                    continue
            out.append(h)
        return out

    def available_scenes(self, *, time_of_day: str,
                         flags: dict[str, Any],
                         played_scenes: set[str],
                         state: Any | None = None) -> list[SceneHook]:
        """Return scene hooks currently available at the active location."""
        return self.scenes_available_at(
            self.current, time_of_day=time_of_day, flags=flags,
            played_scenes=played_scenes, state=state)

    def locations_by_region(self) -> dict[str | None, list[Location]]:
        """Group all locations by their region id.

        Locations without a region appear under the None key.
        """
        out: dict[str | None, list[Location]] = {}
        for loc in self.locations.values():
            key = loc.region or None
            out.setdefault(key, []).append(loc)
        return out
