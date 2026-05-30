"""DataflowAnalyzer — static cross-reference / impact analysis of a pack.

This sits alongside :class:`world_gal_game.dev.pack_inspector.PackInspector`
in the "developer view" tier of pillar B. Where ``PackInspector`` answers
"what does this pack contain and how do scenes connect?", this module answers
the orthogonal data-flow questions an agent needs before editing:

- **Where is a symbol written, and where is it read?** For every flag, scene,
  item, and resource the analyzer collects a :class:`SymbolUsage` — the list
  of writer sites and reader sites, each a human-readable :class:`Reference`
  (``"scene:meet_heroine_1#line3"``, ``"choice:meet_heroine_1.confess"``,
  ``"ending:ending_lover"`` ...). This is the impact-analysis the inspector
  lacks: ``PackInspector`` ignores choice ``requires`` / ``forbids`` entirely.
- **What are the conditioned scene -> scene edges?** Each :class:`Edge` records
  the source / destination scene, the route the transition takes (``choice`` /
  ``on_end`` / ``line``), and the gating conditions (a choice edge carries its
  ``requires`` / ``forbids`` as a guard list), so an agent can reason about
  *under what conditions* one scene leads to another.

Symbols are keyed off effect / condition ``kind`` (see the ``_FLAG_WRITE`` /
``_FLAG_READ`` / ``_SCENE_*`` / ``_ITEM_*`` / ``_RESOURCE_*`` tables below).
The walk is liberal: any effect whose ``kind`` contains ``"flag"`` is treated
as a flag writer keyed by ``target``, so flag-mutating effects contributed by
plugins are caught without enumerating them.

Unlike :class:`PackInspector` (which reads raw YAML), this analyzer walks the
**typed** models produced by :func:`world_gal_game.content_loader.load_pack`,
so it sees exactly what the runtime sees — including conditions on endings,
achievements, clues, and quests, not just scenes and locations. The trade-off:
``load_pack`` discovers and activates the pack's plugins, so constructing the
analyzer may print a one-line plugin summary to stdout. For pure, side-effect-
free structural queries that don't need typed fidelity, prefer
``PackInspector``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field


# ----------------------------------------------------------------------
# Symbol -> kind tables
#
# Each set lists the effect / condition kinds that touch a given symbol
# type. The symbol id is always read from ``.target``. These are matched
# case-sensitively against the kind string; plugin flag effects are also
# caught by the liberal "contains 'flag'" rule in ``_flag_writes``.

_FLAG_WRITE: frozenset[str] = frozenset(
    {"set_flag", "set_flag_if_unset", "increment_flag"}
)
_FLAG_READ: frozenset[str] = frozenset({"flag", "not_flag", "flag_eq"})

_SCENE_WRITE: frozenset[str] = frozenset({"play_scene"})
_SCENE_READ: frozenset[str] = frozenset({"scene_played"})

_ITEM_WRITE: frozenset[str] = frozenset({"give_item", "take_item", "use_item"})
_ITEM_READ: frozenset[str] = frozenset({"has_item"})

_RESOURCE_WRITE: frozenset[str] = frozenset(
    {"gain_resource", "spend_resource", "set_resource"}
)
_RESOURCE_READ: frozenset[str] = frozenset(
    {"resource_gte", "resource_lt", "resource_eq"}
)


# ----------------------------------------------------------------------
# Report data model


class Reference(BaseModel):
    """One write or read of a symbol at a specific authoring site.

    ``site`` is a human-readable location string, e.g.
    ``"scene:meet_heroine_1#line3"``, ``"choice:meet_heroine_1.confess"``,
    ``"scene_end:00_prologue"``, ``"exit:cafe->park"``,
    ``"scene_hook:park#0"``, ``"scene_requires:90_ending"``,
    ``"ending:ending_lover"``. ``kind`` is the effect / condition ``kind``
    that produced the reference (for flag-list fields such as
    ``Exit.requires_flags`` the synthetic kinds ``requires_flags`` /
    ``forbids_flags`` are used). ``role`` is whether the site writes or
    reads the symbol.
    """

    model_config = ConfigDict(extra="forbid")

    site: str
    kind: str
    role: Literal["write", "read"]


class SymbolUsage(BaseModel):
    """All writers and readers of a single symbol id."""

    model_config = ConfigDict(extra="forbid")

    writers: list[Reference] = Field(default_factory=list)
    readers: list[Reference] = Field(default_factory=list)


class Edge(BaseModel):
    """A directed scene -> scene transition with its gating conditions.

    ``via`` is how the transition is reached: ``"choice"`` (a choice's
    ``next_scene``), ``"on_end"`` (a ``play_scene`` effect in the scene's
    ``on_end``), or ``"line"`` (a ``play_scene`` effect on a line).

    The edge carries its gating in two forms. ``guard`` is the legacy flat list
    — one ``{"requires"|"forbids": kind, "target": ..., "value": ...}`` dict per
    condition (the role lives in the *key name*, so the condition ``kind`` is
    awkward to read generically). ``guard_logic`` is the canonical, unambiguous
    boolean form: ``{"all": [cond, ...], "none": [cond, ...]}`` where each
    ``cond`` is a uniform ``{"kind", "target", "value"}`` — the edge is taken
    iff **all** of ``all`` hold and **none** of ``none`` hold. (The engine has
    no OR *within* a guard; the OR in "when can scene Y be reached" is the union
    over every edge with ``dst == Y``.) Both empty ⇒ unconditional once the
    source scene plays.
    """

    model_config = ConfigDict(extra="forbid")

    src: str
    dst: str
    via: str
    guard: list[dict] = Field(default_factory=list)
    guard_logic: dict[str, list[dict]] = Field(
        default_factory=lambda: {"all": [], "none": []})


class DataflowReport(BaseModel):
    """The full cross-reference report for a pack.

    ``flags`` / ``scenes`` / ``items`` / ``resources`` map each symbol id to
    its :class:`SymbolUsage`. ``edges`` is the conditioned scene -> scene
    graph. ``undeclared_flags`` / ``unused_declared_flags`` are populated only
    when :meth:`DataflowAnalyzer.analyze` is given a ``declared_flags`` set.
    """

    model_config = ConfigDict(extra="forbid")

    flags: dict[str, SymbolUsage] = Field(default_factory=dict)
    scenes: dict[str, SymbolUsage] = Field(default_factory=dict)
    items: dict[str, SymbolUsage] = Field(default_factory=dict)
    resources: dict[str, SymbolUsage] = Field(default_factory=dict)
    edges: list[Edge] = Field(default_factory=list)
    undeclared_flags: list[str] = Field(default_factory=list)
    unused_declared_flags: list[str] = Field(default_factory=list)


# ----------------------------------------------------------------------
# Analyzer


class DataflowAnalyzer:
    """Static dataflow / cross-reference analysis of a pack directory.

    Construct with ``DataflowAnalyzer(pack_root)`` (either the pack root or
    its ``content/`` subdirectory). The pack is loaded lazily on the first
    query via :func:`world_gal_game.content_loader.load_pack`; subsequent
    queries reuse the same loaded :class:`GameState`.

    Loading activates the pack's plugins, so the first query may emit a
    one-line plugin summary to stdout — see the module docstring.
    """

    def __init__(self, pack_root: Path) -> None:
        self.pack_root = Path(pack_root).resolve()
        if self.pack_root.name == "content":
            self.pack_root = self.pack_root.parent
        self._state: Any | None = None

    # ------------------------------------------------------------------
    # Loading (lazy)

    def _content_root(self) -> Path:
        content = self.pack_root / "content"
        return content if content.is_dir() else self.pack_root

    def _load(self) -> Any:
        """Load the pack once and cache the resulting GameState."""
        if self._state is None:
            from world_gal_game.content_loader import load_pack

            state, _registry, _meta = load_pack(self._content_root())
            self._state = state
        return self._state

    # ------------------------------------------------------------------
    # Symbol classification helpers

    @staticmethod
    def _flag_writes(kind: str) -> bool:
        """A kind that writes a flag (declared writers + any plugin "*flag*")."""
        return kind in _FLAG_WRITE or "flag" in kind

    # ------------------------------------------------------------------
    # Public API

    def analyze(self, declared_flags: set[str] | None = None) -> DataflowReport:
        """Build the full :class:`DataflowReport` for the pack.

        Walks every typed model — scenes (lines, choices, ``on_end``,
        ``requires``), locations (exits, scene hooks, flag-list fields), and
        the endings / achievements / clues / quests registries — collecting
        writer and reader :class:`Reference`\\ s per symbol and the
        conditioned scene -> scene :class:`Edge`\\ s.

        When ``declared_flags`` is given (a plain set of flag ids):

        - ``undeclared_flags`` = flag ids used (written or read) anywhere that
          are *not* in the set,
        - ``unused_declared_flags`` = declared ids that never appear as a
          writer or reader.

        Per-scene walking is wrapped so a single malformed scene cannot abort
        the whole analysis; whatever can be read is collected. All lists in
        the report are sorted deterministically.
        """
        state = self._load()

        flags: dict[str, SymbolUsage] = {}
        scenes: dict[str, SymbolUsage] = {}
        items: dict[str, SymbolUsage] = {}
        resources: dict[str, SymbolUsage] = {}
        edges: list[Edge] = []

        # -- scenes ----------------------------------------------------
        story = getattr(state, "story", None)
        scene_map: dict[str, Any] = getattr(story, "scenes", {}) or {}
        for sid in sorted(scene_map):
            scene = scene_map[sid]
            try:
                self._walk_scene(
                    sid, scene, flags, scenes, items, resources, edges
                )
            except Exception:
                # Resilient: one bad scene must not sink the whole analysis.
                continue

        # -- locations -------------------------------------------------
        loc_map: dict[str, Any] = getattr(
            getattr(state, "map", None), "locations", {}
        ) or {}
        for lid in sorted(loc_map):
            try:
                self._walk_location(lid, loc_map[lid], flags)
            except Exception:
                continue

        # -- auxiliary condition holders (endings / achievements / ...) -
        # ending / achievement / clue / quest requires+forbids are real
        # reads of flags (and other symbols); e.g. an ending gated on
        # {kind: flag, target: ending_lover} is the only reader of that
        # flag. Walk them so impact analysis is complete.
        self._walk_condition_holders(state, flags, scenes, items, resources)

        report = DataflowReport(
            flags=self._sort_usage(flags),
            scenes=self._sort_usage(scenes),
            items=self._sort_usage(items),
            resources=self._sort_usage(resources),
            edges=self._sort_edges(edges),
        )

        if declared_flags is not None:
            declared = set(declared_flags)
            used = set(flags.keys())
            report.undeclared_flags = sorted(used - declared)
            report.unused_declared_flags = sorted(declared - used)

        return report

    def references(
        self, symbol_id: str, symbol_type: str | None = None
    ) -> dict:
        """Look up one symbol's usage across symbol types.

        Returns ``{"flags": ..., "scenes": ..., "items": ..., "resources":
        ...}`` where each value is the matching :class:`SymbolUsage` as a
        plain dict (``model_dump()``) or ``None`` if ``symbol_id`` is not used
        as that symbol type. When ``symbol_type`` is given (one of ``"flags"``
        / ``"scenes"`` / ``"items"`` / ``"resources"``) only that type is
        searched; the other entries are ``None``.
        """
        report = self.analyze()
        tables = {
            "flags": report.flags,
            "scenes": report.scenes,
            "items": report.items,
            "resources": report.resources,
        }
        out: dict[str, dict | None] = {}
        for name, table in tables.items():
            if symbol_type is not None and name != symbol_type:
                out[name] = None
                continue
            usage = table.get(symbol_id)
            out[name] = usage.model_dump() if usage is not None else None
        return out

    def conditioned_edges(self) -> list[Edge]:
        """Return the conditioned scene -> scene edges.

        These are the ``edges`` of :meth:`analyze`. Every edge whose source is
        a real scene is recorded, including edges whose destination scene id
        is not present in the pack (a dangling ``next_scene`` / ``play_scene``
        target) — recording them is deliberate so the list doubles as a
        broken-link probe; callers can intersect ``dst`` with the scene set to
        keep only resolvable edges.
        """
        return self.analyze().edges

    # ------------------------------------------------------------------
    # Per-model walkers

    def _walk_scene(
        self,
        sid: str,
        scene: Any,
        flags: dict[str, SymbolUsage],
        scenes: dict[str, SymbolUsage],
        items: dict[str, SymbolUsage],
        resources: dict[str, SymbolUsage],
        edges: list[Edge],
    ) -> None:
        # scene.requires — reads gating the whole scene
        for cond in getattr(scene, "requires", None) or []:
            self._read_condition(
                cond, f"scene_requires:{sid}", flags, scenes, items, resources
            )

        # lines — effects (writes) + requires (reads) + line play_scene edges
        for idx, line in enumerate(getattr(scene, "lines", None) or []):
            line_site = f"scene:{sid}#line{idx}"
            line_requires = getattr(line, "requires", None) or []
            for cond in line_requires:
                self._read_condition(
                    cond, line_site, flags, scenes, items, resources
                )
            for eff in getattr(line, "effects", None) or []:
                self._write_effect(
                    eff, line_site, flags, scenes, items, resources
                )
                dst = self._scene_target(eff)
                if dst is not None:
                    edges.append(
                        Edge(
                            src=sid,
                            dst=dst,
                            via="line",
                            guard=self._guard_from_requires(line_requires),
                            guard_logic=self._logic_from_requires(line_requires),
                        )
                    )

        # on_end — effects (writes) + on_end play_scene edges (gated by
        # scene.requires)
        for eff in getattr(scene, "on_end", None) or []:
            self._write_effect(
                eff, f"scene_end:{sid}", flags, scenes, items, resources
            )
            dst = self._scene_target(eff)
            if dst is not None:
                scene_requires = getattr(scene, "requires", None) or []
                edges.append(
                    Edge(
                        src=sid,
                        dst=dst,
                        via="on_end",
                        guard=self._guard_from_requires(scene_requires),
                        guard_logic=self._logic_from_requires(scene_requires),
                    )
                )

        # choices — requires/forbids (reads) + effects (writes) +
        # next_scene / play_scene edges
        for choice in getattr(scene, "choices", None) or []:
            cid = getattr(choice, "id", "") or ""
            choice_site = f"choice:{sid}.{cid}"
            requires = getattr(choice, "requires", None) or []
            forbids = getattr(choice, "forbids", None) or []
            for cond in requires:
                self._read_condition(
                    cond, choice_site, flags, scenes, items, resources
                )
            for cond in forbids:
                self._read_condition(
                    cond, choice_site, flags, scenes, items, resources
                )
            for eff in getattr(choice, "effects", None) or []:
                self._write_effect(
                    eff, choice_site, flags, scenes, items, resources
                )
                dst = self._scene_target(eff)
                if dst is not None:
                    edges.append(
                        Edge(
                            src=sid,
                            dst=dst,
                            via="choice",
                            guard=self._guard_from_choice(requires, forbids),
                            guard_logic=self._logic_from_choice(requires, forbids),
                        )
                    )
            next_scene = getattr(choice, "next_scene", None)
            if isinstance(next_scene, str) and next_scene:
                # next_scene is a scene reference (read) and a transition.
                self._add(scenes, next_scene, "read", choice_site, "next_scene")
                edges.append(
                    Edge(
                        src=sid,
                        dst=next_scene,
                        via="choice",
                        guard=self._guard_from_choice(requires, forbids),
                        guard_logic=self._logic_from_choice(requires, forbids),
                    )
                )

    def _walk_location(
        self, lid: str, loc: Any, flags: dict[str, SymbolUsage]
    ) -> None:
        # location-level flag gates
        self._read_flag_lists(
            loc, f"location:{lid}", flags
        )
        # exits
        for exit_obj in getattr(loc, "exits", None) or []:
            target = getattr(exit_obj, "target", "") or "?"
            site = f"exit:{lid}->{target}"
            self._read_flag_lists(exit_obj, site, flags)
        # scene hooks — flag-list gates AND full requires/forbids conditions
        for hidx, hook in enumerate(getattr(loc, "scene_hooks", None) or []):
            site = f"scene_hook:{lid}#{hidx}"
            self._read_flag_lists(hook, site, flags)
            for cond in getattr(hook, "requires", None) or []:
                self._read_condition(cond, site, flags, None, None, None)
            for cond in getattr(hook, "forbids", None) or []:
                self._read_condition(cond, site, flags, None, None, None)

    def _walk_condition_holders(
        self,
        state: Any,
        flags: dict[str, SymbolUsage],
        scenes: dict[str, SymbolUsage],
        items: dict[str, SymbolUsage],
        resources: dict[str, SymbolUsage],
    ) -> None:
        """Walk endings / achievements / clues / quests for read conditions.

        Each is a tracker holding a ``dict[str, <model>]`` whose models carry
        ``requires`` / ``forbids`` condition lists. The site prefix names the
        holder (``ending:`` / ``achievement:`` / ``clue:`` / ``quest:``).
        """
        holders = (
            ("ending", getattr(getattr(state, "endings", None), "endings", None)),
            (
                "achievement",
                getattr(getattr(state, "achievements", None), "achievements", None),
            ),
            ("clue", getattr(getattr(state, "clues", None), "clues", None)),
            ("quest", getattr(getattr(state, "quests", None), "quests", None)),
        )
        for prefix, store in holders:
            if not isinstance(store, dict):
                continue
            for entry_id in sorted(store):
                entry = store[entry_id]
                site = f"{prefix}:{entry_id}"
                try:
                    for cond in getattr(entry, "requires", None) or []:
                        self._read_condition(
                            cond, site, flags, scenes, items, resources
                        )
                    for cond in getattr(entry, "forbids", None) or []:
                        self._read_condition(
                            cond, site, flags, scenes, items, resources
                        )
                except Exception:
                    continue

    # ------------------------------------------------------------------
    # Reference recording

    def _write_effect(
        self,
        eff: Any,
        site: str,
        flags: dict[str, SymbolUsage],
        scenes: dict[str, SymbolUsage],
        items: dict[str, SymbolUsage],
        resources: dict[str, SymbolUsage],
    ) -> None:
        kind = getattr(eff, "kind", "") or ""
        target = getattr(eff, "target", "") or ""
        if self._flag_writes(kind) and target:
            self._add(flags, target, "write", site, kind)
        if kind in _SCENE_WRITE:
            dst = self._scene_target(eff)
            if dst:
                self._add(scenes, dst, "write", site, kind)
        if kind in _ITEM_WRITE and target:
            self._add(items, target, "write", site, kind)
        if kind in _RESOURCE_WRITE and target:
            self._add(resources, target, "write", site, kind)

    def _read_condition(
        self,
        cond: Any,
        site: str,
        flags: dict[str, SymbolUsage] | None,
        scenes: dict[str, SymbolUsage] | None,
        items: dict[str, SymbolUsage] | None,
        resources: dict[str, SymbolUsage] | None,
    ) -> None:
        kind = getattr(cond, "kind", "") or ""
        target = getattr(cond, "target", "") or ""
        if not target:
            return
        if flags is not None and kind in _FLAG_READ:
            self._add(flags, target, "read", site, kind)
        if scenes is not None and kind in _SCENE_READ:
            self._add(scenes, target, "read", site, kind)
        if items is not None and kind in _ITEM_READ:
            self._add(items, target, "read", site, kind)
        if resources is not None and kind in _RESOURCE_READ:
            self._add(resources, target, "read", site, kind)

    def _read_flag_lists(
        self, obj: Any, site: str, flags: dict[str, SymbolUsage]
    ) -> None:
        """Record ``requires_flags`` / ``forbids_flags`` as flag reads."""
        for flag_id in getattr(obj, "requires_flags", None) or []:
            if flag_id:
                self._add(flags, flag_id, "read", site, "requires_flags")
        for flag_id in getattr(obj, "forbids_flags", None) or []:
            if flag_id:
                self._add(flags, flag_id, "read", site, "forbids_flags")

    @staticmethod
    def _add(
        table: dict[str, SymbolUsage],
        symbol_id: str,
        role: Literal["write", "read"],
        site: str,
        kind: str,
    ) -> None:
        usage = table.setdefault(symbol_id, SymbolUsage())
        ref = Reference(site=site, kind=kind, role=role)
        bucket = usage.writers if role == "write" else usage.readers
        bucket.append(ref)

    # ------------------------------------------------------------------
    # Edge / guard construction

    @staticmethod
    def _scene_target(eff: Any) -> str | None:
        """Resolve the destination scene id of a ``play_scene`` effect."""
        if (getattr(eff, "kind", "") or "") not in _SCENE_WRITE:
            return None
        target = getattr(eff, "target", "") or ""
        if target:
            return target
        value = getattr(eff, "value", None)
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _cond_dict(cond: Any, role_key: str) -> dict:
        """Render a gating condition as a ``{role_key: kind, target, value}``."""
        return {
            role_key: getattr(cond, "kind", "") or "",
            "target": getattr(cond, "target", "") or "",
            "value": getattr(cond, "value", None),
        }

    @staticmethod
    def _cond_entry(cond: Any) -> dict:
        """Render a condition role-free, as ``{kind, target, value}``."""
        return {
            "kind": getattr(cond, "kind", "") or "",
            "target": getattr(cond, "target", "") or "",
            "value": getattr(cond, "value", None),
        }

    @classmethod
    def _guard_from_choice(
        cls, requires: Iterable[Any], forbids: Iterable[Any]
    ) -> list[dict]:
        guard = [cls._cond_dict(c, "requires") for c in requires]
        guard += [cls._cond_dict(c, "forbids") for c in forbids]
        return guard

    @classmethod
    def _guard_from_requires(cls, requires: Iterable[Any]) -> list[dict]:
        return [cls._cond_dict(c, "requires") for c in requires]

    @classmethod
    def _logic_from_choice(
        cls, requires: Iterable[Any], forbids: Iterable[Any]
    ) -> dict[str, list[dict]]:
        """Canonical boolean form: all `requires` true AND none of `forbids`."""
        return {
            "all": [cls._cond_entry(c) for c in requires],
            "none": [cls._cond_entry(c) for c in forbids],
        }

    @classmethod
    def _logic_from_requires(cls, requires: Iterable[Any]) -> dict[str, list[dict]]:
        return {"all": [cls._cond_entry(c) for c in requires], "none": []}

    # ------------------------------------------------------------------
    # Deterministic ordering

    @staticmethod
    def _sort_usage(
        table: dict[str, SymbolUsage]
    ) -> dict[str, SymbolUsage]:
        """Return the table key-sorted, with each ref list sorted by site."""
        out: dict[str, SymbolUsage] = {}
        for symbol_id in sorted(table):
            usage = table[symbol_id]
            out[symbol_id] = SymbolUsage(
                writers=sorted(usage.writers, key=lambda r: (r.site, r.kind)),
                readers=sorted(usage.readers, key=lambda r: (r.site, r.kind)),
            )
        return out

    @staticmethod
    def _sort_edges(edges: list[Edge]) -> list[Edge]:
        return sorted(edges, key=lambda e: (e.src, e.dst, e.via))
