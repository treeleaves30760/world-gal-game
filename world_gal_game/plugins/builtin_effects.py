"""Builtin effect handlers.

Every builtin effect kind that the engine ships (37 of
them, including the presentation-layer effects: camera/screen FX, scene
transitions, weather, portrait emotes, and movie playback) is implemented here
as a free function registered
with ``@effect("kind", plugin_id="builtin")``. The :meth:`GameState.apply`
dispatcher looks them up in the global :data:`EFFECT_REGISTRY`.

Splitting them out of ``GameState.apply`` accomplishes three things:

1. The historical if-elif dispatch table is gone — third-party plugins now use
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
from .effect_args import (
    AffectionArgs, StatArgs, SetFlagArgs, SetFlagIfUnsetArgs, IncrementFlagArgs,
    AdvanceTimeArgs, MoveToArgs, UnlockLocationArgs, PlaySceneArgs, EndSceneArgs,
    LogEventArgs, GiveItemArgs, TakeItemArgs, UseItemArgs, GainResourceArgs,
    SpendResourceArgs, SetResourceArgs, BuyItemArgs, SellItemArgs, GiftArgs,
    StartQuestArgs, CompleteObjectiveArgs, CompleteQuestArgs, FailQuestArgs,
    CameraPanArgs, CameraZoomArgs, ScreenShakeArgs, ScreenFlashArgs,
    ScreenTintArgs, ScreenBlurArgs,
    SetBackgroundArgs, ShowCgArgs, HideCgArgs, TransitionArgs,
    SetWeatherArgs, ClearWeatherArgs, PortraitEmoteArgs, PlayMovieArgs,
)
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

@effect("affection", plugin_id=BUILTIN, args=AffectionArgs,
        description="Adjust a character's affection (default axis 'affection').",
        signature={"target": "character_id", "value": "int (delta)",
                "stat": "str? (axis name, default 'affection')"})
def handle_affection(state: "GameState", eff: "Effect") -> dict[str, Any]:
    new_val, unlocked = state.affection.adjust(
        eff.target, int(eff.value), eff.stat or "affection")
    return {"kind": eff.kind, "target": eff.target, "new": new_val,
            "unlocked": unlocked}


@effect("stat", plugin_id=BUILTIN, args=StatArgs,
        description="Adjust an arbitrary character stat (alias of affection with custom axis).",
        signature={"target": "character_id", "value": "int", "stat": "str (axis name)"})
def handle_stat(state: "GameState", eff: "Effect") -> dict[str, Any]:
    new_val, unlocked = state.affection.adjust(
        eff.target, int(eff.value), eff.stat or "affection")
    return {"kind": eff.kind, "target": eff.target, "stat": eff.stat,
            "new": new_val, "unlocked": unlocked}


# ----------------------------------------------------------------------
# Flags

@effect("set_flag", plugin_id=BUILTIN, args=SetFlagArgs,
        description="Set a flag to value (defaults to True).",
        signature={"target": "flag_name", "value": "any (default True)"})
def handle_set_flag(state: "GameState", eff: "Effect") -> dict[str, Any]:
    state.events.set_flag(
        eff.target, eff.value if eff.value is not None else True)
    return {"kind": eff.kind, "target": eff.target, "value": eff.value}


@effect("set_flag_if_unset", plugin_id=BUILTIN, args=SetFlagIfUnsetArgs,
        description="Set a flag only when it is currently falsy / unset.",
        signature={"target": "flag_name", "value": "any (default True)"})
def handle_set_flag_if_unset(state: "GameState", eff: "Effect") -> dict[str, Any]:
    old = state.events.get_flag(eff.target)
    if old:
        return {"kind": eff.kind, "target": eff.target, "set": False, "old": old}
    value = eff.value if eff.value is not None else True
    state.events.set_flag(eff.target, value)
    return {"kind": eff.kind, "target": eff.target, "set": True, "value": value}


@effect("increment_flag", plugin_id=BUILTIN, args=IncrementFlagArgs,
        description="Add value (default 1) to a numeric flag.",
        signature={"target": "flag_name", "value": "int (default 1)"})
def handle_increment_flag(state: "GameState", eff: "Effect") -> dict[str, Any]:
    new_val = state.events.increment(eff.target, int(eff.value or 1))
    return {"kind": eff.kind, "target": eff.target, "new": new_val}


# ----------------------------------------------------------------------
# Time / location

@effect("advance_time", plugin_id=BUILTIN, args=AdvanceTimeArgs,
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


@effect("move_to", plugin_id=BUILTIN, args=MoveToArgs,
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


@effect("unlock_location", plugin_id=BUILTIN, args=UnlockLocationArgs,
        description="Set the implicit unlock flag for a location.",
        signature={"target": "location_id"})
def handle_unlock_location(state: "GameState", eff: "Effect") -> dict[str, Any]:
    state.events.set_flag(f"unlock:{eff.target}", True)
    return {"kind": eff.kind, "target": eff.target}


# ----------------------------------------------------------------------
# Scene control (dispatched by the dialogue engine, not GameState.apply
# itself — these handlers return a marker dict that the engine consumes)

@effect("play_scene", plugin_id=BUILTIN, args=PlaySceneArgs,
        description="Trigger transition to another scene (interpreted by DialogueEngine).",
        signature={"target": "scene_id"})
def handle_play_scene(state: "GameState", eff: "Effect") -> dict[str, Any]:
    return {"kind": eff.kind, "scene": eff.target}


@effect("end_scene", plugin_id=BUILTIN, args=EndSceneArgs,
        description="End the current scene (interpreted by DialogueEngine).",
        signature={})
def handle_end_scene(state: "GameState", eff: "Effect") -> dict[str, Any]:
    return {"kind": eff.kind}


@effect("log_event", plugin_id=BUILTIN, args=LogEventArgs,
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

@effect("give_item", plugin_id=BUILTIN, args=GiveItemArgs,
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


@effect("take_item", plugin_id=BUILTIN, args=TakeItemArgs,
        description="Remove an item from inventory.",
        signature={"target": "item_id", "value": "int (count, default 1)"})
def handle_take_item(state: "GameState", eff: "Effect") -> dict[str, Any]:
    n = int(eff.value) if eff.value is not None else 1
    removed = state.inventory.remove(eff.target, n)
    return {"kind": eff.kind, "item": eff.target, "removed": removed}


@effect("use_item", plugin_id=BUILTIN, args=UseItemArgs,
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

@effect("gain_resource", plugin_id=BUILTIN, args=GainResourceArgs,
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


@effect("spend_resource", plugin_id=BUILTIN, args=SpendResourceArgs,
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


@effect("set_resource", plugin_id=BUILTIN, args=SetResourceArgs,
        description="Set a resource to an absolute value.",
        signature={"target": "resource_id", "value": "int (absolute)"})
def handle_set_resource(state: "GameState", eff: "Effect") -> dict[str, Any]:
    new = state.resources.set(eff.target, int(eff.value or 0))
    return {"kind": eff.kind, "resource": eff.target, "new": new}


# ----------------------------------------------------------------------
# Shopping / gifting

@effect("buy_item", plugin_id=BUILTIN, args=BuyItemArgs,
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


@effect("sell_item", plugin_id=BUILTIN, args=SellItemArgs,
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


@effect("gift", plugin_id=BUILTIN, args=GiftArgs,
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

@effect("start_quest", plugin_id=BUILTIN, args=StartQuestArgs,
        description="Activate a quest (inactive → active).",
        signature={"target": "quest_id"})
def handle_start_quest(state: "GameState", eff: "Effect") -> dict[str, Any]:
    started = state.quests.start(eff.target)
    return {"kind": eff.kind, "quest": eff.target, "started": started}


@effect("complete_objective", plugin_id=BUILTIN, args=CompleteObjectiveArgs,
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


@effect("complete_quest", plugin_id=BUILTIN, args=CompleteQuestArgs,
        description="Directly mark a quest as completed.",
        signature={"target": "quest_id"})
def handle_complete_quest(state: "GameState", eff: "Effect") -> dict[str, Any]:
    done = state.quests.complete(eff.target)
    return {"kind": eff.kind, "quest": eff.target, "done": done}


@effect("fail_quest", plugin_id=BUILTIN, args=FailQuestArgs,
        description="Mark a quest as failed.",
        signature={"target": "quest_id"})
def handle_fail_quest(state: "GameState", eff: "Effect") -> dict[str, Any]:
    failed = state.quests.fail(eff.target)
    return {"kind": eff.kind, "quest": eff.target, "failed": failed}


# ----------------------------------------------------------------------
# Presentation: camera + screen FX
#
# These are genre-agnostic *presentation* primitives, so they ship as
# builtins. Critically, an effect handler runs inside ``GameState.apply``
# and has no access to pygame or the active scene — it must NEVER touch the
# display. Each handler instead records a small directive dict onto a private
# meta queue and returns immediately. ``DialogueScene`` drains that queue once
# per frame (``state.meta.pop(VISUAL_FX_QUEUE)``) and constructs the matching
# ``Camera`` / ``ScreenShake`` / ``ScreenFlash`` / ``ColorTint`` from
# ``ui/camera.py``, which it then animates and draws.
#
# Channel: ``state.meta["__visual_fx__"]`` — a list of directives, each
#   ``{"fx": <name>, ...params}``. The ``__`` prefix keeps it out of saves:
#   ``GameState._serialize_meta`` and ``SaveManager`` both strip ``__`` keys,
#   so a queued (but not-yet-consumed) effect is never persisted. The values
#   are plain JSON-able scalars, so even if the strip were bypassed it would
#   round-trip cleanly.

VISUAL_FX_QUEUE = "__visual_fx__"


def _queue_visual_fx(state: "GameState", directive: dict[str, Any]) -> None:
    """Append a presentation directive to the per-frame visual-fx queue.

    The scene consumes (and clears) this list each frame. This function is the
    single writer; it deliberately only mutates ``state.meta`` and touches no
    rendering APIs, so it is safe to call from the pure-Python ``apply`` path.
    """
    queue = state.meta.setdefault(VISUAL_FX_QUEUE, [])
    queue.append(directive)


@effect("camera_pan", plugin_id=BUILTIN, args=CameraPanArgs,
        description="Pan the camera to an offset (source px) over a duration. "
                    "Queued for the scene; does not touch the display.",
        signature={"value": "dict {x:float, y:float, duration:float?, "
                            "easing:str?}"})
def handle_camera_pan(state: "GameState", eff: "Effect") -> dict[str, Any]:
    p = eff.value if isinstance(eff.value, dict) else {}
    x = float(p.get("x", 0.0))
    y = float(p.get("y", 0.0))
    duration = float(p.get("duration", 0.6))
    easing = p.get("easing")
    _queue_visual_fx(state, {"fx": "camera_pan", "x": x, "y": y,
                             "duration": duration, "easing": easing})
    return {"kind": eff.kind, "x": x, "y": y, "duration": duration}


@effect("camera_zoom", plugin_id=BUILTIN, args=CameraZoomArgs,
        description="Zoom the camera to a scale (1.0 = neutral) over a "
                    "duration. Queued for the scene; does not touch the display.",
        signature={"value": "dict {scale:float, duration:float?, easing:str?}"})
def handle_camera_zoom(state: "GameState", eff: "Effect") -> dict[str, Any]:
    p = eff.value if isinstance(eff.value, dict) else {}
    scale = float(p.get("scale", 1.0))
    duration = float(p.get("duration", 0.6))
    easing = p.get("easing")
    _queue_visual_fx(state, {"fx": "camera_zoom", "scale": scale,
                             "duration": duration, "easing": easing})
    return {"kind": eff.kind, "scale": scale, "duration": duration}


@effect("screen_shake", plugin_id=BUILTIN, args=ScreenShakeArgs,
        description="Shake the whole frame with a decaying jitter. Queued for "
                    "the scene; does not touch the display.",
        signature={"value": "dict {intensity:float?, duration:float?, "
                            "easing:str?}"})
def handle_screen_shake(state: "GameState", eff: "Effect") -> dict[str, Any]:
    p = eff.value if isinstance(eff.value, dict) else {}
    intensity = float(p.get("intensity", 12.0))
    duration = float(p.get("duration", 0.4))
    easing = p.get("easing")
    _queue_visual_fx(state, {"fx": "screen_shake", "intensity": intensity,
                             "duration": duration, "easing": easing})
    return {"kind": eff.kind, "intensity": intensity, "duration": duration}


@effect("screen_flash", plugin_id=BUILTIN, args=ScreenFlashArgs,
        description="Flash a colour overlay that fades out over a duration. "
                    "Queued for the scene; does not touch the display.",
        signature={"value": "dict {color:[r,g,b]?, duration:float?, "
                            "max_alpha:int?, easing:str?}"})
def handle_screen_flash(state: "GameState", eff: "Effect") -> dict[str, Any]:
    p = eff.value if isinstance(eff.value, dict) else {}
    color = p.get("color", [255, 255, 255])
    duration = float(p.get("duration", 0.3))
    max_alpha = int(p.get("max_alpha", 255))
    easing = p.get("easing")
    _queue_visual_fx(state, {"fx": "screen_flash", "color": color,
                             "duration": duration, "max_alpha": max_alpha,
                             "easing": easing})
    return {"kind": eff.kind, "color": color, "duration": duration}


@effect("screen_tint", plugin_id=BUILTIN, args=ScreenTintArgs,
        description="Apply a persistent colour tint over the frame; fades in "
                    "over duration, or instantly when duration<=0. Pass a "
                    "clear flag (or color null) to remove the active tint. "
                    "Queued for the scene; does not touch the display.",
        signature={"value": "dict {color:[r,g,b]?, duration:float?, "
                            "max_alpha:int?, persist:bool?, clear:bool?, "
                            "easing:str?}"})
def handle_screen_tint(state: "GameState", eff: "Effect") -> dict[str, Any]:
    p = eff.value if isinstance(eff.value, dict) else {}
    clear = bool(p.get("clear", False)) or ("color" in p and p.get("color") is None)
    if clear:
        _queue_visual_fx(state, {"fx": "screen_tint", "clear": True})
        return {"kind": eff.kind, "clear": True}
    color = p.get("color", [0, 0, 0])
    # A persist flag (or duration<=0) means "appear and hold"; the scene treats
    # duration<=0 as instant-and-persistent in ColorTint.
    persist = bool(p.get("persist", False))
    duration = float(p.get("duration", 0.5))
    if persist and duration > 0:
        # Keep the fade-in but mark the directive persistent for clarity; the
        # tint persists regardless once constructed (ColorTint never expires).
        pass
    max_alpha = int(p.get("max_alpha", 120))
    easing = p.get("easing")
    _queue_visual_fx(state, {"fx": "screen_tint", "color": color,
                             "duration": duration, "max_alpha": max_alpha,
                             "persist": persist, "easing": easing})
    return {"kind": eff.kind, "color": color, "duration": duration,
            "persist": persist}


@effect("screen_blur", plugin_id=BUILTIN, args=ScreenBlurArgs,
        description="Apply a persistent depth-of-field blur to the background "
                    "layer (portraits / CG stay sharp); fades in over duration. "
                    "Pass clear=true (or radius<=0) to remove it. Queued for the "
                    "scene; does not touch the display.",
        signature={"value": "dict {radius:float?, duration:float?, "
                            "clear:bool?, easing:str?}"})
def handle_screen_blur(state: "GameState", eff: "Effect") -> dict[str, Any]:
    p = eff.value if isinstance(eff.value, dict) else {}
    radius = float(p.get("radius", 8.0))
    clear = bool(p.get("clear", False)) or radius <= 0
    if clear:
        _queue_visual_fx(state, {"fx": "screen_blur", "clear": True,
                                 "duration": float(p.get("duration", 0.5))})
        return {"kind": eff.kind, "clear": True}
    duration = float(p.get("duration", 0.5))
    easing = p.get("easing")
    _queue_visual_fx(state, {"fx": "screen_blur", "radius": radius,
                             "duration": duration, "easing": easing})
    return {"kind": eff.kind, "radius": radius, "duration": duration}


# ----------------------------------------------------------------------
# Presentation: scene transitions + mid-scene background / CG control
#
# Same contract as the camera/screen FX above: the handler runs in pure Python,
# records a directive on the visual-fx queue, and returns. The scene owns the
# snapshot of the previous frame and the SceneTransition that reveals the new
# one. ``set_background`` / ``show_cg`` / ``hide_cg`` carry both the *what*
# (which image) and the *how* (an optional transition) in one directive so the
# scene applies the state change and the animation together.

def _coerce_transition(value: Any) -> dict[str, Any]:
    """Normalise an effect's ``value`` into a transition directive subset.

    Accepts the nested transition dict (``{style, duration, easing, color,
    mask}``) authors write; missing keys fall back to a plain dissolve. Always
    returns JSON-able scalars so the directive round-trips cleanly.
    """
    p = value if isinstance(value, dict) else {}
    color = p.get("color", [0, 0, 0])
    try:
        color = [int(color[0]), int(color[1]), int(color[2])]
    except Exception:
        color = [0, 0, 0]
    return {
        "style": str(p.get("style", "dissolve")),
        "duration": float(p.get("duration", 0.6)),
        "easing": p.get("easing"),
        "color": color,
        "mask": p.get("mask"),
    }


@effect("set_background", plugin_id=BUILTIN, args=SetBackgroundArgs,
        description="Change the background mid-scene, with an optional "
                    "transition. target=image path; value={style,duration,"
                    "easing,color,mask}. Queued for the scene.",
        signature={"target": "image_path",
                   "value": "dict {style:str?, duration:float?, easing:str?, "
                            "color:[r,g,b]?, mask:str?}"})
def handle_set_background(state: "GameState", eff: "Effect") -> dict[str, Any]:
    tr = _coerce_transition(eff.value)
    _queue_visual_fx(state, {"fx": "set_background", "path": eff.target,
                             "transition": tr})
    return {"kind": eff.kind, "path": eff.target, "transition": tr["style"]}


@effect("show_cg", plugin_id=BUILTIN, args=ShowCgArgs,
        description="Show a full-screen CG, with an optional transition. "
                    "target=image path; value={style,duration,easing,color,"
                    "mask}. Queued for the scene.",
        signature={"target": "image_path",
                   "value": "dict {style:str?, duration:float?, easing:str?, "
                            "color:[r,g,b]?, mask:str?}"})
def handle_show_cg(state: "GameState", eff: "Effect") -> dict[str, Any]:
    tr = _coerce_transition(eff.value)
    _queue_visual_fx(state, {"fx": "show_cg", "path": eff.target,
                             "transition": tr})
    return {"kind": eff.kind, "path": eff.target, "transition": tr["style"]}


@effect("hide_cg", plugin_id=BUILTIN, args=HideCgArgs,
        description="Hide the active CG, with an optional transition. "
                    "value={style,duration,easing,color,mask}. Queued for the "
                    "scene.",
        signature={"value": "dict {style:str?, duration:float?, easing:str?, "
                            "color:[r,g,b]?, mask:str?}"})
def handle_hide_cg(state: "GameState", eff: "Effect") -> dict[str, Any]:
    tr = _coerce_transition(eff.value)
    _queue_visual_fx(state, {"fx": "hide_cg", "transition": tr})
    return {"kind": eff.kind, "transition": tr["style"]}


@effect("transition", plugin_id=BUILTIN, args=TransitionArgs,
        description="Play a stand-alone transition beat over the current frame "
                    "(e.g. fade to black and back) without changing the scene. "
                    "value={style,duration,easing,color,mask}. Queued for the "
                    "scene.",
        signature={"value": "dict {style:str?, duration:float?, easing:str?, "
                            "color:[r,g,b]?, mask:str?}"})
def handle_transition(state: "GameState", eff: "Effect") -> dict[str, Any]:
    tr = _coerce_transition(eff.value)
    _queue_visual_fx(state, {"fx": "transition", "transition": tr})
    return {"kind": eff.kind, "transition": tr["style"]}


# ----------------------------------------------------------------------
# Presentation: ambient / weather overlays
#
# Same queued-directive contract. ``set_weather`` names a registered
# @ambient_backend and carries its params; the scene instantiates the backend
# and draws it above the world layer, below the text box, persisting until a
# ``clear_weather`` (or another ``set_weather``). The handler forwards the raw
# params dict so backend-specific keys (not in WeatherValue) pass through.

@effect("set_weather", plugin_id=BUILTIN, args=SetWeatherArgs,
        description="Turn on an ambient overlay (rain/snow/petals/...). "
                    "target=registered @ambient_backend name; value=its params "
                    "(count, seed, alpha, color, fade, ...). Queued for the "
                    "scene; does not touch the display.",
        signature={"target": "ambient_backend_name",
                   "value": "dict {count:int?, seed:int?, alpha:int?, "
                            "color:[r,g,b]?, fade:float?, ...}"})
def handle_set_weather(state: "GameState", eff: "Effect") -> dict[str, Any]:
    params = dict(eff.value) if isinstance(eff.value, dict) else {}
    fade = float(params.pop("fade", 0.0) or 0.0)
    _queue_visual_fx(state, {"fx": "set_weather", "backend": eff.target,
                             "params": params, "fade": fade})
    return {"kind": eff.kind, "backend": eff.target}


@effect("clear_weather", plugin_id=BUILTIN, args=ClearWeatherArgs,
        description="Remove the active ambient overlay. Optional value.fade "
                    "fades it out. Queued for the scene; does not touch the "
                    "display.",
        signature={"value": "dict {fade:float?}"})
def handle_clear_weather(state: "GameState", eff: "Effect") -> dict[str, Any]:
    params = dict(eff.value) if isinstance(eff.value, dict) else {}
    fade = float(params.get("fade", 0.0) or 0.0)
    _queue_visual_fx(state, {"fx": "clear_weather", "fade": fade})
    return {"kind": eff.kind}


@effect("portrait_emote", plugin_id=BUILTIN, args=PortraitEmoteArgs,
        description="Play a one-shot in-place accent (jump/shake/nod/bounce) on "
                    "a settled portrait. target=slot ('left'/'center'/'right') "
                    "or character name; value={emote, duration, intensity}. "
                    "Queued for the scene; does not touch the display.",
        signature={"target": "slot_or_character",
                   "value": "dict {emote:str, duration:float?, "
                            "intensity:float?}"})
def handle_portrait_emote(state: "GameState", eff: "Effect") -> dict[str, Any]:
    p = eff.value if isinstance(eff.value, dict) else {}
    emote = str(p.get("emote", "jump"))
    duration = float(p.get("duration", 0.45))
    intensity = p.get("intensity")
    directive: dict[str, Any] = {"fx": "portrait_emote", "target": eff.target,
                                 "emote": emote, "duration": duration}
    if intensity is not None:
        directive["intensity"] = float(intensity)
    _queue_visual_fx(state, directive)
    return {"kind": eff.kind, "target": eff.target, "emote": emote}


@effect("play_movie", plugin_id=BUILTIN, args=PlayMovieArgs,
        description="Push a full-screen movie overlay (OP/ED/cutscene). "
                    "target=frame folder (image sequence) or video file; "
                    "value={kind, fps, loop, skippable}. Queued for the scene; "
                    "does not touch the display.",
        signature={"target": "frame_folder_or_video_path",
                   "value": "dict {kind:str?, fps:float?, loop:bool?, "
                            "skippable:bool?}"})
def handle_play_movie(state: "GameState", eff: "Effect") -> dict[str, Any]:
    p = eff.value if isinstance(eff.value, dict) else {}
    directive = {
        "fx": "play_movie", "path": eff.target,
        "kind": str(p.get("kind", "auto")),
        "fps": float(p.get("fps", 24.0)),
        "loop": bool(p.get("loop", False)),
        "skippable": bool(p.get("skippable", True)),
    }
    _queue_visual_fx(state, directive)
    return {"kind": eff.kind, "path": eff.target, "movie": directive["kind"]}


# Names re-exported for tests that want to invoke handlers directly without
# going through GameState.apply.
__all__ = [
    "handle_affection", "handle_stat", "handle_set_flag", "handle_set_flag_if_unset", "handle_increment_flag",
    "handle_advance_time", "handle_move_to", "handle_unlock_location",
    "handle_play_scene", "handle_end_scene", "handle_log_event",
    "handle_give_item", "handle_take_item", "handle_use_item",
    "handle_gain_resource", "handle_spend_resource", "handle_set_resource",
    "handle_buy_item", "handle_sell_item", "handle_gift",
    "handle_start_quest", "handle_complete_objective", "handle_complete_quest",
    "handle_fail_quest",
    "handle_camera_pan", "handle_camera_zoom", "handle_screen_shake",
    "handle_screen_flash", "handle_screen_tint",
    "handle_set_background", "handle_show_cg", "handle_hide_cg",
    "handle_transition", "handle_set_weather", "handle_clear_weather",
    "handle_portrait_emote", "handle_play_movie",
    "VISUAL_FX_QUEUE",
]
