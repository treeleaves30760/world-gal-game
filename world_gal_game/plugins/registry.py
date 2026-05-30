"""Global plugin registry + decorator factories.

This module is intentionally **import-time only** — importing it has no
side effects beyond creating empty global registry instances. The
builtin effect/condition handlers in :mod:`builtin_effects` and
:mod:`builtin_conditions` register themselves at *their* import time;
the package ``__init__.py`` triggers those imports so the registry is
populated before any :class:`GameState` operation.

The registry shape is intentionally narrow:

- :class:`EffectRegistry`         — kind → handler ``(state, eff) -> dict``
- :class:`ConditionRegistry`      — kind → handler ``(state, cond) -> bool``
- :class:`HookRegistry`           — event → list of handlers ``(ctx, **kwargs) -> None``
- :class:`InspectFieldRegistry`   — key → producer ``(state) -> Any``

Each entry carries metadata (``plugin_id``, ``description``, ``schema``)
so the Capability Manifest and PackEditor can introspect the engine at
runtime.

Concurrency: registration uses a module-level :class:`threading.RLock`.
Lookup is read-only and lock-free — handlers are tuples; the dict's GIL
guarantees atomic get.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Protocol, TYPE_CHECKING

from .errors import DuplicateKindError, UnknownKindError, isolate

if TYPE_CHECKING:
    from pydantic import BaseModel

    from ..core.game_state import GameState
    from ..core.story_graph import Condition, Effect
    from .context import PluginContext


# ----------------------------------------------------------------------
# Handler protocol types (runtime: just callables; static: typed)

class EffectHandler(Protocol):
    def __call__(self, state: "GameState", eff: "Effect") -> dict[str, Any]: ...


class ConditionHandler(Protocol):
    def __call__(self, state: "GameState", cond: "Condition") -> bool: ...


class HookHandler(Protocol):
    def __call__(self, ctx: "PluginContext", **kwargs: Any) -> None: ...


class InspectFieldProducer(Protocol):
    def __call__(self, state: "GameState") -> Any: ...


# ----------------------------------------------------------------------
# Registry entry types


@dataclass(frozen=True)
class EffectEntry:
    """Bundle a kind handler with its metadata.

    ``args_model`` (optional) is a pydantic model describing this kind's
    (target/value/stat) arguments. It powers JSON-Schema export in the
    capability manifest and build/lint-time validation; it never feeds the
    handler nor the tolerant runtime dispatch.
    """

    kind: str
    fn: Callable[["GameState", "Effect"], dict[str, Any]]
    plugin_id: str
    description: str = ""
    signature: dict[str, Any] = field(default_factory=dict)
    args_model: type["BaseModel"] | None = None


@dataclass(frozen=True)
class ConditionEntry:
    kind: str
    fn: Callable[["GameState", "Condition"], bool]
    plugin_id: str
    description: str = ""
    signature: dict[str, Any] = field(default_factory=dict)
    args_model: type["BaseModel"] | None = None


@dataclass(frozen=True)
class HookEntry:
    event: str
    fn: Callable[..., None]
    plugin_id: str
    description: str = ""
    priority: int = 100  # lower = earlier


@dataclass(frozen=True)
class InspectFieldEntry:
    key: str
    fn: Callable[["GameState"], Any]
    plugin_id: str
    description: str = ""


@dataclass(frozen=True)
class WidgetEntry:
    """A plugin-registered pygame widget class.

    Use-cases:
    - PluginContext.spawn_widget(name, ...) instantiates the class
    - Scene authors look up plugin widgets by name in their ``enter()``
    - Capability Manifest reports widget availability
    """

    name: str
    cls: type
    plugin_id: str
    description: str = ""
    signature: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SceneEntry:
    """A plugin-registered :class:`Scene` subclass.

    Plugins use this to add new screens (minigames, custom inventories,
    NPC-specific UIs). The engine exposes ``scene_registry.spawn(id, ctx)``
    so scene_id strings can be resolved into Scene instances at runtime.
    """

    scene_id: str
    cls: type
    plugin_id: str
    description: str = ""
    overlay: bool = False  # default is_overlay for instantiated scenes


@dataclass(frozen=True)
class BrainEntry:
    """A plugin-registered LLMBrain implementation.

    The engine reads ``meta.yaml.brain`` (or ``EngineConfig.brain``) and,
    if a plugin has registered a brain with that name, instantiates it.
    Otherwise the engine falls back to EchoBrain.
    """

    name: str
    cls: type
    plugin_id: str
    description: str = ""


@dataclass(frozen=True)
class DialogueOpEntry:
    """A plugin-registered inline dialogue directive.

    Directives appear in line text as ``[[name:arg]]`` and fire a hook
    at parse time. This is registry + payload extraction only; there is
    no UI rendering, plugin hooks do whatever they want with the payload.
    """

    name: str
    fn: Callable[..., Any]
    plugin_id: str
    description: str = ""


@dataclass(frozen=True)
class PortraitBackendEntry:
    """A plugin-registered portrait render backend.

    A backend governs how a *resting* portrait animates once its enter/exit
    transition has settled — procedural breathing, sprite-sheet frames, or a
    native rig (Live2D/Spine) shipped as a desktop-only plugin. It is the seam
    between "which portrait" (``PortraitSpec`` resolution) and "how it moves"
    (per-frame draw), so the engine core binds to no specific animation library.

    ``cls`` is instantiated per slot by the dialogue scene as
    ``cls(spec, assets, fallback_size)`` and is expected to expose
    ``update(dt)`` / ``draw(surface, rect, *, flip, alpha)`` /
    ``base_surface()`` (see :mod:`world_gal_game.ui.portrait_backend`). The
    built-in ``"static"`` backend is implicit (no entry): it means "no
    animation, blit the still".
    """

    name: str
    cls: type
    plugin_id: str
    description: str = ""


@dataclass(frozen=True)
class AmbientBackendEntry:
    """A plugin-registered ambient / weather overlay backend.

    An ambient backend draws a full-screen atmospheric overlay (rain, snow,
    falling petals, drifting sparkles, fireflies ...) above the world layer and
    below the text box, persisting across lines until changed or cleared. It is
    the tenth extension category and mirrors :class:`PortraitBackendEntry`: the
    engine core owns no specific particle system, so themes ship their own.

    ``cls`` is instantiated by the dialogue scene as
    ``cls(params, screen_size)`` and is expected to expose ``update(dt)`` and
    ``draw(surface)`` (see :mod:`world_gal_game.ui.ambient_backend`). It must be
    deterministic (no global RNG) so a save/replay reproduces the same frame.
    """

    name: str
    cls: type
    plugin_id: str
    description: str = ""


# ----------------------------------------------------------------------
# Registries


class _KindRegistry:
    """Generic kind-keyed registry shared by Effect / Condition."""

    _entry_type: type
    _category: str

    def __init__(self, *, entry_type: type, category: str) -> None:
        self._entry_type = entry_type
        self._category = category
        self._entries: dict[str, Any] = {}
        self._lock = threading.RLock()

    # -- Mutating ------------------------------------------------------

    def register(self, entry: Any) -> None:
        """Register ``entry``. Raise :class:`DuplicateKindError` on conflict.

        The check is **identity-aware**: re-registering the *same* function
        for the *same* kind is a no-op (this happens when a module is
        re-imported during hot reload tests). Different functions for the
        same kind always conflict.
        """
        kind = entry.kind
        with self._lock:
            existing = self._entries.get(kind)
            if existing is not None:
                if existing.fn is entry.fn and existing.plugin_id == entry.plugin_id:
                    return  # idempotent
                raise DuplicateKindError(
                    kind=kind,
                    existing_plugin=existing.plugin_id,
                    new_plugin=entry.plugin_id,
                    category=self._category,
                )
            self._entries[kind] = entry

    def unregister(self, kind: str) -> None:
        """Remove ``kind``. No-op if absent."""
        with self._lock:
            self._entries.pop(kind, None)

    def unregister_plugin(self, plugin_id: str) -> list[str]:
        """Remove every entry owned by ``plugin_id``. Return kinds removed."""
        removed: list[str] = []
        with self._lock:
            for kind, entry in list(self._entries.items()):
                if entry.plugin_id == plugin_id:
                    del self._entries[kind]
                    removed.append(kind)
        return removed

    def clear_plugin(self, plugin_id: str) -> list[str]:
        """Alias for unregister_plugin (clearer in test code)."""
        return self.unregister_plugin(plugin_id)

    # -- Reading -------------------------------------------------------

    def get(self, kind: str) -> Any | None:
        return self._entries.get(kind)

    def require(self, kind: str) -> Any:
        entry = self._entries.get(kind)
        if entry is None:
            raise UnknownKindError(kind, category=self._category)
        return entry

    def has(self, kind: str) -> bool:
        return kind in self._entries

    def list_kinds(self) -> list[str]:
        """All registered kinds, sorted."""
        return sorted(self._entries.keys())

    def list_entries(self) -> list[Any]:
        """Snapshot of all entries (sorted by kind) — for capability manifest."""
        return [self._entries[k] for k in sorted(self._entries.keys())]

    def kinds_by_plugin(self) -> dict[str, list[str]]:
        """Group kinds by owning plugin id — for capability summary."""
        out: dict[str, list[str]] = {}
        for kind in sorted(self._entries.keys()):
            pid = self._entries[kind].plugin_id
            out.setdefault(pid, []).append(kind)
        return out

    # Iteration / size (handy in tests)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, kind: object) -> bool:
        return isinstance(kind, str) and kind in self._entries

    def __iter__(self):
        return iter(self._entries)


class EffectRegistry(_KindRegistry):
    """Kind → EffectEntry."""

    def __init__(self) -> None:
        super().__init__(entry_type=EffectEntry, category="effect")


class ConditionRegistry(_KindRegistry):
    """Kind → ConditionEntry."""

    def __init__(self) -> None:
        super().__init__(entry_type=ConditionEntry, category="condition")


class HookRegistry:
    """Event-keyed list of hook handlers.

    Many plugins may hook the same event; on fire they run in priority
    order (ascending), then by registration order within the same priority.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[HookEntry]] = {}
        self._lock = threading.RLock()

    def register(self, entry: HookEntry) -> None:
        with self._lock:
            lst = self._handlers.setdefault(entry.event, [])
            # De-dupe identical (event, fn, plugin_id) triples — same as
            # _KindRegistry, lets module re-import be idempotent.
            for existing in lst:
                if existing.fn is entry.fn and existing.plugin_id == entry.plugin_id:
                    return
            lst.append(entry)
            lst.sort(key=lambda e: e.priority)

    def unregister_plugin(self, plugin_id: str) -> int:
        """Remove every hook owned by ``plugin_id``. Return count removed."""
        n = 0
        with self._lock:
            for event, lst in list(self._handlers.items()):
                kept = [e for e in lst if e.plugin_id != plugin_id]
                n += len(lst) - len(kept)
                if kept:
                    self._handlers[event] = kept
                else:
                    del self._handlers[event]
        return n

    def list_events(self) -> list[str]:
        return sorted(self._handlers.keys())

    def handlers_for(self, event: str) -> list[HookEntry]:
        return list(self._handlers.get(event, []))

    def fire(self, event: str, ctx: "PluginContext", /, **kwargs: Any) -> list:
        """Invoke every handler subscribed to ``event``.

        Each handler is wrapped in :func:`isolate` so an exception in
        one plugin doesn't stop the others. Returns the list of
        :class:`PluginRuntimeError` records collected (empty on full
        success).
        """
        from .errors import PluginRuntimeError  # local import: avoid cycles
        errors: list[PluginRuntimeError] = []
        for entry in self._handlers.get(event, []):
            with isolate(entry.plugin_id, f"hook:{event}", capture=errors):
                entry.fn(ctx, **kwargs)
        return errors

    def __len__(self) -> int:
        return sum(len(v) for v in self._handlers.values())


