"""Pure, read-only travel planning over a :class:`MapSystem` graph.

The legacy movement API intentionally answers only whether one authored exit
connects the current location to a target.  A destination-style visual-novel
map needs a different question: "can I reach that known destination through
the graph, and when would I arrive?"  This module answers that question without
moving the player or advancing the live clock.

Routes are time-dependent.  Each exit is checked at the projected time at
which that leg begins, not only at the journey's initial departure time.  The
search is deterministic: plans minimize total phase cost, then hop count, then
the lexicographic location-id path.  Directed exits and all existing
exit/location flag gates remain authoritative.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import heapq
import hashlib
from itertools import count
import json
from typing import Any, Mapping, TYPE_CHECKING

from .time_system import DayOfWeek, TimeOfDay, TimeSystem

if TYPE_CHECKING:
    from .game_state import GameState
    from .map_system import MapSystem


_PHASES = tuple(TimeOfDay)
_WEEKDAYS = tuple(DayOfWeek)


def graph_travel_enabled(
    meta: Mapping[str, Any],
    flags: Mapping[str, Any],
) -> bool:
    """Return whether a pack's atomic graph policy is active right now.

    ``requires_flags`` lets a directed opening keep its one-step tutorial
    choices before the wider schedule map becomes available.
    """
    policy = meta.get("travel", {})
    if not isinstance(policy, Mapping) or policy.get("scope") != "graph":
        return False
    required = policy.get("requires_flags", ())
    if not isinstance(required, (list, tuple)):
        return False
    return all(bool(flags.get(str(flag))) for flag in required)


def hidden_travel_tags(meta: Mapping[str, Any]) -> frozenset[str]:
    """Pack-authored location tags omitted from graph travel surfaces."""
    policy = meta.get("travel", {})
    if not isinstance(policy, Mapping):
        return frozenset()
    values = policy.get("hidden_tags", ())
    if not isinstance(values, (list, tuple)):
        return frozenset()
    return frozenset(str(value) for value in values)


def minimum_travel_cost(meta: Mapping[str, Any]) -> int:
    """Return the schedule cost floor for one confirmed graph-map action.

    A schedule-driven visual novel commonly charges one time slot for choosing
    a destination even when the authored campus graph uses zero-cost edges for
    ordinary free exploration.  ``travel.minimum_cost`` lets that pack opt in
    without changing legacy direct movement or charging once per invisible
    intermediate hop.
    """
    policy = meta.get("travel", {})
    if not isinstance(policy, Mapping):
        return 0
    value = policy.get("minimum_cost", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


@dataclass(frozen=True, slots=True)
class TravelMoment:
    """Immutable calendar projection used by travel previews.

    It mirrors the three persistent integers in :class:`TimeSystem`, but has
    no mutating methods.  Keeping the projection as primitives makes a
    :class:`TravelPlan` safe to cache, compare, serialize manually, and expose
    to UI/headless callers without leaking the live clock.
    """

    day: int
    weekday_index: int
    phase_index: int

    def __post_init__(self) -> None:
        if self.day < 1:
            raise ValueError("day must be >= 1")
        if not 0 <= self.weekday_index < len(_WEEKDAYS):
            raise ValueError(
                f"weekday_index must be in 0..{len(_WEEKDAYS) - 1}"
            )
        if not 0 <= self.phase_index < len(_PHASES):
            raise ValueError(
                f"phase_index must be in 0..{len(_PHASES) - 1}"
            )

    @classmethod
    def from_time_system(cls, time: TimeSystem) -> "TravelMoment":
        """Snapshot ``time`` without retaining or mutating it."""
        return cls(
            day=time.day,
            weekday_index=time.weekday_index,
            phase_index=time.phase_index,
        )

    @property
    def time_of_day(self) -> TimeOfDay:
        return _PHASES[self.phase_index]

    @property
    def day_of_week(self) -> DayOfWeek:
        return _WEEKDAYS[self.weekday_index]

    def advance(self, phases: int) -> "TravelMoment":
        """Return a new moment ``phases`` later.

        Arithmetic projection avoids the mutable, per-phase loop used by the
        runtime clock and remains fast for authored long-distance costs.
        """
        if phases < 0:
            raise ValueError("travel phases must be >= 0")
        elapsed_days, phase_index = divmod(
            self.phase_index + phases, len(_PHASES)
        )
        return TravelMoment(
            day=self.day + elapsed_days,
            weekday_index=(
                self.weekday_index + elapsed_days
            ) % len(_WEEKDAYS),
            phase_index=phase_index,
        )


@dataclass(frozen=True, slots=True)
class TravelLeg:
    """One immutable directed edge in a planned journey."""

    origin_id: str
    destination_id: str
    exit_ordinal: int
    travel_cost: int
    departure: TravelMoment
    arrival: TravelMoment

    def __post_init__(self) -> None:
        if self.exit_ordinal < 0:
            raise ValueError("exit_ordinal must be >= 0")
        if self.travel_cost < 0:
            raise ValueError("travel_cost must be >= 0")
        if self.arrival != self.departure.advance(self.travel_cost):
            raise ValueError(
                "leg arrival must equal departure advanced by travel_cost"
            )


@dataclass(frozen=True, slots=True)
class TravelPlan:
    """An immutable route proposal; creating one never changes game state."""

    origin_id: str | None
    destination_id: str
    path_location_ids: tuple[str, ...]
    legs: tuple[TravelLeg, ...]
    total_cost: int
    departure: TravelMoment
    arrival: TravelMoment
    # A schedule-map selection can consume at least one story slot even when
    # the graph path itself consists of zero-cost campus edges.  This is a
    # floor on the complete action, not an added charge per leg.
    minimum_cost: int = 0
    # TravelService enriches a pure MapSystem route with the exact arrival
    # promise shown by the UI. MapSystem plans leave these fields at defaults.
    arrival_npc_ids: tuple[str, ...] = ()
    available_hook_ids: tuple[str, ...] = ()
    arrival_hook_ids: tuple[str, ...] = ()
    arrival_hook_id: str | None = None
    state_revision: str | None = None

    def __post_init__(self) -> None:
        if self.total_cost < 0:
            raise ValueError("total_cost must be >= 0")
        if self.minimum_cost < 0:
            raise ValueError("minimum_cost must be >= 0")
        if not self.path_location_ids:
            raise ValueError("path_location_ids must not be empty")
        if (
            self.origin_id is not None
            and self.path_location_ids[0] != self.origin_id
        ):
            raise ValueError("path must start at origin_id")
        if self.origin_id is None and self.legs:
            raise ValueError("an initial-placement plan cannot contain legs")
        if self.path_location_ids[-1] != self.destination_id:
            raise ValueError("path must end at destination_id")
        if len(self.path_location_ids) != len(self.legs) + 1:
            raise ValueError("path must contain exactly one id per leg boundary")
        route_cost = sum(leg.travel_cost for leg in self.legs)
        if self.total_cost != max(route_cost, self.minimum_cost):
            raise ValueError(
                "total_cost must equal the greater of route and minimum cost"
            )

        moment = self.departure
        for index, leg in enumerate(self.legs):
            if leg.origin_id != self.path_location_ids[index]:
                raise ValueError("leg origin does not match path")
            if leg.destination_id != self.path_location_ids[index + 1]:
                raise ValueError("leg destination does not match path")
            if leg.departure != moment:
                raise ValueError("leg departure does not match previous arrival")
            moment = leg.arrival
        schedule_overhead = self.total_cost - route_cost
        if moment.advance(schedule_overhead) != self.arrival:
            raise ValueError(
                "plan arrival does not match route plus schedule cost"
            )
        if (
            self.arrival_hook_id is not None
            and self.arrival_hook_id not in self.arrival_hook_ids
        ):
            raise ValueError("arrival_hook_id must be in arrival_hook_ids")

    @property
    def location_ids(self) -> tuple[str, ...]:
        """Concise alias retained for callers that display only the path."""
        return self.path_location_ids

    @property
    def hop_count(self) -> int:
        return len(self.legs)

    @property
    def is_direct(self) -> bool:
        return self.hop_count == 1

    @property
    def route_cost(self) -> int:
        """Time carried by authored graph edges before the schedule floor."""
        return sum(leg.travel_cost for leg in self.legs)


@dataclass(frozen=True, slots=True)
class TravelPreview:
    """Arrival-state facts advertised before a player commits.

    The spatial/card UI must never calculate NPCs or event hooks independently
    from movement semantics.  This value is derived from a read-only projected
    :class:`GameState`, so its weekday, time-of-day, presence, and hook ids all
    describe the same instant represented by ``plan.arrival``.
    """

    plan: TravelPlan
    npc_ids: tuple[str, ...]
    available_hook_ids: tuple[str, ...]
    arrival_hook_ids: tuple[str, ...]
    scene_triggered: str | None


@dataclass(frozen=True, slots=True)
class TravelResult:
    """Structured outcome shared by GUI and headless travel callers."""

    ok: bool
    plan: TravelPlan | None = None
    moved_to: str | None = None
    scene_triggered: str | None = None
    npc_ids: tuple[str, ...] = ()
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the stable public result shape used by headless callers."""
        if not self.ok:
            return {"ok": False, "error": self.error or "travel failed"}
        out: dict[str, Any] = {"ok": True}
        if self.moved_to is not None:
            out["moved_to"] = self.moved_to
        if self.scene_triggered is not None:
            out["scene_triggered"] = self.scene_triggered
        return out


