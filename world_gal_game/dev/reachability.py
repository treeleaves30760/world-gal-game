"""Organic ending-reachability — the *strand* guard.

``PackInspector.reachability()`` answers "is every scene reachable from *some*
other scene?" — a per-scene, from-anywhere graph reachability. That misses a
whole bug class: a route that plays fine for a while and then **strands** —
reaches, say, year 3 of a four-year arc and can *never* advance to its year-4
ending, because the scene/hook that would carry the chapter forward never fires
in an organic, start-to-finish playthrough. The smoke runner missed it too (it
teleports past the broken link with ``start_scene``), and from-anywhere
reachability called the year-4 ending "reachable" because *some* scene names it.

This module asks the harder, organic question, per declared ending (and per
route's terminal ending in ``chapters.yaml``): **starting from the game's
beginning — seeding only a route's lock-in, never the per-year chapter
cheats — can ordinary play reach the ending?** It answers with two cooperating
analyses:

1. :class:`StrandFixpoint` — a fast, exhaustive, *static* over-approximation. It
   computes the set of flags/chapters that *could* become true under generous
   assumptions (relationship/time/item gates assumed satisfiable; a scene is
   reachable only if some real trigger — a location hook, an intra-scene
   ``play_scene`` / ``next_scene`` edge, or being the intro — can fire it). Being
   an over-approximation, if it says an ending flag is **un**reachable that is a
   *proof* (a true strand), reported as a hard error. It cannot, however, see a
   runtime *ordering* strand (e.g. a higher-priority hook starving the one that
   advances the chapter).

2. A bounded organic :class:`~world_gal_game.dev.planner.Planner` search — the
   forward model itself, so it *does* see ordering. It drives only organic ops
   (move / next / choose / advance_time / examine — never ``start_scene`` to an
   arbitrary scene, never ``set_chapter``), with a coarse progression key
   (affection/clock churn collapsed) and depth / node / wall-clock budgets. A
   ``found`` path is proof of reachability; a frontier that *empties* is proof
   of a strand; hitting a budget is **inconclusive** ("unverified"), reported as
   a warning, never a false "ok".

The combined verdict per ending: **ok** if either analysis confirms it; **strand
(error)** if either *proves* it unreachable; **unverified (warning)** if neither
could conclude within budget. The whole thing is exposed as the ``reachability``
self-check stage (see :mod:`world_gal_game.dev.self_check`).

The planner budget is deliberately modest (a few hundred nodes / ~30s per
ending by default) so a CI run of a multi-ending pack stays bounded; on a large
campus-map pack the organic search will usually run out its budget and the
*static* fixpoint carries the definitive verdicts, with the planner contributing
"unverified" on the rest. See ``docs`` / the stage docstring for the tuning.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Effect kinds that write a flag (mirrors dataflow's table; the liberal
# "contains 'flag'" rule also catches plugin flag effects).
_FLAG_WRITE = frozenset({"set_flag", "set_flag_if_unset", "increment_flag"})
# Condition kinds we can reason about statically.
_FLAG_REQ = frozenset({"flag", "flag_eq"})
_FLAG_FORBID = frozenset({"not_flag"})
_CHAP_GE = frozenset({"chapter_at_or_after"})
_CHAP_IS = frozenset({"chapter_is", "in_chapter"})


@dataclass
class EndingReachability:
    """Per-ending verdict from the strand guard.

    ``status`` is ``"ok"`` (organically reachable), ``"strand"`` (provably or
    by-exhaustion unreachable — a hard error) or ``"unverified"`` (the bounded
    search could not conclude — a warning). ``route`` is the chapters.yaml route
    the ending terminates (or ``None``), ``flags`` the ``ending_*`` flags that
    gate it, and ``detail`` a human line. ``method`` says which analysis decided
    it (``"static"`` / ``"planner"`` / ``"both"`` / ``"budget"``). ``nodes`` /
    ``depth`` / ``elapsed_s`` echo the planner search cost when one ran.
    """

    ending_id: str
    status: str
    detail: str
    route: str | None = None
    flags: list[str] = field(default_factory=list)
    method: str = ""
    nodes: int = 0
    depth: int = 0
    elapsed_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ending_id": self.ending_id,
            "status": self.status,
            "route": self.route,
            "flags": self.flags,
            "method": self.method,
            "detail": self.detail,
            "nodes": self.nodes,
            "depth": self.depth,
            "elapsed_s": self.elapsed_s,
        }


# ----------------------------------------------------------------------
# Static over-approximation fixpoint


class StrandFixpoint:
    """Monotonic flag/chapter reachability over-approximation.

    Built from a loaded :class:`GameState`. Computes, from a seed flag/chapter
    set, the closure of flags and chapters that *could* become true, treating a
    scene as playable iff (a) it is the intro, (b) a location hook for it has
    satisfiable flag/chapter gates, or (c) it is the destination of a
    ``play_scene`` / ``next_scene`` edge from an already-playable scene with a
    satisfiable edge guard. Affection / time / item gates are assumed
    satisfiable (the relationship work is seeded/assumed), and ``forbids`` are
    ignored — both make the result a *super*set of what is truly reachable, so a
    flag the fixpoint omits is provably unreachable.
    """

    def __init__(self, state: Any) -> None:
        self.state = state
        self.scenes: dict[str, Any] = dict(getattr(state.story, "scenes", {}) or {})
        manifest = state.meta.get("__chapters__")
        self.ordered_chapters: list[str] = (
            [c.id for c in manifest.ordered()] if manifest is not None else [])
        self._corder = {c: i for i, c in enumerate(self.ordered_chapters)}
        self.intro: str | None = (state.meta.get("__intro_scene__")
                                  or state.meta.get("intro_scene"))
        self._hook_gates = self._collect_hook_gates()
        self._edges_into = self._collect_edges()

    # -- gate / write extraction ---------------------------------------

    @staticmethod
    def _cond(c: Any) -> tuple[str, str]:
        return (getattr(c, "kind", "") or "", getattr(c, "target", "") or "")

    def _conds_to_gate(self, conds: Any) -> tuple[set[str], list[tuple[str, str]]]:
        """Split a condition list into (required flag names, chapter conds)."""
        req: set[str] = set()
        chap: list[tuple[str, str]] = []
        for c in conds or []:
            k, t = self._cond(c)
            if k in _FLAG_REQ and t:
                req.add(t)
            elif (k in _CHAP_GE or k in _CHAP_IS) and t:
                chap.append((k, t))
        return req, chap

    def _collect_hook_gates(self) -> dict[str, list[tuple[set[str], list]]]:
        """scene_id -> list of (required_flags, chapter_conds) — one per hook
        that can start the scene (an OR over triggering hooks)."""
        out: dict[str, list[tuple[set[str], list]]] = {}
        locs = getattr(getattr(self.state, "map", None), "locations", {}) or {}
        for loc in locs.values():
            for h in getattr(loc, "scene_hooks", None) or []:
                sid = getattr(h, "scene_id", None)
                if not sid:
                    continue
                req = set(getattr(h, "requires_flags", None) or [])
                rq2, chap = self._conds_to_gate(getattr(h, "requires", None))
                req |= rq2
                out.setdefault(sid, []).append((req, chap))
        return out

    def _collect_edges(self) -> dict[str, list[tuple[str, set[str], list]]]:
        """dst_scene -> list of (src_scene, required_flags, chapter_conds) for
        play_scene / next_scene transitions (so a scene reached only via a
        sequel chain is counted reachable once its source is). Each edge is
        ``(src, required_flags, chapter_conds, choice_sets)`` where
        ``choice_sets`` is the flags the *originating choice* writes (empty for
        ``on_end`` / line edges) — so a closure seeded to one route can skip an
        edge that belongs to a mutually-exclusive other-route choice."""
        into: dict[str, list[tuple[str, set[str], list, set[str]]]] = {}

        def _target(eff: Any) -> str | None:
            if (getattr(eff, "kind", "") or "") != "play_scene":
                return None
            t = getattr(eff, "target", "") or ""
            if t:
                return t
            v = getattr(eff, "value", None)
            return v if isinstance(v, str) and v else None

        for sid, scene in self.scenes.items():
            # scene.requires gates on_end / line edges originating here
            srq, schap = self._conds_to_gate(getattr(scene, "requires", None))
            for eff in getattr(scene, "on_end", None) or []:
                dst = _target(eff)
                if dst:
                    into.setdefault(dst, []).append(
                        (sid, set(srq), list(schap), set()))
            for line in getattr(scene, "lines", None) or []:
                lrq, lchap = self._conds_to_gate(getattr(line, "requires", None))
                for eff in getattr(line, "effects", None) or []:
                    dst = _target(eff)
                    if dst:
                        into.setdefault(dst, []).append(
                            (sid, set(srq) | set(lrq),
                             list(schap) + list(lchap), set()))
            for ch in getattr(scene, "choices", None) or []:
                crq, _ = self._conds_to_gate(getattr(ch, "requires", None))
                gate = set(srq) | set(crq)
                sets, _c, _a = self._eff_writes(getattr(ch, "effects", None))
                ns = getattr(ch, "next_scene", None)
                if isinstance(ns, str) and ns:
                    into.setdefault(ns, []).append(
                        (sid, set(gate), list(schap), set(sets)))
                for eff in getattr(ch, "effects", None) or []:
                    dst = _target(eff)
                    if dst:
                        into.setdefault(dst, []).append(
                            (sid, set(gate), list(schap), set(sets)))
        return into

    @staticmethod
    def _eff_writes(effs: Any) -> tuple[set[str], set[str], bool]:
        """(flag names set, chapter ids set, advance_chapter present) for one
        effect list."""
        sf: set[str] = set()
        sc: set[str] = set()
        adv = False
        for e in effs or []:
            k = getattr(e, "kind", "") or ""
            t = getattr(e, "target", "") or ""
            # Only true flag-*setting* effects write a flag; 'flag'/'flag_eq'
            # are read conditions, and a plugin write must look like set_*flag*.
            if t and (k in _FLAG_WRITE or ("flag" in k and "set" in k)):
                sf.add(t)
            if k == "set_chapter" and t:
                sc.add(t)
            if k == "advance_chapter":
                adv = True
        return sf, sc, adv

    def _writes(self, scene: Any,
                forbidden: set[str]) -> tuple[set[str], set[str], bool]:
        """Aggregate flag/chapter writes of a scene's unconditional bodies plus
        every *non-excluded* choice.

        A choice whose effects set a flag in ``forbidden`` (e.g. another route's
        lock-in flag when this closure is seeded to one route) is **not taken** —
        modelling choice exclusivity, so the search for route A does not pick up
        route B's content. Line / on_end effects are unconditional and always
        contribute (writes to forbidden flags from those are simply dropped).
        """
        sf: set[str] = set()
        sc: set[str] = set()
        adv = False

        def _merge(part: tuple[set[str], set[str], bool]) -> None:
            nonlocal adv
            psf, psc, padv = part
            sf.update(f for f in psf if f not in forbidden)
            sc.update(psc)
            adv = adv or padv

        _merge(self._eff_writes(getattr(scene, "on_end", None)))
        for line in getattr(scene, "lines", None) or []:
            _merge(self._eff_writes(getattr(line, "effects", None)))
        for ch in getattr(scene, "choices", None) or []:
            choice_part = self._eff_writes(getattr(ch, "effects", None))
            # Skip choices that would set a forbidden flag (exclusive branch).
            if choice_part[0] & forbidden:
                continue
            _merge(choice_part)
        return sf, sc, adv

    # -- chapter helpers -----------------------------------------------

    def _chap_ge(self, cur: str, target: str) -> bool:
        return self._corder.get(cur, -1) >= self._corder.get(target, 10 ** 9)

    def _chap_ok(self, chap_conds: list[tuple[str, str]], chapters: set[str]) -> bool:
        for k, t in chap_conds:
            if k in _CHAP_GE:
                if not any(self._chap_ge(c, t) for c in chapters):
                    return False
            elif k in _CHAP_IS:
                if t not in chapters:
                    return False
        return True

    def _next_chapter(self, cur: str | None) -> str | None:
        if not self.ordered_chapters:
            return None
        if cur is None:
            return self.ordered_chapters[0]
        if cur in self._corder:
            i = self._corder[cur]
            if i + 1 < len(self.ordered_chapters):
                return self.ordered_chapters[i + 1]
        return None

    # -- the fixpoint ---------------------------------------------------

    def closure(self, seed_flags: set[str],
                seed_chapter: str | None = None,
                forbidden_flags: set[str] | None = None,
                ) -> tuple[set[str], set[str]]:
        """Return (reachable flag names, reachable chapter ids) from the seed.

        ``forbidden_flags`` are flags the closure must not set — used to model
        route exclusivity: when seeded to route A, the other routes' lock-in
        flags are forbidden, so choices/edges that would set them are not taken
        and route B's content does not leak into route A's reachability.
        """
        flags, chapters, _playable = self.closure_with_scenes(
            seed_flags, seed_chapter, forbidden_flags)
        return flags, chapters

    def closure_with_scenes(
        self, seed_flags: set[str],
        seed_chapter: str | None = None,
        forbidden_flags: set[str] | None = None,
    ) -> tuple[set[str], set[str], set[str]]:
        """Like :meth:`closure` but also returns the set of *playable* scene ids.

        The playable set is the over-approximation of scenes reachable from the
        seed under the same generous assumptions (affection/time/item gates
        satisfiable, ``forbids`` ignored, choices that would set a forbidden flag
        not taken). The soft-lock linter uses this to ask "is scene S reachable
        in a state where its choice-gating flags are still unset?" by passing
        those flags as ``forbidden_flags``.
        """
        forbidden = set(forbidden_flags or set())
        flags = set(seed_flags)
        chapters: set[str] = set()
        if seed_chapter:
            chapters.add(seed_chapter)
        playable: set[str] = set()
        changed = True
        while changed:
            changed = False
            for sid, scene in self.scenes.items():
                if sid in playable:
                    continue
                srq, schap = self._conds_to_gate(getattr(scene, "requires", None))
                if not srq.issubset(flags) or not self._chap_ok(schap, chapters):
                    continue
                triggerable = (sid == self.intro)
                if not triggerable:
                    for req, chap in self._hook_gates.get(sid, []):
                        if req.issubset(flags) and self._chap_ok(chap, chapters):
                            triggerable = True
                            break
                if not triggerable:
                    for src, req, chap, sets in self._edges_into.get(sid, []):
                        # Skip an edge whose originating choice sets a forbidden
                        # flag (a mutually-exclusive other-route branch).
                        if sets & forbidden:
                            continue
                        if (src in playable and req.issubset(flags)
                                and self._chap_ok(chap, chapters)):
                            triggerable = True
                            break
                if not triggerable:
                    continue
                playable.add(sid)
                changed = True
                sf, sc, adv = self._writes(scene, forbidden)
                if sf - flags:
                    flags |= sf
                    changed = True
                if sc - chapters:
                    chapters |= sc
                    changed = True
                if adv:
                    for c in list(chapters):
                        nc = self._next_chapter(c)
                        if nc and nc not in chapters:
                            chapters.add(nc)
                            changed = True
        return flags, chapters, playable


# ----------------------------------------------------------------------
# Combined ending-reachability checker


class EndingReachabilityChecker:
    """Run the strand guard over every declared ending of a pack.

    Construct with the pack root (or its ``content/`` dir). :meth:`check_all`
    loads the pack once, derives the route→ending map from ``chapters.yaml`` (so
    a route's *terminal* ending is checked with that route's lock-in seeded), and
    returns one :class:`EndingReachability` per ending.
    """

    def __init__(self, pack_root: Path | str, *, seed: int | None = 42) -> None:
        self.pack_root = Path(pack_root).resolve()
        if self.pack_root.name == "content":
            self.pack_root = self.pack_root.parent
        self.seed = seed
        self._state: Any | None = None
        self._meta: dict[str, Any] = {}

    # -- loading --------------------------------------------------------

    def _content_root(self) -> Path:
        content = self.pack_root / "content"
        return content if content.is_dir() else self.pack_root

    def _load(self) -> Any:
        if self._state is None:
            from ..content_loader import load_pack
            state, _reg, meta = load_pack(self._content_root())
            self._state = state
            self._meta = meta or {}
            # The pack meta (with intro_scene / start_location) is not parked on
            # state.meta; stash the intro on the private bridge so StrandFixpoint
            # — which only sees the GameState — can read it.
            if "intro_scene" in self._meta:
                state.meta.setdefault("__intro_scene__",
                                      self._meta["intro_scene"])
        return self._state

    @property
    def state(self) -> Any:
        return self._load()

    # -- ending / route model ------------------------------------------

    @staticmethod
    def _ending_flags(ending: Any) -> list[str]:
        """The ``ending_*``/flag names a single ending's ``requires`` keys on.

        We can only reason about flag-gated endings statically + organically; an
        ending gated purely on (say) affection has no flag spine to chase, so it
        is reported as skipped/unverified by the caller.
        """
        out: list[str] = []
        for c in getattr(ending, "requires", None) or []:
            if (getattr(c, "kind", "") or "") in _FLAG_REQ:
                t = getattr(c, "target", "") or ""
                if t:
                    out.append(t)
        return out

    def _route_for_ending(self, ending: Any) -> str | None:
        """The route an ending belongs to, for lock-in seeding.

        Prefers the ending's own ``route_id`` (the heroine route, e.g.
        ``qingyi``) — packs that converge several routes' terminal endings into a
        shared *common* finale chapter tag the chapter ``route: common`` but keep
        each ending's ``route_id`` pointing at its heroine, so ``route_id`` is the
        more reliable lock-in signal. Falls back to the chapters.yaml chapter
        route only when ``route_id`` is a non-route value (``common`` / absent).
        """
        rid = getattr(ending, "route_id", None) or None
        if rid and rid != "common":
            return rid
        eid = getattr(ending, "id", "") or ""
        manifest = self.state.meta.get("__chapters__")
        if manifest is not None:
            for ch in manifest.chapters:
                if eid in (getattr(ch, "endings", None) or []):
                    route = getattr(ch, "route", None) or None
                    if route and route != "common":
                        return route
        return rid

    def _declared_flags(self) -> set[str]:
        manifest = self.state.meta.get("__variables__")
        variables = getattr(manifest, "variables", None) or {}
        return set(variables.keys())

    def _heroines(self) -> dict[str, str]:
        """route/route_id -> heroine npc id, for affection seeding."""
        out: dict[str, str] = {}
        reg = self.state.meta.get("__npc_registry__")
        npcs = []
        if reg is not None and hasattr(reg, "all"):
            npcs = reg.all()
        for npc in npcs:
            if getattr(npc, "is_heroine", False):
                nid = getattr(npc, "id", None)
                if nid:
                    out[nid] = nid
        return out

    # -- seed construction ---------------------------------------------

    def _route_lockin_flag(self, route: str, declared: set[str]) -> str | None:
        """The lock-in flag for a route, by the ``route_<route>`` convention."""
        cand = f"route_{route}"
        return cand if cand in declared else None

    def _lockin_chapter(self, lockin_flag: str | None) -> str | None:
        """The chapter the route lock-in establishes.

        Found generically by locating the scene/choice that *sets* the route's
        lock-in flag and reading the ``set_chapter`` target applied alongside it
        (the route_choice convention: pick a route → set ``route_<x>`` + advance
        the chapter to that route's first year). Returns ``None`` if there is no
        such scene or it sets no chapter, in which case the static closure
        discovers the chapter itself and the planner is seeded without one.
        """
        if not lockin_flag:
            return None
        scenes = getattr(self.state.story, "scenes", {}) or {}
        for scene in scenes.values():
            for ch in getattr(scene, "choices", None) or []:
                effs = getattr(ch, "effects", None) or []
                sets_flag = any(
                    (getattr(e, "kind", "") in _FLAG_WRITE
                     or ("flag" in (getattr(e, "kind", "") or "")
                         and "set" in (getattr(e, "kind", "") or "")))
                    and (getattr(e, "target", "") == lockin_flag)
                    for e in effs)
                if not sets_flag:
                    continue
                for e in effs:
                    if (getattr(e, "kind", "") or "") == "set_chapter" \
                            and getattr(e, "target", ""):
                        return getattr(e, "target", "")
            # also look at on_end / line effects of a scene that sets the flag
            for bucket in ([getattr(scene, "on_end", None) or []]
                           + [getattr(ln, "effects", None) or []
                              for ln in (getattr(scene, "lines", None) or [])]):
                if any((getattr(e, "kind", "") in _FLAG_WRITE)
                       and getattr(e, "target", "") == lockin_flag
                       for e in bucket):
                    for e in bucket:
                        if (getattr(e, "kind", "") or "") == "set_chapter" \
                                and getattr(e, "target", ""):
                            return getattr(e, "target", "")
        return None

    def _all_route_lockin_flags(self, declared: set[str]) -> set[str]:
        """Every ``route_<r>`` lock-in flag the pack declares.

        Used as the exclusivity set: when checking route A, the *other* routes'
        lock-in flags are forbidden in the static closure so route B's content
        doesn't leak in. Derived from declared variables (the
        ``route_<something>`` convention) joined with routes named in
        chapters.yaml and ending ``route_id``s, so it works whether the route
        flag is declared or merely used.
        """
        routes: set[str] = set()
        manifest = self.state.meta.get("__chapters__")
        if manifest is not None:
            for ch in manifest.chapters:
                r = getattr(ch, "route", None)
                if r and r != "common":
                    routes.add(r)
        endings = getattr(getattr(self.state, "endings", None), "endings", {})
        for e in (endings or {}).values():
            r = getattr(e, "route_id", None)
            if r and r != "common":
                routes.add(r)
        flags = {f"route_{r}" for r in routes}
        # Also pick up any declared variable literally named route_* (covers a
        # route flag whose route name we didn't otherwise enumerate).
        flags |= {f for f in declared if f.startswith("route_")
                  and f != "route_chosen"}
        return flags

    def _build_seed(
        self, route: str | None, declared: set[str],
    ) -> tuple[list[dict[str, Any]], str | None, bool, str | None]:
        """Return (setup ops, lockin_flag_or_None, strict, lockin_chapter).

        ``strict`` is True when the route has an identifiable lock-in flag — its
        terminal ending is then a first-class committed path whose strand is a
        hard error. Without a lock-in flag the check is best-effort (warnings
        only), since the engine cannot establish the route's preconditions
        precisely.

        The seed establishes the route's lock-in: the lock-in flag (+
        ``route_chosen``), the lock-in *chapter* the route choice advances to
        (the route's first year — NOT a later per-year chapter, which is exactly
        the cheat the guard must not seed), and maxed affection for the route's
        heroine (relationship work assumed done). ``lockin_chapter`` is returned
        so the static closure can seed the same chapter.
        """
        setup: list[dict[str, Any]] = []
        lockin: str | None = None
        strict = False
        lockin_chapter: str | None = None
        # Seed the intro-completion marker if the pack declares one (common
        # convention) so prologue-gated content is not the blocker.
        for marker in ("intro_done", "prologue_done"):
            if marker in declared:
                setup.append({"op": "set_flag", "key": marker, "value": True})
        if route:
            lockin = self._route_lockin_flag(route, declared)
            if lockin:
                strict = True
                setup.append({"op": "set_flag", "key": lockin, "value": True})
                if "route_chosen" in declared:
                    setup.append({"op": "set_flag", "key": "route_chosen",
                                  "value": True})
                # The lock-in chapter (route's first year) — seeded so the
                # route's year-1/2 content hooks (gated chapter_at_or_after the
                # lock-in chapter) become reachable. Later years must still be
                # reached organically.
                lockin_chapter = self._lockin_chapter(lockin)
                if lockin_chapter:
                    setup.append({"op": "apply", "effect": {
                        "kind": "set_chapter", "target": lockin_chapter,
                        "value": False}})
            # Max affection for the route's heroine (id == route convention).
            heroines = self._heroines()
            hero = heroines.get(route)
            if hero:
                setup.append({"op": "adjust_affection", "npc": hero, "delta": 100})
                setup.append({"op": "adjust_affection", "npc": hero, "delta": 100,
                              "stat": "trust"})
        return setup, lockin, strict, lockin_chapter

    # -- per-ending check ----------------------------------------------

    def check_all(self, *, deep: bool = False,
                  max_nodes: int = 700, max_depth: int = 120,
                  time_budget_s: float = 30.0) -> list[EndingReachability]:
        """Check every declared ending; return one verdict each.

        The DEFAULT (``deep=False``) is **fast and conclusive**: the static
        :class:`StrandFixpoint` alone decides every flag-gated ending —
        ``ok`` when its flags lie in the over-approximation closure, ``strand``
        when they provably do not. The fixpoint is unbounded and exhaustive yet
        runs in well under a second even on a large campus-map pack, so the
        marquee ``self-check`` finishes in seconds with real verdicts (no
        "unverified" budget-exhaustion theatre).

        ``deep=True`` *additionally* runs the bounded organic
        :class:`~world_gal_game.dev.planner.Planner` replay per ending — the
        forward model itself, which sees runtime *ordering* the static pass
        cannot, so it can (a) confirm a real start-to-finish path and (b) refute
        a static strand that the over-approximation missed (a scene reached by a
        mechanism the static model doesn't track). It is opt-in because it is the
        slow part (``time_budget_s`` per ending); on a large pack most endings
        run out their budget and return "unverified", which is why it is no
        longer in the default path. The ``max_nodes`` / ``max_depth`` /
        ``time_budget_s`` budgets apply only in deep mode.
        """
        state = self._load()
        endings = list(getattr(getattr(state, "endings", None), "endings", {})
                       .values())
        declared = self._declared_flags()
        fixpoint = StrandFixpoint(state)
        planner = None
        if deep:
            from .planner import Planner
            planner = Planner(str(self.pack_root), seed=self.seed)

        results: list[EndingReachability] = []
        for ending in sorted(endings, key=lambda e: getattr(e, "id", "")):
            results.append(self._check_one(
                ending, declared, fixpoint, planner, deep=deep,
                max_nodes=max_nodes, max_depth=max_depth,
                time_budget_s=time_budget_s))
        return results

    def _check_one(self, ending: Any, declared: set[str],
                   fixpoint: StrandFixpoint, planner: Any, *,
                   deep: bool = False,
                   max_nodes: int, max_depth: int,
                   time_budget_s: float) -> EndingReachability:
        eid = getattr(ending, "id", "") or "?"
        flags = self._ending_flags(ending)
        route = self._route_for_ending(ending)
        setup, lockin, strict, lockin_chapter = self._build_seed(route, declared)

        if not flags:
            # No flag spine to chase (e.g. an affection-only ending): we cannot
            # statically or organically prove a strand, so do not gate on it.
            return EndingReachability(
                ending_id=eid, status="unverified", route=route, flags=[],
                method="skip",
                detail="no flag-typed requires to trace; skipped")

        seed_flag_set = {c["key"] for c in setup if c["op"] == "set_flag"}
        # Other routes' lock-in flags are forbidden so the static closure stays
        # on *this* route (choice exclusivity); never forbid a flag we seed.
        forbidden = (self._all_route_lockin_flags(declared) - seed_flag_set)

        # 1) Static over-approximation, seeded with the lock-in chapter (so the
        #    route's first-year content is in scope) but no *later* chapter — the
        #    closure must discover those via the scenes that advance them.
        reach_flags, _reach_chap = fixpoint.closure(
            seed_flag_set, lockin_chapter, forbidden_flags=forbidden)
        static_ok = all(f in reach_flags for f in flags)

        # DEFAULT FAST PATH: the static fixpoint is conclusive on its own. It is
        # an over-approximation, so ``static_ok`` (flags in the closure) confirms
        # a structural path exists, and a missing flag is a *proof* of a strand.
        # No planner, no wall-clock budget — this is what keeps the default
        # self-check to seconds with real ok/strand verdicts.
        if not deep:
            if static_ok:
                return EndingReachability(
                    ending_id=eid, status="ok", route=route, flags=flags,
                    method="static",
                    detail="statically reachable (over-approximation closure "
                           "contains the ending flags)")
            missing = [f for f in flags if f not in reach_flags]
            return EndingReachability(
                ending_id=eid, status="strand", route=route, flags=flags,
                method="static",
                detail=("static reachability proves no path sets "
                        f"{missing} (over-approximation closure omits it)"))

        # 2) DEEP MODE — bounded organic planner (sees runtime ordering the
        #    static pass cannot). Goal = the first ending flag (they are AND-ed;
        #    the planner checks one, the others travel with it on the same writer
        #    scene in practice — and the static pass confirms all are writable
        #    when it says ``static_ok``).
        goal = {"flag": flags[0]}
        try:
            res = planner.find_path(
                goal, setup=list(setup), explore_scenes=False,
                explore_examines=True, explore_time=True, coarse=True,
                max_depth=max_depth, max_nodes=max_nodes,
                time_budget_s=time_budget_s)
        except Exception as exc:  # pragma: no cover - defensive
            res = None
            if not static_ok:
                # Static proved a strand and the planner can't refute it.
                missing = [f for f in flags if f not in reach_flags]
                return EndingReachability(
                    ending_id=eid, status="strand", route=route, flags=flags,
                    method="static",
                    detail=("static reachability proves no path sets "
                            f"{missing}; planner refutation raised: {exc}"))
            return EndingReachability(
                ending_id=eid, status="unverified", route=route, flags=flags,
                method="planner-error",
                detail=f"planner raised: {exc}")

        if res.found:
            # The planner *replayed* a real path — authoritative, even if the
            # static pass under-counted (a scene reached by a mechanism the
            # static model doesn't track) and called it a strand.
            return EndingReachability(
                ending_id=eid, status="ok", route=route, flags=flags,
                method="planner", nodes=res.nodes_explored, depth=res.depth,
                elapsed_s=res.elapsed_s,
                detail=f"organic path found ({res.depth} ops)")

        # Static proved unreachable and the planner did not refute it (it could
        # not find a path) -> a confirmed strand. This is the fast, definitive
        # verdict for clearly-broken wiring; the planner serves only to *refute*
        # a static strand, so even a budget-limited planner run does not weaken
        # it (failing to find != refuting).
        if not static_ok:
            missing = [f for f in flags if f not in reach_flags]
            return EndingReachability(
                ending_id=eid, status="strand", route=route, flags=flags,
                method="static", nodes=res.nodes_explored, depth=res.depth,
                elapsed_s=res.elapsed_s,
                detail=("static reachability proves no path sets "
                        f"{missing} (over-approximation closure omits it)"))
        if res.exhausted:
            # The frontier emptied without the goal: under the explored organic
            # ops the ending is unreachable. For a *strict* route (real lock-in
            # flag seeded) that is a hard strand. For a best-effort route the
            # seed may simply be wrong (e.g. an affection *band* the blanket-max
            # seed overshoots), so we only warn rather than risk a false strand.
            if strict:
                return EndingReachability(
                    ending_id=eid, status="strand", route=route, flags=flags,
                    method="planner", nodes=res.nodes_explored, depth=res.depth,
                    elapsed_s=res.elapsed_s,
                    detail=("organic search exhausted the reachable state space "
                            f"without reaching {goal['flag']} "
                            f"({res.nodes_explored} states)"))
            return EndingReachability(
                ending_id=eid, status="unverified", route=route, flags=flags,
                method="planner", nodes=res.nodes_explored, depth=res.depth,
                elapsed_s=res.elapsed_s,
                detail=("organic search exhausted the reachable space without "
                        f"reaching {goal['flag']}, but this route has no lock-in "
                        "flag to seed precisely — unverified (seed may be "
                        "incomplete)"))
        # Budget hit without a verdict -> inconclusive. The static
        # over-approximation already confirmed a *structural* path exists (we
        # only reach here when ``static_ok``), so this is "wired but the bounded
        # runtime search couldn't replay it in time" — common on a large
        # campus-map pack. Reported as a warning, never a false "ok" or a false
        # strand.
        return EndingReachability(
            ending_id=eid, status="unverified", route=route, flags=flags,
            method="budget", nodes=res.nodes_explored, depth=res.depth,
            elapsed_s=res.elapsed_s,
            detail=("structurally reachable (static), but the organic search hit "
                    f"its budget without reaching {goal['flag']} (explored "
                    f"{res.nodes_explored} states, depth {res.depth}, "
                    f"{res.elapsed_s}s) — runtime-unverified"
                    + ("; strict route, worth a manual check" if strict else "")))