class InspectFieldRegistry:
    """Key → InspectFieldEntry, for headless / debug inspect outputs."""

    def __init__(self) -> None:
        self._entries: dict[str, InspectFieldEntry] = {}
        self._lock = threading.RLock()

    def register(self, entry: InspectFieldEntry) -> None:
        with self._lock:
            existing = self._entries.get(entry.key)
            if existing is not None:
                if existing.fn is entry.fn and existing.plugin_id == entry.plugin_id:
                    return
                raise DuplicateKindError(
                    kind=entry.key,
                    existing_plugin=existing.plugin_id,
                    new_plugin=entry.plugin_id,
                    category="inspect_field",
                )
            self._entries[entry.key] = entry

    def unregister_plugin(self, plugin_id: str) -> list[str]:
        out: list[str] = []
        with self._lock:
            for key, entry in list(self._entries.items()):
                if entry.plugin_id == plugin_id:
                    del self._entries[key]
                    out.append(key)
        return out

    def list_keys(self) -> list[str]:
        return sorted(self._entries.keys())

    def collect(self, state: "GameState") -> dict[str, Any]:
        """Run every registered producer, returning {key: value}."""
        from .errors import PluginRuntimeError, isolate  # local
        out: dict[str, Any] = {}
        for key in sorted(self._entries):
            entry = self._entries[key]
            with isolate(entry.plugin_id, f"inspect_field:{key}"):
                out[key] = entry.fn(state)
        return out

    def __len__(self) -> int:
        return len(self._entries)


