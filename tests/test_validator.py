"""Tests for world_gal_game.validator.

Each test builds a minimal tmp_path pack, injects a specific defect, and
asserts that validate_pack returns exactly the expected issues.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from world_gal_game.validator import (
    ValidationIssue, validate_pack, validate_for_web,
)


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


# ---------------------------------------------------------------------------
# C-WS2.3: asset existence (warning)
# ---------------------------------------------------------------------------

def test_missing_voice_file_warns(tmp_path: Path) -> None:
    _make_minimal_pack(tmp_path)
    scenes_dir = tmp_path / "content" / "scenes"
    _write(scenes_dir / "voiced.yaml", {"scenes": [
        {
            "id": "scene_voiced",
            "title": "Voiced",
            "lines": [
                {"text": "Hi.", "voice": "assets/voice/missing.ogg"},
            ],
        }
    ]})
    issues = validate_pack(tmp_path)
    errors = [i for i in issues if i.severity == "error"]
    assert errors == [], f"asset miss must not be an error: {errors}"
    warnings = [i for i in issues if i.severity == "warning"]
    voice_w = [w for w in warnings if "missing.ogg" in w.message]
    assert len(voice_w) == 1, f"expected one voice warning, got: {warnings}"


def test_present_voice_file_ok(tmp_path: Path) -> None:
    _make_minimal_pack(tmp_path)
    # Create the referenced asset so the check passes silently.
    voice = tmp_path / "assets" / "voice" / "present.ogg"
    voice.parent.mkdir(parents=True, exist_ok=True)
    voice.write_bytes(b"\x00")
    scenes_dir = tmp_path / "content" / "scenes"
    _write(scenes_dir / "voiced_ok.yaml", {"scenes": [
        {
            "id": "scene_voiced_ok",
            "title": "Voiced OK",
            "lines": [
                {"text": "Hi.", "voice": "assets/voice/present.ogg"},
            ],
        }
    ]})
    issues = validate_pack(tmp_path)
    voice_issues = [i for i in issues if "present.ogg" in i.message]
    assert voice_issues == [], f"present asset should not warn: {voice_issues}"


def test_missing_portrait_all_candidates_warns(tmp_path: Path) -> None:
    _make_minimal_pack(tmp_path)
    scenes_dir = tmp_path / "content" / "scenes"
    _write(scenes_dir / "portrait.yaml", {"scenes": [
        {
            "id": "scene_portrait",
            "title": "Portrait",
            "lines": [
                {"text": "Hi.", "portraits": [
                    {"character": "nobody_here"},
                ]},
            ],
        }
    ]})
    issues = validate_pack(tmp_path)
    errors = [i for i in issues if i.severity == "error"]
    assert errors == [], f"missing portrait must not error: {errors}"
    warnings = [i for i in issues if i.severity == "warning"]
    port_w = [w for w in warnings if "nobody_here" in w.message]
    assert len(port_w) == 1, f"expected one portrait warning, got: {warnings}"


# ---------------------------------------------------------------------------
# C-WS2.3: rich-text well-formedness (error)
# ---------------------------------------------------------------------------

def test_unbalanced_richtext_tag_errors(tmp_path: Path) -> None:
    _make_minimal_pack(tmp_path)
    scenes_dir = tmp_path / "content" / "scenes"
    _write(scenes_dir / "rich_bad.yaml", {"scenes": [
        {
            "id": "scene_rich_bad",
            "title": "Rich Bad",
            "lines": [{"text": "[b]never closed"}],
        }
    ]})
    issues = validate_pack(tmp_path)
    errors = [i for i in issues if i.severity == "error"]
    rich_errors = [e for e in errors if "富文本" in e.message]
    assert len(rich_errors) >= 1, f"expected rich-text error, got: {errors}"


def test_unknown_richtext_tag_errors(tmp_path: Path) -> None:
    _make_minimal_pack(tmp_path)
    scenes_dir = tmp_path / "content" / "scenes"
    # "[bold]" is not a known tag (the known one is "[b]").
    _write(scenes_dir / "rich_unknown.yaml", {"scenes": [
        {
            "id": "scene_rich_unknown",
            "title": "Rich Unknown",
            "lines": [{"text": "[bold]hi[/bold]"}],
        }
    ]})
    issues = validate_pack(tmp_path)
    errors = [i for i in issues if i.severity == "error"]
    unknown = [e for e in errors if "bold" in e.message]
    assert len(unknown) >= 1, f"expected unknown-tag error, got: {errors}"


def test_unknown_richtext_tag_suggests_close_match(tmp_path: Path) -> None:
    _make_minimal_pack(tmp_path)
    scenes_dir = tmp_path / "content" / "scenes"
    # "[colr]" is a near-miss for the known tag "[color]".
    _write(scenes_dir / "rich_typo.yaml", {"scenes": [
        {
            "id": "scene_rich_typo",
            "title": "Rich Typo",
            "lines": [{"text": "[colr=#fff]hi[/colr]"}],
        }
    ]})
    issues = validate_pack(tmp_path)
    errors = [i for i in issues if i.severity == "error"]
    unknown = [e for e in errors if "colr" in e.message]
    assert len(unknown) >= 1, f"expected unknown-tag error, got: {errors}"
    assert any(e.hint and "[color]" in e.hint for e in unknown), \
        f"expected a [color] suggestion, got: {[e.hint for e in unknown]}"


def test_wellformed_richtext_no_error(tmp_path: Path) -> None:
    _make_minimal_pack(tmp_path)
    scenes_dir = tmp_path / "content" / "scenes"
    _write(scenes_dir / "rich_ok.yaml", {"scenes": [
        {
            "id": "scene_rich_ok",
            "title": "Rich OK",
            "lines": [{"text": "[color=#ffcc66][b]hi[/b][/color][w=0.5]"}],
        }
    ]})
    issues = validate_pack(tmp_path)
    rich_errors = [i for i in issues
                   if i.severity == "error" and "富文本" in i.message]
    assert rich_errors == [], f"well-formed markup should not error: {rich_errors}"


# ---------------------------------------------------------------------------
# C-WS2.3: animation / easing name validity (error)
# ---------------------------------------------------------------------------

def test_unknown_portrait_animation_errors_with_suggestion(tmp_path: Path) -> None:
    _make_minimal_pack(tmp_path)
    scenes_dir = tmp_path / "content" / "scenes"
    # "slide_lft" is a typo for "slide_left".
    _write(scenes_dir / "anim_bad.yaml", {"scenes": [
        {
            "id": "scene_anim_bad",
            "title": "Anim Bad",
            "lines": [{"text": "Hi.", "portraits": [
                {"character": "hero_a", "enter": "slide_lft"},
            ]}],
        }
    ]})
    issues = validate_pack(tmp_path)
    errors = [i for i in issues if i.severity == "error"]
    anim_errors = [e for e in errors if "slide_lft" in e.message]
    assert len(anim_errors) >= 1, f"expected animation error, got: {errors}"
    assert any(e.hint and "slide_left" in e.hint for e in anim_errors), \
        f"expected slide_left suggestion, got: {[e.hint for e in anim_errors]}"


def test_known_portrait_animation_ok(tmp_path: Path) -> None:
    _make_minimal_pack(tmp_path)
    scenes_dir = tmp_path / "content" / "scenes"
    _write(scenes_dir / "anim_ok.yaml", {"scenes": [
        {
            "id": "scene_anim_ok",
            "title": "Anim OK",
            "lines": [{"text": "Hi.", "portraits": [
                {"character": "hero_a", "enter": "fade", "exit": "slide_right"},
            ]}],
        }
    ]})
    issues = validate_pack(tmp_path)
    anim_errors = [i for i in issues
                   if i.severity == "error" and "動畫" in i.message]
    assert anim_errors == [], f"known animation should not error: {anim_errors}"


def test_unknown_easing_errors_with_suggestion(tmp_path: Path) -> None:
    _make_minimal_pack(tmp_path)
    scenes_dir = tmp_path / "content" / "scenes"
    # "out_quat" is a typo for "out_quad". easing isn't a PortraitSpec model
    # field, so this exercises the raw-dict animation check directly.
    _write(scenes_dir / "easing_bad.yaml", {"scenes": [
        {
            "id": "scene_easing_bad",
            "title": "Easing Bad",
            "lines": [{"text": "Hi.", "portraits": [
                {"character": "hero_a", "enter": "fade", "easing": "out_quat"},
            ]}],
        }
    ]})
    issues = validate_pack(tmp_path)
    errors = [i for i in issues if i.severity == "error"]
    easing_errors = [e for e in errors if "out_quat" in e.message]
    assert len(easing_errors) >= 1, f"expected easing error, got: {errors}"
    assert any(e.hint and "out_quad" in e.hint for e in easing_errors), \
        f"expected out_quad suggestion, got: {[e.hint for e in easing_errors]}"


# ---------------------------------------------------------------------------
# B1: web-target gate (validate_for_web) — opt-in, not run by validate_pack
# ---------------------------------------------------------------------------

def test_web_gate_missing_bundled_font_errors(tmp_path: Path) -> None:
    """A pack with no bundled_font is a web ERROR (CJK tofu risk)."""
    _make_minimal_pack(tmp_path)
    _write(tmp_path / "content" / "meta.yaml", {
        "title": "No Font", "pack_format_version": "0.1",
    })
    issues = validate_for_web(tmp_path)
    font_errs = [i for i in issues
                 if i.severity == "error" and i.path == "bundled_font"]
    assert len(font_errs) == 1, f"expected a bundled_font error, got: {issues}"


def test_web_gate_with_bundled_font_no_font_error(tmp_path: Path) -> None:
    """With bundled_font present, the font error is gone."""
    _make_minimal_pack(tmp_path)
    _write(tmp_path / "content" / "meta.yaml", {
        "title": "Has Font", "pack_format_version": "0.1",
        "bundled_font": "assets/fonts/font.ttf",
    })
    issues = validate_for_web(tmp_path)
    font_errs = [i for i in issues if i.path == "bundled_font"]
    assert font_errs == [], f"bundled_font present should not error: {font_errs}"


def test_web_gate_warns_on_mp3_and_wav(tmp_path: Path) -> None:
    """mp3/wav audio assets become web warnings (prefer OGG)."""
    _make_minimal_pack(tmp_path)
    _write(tmp_path / "content" / "meta.yaml", {
        "title": "Audio", "pack_format_version": "0.1",
        "bundled_font": "assets/fonts/font.ttf",
    })
    (tmp_path / "assets" / "audio").mkdir(parents=True, exist_ok=True)
    (tmp_path / "assets" / "audio" / "bgm.mp3").write_bytes(b"\x00")
    (tmp_path / "assets" / "audio" / "sfx.wav").write_bytes(b"\x00")
    issues = validate_for_web(tmp_path)
    warns = [i for i in issues if i.severity == "warning"]
    names = " ".join(w.message for w in warns)
    assert "bgm.mp3" in names and "sfx.wav" in names, f"got: {warns}"


def test_web_gate_does_not_affect_validate_pack(tmp_path: Path) -> None:
    """validate_pack must NOT fail a fontless / mp3 pack — web gate is opt-in."""
    _make_minimal_pack(tmp_path)  # no bundled_font in this minimal meta
    (tmp_path / "assets").mkdir(parents=True, exist_ok=True)
    (tmp_path / "assets" / "a.mp3").write_bytes(b"\x00")
    issues = validate_pack(tmp_path)
    # No bundled_font / mp3 errors should appear from the normal validator.
    assert not any(i.path == "bundled_font" for i in issues)
    assert not any(".mp3" in i.message for i in issues
                   if i.severity == "error")


# ---------------------------------------------------------------------------
# Expression reference vs. portrait_set (warning when the face is missing).
# ---------------------------------------------------------------------------

def _expr_warnings(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    return [i for i in issues
            if i.severity == "warning" and "portrait_set 中" in i.message]


def _pack_with_heroine_portraits(root: Path) -> None:
    """Minimal pack whose heroine declares a portrait_set (smile/sad only)."""
    _make_minimal_pack(root)
    _write(root / "content" / "characters.yaml", {"characters": [
        {"id": "hero_a", "name": "Hero A"},
        {"id": "qingyi", "name": "林青衣",
         "portrait": "assets/characters/qingyi/normal.png",
         "portrait_set": {
             "smile": "assets/characters/qingyi/smile.png",
             "sad": "assets/characters/qingyi/sad.png",
         }},
    ]})


def test_missing_expression_in_portrait_spec_warns(tmp_path: Path) -> None:
    """A ``portrait: {character, expression}`` naming an undeclared expression
    warns (it would silently fall back to the default face)."""
    _pack_with_heroine_portraits(tmp_path)
    _write(tmp_path / "content" / "scenes" / "expr.yaml", {"scenes": [
        {"id": "scene_expr", "title": "E", "lines": [
            {"speaker": "林青衣", "text": "...",
             "portrait": {"character": "qingyi", "expression": "distant"}},
        ]},
    ]})
    warns = _expr_warnings(validate_pack(tmp_path))
    assert len(warns) == 1
    assert "distant" in warns[0].message and "qingyi" in warns[0].message
    # The hint enumerates the declared expressions to guide the fix.
    assert warns[0].hint and ("smile" in warns[0].hint
                              or "distant" in warns[0].hint)


def test_missing_expression_line_level_warns(tmp_path: Path) -> None:
    """A line-level ``expression`` is resolved against the speaker's character
    and warns when that character's portrait_set lacks it."""
    _pack_with_heroine_portraits(tmp_path)
    _write(tmp_path / "content" / "scenes" / "expr.yaml", {"scenes": [
        {"id": "scene_expr", "title": "E", "lines": [
            {"speaker": "林青衣", "text": "...", "expression": "distant"},
        ]},
    ]})
    warns = _expr_warnings(validate_pack(tmp_path))
    assert len(warns) == 1
    assert "distant" in warns[0].message


