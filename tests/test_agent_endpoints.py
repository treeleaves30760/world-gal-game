"""Tests for the aggregate agent endpoints and onboarding bundle.

Covers ``build_context`` / ``analyze_impact``
(:mod:`world_gal_game.dev.agent_endpoints`) and the self-contained onboarding
bundle (:mod:`world_gal_game.dev.agent_bundle`) against demo_pack.
"""
from __future__ import annotations

import json
from pathlib import Path

from world_gal_game.dev.agent_bundle import (
    agent_guide_text,
    build_bundle,
    export_bundle,
    recipes,
    session_protocol_schema,
)
from world_gal_game.dev.agent_endpoints import (
    analyze_impact,
    build_context,
    pack_brief,
    symbol_card,
)

DEMO = Path("games/demo_pack")


# ----------------------------------------------------------------------
# pack_brief / symbol_card (token-frugal comprehension surface)


def test_pack_brief_shape_and_compactness() -> None:
    brief = pack_brief(DEMO)
    assert set(brief) >= {"title", "counts", "start", "scenes", "endings",
                          "variables", "gaps"}
    # Adjacency is id -> [targets] (no verbose Reference objects).
    assert all(isinstance(v, list) for v in brief["scenes"].values())
    # The brief is much smaller than the full context aggregate.
    assert len(json.dumps(pack_brief(DEMO))) < len(json.dumps(build_context(DEMO)))


def test_pack_brief_text_is_terse_string() -> None:
    text = pack_brief(DEMO, as_text=True)
    assert isinstance(text, str)
    assert text.startswith("# ")          # title header
    assert "scenes:" in text and "endings:" in text
    # Text form is the tersest: smaller than the JSON form.
    assert len(text) < len(json.dumps(pack_brief(DEMO)))


def test_symbol_card_scene() -> None:
    card = symbol_card(DEMO, "return_to_heroine")
    assert card["type"] == "scene"
    assert card["reachable"] is True
    # outgoing edges carry the structured guard logic.
    assert card["outgoing"]
    assert all(set(e["guard"]) == {"all", "none"} for e in card["outgoing"])


def test_symbol_card_flag() -> None:
    card = symbol_card(DEMO, "quest_started")
    assert card["type"] == "flag"
    assert card["declared"] is True
    assert card["spec"]["type"] == "bool"
    assert card["writers"], "quest_started is set by at least one choice"


def test_symbol_card_unknown_suggests() -> None:
    card = symbol_card(DEMO, "quest_strted")  # typo
    assert card["type"] == "unknown"
    assert "quest_started" in card["did_you_mean"]


# ----------------------------------------------------------------------
# build_context


def test_context_has_all_sections():
    ctx = build_context(DEMO)
    for key in ("pack", "variables", "reachability", "scene_graph",
                "dataflow", "coverage", "gaps"):
        assert key in ctx
    # JSON-serializable end to end.
    json.dumps(ctx, ensure_ascii=False)


def test_context_dataflow_digest_counts_by_default():
    ctx = build_context(DEMO)
    flags = ctx["dataflow"]["flags"]
    assert "ending_lover" in flags
    # Digest form: per-symbol writer/reader counts, not the full ref lists.
    assert set(flags["ending_lover"]) == {"writers", "readers"}
    assert flags["ending_lover"]["writers"] >= 1


def test_context_full_dataflow_emits_reports():
    ctx = build_context(DEMO, full_dataflow=True)
    # Full form: the DataflowReport model dump (writers is a list of refs).
    assert isinstance(ctx["dataflow"]["flags"]["ending_lover"]["writers"], list)


def test_context_coverage_totals_without_script():
    ctx = build_context(DEMO)
    totals = ctx["coverage"]["totals"]
    assert totals["scenes"] > 0 and totals["endings"] > 0
    assert ctx["coverage"]["report"] is None


def test_context_scene_graph_matches_reachability_universe():
    ctx = build_context(DEMO)
    graph_scenes = set(ctx["scene_graph"])
    reach = ctx["reachability"]
    assert graph_scenes == set(reach["reachable"]) | set(reach["unreachable"])


# ----------------------------------------------------------------------
# analyze_impact


def test_impact_flag_reports_readers_and_at_risk_ending():
    result = analyze_impact(DEMO, "ending_lover", probe_reachability=False)
    assert result["symbol"] == "ending_lover"
    assert result["symbol_type"] == "flags"
    assert result["writers"]
    assert result["readers"]
    # ending_lover gates the ending of the same name.
    assert "ending_lover" in result["at_risk_endings"]
    assert result["reachable_today"] is None


def test_impact_unknown_symbol_is_empty_not_error():
    result = analyze_impact(DEMO, "no_such_symbol_xyz", probe_reachability=False)
    assert result["writers"] == []
    assert result["readers"] == []
    assert result["at_risk_endings"] == []
    assert result["symbol_type"] is None


def test_impact_probe_returns_per_ending_verdict():
    result = analyze_impact(DEMO, "ending_lover", probe_reachability=True)
    probe = result["reachable_today"]
    assert isinstance(probe, dict)
    assert "ending_lover" in probe
    assert "found" in probe["ending_lover"]


# ----------------------------------------------------------------------
# Onboarding bundle


def test_agent_guide_text_is_markdown():
    text = agent_guide_text()
    assert text.startswith("# World Gal-Game")
    assert "wgg context" in text


def test_session_protocol_schema_shape():
    schema = session_protocol_schema()
    assert schema["atomicity"]["model"].startswith("best-effort")
    shapes = {s["shape"] for s in schema["message_shapes"]}
    assert shapes == {"control", "batch", "single"}
    assert "snapshot" in schema["ops"] and "restore" in schema["ops"]


def test_recipes_are_actionable():
    items = recipes()
    assert items
    for r in items:
        assert {"goal", "cli", "note"} <= set(r)


def test_build_bundle_contents():
    bundle = build_bundle()
    assert set(bundle) == {
        "agent-guide.md", "capabilities.json", "capabilities.schema.json",
        "session-protocol.json", "recipes.json",
    }
    # The JSON artifacts parse.
    json.loads(bundle["capabilities.json"])
    json.loads(bundle["capabilities.schema.json"])
    json.loads(bundle["session-protocol.json"])
    json.loads(bundle["recipes.json"])


def test_export_bundle_writes_files(tmp_path):
    written = export_bundle(str(tmp_path))
    names = {Path(p).name for p in written}
    assert "agent-guide.md" in names
    for p in written:
        assert Path(p).is_file()
