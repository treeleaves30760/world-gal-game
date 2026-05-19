"""Builtin effect handlers.

Every effect kind that the engine has historically shipped (23 of
them, as of Phase 1) is implemented here as a free function registered
with ``@effect("kind", plugin_id="builtin")``. The :meth:`GameState.apply`
dispatcher looks them up in the global :data:`EFFECT_REGISTRY`.

Splitting them out of ``GameState.apply`` accomplishes three things:

1. The 39 if-elif dispatch table is gone — third-party plugins now use
   the same registration path the engine uses.
2. Capability Manifest can list builtin kinds with their schema hints
   without parsing source code.
3. Each handler is independently testable as ``handle_xxx(state, eff)``.

Return-dict shapes mirror the original implementation exactly so the
20+ existing tests run unchanged.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .registry import effect
from ..core.inventory import evaluate_gift

if TYPE_CHECKING:
    from ..core.game_state import GameState
    from ..core.story_graph import Effect


# Convenient constant for the owner of every builtin entry. Using a
# named string makes Capability Manifest summaries read better and
# lets unregister_plugin("builtin") clear the slate in tests.
BUILTIN = "builtin"


# ----------------------------------------------------------------------
# Affection / stats

@effect("affection", plugin_id=BUILTIN,
        description="Adjust a character's affection (default axis 'affection').",
        signature={"target": "character_id", "value": "int (delta)",
                "stat": "str? (axis name, default 'affection')"})
def handle_affection(state: "GameState", eff: "Effect") -> dict[str, Any]:
    new_val, unlocked = state.affection.adjust(
        eff.target, int(eff.value), eff.stat or "affection")
    return {"kind": eff.kind, "target": eff.target, "new": new_val,
            "unlocked": unlocked}


@effect("stat", plugin_id=BUILTIN,
        description="Adjust an arbitrary character stat (alias of affection with custom axis).",
        signature={"target": "character_id", "value": "int", "stat": "str (axis name)"})
def handle_stat(state: "GameState", eff: "Effect") -> dict[str, Any]:
    new_val, unlocked = state.affection.adjust(
        eff.target, int(eff.value), eff.stat or "affection")
    return {"kind": eff.kind, "target": eff.target, "stat": eff.stat,
            "new": new_val, "unlocked": unlocked}


# ----------------------------------------------------------------------
# Flags

@effect("set_flag", plugin_id=BUILTIN,
        description="Set a flag to value (defaults to True).",
        signature={"target": "flag_name", "value": "any (default True)"})
def handle_set_flag(state: "GameState", eff: "Effect") -> dict[str, Any]:
    state.events.set_flag(
        eff.target, eff.value if eff.value is not None else True)
    return {"kind": eff.kind, "target": eff.target, "value": eff.value}


@effect("increment_flag", plugin_id=BUILTIN,
        description="Add value (default 1) to a numeric flag.",
        signature={"target": "flag_name", "value": "int (default 1)"})
def handle_increment_flag(state: "GameState", eff: "Effect") -> dict[str, Any]:
    new_val = state.events.increment(eff.target, int(eff.value or 1))
    return {"kind": eff.kind, "target": eff.target, "new": new_val}


# ----------------------------------------------------------------------
# Time / location

@effect("advance_time", plugin_id=BUILTIN,
        description="Advance time-of-day by N phases (default 1).",
        signature={"value": "int (phases, default 1)"})
def handle_advance_time(state: "GameState", eff: "Effect") -> dict[str, Any]:
    phases = int(eff.value or 1)
    state.time.advance(phases)
    # Lifecycle hook so plugins can react to time movement uniformly.
    from . import fire_event
    from .context import HookEvent
    fire_event(state, HookEvent.TIME_ADVANCE,
               phases=phases, day=state.time.day,
               time_of_day=state.time.time_of_day.value)
    return {"kind": eff.kind, "phase": state.time.time_of_day.value,
            "day": state.time.day}


@effect("move_to", plugin_id=BUILTIN,
        description="Move player to a location id.",
        signature={"target": "location_id"})
def handle_move_to(state: "GameState", eff: "Effect") -> dict[str, Any]:
    from . import fire_event
    from .context import HookEvent
    prev_loc = state.map.current_location_id
    try:
        loc = state.map.move_to(eff.target)
        state.events.record("location", f"來到 {loc.name}",
                            location=loc.id, data={"to": eff.target})
        fire_event(state, HookEvent.PLAYER_MOVE,
                   from_location=prev_loc, to_location=loc.id)
        return {"kind": eff.kind, "to": eff.target}
    except KeyError:
        return {"kind": eff.kind, "error": f"未知地點: {eff.target}"}


@effect("unlock_location", plugin_id=BUILTIN,
        description="Set the implicit unlock flag for a location.",
        signature={"target": "location_id"})
def handle_unlock_location(state: "GameState", eff: "Effect") -> dict[str, Any]:
    state.events.set_flag(f"unlock:{eff.target}", True)
    return {"kind": eff.kind, "target": eff.target}


# ----------------------------------------------------------------------
# Scene control (dispatched by the dialogue engine, not GameState.apply
# itself — these handlers return a marker dict that the engine consumes)

@effect("play_scene", plugin_id=BUILTIN,
        description="Trigger transition to another scene (interpreted by DialogueEngine).",
        signature={"target": "scene_id"})
def handle_play_scene(state: "GameState", eff: "Effect") -> dict[str, Any]:
    return {"kind": eff.kind, "scene": eff.target}


@effect("end_scene", plugin_id=BUILTIN,
        description="End the current scene (interpreted by DialogueEngine).",
        signature={})
def handle_end_scene(state: "GameState", eff: "Effect") -> dict[str, Any]:
    return {"kind": eff.kind}


@effect("log_event", plugin_id=BUILTIN,
        description="Add a custom entry to the event log.",
        signature={"target": "title", "value": "str? (summary)"})
def handle_log_event(state: "GameState", eff: "Effect") -> dict[str, Any]:
    state.events.record(
        kind="custom",
        title=eff.target,
        summary=str(eff.value or ""),
        location=state.map.current_location_id,
    )
    return {"kind": eff.kind, "title": eff.target}


# ----------------------------------------------------------------------
# Inventory

@effect("give_item", plugin_id=BUILTIN,
        description="Add an item to inventory.",
        signature={"target": "item_id", "value": "int (count, default 1)"})
def handle_give_item(state: "GameState", eff: "Effect") -> dict[str, Any]:
    n = int(eff.value) if eff.value is not None else 1
    item = state.items.get(eff.target)
    max_stack = item.max_stack if item else None
    new_count = state.inventory.add(eff.target, n, max_stack=max_stack)
    state.events.record(
        kind="custom",
        title=f"獲得物品 · {item.name if item else eff.target}",
        data={"item": eff.target, "delta": n, "count": new_count},
    )
    return {"kind": eff.kind, "item": eff.target, "count": new_count}


@effect("take_item", plugin_id=BUILTIN,
        description="Remove an item from inventory.",
        signature={"target": "item_id", "value": "int (count, default 1)"})
def handle_take_item(state: "GameState", eff: "Effect") -> dict[str, Any]:
    n = int(eff.value) if eff.value is not None else 1
    removed = state.inventory.remove(eff.target, n)
    return {"kind": eff.kind, "item": eff.target, "removed": removed}


@effect("use_item", plugin_id=BUILTIN,
        description="Consume one of an item and apply its use_effects in order.",
        signature={"target": "item_id"})
def handle_use_item(state: "GameState", eff: "Effect") -> dict[str, Any]:
    item_id = eff.target
    if not state.inventory.has(item_id, 1):
        return {"kind": eff.kind, "error": "missing item", "item": item_id}
    item = state.items.get(item_id)
    if item is None:
        return {"kind": eff.kind, "error": "unknown item", "item": item_id}
    if not item.consumable:
        return {"kind": eff.kind, "error": "not consumable", "item": item_id}
    state.inventory.remove(item_id, 1)
    sub_results: list[dict[str, Any]] = []
    if item.use_effects:
        sub_results = state.apply_all(item.use_effects)
    state.events.record(
        kind="custom",
        title=f"使用了 · {item.name}",
        data={"item": item_id, "effects": sub_results},
    )
    return {"kind": eff.kind, "item": item_id, "effects": sub_results}


# ----------------------------------------------------------------------
# Resources

@effect("gain_resource", plugin_id=BUILTIN,
        description="Add (or subtract, with negative value) to a resource.",
        signature={"target": "resource_id", "value": "int (delta)"})
def handle_gain_resource(state: "GameState", eff: "Effect") -> dict[str, Any]:
    amount = int(eff.value or 0)
    old, new = state.resources.adjust(eff.target, amount)
    d = state.resources.definition(eff.target)
    label = (d.name or eff.target) if d else eff.target
    symbol = d.symbol if d else ""
    state.events.record(
        kind="custom",
        title=(f"獲得 {label} {symbol}+{amount}" if amount >= 0
               else f"失去 {label} {symbol}{amount}"),
        data={"resource": eff.target, "delta": amount, "new": new},
    )
    return {"kind": eff.kind, "resource": eff.target,
            "delta": amount, "old": old, "new": new}


@effect("spend_resource", plugin_id=BUILTIN,
        description="Spend an amount of a resource. Fails (returns error) if insufficient.",
        signature={"target": "resource_id", "value": "int (positive)"})
def handle_spend_resource(state: "GameState", eff: "Effect") -> dict[str, Any]:
    amount = int(eff.value or 0)
    ok, balance = state.resources.spend(eff.target, amount)
    if not ok:
        return {"kind": eff.kind, "error": "insufficient",
                "resource": eff.target, "balance": balance,
                "needed": amount}
    d = state.resources.definition(eff.target)
    label = (d.name or eff.target) if d else eff.target
    symbol = d.symbol if d else ""
    state.events.record(
        kind="custom",
        title=f"花費 {label} {symbol}-{amount}",
        data={"resource": eff.target, "delta": -amount, "new": balance},
    )
    return {"kind": eff.kind, "resource": eff.target,
            "delta": -amount, "new": balance}


@effect("set_resource", plugin_id=BUILTIN,
        description="Set a resource to an absolute value.",
        signature={"target": "resource_id", "value": "int (absolute)"})
def handle_set_resource(state: "GameState", eff: "Effect") -> dict[str, Any]:
    new = state.resources.set(eff.target, int(eff.value or 0))
    return {"kind": eff.kind, "resource": eff.target, "new": new}


# ----------------------------------------------------------------------
# Shopping / gifting

@effect("buy_item", plugin_id=BUILTIN,
        description="Spend currency, gain one item.",
        signature={"target": "item_id", "stat": "currency_id (default 'money')",
                "value": "int (price)"})
def handle_buy_item(state: "GameState", eff: "Effect") -> dict[str, Any]:
    item_id = eff.target
    currency = eff.stat or "money"
    price = int(eff.value or 0)
    if not state.resources.can_afford(currency, price):
        return {"kind": eff.kind, "error": "insufficient_funds",
                "currency": currency, "needed": price,
                "balance": state.resources.get(currency)}
    state.resources.spend(currency, price)
    state.inventory.add(item_id, 1)
    item = state.items.get(item_id)
    name = item.name if item else item_id
    state.events.record(
        kind="custom",
        title=f"購買 · {name} ({currency} -{price})",
        data={"item": item_id, "currency": currency, "price": price},
    )
    return {"kind": eff.kind, "item": item_id, "currency": currency,
            "price": price}


@effect("sell_item", plugin_id=BUILTIN,
        description="Remove one item, gain currency.",
        signature={"target": "item_id", "stat": "currency_id (default 'money')",
                "value": "int? (price; defaults to item.value)"})
def handle_sell_item(state: "GameState", eff: "Effect") -> dict[str, Any]:
    item_id = eff.target
    if not state.inventory.has(item_id, 1):
        return {"kind": eff.kind, "error": "missing item", "item": item_id}
    currency = eff.stat or "money"
    item = state.items.get(item_id)
    price = int(eff.value if eff.value is not None
                else (item.value if item else 0))
    state.inventory.remove(item_id, 1)
    state.resources.adjust(currency, price)
    name = item.name if item else item_id
    state.events.record(
        kind="custom",
        title=f"賣出 · {name} ({currency} +{price})",
        data={"item": item_id, "currency": currency, "price": price},
    )
    return {"kind": eff.kind, "item": item_id, "currency": currency,
            "price": price}


@effect("gift", plugin_id=BUILTIN,
        description="Give an item to an NPC; the gift heuristic computes a "
                    "tailored affection delta.",
        signature={"target": "npc_id", "stat": "item_id",
                "value": "int? (count, default 1)"})
def handle_gift(state: "GameState", eff: "Effect") -> dict[str, Any]:
    item_id = eff.stat or ""
    n = int(eff.value) if eff.value is not None else 1
    if not item_id or not state.inventory.has(item_id, n):
        return {"kind": eff.kind, "error": "missing item", "item": item_id}
    item = state.items.get(item_id)
    if item is None:
        return {"kind": eff.kind, "error": "unknown item", "item": item_id}
    # NPCRegistry sits on state.meta under a private key (see content_loader);
    # the gift heuristic needs the NPC to compute a tailored delta. If no
    # registry is around (e.g. a unit test with bare GameState), fall back
    # to a generic +2.
    registry = state.meta.get("__npc_registry__")
    delta = 2
    if registry is not None:
        npc = registry.get(eff.target)
        if npc is not None:
            delta = evaluate_gift(item, npc)
    new_val, unlocked = state.affection.adjust(eff.target, delta)
    if item.consumed_on_gift:
        state.inventory.remove(item_id, n)
    state.events.record(
        kind="custom",
        title=f"送禮 · {item.name} → {eff.target}",
        summary=f"好感 {'+' if delta >= 0 else ''}{delta}",
        data={"item": item_id, "target": eff.target,
              "delta": delta, "new": new_val},
    )
    return {"kind": eff.kind, "item": item_id, "target": eff.target,
            "delta": delta, "new": new_val, "unlocked": unlocked}


# ----------------------------------------------------------------------
# Quests

@effect("start_quest", plugin_id=BUILTIN,
        description="Activate a quest (inactive → active).",
        signature={"target": "quest_id"})
def handle_start_quest(state: "GameState", eff: "Effect") -> dict[str, Any]:
    started = state.quests.start(eff.target)
    return {"kind": eff.kind, "quest": eff.target, "started": started}


@effect("complete_objective", plugin_id=BUILTIN,
        description="Mark one objective on a quest as done. Auto-completes the "
                    "quest if all required objectives are now done.",
        signature={"target": "quest_id", "stat": "objective_id"})
def handle_complete_objective(state: "GameState", eff: "Effect") -> dict[str, Any]:
    obj_id = eff.stat or ""
    ok = state.quests.complete_objective(eff.target, obj_id)
    auto_completed = False
    if ok and state.quests._all_required_done(eff.target):
        auto_completed = state.quests.complete(eff.target)
    return {"kind": eff.kind, "quest": eff.target, "objective": obj_id,
            "ok": ok, "auto_completed": auto_completed}


@effect("complete_quest", plugin_id=BUILTIN,
        description="Directly mark a quest as completed.",
        signature={"target": "quest_id"})
def handle_complete_quest(state: "GameState", eff: "Effect") -> dict[str, Any]:
    done = state.quests.complete(eff.target)
    return {"kind": eff.kind, "quest": eff.target, "done": done}


@effect("fail_quest", plugin_id=BUILTIN,
        description="Mark a quest as failed.",
        signature={"target": "quest_id"})
def handle_fail_quest(state: "GameState", eff: "Effect") -> dict[str, Any]:
    failed = state.quests.fail(eff.target)
    return {"kind": eff.kind, "quest": eff.target, "failed": failed}


# Names re-exported for tests that want to invoke handlers directly without
# going through GameState.apply.
__all__ = [
    "handle_affection", "handle_stat", "handle_set_flag", "handle_increment_flag",
    "handle_advance_time", "handle_move_to", "handle_unlock_location",
    "handle_play_scene", "handle_end_scene", "handle_log_event",
    "handle_give_item", "handle_take_item", "handle_use_item",
    "handle_gain_resource", "handle_spend_resource", "handle_set_resource",
    "handle_buy_item", "handle_sell_item", "handle_gift",
    "handle_start_quest", "handle_complete_objective", "handle_complete_quest",
    "handle_fail_quest",
]
