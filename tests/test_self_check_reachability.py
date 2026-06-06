"""Tests for the organic ending-reachability (strand) guard — Guard 2.

The decisive fixture is a tiny two-route, four-"year" pack that reproduces the
real bug class: a route plays fine through year 3 and then **strands** because
the scene that would carry its chapter into year 4 is unreachable, so its
year-4 ending flag can never be set. A from-anywhere reachability check (and the
smoke runner, which teleports past the break) both call that ending "reachable";
the strand guard must call it what it is.

- ``red`` route: fully wired — its terminal ``ending_red`` is organically
  reachable, so the guard reports it ``ok``.
- ``blue`` route: its year-4 opener (``y4_blue``, the scene that sets the
  chapter to ``c4`` and chains to the finale) is **orphaned** — nothing triggers
  it — so the chapter never advances to ``c4`` for blue, ``blue_finale`` never
  fires, and ``ending_blue`` is never set. The guard must flag ``ending_blue``
  as a strand and fail the stage.

The clean twin (``blue`` re-wired) must pass, proving the guard does not
false-positive.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from world_gal_game.dev.reachability import EndingReachabilityChecker
from world_gal_game.dev.self_check import SelfCheck


# ----------------------------------------------------------------------
# Fixture pack builder


_META = """
    pack_format_version: "0.1"
    title: "Strand Fixture"
    start_location: hub
    intro_scene: intro
"""

_LOCATIONS = """
    - id: hub
      name: "Hub"
      exits:
        - {target: finale_loc}
      scene_hooks:
        # route lock-in once the intro is done
        - {scene_id: route_choice, trigger: auto, requires_flags: [intro_done],
           forbids_flags: [route_chosen]}
        # year-2 route content, reachable via a hook after lock-in (mirrors the
        # real pack, where route content hangs off location hooks, not only the
        # route_choice next_scene — so a route-flag-seeded search reaches it)
        - {scene_id: red_y2, trigger: auto, requires_flags: [route_red],
           forbids_flags: [red_arc2],
           requires: [{kind: chapter_at_or_after, target: c2}]}
        - {scene_id: blue_y2, trigger: auto, requires_flags: [route_blue],
           forbids_flags: [blue_arc2],
           requires: [{kind: chapter_at_or_after, target: c2}]}
        # year openers, gated on the previous year's per-route arc flag
        - {scene_id: y3_red, trigger: auto, requires_flags: [route_red, red_arc2],
           requires: [{kind: chapter_at_or_after, target: c2}],
           forbids: [{kind: chapter_at_or_after, target: c3}]}
        - {scene_id: y4_red, trigger: auto, requires_flags: [route_red, red_arc3],
           requires: [{kind: chapter_at_or_after, target: c3}],
           forbids: [{kind: chapter_at_or_after, target: c4}]}
        - {scene_id: y3_blue, trigger: auto, requires_flags: [route_blue, blue_arc2],
           requires: [{kind: chapter_at_or_after, target: c2}],
           forbids: [{kind: chapter_at_or_after, target: c3}]}
__Y4_BLUE_HOOK__
    - id: finale_loc
      name: "Finale"
      exits:
        - {target: hub}
      scene_hooks:
        - {scene_id: red_finale, trigger: auto, requires_flags: [route_red],
           requires: [{kind: chapter_at_or_after, target: c4}]}
        - {scene_id: blue_finale, trigger: auto, requires_flags: [route_blue],
           requires: [{kind: chapter_at_or_after, target: c4}]}
"""

# y4_blue hook line, present only in the CLEAN variant.
_Y4_BLUE_HOOK = ("        - {scene_id: y4_blue, trigger: auto, "
                 "requires_flags: [route_blue, blue_arc3], "
                 "requires: [{kind: chapter_at_or_after, target: c3}], "
                 "forbids: [{kind: chapter_at_or_after, target: c4}]}")

_CHAPTERS = """
    chapters:
      - {id: c1, title: "Year 1", route: common, order: 10, entry_scene: intro,
         scenes: [intro, route_choice]}
      - {id: c2, title: "Year 2", route: common, order: 20,
         scenes: [red_y2, blue_y2]}
      - {id: c3, title: "Year 3", route: common, order: 30,
         scenes: [y3_red, y3_blue, red_y3, blue_y3]}
      - {id: c4, title: "Year 4", route: common, order: 40,
         scenes: [y4_red, y4_blue, red_finale, blue_finale],
         endings: [ending_red, ending_blue]}