class _NamedClassRegistry:
    """Generic name → entry-with-class registry (used by Widget/Scene/Brain)."""

    _category: str

    def __init__(self, *, category: str) -> None:
        self._category = category
        self._entries: dict[str, Any] = {}
        self._lock = threading.RLock()

    def register(self, entry: Any) -> None:
        name = entry.name if hasattr(entry, "name") else entry.scene_id
        with self._lock:
            existing = self._entries.get(name)
            if existing is not None:
                existing_cls = (getattr(existing, "cls", None))
                if existing_cls is entry.cls and existing.plugin_id == entry.plugin_id:
                    return
                raise DuplicateKindError(
                    kind=name,
                    existing_plugin=existing.plugin_id,
                    new_plugin=entry.plugin_id,
                    category=self._category,
                )
            self._entries[name] = entry

    def unregister_plugin(self, plugin_id: str) -> list[str]:
        out: list[str] = []
        with self._lock:
            for k, entry in list(self._entries.items()):
                if entry.plugin_id == plugin_id:
                    del self._entries[k]
                    out.append(k)
        return out

    def get(self, name: str) -> Any | None:
        return self._entries.get(name)

    def has(self, name: str) -> bool:
        return name in self._entries

    def list_names(self) -> list[str]:
        return sorted(self._entries.keys())

    def list_entries(self) -> list[Any]:
        return [self._entries[k] for k in sorted(self._entries.keys())]

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, item: object) -> bool:
        return isinstance(item, str) and item in self._entries


