"""Central game state.

Holds player info + all subsystems. Provides serialize/deserialize for
save/load, and a single place to evaluate Conditions and apply Effects.
"""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field

from .achievements import AchievementTracker
from .affection import AffectionTracker
from .event_log import EventLog, DialogueHistory
from .inventory import Inventory, ItemRegistry, evaluate_gift
from .map_system import MapSystem
from .quest import QuestTracker
from .read_log import ReadLog
from .resources import ResourceTracker
from .story_graph import StoryGraph, Condition, Effect
from .time_system import TimeSystem


class PlayerInfo(BaseModel):
    name: str = "玩家"
    pronouns: str = "他"  # 他/她
    portrait: str | None = None
    notes: dict[str, Any] = Field(default_factory=dict)


class GameState(BaseModel):
    """Aggregate of every persistent subsystem."""

    player: PlayerInfo = Field(default_factory=PlayerInfo)
    affection: AffectionTracker = Field(default_factory=AffectionTracker)
    events: EventLog = Field(default_factory=EventLog)
    dialogue_history: DialogueHistory = Field(default_factory=DialogueHistory)
    map: MapSystem = Field(default_factory=MapSystem)
    story: StoryGraph = Field(default_factory=StoryGraph)
    time: TimeSystem = Field(default_factory=TimeSystem)
    achievements: AchievementTracker = Field(default_factory=AchievementTracker)
    items: ItemRegistry = Field(default_factory=ItemRegistry)
    inventory: Inventory = Field(default_factory=Inventory)
    resources: ResourceTracker = Field(default_factory=ResourceTracker)
    read_log: ReadLog = Field(default_factory=ReadLog)
    quests: QuestTracker = Field(default_factory=QuestTracker)
    route: str | None = None    # which heroine route is currently dominant
    meta: dict[str, Any] = Field(default_factory=dict)

    def evaluate(self, cond: Condition) -> bool:
        """Return True if condition is satisfied by current state."""
        k = cond.kind
        if k == "flag":
            return bool(self.events.get_flag(cond.target))
        if k == "not_flag":
            return not bool(self.events.get_flag(cond.target))
        if k == "flag_eq":
            return self.events.get_flag(cond.target) == cond.value
        if k == "affection_gte":
            stat = cond.stat or "affection"
            return self.affection.get(cond.target, stat) >= int(cond.value)
        if k == "affection_lt":
            stat = cond.stat or "affection"
            return self.affection.get(cond.target, stat) < int(cond.value)
        if k == "time_in":
            vals = cond.value if isinstance(cond.value, list) else [cond.value]
            return self.time.time_of_day.value in vals
        if k == "visited":
            return cond.target in self.map.visited
        if k == "scene_played":
            return self.story.is_played(cond.target)
        if k == "has_item":
            need = int(cond.value) if cond.value is not None else 1
            return self.inventory.has(cond.target, need)
        if k == "achievement":
            return self.achievements.is_unlocked(cond.target)
        if k == "resource_gte":
            return self.resources.get(cond.target) >= int(cond.value or 0)
        if k == "resource_lt":
            return self.resources.get(cond.target) < int(cond.value or 0)
        if k == "resource_eq":
            return self.resources.get(cond.target) == int(cond.value or 0)
        if k == "quest_active":
            return self.quests.is_active(cond.target)
        if k == "quest_completed":
            return self.quests.is_completed(cond.target)
        if k == "objective_completed":
            return self.quests.objective_completed(cond.target, cond.stat or "")
        return False

    def evaluate_all(self, conds: list[Condition]) -> bool:
        return all(self.evaluate(c) for c in conds)

    def evaluate_none(self, conds: list[Condition]) -> bool:
        return not any(self.evaluate(c) for c in conds)

    def apply(self, eff: Effect) -> dict[str, Any]:
        """Apply a single effect; return a small dict describing what happened."""
        k = eff.kind
        if k == "affection":
            new_val, unlocked = self.affection.adjust(eff.target, int(eff.value),
                                                     eff.stat or "affection")
            return {"kind": k, "target": eff.target, "new": new_val,
                    "unlocked": unlocked}
        if k == "stat":
            new_val, unlocked = self.affection.adjust(eff.target, int(eff.value),
                                                     eff.stat or "affection")
            return {"kind": k, "target": eff.target, "stat": eff.stat,
                    "new": new_val, "unlocked": unlocked}
        if k == "set_flag":
            self.events.set_flag(eff.target, eff.value if eff.value is not None else True)
            return {"kind": k, "target": eff.target, "value": eff.value}
        if k == "increment_flag":
            new_val = self.events.increment(eff.target, int(eff.value or 1))
            return {"kind": k, "target": eff.target, "new": new_val}
        if k == "advance_time":
            self.time.advance(int(eff.value or 1))
            return {"kind": k, "phase": self.time.time_of_day.value,
                    "day": self.time.day}
        if k == "move_to":
            try:
                loc = self.map.move_to(eff.target)
                self.events.record("location", f"來到 {loc.name}",
                                   location=loc.id, data={"to": eff.target})
                return {"kind": k, "to": eff.target}
            except KeyError:
                return {"kind": k, "error": f"未知地點: {eff.target}"}
        if k == "unlock_location":
            self.events.set_flag(f"unlock:{eff.target}", True)
            return {"kind": k, "target": eff.target}
        if k == "play_scene":
            return {"kind": k, "scene": eff.target}
        if k == "end_scene":
            return {"kind": k}
        if k == "log_event":
            self.events.record(
                kind="custom",
                title=eff.target,
                summary=str(eff.value or ""),
                location=self.map.current_location_id,
            )
            return {"kind": k, "title": eff.target}
        if k == "give_item":
            n = int(eff.value) if eff.value is not None else 1
            item = self.items.get(eff.target)
            max_stack = item.max_stack if item else None
            new_count = self.inventory.add(eff.target, n, max_stack=max_stack)
            self.events.record(
                kind="custom",
                title=f"獲得物品 · {item.name if item else eff.target}",
                data={"item": eff.target, "delta": n, "count": new_count},
            )
            return {"kind": k, "item": eff.target, "count": new_count}
        if k == "take_item":
            n = int(eff.value) if eff.value is not None else 1
            removed = self.inventory.remove(eff.target, n)
            return {"kind": k, "item": eff.target, "removed": removed}
        if k == "use_item":
            # target = item_id; consumes one of the item and applies its
            # use_effects in order.
            item_id = eff.target
            if not self.inventory.has(item_id, 1):
                return {"kind": k, "error": "missing item", "item": item_id}
            item = self.items.get(item_id)
            if item is None:
                return {"kind": k, "error": "unknown item", "item": item_id}
            if not item.consumable:
                return {"kind": k, "error": "not consumable", "item": item_id}
            self.inventory.remove(item_id, 1)
            sub_results: list[dict[str, Any]] = []
            if item.use_effects:
                sub_results = self.apply_all(item.use_effects)
            self.events.record(
                kind="custom",
                title=f"使用了 · {item.name}",
                data={"item": item_id, "effects": sub_results},
            )
            return {"kind": k, "item": item_id, "effects": sub_results}
        if k == "gain_resource":
            amount = int(eff.value or 0)
            old, new = self.resources.adjust(eff.target, amount)
            d = self.resources.definition(eff.target)
            label = (d.name or eff.target) if d else eff.target
            symbol = d.symbol if d else ""
            self.events.record(
                kind="custom",
                title=(f"獲得 {label} {symbol}+{amount}" if amount >= 0
                       else f"失去 {label} {symbol}{amount}"),
                data={"resource": eff.target, "delta": amount, "new": new},
            )
            return {"kind": k, "resource": eff.target,
                    "delta": amount, "old": old, "new": new}
        if k == "spend_resource":
            amount = int(eff.value or 0)
            ok, balance = self.resources.spend(eff.target, amount)
            if not ok:
                return {"kind": k, "error": "insufficient",
                        "resource": eff.target, "balance": balance,
                        "needed": amount}
            d = self.resources.definition(eff.target)
            label = (d.name or eff.target) if d else eff.target
            symbol = d.symbol if d else ""
            self.events.record(
                kind="custom",
                title=f"花費 {label} {symbol}-{amount}",
                data={"resource": eff.target, "delta": -amount, "new": balance},
            )
            return {"kind": k, "resource": eff.target,
                    "delta": -amount, "new": balance}
        if k == "set_resource":
            new = self.resources.set(eff.target, int(eff.value or 0))
            return {"kind": k, "resource": eff.target, "new": new}
        if k == "buy_item":
            # target = item_id; stat = currency_id; value = price (per unit)
            item_id = eff.target
            currency = eff.stat or "money"
            price = int(eff.value or 0)
            if not self.resources.can_afford(currency, price):
                return {"kind": k, "error": "insufficient_funds",
                        "currency": currency, "needed": price,
                        "balance": self.resources.get(currency)}
            self.resources.spend(currency, price)
            self.inventory.add(item_id, 1)
            item = self.items.get(item_id)
            name = item.name if item else item_id
            self.events.record(
                kind="custom",
                title=f"購買 · {name} ({currency} -{price})",
                data={"item": item_id, "currency": currency, "price": price},
            )
            return {"kind": k, "item": item_id, "currency": currency,
                    "price": price}
        if k == "sell_item":
            # target = item_id; stat = currency_id; value = price gained
            item_id = eff.target
            if not self.inventory.has(item_id, 1):
                return {"kind": k, "error": "missing item", "item": item_id}
            currency = eff.stat or "money"
            item = self.items.get(item_id)
            price = int(eff.value if eff.value is not None
                        else (item.value if item else 0))
            self.inventory.remove(item_id, 1)
            self.resources.adjust(currency, price)
            name = item.name if item else item_id
            self.events.record(
                kind="custom",
                title=f"賣出 · {name} ({currency} +{price})",
                data={"item": item_id, "currency": currency, "price": price},
            )
            return {"kind": k, "item": item_id, "currency": currency,
                    "price": price}
        if k == "gift":
            # target = npc_id, stat = item_id (reuse the field), value =
            # optional override count (default 1). The item must be in
            # inventory; the gift effect applies the heuristic in
            # inventory.evaluate_gift.
            item_id = eff.stat or ""
            n = int(eff.value) if eff.value is not None else 1
            if not item_id or not self.inventory.has(item_id, n):
                return {"kind": k, "error": "missing item", "item": item_id}
            item = self.items.get(item_id)
            if item is None:
                return {"kind": k, "error": "unknown item", "item": item_id}
            # Find the NPC: GameState doesn't own the registry, but the
            # NPCRegistry is created by the App and reachable via the gift
            # effect's *target* + meta hook. Without that registry, we
            # fall back to a +2 generic delta.
            registry = self.meta.get("__npc_registry__")
            delta = 2
            if registry is not None:
                npc = registry.get(eff.target)
                if npc is not None:
                    delta = evaluate_gift(item, npc)
            new_val, unlocked = self.affection.adjust(eff.target, delta)
            if item.consumed_on_gift:
                self.inventory.remove(item_id, n)
            self.events.record(
                kind="custom",
                title=f"送禮 · {item.name} → {eff.target}",
                summary=f"好感 {'+' if delta >= 0 else ''}{delta}",
                data={"item": item_id, "target": eff.target,
                       "delta": delta, "new": new_val},
            )
            return {"kind": k, "item": item_id, "target": eff.target,
                    "delta": delta, "new": new_val, "unlocked": unlocked}
        if k == "start_quest":
            started = self.quests.start(eff.target)
            return {"kind": k, "quest": eff.target, "started": started}
        if k == "complete_objective":
            obj_id = eff.stat or ""
            ok = self.quests.complete_objective(eff.target, obj_id)
            # Auto-complete the quest when all required objectives are done.
            auto_completed = False
            if ok and self.quests._all_required_done(eff.target):
                auto_completed = self.quests.complete(eff.target)
            return {"kind": k, "quest": eff.target, "objective": obj_id,
                    "ok": ok, "auto_completed": auto_completed}
        if k == "complete_quest":
            done = self.quests.complete(eff.target)
            return {"kind": k, "quest": eff.target, "done": done}
        if k == "fail_quest":
            failed = self.quests.fail(eff.target)
            return {"kind": k, "quest": eff.target, "failed": failed}
        return {"kind": k, "error": "unknown effect"}

    def apply_all(self, effs: list[Effect]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        # Capture pre-state so we can surface what changed (for toasts).
        pre_inventory = dict(self.inventory.counts)
        pre_resources = dict(self.resources.values)
        for e in effs:
            out.append(self.apply(e))
        # Re-evaluate achievements after any state mutation. Newly
        # unlocked ones go into the event log so they're visible in the
        # journal alongside other story beats.
        for ach in self.achievements.check(self):
            self.events.record(
                kind="unlock",
                title=f"成就解鎖：{ach.title}",
                summary=ach.description,
                data={"achievement": ach.id},
            )
            out.append({"kind": "achievement", "id": ach.id, "title": ach.title})
        # Stash item / resource deltas onto meta so the App's toast loop
        # can surface them. The keys are private (double-underscore).
        item_deltas: dict[str, int] = {}
        for iid, count in self.inventory.counts.items():
            delta = count - pre_inventory.get(iid, 0)
            if delta != 0:
                item_deltas[iid] = delta
        for iid, prev in pre_inventory.items():
            if iid not in self.inventory.counts:
                item_deltas[iid] = -prev
        resource_deltas: dict[str, int] = {}
        for rid, val in self.resources.values.items():
            delta = val - pre_resources.get(rid, 0)
            if delta != 0:
                resource_deltas[rid] = delta
        if item_deltas or resource_deltas:
            queue = self.meta.setdefault("__pending_toasts__", [])
            for iid, d in item_deltas.items():
                queue.append(("item", iid, d))
            for rid, d in resource_deltas.items():
                queue.append(("resource", rid, d))
        return out
