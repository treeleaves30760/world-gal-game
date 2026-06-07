"""Soft-lock linter — every choice-bearing scene must offer a way forward.

A *soft-lock* is a choice point the player can reach with **no selectable
option and no fallthrough** — they are simply stuck. This is a recurring,
ship-able defect: the latest instance was a route climax whose two forks were
each hard-gated on a mutually-exclusive *stance* flag, so a player who arrived
having set neither (there was no third, always-available option) faced two
greyed buttons and could not proceed.

Runtime semantics this guard models (see
:meth:`world_gal_game.dialogue.dialogue_engine.DialogueEngine` choice
presentation):

- A choice is **selectable** iff all its ``requires`` hold and none of its
  ``forbids`` hit.
- A locked choice is dropped from the menu *only* when it is
  ``hidden_if_locked``; an ordinary locked choice still renders, greyed, with a
  lock reason.
- If — after dropping hidden-locked choices — the menu is **empty**, the scene
  falls through to ``on_end`` and a ``play_scene`` there continues play.
  Otherwise the scene **ends** with no transition.

So there are two soft-lock shapes:

1. **all-hidden, no fallthrough** — every choice is ``hidden_if_locked`` and a
   reachable state locks them all, emptying the menu, and the scene's ``on_end``
   has no ``play_scene`` to carry play forward → the scene silently ends with
   zero player agency. (The clearest catch.)
2. **all-locked-visible** — at least one choice is visible (not
   ``hidden_if_locked``) and a reachable state locks *every* choice → the menu
   shows only greyed buttons, no option is selectable, and because the menu is
   non-empty the ``on_end`` fallthrough never runs → the player is stuck. (The
   stance-bug shape.)

Approximating "reachable state". Following the same static over-approximation
the strand guard uses (:class:`world_gal_game.dev.reachability.StrandFixpoint`),
this linter favours **precision** — it reports only when it can *construct* a
concrete reachable all-locked witness:

- A choice with **no** ``requires`` and **no** ``forbids`` is always selectable
  → the scene can never be all-locked → never reported.
- A choice is treated as *lockable* only via its **flag** ``requires`` (kinds
  ``flag`` / ``flag_eq``). A choice gated solely on affection / items / chapter
  (or only via ``forbids``) is *not* proven lockable — affection bands and the
  like are usually satisfiable — so its presence makes the all-locked claim
  unprovable and the scene is **not** reported (conservative: no false
  positive).
- The witness assignment is "the player reached the scene having set **none**
  of the choice-gating flags". It is verified reachable by re-running the
  fixpoint with those flags **forbidden** (so no path sets them) and a
  satisfiable chapter/route seed; if the scene is still reachable, the witness
  is real and every flag-gated choice is locked in it.

This is a fast, static self-check stage. It never reports an affection-only or
unconditional menu, so the demo pack (whose menus always include an available
option) passes clean.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Condition kinds the linter can reason about as *flag* gates on a choice.
_FLAG_REQ = frozenset({"flag", "flag_eq"})


@dataclass
class SoftLock:
    """One potential soft-lock: a choice-bearing scene with a provable
    reachable all-locked state and no escape.

    ``shape`` is ``"all_hidden"`` (an empty menu that silently ends — no
    ``on_end`` ``play_scene``) or ``"all_locked_visible"`` (greyed buttons with
    no selectable option). ``locking_flags`` are the choice-gating flags that
    are unset in the witness state, and ``choices`` echoes each choice id with
    the flag gate that locks it, so the author sees exactly which guards collude.
    """

    scene_id: str
    shape: str
    detail: str
    file: str | None = None
    locking_flags: list[str] = field(default_factory=list)
    choices: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "shape": self.shape,
            "file": self.file,
            "locking_flags": self.locking_flags,
            "choices": self.choices,
            "detail": self.detail,
        }


class SoftLockChecker:
    """Run the soft-lock analysis over a pack's choice-bearing scenes.

    Construct with the pack root (or its ``content/`` dir). :meth:`check`
    loads the pack once and returns one :class:`SoftLock` per provable
    soft-lock.
    """

    def __init__(self, pack_root: Path | str) -> None:
        self.pack_root = Path(pack_root).resolve()
        if self.pack_root.name == "content":
            self.pack_root = self.pack_root.parent
        self._state: Any | None = None
        self._meta: dict[str, Any] = {}
        self._scene_files: dict[str, str] = {}

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
            if "intro_scene" in self._meta:
                state.meta.setdefault("__intro_scene__",
                                      self._meta["intro_scene"])
            self._scene_files = self._map_scene_files()
        return self._state

    @property
    def state(self) -> Any:
        return self._load()

    def _map_scene_files(self) -> dict[str, str]:
        """scene id -> relative source file (best-effort, for friendlier
        diagnostics). Raw-YAML walk; failures degrade to no file."""
        import yaml
        out: dict[str, str] = {}
        scenes_dir = self._content_root() / "scenes"
        if not scenes_dir.is_dir():
            return out
        for path in sorted(scenes_dir.rglob("*.yaml")):
            try:
                with open(path, encoding="utf-8") as fh:
                    data = yaml.safe_load(fh)
            except Exception:
                continue
            raw_scenes: list[Any] = []
            if isinstance(data, dict) and "scenes" in data:
                raw_scenes = data["scenes"] or []
            elif isinstance(data, list):
                raw_scenes = data
            elif isinstance(data, dict) and "id" in data:
                raw_scenes = [data]
            try:
                rel = str(path.relative_to(self.pack_root))
            except ValueError:
                rel = str(path)
            for s in raw_scenes:
                if isinstance(s, dict) and "id" in s:
                    out.setdefault(s["id"], rel)
        return out

    # -- condition / effect helpers ------------------------------------

    @staticmethod
    def _flag_reqs(choice: Any) -> list[tuple[str, str, Any]]:
        """The choice's flag-typed ``requires`` as (kind, target, value)."""
        out: list[tuple[str, str, Any]] = []
        for c in getattr(choice, "requires", None) or []:
            k = getattr(c, "kind", "") or ""
            t = getattr(c, "target", "") or ""
            if k in _FLAG_REQ and t:
                out.append((k, t, getattr(c, "value", None)))
        return out

    @staticmethod
    def _has_any_gate(choice: Any) -> bool:
        """Does the choice have any ``requires`` or ``forbids`` at all?"""
        return bool(getattr(choice, "requires", None)
                    or getattr(choice, "forbids", None))

    @staticmethod
    def _on_end_has_play_scene(scene: Any) -> bool:
        for eff in getattr(scene, "on_end", None) or []:
            if (getattr(eff, "kind", "") or "") == "play_scene":
                return True
        return False

    # -- route seeding (mirrors EndingReachabilityChecker) -------------

    def _declared_flags(self) -> set[str]:
        manifest = self.state.meta.get("__variables__")
        variables = getattr(manifest, "variables", None) or {}
        return set(variables.keys())

    def _route_lockin_flag(self, route: str | None,
                           declared: set[str]) -> str | None:
        if not route or route == "common":
            return None
        cand = f"route_{route}"
        return cand if cand in declared else None

    def _all_route_lockin_flags(self, declared: set[str]) -> set[str]:
        """Every ``route_<r>`` lock-in flag (for route exclusivity)."""
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
        flags |= {f for f in declared if f.startswith("route_")
                  and f != "route_chosen"}
        return flags

    def _seed_for_scene(self, scene: Any,
                        declared: set[str]) -> tuple[set[str], str | None, set[str]]:
        """(seed flags, seed chapter, route-exclusivity forbidden flags) for a
        scene, so the reachability witness is evaluated on the scene's own route.

        A route-tagged scene is checked with that route's lock-in seeded (and the
        other routes' lock-ins forbidden), matching how the strand guard seeds
        routes. An untagged / common scene is checked from a bare start.
        """
        seed: set[str] = set()
        for marker in ("intro_done", "prologue_done"):
            if marker in declared:
                seed.add(marker)
        route = getattr(scene, "route", None)
        lockin = self._route_lockin_flag(route, declared)
        if lockin:
            seed.add(lockin)
            if "route_chosen" in declared:
                seed.add("route_chosen")
        # Forbid the *other* routes' lock-ins (exclusivity); never forbid one we
        # seed.
        forbidden = self._all_route_lockin_flags(declared) - seed
        return seed, None, forbidden

    # -- the check ------------------------------------------------------

    def check(self) -> list[SoftLock]:
        """Return one :class:`SoftLock` per provable soft-lock."""
        from .reachability import StrandFixpoint

        state = self._load()
        scenes: dict[str, Any] = dict(getattr(state.story, "scenes", {}) or {})
        declared = self._declared_flags()
        fixpoint = StrandFixpoint(state)

        results: list[SoftLock] = []
        for sid in sorted(scenes):
            scene = scenes[sid]
            choices = list(getattr(scene, "choices", None) or [])
            if not choices:
                continue  # no menu, no soft-lock

            # Precision filter 1: an unconditional choice is always selectable.
            if any(not self._has_any_gate(ch) for ch in choices):
                continue

            # Precision filter 2: every choice must be lockable via a *flag*
            # requirement. A choice with no flag requires (only affection/item/
            # forbids gates) is not provably lockable → cannot prove all-locked.
            per_choice: list[tuple[Any, list[tuple[str, str, Any]]]] = []
            all_flag_lockable = True
            for ch in choices:
                freqs = self._flag_reqs(ch)
                if not freqs:
                    all_flag_lockable = False
                    break
                per_choice.append((ch, freqs))
            if not all_flag_lockable:
                continue

            # The witness: the player reaches this scene having set NONE of the
            # choice-gating flags. Defeat each choice via its first flag req
            # (``flag`` → must be false; ``flag_eq`` → must hold a value, and an
            # unset flag defaults away from it). Collect the flags to forbid.
            lock_flags: set[str] = set()
            choice_locks: list[dict[str, Any]] = []
            for ch, freqs in per_choice:
                kind, target, value = freqs[0]
                lock_flags.add(target)
                choice_locks.append({
                    "choice": getattr(ch, "id", "?"),
                    "locked_by": target,
                    "kind": kind,
                })

            # Verify the witness is reachable: re-run the fixpoint forbidding the
            # locking flags (so no path sets them) on this scene's route seed. If
            # the scene is still playable, a real all-locked path exists.
            seed, seed_chapter, route_forbidden = self._seed_for_scene(
                scene, declared)
            forbidden = route_forbidden | lock_flags
            # Never forbid a flag we also need to seed the route with.
            forbidden -= seed
            _flags, _chap, playable = fixpoint.closure_with_scenes(
                seed, seed_chapter, forbidden_flags=forbidden)
            if sid not in playable:
                # Either the scene is genuinely unreachable in this witness
                # (a gating flag is required to reach it too — so it can't be
                # reached locked), or unreachable at all. Either way, not a
                # provable soft-lock here.
                continue

            # Reachable AND every choice locked. Classify the escape shape.
            all_hidden = all(getattr(ch, "hidden_if_locked", False)
                             for ch in choices)
            if all_hidden:
                if self._on_end_has_play_scene(scene):
                    continue  # empty menu falls through to a real transition
                shape = "all_hidden"
                detail = (
                    "every choice is hidden_if_locked and a reachable state "
                    f"locks them all (flags unset: {sorted(lock_flags)}); the "
                    "menu empties and on_end has no play_scene fallthrough, so "
                    "the scene silently ends with no player agency")
            else:
                shape = "all_locked_visible"
                detail = (
                    "a reachable state locks every choice "
                    f"(flags unset: {sorted(lock_flags)}); the menu shows only "
                    "greyed options with no selectable choice and no "
                    "fallthrough, so the player is stuck")

            results.append(SoftLock(
                scene_id=sid, shape=shape, detail=detail,
                file=self._scene_files.get(sid),
                locking_flags=sorted(lock_flags),
                choices=choice_locks,
            ))
        return results