"""

_ENDINGS = """
    endings:
      - {id: ending_red, title: "Red end", route_id: red,
         requires: [{kind: flag, target: ending_red}]}
      - {id: ending_blue, title: "Blue end", route_id: blue,
         requires: [{kind: flag, target: ending_blue}]}
"""

_VARIABLES = """
    variables:
      - {key: intro_done, type: bool, default: false, category: progress}
      - {key: route_chosen, type: bool, default: false, category: progress}
      - {key: route_red, type: bool, default: false, category: progress}
      - {key: route_blue, type: bool, default: false, category: progress}
      - {key: red_arc2, type: bool, default: false, category: progress}
      - {key: red_arc3, type: bool, default: false, category: progress}
      - {key: blue_arc2, type: bool, default: false, category: progress}
      - {key: blue_arc3, type: bool, default: false, category: progress}
      - {key: ending_red, type: bool, default: false, category: progress}
      - {key: ending_blue, type: bool, default: false, category: progress}
"""

# Scenes. The year content scenes set their per-route arc flag; the year openers
# advance the chapter and chain (play_scene) into that year's route content; the
# finales set the ending flag. blue_y3 sets blue_arc3 so the *only* thing missing
# in the strand variant is the y4_blue trigger.
_SCENES = """
    scenes:
      - id: intro
        title: "Intro"
        location: hub
        lines: [{text: "begin"}]
        on_end:
          - {kind: set_chapter, target: c1, value: false}
          - {kind: set_flag, target: intro_done, value: true}

      - id: route_choice
        title: "Choose"
        location: hub
        lines: [{text: "pick a route"}]
        choices:
          - id: pick_red
            text: "red"
            effects:
              - {kind: set_flag, target: route_chosen, value: true}
              - {kind: set_flag, target: route_red, value: true}
              - {kind: set_chapter, target: c2, value: false}
            next_scene: red_y2
          - id: pick_blue
            text: "blue"
            effects:
              - {kind: set_flag, target: route_chosen, value: true}
              - {kind: set_flag, target: route_blue, value: true}
              - {kind: set_chapter, target: c2, value: false}
            next_scene: blue_y2

      - id: red_y2
        title: "Red Y2"
        location: hub
        route: red
        lines: [{text: "red year 2"}]
        on_end:
          - {kind: set_flag, target: red_arc2, value: true}

      - id: blue_y2
        title: "Blue Y2"
        location: hub
        route: blue
        lines: [{text: "blue year 2"}]
        on_end:
          - {kind: set_flag, target: blue_arc2, value: true}

      - id: y3_red
        title: "Year 3 (red)"
        location: hub
        route: red
        lines: [{text: "up to year 3"}]
        on_end:
          - {kind: set_chapter, target: c3, value: false}
          - {kind: play_scene, target: red_y3}

      - id: y3_blue
        title: "Year 3 (blue)"
        location: hub
        route: blue
        lines: [{text: "up to year 3"}]
        on_end:
          - {kind: set_chapter, target: c3, value: false}
          - {kind: play_scene, target: blue_y3}

      - id: red_y3
        title: "Red Y3"
        location: hub
        route: red
        lines: [{text: "red year 3"}]
        on_end:
          - {kind: set_flag, target: red_arc3, value: true}

      - id: blue_y3
        title: "Blue Y3"
        location: hub
        route: blue
        lines: [{text: "blue year 3"}]
        on_end:
          - {kind: set_flag, target: blue_arc3, value: true}

      - id: y4_red
        title: "Year 4 (red)"
        location: hub
        route: red
        lines: [{text: "up to year 4"}]
        on_end:
          - {kind: set_chapter, target: c4, value: false}

      - id: y4_blue
        title: "Year 4 (blue)"
        location: hub
        route: blue
        lines: [{text: "up to year 4"}]
        on_end:
          - {kind: set_chapter, target: c4, value: false}

      - id: red_finale
        title: "Red finale"
        location: finale_loc
        route: red
        lines: [{text: "red graduates"}]
        on_end:
          - {kind: set_flag, target: ending_red, value: true}

      - id: blue_finale
        title: "Blue finale"
        location: finale_loc
        route: blue
        lines: [{text: "blue graduates"}]
        on_end:
          - {kind: set_flag, target: ending_blue, value: true}
