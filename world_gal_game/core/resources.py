"""Generic numeric resource system.

A *resource* is a single named integer counter on the player. Game packs
declare what resources exist in ``meta.yaml`` (or ``content/resources.yaml``);
the engine doesn't care what they represent — currency, energy, study
hours, faith points, reputation, etc.

Example declaration::

    resources:
      - id: money
        name: "新台幣"
        symbol: "$"
        starting: 500
      - id: energy
        name: "體力"
        starting: 100
        max: 100
        min: 0
      - id: rep_dorm
        name: "宿舍口碑"
        starting: 0
        min: -100
        max: 100

Resources are reachable in story YAML as effects / conditions::

    effects:
      - {kind: gain_resource, target: money, value: 50}
      - {kind: spend_resource, target: energy, value: 20}
      - {kind: set_resource, target: rep_dorm, value: 0}

    requires:
      - {kind: resource_gte, target: money, value: 100}
      - {kind: resource_lt, target: energy, value: 50}

Unlike :class:`AffectionTracker` (per-NPC stats), resources are *global*
to the player and persisted through saves.
"""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class Resource(BaseModel):
    """Declarative definition of one named resource."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str = ""           # human-readable label for UI
    symbol: str = ""         # optional prefix/suffix glyph (e.g. "$", "¥")
    description: str = ""
    starting: int = 0
    min: int | None = None   # floor (None = unbounded)
    max: int | None = None   # ceiling (None = unbounded)
    icon: str | None = None  # optional asset path for status badge
    visible: bool = True     # hide from UI status row if False
    # Free-form tags for game-specific grouping; the engine ignores them
    # but a pack can use them when filtering.
    tags: list[str] = Field(default_factory=list)


class ResourceTracker(BaseModel):
    """Per-game wallet that holds the current values of declared resources.

    The tracker is permissive: spending more than you have is allowed (it
    clamps to ``min`` if defined, otherwise goes negative). Game code can
    pre-check with :meth:`can_afford` and refuse to apply the effect.
    """

    definitions: dict[str, Resource] = Field(default_factory=dict)
    values: dict[str, int] = Field(default_factory=dict)

    # ----- registry --------------------------------------------------------

    def register(self, resource: Resource) -> Resource:
        self.definitions[resource.id] = resource
        if resource.id not in self.values:
            self.values[resource.id] = int(resource.starting)
        return resource

    def define_many(self, resources: list[Resource]) -> None:
        for r in resources:
            self.register(r)

    def definition(self, resource_id: str) -> Resource | None:
        return self.definitions.get(resource_id)

    def all(self) -> list[Resource]:
        return list(self.definitions.values())

    # ----- reading ---------------------------------------------------------

    def get(self, resource_id: str) -> int:
        return int(self.values.get(resource_id, 0))

    def has(self, resource_id: str, amount: int = 1) -> bool:
        return self.get(resource_id) >= amount

    def can_afford(self, resource_id: str, amount: int) -> bool:
        return self.get(resource_id) >= amount

    # ----- mutations -------------------------------------------------------

    def _clamp(self, resource_id: str, value: int) -> int:
        d = self.definitions.get(resource_id)
        if d is None:
            return value
        if d.min is not None and value < d.min:
            value = d.min
        if d.max is not None and value > d.max:
            value = d.max
        return value

    def adjust(self, resource_id: str, delta: int) -> tuple[int, int]:
        """Add ``delta`` to a resource. Returns (old, new) after clamping.

        Auto-registers an unknown resource id with sensible defaults so
        a pack can opportunistically write to a resource it didn't
        formally declare (matches how flags work).
        """
        if resource_id not in self.definitions:
            self.register(Resource(id=resource_id, name=resource_id, starting=0))
        old = self.get(resource_id)
        new = self._clamp(resource_id, old + delta)
        self.values[resource_id] = new
        return old, new

    def spend(self, resource_id: str, amount: int) -> tuple[bool, int]:
        """Try to spend ``amount`` from a resource.

        Returns ``(ok, balance_after)``. ``ok=False`` means the player
        didn't have enough and nothing was deducted.
        """
        if amount < 0:
            return self.adjust(resource_id, -amount)[1] >= 0, self.get(resource_id)
        if not self.can_afford(resource_id, amount):
            return False, self.get(resource_id)
        _, new = self.adjust(resource_id, -amount)
        return True, new

    def set(self, resource_id: str, value: int) -> int:
        if resource_id not in self.definitions:
            self.register(Resource(id=resource_id, name=resource_id, starting=0))
        new = self._clamp(resource_id, int(value))
        self.values[resource_id] = new
        return new

    # ----- introspection ---------------------------------------------------

    def snapshot(self) -> dict[str, int]:
        return dict(self.values)

    def visible_snapshot(self) -> list[dict[str, Any]]:
        """List of (definition + current value) suitable for UI rendering."""
        out: list[dict[str, Any]] = []
        for rid, d in self.definitions.items():
            if not d.visible:
                continue
            out.append({
                "id": rid,
                "name": d.name or rid,
                "symbol": d.symbol,
                "value": self.values.get(rid, 0),
                "min": d.min,
                "max": d.max,
                "icon": d.icon,
            })
        return out
