"""Tests for DataflowAnalyzer against demo_pack.

Exercises the cross-reference report (where flags / scenes are written and
read), the per-symbol ``references`` lookup, the conditioned scene -> scene
edges, and the declared-flag reconciliation (undeclared / unused).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from world_gal_game.core.story_graph import Condition, Effect, Scene
from world_gal_game.dev.dataflow import DataflowAnalyzer, Edge, SymbolUsage


# The 12 flags the demo pack actually uses (writers and/or readers).
DEMO_FLAGS = {
    "prologue_done",
    "met_heroine_1",
    "quest_started",
    "quest_done",
    "obj_alley_done",
    "obj_park_done",
    "obj_square_done",
    "heroine_1_friend",
    "heroine_1_lover",
    "ending_lover",
    "ending_friend",
    "ending_alone",
}


# ----------------------------------------------------------------------
# Symbol usage on demo_pack


def test_ending_lover_has_writer_and_reader():
    """ending_lover is set by a 9x ending scene and read by an ending gate."""
    analyzer = DataflowAnalyzer(Path("games/demo_pack"))
    report = analyzer.analyze()
    assert "ending_lover" in report.flags
    usage = report.flags["ending_lover"]
    assert isinstance(usage, SymbolUsage)
    assert len(usage.writers) >= 1
    assert len(usage.readers) >= 1
    # The writer is a set_flag in the lover ending scene.
    assert any(
        w.kind == "set_flag" and "ending_lover" in w.site for w in usage.writers
    )


def test_met_heroine_1_has_writer_and_reader():
    analyzer = DataflowAnalyzer(Path("games/demo_pack"))
    report = analyzer.analyze()
    assert "met_heroine_1" in report.flags
    usage = report.flags["met_heroine_1"]
    assert len(usage.writers) >= 1
    assert len(usage.readers) >= 1


def test_references_lookup_returns_usage_dict():
    analyzer = DataflowAnalyzer(Path("games/demo_pack"))
    refs = analyzer.references("ending_lover")
    assert isinstance(refs, dict)
    flags_entry = refs["flags"]
    assert flags_entry is not None
    assert len(flags_entry["writers"]) >= 1
    assert len(flags_entry["readers"]) >= 1


def test_references_filtered_by_symbol_type():
    """A symbol_type filter blanks the other tables."""
    analyzer = DataflowAnalyzer(Path("games/demo_pack"))
    refs = analyzer.references("ending_lover", symbol_type="flags")
    assert refs["flags"] is not None
    assert refs["scenes"] is None
    assert refs["items"] is None
    assert refs["resources"] is None


def test_plugin_flag_condition_is_a_conservative_reader() -> None:
    """Plugin ``*flag*`` predicates keep custom state visible to dataflow."""
    scenes = {
        "start": Scene(
            id="start",
            requires=[
                Condition(
                    kind="days_since_flag_gte",
                    target="repair_day",
                    value=1,
                ),
            ],
        ),
    }
    analyzer = DataflowAnalyzer(Path("."))
    analyzer._state = SimpleNamespace(
        story=SimpleNamespace(scenes=scenes),
        map=SimpleNamespace(locations={}),
    )

    usage = analyzer.analyze().flags["repair_day"]

    assert usage.writers == []
    assert [(ref.kind, ref.role) for ref in usage.readers] == [
        ("days_since_flag_gte", "read"),
    ]


# ----------------------------------------------------------------------
# Conditioned edges


def test_conditioned_edges_nonempty_with_at_least_one_guard():
    analyzer = DataflowAnalyzer(Path("games/demo_pack"))
    edges = analyzer.conditioned_edges()
    assert isinstance(edges, list)
    assert edges
    assert all(isinstance(e, Edge) for e in edges)
    # Some demo choices have requires, so at least one edge is guarded.
    assert any(e.guard for e in edges)


def test_guarded_edge_carries_requires_shape():
    """A guarded edge's guard entries use the {requires|forbids: kind} shape."""
    analyzer = DataflowAnalyzer(Path("games/demo_pack"))
    guards = [g for e in analyzer.conditioned_edges() for g in e.guard]
    assert guards
    # Every guard dict tags the gating role and carries a target slot.
    for g in guards:
        assert ("requires" in g) or ("forbids" in g)
        assert "target" in g


