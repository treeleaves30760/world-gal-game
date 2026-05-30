"""Goal-directed planner: turn the forward model into backward search.

The engine already has everything a search needs: a *deterministic* forward
model (``GameState.apply`` dispatch + an ``EngineConfig.seed`` that pins
``GameState.rng()``), a JSON-safe ``snapshot`` / ``restore`` pair for
checkpointing, and an ``affordances()`` view that enumerates the legal moves
from any state. What it lacks is the *backward* direction — given a goal as a
state predicate, find a sequence of ops that reaches it.

:class:`Planner` closes that gap. It runs a breadth-first search over the op
action space (so the returned ``path`` is a shortest op sequence), restoring a
snapshot at every node, expanding the affordances into candidate ops
(``choose`` / ``move`` / ``start_scene`` / ``next``), and testing the goal with
``HeadlessSession.assert_expect`` at each node. A visited-set keyed on the
state (location, scene, line index, flags, played scenes, inventory, resources,
affection, and the clock) collapses the ``move``/``next`` loops that would
otherwise make the space infinite — while still distinguishing states that
differ in any goal-readable dimension.

The result is the "find me a path that sets flag ``ending_lover``" primitive: an
agent states a goal and gets back a replayable op script instead of hand-writing
a test. The ``max_depth`` / ``max_nodes`` caps keep an unsatisfiable goal (or a
combinatorial pack) from exploring forever; on exhaustion the planner returns
``found=False`` with the nodes it did explore.
"""
from __future__ import annotations

from collections import deque
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..config import EngineConfig
from ..headless import HeadlessSession


class PlanResult(BaseModel):
    """Outcome of a :meth:`Planner.find_path` search.

    ``path`` is the op script (a list of ``{"op": ...}`` dicts) that, replayed
    on a fresh session after the same ``setup``, drives the state to satisfy
    ``goal``. ``depth`` is the number of ops in ``path`` (the search depth
    beyond the setup), and ``nodes_explored`` is how many states the search
    popped before stopping — useful both as a cost signal and as proof that a
    ``found=False`` result actually terminated within its caps.
    """

    model_config = ConfigDict(extra="forbid")

    found: bool
    goal: dict[str, Any]
    path: list[dict[str, Any]]
    depth: int
    nodes_explored: int