def project_arrival(
    departure: TimeSystem | TravelMoment,
    phases: int,
) -> TravelMoment:
    """Project an arrival without mutating ``departure``."""
    moment = (
        departure
        if isinstance(departure, TravelMoment)
        else TravelMoment.from_time_system(departure)
    )
    return moment.advance(phases)


def plan_direct_travel(
    map_system: "MapSystem",
    destination_id: str,
    *,
    flags: Mapping[str, Any],
    departure: TimeSystem | TravelMoment,
) -> TravelPlan | None:
    """Plan exactly one legacy authored exit without mutating state.

    Exploration buttons and the current card picker are direct-movement
    surfaces.  They must not silently choose an indirect route merely because
    a graph planner can find one.  Parallel exits deliberately retain the
    legacy first-match rule.
    """
    origin = map_system.current
    destination = map_system.get(destination_id)
    if destination is None:
        return None
    start = (
        departure
        if isinstance(departure, TravelMoment)
        else TravelMoment.from_time_system(departure)
    )
    if origin is None:
        # Preserve MapSystem.can_move_to's bootstrap contract: with no current
        # location, any known location may initialize the player position.
        return TravelPlan(
            origin_id=None,
            destination_id=destination_id,
            path_location_ids=(destination_id,),
            legs=(),
            total_cost=0,
            departure=start,
            arrival=start,
        )
    exit_match = next(
        (
            (ordinal, exit_obj)
            for ordinal, exit_obj in enumerate(origin.exits)
            if exit_obj.target == destination_id
        ),
        None,
    )
    if exit_match is None or not destination.is_accessible(dict(flags)):
        return None
    ordinal, exit_obj = exit_match
    snapshot = dict(flags)
    if not exit_obj.is_available(start.time_of_day.value, snapshot):
        return None
    arrival = start.advance(exit_obj.travel_cost)
    leg = TravelLeg(
        origin_id=origin.id,
        destination_id=destination_id,
        exit_ordinal=ordinal,
        travel_cost=exit_obj.travel_cost,
        departure=start,
        arrival=arrival,
    )
    return TravelPlan(
        origin_id=origin.id,
        destination_id=destination_id,
        path_location_ids=(origin.id, destination_id),
        legs=(leg,),
        total_cost=exit_obj.travel_cost,
        departure=start,
        arrival=arrival,
    )


