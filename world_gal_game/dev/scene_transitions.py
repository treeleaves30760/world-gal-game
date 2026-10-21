"""Static introspection for effects that transition between scenes.

The runtime resolves both ``play_scene`` and ``play_scene_branch`` into a
scene transition.  Developer tooling must see the same target universe without
executing the effect against a particular game state.  This module provides the
small, side-effect-free bridge shared by the raw-YAML inspector, typed
dataflow/reachability analyses, and validator.

``play_scene_branch`` cases are ordered: the first matching ``when`` wins and
the top-level ``target`` is the default when no case matches.  A transition
therefore carries the positive conditions that select it and the earlier case
conditions that must *not* match.  Analyses that can express boolean guards can
preserve that detail; simpler graph walkers may safely over-approximate by
using every returned target as an edge.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, Mapping


@dataclass(frozen=True)
class SceneTransition:
    """One possible static destination of a scene-transition effect."""

    target: str
    effect_kind: str
    path: str
    requires: tuple[Any, ...] = ()
    forbids: tuple[Any, ...] = ()
    branch_index: int | None = None
    is_default: bool = False


def field_value(obj: Any, name: str, default: Any = None) -> Any:
    """Read one field from either a raw mapping or a typed model."""

    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _condition(value: Any) -> Any | None:
    """Return a statically useful condition, or ``None`` when malformed."""

    kind = field_value(value, "kind", "")
    if isinstance(kind, str) and kind:
        return value
    return None


def iter_branch_conditions(effect: Any) -> Iterator[tuple[int, Any]]:
    """Yield each authored, well-shaped ``play_scene_branch`` case condition."""

    if field_value(effect, "kind", "") != "play_scene_branch":
        return
    cases = field_value(effect, "value")
    if not isinstance(cases, list):
        return
    for index, case in enumerate(cases):
        condition = _condition(field_value(case, "when"))
        if condition is not None:
            yield index, condition


def iter_scene_transitions(effect: Any) -> Iterator[SceneTransition]:
    """Yield every possible scene target exposed by *effect*.

    Supports both raw dictionaries and typed :class:`Effect` objects.  Invalid
    or empty targets are ignored; schema validation remains responsible for
    reporting malformed cases.
    """

    kind = field_value(effect, "kind", "")
    if kind == "play_scene":
        target = field_value(effect, "target", "")
        path = "target"
        if not target:
            value = field_value(effect, "value")
            if isinstance(value, str):
                target = value
                path = "value"
        if isinstance(target, str) and target:
            yield SceneTransition(
                target=target,
                effect_kind=kind,
                path=path,
            )
        return

    if kind != "play_scene_branch":
        return

    prior_conditions: list[Any] = []
    cases = field_value(effect, "value")
    if isinstance(cases, list):
        for index, case in enumerate(cases):
            condition = _condition(field_value(case, "when"))
            target = field_value(case, "target", "")
            if isinstance(target, str) and target:
                yield SceneTransition(
                    target=target,
                    effect_kind=kind,
                    path=f"value[{index}].target",
                    requires=((condition,) if condition is not None else ()),
                    forbids=tuple(prior_conditions),
                    branch_index=index,
                )
            if condition is not None:
                prior_conditions.append(condition)

    default = field_value(effect, "target", "")
    if isinstance(default, str) and default:
        yield SceneTransition(
            target=default,
            effect_kind=kind,
            path="target",
            forbids=tuple(prior_conditions),
            is_default=True,
        )