class WidgetRegistry(_NamedClassRegistry):
    """Name → WidgetEntry."""

    def __init__(self) -> None:
        super().__init__(category="widget")

    def spawn(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """Instantiate the registered widget class. Raises if unknown."""
        entry = self._entries.get(name)
        if entry is None:
            raise UnknownKindError(name, category="widget")
        return entry.cls(*args, **kwargs)


class SceneRegistry(_NamedClassRegistry):
    """scene_id → SceneEntry."""

    def __init__(self) -> None:
        super().__init__(category="scene")

    def spawn(self, scene_id: str, ctx: Any, **kwargs: Any) -> Any:
        entry = self._entries.get(scene_id)
        if entry is None:
            raise UnknownKindError(scene_id, category="scene")
        inst = entry.cls(ctx, **kwargs)
        if hasattr(inst, "is_overlay") and getattr(inst, "is_overlay") is None:
            inst.is_overlay = entry.overlay
        return inst


class BrainRegistry(_NamedClassRegistry):
    """name → BrainEntry. ``spawn`` returns a brain instance."""

    def __init__(self) -> None:
        super().__init__(category="brain")

    def spawn(self, name: str, *args: Any, **kwargs: Any) -> Any:
        entry = self._entries.get(name)
        if entry is None:
            raise UnknownKindError(name, category="brain")
        return entry.cls(*args, **kwargs)


class PortraitBackendRegistry(_NamedClassRegistry):
    """name → PortraitBackendEntry. ``spawn`` returns a per-slot instance."""

    def __init__(self) -> None:
        super().__init__(category="portrait_backend")

    def spawn(self, name: str, *args: Any, **kwargs: Any) -> Any:
        entry = self._entries.get(name)
        if entry is None:
            raise UnknownKindError(name, category="portrait_backend")
        return entry.cls(*args, **kwargs)


class AmbientBackendRegistry(_NamedClassRegistry):
    """name → AmbientBackendEntry. ``spawn`` returns an overlay instance."""

    def __init__(self) -> None:
        super().__init__(category="ambient_backend")

    def spawn(self, name: str, *args: Any, **kwargs: Any) -> Any:
        entry = self._entries.get(name)
        if entry is None:
            raise UnknownKindError(name, category="ambient_backend")
        return entry.cls(*args, **kwargs)


class DialogueOpRegistry:
    """name → DialogueOpEntry. Used by parsed ``[[name:arg]]`` directives."""

    def __init__(self) -> None:
        self._entries: dict[str, DialogueOpEntry] = {}
        self._lock = threading.RLock()

    def register(self, entry: DialogueOpEntry) -> None:
        with self._lock:
            existing = self._entries.get(entry.name)
            if existing is not None:
                if existing.fn is entry.fn and existing.plugin_id == entry.plugin_id:
                    return
                raise DuplicateKindError(
                    kind=entry.name,
                    existing_plugin=existing.plugin_id,
                    new_plugin=entry.plugin_id,
                    category="dialogue_op",
                )
            self._entries[entry.name] = entry

    def unregister_plugin(self, plugin_id: str) -> list[str]:
        out: list[str] = []
        with self._lock:
            for k, entry in list(self._entries.items()):
                if entry.plugin_id == plugin_id:
                    del self._entries[k]
                    out.append(k)
        return out

    def get(self, name: str) -> DialogueOpEntry | None:
        return self._entries.get(name)

    def list_names(self) -> list[str]:
        return sorted(self._entries.keys())

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, item: object) -> bool:
        return isinstance(item, str) and item in self._entries


