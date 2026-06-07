"""Tests for the soft-lock linter — Guard 2.

A *soft-lock* is a choice point the player can reach with no selectable option
and no fallthrough. The decisive fixture reproduces the real shipped defect: a
route climax whose two forks are each hard-gated on a mutually-exclusive stance
flag, with no third always-available option — so a player who arrives having set
neither stance faces two greyed buttons and is stuck.

- The deliberately-broken fixture (climax reachable on the route *without* a
  stance scene on the path) must be flagged as an ``all_locked_visible``
  soft-lock.
- The clean twin (a stance-setting scene with always-available choices on the
  only path to the climax) must NOT be flagged — proving the linter does not
  false-positive when an option is always available on the way in.

Plus the all-hidden shape (every choice ``hidden_if_locked`` with no ``on_end``
fallthrough) and the precision filters (an unconditional choice; an
affection-only gate; a real fallthrough).
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from world_gal_game.dev.softlock import SoftLockChecker
from world_gal_game.dev.self_check import SelfCheck


# ----------------------------------------------------------------------
# Fixture builders


_META = """
    pack_format_version: "0.1"
    title: "SoftLock Fixture"
    start_location: lab
    intro_scene: intro
"""

_CHARACTERS = """
    characters:
      - {id: yuening, name: "沈月凝", is_heroine: true, route_id: yuening}
"""

_VARIABLES = """
    variables:
      - {key: intro_done, type: bool, default: false, category: progress}
      - {key: route_chosen, type: bool, default: false, category: progress}
      - {key: route_yuening, type: bool, default: false, category: progress}
      - {key: stance_together, type: bool, default: false, category: progress}
      - {key: stance_alone, type: bool, default: false, category: progress}
      - {key: arc_done, type: bool, default: false, category: progress}
"""


def _write_common(content: Path, locations: str, scenes: str) -> None:
    (content / "scenes").mkdir(parents=True)
    (content / "meta.yaml").write_text(textwrap.dedent(_META), encoding="utf-8")
    (content / "characters.yaml").write_text(
        textwrap.dedent(_CHARACTERS), encoding="utf-8")
    (content / "variables.yaml").write_text(
        textwrap.dedent(_VARIABLES), encoding="utf-8")
    (content / "locations.yaml").write_text(locations, encoding="utf-8")
    (content / "scenes" / "s.yaml").write_text(scenes, encoding="utf-8")


# --- the stance soft-lock (broken): climax reachable with no stance set ----

_LOC_BROKEN = """
locations:
  - id: lab
    name: Lab
    exits: []
    scene_hooks:
      - {scene_id: route_choice, trigger: auto, requires_flags: [intro_done], forbids_flags: [route_chosen]}
      - {scene_id: climax, trigger: auto, requires_flags: [route_yuening]}
"""

_SCENES_BROKEN = """
scenes:
  - id: intro
    title: I
    location: lab
    lines: [{text: begin}]
    on_end: [{kind: set_flag, target: intro_done, value: true}]
  - id: route_choice
    title: C
    location: lab
    lines: [{text: pick}]
    choices:
      - id: pick_yuening
        text: yuening
        effects:
          - {kind: set_flag, target: route_chosen, value: true}
          - {kind: set_flag, target: route_yuening, value: true}
        next_scene: climax
  - id: climax
    title: Climax
    location: lab
    route: yuening
    lines: [{text: climax}]
    choices:
      - id: turn_with_her
        text: together
        requires: [{kind: flag, target: stance_together}]
        next_scene: resolved
      - id: trust_her_science
        text: solo
        requires: [{kind: flag, target: stance_alone}]
        next_scene: solo
  - id: resolved
    title: R
    location: lab
    lines: [{text: r}]
  - id: solo
    title: S
    location: lab
    lines: [{text: s}]
"""


# --- clean twin: a stance scene with always-available choices on the path --

_LOC_CLEAN = """
locations:
  - id: lab
    name: Lab
    exits: []
    scene_hooks:
      - {scene_id: route_choice, trigger: auto, requires_flags: [intro_done], forbids_flags: [route_chosen]}
"""

_SCENES_CLEAN = """
scenes:
  - id: intro
    title: I
    location: lab
    lines: [{text: begin}]
    on_end: [{kind: set_flag, target: intro_done, value: true}]
  - id: route_choice
    title: C
    location: lab
    lines: [{text: pick}]
    choices:
      - id: pick_yuening
        text: yuening
        effects:
          - {kind: set_flag, target: route_chosen, value: true}
          - {kind: set_flag, target: route_yuening, value: true}
        next_scene: stance
  - id: stance
    title: Stance
    location: lab
    route: yuening
    lines: [{text: "pick a stance"}]
    choices:
      - id: insist_together
        text: together
        effects: [{kind: set_flag, target: stance_together, value: true}]
        next_scene: climax
      - id: respect_solo
        text: solo
        effects: [{kind: set_flag, target: stance_alone, value: true}]
        next_scene: climax
  - id: climax
    title: Climax
    location: lab
    route: yuening
    lines: [{text: climax}]
    choices:
      - id: turn_with_her
        text: together
        requires: [{kind: flag, target: stance_together}]
        next_scene: resolved
      - id: trust_her_science
        text: solo
        requires: [{kind: flag, target: stance_alone}]
        next_scene: solo
  - id: resolved
    title: R
    location: lab
    lines: [{text: r}]
  - id: solo
    title: S
    location: lab
    lines: [{text: s}]