def _arrival_preview_from_state(
    state: "GameState",
    plan: TravelPlan,
) -> TravelPreview:
    """Read arrival facts from a state already positioned at arrival."""
    destination = state.map.get(plan.destination_id)
    flags = state.events.flags
    npc_ids = tuple(state.map.npcs_present_at(
        destination,
        state.time.time_of_day.value,
        state.time.day_of_week.value,
        flags,
    ))
    eligible = [
        hook
        for hook in state.map.scenes_available_at(
            destination,
            time_of_day=state.time.time_of_day.value,
            flags=flags,
            played_scenes=state.story.played,
            state=state,
        )
        if state.story.get(hook.scene_id) is not None
    ]
    hook_ids = tuple(
        hook.scene_id
        for hook in eligible
        if hook.trigger in ("enter", "auto")
    )
    return TravelPreview(
        plan=plan,
        npc_ids=npc_ids,
        available_hook_ids=tuple(hook.scene_id for hook in eligible),
        arrival_hook_ids=hook_ids,
        scene_triggered=hook_ids[0] if hook_ids else None,
    )


def preview_travel(state: "GameState", plan: TravelPlan) -> TravelPreview:
    """Project the exact arrival NPCs and auto/enter hooks, read-only.

    Scene-hook conditions are plugin-dispatched and cannot be assumed pure.
    Evaluate them against a detached persistent-state clone so even a faulty
    condition that writes flags, inventory, story state, or resources cannot
    mutate the live playthrough merely because a picker card was rendered.
    """
    projected = clone_for_travel_preview(state)
    projected.map.move_to(plan.destination_id)
    if plan.total_cost:
        projected.time.advance(plan.total_cost)
    return _arrival_preview_from_state(projected, plan)