# ----------------------------------------------------------------------
# Module-level singletons (the "global registry")
#
# Most code reaches into these directly. Tests can save and restore them
# with snapshot()/restore() to keep test isolation tight.


EFFECT_REGISTRY = EffectRegistry()
CONDITION_REGISTRY = ConditionRegistry()
HOOK_REGISTRY = HookRegistry()
INSPECT_FIELD_REGISTRY = InspectFieldRegistry()
WIDGET_REGISTRY = WidgetRegistry()
SCENE_REGISTRY = SceneRegistry()
BRAIN_REGISTRY = BrainRegistry()
DIALOGUE_OP_REGISTRY = DialogueOpRegistry()
PORTRAIT_BACKEND_REGISTRY = PortraitBackendRegistry()
AMBIENT_BACKEND_REGISTRY = AmbientBackendRegistry()


# ----------------------------------------------------------------------
# Context var: the "currently loading" plugin id

_current_plugin_id: ContextVar[str] = ContextVar(
    "_wgg_plugin_loading", default="builtin"
)


@contextmanager
def loading(plugin_id: str) -> Iterator[None]:
    """Context manager used by :class:`PluginManager` while it imports a plugin.

    Any decorator call inside the ``with`` block picks up ``plugin_id``
    as the owning plugin, so plugin authors don't need to repeat their
    own id at every decorator site.
    """
    token = _current_plugin_id.set(plugin_id)
    try:
        yield
    finally:
        _current_plugin_id.reset(token)


def current_plugin_id() -> str:
    """Return the plugin id currently being loaded (or "builtin")."""
    return _current_plugin_id.get()


# ----------------------------------------------------------------------
# Decorator factories — the public surface plugin authors use


def effect(kind: str,
           *, plugin_id: str | None = None,
           description: str = "",
           signature: dict[str, Any] | None = None,
           args: type["BaseModel"] | None = None) -> Callable:
    """Decorator: register an effect handler.

    Usage in plugin code::

        from world_gal_game.plugins import effect

        @effect("reset_step_counter",
                description="Reset the step counter to zero.",
                signature={"target": "<unused>", "value": "<unused>"})
        def handle_reset(state, eff):
            state.meta["__step_counter__"] = 0
            return {"kind": "reset_step_counter", "ok": True}

    ``args`` optionally binds a pydantic model describing the
    (target/value/stat) arguments for this kind. It is used for JSON-Schema
    export in the capability manifest and for build/lint-time validation
    (``wgg validate``); it changes neither the ``(state, eff)`` handler
    signature nor the tolerant runtime dispatch in ``GameState.apply``.
    """
    def deco(fn: Callable) -> Callable:
        pid = plugin_id or current_plugin_id()
        EFFECT_REGISTRY.register(EffectEntry(
            kind=kind, fn=fn, plugin_id=pid,
            description=description, signature=signature or {},
            args_model=args,
        ))
        # Stamp the function so tests / introspection can recognise it.
        fn.__wgg_effect_kind__ = kind  # type: ignore[attr-defined]
        fn.__wgg_plugin_id__ = pid     # type: ignore[attr-defined]
        return fn
    return deco


def condition(kind: str,
              *, plugin_id: str | None = None,
              description: str = "",
              signature: dict[str, Any] | None = None,
              args: type["BaseModel"] | None = None) -> Callable:
    """Decorator: register a condition handler.

    Handler signature: ``(state: GameState, cond: Condition) -> bool``.

    ``args`` optionally binds a pydantic arg model (see :func:`effect`); it is
    used for schema export and build/lint-time validation only.
    """
    def deco(fn: Callable) -> Callable:
        pid = plugin_id or current_plugin_id()
        CONDITION_REGISTRY.register(ConditionEntry(
            kind=kind, fn=fn, plugin_id=pid,
            description=description, signature=signature or {},
            args_model=args,
        ))
        fn.__wgg_condition_kind__ = kind  # type: ignore[attr-defined]
        fn.__wgg_plugin_id__ = pid        # type: ignore[attr-defined]
        return fn
    return deco