"""


def _broken(root: Path) -> Path:
    _write_common(root / "content", _LOC_BROKEN, _SCENES_BROKEN)
    return root


def _clean(root: Path) -> Path:
    _write_common(root / "content", _LOC_CLEAN, _SCENES_CLEAN)
    return root


# ----------------------------------------------------------------------
# Tests: the stance soft-lock


def test_flags_stance_softlock(tmp_path: Path) -> None:
    """The broken climax (reachable on-route with no stance set) is an
    all_locked_visible soft-lock naming both stance flags."""
    pack = _broken(tmp_path / "broken")
    locks = {s.scene_id: s for s in SoftLockChecker(pack).check()}
    assert "climax" in locks, f"expected a soft-lock at climax, got: {locks}"
    sl = locks["climax"]
    assert sl.shape == "all_locked_visible"
    assert set(sl.locking_flags) == {"stance_together", "stance_alone"}
    # Each choice is reported with the flag that locks it.
    locked_by = {c["choice"]: c["locked_by"] for c in sl.choices}
    assert locked_by == {"turn_with_her": "stance_together",
                         "trust_her_science": "stance_alone"}


def test_clean_twin_not_flagged(tmp_path: Path) -> None:
    """When a stance-setting scene with always-available choices sits on the
    only path to the climax, every path arrives with a stance set -> no
    soft-lock (no false positive)."""
    pack = _clean(tmp_path / "clean")
    locks = SoftLockChecker(pack).check()
    # Neither the stance scene (it has unconditional choices) nor the climax
    # (reachable only with a stance set) is a soft-lock.
    assert locks == [], f"clean pack must not be flagged, got: {locks}"


# ----------------------------------------------------------------------
# Tests: precision filters


def test_always_available_choice_not_flagged(tmp_path: Path) -> None:
    """A menu with an unconditional (always-available) option can never be
    all-locked -> never flagged, even if the other options are gated."""
    scenes = """
scenes:
  - id: intro
    title: I
    location: lab
    lines: [{text: begin}]
    on_end: [{kind: set_flag, target: intro_done, value: true}]
  - id: route_choice
    title: C
    location: lab
    lines: [{text: pick}]
    choices:
      - id: go
        text: go
        effects: [{kind: set_flag, target: route_chosen, value: true}]
        next_scene: pivot
  - id: pivot
    title: Pivot
    location: lab
    lines: [{text: pivot}]
    choices:
      - id: gated
        text: gated
        requires: [{kind: flag, target: stance_together}]
        next_scene: resolved
      - id: always
        text: always there
        next_scene: solo
  - id: resolved
    title: R
    location: lab
    lines: [{text: r}]
  - id: solo
    title: S
    location: lab
    lines: [{text: s}]
"""
    loc = """
locations:
  - id: lab
    name: Lab
    exits: []
    scene_hooks:
      - {scene_id: route_choice, trigger: auto, requires_flags: [intro_done], forbids_flags: [route_chosen]}
"""
    _write_common((tmp_path / "p") / "content", loc, scenes)
    assert SoftLockChecker(tmp_path / "p").check() == []


def test_affection_only_gate_not_flagged(tmp_path: Path) -> None:
    """A choice gated solely on affection (no flag gate) is not provably
    lockable -> the scene is not reported (conservative, no false positive)."""
    scenes = """
scenes:
  - id: intro
    title: I
    location: lab
    lines: [{text: begin}]
    on_end: [{kind: set_flag, target: intro_done, value: true}]
  - id: route_choice
    title: C
    location: lab
    lines: [{text: pick}]
    choices:
      - id: go
        text: go
        effects:
          - {kind: set_flag, target: route_chosen, value: true}
          - {kind: set_flag, target: route_yuening, value: true}
        next_scene: pivot
  - id: pivot
    title: Pivot
    location: lab
    route: yuening
    lines: [{text: pivot}]
    choices:
      - id: warm
        text: warm
        requires: [{kind: affection_gte, target: yuening, value: 50}]
        next_scene: resolved
      - id: cool
        text: cool
        requires: [{kind: flag, target: stance_alone}]
        next_scene: solo
  - id: resolved
    title: R
    location: lab
    lines: [{text: r}]
  - id: solo
    title: S
    location: lab
    lines: [{text: s}]
"""
    loc = """
locations:
  - id: lab
    name: Lab
    exits: []
    scene_hooks:
      - {scene_id: route_choice, trigger: auto, requires_flags: [intro_done], forbids_flags: [route_chosen]}
      - {scene_id: pivot, trigger: auto, requires_flags: [route_yuening]}
