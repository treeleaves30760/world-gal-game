"""Tests for the Capability Manifest builder."""
from __future__ import annotations

import json

import pytest

from world_gal_game.dev.capability_manifest import (
    all_condition_kinds, all_effect_kinds, all_hook_events,
    all_easing_names, all_line_fields, all_richtext_tags,
    build_manifest, find_condition, find_effect, line_field_schema,
    manifest_json, summary_table,
)
from world_gal_game.plugins import effect, condition, snapshot, restore


@pytest.fixture
def clean_registry():
    snap = snapshot()
    yield
    restore(snap)


def test_manifest_includes_engine_version():
    m = build_manifest()
    assert "engine_version" in m
    assert m["engine_version"]  # non-empty


def test_manifest_includes_39_builtin_kinds():
    m = build_manifest()
    eff_kinds = {e["kind"] for e in m["effects"]}
    cond_kinds = {c["kind"] for c in m["conditions"]}
    assert "affection" in eff_kinds
    assert "set_flag" in eff_kinds
    assert "flag" in cond_kinds
    assert "scene_played" in cond_kinds
    assert len(eff_kinds) >= 23
    assert len(cond_kinds) >= 16


def test_manifest_includes_signature_for_builtin():
    aff = find_effect("affection")
    assert aff is not None
    assert aff["signature"].get("target") == "character_id"
    assert aff["plugin_id"] == "builtin"


def test_manifest_hooks_list_includes_phase2_events():
    m = build_manifest()
    events = m["hooks"]["events"]
    # Phase 1 events
    assert "effect.before_apply" in events
    assert "effect.after_apply" in events
    assert "pack.after_load" in events
    # Phase 2 additions
    assert "scene.push" in events
    assert "scene.pop" in events
    assert "dialogue.before_line" in events
    assert "player.move" in events
    assert "time.advance" in events
    assert len(events) >= 14


def test_plugin_added_kind_appears_in_manifest(clean_registry):
    @effect("__test_custom__", plugin_id="testbed",
            description="just a test")
    def fn(state, eff): return {"ok": True}

    m = build_manifest()
    entry = find_effect("__test_custom__")
    assert entry is not None
    assert entry["plugin_id"] == "testbed"
    assert entry["description"] == "just a test"


def test_manifest_json_is_valid_json():
    txt = manifest_json()
    parsed = json.loads(txt)
    assert parsed["engine_version"]
    assert isinstance(parsed["effects"], list)


def test_summary_table_returns_string():
    out = summary_table()
    assert "Effects" in out
    assert "Conditions" in out
    assert "Hook events" in out


def test_all_kinds_helpers_match_manifest():
    m = build_manifest()
    assert sorted(all_effect_kinds()) == sorted(
        e["kind"] for e in m["effects"])
    assert sorted(all_condition_kinds()) == sorted(
        c["kind"] for c in m["conditions"])
    assert all_hook_events() == m["hooks"]["events"]


def test_find_unknown_returns_none():
    assert find_effect("___nonexistent_kind___") is None
    assert find_condition("___nonexistent_kind___") is None


def test_manifest_includes_content_schema():
    m = build_manifest()
    cs = m["content_schema"]
    assert "Line" in cs and "Scene" in cs and "PortraitSpec" in cs
    text = cs["Line"]["text"]
    assert text["required"] is True
    assert text["type"] == "str"
    # Optional field with a default carries required=False + the default value.
    portraits = cs["Line"]["portraits"]
    assert portraits["required"] is False
    assert portraits["default"] == []


def test_portraitspec_slot_allowed_values():
    m = build_manifest()
    slot = m["content_schema"]["PortraitSpec"]["slot"]
    assert slot["allowed_values"] == ["left", "center", "right"]
    assert slot["default"] == "center"


def test_all_line_fields_matches_model():
    fields = all_line_fields()
    assert "text" in fields and "speaker" in fields and "portraits" in fields
    assert set(fields) == set(line_field_schema().keys())


def test_markup_section_present():
    m = build_manifest()
    markup = m["markup"]
    for key in ("richtext_tags", "dialogue_ops", "interpolation_tokens",
                "easing", "portrait_animations"):
        assert key in markup
    assert "linear" in markup["easing"]
    assert all_easing_names() == markup["easing"]
    # richtext_tags / portrait_animations may be empty until VN modules land.
    assert isinstance(markup["richtext_tags"], list)
    assert all_richtext_tags() == markup["richtext_tags"]


def test_manifest_content_schema_is_json_serializable():
    parsed = json.loads(manifest_json())
    assert "content_schema" in parsed
    assert "markup" in parsed


def test_manifest_with_pack_manager_includes_plugins():
    """When given a real PluginManager, manifest reports loaded plugins."""
    from world_gal_game.config import EngineConfig
    from world_gal_game.headless import HeadlessSession

    snap = snapshot()
    try:
        sess = HeadlessSession.open(EngineConfig(), pack="demo_pack")
        manager = sess.state.meta["__plugin_manager__"]
        m = build_manifest(manager=manager)
        assert m["plugins"]["manager_available"] is True
        loaded_ids = [p["id"] for p in m["plugins"]["loaded"]]
        # step_counter is shipped with demo_pack
        assert "step_counter" in loaded_ids
    finally:
        restore(snap)
