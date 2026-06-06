"""Tests for the SmokeRunner pass/fail criterion (Guard 1).

These exercise the criterion split introduced so the smoke gate honours explicit
per-script ``assert`` ops instead of only the "an ending_* flag was set"
heuristic:

- a script whose ``assert`` ops all pass is OK even if it sets no ending flag,
- a script whose ``assert`` ops fail is FAIL even if it *does* set an ending
  flag (the regression the old heuristic-only rule hid),
- a script with no ``assert`` ops still uses the ending-flag heuristic.

Each test writes a tiny self-contained pack to ``tmp_path`` and runs one script
through it; the pack is passed to the runner by absolute path so it resolves
without living under ``games/``.
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

from world_gal_game.dev.smoke_runner import SmokeRunner


def _write_pack(root: Path, *, scenes: str) -> Path:
    """Write a minimal loadable pack under ``root`` and return its directory."""
    content = root / "content"
    (content / "scenes").mkdir(parents=True)
    (content / "meta.yaml").write_text(textwrap.dedent("""
        pack_format_version: "0.1"
        title: "Smoke Fixture"
        start_location: room
        intro_scene: s_start
    """), encoding="utf-8")
    (content / "locations.yaml").write_text(textwrap.dedent("""
        - id: room
          name: "Room"
          exits: []
    """), encoding="utf-8")
    (content / "scenes" / "s.yaml").write_text(textwrap.dedent(scenes),
                                               encoding="utf-8")
    return root


def _write_script(root: Path, name: str, commands: list[dict]) -> None:
    scripts = root / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / name).write_text(
        json.dumps({"commands": commands}, ensure_ascii=False), encoding="utf-8")


# A scene that, when played to the end, sets ending_win and a flag we can assert.
_SCENES = """
    scenes:
      - id: s_start
        title: "Start"
        location: room
        lines:
          - text: "hello"
            effects:
              - {kind: set_flag, target: greeted, value: true}
        on_end:
          - {kind: set_flag, target: ending_win, value: true}
"""


def test_assert_based_script_passes_without_ending_flag(tmp_path: Path):
    """A script that only asserts (and never sets ending_*) passes when its
    asserts hold — the heuristic must not drag it to FAIL."""
    pack = _write_pack(tmp_path, scenes="""
        scenes:
          - id: s_start
            title: "Start"
            location: room
            lines:
              - text: "hello"
                effects:
                  - {kind: set_flag, target: greeted, value: true}
    """)
    _write_script(pack, "test_assert_pass.json", [
        {"op": "start_scene", "scene": "s_start"},
        {"op": "next", "count": 3},
        {"op": "assert", "flag": "greeted"},
    ])
    rep = SmokeRunner(pack).run(pack_name=str(pack))
    (res,) = rep.results
    assert res.ok is True
    assert res.criterion == "assert"
    assert res.asserts_total == 1 and res.asserts_passed == 1
    assert res.ending_flag is None  # proves the heuristic was not what passed it
    assert rep.ok is True


def test_failing_assert_fails_even_with_ending_flag(tmp_path: Path):
    """The key regression: a failing assert must FAIL the script even though an
    ending_* flag is set (which the old heuristic-only rule would have passed)."""
    pack = _write_pack(tmp_path, scenes=_SCENES)
    _write_script(pack, "test_assert_fail.json", [
        {"op": "start_scene", "scene": "s_start"},
        {"op": "next", "count": 3},
        # ending_win IS now set by on_end, but this expectation is wrong:
        {"op": "assert", "flag": "nonexistent_flag"},
    ])
    rep = SmokeRunner(pack).run(pack_name=str(pack))
    (res,) = rep.results
    assert res.ending_flag == "ending_win"      # the heuristic *would* have passed
    assert res.ok is False                       # but the failed assert wins
    assert res.criterion == "assert"
    assert res.asserts_total == 1 and res.asserts_passed == 0
    assert len(res.failed_asserts) == 1
    assert res.failed_asserts[0]["index"] == 2
    assert rep.ok is False


def test_no_assert_uses_ending_heuristic(tmp_path: Path):
    """A script with no asserts still passes via the ending-flag heuristic, and
    fails when no ending flag is set."""
    pack = _write_pack(tmp_path, scenes=_SCENES)

    # Reaches the end -> ending_win set -> heuristic pass.
    _write_script(pack, "test_reach_end.json", [
        {"op": "start_scene", "scene": "s_start"},
        {"op": "next", "count": 3},
    ])
    rep = SmokeRunner(pack).run(pack_name=str(pack))
    res = {r.script: r for r in rep.results}["scripts/test_reach_end.json"]
    assert res.ok is True
    assert res.criterion == "ending_flag"
    assert res.ending_flag == "ending_win"
    assert res.asserts_total == 0


def test_no_assert_no_ending_flag_fails(tmp_path: Path):
    """No asserts and no ending flag set -> heuristic FAIL."""
    pack = _write_pack(tmp_path, scenes="""
        scenes:
          - id: s_start
            title: "Start"
            location: room
            lines:
              - text: "hello"
    """)
    _write_script(pack, "test_no_ending.json", [
        {"op": "start_scene", "scene": "s_start"},
        {"op": "next", "count": 2},
    ])
    rep = SmokeRunner(pack).run(pack_name=str(pack))
    (res,) = rep.results
    assert res.ok is False
    assert res.criterion == "ending_flag"
    assert res.ending_flag is None


def test_command_error_fails_before_asserts(tmp_path: Path):
    """A command that errors fails the script regardless of asserts/endings."""
    pack = _write_pack(tmp_path, scenes=_SCENES)
    _write_script(pack, "test_bad_op.json", [
        {"op": "start_scene", "scene": "s_start"},
        {"op": "move", "location": "no_such_place"},  # ok == False
        {"op": "assert", "flag": "greeted"},
    ])
    rep = SmokeRunner(pack).run(pack_name=str(pack))
    (res,) = rep.results
    assert res.ok is False
    assert res.criterion == "error"
    assert res.errors


def test_to_dict_includes_assert_fields(tmp_path: Path):
    """The serialised result carries the new assert/criterion fields."""
    pack = _write_pack(tmp_path, scenes=_SCENES)
    _write_script(pack, "test_assert_pass.json", [
        {"op": "start_scene", "scene": "s_start"},
        {"op": "next", "count": 3},
        {"op": "assert", "flag": "greeted"},
        {"op": "assert", "flag": "ending_win"},
    ])
    rep = SmokeRunner(pack).run(pack_name=str(pack))
    d = rep.to_dict()["results"][0]
    assert d["criterion"] == "assert"
    assert d["asserts_total"] == 2
    assert d["asserts_passed"] == 2
    assert d["failed_asserts"] == []