def _clone_state(state: "GameState") -> "GameState":
    """Clone the complete persistent aggregate for a travel transaction.

    Commit candidates retain the original deep-copy boundary because plugin
    effects may write any persistent subsystem before the transaction either
    succeeds or rolls back. Authored transient catalogue baselines are
    immutable app authorities and can be shared; all other transient condition
    context is copied best-effort and the live plugin manager is omitted.
    """
    cloned = type(state).model_validate(state.model_dump(mode="python"))
    authored_catalogue_keys = {
        "__pack_map_catalog__",
        "__pack_story_catalog__",
        "__pack_ending_catalog__",
    }
    for key, value in state.meta.items():
        if key == "__plugin_manager__":
            continue
        if key in authored_catalogue_keys:
            cloned.meta[key] = value
            continue
        if not str(key).startswith("__"):
            continue
        try:
            cloned.meta[key] = deepcopy(value)
        except Exception:
            # Never share a mutable bridge with a transaction. An extension
            # whose opaque context cannot be copied cannot participate in a
            # reliable projected promise and is omitted defensively.
            continue
    return cloned


def _clone_preview_state(state: "GameState") -> "GameState":
    """Clone mutable play state while sharing authored read-only catalogues.

    A loaded game parks complete map, story, and ending catalogues both on the
    persistent aggregate and under transient ``__pack_*_catalog__`` bridges.
    Re-validating a full ``model_dump`` copied thousands of immutable authored
    dialogue lines for every map marker preview.  Travel conditions only need
    an isolated copy of *progress* (flags, clock, visited/played sets, route,
    inventory, and similar mutable fields), so keep shallow catalogue maps and
    clone every mutable container around them.

    Location/scene/ending definition objects are immutable-in-practice pack
    content, just like the app's authored catalogue baselines.  Their mapping
    containers are still copied, which prevents a faulty condition from
    adding/removing definitions through a projected state.  The plugin manager
    remains omitted so projected reads cannot fire lifecycle hooks.
    """
    map_copy = state.map.model_copy(
        update={
            "locations": dict(state.map.locations),
            "regions": dict(state.map.regions),
            "visited": set(state.map.visited),
        },
        deep=False,
    )
    story_copy = state.story.model_copy(
        update={
            "scenes": dict(state.story.scenes),
            "played": set(state.story.played),
        },
        deep=False,
    )
    endings_copy = state.endings.model_copy(
        update={
            "endings": dict(state.endings.endings),
            "unlocked": dict(state.endings.unlocked),
            "seen": set(state.endings.seen),
        },
        deep=False,
    )

    authored_catalogue_keys = {
        "__pack_map_catalog__",
        "__pack_story_catalog__",
        "__pack_ending_catalog__",
    }
    meta_copy: dict[str, Any] = {}
    for key, value in state.meta.items():
        if key == "__plugin_manager__":
            continue
        if key in authored_catalogue_keys:
            meta_copy[key] = value
            continue
        try:
            meta_copy[key] = deepcopy(value)
        except Exception:
            # Never share an unknown mutable bridge with a preview. An
            # extension whose opaque context cannot be copied simply cannot
            # make a reliable projected promise and its condition will
            # defensively evaluate false if it requires that omitted bridge.
            continue

    updates: dict[str, Any] = {
        "map": map_copy,
        "story": story_copy,
        "endings": endings_copy,
        "meta": meta_copy,
    }
    for field_name in type(state).model_fields:
        if field_name in updates:
            continue
        value = getattr(state, field_name)
        model_copy = getattr(value, "model_copy", None)
        updates[field_name] = (
            model_copy(deep=True)
            if callable(model_copy)
            else deepcopy(value)
        )
    return state.model_copy(update=updates, deep=False)


def clone_for_travel_preview(state: "GameState") -> "GameState":
    """Return the detached state used by projected travel presentation."""
    return _clone_preview_state(state)


def _state_revision(state: "GameState") -> str:
    """Hash every serialized persistent field relevant to arrival promises."""
    payload = state.model_dump(mode="json")
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def travel_state_revision(state: "GameState") -> str:
    """Public stable revision for UI/headless destination offers."""
    return _state_revision(state)