def hook(event: str,
         *, plugin_id: str | None = None,
         description: str = "",
         priority: int = 100) -> Callable:
    """Decorator: subscribe to an engine lifecycle event.

    Handler signature: ``(ctx: PluginContext, **payload) -> None``.

    Events are :class:`~world_gal_game.plugins.context.HookEvent`
    string constants. Multiple plugins may hook the same event; they
    run in ``priority`` order (lower first) and then registration order
    within the same priority.
    """
    def deco(fn: Callable) -> Callable:
        pid = plugin_id or current_plugin_id()
        HOOK_REGISTRY.register(HookEntry(
            event=event, fn=fn, plugin_id=pid,
            description=description, priority=priority,
        ))
        fn.__wgg_hook_event__ = event    # type: ignore[attr-defined]
        fn.__wgg_plugin_id__ = pid       # type: ignore[attr-defined]
        return fn
    return deco


def inspect_field(key: str,
                  *, plugin_id: str | None = None,
                  description: str = "") -> Callable:
    """Decorator: register a producer for an extra inspect() output key.

    Producer signature: ``(state: GameState) -> Any``. Whatever it
    returns is serialised under ``snapshot["plugin_fields"][key]``.
    """
    def deco(fn: Callable) -> Callable:
        pid = plugin_id or current_plugin_id()
        INSPECT_FIELD_REGISTRY.register(InspectFieldEntry(
            key=key, fn=fn, plugin_id=pid, description=description,
        ))
        fn.__wgg_inspect_key__ = key   # type: ignore[attr-defined]
        fn.__wgg_plugin_id__ = pid     # type: ignore[attr-defined]
        return fn
    return deco


def widget(name: str,
           *, plugin_id: str | None = None,
           description: str = "",
           signature: dict[str, Any] | None = None) -> Callable:
    """Class decorator: register a pygame widget for scene use.

    Usage::

        from world_gal_game.plugins import widget
        from world_gal_game.ui.widgets.base import Widget

        @widget("score_badge", description="HUD that shows the player's score.")
        class ScoreBadge(Widget):
            def __init__(self, rect, **kw):
                super().__init__(rect)
                ...

    Scenes look up plugin widgets via ``WIDGET_REGISTRY.spawn(name, ...)``.
    """
    def deco(cls: type) -> type:
        pid = plugin_id or current_plugin_id()
        WIDGET_REGISTRY.register(WidgetEntry(
            name=name, cls=cls, plugin_id=pid,
            description=description, signature=signature or {},
        ))
        cls.__wgg_widget_name__ = name        # type: ignore[attr-defined]
        cls.__wgg_plugin_id__ = pid           # type: ignore[attr-defined]
        return cls
    return deco


def scene(scene_id: str,
          *, plugin_id: str | None = None,
          description: str = "",
          overlay: bool = False) -> Callable:
    """Class decorator: register a custom Scene class.

    Plugins can then push the scene by id via
    ``SCENE_REGISTRY.spawn(scene_id, ctx)`` — useful for minigames,
    custom inventories, NPC-specific UIs etc.
    """
    def deco(cls: type) -> type:
        pid = plugin_id or current_plugin_id()
        SCENE_REGISTRY.register(SceneEntry(
            scene_id=scene_id, cls=cls, plugin_id=pid,
            description=description, overlay=overlay,
        ))
        cls.__wgg_scene_id__ = scene_id       # type: ignore[attr-defined]
        cls.__wgg_plugin_id__ = pid           # type: ignore[attr-defined]
        return cls
    return deco


def brain(name: str,
          *, plugin_id: str | None = None,
          description: str = "") -> Callable:
    """Class decorator: register a custom LLMBrain implementation.

    The engine picks one of the registered brains by name (from
    ``meta.yaml.brain`` or ``EngineConfig.brain``); absent → EchoBrain.
    """
    def deco(cls: type) -> type:
        pid = plugin_id or current_plugin_id()
        BRAIN_REGISTRY.register(BrainEntry(
            name=name, cls=cls, plugin_id=pid, description=description,
        ))
        cls.__wgg_brain_name__ = name         # type: ignore[attr-defined]
        cls.__wgg_plugin_id__ = pid           # type: ignore[attr-defined]
        return cls
    return deco


