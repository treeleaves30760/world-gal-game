"""VariableSpec / VariableManifest: typed flag declarations and validation."""
from __future__ import annotations

from pathlib import Path

import pytest

from world_gal_game.core.variable_spec import VariableManifest, VariableSpec

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_VARIABLES = REPO_ROOT / "games" / "demo_pack" / "content" / "variables.yaml"

EXPECTED_KEYS = {
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


def test_load_demo_manifest():
    manifest = VariableManifest.load(DEMO_VARIABLES)
    assert len(manifest.keys()) == 12
    assert set(manifest.keys()) == EXPECTED_KEYS
    for key in EXPECTED_KEYS:
        assert manifest.declared(key) is True
    lover = manifest.get("ending_lover")
    assert lover is not None
    assert lover.type == "bool"
    assert lover.coerced_default() is False


def test_validate_value_messages():
    manifest = VariableManifest.load(DEMO_VARIABLES)
    assert manifest.validate_value("ending_lover", True) is None
    bad_type = manifest.validate_value("ending_lover", "x")
    assert bad_type is not None
    assert "bool" in bad_type
    undeclared = manifest.validate_value("nope", True)
    assert undeclared is not None
    assert "undeclared" in undeclared


def test_inline_int_spec_accepts_and_rejects():
    spec = VariableSpec(key="score", type="int", default="3")
    assert spec.coerced_default() == 3
    assert spec.accepts(5) is True
    assert spec.accepts("a") is False
    assert spec.accepts(True) is False


def test_inline_float_spec_accepts():
    spec = VariableSpec(key="ratio", type="float")
    assert spec.accepts(1.5) is True
    assert spec.accepts(2) is False
    assert spec.accepts(True) is False


def test_inline_enum_spec_domain():
    spec = VariableSpec(
        key="season", type="enum", values=["spring", "autumn"]
    )
    assert spec.accepts("spring") is True
    assert spec.accepts("winter") is False
    assert spec.coerced_default() == "spring"


def test_coerced_default_fallbacks_per_type():
    assert VariableSpec(key="b", type="bool").coerced_default() is False
    assert VariableSpec(key="i", type="int").coerced_default() == 0
    assert VariableSpec(key="f", type="float").coerced_default() == 0.0
    assert VariableSpec(key="s", type="str").coerced_default() == ""
    enum_spec = VariableSpec(key="e", type="enum", values=["x", "y"])
    assert enum_spec.coerced_default() == "x"


def test_enum_empty_values_raises():
    with pytest.raises(ValueError):
        VariableSpec(key="season", type="enum", values=[])


def test_enum_default_not_in_values_raises():
    with pytest.raises(ValueError):
        VariableSpec(
            key="season",
            type="enum",
            values=["spring", "autumn"],
            default="winter",
        )


def test_load_missing_file_is_empty():
    manifest = VariableManifest.load(Path("/no/such/file.yaml"))
    assert manifest.keys() == []
    assert manifest.declared("anything") is False


def test_from_items_list_and_mapping_shapes():
    from_list = VariableManifest.from_items(
        [
            {"key": "a", "type": "bool", "default": False},
            {"key": "b", "type": "int", "default": 2},
        ]
    )
    assert from_list.keys() == ["a", "b"]
    assert from_list.get("b").coerced_default() == 2

    from_mapping = VariableManifest.from_items(
        {
            "a": {"type": "bool", "default": False},
            "b": {"type": "int", "default": 2},
        }
    )
    assert from_mapping.keys() == ["a", "b"]
    assert from_mapping.get("b").coerced_default() == 2


def test_from_items_list_missing_key_raises():
    with pytest.raises(ValueError):
        VariableManifest.from_items([{"type": "bool"}])


def test_defaults_for_demo_manifest():
    manifest = VariableManifest.load(DEMO_VARIABLES)
    defaults = manifest.defaults()
    assert len(defaults) == 12
    assert all(value is False for value in defaults.values())
