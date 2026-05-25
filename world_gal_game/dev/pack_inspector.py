"""PackInspector — read-only structural analysis of a pack on disk.

This sits at the "developer view" tier of pillar B: the engine already
exposes the *player view* through :class:`HeadlessSession.inspect`
(what flags are set, where the player is, etc); :class:`PackInspector`
answers the orthogonal "what does this pack actually contain?":

- how many scenes / locations / NPCs / quests
- which scenes are reachable from the start
- which scenes / locations have no outgoing edges (dead ends)
- the scene/location graph as mermaid / dot / adjacency dict

The class operates on **raw YAML** (PyYAML safe_load), not on a fully
loaded :class:`GameState`. That keeps it pure (no plugin side effects,
no need for a runnable pack), so it's the right tool for ``wgg check``
and AI-driven validation pipelines.

For runtime / player-state inspection use :meth:`HeadlessSession.inspect`
instead.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

import yaml


# ----------------------------------------------------------------------
# Internal data model


@dataclass
class SceneNode:
    id: str
    title: str
    file: str
    # (origin_kind, target_scene_id), e.g.
    #   ("choice", "ending_lover")
    #   ("on_end", "next_scene")
    #   ("line_effect", "scene_x")
    outgoing: list[tuple[str, str]] = field(default_factory=list)
    has_choices: bool = False
    is_ending: bool = False  # heuristic: id starts with "ending_" or has tag "ending"
    on_end_count: int = 0


@dataclass
class LocationNode:
    id: str
    name: str
    file: str
    exits: list[str] = field(default_factory=list)
    scene_hooks: list[str] = field(default_factory=list)  # scene ids


@dataclass
class DeadEnd:
    """One detected dead-end issue."""

    kind: Literal["scene", "location", "choice", "unreachable"]
    target: str
    detail: str
    file: str = ""


# ----------------------------------------------------------------------
# PackInspector


class PackInspector:
    """Structural analysis of a pack directory.

    Construct with ``PackInspector(pack_root)`` (either the pack root or
    its ``content/`` subdirectory). The constructor loads every YAML
    file once; subsequent queries are pure dict lookups.
    """

    def __init__(self, pack_root: Path) -> None:
        self.pack_root = Path(pack_root).resolve()
        if (self.pack_root / "content").is_dir():
            self.content_root = self.pack_root / "content"
        else:
            self.content_root = self.pack_root
            # If the user passed a content dir, climb up for pack-level paths.
            if self.pack_root.name == "content":
                self.pack_root = self.pack_root.parent

        self.meta: dict[str, Any] = {}
        self.scenes_by_id: dict[str, SceneNode] = {}
        self.locations_by_id: dict[str, LocationNode] = {}
        self.characters_by_id: dict[str, dict[str, Any]] = {}
        self.items_by_id: dict[str, dict[str, Any]] = {}
        self.achievements: list[dict[str, Any]] = []
        self.quests: list[dict[str, Any]] = []
        self.resources_by_id: dict[str, dict[str, Any]] = {}
        self._load()

    # ------------------------------------------------------------------
    # Loading

    def _read_yaml(self, path: Path) -> Any:
        if not path.is_file():
            return None
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    def _rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.pack_root))
        except ValueError:
            return str(path)

    def _load(self) -> None:
        self.meta = self._read_yaml(self.content_root / "meta.yaml") or {}
        self._load_locations()
        self._load_characters()
        self._load_items()
        self._load_achievements()
        self._load_quests()
        self._load_resources()
        self._load_scenes()

    def _load_locations(self) -> None:
        data = self._read_yaml(self.content_root / "locations.yaml")
        if isinstance(data, dict):
            data = data.get("locations") or []
        if not isinstance(data, list):
            return
        rel = "content/locations.yaml"
        for raw in data:
            if not isinstance(raw, dict) or "id" not in raw:
                continue
            exits: list[str] = []
            for ex in raw.get("exits") or []:
                if isinstance(ex, str):
                    exits.append(ex)
                elif isinstance(ex, dict) and "target" in ex:
                    exits.append(ex["target"])
            scene_hooks = [
                h.get("scene_id", "")
                for h in (raw.get("scene_hooks") or [])
                if isinstance(h, dict) and h.get("scene_id")
            ]
            self.locations_by_id[raw["id"]] = LocationNode(
                id=raw["id"],
                name=raw.get("name") or raw["id"],
                file=rel,
                exits=exits,
                scene_hooks=scene_hooks,
            )

    def _load_characters(self) -> None:
        data = self._read_yaml(self.content_root / "characters.yaml")
        if isinstance(data, dict):
            data = data.get("characters") or []
        if not isinstance(data, list):
            return
        for raw in data:
            if isinstance(raw, dict) and "id" in raw:
                self.characters_by_id[raw["id"]] = raw

    def _load_items(self) -> None:
        data = self._read_yaml(self.content_root / "items.yaml")
        if isinstance(data, dict):
            data = data.get("items") or []
        if not isinstance(data, list):
            return
        for raw in data:
            if isinstance(raw, dict) and "id" in raw:
                self.items_by_id[raw["id"]] = raw

    def _load_achievements(self) -> None:
        data = self._read_yaml(self.content_root / "achievements.yaml")
        if isinstance(data, dict):
            data = data.get("achievements") or []
        if isinstance(data, list):
            self.achievements = data

    def _load_quests(self) -> None:
        data = self._read_yaml(self.content_root / "quests.yaml")
        if isinstance(data, dict):
            data = data.get("quests") or []
        if isinstance(data, list):
            self.quests = data

    def _load_resources(self) -> None:
        data = self._read_yaml(self.content_root / "resources.yaml")
        if isinstance(data, dict):
            data = data.get("resources") or []
        if not isinstance(data, list):
            data = []
        for raw in data:
            if isinstance(raw, dict) and "id" in raw:
                self.resources_by_id[raw["id"]] = raw
        # Also read resources declared inside meta.yaml.
        for raw in (self.meta.get("resources") or []):
            if isinstance(raw, dict) and "id" in raw:
                self.resources_by_id.setdefault(raw["id"], raw)

    def _load_scenes(self) -> None:
        scenes_dir = self.content_root / "scenes"
        if not scenes_dir.is_dir():
            return
        for yaml_path in sorted(scenes_dir.rglob("*.yaml")):
            data = self._read_yaml(yaml_path)
            scenes = self._extract_scenes(data)
            rel = self._rel(yaml_path)
            for raw in scenes:
                if not isinstance(raw, dict) or "id" not in raw:
                    continue
                self.scenes_by_id[raw["id"]] = self._make_scene_node(raw, rel)

    def _extract_scenes(self, data: Any) -> list[Any]:
        if isinstance(data, dict) and "scenes" in data:
            return data["scenes"] or []
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "id" in data:
            return [data]
        return []

    def _make_scene_node(self, raw: dict[str, Any], rel: str) -> SceneNode:
        out: list[tuple[str, str]] = []
        for choice in raw.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            ns = choice.get("next_scene")
            if isinstance(ns, str) and ns:
                out.append(("choice", ns))
            for eff in choice.get("effects") or []:
                if isinstance(eff, dict) and eff.get("kind") == "play_scene":
                    tgt = eff.get("target") or eff.get("value")
                    if isinstance(tgt, str) and tgt:
                        out.append(("choice_effect", tgt))
        for eff in raw.get("on_end") or []:
            if isinstance(eff, dict) and eff.get("kind") == "play_scene":
                tgt = eff.get("target") or eff.get("value")
                if isinstance(tgt, str) and tgt:
                    out.append(("on_end", tgt))
        for line in raw.get("lines") or []:
            if not isinstance(line, dict):
                continue
            for eff in line.get("effects") or []:
                if isinstance(eff, dict) and eff.get("kind") == "play_scene":
                    tgt = eff.get("target") or eff.get("value")
                    if isinstance(tgt, str) and tgt:
                        out.append(("line_effect", tgt))
        sid = raw["id"]
        tags = raw.get("tags") or []
        is_ending = (
            sid.startswith("ending_")
            or any(isinstance(t, str) and "ending" in t for t in tags)
        )
        return SceneNode(
            id=sid,
            title=raw.get("title") or sid,
            file=rel,
            outgoing=out,
            has_choices=bool(raw.get("choices")),
            is_ending=is_ending,
            on_end_count=len(raw.get("on_end") or []),
        )

    # ------------------------------------------------------------------
    # Public queries

    def summary(self) -> dict[str, Any]:
        """A high-level rollup. Cheap, JSON-friendly."""
        return {
            "pack_root": str(self.pack_root),
            "title": self.meta.get("title"),
            "pack_format_version": self.meta.get("pack_format_version"),
            "start_location": self.meta.get("start_location"),
            "intro_scene": self.meta.get("intro_scene"),
            "counts": {
                "scenes": len(self.scenes_by_id),
                "locations": len(self.locations_by_id),
                "characters": len(self.characters_by_id),
                "items": len(self.items_by_id),
                "achievements": len(self.achievements),
                "quests": len(self.quests),
                "resources": len(self.resources_by_id),
                "endings": sum(1 for s in self.scenes_by_id.values()
                               if s.is_ending),
            },
        }

    def scenes(self) -> list[dict[str, Any]]:
        return [
            {
                "id": s.id,
                "title": s.title,
                "file": s.file,
                "outgoing": [
                    {"origin": o[0], "target": o[1]} for o in s.outgoing
                ],
                "has_choices": s.has_choices,
                "is_ending": s.is_ending,
            }
            for s in sorted(self.scenes_by_id.values(), key=lambda x: x.id)
        ]

    def locations(self) -> list[dict[str, Any]]:
        return [
            {
                "id": loc.id,
                "name": loc.name,
                "file": loc.file,
                "exits": loc.exits,
                "scene_hooks": loc.scene_hooks,
            }
            for loc in sorted(self.locations_by_id.values(), key=lambda x: x.id)
        ]

    def npcs(self) -> list[dict[str, Any]]:
        out = []
        for cid, raw in sorted(self.characters_by_id.items()):
            out.append({
                "id": cid,
                "name": raw.get("name"),
                "role": raw.get("role"),
                "is_heroine": raw.get("is_heroine", False),
            })
        return out

    def items(self) -> list[dict[str, Any]]:
        out = []
        for iid, raw in sorted(self.items_by_id.items()):
            out.append({
                "id": iid,
                "name": raw.get("name"),
                "consumable": raw.get("consumable", False),
                "max_stack": raw.get("max_stack"),
            })
        return out

    def variables(self) -> list[dict[str, Any]]:
        """Declared narrative-state variables (``content/variables.yaml``).

        Returns one row per declared variable (key/type/default/description/
        category), or an empty list when the pack ships no manifest. Pure
        YAML — parsed via :class:`VariableManifest`, no engine load.
        """
        from world_gal_game.core.variable_spec import VariableManifest
        manifest = VariableManifest.load(self.content_root / "variables.yaml")
        out = []
        for key in manifest.keys():
            spec = manifest.get(key)
            out.append({
                "key": key,
                "type": spec.type,
                "default": spec.coerced_default(),
                "category": spec.category,
                "description": spec.description,
            })
        return out

    # ------------------------------------------------------------------
    # Reachability

    def _scenes_triggered_by_locations(self) -> set[str]:
        """Scenes referenced by any location's ``scene_hooks``."""
        out: set[str] = set()
        for loc in self.locations_by_id.values():
            for sid in loc.scene_hooks:
                out.add(sid)
        return out

    def _start_scene_candidates(self) -> list[str]:
        """Heuristic: meta.intro_scene first, then any location-hooked scenes."""
        candidates: list[str] = []
        intro = self.meta.get("intro_scene")
        if isinstance(intro, str) and intro:
            candidates.append(intro)
        for sid in sorted(self._scenes_triggered_by_locations()):
            if sid not in candidates:
                candidates.append(sid)
        return candidates

    def reachability(self, *, start: str | Iterable[str] | None = None
                     ) -> dict[str, Any]:
        """BFS over scene → outgoing scenes from a starting set.

        ``start`` may be:
        - ``None``  → use :meth:`_start_scene_candidates`
        - ``str``   → start from one scene
        - iterable  → start from a set
        """
        if start is None:
            starts = self._start_scene_candidates()
        elif isinstance(start, str):
            starts = [start]
        else:
            starts = list(start)

        reachable: set[str] = set()
        frontier = [s for s in starts if s in self.scenes_by_id]
        while frontier:
            sid = frontier.pop(0)
            if sid in reachable:
                continue
            reachable.add(sid)
            node = self.scenes_by_id.get(sid)
            if node is None:
                continue
            for _origin, tgt in node.outgoing:
                if tgt in self.scenes_by_id and tgt not in reachable:
                    frontier.append(tgt)

        unreachable = sorted(set(self.scenes_by_id) - reachable)
        endings = sorted(
            s.id for s in self.scenes_by_id.values() if s.is_ending
        )
        ending_reachable = sorted(e for e in endings if e in reachable)
        ending_unreachable = sorted(e for e in endings if e not in reachable)
        return {
            "start": starts,
            "reachable": sorted(reachable),
            "unreachable": unreachable,
            "endings": {
                "all": endings,
                "reachable": ending_reachable,
                "unreachable": ending_unreachable,
            },
        }

    # ------------------------------------------------------------------
    # Dead-end detection

    def dead_ends(self) -> list[DeadEnd]:
        """Detect structural dead-ends in scenes / locations / choices.

        Returns a list of :class:`DeadEnd` records. Empty list = clean
        pack. Dead-ends are *warnings*, not errors — many scenes end
        without an explicit play_scene because the player returns to
        the exploration screen and reaches the next beat via location
        movement, which the inspector treats as a valid terminal too.

        What we *do* flag:

        - **scene_with_dead_choices** — scene has at least one choice
          but every choice has neither ``next_scene`` nor any effects.
          Clicking these does nothing, which is almost always a bug.
        - **location_unleavable** — location has no exits and no
          scene_hooks. The player can enter but can't leave.
        - **unreachable** — scene cannot be reached from ``intro_scene``
          or any location's scene_hook (set union). Orphan content.
        """
        out: list[DeadEnd] = []

        # Scenes whose every choice is a no-op.
        for s in self.scenes_by_id.values():
            if not s.has_choices:
                continue
            raw_scene = self._raw_scene(s.id)
            if raw_scene is None:
                continue
            choices = raw_scene.get("choices") or []
            if not choices:
                continue
            if all(self._choice_is_noop(c) for c in choices):
                out.append(DeadEnd(
                    kind="scene", target=s.id, file=s.file,
                    detail=(
                        f"scene '{s.id}' has choices but none of them lead "
                        "anywhere (no next_scene + no effects)"
                    ),
                ))

        # Locations with no exits AND no scene_hooks — player gets stuck.
        for loc in self.locations_by_id.values():
            if not loc.exits and not loc.scene_hooks:
                out.append(DeadEnd(
                    kind="location", target=loc.id, file=loc.file,
                    detail=(
                        f"location '{loc.id}' has no exits and no scene_hooks "
                        "— player can't leave or trigger anything"
                    ),
                ))

        # Orphan scenes: not reachable from intro_scene + any location hook.
        reach = self.reachability()
        for sid in reach["unreachable"]:
            node = self.scenes_by_id.get(sid)
            if node is None:
                continue
            out.append(DeadEnd(
                kind="unreachable", target=sid, file=node.file,
                detail=(
                    f"scene '{sid}' is not reachable from any start "
                    "(meta.intro_scene + location scene_hooks)"
                ),
            ))

        return out

    def _choice_is_noop(self, raw_choice: Any) -> bool:
        if not isinstance(raw_choice, dict):
            return False
        if raw_choice.get("next_scene"):
            return False
        if raw_choice.get("effects"):
            return False
        return True

    def _raw_scene(self, scene_id: str) -> dict[str, Any] | None:
        """Re-load a scene's raw dict — used by dead-end detection."""
        node = self.scenes_by_id.get(scene_id)
        if node is None:
            return None
        path = self.pack_root / node.file
        data = self._read_yaml(path)
        for raw in self._extract_scenes(data):
            if isinstance(raw, dict) and raw.get("id") == scene_id:
                return raw
        return None

    # ------------------------------------------------------------------
    # Graph output

    def graph(self, *, format: Literal["mermaid", "dot", "dict"] = "mermaid"
              ) -> str | dict:
        """Render the scene graph for visualisation."""
        if format == "dict":
            return {
                sid: [t for _origin, t in node.outgoing]
                for sid, node in sorted(self.scenes_by_id.items())
            }
        if format == "mermaid":
            return self._render_mermaid()
        if format == "dot":
            return self._render_dot()
        raise ValueError(f"unknown graph format: {format!r}")

    def _render_mermaid(self) -> str:
        lines = ["graph LR"]
        reachable = set(self.reachability()["reachable"])
        for sid, node in sorted(self.scenes_by_id.items()):
            shape = (
                f"  {self._mid(sid)}([\"{self._mlabel(node)}\"])"
                if node.is_ending
                else f"  {self._mid(sid)}[\"{self._mlabel(node)}\"]"
            )
            lines.append(shape)
            if sid not in reachable:
                # mark unreachable with a class
                lines.append(f"  class {self._mid(sid)} unreachable")
        for sid, node in sorted(self.scenes_by_id.items()):
            for origin, tgt in node.outgoing:
                arrow = "-->" if origin == "choice" else "-.->"
                lines.append(f"  {self._mid(sid)} {arrow}|{origin}| "
                             f"{self._mid(tgt)}")
        lines.append("  classDef unreachable fill:#f99,stroke:#900")
        return "\n".join(lines)

    @staticmethod
    def _mid(s: str) -> str:
        """Sanitise scene id for mermaid (alnum + underscore only)."""
        return "".join(c if c.isalnum() or c == "_" else "_" for c in s)

    @staticmethod
    def _mlabel(node: SceneNode) -> str:
        """Mermaid-safe label: title with id annotation, escapes quotes."""
        title = node.title.replace('"', '\\"')
        return f"{title}<br/>{node.id}"

    def _render_dot(self) -> str:
        lines = ["digraph pack {"]
        lines.append('  rankdir="LR"; node [shape=box];')
        reachable = set(self.reachability()["reachable"])
        for sid, node in sorted(self.scenes_by_id.items()):
            attrs = [f'label="{node.title} ({sid})"']
            if node.is_ending:
                attrs.append("shape=oval")
            if sid not in reachable:
                attrs.append("color=red")
            lines.append(f'  "{sid}" [{", ".join(attrs)}];')
        for sid, node in sorted(self.scenes_by_id.items()):
            for origin, tgt in node.outgoing:
                style = "solid" if origin == "choice" else "dashed"
                lines.append(f'  "{sid}" -> "{tgt}" '
                             f'[style={style}, label="{origin}"];')
        lines.append("}")
        return "\n".join(lines)
