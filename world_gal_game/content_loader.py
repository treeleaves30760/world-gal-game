"""Load game content (locations, characters, scenes) from YAML.

A game pack is a directory under games/<pack>/ containing:

    content/
        locations.yaml        # list of locations
        characters.yaml       # list of NPCs
        scenes/               # YAML scene files (one or many per file)
            00_prologue.yaml
            10_meet_heroine.yaml
            ...
    assets/
        backgrounds/
        characters/
        cgs/
        ui/
        fonts/

This module reads them and populates a GameState, NPCRegistry, and the
StoryGraph. Asset paths inside the YAML are interpreted relative to the
project root (so "games/<pack>/assets/...").
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import yaml

from .core.achievements import Achievement
from .core.affection import AffectionThreshold
from .core.clue import Clue
from .core.game_state import GameState, PlayerInfo
from .core.inventory import Item
from .core.map_system import MapSystem, Location, NPCPresence, SceneHook, Exit, Region
from .core.quest import Quest, Objective
from .core.resources import Resource
from .core.story_graph import Condition
from .npc.npc_base import NPC, NPCRegistry
from .dialogue.script_loader import load_scenes_dir
from .dialogue.script_loader import _to_conditions, _to_effects


def _read_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _parse_exits(raw_exits: list) -> list[Exit]:
    """Accept both shorthand strings and full dict forms for exits."""
    out: list[Exit] = []
    for item in raw_exits:
        if isinstance(item, str):
            out.append(Exit(target=item))
        else:
            out.append(Exit(**item))
    return out


def load_locations(content_root: Path, state: GameState) -> None:
    data = _read_yaml(content_root / "locations.yaml") or []
    top: dict = {}
    if isinstance(data, dict):
        top = data
        data = data.get("locations", [])

    # Load regions first so they exist before locations reference them.
    for raw_region in top.get("regions", []):
        region = Region(**raw_region)
        state.map.add_region(region)

    for raw in data:
        raw = dict(raw)
        npc_presences = [NPCPresence(**p) for p in raw.pop("npcs", [])]
        scene_hooks = [SceneHook(**h) for h in raw.pop("scene_hooks", [])]
        exits = _parse_exits(raw.pop("exits", []))
        loc = Location(npcs=npc_presences, scene_hooks=scene_hooks,
                       exits=exits, **raw)
        state.map.add_location(loc)


def load_characters(content_root: Path, registry: NPCRegistry,
                    state: GameState) -> None:
    data = _read_yaml(content_root / "characters.yaml") or []
    if isinstance(data, dict) and "characters" in data:
        data = data["characters"]
    for raw in data:
        thresholds_raw = raw.pop("thresholds", None)
        # NPC.thresholds isn't a field; we set them on the affection tracker.
        npc = NPC(**raw)
        registry.add(npc)
        state.affection.register(npc.id)
        if thresholds_raw:
            char = state.affection.characters[npc.id]
            for t in thresholds_raw:
                char.thresholds.append(AffectionThreshold(**t))


def load_scenes(content_root: Path, state: GameState) -> None:
    scenes_dir = content_root / "scenes"
    if not scenes_dir.exists():
        return
    for sc in load_scenes_dir(scenes_dir):
        state.story.add_scene(sc)


def load_items(content_root: Path, state: GameState) -> None:
    """Read ``content/items.yaml`` (if present) into the GameState.

    ``use_effects`` in YAML is parsed with the same effect-loader used by
    scenes, so all of the engine's effect kinds (gain_resource,
    affection, set_flag, ...) are usable as item-use side effects.
    """
    data = _read_yaml(content_root / "items.yaml") or []
    if isinstance(data, dict) and "items" in data:
        data = data["items"]
    for raw in data:
        raw = dict(raw)
        raw["use_effects"] = _to_effects(raw.get("use_effects"))
        item = Item(**raw)
        state.items.add(item)
    # Pre-stock the player's inventory from meta later (done in load_pack).


def load_resources(content_root: Path, state: GameState,
                   meta: dict[str, Any]) -> None:
    """Define resources for the pack.

    Resources can be declared in either:

    - ``meta.yaml`` ``resources:`` block, or
    - ``content/resources.yaml`` as a list (or {resources: [...]}).

    Both are merged — content/resources.yaml takes precedence if a
    resource id appears in both.
    """
    declared: list[dict[str, Any]] = []
    if "resources" in meta and isinstance(meta["resources"], list):
        declared.extend(meta["resources"])
    file_data = _read_yaml(content_root / "resources.yaml")
    if file_data is not None:
        if isinstance(file_data, dict) and "resources" in file_data:
            file_data = file_data["resources"]
        if isinstance(file_data, list):
            declared.extend(file_data)
    # Dedupe by id with later entries winning.
    by_id: dict[str, dict[str, Any]] = {}
    for r in declared:
        if not isinstance(r, dict) or "id" not in r:
            continue
        by_id[r["id"]] = r
    for raw in by_id.values():
        state.resources.register(Resource(**raw))


def load_achievements(content_root: Path, state: GameState) -> None:
    data = _read_yaml(content_root / "achievements.yaml") or []
    if isinstance(data, dict) and "achievements" in data:
        data = data["achievements"]
    for raw in data:
        raw = dict(raw)
        raw["requires"] = _to_conditions(raw.get("requires"))
        raw["forbids"] = _to_conditions(raw.get("forbids"))
        ach = Achievement(**raw)
        state.achievements.register(ach)


def load_quests(content_root: Path, state: GameState) -> None:
    """Read ``content/quests.yaml`` (if present) and register every quest."""
    data = _read_yaml(content_root / "quests.yaml") or []
    if isinstance(data, dict) and "quests" in data:
        data = data["quests"]
    for raw in data:
        raw = dict(raw)
        objs_raw = raw.pop("objectives", [])
        objectives = [Objective(**o) for o in (objs_raw or [])]
        quest = Quest(objectives=objectives, **raw)
        state.quests.register(quest)


def load_clues(content_root: Path, state: GameState) -> None:
    """Read ``content/clues.yaml`` (if present) and register every clue.

    The journal/clue system is purely game-pack-driven content; the
    engine ships no defaults. Each entry's ``requires`` / ``forbids``
    fields go through the standard condition loader so all condition
    kinds (flag, scene_played, has_item, ...) are usable.
    """
    data = _read_yaml(content_root / "clues.yaml") or []
    if isinstance(data, dict) and "clues" in data:
        data = data["clues"]
    for raw in data:
        raw = dict(raw)
        raw["requires"] = _to_conditions(raw.get("requires"))
        raw["forbids"] = _to_conditions(raw.get("forbids"))
        clue = Clue(**raw)
        state.clues.register(clue)


def load_game_meta(content_root: Path) -> dict[str, Any]:
    meta = _read_yaml(content_root / "meta.yaml") or {}
    return meta


def load_pack(content_root: Path) -> tuple[GameState, NPCRegistry, dict]:
    """Build a :class:`GameState` from a pack on disk.

    The pack root is the parent of ``content_root`` (handles both
    ``games/<pack>/`` and ``games/<pack>/content/`` shapes). Plugins
    live under ``<pack_root>/plugins/<plugin_id>/`` — they're
    discovered + activated *first*, so any effects / conditions /
    inspect fields they register are in place before the YAML loader
    references them.
    """
    from .plugins import PluginContext, PluginManager
    from .plugins.context import HookEvent
    from . import __version__ as engine_version

    # The content_root may be a content/ subdir or the pack root itself.
    # Plugins always live at <pack_root>/plugins/.
    pack_root = content_root.parent if content_root.name == "content" else content_root

    state = GameState()
    registry = NPCRegistry()

    # ------------------------------------------------------------------
    # Stage 1: plugin discovery + activation
    #
    # Done before YAML loading so that effect/condition kinds registered
    # by pack-local plugins exist when scenes/achievements/items reference
    # them. The PluginContext starts with no state attached; we'll patch
    # it in once GameState is built (between stage 2 and stage 3).
    pre_meta = load_game_meta(content_root)
    pre_ctx = PluginContext(
        state=None, meta=pre_meta, pack_root=pack_root,
    )
    plugin_manager = PluginManager(
        pack_root=pack_root, engine_version=engine_version,
    )
    plugin_manager.discover()
    plugin_manager.activate(context=pre_ctx)
    plugin_manager.print_summary()
    # Fire pack.before_load so plugins can fix up meta / patch state if
    # they want to.
    plugin_manager.fire_hook(
        HookEvent.PACK_BEFORE_LOAD, pre_ctx, pack_root=pack_root,
    )

    # ------------------------------------------------------------------
    # Stage 2: load YAML content into the GameState
    load_characters(content_root, registry, state)
    load_locations(content_root, state)
    load_items(content_root, state)
    load_scenes(content_root, state)
    load_achievements(content_root, state)
    load_quests(content_root, state)
    load_clues(content_root, state)
    meta = load_game_meta(content_root)
    load_resources(content_root, state, meta)
    if "player" in meta:
        state.player = PlayerInfo(**meta["player"])
    if "start_location" in meta and meta["start_location"] in state.map.locations:
        state.map.current_location_id = meta["start_location"]
        state.map.visited.add(meta["start_location"])
    # Pre-stock the inventory if meta says so:
    #   starting_inventory: {item_id: count, ...}
    for iid, count in (meta.get("starting_inventory") or {}).items():
        item = state.items.get(iid)
        max_stack = item.max_stack if item else None
        state.inventory.add(iid, int(count), max_stack=max_stack)
    # Apply resource starting values from meta (handy when the resource
    # was declared elsewhere but its starting value depends on the pack).
    for rid, val in (meta.get("starting_resources") or {}).items():
        state.resources.set(rid, int(val))
    # Make the NPC registry reachable to the gift effect (which lives on
    # GameState and otherwise wouldn't know about NPCs). We stash it in
    # meta with a private key so it doesn't get serialized to a save.
    state.meta["__npc_registry__"] = registry
    # Park the plugin manager on state.meta with a double-underscore
    # private key so SaveManager filters it out on serialise.
    state.meta["__plugin_manager__"] = plugin_manager
    # Pack identity bridge: lets SaveManager stamp saves with which pack +
    # content version produced them (for cross-version migration / mismatch
    # detection on load). Double-underscore so it's stripped from serialised
    # state — the identity is written to the save envelope, not the body.
    state.meta["__pack_meta__"] = {
        "pack_id": str(meta.get("id") or pack_root.name),
        "pack_format_version": str(meta.get("pack_format_version", "0")),
        "engine_version": engine_version,
    }

    # ------------------------------------------------------------------
    # Stage 3: plugin post-load hooks + clue sweep
    full_ctx = PluginContext(
        state=state, meta=meta, pack_root=pack_root, manager=plugin_manager,
    )
    plugin_manager.set_context(full_ctx)
    plugin_manager.fire_hook(
        HookEvent.PACK_AFTER_LOAD, full_ctx, pack_root=pack_root, meta=meta,
    )

    # Sweep clues once at startup so any whose requires are already
    # satisfied by the starting state (e.g. clues gated by visited or
    # already-set flags from a loaded save) enter the journal. Discard
    # the unread badge for these — the player didn't "earn" them mid-play.
    newly = state.clues.refresh(state)
    for c in newly:
        state.clues.mark_read(c.id)

    plugin_manager.fire_hook(HookEvent.GAME_STATE_READY, full_ctx)
    return state, registry, meta
