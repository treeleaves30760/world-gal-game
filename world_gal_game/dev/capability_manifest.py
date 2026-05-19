"""Capability Manifest — a machine-readable snapshot of engine extensions.

This is the answer to the question: *"what kinds / hooks / inspect
fields does this engine currently support?"* — asked by:

- AI tools generating pack YAML, who need to know which ``kind`` strings
  are legal.
- :class:`world_gal_game.dev.pack_editor.PackEditor`, which uses the
  manifest to validate fields before writing.
- Plugin authors checking for name collisions before publishing.
- The CLI subcommand ``wgg inspect-pack --capabilities``.

The manifest is **live** — calling :func:`build_manifest` reflects the
state of the global registries at that moment, including any
plugin-supplied kinds that have loaded into the current process.

Output shape (JSON-friendly)::

    {
      "engine_version": "0.1.0",
      "generated_at": "2026-05-19T...",
      "effects": [
        {"kind": "affection", "plugin_id": "builtin",
         "description": "...", "signature": {...}},
        ...
      ],
      "conditions": [...],
      "hooks": {
        "events": ["pack.before_load", ...],
        "subscriptions": [
          {"event": "effect.after_apply", "plugin_id": "step_counter",
           "priority": 100, "description": "..."},
          ...
        ],
      },
      "inspect_fields": [
        {"key": "step_counter", "plugin_id": "step_counter", "description": "..."},
        ...
      ],
      "plugins": {
        "loaded": [
          {"id": "step_counter", "version": "0.1.0", "source": "pack", ...},
          ...
        ],
        "failed": [...],
      },
    }

Use :func:`manifest_json` for a pretty-printed string suitable for piping
into a file or LLM context.
"""
from __future__ import annotations

import datetime as _dt
import json
from typing import Any, TYPE_CHECKING

from .. import __version__ as engine_version
from ..plugins import (
    EFFECT_REGISTRY, CONDITION_REGISTRY,
    HOOK_REGISTRY, INSPECT_FIELD_REGISTRY,
    WIDGET_REGISTRY, SCENE_REGISTRY, BRAIN_REGISTRY, DIALOGUE_OP_REGISTRY,
)
from ..plugins.context import HookEvent

if TYPE_CHECKING:
    from ..plugins.manager import PluginManager


# ----------------------------------------------------------------------
# Builders