"""


def _write_fixture(root: Path, *, strand: bool) -> Path:
    """Write the fixture pack to ``root``; ``strand`` omits the y4_blue trigger."""
    content = root / "content"
    (content / "scenes").mkdir(parents=True)
    (content / "meta.yaml").write_text(textwrap.dedent(_META), encoding="utf-8")
    locations = textwrap.dedent(_LOCATIONS).replace(
        "__Y4_BLUE_HOOK__", "" if strand else _Y4_BLUE_HOOK)
    (content / "locations.yaml").write_text(locations, encoding="utf-8")
    (content / "chapters.yaml").write_text(
        textwrap.dedent(_CHAPTERS), encoding="utf-8")
    (content / "endings.yaml").write_text(
        textwrap.dedent(_ENDINGS), encoding="utf-8")
    (content / "variables.yaml").write_text(
        textwrap.dedent(_VARIABLES), encoding="utf-8")
    (content / "scenes" / "s.yaml").write_text(
        textwrap.dedent(_SCENES), encoding="utf-8")
    return root


# ----------------------------------------------------------------------
# Tests


def test_checker_flags_blue_strand(tmp_path: Path):
    """The stranded fixture: ending_blue is a strand, ending_red is reachable."""
    pack = _write_fixture(tmp_path / "strand", strand=True)
    chk = EndingReachabilityChecker(pack)
    results = {r.ending_id: r for r in chk.check_all(
        max_nodes=2000, time_budget_s=30)}

    assert results["ending_blue"].status == "strand"
    # The static over-approximation alone is enough to prove this one, so it is
    # a fast, definitive verdict (not a timed-out "unverified").
    assert results["ending_blue"].method == "static"
    assert results["ending_red"].status == "ok"


def test_checker_passes_clean_twin(tmp_path: Path):
    """Re-wiring y4_blue makes ending_blue reachable -> no strands."""
    pack = _write_fixture(tmp_path / "clean", strand=False)
    chk = EndingReachabilityChecker(pack)
    results = {r.ending_id: r for r in chk.check_all(
        max_nodes=2000, time_budget_s=30)}

    assert results["ending_red"].status == "ok"
    assert results["ending_blue"].status == "ok"
    assert not any(r.status == "strand" for r in results.values())


def test_self_check_stage_fails_on_strand(tmp_path: Path):
    """The reachability stage fails the whole self-check on a strand and names
    the stranded ending in its summary."""
    from world_gal_game.plugins import snapshot, restore
    pack = _write_fixture(tmp_path / "strand", strand=True)
    snap = snapshot()
    try:
        # Skip smoke (the fixture ships no scripts) so we isolate the new stage.
        # stop_on_failure=False so the reachability stage still runs even though
        # the orphaned y4_blue also trips the earlier dead-ends stage (an orphan
        # scene is, correctly, both a dead-end *and* the cause of the strand).
        rep = SelfCheck(pack, skip_smoke=True, skip_visual=True,
                        stop_on_failure=False).run()
    finally:
        restore(snap)

    reach = next(s for s in rep.stages if s.name == "reachability")
    assert reach.ok is False
    assert "ending_blue" in reach.summary
    strands = [s["ending_id"] for s in reach.details["strands"]]
    assert strands == ["ending_blue"]
    assert rep.ok is False


def test_self_check_stage_passes_on_clean(tmp_path: Path):
    """The clean twin passes the reachability stage (and the run, modulo smoke)."""
    from world_gal_game.plugins import snapshot, restore
    pack = _write_fixture(tmp_path / "clean", strand=False)
    snap = snapshot()
    try:
        rep = SelfCheck(pack, skip_smoke=True, skip_visual=True).run()
    finally:
        restore(snap)

    reach = next(s for s in rep.stages if s.name == "reachability")
    assert reach.ok is True
    assert not reach.details["strands"]


def test_skip_reachability_flag(tmp_path: Path):
    """skip_reachability marks the stage skipped (and so cannot fail a run)."""
    from world_gal_game.plugins import snapshot, restore
    pack = _write_fixture(tmp_path / "clean", strand=False)
    snap = snapshot()
    try:
        rep = SelfCheck(pack, skip_smoke=True, skip_visual=True,
                        skip_reachability=True).run()
    finally:
        restore(snap)
    reach = next(s for s in rep.stages if s.name == "reachability")
    assert reach.skipped is True
    assert reach.ok is True
    assert "skipped" in reach.summary.lower()
