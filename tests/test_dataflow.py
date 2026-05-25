"""Tests for DataflowAnalyzer against demo_pack.

Exercises the cross-reference report (where flags / scenes are written and
read), the per-symbol ``references`` lookup, the conditioned scene -> scene
edges, and the declared-flag reconciliation (undeclared / unused).
"""
from __future__ import annotations

from pathlib import Path

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