def _serialize_kind_registry(reg: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for kind in reg.list_kinds():
        entry = reg.get(kind)
        out.append({
            "kind": entry.kind,
            "plugin_id": entry.plugin_id,
            "description": entry.description,
            "signature": dict(entry.signature),
        })
    return out


def _serialize_hooks() -> dict[str, Any]:
    subscriptions: list[dict[str, Any]] = []
    for event in HOOK_REGISTRY.list_events():
        for entry in HOOK_REGISTRY.handlers_for(event):
            subscriptions.append({
                "event": event,
                "plugin_id": entry.plugin_id,
                "priority": entry.priority,
                "description": entry.description,
            })
    return {
        "events": HookEvent.all(),
        "active_events": HOOK_REGISTRY.list_events(),
        "subscriptions": subscriptions,
    }


def _serialize_inspect_fields() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in INSPECT_FIELD_REGISTRY.list_keys():
        entry = INSPECT_FIELD_REGISTRY._entries[key]
        out.append({
            "key": entry.key,
            "plugin_id": entry.plugin_id,
            "description": entry.description,
        })
    return out


def _serialize_plugins(manager: "PluginManager | None") -> dict[str, Any]:
    if manager is None:
        return {"loaded": [], "failed": [], "manager_available": False}
    summary = manager.summary()
    summary["manager_available"] = True
    return summary


# ----------------------------------------------------------------------
# Public API


def _serialize_named_class_registry(reg: Any, *, extra_keys: tuple[str, ...] = ()
                                    ) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name in reg.list_names():
        entry = reg.get(name)
        row = {
            "name": name,
            "class": f"{entry.cls.__module__}.{entry.cls.__qualname__}",
            "plugin_id": entry.plugin_id,
            "description": entry.description,
        }
        for k in extra_keys:
            row[k] = getattr(entry, k, None)
        out.append(row)
    return out


def _serialize_dialogue_ops() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name in DIALOGUE_OP_REGISTRY.list_names():
        entry = DIALOGUE_OP_REGISTRY.get(name)
        out.append({
            "name": entry.name,
            "plugin_id": entry.plugin_id,
            "description": entry.description,
        })
    return out


def build_manifest(*, manager: "PluginManager | None" = None) -> dict[str, Any]:
    """Snapshot every extension currently active.

    Pass ``manager`` (e.g. ``state.meta["__plugin_manager__"]`` from a
    loaded pack) to include per-plugin metadata. With ``manager=None``
    only the registry contents are reported (still sufficient for
    "what kinds exist?" questions).
    """
    return {
        "engine_version": engine_version,
        "generated_at": _dt.datetime.now(_dt.UTC).isoformat(),
        "effects": _serialize_kind_registry(EFFECT_REGISTRY),
        "conditions": _serialize_kind_registry(CONDITION_REGISTRY),
        "hooks": _serialize_hooks(),
        "inspect_fields": _serialize_inspect_fields(),
        "widgets": _serialize_named_class_registry(WIDGET_REGISTRY),
        "scenes": _serialize_named_class_registry(SCENE_REGISTRY,
                                                   extra_keys=("overlay",)),
        "brains": _serialize_named_class_registry(BRAIN_REGISTRY),
        "dialogue_ops": _serialize_dialogue_ops(),
        "plugins": _serialize_plugins(manager),
    }


def manifest_json(*, indent: int = 2,
                  manager: "PluginManager | None" = None) -> str:
    """Pretty-printed JSON for piping into files or LLM prompts."""
    return json.dumps(
        build_manifest(manager=manager),
        ensure_ascii=False, indent=indent,
    )


# ----------------------------------------------------------------------
# Compact queries — handy for AI tools that only want a quick answer


def all_effect_kinds() -> list[str]:
    """Every effect kind currently registered, sorted."""
    return EFFECT_REGISTRY.list_kinds()


def all_condition_kinds() -> list[str]:
    """Every condition kind currently registered, sorted."""
    return CONDITION_REGISTRY.list_kinds()


def all_hook_events() -> list[str]:
    """Every defined hook event (whether subscribed or not)."""
    return HookEvent.all()


def all_inspect_field_keys() -> list[str]:
    return INSPECT_FIELD_REGISTRY.list_keys()


def all_widget_names() -> list[str]:
    return WIDGET_REGISTRY.list_names()


def all_scene_ids() -> list[str]:
    return SCENE_REGISTRY.list_names()


def all_brain_names() -> list[str]:
    return BRAIN_REGISTRY.list_names()


def all_dialogue_op_names() -> list[str]:
    return DIALOGUE_OP_REGISTRY.list_names()


def find_effect(kind: str) -> dict[str, Any] | None:
    """One effect entry by kind, or ``None`` if unknown."""
    entry = EFFECT_REGISTRY.get(kind)
    if entry is None:
        return None
    return {
        "kind": entry.kind,
        "plugin_id": entry.plugin_id,
        "description": entry.description,
        "signature": dict(entry.signature),
    }


def find_condition(kind: str) -> dict[str, Any] | None:
    entry = CONDITION_REGISTRY.get(kind)
    if entry is None:
        return None
    return {
        "kind": entry.kind,
        "plugin_id": entry.plugin_id,
        "description": entry.description,
        "signature": dict(entry.signature),
    }


def summary_table() -> str:
    """Compact human-readable table — for CLI / dev-loop debugging."""
    lines = [
        f"World Gal-Game engine {engine_version} — capability snapshot",
        "",
        f"Effects     ({len(EFFECT_REGISTRY):>3} kinds): "
        + ", ".join(EFFECT_REGISTRY.list_kinds()),
        f"Conditions  ({len(CONDITION_REGISTRY):>3} kinds): "
        + ", ".join(CONDITION_REGISTRY.list_kinds()),
        f"Hook events ({len(HookEvent.all()):>3} defined, "
        f"{len(HOOK_REGISTRY.list_events()):>2} subscribed): "
        + ", ".join(HookEvent.all()),
    ]
    if INSPECT_FIELD_REGISTRY.list_keys():
        lines.append(
            f"Inspect fields: "
            + ", ".join(INSPECT_FIELD_REGISTRY.list_keys())
        )
    # Plugin grouping
    eff_by_plugin = EFFECT_REGISTRY.kinds_by_plugin()
    if len(eff_by_plugin) > 1:
        lines.append("")
        lines.append("Effects by plugin:")
        for pid, kinds in sorted(eff_by_plugin.items()):
            lines.append(f"  {pid}: {', '.join(kinds)}")
    return "\n".join(lines)
