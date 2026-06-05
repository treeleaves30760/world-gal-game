"""Cross-playthrough "clear data" (New Game+).

The union of everything the player has ever seen — scenes read, endings reached,
CGs unlocked, routes cleared — persisted SEPARATELY from save slots (in its own
``clear_data.json``) so it survives a fresh start. Powers:

- **Skip-already-read on a new game:** a new playthrough's read-log is seeded
  from ``scenes_seen``, so skip-read works immediately on content the player has
  seen in any prior run (the Key/Yuzusoft completion loop).
- **After-story gating:** parked on ``state.meta["__clear_data__"]`` so packs
  can gate bonus content on a prior clear (see the ``cleared_ending`` /
  ``cleared_route`` conditions).

This is global, not per-save, so it is intentionally absent from the save schema.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field, field_serializer


class ClearData(BaseModel):
    scenes_seen: set[str] = Field(default_factory=set)
    endings_seen: set[str] = Field(default_factory=set)
    cgs_seen: set[str] = Field(default_factory=set)
    cleared_routes: set[str] = Field(default_factory=set)

    @field_serializer("scenes_seen", "endings_seen", "cgs_seen",
                      "cleared_routes")
    def _ser_set(self, v: set[str]) -> list[str]:
        return sorted(v)

    @property
    def is_cleared(self) -> bool:
        """True once the player has reached at least one ending (NG+ available)."""
        return bool(self.endings_seen)

    # ---- persistence (robust by contract; never raises) ----------------
    @classmethod
    def load(cls, path: Path) -> "ClearData":
        try:
            if path.exists():
                return cls(**json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            pass
        return cls()

    def save(self, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(self.model_dump_json(indent=2), encoding="utf-8")
            os.replace(tmp, path)        # atomic
        except OSError:
            pass

    # ---- update from a live GameState ----------------------------------
    def record_from_state(self, state) -> bool:
        """Merge everything the given state has seen. Returns True if anything
        new was added (so the caller can skip a redundant save)."""
        before = (len(self.scenes_seen), len(self.endings_seen),
                  len(self.cgs_seen), len(self.cleared_routes))
        try:
            self.scenes_seen |= set(getattr(state.read_log, "scenes", set())
                                    or set())
        except Exception:
            pass
        try:
            unlocked = getattr(state.endings, "unlocked", {}) or {}
            self.endings_seen |= set(unlocked.keys())
            getter = getattr(state.endings, "get", None)
            if callable(getter):
                for eid in unlocked:
                    e = getter(eid)
                    rid = getattr(e, "route_id", None) if e else None
                    if rid:
                        self.cleared_routes.add(rid)
        except Exception:
            pass
        try:
            self.cgs_seen |= set(getattr(state.cg_gallery, "unlocked", set())
                                 or set())
        except Exception:
            pass
        after = (len(self.scenes_seen), len(self.endings_seen),
                 len(self.cgs_seen), len(self.cleared_routes))
        return after != before
