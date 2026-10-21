"""Progressive story-destination offers built on exact travel plans.

Schedule-driven visual novels should not present every graph node as an equal
decision.  This module resolves a small primary layer of guaranteed current or
time-future story events while retaining ordinary reachable locations as a
secondary list.  It never invents a second movement path: current offers carry
the same immutable :class:`TravelPlan` that GUI and headless callers commit.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from .action_policy import action_availability
from .game_state import GameState
from .time_system import TimeOfDay
from .travel import (
    TravelMoment,
    TravelPlan,
    TravelService,
    clone_for_travel_preview,
    graph_travel_enabled,
    hidden_travel_tags,
    minimum_travel_cost,
    travel_state_revision,
)


Visibility = Literal["primary", "secondary"]
OfferStatus = Literal["current", "future", "locked", "neutral"]
OfferAction = Literal["travel", "wait", "none"]


@dataclass(frozen=True, slots=True)
class DestinationEvent:
    """Non-spoiler presentation for one exact enter/auto scene."""

    scene_id: str
    title: str
    npc_ids: tuple[str, ...] = ()
    teaser: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "title": self.title,
            "npc_ids": list(self.npc_ids),
            "teaser": self.teaser,
        }


@dataclass(frozen=True, slots=True)
class DestinationFuture:
    """Earliest advisory departure that produces the promised event."""

    wait_phases: int
    departure: TravelMoment
    arrival: TravelMoment

    def as_dict(self) -> dict[str, Any]:
        return {
            "wait_phases": self.wait_phases,
            "departure": _moment_dict(self.departure),
            "arrival": _moment_dict(self.arrival),
        }


@dataclass(frozen=True, slots=True)
class DestinationOffer:
    """One primary story marker or secondary ordinary destination."""

    target: str
    visibility: Visibility
    status: OfferStatus
    action: OfferAction
    available: bool
    plan: TravelPlan | None
    event: DestinationEvent | None
    future: DestinationFuture | None
    blocked_reason: str | None
    state_revision: str

    def as_dict(self) -> dict[str, Any]:
        plan = self.plan
        payload: dict[str, Any] = {
            "target": self.target,
            "visibility": self.visibility,
            "status": self.status,
            "action": self.action,
            "available": self.available,
            "event": self.event.as_dict() if self.event else None,
            "future": self.future.as_dict() if self.future else None,
            "blocked_reason": self.blocked_reason,
            "state_revision": self.state_revision,
        }
        if plan is None:
            payload["travel"] = None
        else:
            payload["travel"] = {
                "path": list(plan.location_ids),
                "route_cost": plan.route_cost,
                "cost": plan.total_cost,
                "arrival": _moment_dict(plan.arrival),
                "scene": plan.arrival_hook_id,
            }
        return payload


@dataclass(frozen=True, slots=True)
class GuidedStoryConfig:
    future_horizon_phases: int = 6
    max_primary: int = 3
    require_marker: bool = False
    direct_before_graph: bool = False
    auto_reopen: bool = False
    markers: Mapping[str, Mapping[str, Any]] | None = None


@dataclass(frozen=True, slots=True)
class GuidedStoryProgress:
    """Pack-authored progress for the current guided-map stage."""

    label: str
    stage: str
    completed: int
    total: int
    stage_index: int
    stage_count: int
    complete: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "stage": self.stage,
            "completed": self.completed,
            "total": self.total,
            "stage_index": self.stage_index,
            "stage_count": self.stage_count,
            "complete": self.complete,
        }


def _moment_dict(moment: TravelMoment) -> dict[str, Any]:
    return {
        "day": moment.day,
        "weekday": moment.day_of_week.value,
        "time_of_day": moment.time_of_day.value,
    }


def guided_story_config(
    meta: Mapping[str, Any],
    flags: Mapping[str, Any],
) -> GuidedStoryConfig | None:
    """Parse the optional engine-generic guided-story presentation policy."""
    travel = meta.get("travel", {})
    if (
        not isinstance(travel, Mapping)
        or travel.get("scope") != "graph"
    ):
        return None
    raw = travel.get("guided_story")
    if not isinstance(raw, Mapping):
        return None
    raw_direct_before_graph = raw.get("direct_before_graph", False)
    direct_before_graph = (
        raw_direct_before_graph
        if isinstance(raw_direct_before_graph, bool)
        else False
    )
    required = travel.get("requires_flags", ())
    if not isinstance(required, (list, tuple)):
        return None
    if (
        not graph_travel_enabled(meta, flags)
        and not direct_before_graph
    ):
        return None

    horizon = raw.get("future_horizon_phases", 6)
    if isinstance(horizon, bool) or not isinstance(horizon, int):
        horizon = 6
    horizon = max(0, min(horizon, len(tuple(TimeOfDay)) * 7))

    max_primary = raw.get("max_primary", 3)
    if isinstance(max_primary, bool) or not isinstance(max_primary, int):
        max_primary = 3
    max_primary = max(1, max_primary)

    raw_markers = raw.get("markers", {})
    markers: dict[str, Mapping[str, Any]] = {}
    if isinstance(raw_markers, Mapping):
        markers = {
            str(scene_id): marker
            for scene_id, marker in raw_markers.items()
            if isinstance(marker, Mapping)
        }
    return GuidedStoryConfig(
        future_horizon_phases=horizon,
        max_primary=max_primary,
        require_marker=(
            raw.get("require_marker")
            if isinstance(raw.get("require_marker"), bool)
            else False
        ),
        direct_before_graph=direct_before_graph,
        auto_reopen=(
            raw.get("auto_reopen")
            if isinstance(raw.get("auto_reopen"), bool)
            else False
        ),
        markers=markers,
    )


def guided_story_enabled(
    meta: Mapping[str, Any],
    flags: Mapping[str, Any],
) -> bool:
    return guided_story_config(meta, flags) is not None


def guided_story_progress(
    meta: Mapping[str, Any],
    flags: Mapping[str, Any],
) -> GuidedStoryProgress | None:
    """Resolve an optional ordered progress stage from pack metadata.

    Packs declare presentation-only stages under
    ``travel.guided_story.progress``. ``counted_flags`` drive the visible
    numerator while ``completion_flags`` hold the stage until its authored
    wrap-up scene actually finishes. Legacy stages that only declare ``flags``
    use that list for both roles. The helper never grants access or mutates
    narrative state, so authored scene conditions remain the sole source of
    progression truth.
    """
    if guided_story_config(meta, flags) is None:
        return None
    travel = meta.get("travel", {})
    if not isinstance(travel, Mapping):
        return None
    guided = travel.get("guided_story", {})
    if not isinstance(guided, Mapping):
        return None
    raw_progress = guided.get("progress")
    if not isinstance(raw_progress, Mapping):
        return None

    raw_label = raw_progress.get("label", "故事進度")
    label = (
        raw_label.strip()
        if isinstance(raw_label, str) and raw_label.strip()
        else "故事進度"
    )
    raw_stages = raw_progress.get("stages")
    if not isinstance(raw_stages, list):
        return None

    stages: list[
        tuple[str, tuple[str, ...], tuple[str, ...]]
    ] = []
    for index, raw_stage in enumerate(raw_stages, start=1):
        if not isinstance(raw_stage, Mapping):
            continue
        raw_counted_flags = raw_stage.get(
            "counted_flags",
            raw_stage.get("flags"),
        )
        if not isinstance(raw_counted_flags, list):
            continue
        counted_flags = tuple(dict.fromkeys(
            value.strip()
            for value in raw_counted_flags
            if isinstance(value, str) and value.strip()
        ))
        if not counted_flags:
            continue
        raw_completion_flags = raw_stage.get(
            "completion_flags",
            counted_flags,
        )
        if not isinstance(raw_completion_flags, list):
            raw_completion_flags = counted_flags
        completion_flags = tuple(dict.fromkeys(
            value.strip()
            for value in raw_completion_flags
            if isinstance(value, str) and value.strip()
        )) or counted_flags
        raw_stage_label = raw_stage.get("label")
        stage_label = (
            raw_stage_label.strip()
            if (
                isinstance(raw_stage_label, str)
                and raw_stage_label.strip()
            )
            else f"階段 {index}"
        )
        stages.append((stage_label, counted_flags, completion_flags))
    if not stages:
        return None

    selected_index = len(stages) - 1
    all_complete = True
    for index, (
        _stage_label,
        counted_flags,
        completion_flags,
    ) in enumerate(stages):
        counted_complete = all(
            bool(flags.get(flag)) for flag in counted_flags
        )
        wrap_complete = all(
            bool(flags.get(flag)) for flag in completion_flags
        )
        if not (counted_complete and wrap_complete):
            selected_index = index
            all_complete = False
            break
    stage_label, counted_flags, _completion_flags = stages[selected_index]
    completed = sum(bool(flags.get(flag)) for flag in counted_flags)
    return GuidedStoryProgress(
        label=label,
        stage=stage_label,
        completed=completed,
        total=len(counted_flags),
        stage_index=selected_index + 1,
        stage_count=len(stages),
        complete=all_complete,
    )


def guided_story_should_auto_reopen(
    meta: Mapping[str, Any],
    flags: Mapping[str, Any],
) -> bool:
    """Whether an unfinished guided sequence should hand control to the map.

    This is presentation policy only. It neither completes progress nor
    changes travel access, and it deliberately requires authored progress
    metadata so a pack with no finite guide cannot reopen the map forever.
    Invalid/non-boolean opt-ins retain the historical disabled behaviour.
    """
    config = guided_story_config(meta, flags)
    if config is None or not config.auto_reopen:
        return False
    progress = guided_story_progress(meta, flags)
    return progress is not None and not progress.complete


class GuidedDestinationResolver:
    """Resolve current/future story markers and secondary map locations."""

    def __init__(self, state: GameState, meta: Mapping[str, Any]):
        self.state = state
        self.meta = meta
        self.config = guided_story_config(meta, state.events.flags)
        self._graph_scope_active = graph_travel_enabled(
            meta,
            state.events.flags,
        )
        self._minimum_cost = (
            minimum_travel_cost(meta)
            if self._graph_scope_active
            else 0
        )
        # Hashing a full GameState can be comparatively expensive.  Most packs
        # do not enable guided destinations, and picker construction probes
        # ``enabled`` more than once, so defer the hash until an offer is
        # actually materialised.
        self._revision: str | None = None
        self._projected_states: dict[int, GameState] = {}
        self._projected_revisions: dict[int, str] = {}

    @property
    def enabled(self) -> bool:
        return self.config is not None

    @property
    def max_primary(self) -> int:
        return self.config.max_primary if self.config else 0

    def _prepare_resolution(self) -> bool:
        """Refresh policy and clear all caches for one live-state pass."""
        self.config = guided_story_config(
            self.meta,
            self.state.events.flags,
        )
        self._graph_scope_active = graph_travel_enabled(
            self.meta,
            self.state.events.flags,
        )
        self._minimum_cost = (
            minimum_travel_cost(self.meta)
            if self._graph_scope_active
            else 0
        )
        self._revision = None
        self._projected_states.clear()
        self._projected_revisions.clear()
        return self.config is not None

    def _candidate_locations(self):
        # A resolver may be retained by a custom host between frames.  Keep
        # per-pass projections reusable across locations, but never reuse them
        # across calls after the live clock or flags may have changed.
        current_id = self.state.map.current_location_id
        hidden = hidden_travel_tags(self.meta)
        for location in self.state.map.locations.values():
            if (
                location.id == current_id
                or hidden.intersection(location.tags)
            ):
                continue
            yield location

    def resolve(self) -> tuple[DestinationOffer, ...]:
        if not self._prepare_resolution():
            return ()
        offers: list[DestinationOffer] = []
        for location in self._candidate_locations():
            offers.append(self._resolve_location(location.id))
        return tuple(offers)

    def resolve_target(self, target: str) -> DestinationOffer | None:
        """Resolve one validated destination without probing every location.

        Detail/confirmation sheets already know which map marker the player
        selected.  Re-running the all-location resolver there needlessly
        projected unrelated arrivals and made opening one card scale with the
        complete world size.  This method preserves the exact current/future
        offer semantics while limiting work to that selected target.
        """
        if not self._prepare_resolution():
            return None
        location = self.state.map.get(target)
        if location is None:
            return None
        if location.id == self.state.map.current_location_id:
            return None
        if hidden_travel_tags(self.meta).intersection(location.tags):
            return None
        return self._resolve_location(location.id)

    def primary(self) -> tuple[DestinationOffer, ...]:
        """Resolve only locations that can possibly enter the primary layer."""
        if not self._prepare_resolution():
            return ()
        offers: list[DestinationOffer] = []
        for location in self._candidate_locations():
            if not self._may_have_primary_event(location.id):
                continue
            offer = self._resolve_location(location.id)
            if offer.visibility == "primary":
                offers.append(offer)
        return tuple(offers)

    def _plan(
        self,
        state: GameState,
        target: str,
        *,
        state_revision: str,
    ) -> TravelPlan | None:
        return TravelService._plan_with_state_revision(
            state,
            target,
            allow_distant=self._graph_scope_active,
            minimum_cost=self._minimum_cost,
            state_revision=state_revision,
        )

    def _offer_revision(self) -> str:
        if self._revision is None:
            self._revision = travel_state_revision(self.state)
        return self._revision

    def _projected_state(self, offset: int) -> GameState:
        """Return one detached clock projection shared across target probes.

        ``TravelService.plan`` is read-only and performs hook evaluation on a
        further detached clone, so reusing this projection cannot let one
        destination's condition leak into another's result.
        """
        projected = self._projected_states.get(offset)
        if projected is None:
            projected = clone_for_travel_preview(self.state)
            projected.time.advance(offset)
            self._projected_states[offset] = projected
        return projected

    def _projected_revision(self, offset: int) -> str:
        revision = self._projected_revisions.get(offset)
        if revision is None:
            revision = travel_state_revision(self._projected_state(offset))
            self._projected_revisions[offset] = revision
        return revision

    def _may_have_primary_event(self, target: str) -> bool:
        """Rule out locations no current or clock-projected hook can promote.

        Waiting in this resolver advances only the calendar.  Static hook
        facts—trigger kind, scene existence, once/played state, flag gates, and
        the optional marker allowlist—therefore cannot become true at a later
        offset.  Avoiding a graph plan plus detached preview for every ordinary
        location at every horizon phase keeps the guided layer affordable on
        larger maps.
        """
        location = self.state.map.get(target)
        if location is None:
            return False
        flags = self.state.events.flags
        played = self.state.story.played
        for hook in location.scene_hooks:
            if hook.trigger not in ("enter", "auto"):
                continue
            if self.state.story.get(hook.scene_id) is None:
                continue
            if not self._is_primary_scene(hook.scene_id):
                continue
            if hook.once and hook.scene_id in played:
                continue
            if any(not flags.get(flag) for flag in hook.requires_flags):
                continue
            if any(flags.get(flag) for flag in hook.forbids_flags):
                continue
            return True
        return False

    def _resolve_location(self, target: str) -> DestinationOffer:
        plan = self._plan(
            self.state,
            target,
            state_revision=self._offer_revision(),
        )
        current_secondary_event: DestinationEvent | None = None
        current_secondary_status: OfferStatus | None = None
        if plan is not None and plan.arrival_hook_id is not None:
            if not self._is_primary_scene(plan.arrival_hook_id):
                # Keep this exact current event as the ordinary-layer fallback,
                # but do not return yet.  A marked primary event may become the
                # actual first arrival hook at a later phase.
                current_secondary_event = self._event(plan)
                current_secondary_status = "current"
            else:
                return DestinationOffer(
                    target=target,
                    visibility="primary",
                    status="current",
                    action="travel",
                    available=True,
                    plan=plan,
                    event=self._event(plan),
                    future=None,
                    blocked_reason=None,
                    state_revision=self._offer_revision(),
                )

        future_plan: TravelPlan | None = None
        wait_phases = 0
        assert self.config is not None
        if self._may_have_primary_event(target):
            for offset in range(1, self.config.future_horizon_phases + 1):
                projected = self._projected_state(offset)
                candidate = self._plan(
                    projected,
                    target,
                    state_revision=self._projected_revision(offset),
                )
                if candidate is None or candidate.arrival_hook_id is None:
                    continue
                if not self._is_primary_scene(candidate.arrival_hook_id):
                    continue
                future_plan = candidate
                wait_phases = offset
                break

        if future_plan is not None:
            can_wait, wait_reason = action_availability(
                dict(self.meta),
                self.state,
                "advance_time",
            )
            return DestinationOffer(
                target=target,
                visibility="primary",
                status="future" if can_wait else "locked",
                action="wait" if can_wait else "none",
                available=can_wait,
                plan=future_plan,
                event=self._event(future_plan),
                future=DestinationFuture(
                    wait_phases=wait_phases,
                    departure=future_plan.departure,
                    arrival=future_plan.arrival,
                ),
                blocked_reason=(
                    f"再等待 {wait_phases} 個時段即可安排"
                    if can_wait
                    else wait_reason or "目前不能等待"
                ),
                state_revision=self._offer_revision(),
            )

        return DestinationOffer(
            target=target,
            visibility="secondary",
            status=(
                current_secondary_status
                or ("neutral" if plan is not None else "locked")
            ),
            action="travel" if plan is not None else "none",
            available=plan is not None,
            plan=plan,
            event=current_secondary_event,
            future=None,
            blocked_reason=None if plan is not None else "目前沒有可行路線",
            state_revision=self._offer_revision(),
        )

    def _is_primary_scene(self, scene_id: str) -> bool:
        if self.config is None or not self.config.require_marker:
            return True
        return (
            self.config.markers is not None
            and scene_id in self.config.markers
        )

    def _event(self, plan: TravelPlan) -> DestinationEvent | None:
        scene_id = plan.arrival_hook_id
        if scene_id is None:
            return None
        scene = self.state.story.get(scene_id)
        if scene is None:
            return None

        marker: Mapping[str, Any] = {}
        if self.config is not None and self.config.markers is not None:
            marker = self.config.markers.get(scene_id, {})
        raw_npcs = marker.get("npc_ids")
        if isinstance(raw_npcs, (list, tuple)) and all(
            isinstance(npc_id, str) for npc_id in raw_npcs
        ):
            npc_ids = tuple(raw_npcs)
        else:
            npc_ids = plan.arrival_npc_ids
        teaser = marker.get("teaser")
        if not isinstance(teaser, str) or not teaser.strip():
            teaser = None
        return DestinationEvent(
            scene_id=scene_id,
            title=scene.title or scene.id,
            npc_ids=npc_ids,
            teaser=teaser.strip() if teaser else None,
        )
