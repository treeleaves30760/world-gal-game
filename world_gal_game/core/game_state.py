"""Central game state.

Holds player info + all subsystems. Provides serialize/deserialize for
save/load, and a single place to evaluate Conditions and apply Effects.

Effect/Condition dispatch is plugin-driven. :meth:`apply` and
:meth:`evaluate` look up the kind in
:data:`world_gal_game.plugins.EFFECT_REGISTRY` /
:data:`CONDITION_REGISTRY` and call the registered handler. The 40
builtin kinds the engine ships are registered by
:mod:`world_gal_game.plugins.builtin_effects` /
:mod:`builtin_conditions`; third-party plugins extend the same registry.

A :class:`world_gal_game.plugins.PluginManager` may be parked at
``state.meta["__plugin_manager__"]`` (the content loader does this
automatically). When present, ``apply`` fires
:data:`HookEvent.EFFECT_BEFORE_APPLY` / ``EFFECT_AFTER_APPLY`` around
each dispatch so plugins can observe and react.
"""
from __future__ import annotations

import logging
from typing import Any
from pydantic import BaseModel, Field, field_serializer

from .achievements import AchievementTracker
from .affection import AffectionTracker
from .cg_gallery import CGGallery
from .clue import ClueTracker
from .endings import EndingTracker
from .event_log import EventLog, DialogueHistory
from .inventory import Inventory, ItemRegistry
from .map_system import MapSystem
from .music_room import MusicRoom
from .quest import QuestTracker
from .read_log import ReadLog
from .resources import ResourceTracker
from .story_graph import StoryGraph, Condition, Effect
from .time_system import TimeSystem


