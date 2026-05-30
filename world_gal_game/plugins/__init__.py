"""World Gal-Game plugin system — public API.

Plugins extend the engine without touching ``core/`` source. A plugin
is a directory shipping a ``plugin.yaml`` manifest next to a Python
entry module that uses the decorators exported here::

    # games/<pack>/plugins/step_counter/plugin.py
    from world_gal_game.plugins import effect, condition, hook, HookEvent

    @effect("reset_step_counter", description="Reset to 0.")
    def reset(state, eff):
        state.meta["__plugin:step_counter__"] = {"count": 0}
        return {"kind": "reset_step_counter", "ok": True}

    @hook(HookEvent.EFFECT_AFTER_APPLY)
    def count_steps(ctx, eff=None, result=None):
        if eff and eff.kind == "move_to":
            slot = ctx.get_plugin_state("step_counter")
            slot["count"] = slot.get("count", 0) + 1

Three big pieces this module exposes:

1. **Decorators**: :func:`effect`, :func:`condition`, :func:`hook`,
   :func:`inspect_field` — register handlers into the global registry.
2. **Models / runtime**: :class:`PluginManifest`, :class:`PluginManager`,
   :class:`PluginContext`, :class:`HookEvent`.
3. **Errors**: :class:`PluginError` and the typed subclasses
   (:class:`DuplicateKindError`, :class:`ManifestError`, etc).

Import side effect: importing this package eagerly imports
:mod:`builtin_effects` and :mod:`builtin_conditions`, which populates
the global registries with the builtin kinds the engine ships.
Without that bootstrap step :meth:`GameState.apply` would have nothing
to dispatch to.
"""
from __future__ import annotations

# Re-export decorators + low-level registries first, since the builtin
# modules below depend on them.
from .registry import (
    effect, condition, hook, inspect_field,
    widget, scene, brain, dialogue_op, portrait_backend, ambient_backend,
    save_migration,
    EFFECT_REGISTRY, CONDITION_REGISTRY, HOOK_REGISTRY, INSPECT_FIELD_REGISTRY,
    WIDGET_REGISTRY, SCENE_REGISTRY, BRAIN_REGISTRY, DIALOGUE_OP_REGISTRY,
    PORTRAIT_BACKEND_REGISTRY, AMBIENT_BACKEND_REGISTRY,
    EffectEntry, ConditionEntry, HookEntry, InspectFieldEntry,
    WidgetEntry, SceneEntry, BrainEntry, DialogueOpEntry, PortraitBackendEntry,
    AmbientBackendEntry,
    EffectRegistry, ConditionRegistry, HookRegistry, InspectFieldRegistry,
    WidgetRegistry, SceneRegistry, BrainRegistry, DialogueOpRegistry,
    PortraitBackendRegistry, AmbientBackendRegistry,
    EffectHandler, ConditionHandler, HookHandler, InspectFieldProducer,
    loading, current_plugin_id, snapshot, restore,
)
from .errors import (
    PluginError, ManifestError, DuplicateKindError, UnknownKindError,
    DependencyError, IncompatibleEngineError, PluginLoadError,
    PluginRuntimeError, isolate,
)
from .manifest import PluginManifest, SideEffectFlags, ExtensionDeclaration, Extends
from .context import PluginContext, HookEvent

# Trigger registration of the builtin effect/condition handlers.
# IMPORTANT: this must come AFTER the registry/errors imports above, but
# may run BEFORE ``manager`` is imported — the builtin modules only
# depend on the registry, not on the manager. Splitting like this avoids
# a circular import: manager imports manifest which imports ... etc.
from . import builtin_effects  # noqa: F401  (side effect: register handlers)
from . import builtin_conditions  # noqa: F401

# PluginManager is exported after the bootstrap above so anyone calling
# ``from world_gal_game.plugins import PluginManager`` already has all
# builtin handlers in place.
from .manager import PluginManager  # noqa: E402


# ----------------------------------------------------------------------
# Hook-firing helpers
#
# Most lifecycle hooks fire from places that already hold a GameState
# reference (DialogueEngine, SaveManager, SceneManager-via-Scene, effect
# handlers). This helper provides a uniform, exception-safe way to fire
# without forcing every call site to look up the manager itself.

import logging as _logging  # noqa: E402
_hook_log = _logging.getLogger("world_gal_game.plugins.fire")


def fire_event(state, event: str, /, **kwargs):
    """Fire a hook event using the PluginManager parked on ``state``.

    No-op when:
    - ``state`` is None
    - ``state.meta["__plugin_manager__"]`` is absent (e.g. unit tests
      constructing bare ``GameState()``; we want zero side effects there)

    Hook handler exceptions never propagate — they're already isolated
    inside :meth:`PluginManager.fire_hook`. A manager-side error (e.g.
    manager itself blew up) is logged here and swallowed.
    """
    if state is None:
        return
    manager = state.meta.get("__plugin_manager__") if hasattr(state, "meta") else None
    if manager is None:
        return
    try:
        manager.fire_hook(event, **kwargs)
    except Exception as exc:
        _hook_log.exception("hook fire %s failed: %s", event, exc)


__all__ = [
    # decorators
    "effect", "condition", "hook", "inspect_field",
    "widget", "scene", "brain", "dialogue_op", "portrait_backend",
    "ambient_backend", "save_migration",
    # registries (singletons)
    "EFFECT_REGISTRY", "CONDITION_REGISTRY", "HOOK_REGISTRY",
    "INSPECT_FIELD_REGISTRY",
    "WIDGET_REGISTRY", "SCENE_REGISTRY", "BRAIN_REGISTRY",
    "DIALOGUE_OP_REGISTRY", "PORTRAIT_BACKEND_REGISTRY",
    "AMBIENT_BACKEND_REGISTRY",
    # registry types + entry types
    "EffectRegistry", "ConditionRegistry", "HookRegistry",
    "InspectFieldRegistry",
    "WidgetRegistry", "SceneRegistry", "BrainRegistry",
    "DialogueOpRegistry", "PortraitBackendRegistry", "AmbientBackendRegistry",
    "EffectEntry", "ConditionEntry", "HookEntry", "InspectFieldEntry",
    "WidgetEntry", "SceneEntry", "BrainEntry", "DialogueOpEntry",
    "PortraitBackendEntry", "AmbientBackendEntry",
    # handler protocol types
    "EffectHandler", "ConditionHandler", "HookHandler",
    "InspectFieldProducer",
    # context vars + helpers
    "loading", "current_plugin_id", "snapshot", "restore",
    # context + events
    "PluginContext", "HookEvent",
    # manifest
    "PluginManifest", "SideEffectFlags", "ExtensionDeclaration", "Extends",
    # manager
    "PluginManager",
    # errors
    "PluginError", "ManifestError", "DuplicateKindError", "UnknownKindError",
    "DependencyError", "IncompatibleEngineError", "PluginLoadError",
    "PluginRuntimeError", "isolate",
    # helpers
    "fire_event",
]