class Planner:
    """Breadth-first goal search over a pack's op action space.

    Sessions are opened fresh from ``HeadlessSession.open`` as needed, always
    with the same ``seed`` so the forward model stays deterministic across the
    snapshot/restore churn of the search.
    """

    def __init__(self, pack: str, *, seed: int | None = 42) -> None:
        self.pack = pack
        self.seed = seed

    # ----- session helpers ----------------------------------------------

    def _open(self) -> HeadlessSession:
        return HeadlessSession.open(EngineConfig(seed=self.seed), pack=self.pack)

    @staticmethod
    def _state_key(sess: HeadlessSession) -> tuple:
        """A hashable fingerprint of the search-relevant state.

        Read straight off ``sess.state`` — cheaper than a full ``inspect()``
        for a key computed on every visited node. Two states are treated as
        identical only when *every* dimension a goal can read matches:
        location, current scene, line index, flags, played scenes, **plus
        inventory counts, resource values, per-character affection stats, and
        the clock**. Folding those last four in is the correctness fix for the
        earlier key: a goal like "affection >= 50" or "has 3 coins" lives in a
        dimension the old key ignored, so the search could collapse a genuinely
        new state onto a visited one and wrongly report the goal unreachable.
        ``move``/``next`` cycles still collapse because they leave all of these
        unchanged; only ops that actually move a tracked dimension fan out.
        """
        st = sess.state
        flags = tuple(sorted(
            (str(k), _hashable(v)) for k, v in st.events.flags.items()))
        played = tuple(sorted(str(s) for s in st.story.played))
        inventory = tuple(sorted(
            (str(k), int(v)) for k, v in st.inventory.counts.items() if v))
        resources = tuple(sorted(
            (str(k), int(v)) for k, v in st.resources.values.items()))
        affection = tuple(
            (cid, tuple(sorted((str(s), int(val)) for s, val in ca.stats.items())))
            for cid, ca in sorted(st.affection.characters.items())
        )
        clock = (st.time.day, st.time.phase_index, st.time.weekday_index)
        return (
            st.map.current_location_id,
            st.story.current_scene,
            st.story.current_line_index,
            flags,
            played,
            inventory,
            resources,
            affection,
            clock,
        )

    @staticmethod
    def _presentation_kind(sess: HeadlessSession) -> str | None:
        pres = sess.last_presentation
        return pres.get("kind") if isinstance(pres, dict) else None

    def _actions(self, sess: HeadlessSession, *, explore_moves: bool,
                 explore_scenes: bool) -> list[dict[str, Any]]:
        """Candidate ops from the current affordances + an ``next`` step.

        ``next`` is always offered unless the scene has already ended (advancing
        past an end is a no-op); the state-key dedup catches any that slip
        through as redundant.
        """
        actions: list[dict[str, Any]] = []
        try:
            aff = sess.affordances()
        except Exception:
            aff = {}

        for ch in aff.get("choices", []):
            if ch.get("enabled") and ch.get("id") is not None:
                actions.append({"op": "choose", "choice": ch["id"]})

        if explore_moves:
            for ex in aff.get("exits", []):
                if ex.get("available") and ex.get("target") is not None:
                    actions.append({"op": "move", "location": ex["target"]})

        if explore_scenes:
            for sid in aff.get("scenes_available", []):
                if sid is not None:
                    actions.append({"op": "start_scene", "scene": sid})

        if self._presentation_kind(sess) != "end":
            actions.append({"op": "next", "count": 1})

        return actions

    # ----- search --------------------------------------------------------

    def find_path(self, goal: dict[str, Any], *,
                  setup: list[dict[str, Any]] | None = None,
                  explore_moves: bool = True, explore_scenes: bool = True,
                  max_depth: int = 30, max_nodes: int = 4000) -> PlanResult:
        """BFS for a shortest op ``path`` that makes ``goal`` hold.

        Opens a session, runs ``setup`` to establish the start state, then
        searches outward. Each frontier entry pairs a ``snapshot`` with the
        ``path`` of ops that produced it; popping one restores the snapshot,
        checks the goal, and (on miss) expands the action space. A failing
        action simply yields no child. Returns ``found=False`` with the
        explored-node count if the frontier empties or ``max_nodes`` is hit.
        """
        setup = setup or []
        sess = self._open()
        if setup:
            try:
                sess.run_script(list(setup))
            except Exception:
                # A broken setup leaves us at whatever state it reached; the
                # search still runs from there rather than crashing.
                pass

        start_snapshot = sess.snapshot()
        start_key = self._state_key(sess)

        frontier: deque[tuple[dict[str, Any], list[dict[str, Any]]]] = deque()
        frontier.append((start_snapshot, []))
        visited: set[tuple] = {start_key}
        nodes_explored = 0

        while frontier:
            if nodes_explored >= max_nodes:
                break
            snap, path = frontier.popleft()
            nodes_explored += 1

            sess.restore(snap)
            try:
                reached = bool(sess.assert_expect(goal).get("ok"))
            except Exception:
                reached = False
            if reached:
                return PlanResult(found=True, goal=goal, path=path,
                                  depth=len(path), nodes_explored=nodes_explored)

            if len(path) >= max_depth:
                continue

            # Expand from this node. ``_actions`` reads the restored state.
            for action in self._actions(sess, explore_moves=explore_moves,
                                         explore_scenes=explore_scenes):
                sess.restore(snap)
                try:
                    self._apply_action(sess, action)
                except Exception:
                    continue
                key = self._state_key(sess)
                if key in visited:
                    continue
                visited.add(key)
                child = sess.snapshot()
                frontier.append((child, path + [action]))

        return PlanResult(found=False, goal=goal, path=[], depth=0,
                          nodes_explored=nodes_explored)

    @staticmethod
    def _apply_action(sess: HeadlessSession, action: dict[str, Any]) -> None:
        """Apply one expansion op to ``sess`` via its matching method.

        Mirrors the ``run_script`` dispatch for the four search ops; anything
        else is routed through ``run_script`` so the planner stays correct even
        if the action vocabulary grows.
        """
        op = action.get("op")
        if op == "choose":
            sess.choose(action["choice"])
        elif op == "move":
            sess.move_to(action["location"])
        elif op == "start_scene":
            sess.start_scene(action["scene"])
        elif op == "next":
            sess.next_line(int(action.get("count", 1)))
        else:
            sess.run_script([action])


def _hashable(value: Any) -> Any:
    """Coerce a flag value into something hashable for the visited-set key."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return tuple(_hashable(v) for v in value)
    if isinstance(value, dict):
        return tuple(sorted((str(k), _hashable(v)) for k, v in value.items()))
    return repr(value)
