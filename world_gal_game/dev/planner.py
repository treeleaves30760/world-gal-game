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

    ``exhausted`` distinguishes the two flavours of a ``found=False`` result:
    ``True`` means the frontier emptied — every reachable state was visited and
    none satisfied the goal, i.e. the goal is **provably unreachable** under the
    explored op vocabulary. ``False`` means a cap (``max_nodes`` /
    ``max_depth`` / ``time_budget_s``) stopped the search first, so the verdict
    is *inconclusive* (the goal may lie just beyond the cap). ``elapsed_s`` is
    the wall-clock the search took. For a ``found=True`` result ``exhausted`` is
    ``False`` (the search stopped on the hit, not on emptiness).
    """

    model_config = ConfigDict(extra="forbid")

    found: bool
    goal: dict[str, Any]
    path: list[dict[str, Any]]
    depth: int
    nodes_explored: int
    exhausted: bool = False
    elapsed_s: float = 0.0


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
    def _progress_key(sess: HeadlessSession) -> tuple:
        """A *coarse* fingerprint keyed on narrative progression only.

        Where :meth:`_state_key` separates states that differ in any
        goal-readable scalar (affection points, coin counts, the clock), this
        key deliberately **collapses** all of those numeric dimensions and keys
        only on what drives *story* progression: location, the current scene +
        line, the chapter cursor, and the set of *truthy* flag names (the value
        is dropped — a flag is in or out). That makes the visited-set immune to
        the combinatorial fan-out of "+1 affection" / "advance a phase" churn,
        which is what makes a forward search over a large, time-and-affection
        gated pack tractable. The trade-off — it cannot prove a *quantitative*
        goal (``affection >= 50``) — is fine for the reachability question it
        serves: "can story progression organically reach this ending / chapter",
        where the relationship work is assumed satisfiable and seeded, and only
        the flag/chapter spine decides the answer.
        """
        st = sess.state
        flags = tuple(sorted(str(k) for k, v in st.events.flags.items() if v))
        return (
            st.map.current_location_id,
            st.story.current_scene,
            st.story.current_line_index,
            getattr(st, "current_chapter", None),
            flags,
        )

    @staticmethod
    def _presentation_kind(sess: HeadlessSession) -> str | None:
        pres = sess.last_presentation
        return pres.get("kind") if isinstance(pres, dict) else None

    def _examine_scenes(self, sess: HeadlessSession) -> list[str]:
        """Scene ids reachable by *examining* the current location.

        ``examine`` hooks are not auto-triggered by ``move`` (only ``enter`` /
        ``auto`` are), so a purely move/next/choose search can never reach the
        content behind them — yet "look around / interact here" is an ordinary
        player action, not a teleport. This enumerates the currently-available
        scene hooks whose ``trigger`` is ``"examine"`` so an *organic* search
        (one with ``start_scene`` disabled) can still open them. Returns [] on
        any error (degrade, never crash the search).
        """
        try:
            st = sess.state
            hooks = st.map.available_scenes(
                time_of_day=st.time.time_of_day.value,
                flags=st.events.flags,
                played_scenes=st.story.played,
                state=st,
            )
            return [h.scene_id for h in hooks
                    if getattr(h, "trigger", None) == "examine"
                    and h.scene_id is not None]
        except Exception:
            return []

    def _actions(self, sess: HeadlessSession, *, explore_moves: bool,
                 explore_scenes: bool, explore_examines: bool = True,
                 explore_time: bool = False) -> list[dict[str, Any]]:
        """Candidate ops from the current affordances + a ``next`` step.

        ``next`` is always offered unless the scene has already ended (advancing
        past an end is a no-op); the state-key dedup catches any that slip
        through as redundant.

        ``explore_examines`` adds ``examine`` (``start_scene`` of an available
        examine-trigger hook) — these read as organic player interaction, so
        they are offered even when ``explore_scenes`` (arbitrary ``start_scene``
        teleport) is off. ``explore_time`` offers an ``advance_time`` step, for
        searches over packs whose progression is gated on the clock.
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
        elif explore_examines:
            # Organic: open examine-trigger hooks here (not arbitrary scenes).
            for sid in self._examine_scenes(sess):
                actions.append({"op": "start_scene", "scene": sid})

        if self._presentation_kind(sess) != "end":
            actions.append({"op": "next", "count": 1})

        if explore_time:
            actions.append({"op": "advance_time", "phases": 1})

        return actions

    # ----- search --------------------------------------------------------

    def find_path(self, goal: dict[str, Any], *,
                  setup: list[dict[str, Any]] | None = None,
                  explore_moves: bool = True, explore_scenes: bool = True,
                  explore_examines: bool = True, explore_time: bool = False,
                  max_depth: int = 30, max_nodes: int = 4000,
                  time_budget_s: float | None = None,
                  coarse: bool = False) -> PlanResult:
        """BFS for a shortest op ``path`` that makes ``goal`` hold.

        Opens a session, runs ``setup`` to establish the start state, then
        searches outward. Each frontier entry pairs a ``snapshot`` with the
        ``path`` of ops that produced it; popping one restores the snapshot,
        checks the goal, and (on miss) expands the action space. A failing
        action simply yields no child.

        Three caps bound an unsatisfiable goal (or a combinatorial pack):
        ``max_nodes``, ``max_depth``, and an optional wall-clock
        ``time_budget_s``. On a ``found=False`` result the returned
        ``exhausted`` flag says *why* the search stopped: ``True`` ⇒ the
        frontier emptied (goal provably unreachable under the explored ops),
        ``False`` ⇒ a cap fired first (verdict inconclusive). The action
        vocabulary is controlled by ``explore_moves`` / ``explore_scenes``
        (arbitrary ``start_scene`` teleport) / ``explore_examines`` (organic
        examine-hook opens) / ``explore_time`` (``advance_time``).

        ``coarse`` selects the visited-set key: the default fine key
        (:meth:`_state_key`) separates every goal-readable scalar; ``coarse``
        uses the progression key (:meth:`_progress_key`) that collapses
        affection / inventory / resource / clock churn, which is what keeps a
        forward search over a large time-and-affection-gated pack from
        exploding. Use ``coarse`` for flag/chapter *reachability* questions (the
        relationship work is assumed satisfiable), not quantitative goals.
        """
        import time as _time

        key_of = self._progress_key if coarse else self._state_key
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
        start_key = key_of(sess)

        frontier: deque[tuple[dict[str, Any], list[dict[str, Any]]]] = deque()
        frontier.append((start_snapshot, []))
        visited: set[tuple] = {start_key}
        nodes_explored = 0
        started = _time.monotonic()
        capped = False  # a cap (nodes / time) stopped us before the frontier emptied

        while frontier:
            if nodes_explored >= max_nodes:
                capped = True
                break
            if time_budget_s is not None and \
                    (_time.monotonic() - started) >= time_budget_s:
                capped = True
                break
            snap, path = frontier.popleft()
            nodes_explored += 1

            sess.restore(snap)
            try:
                reached = bool(sess.assert_expect(goal).get("ok"))
            except Exception:
                reached = False
            if reached:
                return PlanResult(
                    found=True, goal=goal, path=path, depth=len(path),
                    nodes_explored=nodes_explored, exhausted=False,
                    elapsed_s=round(_time.monotonic() - started, 3))

            if len(path) >= max_depth:
                # A depth-capped node leaves states beyond the horizon
                # unexplored, so a subsequent empty frontier is not a true
                # exhaustion proof.
                capped = True
                continue

            # Expand from this node. ``_actions`` reads the restored state.
            for action in self._actions(
                    sess, explore_moves=explore_moves,
                    explore_scenes=explore_scenes,
                    explore_examines=explore_examines,
                    explore_time=explore_time):
                sess.restore(snap)
                try:
                    self._apply_action(sess, action)
                except Exception:
                    continue
                key = key_of(sess)
                if key in visited:
                    continue
                visited.add(key)
                child = sess.snapshot()
                frontier.append((child, path + [action]))

        # exhausted == the frontier emptied AND no cap (nodes/time/depth) ever
        # truncated the search — only then is found=False a real unreachability.
        return PlanResult(found=False, goal=goal, path=[], depth=0,
                          nodes_explored=nodes_explored,
                          exhausted=not capped,
                          elapsed_s=round(_time.monotonic() - started, 3))

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
        elif op == "advance_time":
            sess.advance_time(int(action.get("phases", 1)))
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
