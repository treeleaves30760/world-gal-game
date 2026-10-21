"""Pack-authored availability policies for generic player actions.

The engine owns actions such as free time advancement, while a pack owns the
story moment in which those actions make sense.  This module keeps the bridge
small and game-neutral: optional metadata gates an action on ordinary flags and
provides a player-facing explanation. GUI, headless sessions, planners, and
tests use the same policy for the action they expose. A map gate controls the
map and distant travel surfaces; it does not erase explicitly authored local
tutorial exits or story ``move_to`` effects.
"""
from __future__ import annotations

from typing import Any


def action_availability(
    meta: dict[str, Any],
    state: Any,
    action: str,
) -> tuple[bool, str | None]:
    """Return ``(available, locked_reason)`` for a generic action.

    Policies live under ``meta.yaml``'s optional ``exploration_guidance`` map::

        exploration_guidance:
          advance_time:
            requires_flags: [orientation_done]
            forbids_flags: []
            locked_reason: "先完成目前的目標。"

    Invalid optional metadata preserves the engine's historical
    allow-by-default behaviour. Pack validation can warn separately without
    trapping a player in exploration.
    """
    if not isinstance(meta, dict):
        return True, None
    guidance = meta.get("exploration_guidance", {})
    if not isinstance(guidance, dict):
        return True, None
    rule = guidance.get(action, {})
    if not isinstance(rule, dict):
        return True, None

    requires = rule.get("requires_flags", [])
    forbids = rule.get("forbids_flags", [])
    if not isinstance(requires, list) or not all(
            isinstance(flag, str) for flag in requires):
        return True, None
    if not isinstance(forbids, list) or not all(
            isinstance(flag, str) for flag in forbids):
        return True, None

    flags = state.events.flags
    available = all(bool(flags.get(flag)) for flag in requires)
    available = available and not any(
        bool(flags.get(flag)) for flag in forbids
    )
    if available:
        return True, None

    reason = rule.get("locked_reason")
    if not isinstance(reason, str) or not reason.strip():
        reason = "先完成目前的目標。"
    return False, reason.strip()