"""
    _write_common((tmp_path / "p") / "content", loc, scenes)
    assert SoftLockChecker(tmp_path / "p").check() == []


def test_all_hidden_with_fallthrough_not_flagged(tmp_path: Path) -> None:
    """Every choice hidden_if_locked but on_end has a play_scene fallthrough:
    an empty menu continues play, so it is NOT a soft-lock."""
    scenes = """
scenes:
  - id: intro
    title: I
    location: lab
    lines: [{text: begin}]
    on_end: [{kind: set_flag, target: intro_done, value: true}]
  - id: route_choice
    title: C
    location: lab
    lines: [{text: pick}]
    choices:
      - id: go
        text: go
        effects: [{kind: set_flag, target: route_chosen, value: true}]
        next_scene: gate
  - id: gate
    title: Gate
    location: lab
    lines: [{text: maybe a bonus}]
    choices:
      - id: bonus
        text: bonus
        requires: [{kind: flag, target: stance_together}]
        hidden_if_locked: true
        next_scene: resolved
    on_end: [{kind: play_scene, target: solo}]
  - id: resolved
    title: R
    location: lab
    lines: [{text: r}]
  - id: solo
    title: S
    location: lab
    lines: [{text: s}]
"""
    loc = """
locations:
  - id: lab
    name: Lab
    exits: []
    scene_hooks:
      - {scene_id: route_choice, trigger: auto, requires_flags: [intro_done], forbids_flags: [route_chosen]}
"""
    _write_common((tmp_path / "p") / "content", loc, scenes)
    assert SoftLockChecker(tmp_path / "p").check() == []


def test_all_hidden_without_fallthrough_flagged(tmp_path: Path) -> None:
    """Every choice hidden_if_locked and NO on_end fallthrough: an all-locked
    state empties the menu and the scene silently ends -> an all_hidden
    soft-lock."""
    scenes = """
scenes:
  - id: intro
    title: I
    location: lab
    lines: [{text: begin}]
    on_end: [{kind: set_flag, target: intro_done, value: true}]
  - id: route_choice
    title: C
    location: lab
    lines: [{text: pick}]
    choices:
      - id: go
        text: go
        effects: [{kind: set_flag, target: route_chosen, value: true}]
        next_scene: dead
  - id: dead
    title: Dead
    location: lab
    lines: [{text: "all options may vanish"}]
    choices:
      - id: bonus
        text: bonus
        requires: [{kind: flag, target: stance_together}]
        hidden_if_locked: true
        next_scene: resolved
  - id: resolved
    title: R
    location: lab
    lines: [{text: r}]
"""
    loc = """
locations:
  - id: lab
    name: Lab
    exits: []
    scene_hooks:
      - {scene_id: route_choice, trigger: auto, requires_flags: [intro_done], forbids_flags: [route_chosen]}
"""
    _write_common((tmp_path / "p") / "content", loc, scenes)
    locks = {s.scene_id: s for s in SoftLockChecker(tmp_path / "p").check()}
    assert "dead" in locks, f"expected an all_hidden soft-lock, got: {locks}"
    assert locks["dead"].shape == "all_hidden"


# ----------------------------------------------------------------------
# Tests: self-check stage wiring


def test_self_check_stage_fails_on_softlock(tmp_path: Path) -> None:
    """The softlock stage fails the self-check on a real soft-lock and names the
    scene in its summary."""
    from world_gal_game.plugins import snapshot, restore
    pack = _broken(tmp_path / "broken")
    snap = snapshot()
    try:
        rep = SelfCheck(pack, skip_smoke=True, skip_visual=True,
                        skip_reachability=True, stop_on_failure=False).run()
    finally:
        restore(snap)
    stage = next(s for s in rep.stages if s.name == "softlock")
    assert stage.ok is False
    assert "climax" in stage.summary
    scenes = [it["scene_id"] for it in stage.details["items"]]
    assert "climax" in scenes
    assert rep.ok is False


def test_self_check_stage_passes_on_clean(tmp_path: Path) -> None:
    """The clean twin passes the softlock stage."""
    from world_gal_game.plugins import snapshot, restore
    pack = _clean(tmp_path / "clean")
    snap = snapshot()
    try:
        rep = SelfCheck(pack, skip_smoke=True, skip_visual=True).run()
    finally:
        restore(snap)
    stage = next(s for s in rep.stages if s.name == "softlock")
    assert stage.ok is True
    assert not stage.details["items"]


def test_skip_softlock_flag(tmp_path: Path) -> None:
    """skip_softlock marks the stage skipped (and so cannot fail a run)."""
    from world_gal_game.plugins import snapshot, restore
    pack = _broken(tmp_path / "broken")
    snap = snapshot()
    try:
        rep = SelfCheck(pack, skip_smoke=True, skip_visual=True,
                        skip_reachability=True, skip_softlock=True).run()
    finally:
        restore(snap)
    stage = next(s for s in rep.stages if s.name == "softlock")
    assert stage.skipped is True
    assert stage.ok is True
    assert "skipped" in stage.summary.lower()