def portrait_backend(name: str,
                     *, plugin_id: str | None = None,
                     description: str = "") -> Callable:
    """Class decorator: register a portrait render backend.

    The class becomes the per-slot renderer the dialogue scene instantiates as
    ``cls(spec, assets, fallback_size)``; it should expose ``update(dt)`` /
    ``draw(surface, rect, *, flip=False, alpha=255)`` / ``base_surface()`` (see
    :mod:`world_gal_game.ui.portrait_backend`). A ``PortraitSpec.backend`` field
    naming this backend routes that portrait's resting animation through it;
    ``"static"`` (the default) bypasses backends entirely. Unknown names fall
    back to the static blit, so a missing plugin never breaks rendering.

    Usage::

        from world_gal_game.plugins import portrait_backend
        from world_gal_game.ui.portrait_backend import blit_fitted

        @portrait_backend("breath", description="Procedural idle breathing.")
        class BreathBackend:
            def __init__(self, spec, assets, fallback_size): ...
            def update(self, dt): ...
            def draw(self, surface, rect, *, flip=False, alpha=255): ...
            def base_surface(self): ...
    """
    def deco(cls: type) -> type:
        pid = plugin_id or current_plugin_id()
        PORTRAIT_BACKEND_REGISTRY.register(PortraitBackendEntry(
            name=name, cls=cls, plugin_id=pid, description=description,
        ))
        cls.__wgg_portrait_backend__ = name   # type: ignore[attr-defined]
        cls.__wgg_plugin_id__ = pid           # type: ignore[attr-defined]
        return cls
    return deco


def ambient_backend(name: str,
                    *, plugin_id: str | None = None,
                    description: str = "") -> Callable:
    """Class decorator: register an ambient / weather overlay backend.

    The class becomes the full-screen atmospheric overlay the dialogue scene
    instantiates as ``cls(params, screen_size)``; it should expose
    ``update(dt)`` and ``draw(surface)`` (see
    :mod:`world_gal_game.ui.ambient_backend`). A ``set_weather`` effect naming
    this backend routes the scene's ambient layer through it; ``clear_weather``
    (or an unknown name) removes it, so a missing plugin never breaks rendering.

    Usage::

        from world_gal_game.plugins import ambient_backend

        @ambient_backend("rain", description="Falling rain streaks.")
        class RainBackend:
            def __init__(self, params, screen_size): ...
            def update(self, dt): ...
            def draw(self, surface): ...
    """
    def deco(cls: type) -> type:
        pid = plugin_id or current_plugin_id()
        AMBIENT_BACKEND_REGISTRY.register(AmbientBackendEntry(
            name=name, cls=cls, plugin_id=pid, description=description,
        ))
        cls.__wgg_ambient_backend__ = name    # type: ignore[attr-defined]
        cls.__wgg_plugin_id__ = pid           # type: ignore[attr-defined]
        return cls
    return deco


def dialogue_op(name: str,
                *, plugin_id: str | None = None,
                description: str = "") -> Callable:
    """Function decorator: register an inline ``[[name:arg]]`` directive.

    The DialogueEngine parses any ``[[name:arg]]`` token out of the line
    text, invokes the registered handler with ``(state, arg)`` (which
    can mutate state, schedule effects, fire toasts, etc), and removes
    the token from the rendered text. Handlers return ``None`` or a
    replacement substring.
    """
    def deco(fn: Callable) -> Callable:
        pid = plugin_id or current_plugin_id()
        DIALOGUE_OP_REGISTRY.register(DialogueOpEntry(
            name=name, fn=fn, plugin_id=pid, description=description,
        ))
        fn.__wgg_dialogue_op__ = name         # type: ignore[attr-defined]
        fn.__wgg_plugin_id__ = pid            # type: ignore[attr-defined]
        return fn
    return deco


