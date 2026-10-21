"""Tests for PackInspector against demo_pack + synthetic packs."""
from __future__ import annotations

import textwrap
from pathlib import Path


from world_gal_game.dev.pack_inspector import PackInspector


# ----------------------------------------------------------------------
# Demo pack baseline


def test_demo_pack_summary():
    ins = PackInspector(Path("games/demo_pack"))
    s = ins.summary()
    assert s["title"] == "小鎮的午後"
    assert s["pack_format_version"] == "0.1"
    assert s["counts"]["scenes"] >= 10
    assert s["counts"]["endings"] == 3
    assert s["counts"]["declared_endings"] == 3
    assert s["counts"]["ending_scenes"] == 3


def test_demo_pack_reachability_clean():
    """All scenes should be reachable, all 3 endings achievable."""
    ins = PackInspector(Path("games/demo_pack"))
    r = ins.reachability()
    assert r["unreachable"] == []
    assert set(r["endings"]["reachable"]) == {
        "ending_alone", "ending_friend", "ending_lover",
    }
    assert r["endings"]["unreachable"] == []


def test_demo_pack_no_dead_ends():
    ins = PackInspector(Path("games/demo_pack"))
    assert ins.dead_ends() == []


def test_demo_pack_graph_mermaid_renders():
    ins = PackInspector(Path("games/demo_pack"))
    m = ins.graph(format="mermaid")
    assert m.startswith("graph LR")
    assert "ending_lover" in m
    assert "prologue" in m


def test_demo_pack_graph_dot_renders():
    ins = PackInspector(Path("games/demo_pack"))
    d = ins.graph(format="dot")
    assert d.startswith("digraph pack")
    assert "rankdir=" in d


def test_demo_pack_graph_dict():
    ins = PackInspector(Path("games/demo_pack"))
    g = ins.graph(format="dict")
    assert isinstance(g, dict)
    assert "meet_heroine" in g


def test_demo_pack_npcs_and_locations():
    ins = PackInspector(Path("games/demo_pack"))
    npcs = ins.npcs()
    assert len(npcs) == 2
    locs = ins.locations()
    assert any(loc["id"] == "starting_room" for loc in locs)


# ----------------------------------------------------------------------
# Synthetic packs for edge-case coverage


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def test_orphan_scene_is_unreachable(tmp_path: Path):
    pack = tmp_path / "p"
    _write(pack / "content/meta.yaml", "intro_scene: a\n")
    _write(pack / "content/scenes/all.yaml", """
        scenes:
          - id: a
            title: A
            lines: [{text: hello}]
            choices:
              - {id: c, text: go b, next_scene: b}
          - id: b
            title: B
            lines: [{text: hello}]
          - id: orphan
            title: Orphan
            lines: [{text: nobody calls me}]
    """)
    ins = PackInspector(pack)
    r = ins.reachability()
    assert "orphan" in r["unreachable"]
    assert "orphan" not in r["reachable"]


def test_summary_prefers_declared_endings_over_scene_name_heuristic(
    tmp_path: Path,
):
    pack = tmp_path / "p"
    _write(pack / "content/meta.yaml", "intro_scene: ending_shared\n")
    _write(pack / "content/scenes/all.yaml", """
        scenes:
          - id: ending_shared
            title: Shared terminal
            lines: [{text: done}]
    """)
    _write(pack / "content/endings.yaml", """
        endings:
          - id: ending_good
            title: Good
            requires: [{kind: flag, target: ending_good}]
          - id: ending_bad
            title: Bad
            requires: [{kind: flag, target: ending_bad}]
    """)

    counts = PackInspector(pack).summary()["counts"]
    assert counts["endings"] == 2
    assert counts["declared_endings"] == 2
    assert counts["ending_scenes"] == 1


def test_dead_end_choice_detected(tmp_path: Path):
    """A choice with neither next_scene nor effects is a no-op dead-end."""
    pack = tmp_path / "p"
    _write(pack / "content/meta.yaml", "intro_scene: a\n")
    _write(pack / "content/scenes/all.yaml", """
        scenes:
          - id: a
            title: A
            lines: [{text: stuck}]
            choices:
              - id: nope
                text: this does nothing
    """)
    ins = PackInspector(pack)
    de = ins.dead_ends()
    assert any(d.kind == "scene" and d.target == "a" for d in de)


def test_dead_end_unleavable_location(tmp_path: Path):
    pack = tmp_path / "p"
    _write(pack / "content/meta.yaml", "intro_scene: a\n")
    _write(pack / "content/scenes/all.yaml", """
        scenes:
          - id: a
            title: A
            lines: [{text: x}]
    """)
    _write(pack / "content/locations.yaml", """
        locations:
          - id: trap
            name: A Trap
    """)
    ins = PackInspector(pack)
    de = ins.dead_ends()
    assert any(d.kind == "location" and d.target == "trap" for d in de)


def test_ending_scene_is_not_dead_end(tmp_path: Path):
    """Scenes whose id starts with ending_ are valid terminals."""
    pack = tmp_path / "p"
    _write(pack / "content/meta.yaml", "intro_scene: a\n")
    _write(pack / "content/scenes/all.yaml", """
        scenes:
          - id: a
            title: A
            lines: [{text: x}]
            on_end:
              - {kind: play_scene, target: ending_x}
          - id: ending_x
            title: X
            lines: [{text: fin}]
    """)
    ins = PackInspector(pack)
    de = ins.dead_ends()
    assert not any(d.target == "ending_x" for d in de)


def test_reachability_via_play_scene_in_on_end(tmp_path: Path):
    pack = tmp_path / "p"
    _write(pack / "content/meta.yaml", "intro_scene: start\n")
    _write(pack / "content/scenes/all.yaml", """
        scenes:
          - id: start
            title: S
            lines: [{text: go}]
            on_end:
              - {kind: play_scene, target: middle}
          - id: middle
            title: M
            lines: [{text: more}]
            on_end:
              - {kind: play_scene, target: ending_z}
          - id: ending_z
            title: Z
            lines: [{text: done}]
    """)
    ins = PackInspector(pack)
    r = ins.reachability()
    assert set(r["reachable"]) == {"start", "middle", "ending_z"}


def test_reachability_via_play_scene_branch_default_and_cases(
    tmp_path: Path,
):
    """Every static branch destination is an edge, not an orphan scene."""

    pack = tmp_path / "p"
    _write(pack / "content/meta.yaml", "intro_scene: start\n")
    _write(pack / "content/scenes/all.yaml", """
        scenes:
          - id: start
            title: S
            lines: [{text: choose at runtime}]
            on_end:
              - kind: play_scene_branch
                target: fallback
                value:
                  - when: {kind: flag, target: route_a}
                    target: route_a_scene
                  - when: {kind: flag, target: route_b}
                    target: route_b_scene
          - id: fallback
            title: Fallback
            lines: [{text: done}]
          - id: route_a_scene
            title: A
            lines: [{text: done}]
          - id: route_b_scene
            title: B
            lines: [{text: done}]
    """)

    inspector = PackInspector(pack)

    assert set(inspector.reachability()["reachable"]) == {
        "start",
        "fallback",
        "route_a_scene",
        "route_b_scene",
    }
    assert {
        target for _origin, target in inspector.scenes_by_id["start"].outgoing
    } == {"fallback", "route_a_scene", "route_b_scene"}
    assert not any(
        issue.kind == "unreachable" for issue in inspector.dead_ends()
    )
