"""State snapshot / restore / diff for headless branch exploration.

Lets an agent checkpoint the game, try a branch (a choice, an effect), inspect
the outcome, then roll back and try another — the tree-search primitive that
turns the engine into something an agent can *reason over*, not just step
through.

``GameState`` is already fully JSON-serializable (the save system depends on
it), so:

- :func:`snapshot` = ``state.model_dump(mode="json")`` (the ``field_serializer``
  on ``meta`` strips the transient ``__``-prefixed bridges).
- :func:`restore` rebuilds the state from a snapshot and re-attaches the live
  ``__``-prefixed bridges (``__plugin_manager__``, ``__npc_registry__``,
  ``__rng__`` …) from the running session — mirroring the save-load path in
  ``scenes/save_scene.py``.
- :func:`diff` reports the leaf-level changes between two snapshots as a flat
  ``{dotted.path: {"from": ..., "to": ...}}`` map.

Sets (``story.played``, ``map.visited`` …) round-trip cleanly: ``model_dump``
emits them as lists and pydantic re-coerces them to sets on restore — the same
behaviour the save system relies on.
"""
from __future__ import annotations

from typing import Any

from ..core.game_state import GameState

_MISSING = object()


def snapshot(state: GameState) -> dict[str, Any]:
    """A JSON-safe deep snapshot of ``state`` (transient ``__`` meta stripped)."""
    return state.model_dump(mode="json")


def restore(state: GameState, data: dict[str, Any]) -> None:
    """Restore ``state`` in place from a :func:`snapshot` dict.

    The live transient bridges (every ``__``-prefixed ``meta`` key — plugin
    manager, npc registry, rng, pending toasts, …) are preserved from the
    current ``state`` because they were stripped from the snapshot and hold
    un-serializable Python objects. Copies the proven swap from
    ``scenes/save_scene.py``.
    """
    preserved = {k: v for k, v in state.meta.items() if k.startswith("__")}
    rebuilt = GameState.model_validate(data)
    state.__dict__.update(rebuilt.__dict__)
    state.meta.update(preserved)


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Leaf-level diff of two snapshots → ``{path: {"from": x, "to": y}}``.

    Dicts are walked recursively; any other value (including lists) is compared
    by equality and reported whole. Absent-on-one-side is reported with the
    missing side as ``None``.
    """
    changes: dict[str, dict[str, Any]] = {}

    def walk(b: Any, a: Any, path: str) -> None:
        if isinstance(b, dict) and isinstance(a, dict):
            for key in sorted(set(b) | set(a)):
                walk(b.get(key, _MISSING), a.get(key, _MISSING),
                     f"{path}.{key}" if path else key)
        elif b != a:
            changes[path] = {
                "from": None if b is _MISSING else b,
                "to": None if a is _MISSING else a,
            }

    walk(before, after, "")
    return changes