def _route_signature(plan: TravelPlan) -> tuple[Any, ...]:
    """Fields that define route replay, excluding an enriched UI promise."""
    return (
        plan.origin_id,
        plan.destination_id,
        plan.path_location_ids,
        plan.legs,
        plan.total_cost,
        plan.minimum_cost,
        plan.departure,
        plan.arrival,
    )


def _preview_signature(preview: TravelPreview) -> tuple[Any, ...]:
    return (
        preview.npc_ids,
        preview.available_hook_ids,
        preview.arrival_hook_ids,
        preview.scene_triggered,
    )


def _promised_preview(plan: TravelPlan) -> TravelPreview:
    return TravelPreview(
        plan=plan,
        npc_ids=plan.arrival_npc_ids,
        available_hook_ids=plan.available_hook_ids,
        arrival_hook_ids=plan.arrival_hook_ids,
        scene_triggered=plan.arrival_hook_id,
    )


def _replace_persistent_state(
    state: "GameState",
    candidate: "GameState",
) -> None:
    """Commit a detached candidate while retaining live transient services.

    Plugin-owned runtime state follows the documented
    ``__plugin:<id>__`` convention.  It is part of the travel transaction:
    hooks mutate the candidate copy and those values replace their live
    counterparts only after the complete journey succeeds.  Opaque engine
    bridges such as the plugin manager and NPC registry remain live objects.
    """
    transient = {
        key: value
        for key, value in state.meta.items()
        if (
            str(key).startswith("__")
            and not str(key).startswith("__plugin:")
        )
    }
    live_plugin_state = {
        key: value
        for key, value in state.meta.items()
        if str(key).startswith("__plugin:")
    }
    plugin_state: dict[str, Any] = {}
    for key, value in candidate.meta.items():
        if not str(key).startswith("__plugin:"):
            continue
        existing = live_plugin_state.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            existing.clear()
            existing.update(value)
            plugin_state[key] = existing
        else:
            plugin_state[key] = value
    for field_name in type(state).model_fields:
        if field_name == "meta":
            continue
        setattr(state, field_name, getattr(candidate, field_name))
    state.meta = {
        key: value
        for key, value in candidate.meta.items()
        if not str(key).startswith("__")
    }
    state.meta.update(transient)
    state.meta.update(plugin_state)


class _TravelPluginTransaction:
    """Bind lifecycle dispatch to a detached travel candidate.

    ``PluginManager`` caches the live :class:`PluginContext`, so parking that
    manager directly on a cloned state would still hand hooks the live
    ``ctx.state``.  This adapter supplies an explicit detached context on
    every fire without rebinding the manager.  Context ``meta`` and ``scratch``
    are also copied, then installed only after a successful commit.

    GameState and conventional ``__plugin:<id>__`` mutations are therefore
    transactional.  As with every in-process hook system, opaque side effects
    outside those containers (module globals, files, network calls, and
    references retained by a plugin) cannot be rolled back after a hook runs;
    plugin side-effect declarations and handler isolation remain authoritative
    for those operations.
    """

    def __init__(
        self,
        manager: Any,
        live_context: Any,
        candidate: "GameState",
    ) -> None:
        self._manager = manager
        self._live_context = live_context
        try:
            context_meta = deepcopy(live_context.meta)
            self._commit_context_meta = True
        except Exception:
            context_meta = {}
            self._commit_context_meta = False
        try:
            context_scratch = deepcopy(live_context.scratch)
            self._commit_context_scratch = True
        except Exception:
            context_scratch = {}
            self._commit_context_scratch = False
        self.context = replace(
            live_context,
            state=candidate,
            meta=context_meta,
            scratch=context_scratch,
            manager=self,
        )

    def __getattr__(self, name: str) -> Any:
        """Expose read-only manager services while intercepting hook fires."""
        return getattr(self._manager, name)

    def fire_hook(
        self,
        event: str,
        _context: Any | None = None,
        /,
        **payload: Any,
    ) -> Any:
        """Fire through the live registry with the detached context."""
        return self._manager.fire_hook(event, self.context, **payload)

    def commit_context(self) -> None:
        """Install transaction-local context mappings after state commit."""
        if self._commit_context_meta:
            self._live_context.meta.clear()
            self._live_context.meta.update(self.context.meta)
        if self._commit_context_scratch:
            self._live_context.scratch.clear()
            self._live_context.scratch.update(self.context.scratch)