# ----------------------------------------------------------------------
# Declared-flag reconciliation


def test_declared_flags_all_match_clean():
    """With the real 12 flags declared, nothing is undeclared or unused."""
    analyzer = DataflowAnalyzer(Path("games/demo_pack"))
    report = analyzer.analyze(declared_flags=set(DEMO_FLAGS))
    assert report.undeclared_flags == []
    assert report.unused_declared_flags == []


def test_declared_flags_detects_undeclared_and_unused():
    """A bogus single declaration surfaces both gaps."""
    analyzer = DataflowAnalyzer(Path("games/demo_pack"))
    report = analyzer.analyze(declared_flags={"only_this"})
    assert report.undeclared_flags  # real flags are not declared
    assert report.unused_declared_flags == ["only_this"]


def test_edge_guard_logic_is_structured() -> None:
    """Every edge exposes the canonical {all, none} boolean form, and it
    mirrors the legacy flat guard's targets (no information lost)."""
    report = DataflowAnalyzer(Path("games/demo_pack")).analyze()
    for e in report.edges:
        assert set(e.guard_logic) == {"all", "none"}
        for entry in e.guard_logic["all"] + e.guard_logic["none"]:
            assert set(entry) == {"kind", "target", "value"}
            assert entry["kind"], "a guard condition must carry its kind"
    gated = [e for e in report.edges if e.guard]
    assert gated, "demo_pack should have at least one gated edge"
    for e in gated:
        flat_targets = {g.get("target") for g in e.guard}
        logic_targets = {c["target"]
                         for c in e.guard_logic["all"] + e.guard_logic["none"]}
        assert flat_targets == logic_targets


def test_play_scene_branch_edges_references_and_ordered_guards() -> None:
    """Branch cases and default are references with first-match guard logic."""

    branch = Effect(
        kind="play_scene_branch",
        target="fallback",
        value=[
            {
                "when": {"kind": "flag", "target": "route_a"},
                "target": "route_a_scene",
            },
            {
                "when": {
                    "kind": "scene_played",
                    "target": "checkpoint",
                },
                "target": "route_b_scene",
            },
        ],
    )
    scenes = {
        "start": Scene(
            id="start",
            requires=[Condition(kind="flag", target="outer_gate")],
            lines=[],
            on_end=[branch],
        ),
        "fallback": Scene(id="fallback"),
        "route_a_scene": Scene(id="route_a_scene"),
        "route_b_scene": Scene(id="route_b_scene"),
        "checkpoint": Scene(id="checkpoint"),
    }
    analyzer = DataflowAnalyzer(Path("."))
    analyzer._state = SimpleNamespace(
        story=SimpleNamespace(scenes=scenes),
        map=SimpleNamespace(locations={}),
    )

    report = analyzer.analyze()
    branch_edges = {
        edge.dst: edge
        for edge in report.edges
        if edge.src == "start"
    }

    assert set(branch_edges) == {
        "fallback",
        "route_a_scene",
        "route_b_scene",
    }
    assert branch_edges["route_a_scene"].guard_logic == {
        "all": [
            {"kind": "flag", "target": "outer_gate", "value": None},
            {"kind": "flag", "target": "route_a", "value": None},
        ],
        "none": [],
    }
    assert branch_edges["route_b_scene"].guard_logic == {
        "all": [
            {"kind": "flag", "target": "outer_gate", "value": None},
            {
                "kind": "scene_played",
                "target": "checkpoint",
                "value": None,
            },
        ],
        "none": [
            {"kind": "flag", "target": "route_a", "value": None},
        ],
    }
    assert branch_edges["fallback"].guard_logic == {
        "all": [
            {"kind": "flag", "target": "outer_gate", "value": None},
        ],
        "none": [
            {"kind": "flag", "target": "route_a", "value": None},
            {
                "kind": "scene_played",
                "target": "checkpoint",
                "value": None,
            },
        ],
    }
    for target in ("fallback", "route_a_scene", "route_b_scene"):
        assert any(
            ref.kind == "play_scene_branch"
            for ref in report.scenes[target].writers
        )
    assert any(
        ref.kind == "flag"
        for ref in report.flags["route_a"].readers
    )
    assert any(
        ref.kind == "scene_played"
        for ref in report.scenes["checkpoint"].readers
    )
