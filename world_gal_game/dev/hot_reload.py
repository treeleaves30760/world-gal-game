"""Hot-reload pack YAML without losing runtime player state.

Triggered by F5 in dev mode (WGG_DEV=1). No filesystem watcher;
the full reload happens on demand each press.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from ..core.game_state import GameState
from ..npc.npc_base import NPCRegistry


class HotReloader:
    """Reload pack YAML without losing runtime state.

    Strategy:
    1. Snapshot the volatile runtime parts (events flags, affection,
       resources, inventory, achievements, route).
    2. Re-run content_loader.load_pack(pack_content).
    3. Overlay the snapshot onto the freshly-loaded state.
    4. Static content (scene definitions, locations, NPCs) is refreshed;
       player progress is preserved.
    """

    def __init__(self, pack_root: Path) -> None:
        self.pack_root = pack_root

    # ------------------------------------------------------------------
    # Public API

    def reload(
        self,
        current_state: GameState,
        current_npcs: NPCRegistry,
    ) -> tuple[GameState, NPCRegistry, dict[str, Any]]:
        """Return (new_state, new_npcs, new_meta) with player progress intact.

        Caller is responsible for wiring the results back into the App and
        notifying scenes that need a context refresh.
        """
        snapshot = _snapshot_progress(current_state)

        content_root = self.pack_root / "content"
        from ..content_loader import load_pack
        new_state, new_npcs, new_meta = load_pack(content_root)

        _restore_progress(new_state, snapshot)
        return new_state, new_npcs, new_meta


# ------------------------------------------------------------------
# Helpers


def _snapshot_progress(state: GameState) -> dict[str, Any]:
    """Capture the mutable player-progress fields before a reload."""
    return {
        "flags": dict(state.events.flags),
        "affection": {
            cid: dict(ca.stats)
            for cid, ca in state.affection.characters.items()
        },
        "resources": dict(state.resources.values),
        "inventory": dict(state.inventory.counts),
        # unlocked is dict[str, timestamp]; seen is set[str]
        "achievements_unlocked": dict(state.achievements.unlocked),
        "achievements_seen": set(state.achievements.seen),
        "route": state.route,
        "player": state.player.model_dump(),
        "current_location_id": state.map.current_location_id,
        "visited": set(state.map.visited),
        # story progress
        "scenes_played": set(state.story.played),
        "current_scene": state.story.current_scene,
        "current_line_index": state.story.current_line_index,
    }


def _restore_progress(state: GameState, snap: dict[str, Any]) -> None:
    """Overlay a progress snapshot onto a freshly-loaded GameState."""
    # flags
    for k, v in snap["flags"].items():
        state.events.set_flag(k, v)

    # affection — only restore characters that still exist in the new pack;
    # new characters get default stats from content_loader.
    for cid, stats in snap["affection"].items():
        if cid in state.affection.characters:
            for stat, value in stats.items():
                state.affection.characters[cid].stats[stat] = value
        else:
            # Character was removed from pack — skip silently.
            pass

    # resources (safe: extra keys stay at 0 in the new state)
    for rid, val in snap["resources"].items():
        if rid in state.resources.definitions:
            state.resources.set(rid, val)

    # inventory
    for iid, count in snap["inventory"].items():
        if count > 0:
            item = state.items.get(iid)
            max_stack = item.max_stack if item else None
            state.inventory.add(iid, count, max_stack=max_stack)

    # achievements — unlocked is dict[id -> timestamp]
    for ach_id, ts in snap["achievements_unlocked"].items():
        if ach_id in state.achievements.achievements:
            state.achievements.unlocked[ach_id] = ts
    state.achievements.seen.update(snap["achievements_seen"])

    # misc
    state.route = snap["route"]
    from ..core.game_state import PlayerInfo
    state.player = PlayerInfo(**snap["player"])

    # map: restore location if it still exists
    if snap["current_location_id"] and snap["current_location_id"] in state.map.locations:
        state.map.current_location_id = snap["current_location_id"]
    state.map.visited = {loc for loc in snap["visited"] if loc in state.map.locations}

    # story progress
    for scene_id in snap["scenes_played"]:
        state.story.played.add(scene_id)
    state.story.current_scene = snap["current_scene"]
    state.story.current_line_index = snap["current_line_index"]