_log = logging.getLogger("world_gal_game.core.game_state")


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
    clues: ClueTracker = Field(default_factory=ClueTracker)
    cg_gallery: CGGallery = Field(default_factory=CGGallery)
    music_room: MusicRoom = Field(default_factory=MusicRoom)
    endings: EndingTracker = Field(default_factory=EndingTracker)
    route: str | None = None    # which heroine route is currently dominant
    current_chapter: str | None = None   # id of the chapter the player is in (None = chapter-less / not yet entered)
    meta: dict[str, Any] = Field(default_factory=dict)

    @field_serializer("meta")
    def _serialize_meta(self, meta: dict[str, Any], _info) -> dict[str, Any]:
        """Strip transient ``__`` keys so pydantic JSON dump stays clean.

        Keys like ``__plugin_manager__`` / ``__npc_registry__`` hold live
        Python objects that pydantic can't serialise; they're recreated
        on the fly by ``load_pack``. SaveManager later applies a similar
        filter, but doing it here keeps every ``model_dump(mode='json')``
        path safe — not just the save_scene one.
        """
        return {k: v for k, v in meta.items() if not k.startswith("__")}

    def rng(self) -> "random.Random":
        """Return the per-state pseudo-random generator.

        Deterministic when a seed was threaded onto ``meta['__seed__']`` (the
        headless session / app does this from ``EngineConfig.seed``); otherwise
        entropy-seeded. Plugins, brains, and effects that need randomness must
        use this instead of the global ``random`` module, so that a seeded run
        is byte-for-byte reproducible — the determinism contract that lets an
        agent trust "same seed + same script -> same state". The generator
        lives under the transient ``__rng__`` meta key and is never serialized.
        """
        import random
        rng = self.meta.get("__rng__")
        if not isinstance(rng, random.Random):
            rng = random.Random(self.meta.get("__seed__"))
            self.meta["__rng__"] = rng
        return rng

    def evaluate(self, cond: Condition) -> bool:
        """Return True if condition is satisfied by current state.

        Dispatch goes through :data:`CONDITION_REGISTRY`. Unknown kinds
        log a warning and evaluate to ``False`` — a defensive default
        that lets the engine keep running when a YAML pack references a
        plugin that did not load.

        Handler exceptions are isolated: the error is logged and the
        condition evaluates to ``False`` for that call so a single
        misbehaving plugin doesn't crash the game.
        """
        # Late import: avoids any import-time coupling between core and
        # plugins. The import triggers builtin handler registration on
        # first call.
        from world_gal_game.plugins.registry import CONDITION_REGISTRY
        entry = CONDITION_REGISTRY.get(cond.kind)
        if entry is None:
            _log.warning("evaluate: unknown condition kind '%s' (target=%r); "
                         "treating as False", cond.kind, cond.target)
            return False
        try:
            return bool(entry.fn(self, cond))
        except Exception as exc:
            _log.exception(
                "condition handler '%s' (plugin %s) raised: %s; "
                "treating as False",
                cond.kind, entry.plugin_id, exc,
            )
            return False

    def evaluate_all(self, conds: list[Condition]) -> bool:
        return all(self.evaluate(c) for c in conds)

    def evaluate_none(self, conds: list[Condition]) -> bool:
        return not any(self.evaluate(c) for c in conds)

    def apply(self, eff: Effect) -> dict[str, Any]:
        """Apply a single effect; return a small dict describing what happened.

        Dispatch goes through :data:`EFFECT_REGISTRY`. If a
        :class:`world_gal_game.plugins.PluginManager` sits at
        ``state.meta["__plugin_manager__"]`` (the content loader puts
        one there), :data:`HookEvent.EFFECT_BEFORE_APPLY` and
        ``EFFECT_AFTER_APPLY`` fire around the handler call. Hook
        failures and handler failures are both isolated: errors are
        logged and an ``{"kind": kind, "error": ...}`` dict is returned
        instead of propagating the exception.
        """
        from world_gal_game.plugins.registry import EFFECT_REGISTRY

        manager = self.meta.get("__plugin_manager__")
        if manager is not None:
            self._fire_effect_hook(manager, "effect.before_apply", eff=eff)

        entry = EFFECT_REGISTRY.get(eff.kind)
        if entry is None:
            result: dict[str, Any] = {"kind": eff.kind, "error": "unknown effect"}
        else:
            try:
                result = entry.fn(self, eff)
            except Exception as exc:
                _log.exception(
                    "effect handler '%s' (plugin %s) raised: %s",
                    eff.kind, entry.plugin_id, exc,
                )
                result = {"kind": eff.kind, "error": f"handler failed: {exc}"}

        if manager is not None:
            self._fire_effect_hook(
                manager, "effect.after_apply", eff=eff, result=result,
            )
        return result

    # ------------------------------------------------------------------
    # Internals

    def _fire_effect_hook(self, manager: Any, event: str, **kwargs: Any) -> None:
        """Fire a lifecycle hook; never raise, only log on failure.

        ``manager`` is typed Any because game_state cannot import the
        plugin manager (it would be a circular import). At runtime it's
        always either ``None`` or a real :class:`PluginManager`.
        """
        try:
            manager.fire_hook(event, **kwargs)
        except Exception as exc:
            _log.exception("hook fire '%s' failed: %s", event, exc)

    def _queue_affection_toasts(self, pre_affection: dict[str, dict[str, int]],
                                out: list[dict[str, Any]]) -> None:
        """Enqueue a relationship toast for every NAMED affection threshold this
        batch just crossed (e.g. "林青衣 ·「在意你」").

        Driven purely off the data ``check_thresholds`` / ``crossed_thresholds``
        model: for each tracked character we diff the pre-batch ``affection``
        value against the post-batch value and emit one ``notice`` toast per
        threshold whose ``value`` was crossed upward. The character's display
        name comes from the parked NPC registry when available, else the id.
        Isolated: a failure here never disturbs effect application.
        """
        try:
            registry = self.meta.get("__npc_registry__")
            for cid, ca in self.affection.characters.items():
                before = (pre_affection.get(cid) or {}).get("affection", 0)
                after = ca.stats.get("affection", 0)
                crossed = ca.crossed_thresholds(before, after, "affection")
                if not crossed:
                    continue
                name = cid
                if registry is not None:
                    npc = registry.get(cid)
                    if npc is not None and getattr(npc, "name", None):
                        name = npc.name
                queue = self.meta.setdefault("__pending_toasts__", [])
                for th in crossed:
                    # ("notice", title, detail) — the App's toast loop renders
                    # title + detail. Keep the relationship beat in the detail.
                    queue.append(("notice", name, f"「{th.name}」"))
                    out.append({"kind": "affection_threshold", "target": cid,
                                "threshold": th.name, "value": th.value})
        except Exception as exc:
            _log.exception("affection toast queueing failed: %s", exc)

    def apply_all(self, effs: list[Effect]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        # Capture pre-state so we can surface what changed (for toasts).
        pre_inventory = dict(self.inventory.counts)
        pre_resources = dict(self.resources.values)
        # Per-character affection snapshot, so a NAMED threshold crossed by this
        # batch can be surfaced as a relationship toast (mirrors clues below).
        pre_affection = {
            cid: dict(ca.stats)
            for cid, ca in self.affection.characters.items()
        }
        for e in effs:
            out.append(self.apply(e))
        self._queue_affection_toasts(pre_affection, out)
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
        # Re-evaluate endings the same way: a route that just set its
        # ending_* flag unlocks here and is recorded in the event log so it
        # shows up in the journal and the endings / completion screen.
        for ending in self.endings.check(self):
            self.events.record(
                kind="unlock",
                title=f"結局解鎖：{ending.title}",
                summary=ending.description,
                data={"ending": ending.id},
            )
            out.append({"kind": "ending", "id": ending.id, "title": ending.title})
        # Re-evaluate clues: any newly satisfied requires-gates get added
        # to the journal and surfaced as toasts so the player notices the
        # journal button. Resolved clues stay (greyed out) inside the
        # journal — see ClueTracker.journal().
        for clue in self.clues.refresh(self):
            queue = self.meta.setdefault("__pending_toasts__", [])
            queue.append(("clue", clue.id, clue.title))
            out.append({"kind": "clue_unlocked", "id": clue.id,
                        "title": clue.title})
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
