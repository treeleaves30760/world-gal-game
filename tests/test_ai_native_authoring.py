"""AI-Coding-Native authoring surface: JSON-Schema export, validator arg-model
warnings, manifest<->registry declarations + reconciliation, PackEditor
did-you-mean, and reference-doc drift (Phases 1-2)."""
from __future__ import annotations

import pathlib

import pytest

import world_gal_game
import world_gal_game.plugins  # populate registries

_REPO = pathlib.Path(world_gal_game.__file__).parent.parent


# ----- Phase 1: JSON-Schema export ---------------------------------------

def test_schema_document_has_kinds_and_models():
    from world_gal_game.dev.capability_manifest import schema_document
    doc = schema_document()
    assert {"effects", "conditions", "models"} <= set(doc)
    # builtin kinds present, each a real JSON Schema (has properties or is empty)
    assert "affection" in doc["effects"]
    assert "affection_gte" in doc["conditions"]
    assert {"Effect", "Condition", "Line", "Scene", "Choice"} <= set(doc["models"])


def test_affection_schema_is_accurate():
    from world_gal_game.dev.capability_manifest import schema_document
    aff = schema_document()["effects"]["affection"]
    assert set(aff["required"]) == {"target", "value"}
    assert aff["properties"]["value"]["type"] == "integer"
    assert aff["properties"]["target"]["minLength"] == 1


def test_camera_pan_schema_has_nested_value_model():
    from world_gal_game.dev.capability_manifest import schema_document
    cam = schema_document()["effects"]["camera_pan"]
    assert "$defs" in cam and "CameraPanValue" in cam["$defs"]


def test_manifest_emits_args_schema_alongside_signature():
    from world_gal_game.dev.capability_manifest import build_manifest
    aff = next(e for e in build_manifest()["effects"] if e["kind"] == "affection")
    assert "signature" in aff and "args_schema" in aff  # both, not either


# ----- Phase 1: validator arg-model checks (warning-level) ---------------

def test_validator_warns_on_bad_arg_not_errors():
    from world_gal_game.validator import _validate_effect_raw
    issues = _validate_effect_raw(
        {"kind": "affection", "target": "a", "value": "abc"},
        file="x", path="p")
    assert issues and all(i.severity == "warning" for i in issues)


def test_validator_tolerates_omitted_optional_value():
    from world_gal_game.validator import _validate_effect_raw
    # bare advance_time (no value) must not warn — value is optional there.
    assert _validate_effect_raw({"kind": "advance_time"}, file="x", path="p") == []


def test_demo_pack_validates_without_errors():
    from world_gal_game.validator import validate_pack
    issues = validate_pack(_REPO / "games" / "demo_pack")
    errors = [i for i in issues if i.severity == "error"]
    assert errors == [], f"unexpected errors: {[i.message for i in errors]}"


# ----- Phase 2: manifest <-> registry --------------------------------------

def test_manifest_declares_class_extension_points():
    from world_gal_game.plugins.manifest import PluginManifest
    m = PluginManifest.model_validate({
        "id": "demo_x",
        "extends": {"widgets": [{"kind": "badge"}], "scenes": [{"kind": "mini"}],
                    "brains": [{"kind": "llama"}], "dialogue_ops": [{"kind": "op"}]},
    })
    assert [d.kind for d in m.extends.widgets] == ["badge"]
    assert [d.kind for d in m.extends.scenes] == ["mini"]
    assert [d.kind for d in m.extends.brains] == ["llama"]
    assert [d.kind for d in m.extends.dialogue_ops] == ["op"]


def test_reconcile_warns_on_declared_but_unregistered():
    from world_gal_game.plugins.manager import PluginManager, PluginRecord
    from world_gal_game.plugins.manifest import PluginManifest
    rec = PluginRecord(
        manifest=PluginManifest.model_validate(
            {"id": "demo_x", "extends": {"widgets": [{"kind": "ghost"}]}}),
        root=_REPO, source="pack")
    rec.widget_names = []  # nothing registered
    PluginManager(pack_root=None)._reconcile_declarations(rec)
    assert any("ghost" in w for w in rec.warnings)


# ----- Phase 2: PackEditor did-you-mean ----------------------------------

def test_pack_editor_suggests_for_typo_kind():
    from world_gal_game.core.story_graph import Choice
    from world_gal_game.dev.pack_editor import PackEditError, PackEditor
    with pytest.raises(PackEditError) as ei:
        PackEditor._validate(Choice, "add_choice", {
            "id": "c", "text": "go",
            "effects": [{"kind": "affecton", "target": "a", "value": 1}]},
            path="choice")
    assert "affection" in (ei.value.hint or "")


def test_pack_editor_allows_unknown_kind_without_close_match():
    from world_gal_game.core.story_graph import Choice
    from world_gal_game.dev.pack_editor import PackEditor
    # No close match -> assume a plugin kind not loaded here; do not block.
    obj = PackEditor._validate(Choice, "add_choice", {
        "id": "c", "text": "go",
        "effects": [{"kind": "zzqwxnope", "target": "a"}]}, path="choice")
    assert obj.effects[0].kind == "zzqwxnope"


# ----- Phase 2: reference docs stay in sync ------------------------------

def test_reference_docs_in_sync_with_manifest():
    # Run the drift guard in a *fresh* process: the on-disk docs are generated
    # builtin-only (no --pack), but other tests in this suite leave plugin kinds
    # in the global registry, so an in-process render would see a superset.
    # Subprocess isolation matches how the docs are actually produced/checked.
    import subprocess
    import sys
    r = subprocess.run(
        [sys.executable, str(_REPO / "tools" / "gen_references.py"), "--check"],
        cwd=_REPO, capture_output=True, text=True)
    assert r.returncode == 0, (
        f"reference docs stale; run `uv run python tools/gen_references.py`\n"
        f"{r.stdout}{r.stderr}")
