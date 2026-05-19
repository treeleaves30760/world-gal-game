"""PluginManager — discover, load, and lifecycle plugins.

Plugins are discovered from three roots, in order:

1. ``world_gal_game/plugins_user/`` (global, beside the engine package).
   Anything dropped here is available to *every* pack.
2. ``~/.world-gal-game/plugins/`` (per-user, override-friendly).
3. ``<pack_root>/plugins/`` (pack-local, only active for that pack).

Each plugin lives in its own directory; its layout is::

    my_plugin/
        plugin.yaml         # manifest (required)
        plugin.py           # entry module (or whatever entry_module names)
        assets/             # optional plugin-private assets
        ...

The manager:

- :meth:`discover` — scans the three roots, parses every ``plugin.yaml``
  into a :class:`PluginManifest`, and returns a list of
  :class:`PluginRecord`. Discovery does *not* import any plugin code.
- :meth:`activate` — resolves dependencies (topological sort), imports
  each plugin's entry module inside :func:`loading` context (so
  decorators auto-stamp the right plugin id), checks engine version
  compatibility, and records load failures without crashing.
- :meth:`fire_hook` — fan-out wrapper over :meth:`HookRegistry.fire`.
- :meth:`deactivate` — undo by unregistering every entry that belongs
  to each loaded plugin.

Concurrency: a single manager is **not** thread-safe. The engine
creates one per pack load on the main thread.
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .errors import (
    DependencyError, IncompatibleEngineError, ManifestError,
    PluginError, PluginLoadError, PluginRuntimeError, isolate,
)
from .manifest import PluginManifest
from .registry import (
    EFFECT_REGISTRY, CONDITION_REGISTRY, HOOK_REGISTRY, INSPECT_FIELD_REGISTRY,
    WIDGET_REGISTRY, SCENE_REGISTRY, BRAIN_REGISTRY, DIALOGUE_OP_REGISTRY,
    loading,
)
from .context import HookEvent, PluginContext

if TYPE_CHECKING:
    pass


_log = logging.getLogger("world_gal_game.plugins.manager")


# ----------------------------------------------------------------------
# Records


@dataclass
class PluginRecord:
    """One discovered plugin plus its load state."""

    manifest: PluginManifest
    root: Path
    source: str                       # "engine" | "user" | "pack"
    state: str = "discovered"         # "discovered" | "loaded" | "failed" | "disabled"
    module: Any = None
    error: PluginError | None = None
    # Kinds it registered (for clean unregister).
    effect_kinds: list[str] = field(default_factory=list)
    condition_kinds: list[str] = field(default_factory=list)
    hook_events: list[str] = field(default_factory=list)
    inspect_keys: list[str] = field(default_factory=list)
    widget_names: list[str] = field(default_factory=list)
    scene_ids: list[str] = field(default_factory=list)
    brain_names: list[str] = field(default_factory=list)
    dialogue_ops: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.manifest.id


# ----------------------------------------------------------------------
# Discovery helpers


def _engine_plugin_root() -> Path:
    """Engine-shipped plugin root (``world_gal_game/plugins_user/``).

    The folder may not exist in a fresh checkout; that's fine — discovery
    just returns nothing.
    """
    return Path(__file__).resolve().parent.parent / "plugins_user"


def _user_plugin_root() -> Path:
    """Per-user plugin root (``~/.world-gal-game/plugins/``)."""
    return Path.home() / ".world-gal-game" / "plugins"


def _is_plugin_dir(path: Path) -> bool:
    return path.is_dir() and (path / "plugin.yaml").is_file()


def _scan_root(root: Path, source: str) -> list[tuple[Path, str]]:
    """Return list of (plugin_dir, source) under *root*."""
    if not root.is_dir():
        return []
    out: list[tuple[Path, str]] = []
    for child in sorted(root.iterdir()):
        if child.name.startswith("."):
            continue
        if _is_plugin_dir(child):
            out.append((child, source))
    return out


def _load_manifest(plugin_dir: Path) -> PluginManifest:
    return PluginManifest.from_yaml(plugin_dir / "plugin.yaml")


# ----------------------------------------------------------------------
# PluginManager


class PluginManager:
    """Lifecycle-aware container for a single pack's plugin set."""

    def __init__(self, pack_root: Path | None = None,
                 *, engine_version: str = "0.1.0",
                 extra_search_roots: list[Path] | None = None) -> None:
        self.pack_root = Path(pack_root).resolve() if pack_root else None
        self.engine_version = engine_version
        self.extra_search_roots = [Path(p) for p in (extra_search_roots or [])]
        self.records: dict[str, PluginRecord] = {}
        self.errors: list[PluginRuntimeError] = []
        # Cached PluginContext for hook fires within this manager.
        self._ctx: PluginContext | None = None

    # ------------------------------------------------------------------
    # Public API

    def discover(self) -> list[PluginRecord]:
        """Scan all roots, parse manifests, populate ``self.records``.

        Manifest parse failures are recorded as ``state="failed"`` with
        the error attached, *not* raised, so a single malformed plugin
        does not block discovery of the rest.
        """
        roots: list[tuple[Path, str]] = []
        roots.extend(_scan_root(_engine_plugin_root(), "engine"))
        roots.extend(_scan_root(_user_plugin_root(), "user"))
        for r in self.extra_search_roots:
            roots.extend(_scan_root(r, "extra"))
        if self.pack_root is not None:
            roots.extend(_scan_root(self.pack_root / "plugins", "pack"))

        for plugin_dir, source in roots:
            try:
                manifest = _load_manifest(plugin_dir)
            except ManifestError as exc:
                _log.warning("manifest parse failed at %s: %s", plugin_dir, exc)
                # We can't construct a normal PluginRecord without an id,
                # so synthesise a sentinel id from the directory name.
                synthetic = PluginManifest(
                    id=_safe_id_from_path(plugin_dir),
                    name=plugin_dir.name,
                    description="(manifest failed to parse)",
                )
                rec = PluginRecord(
                    manifest=synthetic, root=plugin_dir, source=source,
                    state="failed", error=exc,
                )
                # Avoid id collision with successfully-parsed plugins.
                key = f"@invalid:{synthetic.id}:{id(plugin_dir)}"
                self.records[key] = rec
                continue

            if manifest.id in self.records:
                prior = self.records[manifest.id]
                # Pack-local overrides user overrides engine: see source rank.
                if _source_rank(source) > _source_rank(prior.source):
                    self.records[manifest.id] = PluginRecord(
                        manifest=manifest, root=plugin_dir, source=source,
                    )
                # otherwise keep the earlier (higher-priority) record
                continue
            self.records[manifest.id] = PluginRecord(
                manifest=manifest, root=plugin_dir, source=source,
            )

        return self.list_records()

    def activate(self, context: PluginContext | None = None) -> list[PluginRecord]:
        """Import + register every discovered plugin.

        Returns the list of records whose state is ``"loaded"``.
        Failures are kept in the records list with ``state="failed"``.
        """
        self._ctx = context  # cached for fire_hook convenience

        loadable = [r for r in self.records.values() if r.state == "discovered"]
        ordered = self._topo_sort(loadable)

        for record in ordered:
            try:
                record.manifest.check_engine_compatible(self.engine_version)
            except IncompatibleEngineError as exc:
                record.state = "failed"
                record.error = exc
                _log.warning("plugin %s: %s", record.id, exc)
                continue
            self._load_one(record)

        return [r for r in self.records.values() if r.state == "loaded"]

    def deactivate(self) -> None:
        """Unregister every entry owned by any loaded plugin."""
        for record in self.records.values():
            if record.state != "loaded":
                continue
            EFFECT_REGISTRY.unregister_plugin(record.id)
            CONDITION_REGISTRY.unregister_plugin(record.id)
            HOOK_REGISTRY.unregister_plugin(record.id)
            INSPECT_FIELD_REGISTRY.unregister_plugin(record.id)
            WIDGET_REGISTRY.unregister_plugin(record.id)
            SCENE_REGISTRY.unregister_plugin(record.id)
            BRAIN_REGISTRY.unregister_plugin(record.id)
            DIALOGUE_OP_REGISTRY.unregister_plugin(record.id)
            record.state = "discovered"
            record.module = None

    def set_context(self, ctx: PluginContext) -> None:
        """Replace the cached :class:`PluginContext` used by :meth:`fire_hook`.

        Useful when the engine builds the manager before the GameState
        exists (pack discovery happens first) and wants to rebind once
        the full state is ready.
        """
        self._ctx = ctx

    def fire_hook(self, event: str, ctx: PluginContext | None = None,
                  /, **kwargs: Any) -> list[PluginRuntimeError]:
        """Fire ``event`` via HOOK_REGISTRY using the manager's cached context."""
        ctx = ctx or self._ctx
        if ctx is None:
            raise ValueError(
                "no PluginContext available — call activate(context=...) "
                "or pass ctx=... explicitly to fire_hook"
            )
        errs = HOOK_REGISTRY.fire(event, ctx, **kwargs)
        self.errors.extend(errs)
        return errs

    def list_records(self) -> list[PluginRecord]:
        """All records, in discovery order, by id."""
        return sorted(
            self.records.values(),
            key=lambda r: (r.state != "loaded", r.id),
        )

    def loaded(self) -> list[PluginRecord]:
        return [r for r in self.records.values() if r.state == "loaded"]

    def failed(self) -> list[PluginRecord]:
        return [r for r in self.records.values() if r.state == "failed"]

    def summary(self) -> dict[str, Any]:
        """Machine-readable summary — useful in CLI / capability manifest."""
        return {
            "engine_version": self.engine_version,
            "pack_root": str(self.pack_root) if self.pack_root else None,
            "loaded": [
                {
                    "id": r.id,
                    "name": r.manifest.name,
                    "version": r.manifest.version,
                    "source": r.source,
                    "effects": r.effect_kinds,
                    "conditions": r.condition_kinds,
                    "hooks": r.hook_events,
                    "inspect_fields": r.inspect_keys,
                    "widgets": r.widget_names,
                    "scenes": r.scene_ids,
                    "brains": r.brain_names,
                    "dialogue_ops": r.dialogue_ops,
                    "side_effects": r.manifest.side_effects.model_dump(),
                }
                for r in self.loaded()
            ],
            "failed": [
                {
                    "id": r.id,
                    "source": r.source,
                    "error": str(r.error) if r.error else None,
                }
                for r in self.failed()
            ],
        }

    def print_summary(self) -> None:
        """Human-friendly one-shot summary; called on pack load by default."""
        for r in self.loaded():
            ext = []
            if r.effect_kinds:
                ext.append(f"effects=[{', '.join(r.effect_kinds)}]")
            if r.condition_kinds:
                ext.append(f"conditions=[{', '.join(r.condition_kinds)}]")
            if r.hook_events:
                ext.append(f"hooks=[{', '.join(r.hook_events)}]")
            side_flags = [
                k for k, v in r.manifest.side_effects.model_dump().items()
                if v and k not in ("other",)
            ]
            if side_flags:
                ext.append(f"side_effects=[{', '.join(side_flags)}]")
            _log.info("plugin loaded %s@%s (%s): %s",
                      r.id, r.manifest.version, r.source,
                      "; ".join(ext) if ext else "(no extensions)")
        for r in self.failed():
            _log.warning("plugin failed %s (%s): %s",
                         r.id, r.source, r.error)

    # ------------------------------------------------------------------
    # Internal: dependency resolution

    def _topo_sort(self, records: list[PluginRecord]) -> list[PluginRecord]:
        """Kahn's algorithm; sets ``state="failed"`` on members of any cycle."""
        by_id = {r.id: r for r in records}
        # Validate deps exist; missing ones mark the dependent failed.
        for record in list(records):
            missing = [d for d in record.manifest.depends if d not in by_id]
            if missing:
                record.state = "failed"
                record.error = DependencyError(
                    f"plugin '{record.id}' depends on missing plugin(s): "
                    + ", ".join(missing),
                    plugin_id=record.id,
                )

        live = [r for r in records if r.state != "failed"]
        live_ids = {r.id for r in live}
        in_degree = {r.id: 0 for r in live}
        edges: dict[str, list[str]] = {r.id: [] for r in live}
        for r in live:
            for dep in r.manifest.depends:
                if dep in live_ids:
                    edges[dep].append(r.id)
                    in_degree[r.id] += 1

        ready = sorted([rid for rid, d in in_degree.items() if d == 0])
        order: list[str] = []
        while ready:
            rid = ready.pop(0)
            order.append(rid)
            for nxt in edges.get(rid, []):
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    ready.append(nxt)
                    ready.sort()

        if len(order) != len(live):
            cycle = [rid for rid in live_ids if rid not in order]
            for rid in cycle:
                rec = by_id[rid]
                rec.state = "failed"
                rec.error = DependencyError(
                    f"dependency cycle detected involving: {', '.join(sorted(cycle))}",
                    plugin_id=rid,
                    cycle=sorted(cycle),
                )
            # Still load the non-cyclic ones in their resolved order.

        return [by_id[rid] for rid in order]

    # ------------------------------------------------------------------
    # Internal: load + import

    def _load_one(self, record: PluginRecord) -> None:
        """Import a plugin's entry module and snapshot what it registered.

        Re-load safety: if the same plugin id has any entries still in the
        global registries from a previous session/load (which happens
        every time a new ``HeadlessSession`` opens the same pack), clear
        them first. Without this, the second pack load raises
        :class:`DuplicateKindError` when the plugin's decorators run again.
        """
        for reg in (EFFECT_REGISTRY, CONDITION_REGISTRY, HOOK_REGISTRY,
                    INSPECT_FIELD_REGISTRY, WIDGET_REGISTRY, SCENE_REGISTRY,
                    BRAIN_REGISTRY, DIALOGUE_OP_REGISTRY):
            reg.unregister_plugin(record.id)

        # Snapshot kinds-by-plugin before, so we can diff afterwards.
        before_effects = set(EFFECT_REGISTRY.list_kinds())
        before_conds = set(CONDITION_REGISTRY.list_kinds())
        before_hooks = {(e, h.fn) for e in HOOK_REGISTRY.list_events()
                        for h in HOOK_REGISTRY.handlers_for(e)}
        before_inspect = set(INSPECT_FIELD_REGISTRY.list_keys())
        before_widgets = set(WIDGET_REGISTRY.list_names())
        before_scenes = set(SCENE_REGISTRY.list_names())
        before_brains = set(BRAIN_REGISTRY.list_names())
        before_dops = set(DIALOGUE_OP_REGISTRY.list_names())

        try:
            with loading(record.id):
                self._import_entry_module(record)
        except PluginError as exc:
            record.state = "failed"
            record.error = exc
            _log.warning("plugin %s failed to load: %s", record.id, exc)
            return
        except Exception as exc:  # entry module raised unrelated
            record.state = "failed"
            record.error = PluginLoadError(record.id, str(exc), cause=exc)
            _log.warning("plugin %s raised during import: %s", record.id, exc)
            return

        # Compute what was added (kinds/hooks/inspect keys owned by this plugin).
        after_effects = set(EFFECT_REGISTRY.list_kinds())
        after_conds = set(CONDITION_REGISTRY.list_kinds())
        after_inspect = set(INSPECT_FIELD_REGISTRY.list_keys())

        record.effect_kinds = sorted(
            k for k in after_effects - before_effects
            if (e := EFFECT_REGISTRY.get(k)) and e.plugin_id == record.id
        )
        record.condition_kinds = sorted(
            k for k in after_conds - before_conds
            if (e := CONDITION_REGISTRY.get(k)) and e.plugin_id == record.id
        )
        record.inspect_keys = sorted(after_inspect - before_inspect)
        record.widget_names = sorted(
            set(WIDGET_REGISTRY.list_names()) - before_widgets)
        record.scene_ids = sorted(set(SCENE_REGISTRY.list_names()) - before_scenes)
        record.brain_names = sorted(set(BRAIN_REGISTRY.list_names()) - before_brains)
        record.dialogue_ops = sorted(set(DIALOGUE_OP_REGISTRY.list_names()) - before_dops)
        # Hooks need an explicit re-scan since they're list-valued.
        record.hook_events = sorted({
            event for event in HOOK_REGISTRY.list_events()
            for h in HOOK_REGISTRY.handlers_for(event)
            if h.plugin_id == record.id and (event, h.fn) not in before_hooks
        })

        record.state = "loaded"

    def _import_entry_module(self, record: PluginRecord) -> None:
        """importlib.util based module loader, isolated to the plugin dir."""
        entry_name = record.manifest.entry_module or "plugin"
        # If the entry value contains a slash, treat as a path; else as
        # a module file name (.py is appended automatically).
        if "/" in entry_name or "\\" in entry_name:
            file_path = (record.root / entry_name).resolve()
        else:
            file_path = (record.root / f"{entry_name}.py").resolve()
        if not file_path.is_file():
            raise PluginLoadError(
                record.id, f"entry module not found at {file_path}")

        # Build a unique module name so different plugins with the same
        # entry filename don't collide in sys.modules.
        mod_name = f"wgg_plugin__{record.id}"
        spec = importlib.util.spec_from_file_location(mod_name, file_path)
        if spec is None or spec.loader is None:
            raise PluginLoadError(
                record.id, f"importlib could not build spec for {file_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(mod_name, None)
            raise
        record.module = module


# ----------------------------------------------------------------------
# Helpers


def _source_rank(source: str) -> int:
    """Higher rank wins on id collision: pack > user > engine."""
    return {"engine": 0, "user": 1, "extra": 1, "pack": 2}.get(source, 0)


def _safe_id_from_path(p: Path) -> str:
    """Synth a slug id from a directory path (for malformed manifests)."""
    raw = p.name.lower().replace("-", "_").replace(".", "_")
    out = "".join(c for c in raw if c.isalnum() or c == "_")
    if not out or not out[0].isalpha():
        out = "p_" + out
    return out[:64] or "p_invalid"