def test_declared_and_default_expressions_do_not_warn(tmp_path: Path) -> None:
    """A declared expression — and the default portrait's stem ('normal') —
    are both valid and must not warn."""
    _pack_with_heroine_portraits(tmp_path)
    _write(tmp_path / "content" / "scenes" / "expr.yaml", {"scenes": [
        {"id": "scene_expr", "title": "E", "lines": [
            {"speaker": "林青衣", "text": "a", "expression": "smile"},
            {"speaker": "林青衣", "text": "b", "expression": "normal"},
            {"speaker": "林青衣", "text": "c", "expression": "default"},
            {"speaker": "林青衣", "text": "d",
             "portrait": {"character": "qingyi", "expression": "sad"}},
        ]},
    ]})
    assert _expr_warnings(validate_pack(tmp_path)) == []


def test_expression_not_checked_without_portrait_set(tmp_path: Path) -> None:
    """A character with no declared portrait_set resolves portraits by naming
    convention, so its expressions can't be validated — never warn."""
    _make_minimal_pack(tmp_path)  # hero_a has no portrait_set
    _write(tmp_path / "content" / "scenes" / "expr.yaml", {"scenes": [
        {"id": "scene_expr", "title": "E", "lines": [
            {"speaker": "Hero A", "text": "...", "expression": "whatever"},
            {"speaker": "Hero A", "text": "...",
             "portrait": {"character": "hero_a", "expression": "anything"}},
        ]},
    ]})
    assert _expr_warnings(validate_pack(tmp_path)) == []


