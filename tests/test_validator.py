"""Tests for world_gal_game.validator.

Each test builds a minimal tmp_path pack, injects a specific defect, and
asserts that validate_pack returns exactly the expected issues.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from world_gal_game.validator import ValidationIssue, validate_pack


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh, allow_unicode=True, default_flow_style=False)


def _make_minimal_pack(root: Path) -> None:
    """Write a structurally valid minimal pack with no errors."""
    content = root / "content"

    _write(content / "characters.yaml", {"characters": [
        {"id": "hero_a", "name": "Hero A"},
    ]})

    _write(content / "locations.yaml", {"locations": [
        {"id": "loc_start", "name": "Start", "exits": []},
    ]})

    _write(content / "items.yaml", {"items": [
        {"id": "item_key", "name": "Key"},
    ]})

    _write(content / "resources.yaml", {"resources": [
        {"id": "money", "name": "Money"},
    ]})

    scenes_dir = content / "scenes"
    _write(scenes_dir / "main.yaml", {"scenes": [
        {
            "id": "scene_intro",
            "title": "Intro",
            "lines": [{"text": "Hello."}],
            "choices": [
                {
                    "id": "ch_continue",
                    "text": "Continue",
                    "next_scene": "scene_end",
                }
            ],
        },
        {
            "id": "scene_end",
            "title": "End",
            "lines": [{"text": "Done."}],
        },
    ]})

    _write(content / "achievements.yaml", {"achievements": [
        {
            "id": "ach_win",
            "title": "You Win",
            "requires": [{"kind": "flag", "target": "done"}],
        }
    ]})


# ---------------------------------------------------------------------------
# Test 1: valid pack produces 0 errors
# ---------------------------------------------------------------------------

def test_valid_pack_no_errors(tmp_path: Path) -> None:
    _make_minimal_pack(tmp_path)
    issues = validate_pack(tmp_path)
    errors = [i for i in issues if i.severity == "error"]
    assert errors == [], f"Unexpected errors: {errors}"


# ---------------------------------------------------------------------------
# Test 2: typo field name in choice -> 1 error with hint
# ---------------------------------------------------------------------------

def test_typo_choice_field_requirss(tmp_path: Path) -> None:
    _make_minimal_pack(tmp_path)
    scenes_dir = tmp_path / "content" / "scenes"
    _write(scenes_dir / "bad.yaml", {"scenes": [
        {
            "id": "scene_bad_choice",
            "title": "Bad",
            "lines": [{"text": "Hello."}],
            "choices": [
                {
                    "id": "ch_bad",
                    "text": "Pick me",
                    "requirss": [{"kind": "flag", "target": "x"}],  # typo
                    "next_scene": "scene_end",
                }
            ],
        }
    ]})

    issues = validate_pack(tmp_path)
    errors = [i for i in issues if i.severity == "error"]
    assert len(errors) >= 1

    typo_error = next(
        (i for i in errors if "requirss" in i.message),
        None,
    )
    assert typo_error is not None, f"Expected typo error, got: {errors}"
    assert typo_error.hint is not None, "Expected a hint for the typo"
    assert "requires" in typo_error.hint


# ---------------------------------------------------------------------------
# Test 3: invalid effect kind -> 1 error with hint
# ---------------------------------------------------------------------------

def test_invalid_effect_kind_affecton(tmp_path: Path) -> None:
    _make_minimal_pack(tmp_path)
    scenes_dir = tmp_path / "content" / "scenes"
    _write(scenes_dir / "bad_effect.yaml", {"scenes": [
        {
            "id": "scene_bad_effect",
            "title": "Bad Effect",
            "lines": [{"text": "Hello."}],
            "on_end": [
                {"kind": "affecton", "target": "hero_a", "value": 5},  # typo
            ],
        }
    ]})

    issues = validate_pack(tmp_path)
    errors = [i for i in issues if i.severity == "error"]
    assert len(errors) >= 1

    kind_error = next(
        (i for i in errors if "affecton" in i.message),
        None,
    )
    assert kind_error is not None, f"Expected effect kind error, got: {errors}"
    assert kind_error.hint is not None, "Expected hint for bad effect kind"
    assert "affection" in kind_error.hint


# ---------------------------------------------------------------------------
# Test 4: choice.next_scene points to unknown scene id -> 1 error
# ---------------------------------------------------------------------------

def test_unknown_next_scene(tmp_path: Path) -> None:
    _make_minimal_pack(tmp_path)
    scenes_dir = tmp_path / "content" / "scenes"
    _write(scenes_dir / "bad_ref.yaml", {"scenes": [
        {
            "id": "scene_ref_test",
            "title": "Ref Test",
            "lines": [{"text": "Hello."}],
            "choices": [
                {
                    "id": "ch_ghost",
                    "text": "Go somewhere",
                    "next_scene": "scene_does_not_exist",
                }
            ],
        }
    ]})

    issues = validate_pack(tmp_path)
    errors = [i for i in issues if i.severity == "error"]
    ref_errors = [
        i for i in errors if "scene_does_not_exist" in i.message
    ]
    assert len(ref_errors) >= 1, f"Expected unknown scene id error, got: {errors}"


# ---------------------------------------------------------------------------
# Test 5: effect.target points to unknown character -> 1 error
# ---------------------------------------------------------------------------

def test_unknown_character_in_effect_target(tmp_path: Path) -> None:
    _make_minimal_pack(tmp_path)
    scenes_dir = tmp_path / "content" / "scenes"
    _write(scenes_dir / "bad_char.yaml", {"scenes": [
        {
            "id": "scene_bad_char",
            "title": "Bad Char",
            "lines": [{"text": "Hello."}],
            "on_end": [
                {"kind": "affection", "target": "ghost_npc_xyz", "value": 10},
            ],
        }
    ]})

    issues = validate_pack(tmp_path)
    errors = [i for i in issues if i.severity == "error"]
    char_errors = [
        i for i in errors if "ghost_npc_xyz" in i.message
    ]
    assert len(char_errors) >= 1, f"Expected unknown character error, got: {errors}"


# ---------------------------------------------------------------------------
# Test 6: achievement with no requires -> 1 warning
# ---------------------------------------------------------------------------

def test_achievement_no_requires_warning(tmp_path: Path) -> None:
    _make_minimal_pack(tmp_path)
    content = tmp_path / "content"
    _write(content / "achievements.yaml", {"achievements": [
        {
            "id": "ach_free",
            "title": "Free Achievement",
            # No 'requires' — should trigger a warning
        }
    ]})

    issues = validate_pack(tmp_path)
    warnings = [i for i in issues if i.severity == "warning"]
    ach_warnings = [
        i for i in warnings if "ach_free" in i.message
    ]
    assert len(ach_warnings) >= 1, f"Expected achievement warning, got warnings: {warnings}"


# ---------------------------------------------------------------------------
# Test 7: validate_pack returns ValidationIssue dataclasses
# ---------------------------------------------------------------------------

def test_issue_type_is_dataclass(tmp_path: Path) -> None:
    _make_minimal_pack(tmp_path)
    issues = validate_pack(tmp_path)
    for iss in issues:
        assert isinstance(iss, ValidationIssue)
        assert iss.severity in ("error", "warning")
        assert isinstance(iss.file, str)
        assert isinstance(iss.path, str)
        assert isinstance(iss.message, str)