def _attach_travel_plugin_transaction(
    state: "GameState",
    candidate: "GameState",
) -> _TravelPluginTransaction | None:
    """Park a detached-context adapter when a real plugin context is active.

    Lightweight manager-shaped test doubles do not expose a cached
    :class:`PluginContext`; they retain the legacy post-commit lifecycle
    notification path below.
    """
    manager = state.meta.get("__plugin_manager__")
    if manager is None:
        return None
    live_context = getattr(manager, "_ctx", None)
    if live_context is None:
        return None
    from world_gal_game.plugins.context import PluginContext

    if not isinstance(live_context, PluginContext):
        return None
    transaction = _TravelPluginTransaction(
        manager,
        live_context,
        candidate,
    )
    candidate.meta["__plugin_manager__"] = transaction
    return transaction


class TravelService:
    """Single mutation boundary for player travel.

    Planning and preview are pure.  Commit revalidates the plan against the
    live origin, clock, flags, and graph, then applies movement/time through the
    registered builtin effects on a detached candidate.  Public
    ``effect.before_apply`` / ``effect.after_apply`` and movement/time hooks
    receive a transaction-bound context in their normal handler order.
    Candidate state, including conventional plugin runtime state, becomes live
    only after the complete journey and arrival promise validate.
    """

    @staticmethod
    def route(
        state: "GameState",
        destination_id: str,
        *,
        allow_distant: bool = False,
        minimum_cost: int = 0,
    ) -> TravelPlan | None:
        """Return only the immutable route and clock projection.

        Unlike :meth:`plan`, this lightweight query does not clone or hash the
        state and does not evaluate NPC or scene-hook conditions.  It is the
        appropriate primitive for map overview reachability, where callers
        need to know whether a destination can be selected but do not yet need
        an arrival promise suitable for confirmation and commit.
        """
        if minimum_cost < 0:
            raise ValueError("minimum_cost must be >= 0")
        planner = plan_travel if allow_distant else plan_direct_travel
        base = planner(
            state.map,
            destination_id,
            flags=state.events.flags,
            departure=state.time,
        )
        if base is None:
            return None
        if minimum_cost:
            total_cost = max(base.total_cost, minimum_cost)
            base = replace(
                base,
                total_cost=total_cost,
                arrival=base.departure.advance(total_cost),
                minimum_cost=minimum_cost,
            )
        return base

    @staticmethod
    def can_reach(
        state: "GameState",
        destination_id: str,
        *,
        allow_distant: bool = False,
        minimum_cost: int = 0,
    ) -> bool:
        """Return route reachability without constructing an arrival quote."""
        return TravelService.route(
            state,
            destination_id,
            allow_distant=allow_distant,
            minimum_cost=minimum_cost,
        ) is not None

    @staticmethod
    def plan(
        state: "GameState",
        destination_id: str,
        *,
        allow_distant: bool = False,
        minimum_cost: int = 0,
    ) -> TravelPlan | None:
        return TravelService._plan_with_state_revision(
            state,
            destination_id,
            allow_distant=allow_distant,
            minimum_cost=minimum_cost,
            state_revision=None,
        )

    @staticmethod
    def _plan_with_state_revision(
        state: "GameState",
        destination_id: str,
        *,
        allow_distant: bool = False,
        minimum_cost: int = 0,
        state_revision: str | None,
    ) -> TravelPlan | None:
        """Build one quote, optionally reusing an exact caller-owned revision.

        Batch presentation layers plan many destinations against the same
        immutable snapshot.  They may hash that snapshot once and pass the
        result here; ordinary callers use :meth:`plan`, which computes the
        revision itself after confirming that a route exists.
        """
        base = TravelService.route(
            state,
            destination_id,
            allow_distant=allow_distant,
            minimum_cost=minimum_cost,
        )
        if base is None:
            return None
        preview = preview_travel(state, base)
        return replace(
            base,
            arrival_npc_ids=preview.npc_ids,
            available_hook_ids=preview.available_hook_ids,
            arrival_hook_ids=preview.arrival_hook_ids,
            arrival_hook_id=preview.scene_triggered,
            state_revision=(
                state_revision
                if state_revision is not None
                else _state_revision(state)
            ),
        )

    @staticmethod
    def preview(state: "GameState", plan: TravelPlan) -> TravelPreview:
        if plan.state_revision is not None:
            return _promised_preview(plan)
        return preview_travel(state, plan)

    @staticmethod
    def _stale_reason(state: "GameState", plan: TravelPlan) -> str | None:
        current = state.map.current_location_id
        if current != plan.origin_id:
            return "出發地點已改變，請重新確認行程"
        if TravelMoment.from_time_system(state.time) != plan.departure:
            return "目前時段已改變，請重新確認行程"
        if (
            plan.state_revision is not None
            and _state_revision(state) != plan.state_revision
        ):
            return "角色或事件狀態已改變，請重新確認行程"

        planner = plan_travel if plan.hop_count > 1 else plan_direct_travel
        refreshed = planner(
            state.map,
            plan.destination_id,
            flags=state.events.flags,
            departure=state.time,
        )
        if refreshed is not None and plan.minimum_cost:
            total_cost = max(refreshed.total_cost, plan.minimum_cost)
            refreshed = replace(
                refreshed,
                total_cost=total_cost,
                arrival=refreshed.departure.advance(total_cost),
                minimum_cost=plan.minimum_cost,
            )
        if (
            refreshed is None
            or _route_signature(refreshed) != _route_signature(plan)
        ):
            return "路線或通行條件已改變，請重新確認行程"
        observed = preview_travel(state, refreshed)
        if (
            plan.state_revision is not None
            and _preview_signature(observed)
            != _preview_signature(_promised_preview(plan))
        ):
            return "抵達後的角色或事件已改變，請重新確認行程"
        return None

    @classmethod
    def commit(cls, state: "GameState", plan: TravelPlan) -> TravelResult:
        stale = cls._stale_reason(state, plan)
        if stale is not None:
            return TravelResult(ok=False, plan=plan, error=stale)

        promised = (
            _promised_preview(plan)
            if plan.state_revision is not None
            else preview_travel(state, plan)
        )
        from .story_graph import Effect

        # Run both extensible effect handlers against a detached candidate.
        # Only a completely successful, internally consistent candidate is
        # installed on the live aggregate, so a failure cannot leave behind a
        # location, time, visited, or event-log half-commit.
        candidate = _clone_state(state)
        plugin_transaction = _attach_travel_plugin_transaction(
            state,
            candidate,
        )
        move = candidate.apply(
            Effect(kind="move_to", target=plan.destination_id)
        )
        if "error" in move:
            return TravelResult(
                ok=False,
                plan=plan,
                error=str(move["error"]),
            )
        if plan.total_cost:
            advanced = candidate.apply(
                Effect(kind="advance_time", value=plan.total_cost)
            )
            if "error" in advanced:
                return TravelResult(
                    ok=False,
                    plan=plan,
                    error=str(advanced["error"]),
                )

        if (
            candidate.map.current_location_id != plan.destination_id
            or TravelMoment.from_time_system(candidate.time) != plan.arrival
        ):
            return TravelResult(
                ok=False,
                plan=plan,
                error="行程執行結果與預覽不一致，請重新確認行程",
            )
        arrived = _arrival_preview_from_state(_clone_state(candidate), plan)
        if _preview_signature(arrived) != _preview_signature(promised):
            return TravelResult(
                ok=False,
                plan=plan,
                error="抵達後的角色或事件已改變，請重新確認行程",
            )

        _replace_persistent_state(state, candidate)
        if plugin_transaction is not None:
            plugin_transaction.commit_context()

        # A real PluginManager already fired movement/time hooks from the
        # builtin effect handlers against the detached candidate. Keep the
        # legacy post-commit path for manager-shaped observers without a
        # PluginContext (used by lightweight embedders and focused tests).
        if plugin_transaction is None:
            from world_gal_game.plugins import fire_event
            from world_gal_game.plugins.context import HookEvent
            fire_event(
                state,
                HookEvent.PLAYER_MOVE,
                from_location=plan.origin_id,
                to_location=plan.destination_id,
            )
            if plan.total_cost:
                fire_event(
                    state,
                    HookEvent.TIME_ADVANCE,
                    phases=plan.total_cost,
                    day=state.time.day,
                    time_of_day=state.time.time_of_day.value,
                )
        return TravelResult(
            ok=True,
            plan=plan,
            moved_to=plan.destination_id,
            scene_triggered=promised.scene_triggered,
            npc_ids=promised.npc_ids,
        )


