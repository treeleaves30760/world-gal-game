"""step_counter — a tiny reference plugin.

Demonstrates every Phase 1 plugin extension point:

- ``@effect("reset_step_counter")`` — a new effect kind packs can call.
- ``@condition("steps_gte")`` — a new condition kind YAML scenes can gate on.
- ``@hook(HookEvent.EFFECT_AFTER_APPLY)`` — observe every dispatched effect.
- ``@inspect_field("step_counter")`` — surface a value into HeadlessSession.inspect().

Private state lives at ``state.meta["__plugin:step_counter__"]``. The
double-underscore prefix means :class:`SaveManager` filters it out on
serialise, which is correct: the step count is recomputed on the fly
by the hook anyway.

A pack can use this plugin by referencing the kinds it adds in YAML::

    # in scenes/example.yaml
    choices:
      - id: see_steps
        text: "How many steps have I taken?"
        requires:
          - {kind: steps_gte, value: 5}
        effects:
          - {kind: log_event, target: "走得真多", value: ""}
          - {kind: reset_step_counter}
"""
from __future__ import annotations

from world_gal_game.plugins import (
    effect, condition, hook, inspect_field, HookEvent,
)


# ----------------------------------------------------------------------
# Per-plugin state helper
#
# Every plugin should namespace its private state under a private
# key on ``state.meta``. The :meth:`PluginContext.get_plugin_state`
# helper does this with a consistent naming convention, but here in
# pure-effect-handler land we reach into ``state.meta`` directly.

def _slot(state):
    """Return (creating if missing) this plugin's private state dict."""
    key = "__plugin:step_counter__"
    slot = state.meta.get(key)
    if not isinstance(slot, dict):
        slot = {"count": 0}
        state.meta[key] = slot
    return slot


# ----------------------------------------------------------------------
# Effect — reset to zero

@effect("reset_step_counter",
        description="Reset the step counter to zero.")
def handle_reset(state, eff):
    slot = _slot(state)
    old = slot.get("count", 0)
    slot["count"] = 0
    return {"kind": eff.kind, "ok": True, "old": old, "new": 0}


# ----------------------------------------------------------------------
# Condition — "at least N steps taken"

@condition("steps_gte",
           description="True when steps taken >= value.",
           signature={"value": "int (minimum)"})
def cond_steps_gte(state, cond):
    slot = _slot(state)
    return slot.get("count", 0) >= int(cond.value or 0)


# ----------------------------------------------------------------------
# Hook — increment on every successful move_to
#
# The hook fires *after* the move handler ran, so we can read
# ``result`` to confirm the move actually succeeded (no error key).

@hook(HookEvent.EFFECT_AFTER_APPLY,
      description="Count successful move_to dispatches.")
def on_effect_applied(ctx, eff=None, result=None):
    if eff is None or eff.kind != "move_to":
        return
    if isinstance(result, dict) and "error" in result:
        return  # the move failed — don't credit a step
    if ctx.state is None:
        return
    slot = _slot(ctx.state)
    slot["count"] = slot.get("count", 0) + 1


# ----------------------------------------------------------------------
# Inspect field — surface the counter into HeadlessSession.inspect()

@inspect_field("step_counter",
               description="Number of successful location moves so far.")
def inspect_step_count(state):
    slot = _slot(state)
    return {"count": slot.get("count", 0)}