def save_migration(from_version: str, to_version: str,
                   *, pack_id: str = "",
                   plugin_id: str | None = None,
                   description: str = "") -> Callable:
    """Function decorator: register a pack-level save migration.

    The migration bridges one pack content version to the next. The handler
    signature is ``(data: dict) -> dict``: it receives the loaded save dict
    (with ``_pack_id`` / ``_pack_format_version`` / etc. still present) and
    returns the transformed dict (mutating in place + returning it is fine).
    The pack-load gate (:func:`world_gal_game.core.pack_migration.check_and_migrate_pack`)
    chains these in order from the save's version up to the pack's current
    version, so each step only needs to handle one hop.

    **Pure-additive schema changes need NO migration.** When a new field on
    Line / Scene / PortraitSpec etc. is *optional*, pydantic fills its
    default while reconstructing ``GameState(**data)``, so an old save still
    loads untouched. Write a migration only for *breaking* changes — renames,
    restructures, removals.

    Usage in a pack's ``plugins/<id>_migrations/migration_0_1_0_2.py``::

        from world_gal_game.plugins import save_migration

        @save_migration("0.1", "0.2", description="rename hp -> health")
        def migrate(data: dict) -> dict:
            if "hp" in data:
                data["health"] = data.pop("hp")
            data["_pack_format_version"] = "0.2"
            return data

    ``pack_id`` scopes the migration to one pack (empty applies to any pack).
    Registered into the module singleton
    :data:`world_gal_game.core.pack_migration.PACK_MIGRATIONS`.
    """
    # Local import keeps this module import-light (registry.py has no hard
    # dependency on core at import time; the migration registry lives in core).
    from ..core.pack_migration import PACK_MIGRATIONS, PackMigrationEntry

    def deco(fn: Callable) -> Callable:
        pid = plugin_id or current_plugin_id()
        # current_plugin_id() defaults to "builtin" outside a plugin load; for
        # save migrations a friendlier owner default is "pack".
        if pid == "builtin":
            pid = "pack"
        PACK_MIGRATIONS.register(PackMigrationEntry(
            from_version=from_version, to_version=to_version, fn=fn,
            pack_id=pack_id, plugin_id=pid, description=description,
        ))
        fn.__wgg_save_migration__ = (from_version, to_version)  # type: ignore[attr-defined]
        fn.__wgg_plugin_id__ = pid                              # type: ignore[attr-defined]
        return fn
    return deco


# ----------------------------------------------------------------------
# Snapshot / restore — primarily for test isolation


@dataclass
class _RegistrySnapshot:
    effects: dict[str, EffectEntry]
    conditions: dict[str, ConditionEntry]
    hooks: dict[str, list[HookEntry]]
    inspect_fields: dict[str, InspectFieldEntry]
    widgets: dict[str, WidgetEntry]
    scenes: dict[str, SceneEntry]
    brains: dict[str, BrainEntry]
    dialogue_ops: dict[str, DialogueOpEntry]
    portrait_backends: dict[str, PortraitBackendEntry]
    ambient_backends: dict[str, AmbientBackendEntry]


def snapshot() -> _RegistrySnapshot:
    """Shallow-copy every registry. Used by tests to isolate global state."""
    return _RegistrySnapshot(
        effects=dict(EFFECT_REGISTRY._entries),
        conditions=dict(CONDITION_REGISTRY._entries),
        hooks={k: list(v) for k, v in HOOK_REGISTRY._handlers.items()},
        inspect_fields=dict(INSPECT_FIELD_REGISTRY._entries),
        widgets=dict(WIDGET_REGISTRY._entries),
        scenes=dict(SCENE_REGISTRY._entries),
        brains=dict(BRAIN_REGISTRY._entries),
        dialogue_ops=dict(DIALOGUE_OP_REGISTRY._entries),
        portrait_backends=dict(PORTRAIT_BACKEND_REGISTRY._entries),
        ambient_backends=dict(AMBIENT_BACKEND_REGISTRY._entries),
    )


def restore(snap: _RegistrySnapshot) -> None:
    """Reset every registry to the state captured in *snap*."""
    EFFECT_REGISTRY._entries.clear()
    EFFECT_REGISTRY._entries.update(snap.effects)
    CONDITION_REGISTRY._entries.clear()
    CONDITION_REGISTRY._entries.update(snap.conditions)
    HOOK_REGISTRY._handlers.clear()
    HOOK_REGISTRY._handlers.update({k: list(v) for k, v in snap.hooks.items()})
    INSPECT_FIELD_REGISTRY._entries.clear()
    INSPECT_FIELD_REGISTRY._entries.update(snap.inspect_fields)
    WIDGET_REGISTRY._entries.clear()
    WIDGET_REGISTRY._entries.update(snap.widgets)
    SCENE_REGISTRY._entries.clear()
    SCENE_REGISTRY._entries.update(snap.scenes)
    BRAIN_REGISTRY._entries.clear()
    BRAIN_REGISTRY._entries.update(snap.brains)
    DIALOGUE_OP_REGISTRY._entries.clear()
    DIALOGUE_OP_REGISTRY._entries.update(snap.dialogue_ops)
    PORTRAIT_BACKEND_REGISTRY._entries.clear()
    PORTRAIT_BACKEND_REGISTRY._entries.update(snap.portrait_backends)
    AMBIENT_BACKEND_REGISTRY._entries.clear()
    AMBIENT_BACKEND_REGISTRY._entries.update(snap.ambient_backends)