def plan_travel(
    map_system: "MapSystem",
    destination_id: str,
    *,
    flags: Mapping[str, Any],
    departure: TimeSystem | TravelMoment,
) -> TravelPlan | None:
    """Find the deterministic best currently traversable distant route.

    The graph is searched as ``(location, weekday, time-of-day)`` states.
    Including projected time is essential: a first leg may advance the clock
    into (or out of) the availability window of a later leg.  The engine does
    not insert implicit waiting; every phase in the result comes from an
    authored ``Exit.travel_cost``.

    ``None`` means the destination is unknown, inaccessible, or unreachable
    under the supplied flag/time snapshot.  The live map, visited set, flags,
    and clock are never mutated.
    """
    origin = map_system.current
    destination = map_system.get(destination_id)
    if origin is None or destination is None:
        return None

    start = (
        departure
        if isinstance(departure, TravelMoment)
        else TravelMoment.from_time_system(departure)
    )
    if origin.id == destination_id:
        return TravelPlan(
            origin_id=origin.id,
            destination_id=destination_id,
            path_location_ids=(origin.id,),
            legs=(),
            total_cost=0,
            departure=start,
            arrival=start,
        )
    if not destination.is_accessible(dict(flags)):
        return None

    # Queue order is the route objective itself:
    #   total phase cost -> hops -> lexicographic location-id path.
    # The sequence number keeps heap entries comparable even when duplicate
    # authored exits produce identical objective keys.
    serial = count()
    start_path = (origin.id,)
    start_exit_ordinals: tuple[int, ...] = ()
    start_key = (0, 0, start_path, start_exit_ordinals)
    queue: list[
        tuple[
            tuple[
                int,
                int,
                tuple[str, ...],
                tuple[int, ...],
            ],
            int,
            str,
            TravelMoment,
            tuple[TravelLeg, ...],
        ]
    ] = [
        (start_key, next(serial), origin.id, start, ())
    ]
    best: dict[
        tuple[str, int, int],
        tuple[
            int,
            int,
            tuple[str, ...],
            tuple[int, ...],
        ],
    ] = {
        (origin.id, start.weekday_index, start.phase_index): start_key
    }
    flag_snapshot = dict(flags)

    while queue:
        objective, _seq, location_id, moment, legs = heapq.heappop(queue)
        total_cost, hops, path, path_exit_ordinals = objective
        state_key = (
            location_id,
            moment.weekday_index,
            moment.phase_index,
        )
        if best.get(state_key) != objective:
            continue

        if location_id == destination_id:
            return TravelPlan(
                origin_id=origin.id,
                destination_id=destination_id,
                path_location_ids=path,
                legs=legs,
                total_cost=total_cost,
                departure=start,
                arrival=moment,
            )

        location = map_system.get(location_id)
        if location is None:
            continue

        # Legacy direct movement resolves the first authored exit matching a
        # target.  Keep the same rule here so every planned leg can later be
        # replayed by that API; a cheaper parallel edge must not become a route
        # the current runtime cannot execute.
        first_exit_by_target: dict[str, tuple[int, Any]] = {}
        for exit_ordinal, exit_obj in enumerate(location.exits):
            first_exit_by_target.setdefault(
                exit_obj.target,
                (exit_ordinal, exit_obj),
            )

        # Distinct destinations remain insertion-order independent.  The
        # ordinal is retained only as an edge identity / final tie-break.
        ordered_exits = sorted(
            first_exit_by_target.values(),
            key=lambda item: (
                item[1].target,
                item[0],
            ),
        )
        for exit_ordinal, exit_obj in ordered_exits:
            target = map_system.get(exit_obj.target)
            if target is None or not target.is_accessible(flag_snapshot):
                continue
            if not exit_obj.is_available(
                moment.time_of_day.value, flag_snapshot
            ):
                continue

            next_moment = moment.advance(exit_obj.travel_cost)
            next_path = path + (target.id,)
            next_exit_ordinals = path_exit_ordinals + (exit_ordinal,)
            next_objective = (
                total_cost + exit_obj.travel_cost,
                hops + 1,
                next_path,
                next_exit_ordinals,
            )
            next_state = (
                target.id,
                next_moment.weekday_index,
                next_moment.phase_index,
            )
            previous = best.get(next_state)
            if previous is not None and previous <= next_objective:
                continue

            leg = TravelLeg(
                origin_id=location_id,
                destination_id=target.id,
                exit_ordinal=exit_ordinal,
                travel_cost=exit_obj.travel_cost,
                departure=moment,
                arrival=next_moment,
            )
            best[next_state] = next_objective
            heapq.heappush(
                queue,
                (
                    next_objective,
                    next(serial),
                    target.id,
                    next_moment,
                    legs + (leg,),
                ),
            )

    return None