# ---------------------------------------------------------------------------
# Guard 1: speaker ↔ portrait-character mismatch ("wrong face on screen").
# ---------------------------------------------------------------------------

def _mismatch_warnings(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    return [i for i in issues
            if i.severity == "warning" and "立繪卻是另一個角色" in i.message]


def _pack_with_npc_and_heroine(root: Path) -> None:
    """Pack with one heroine (yuening, is_heroine) and one ordinary declared
    character (senpai), so both mismatch sub-cases can be exercised."""
    _make_minimal_pack(root)
    _write(root / "content" / "characters.yaml", {"characters": [
        {"id": "hero_a", "name": "Hero A"},
        {"id": "yuening", "name": "沈月凝", "is_heroine": True,
         "portrait": "assets/characters/yuening/normal.png",
         "portrait_set": {"normal": "n.png", "scared": "s.png"}},
        {"id": "senpai", "name": "研究生學長"},
    ]})


def test_speaker_portrait_mismatch_incidental_npc_with_heroine_portrait(
        tmp_path: Path) -> None:
    """The flagship bug: an incidental/other NPC's line carries a heroine's lone
    portrait (sub-case b) -> warning naming both sides."""
    _pack_with_npc_and_heroine(tmp_path)
    _write(tmp_path / "content" / "scenes" / "mm.yaml", {"scenes": [
        {"id": "scene_mm", "title": "M", "lines": [
            {"speaker": "研究生學長", "text": "...",
             "portrait": {"character": "yuening", "expression": "normal"}},
        ]},
    ]})
    warns = _mismatch_warnings(validate_pack(tmp_path))
    assert len(warns) == 1, f"expected one mismatch warning, got: {warns}"
    assert "研究生學長" in warns[0].message and "yuening" in warns[0].message
    assert warns[0].hint  # carries a fix hint


def test_speaker_portrait_mismatch_two_declared_characters(
        tmp_path: Path) -> None:
    """Both speaker and portrait are declared characters with their own faces;
    a lone non-speaker portrait (sub-case a) is almost always a wrong-id paste."""
    _pack_with_npc_and_heroine(tmp_path)
    _write(tmp_path / "content" / "scenes" / "mm.yaml", {"scenes": [
        {"id": "scene_mm", "title": "M", "lines": [
            # senpai speaks, but the single portrait is Hero A.
            {"speaker": "研究生學長", "text": "...",
             "portraits": [{"character": "hero_a"}]},
        ]},
    ]})
    warns = _mismatch_warnings(validate_pack(tmp_path))
    assert len(warns) == 1, f"expected one mismatch warning, got: {warns}"
    assert "hero_a" in warns[0].message


def test_speaker_matches_portrait_no_warning(tmp_path: Path) -> None:
    """Speaker resolves to the same character as the portrait -> no warning."""
    _pack_with_npc_and_heroine(tmp_path)
    _write(tmp_path / "content" / "scenes" / "ok.yaml", {"scenes": [
        {"id": "scene_ok", "title": "OK", "lines": [
            {"speaker": "沈月凝", "text": "...",
             "portrait": {"character": "yuening", "expression": "normal"}},
        ]},
    ]})
    assert _mismatch_warnings(validate_pack(tmp_path)) == []


def test_narration_line_never_flagged(tmp_path: Path) -> None:
    """A narration line (no speaker) showing a heroine's portrait is fine."""
    _pack_with_npc_and_heroine(tmp_path)
    _write(tmp_path / "content" / "scenes" / "narr.yaml", {"scenes": [
        {"id": "scene_narr", "title": "N", "lines": [
            {"text": "the camera lingers on her face",
             "portrait": {"character": "yuening"}},
        ]},
    ]})
    assert _mismatch_warnings(validate_pack(tmp_path)) == []


def test_player_token_speaker_never_flagged(tmp_path: Path) -> None:
    """A ``{player_name}`` speaker is the faceless protagonist; keeping the
    listener's (heroine's) portrait on screen is the VN norm -> never flagged."""
    _pack_with_npc_and_heroine(tmp_path)
    _write(tmp_path / "content" / "scenes" / "pc.yaml", {"scenes": [
        {"id": "scene_pc", "title": "PC", "lines": [
            {"speaker": "{player_name}", "text": "「我幫你。」",
             "portrait": {"character": "yuening", "expression": "normal"}},
        ]},
    ]})
    assert _mismatch_warnings(validate_pack(tmp_path)) == []


def test_multi_portrait_reaction_shot_not_flagged(tmp_path: Path) -> None:
    """A multi-character composition (>1 portrait) is deliberate staging where
    reaction shots are expected -> never flagged, even if the speaker isn't the
    only face shown."""
    _pack_with_npc_and_heroine(tmp_path)
    _write(tmp_path / "content" / "scenes" / "multi.yaml", {"scenes": [
        {"id": "scene_multi", "title": "Multi", "lines": [
            {"speaker": "研究生學長", "text": "...", "portraits": [
                {"character": "senpai", "slot": "left"},
                {"character": "yuening", "slot": "right"},
            ]},
        ]},
    ]})
    assert _mismatch_warnings(validate_pack(tmp_path)) == []


def test_reaction_shot_of_non_heroine_not_flagged(tmp_path: Path) -> None:
    """A single non-heroine portrait while a *non-declared* speaker talks hits
    neither high-confidence signal (speaker isn't a character, portrait isn't a
    heroine) -> treated as a legitimate reaction shot, not flagged."""
    _make_minimal_pack(tmp_path)  # hero_a is the only declared char (not heroine)
    _write(tmp_path / "content" / "scenes" / "rs.yaml", {"scenes": [
        {"id": "scene_rs", "title": "RS", "lines": [
            # An off-screen voice (not a declared character) over hero_a's face.
            {"speaker": "廣播", "text": "...",
             "portrait": {"character": "hero_a"}},
        ]},
    ]})
    assert _mismatch_warnings(validate_pack(tmp_path)) == []


def test_undeclared_portrait_character_not_a_mismatch(tmp_path: Path) -> None:
    """A portrait whose character isn't declared is an asset/typo problem (its
    own check), not a speaker-mismatch -> no mismatch warning from this guard."""
    _pack_with_npc_and_heroine(tmp_path)
    _write(tmp_path / "content" / "scenes" / "ud.yaml", {"scenes": [
        {"id": "scene_ud", "title": "UD", "lines": [
            {"speaker": "研究生學長", "text": "...",
             "portrait": {"character": "nobody_xyz"}},
        ]},
    ]})
    assert _mismatch_warnings(validate_pack(tmp_path)) == []
