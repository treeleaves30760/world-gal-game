"""Hot-reload pack YAML without losing runtime player state.

Triggered by F5 in dev mode (WGG_DEV=1). No filesystem watcher;
the full reload happens on demand each press.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.game_state import GameState
from ..npc.npc_base import NPCRegistry


class HotReloader:
    """Reload pack YAML without losing runtime state.

    Strategy:
    1. Snapshot the volatile runtime parts (events flags, affection,
       resources, inventory, read/completion trackers, time, route).
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

        # Rebuild the new pack's media boundary before overlaying mutable
        # progress.  Otherwise a forged or retired path in the old in-memory
        # state could bootstrap itself into authority during App rebind.
        from ..core.media_authority import build_media_authority
        from ..core.music_room_spec import MusicRoomManifest
        music_manifest = new_state.meta.get("__music_room_manifest__")
        if not isinstance(music_manifest, MusicRoomManifest):
            music_manifest = None
        discovered_music: list[str] = []
        bgm_root = self.pack_root / "assets" / "bgm"
        try:
            discovered_music = [
                f"assets/bgm/{entry.name}"
                for entry in bgm_root.iterdir()
                if entry.is_file()
                and entry.suffix.lower() in {".ogg", ".mp3", ".wav"}
            ]
        except OSError:
            pass
        new_state.meta["__pack_media_authority__"] = build_media_authority(
            new_state.story,
            music_manifest=music_manifest,
            additional_music_ids=(
                *discovered_music,
                new_meta.get("title_bgm", ""),
            ),
        )
        _restore_progress(new_state, snapshot)
        return new_state, new_npcs, new_meta


# ------------------------------------------------------------------
# Helpers


def _snapshot_progress(state: GameState) -> dict[str, Any]:
    """Capture the mutable player-progress fields before a reload."""
    current_scene = state.story.current_scene
    current_line_index = state.story.current_line_index
    cursor_scene = (
        state.story.get(current_scene)
        if isinstance(current_scene, str) and current_scene
        else None
    )
    cursor_at_end = bool(
        cursor_scene is not None
        and current_line_index == len(cursor_scene.lines)
    )
    cursor_loc_id = (
        cursor_scene.lines[current_line_index].loc_id
        if cursor_scene is not None
        and 0 <= current_line_index < len(cursor_scene.lines)
        else None
    )
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
        "endings_unlocked": dict(state.endings.unlocked),
        "endings_seen": set(state.endings.seen),
        "cgs_unlocked": set(state.cg_gallery.unlocked),
        "music_unlocked": set(state.music_room.unlocked),
        "read_scenes": set(state.read_log.scenes),
        "read_lines": set(state.read_log.lines),
        "replay_archive": state.read_log.model_dump(mode="json").get(
            "replay_archive", {}
        ),
        "time": state.time.model_dump(),
        "route": state.route,
        "player": state.player.model_dump(),
        "current_location_id": state.map.current_location_id,
        "visited": set(state.map.visited),
        # story progress
        "scenes_played": set(state.story.played),
        "current_scene": current_scene,
        "current_line_index": current_line_index,
        "story_cursor_loc_id": cursor_loc_id,
        "story_cursor_at_end": cursor_at_end,
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

    # Completion/read state follows the player while definitions come from
    # the freshly loaded pack. Retired ending ids may remain as inert save
    # history; player-facing trackers intersect them with current definitions.
    state.endings.unlocked.update(snap["endings_unlocked"])
    state.endings.seen.update(snap["endings_seen"])
    for path in snap["cgs_unlocked"]:
        state.cg_gallery.unlock(path)
    music_ids = set(snap.get("music_unlocked", ()))
    from ..core.media_authority import MediaAuthority
    authority = state.meta.get("__pack_media_authority__")
    if isinstance(authority, MediaAuthority):
        music_ids = authority.filter_music_ids(music_ids)
    for path in music_ids:
        state.music_room.unlock(path)
    state.read_log.scenes.update(snap["read_scenes"])
    state.read_log.lines.update(snap["read_lines"])
    from ..core.read_log import ReadLog, ReplayArchiveEntry
    candidate_replay = ReadLog(replay_archive={
        scene_id: ReplayArchiveEntry.model_validate(entry)
        for scene_id, entry in snap.get("replay_archive", {}).items()
    })
    from ..dialogue.replay_engine import rebind_authored_replay_visual_fx
    rebound_replay = rebind_authored_replay_visual_fx(
        candidate_replay,
        state.story,
    )
    state.read_log.replay_archive.update(rebound_replay.replay_archive)

    # misc
    state.route = snap["route"]
    from ..core.game_state import PlayerInfo
    state.player = PlayerInfo(**snap["player"])
    from ..core.time_system import TimeSystem
    state.time = TimeSystem(**snap["time"])

    # map: restore location if it still exists
    if snap["current_location_id"] and snap["current_location_id"] in state.map.locations:
        state.map.current_location_id = snap["current_location_id"]
    state.map.visited = {loc for loc in snap["visited"] if loc in state.map.locations}

    # story progress
    for scene_id in snap["scenes_played"]:
        state.story.played.add(scene_id)
    current_scene = snap["current_scene"]
    if current_scene is None:
        state.story.current_scene = None
        state.story.current_line_index = 0
        return
    scene = state.story.get(current_scene)
    if scene is None:
        raise ValueError("hot reload removed the active story scene")
    has_cursor_loc = "story_cursor_loc_id" in snap
    has_cursor_end = "story_cursor_at_end" in snap
    if has_cursor_loc != has_cursor_end:
        raise ValueError("hot reload story cursor evidence is incomplete")
    if has_cursor_loc:
        at_end = snap["story_cursor_at_end"]
        loc_id = snap["story_cursor_loc_id"]
        if not isinstance(at_end, bool):
            raise ValueError("hot reload story cursor boundary is invalid")
        if at_end:
            if loc_id is not None:
                raise ValueError("hot reload end cursor carries a line id")
            rebound_index = len(scene.lines)
        else:
            from ..core.story_graph import resolve_authored_line
            rebound_index, _ = resolve_authored_line(
                scene,
                loc_id=loc_id,
                line_index=snap["current_line_index"],
                label="hot reload story cursor",
            )
    else:
        rebound_index = snap["current_line_index"]
        if (
            type(rebound_index) is not int
            or not 0 <= rebound_index <= len(scene.lines)
        ):
            raise ValueError("hot reload story cursor is outside the scene")
    state.story.current_scene = current_scene
    state.story.current_line_index = rebound_index
