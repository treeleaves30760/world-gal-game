"""Pack-driven title presentation has a strict, discoverable contract."""
from __future__ import annotations

from pathlib import Path

import pygame
import pytest
import yaml
from pydantic import ValidationError

from world_gal_game.core.title_screen_spec import (
    TitleScreenSpec,
    parse_title_screen_spec,
)
from world_gal_game.dev.capability_manifest import (
    build_manifest,
    schema_document,
)
from world_gal_game.validator import validate_pack


def _write_meta(root: Path, data: object) -> None:
    path = root / "content" / "meta.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _title_issues(root: Path):
    return [
        issue
        for issue in validate_pack(root)
        if issue.path == "title_bg"
        or issue.path == "title_bgm"
        or issue.path.startswith("title_screen")
    ]


def test_missing_title_block_uses_exact_legacy_defaults() -> None:
    spec = parse_title_screen_spec({"title": "Existing pack"})

    assert spec == TitleScreenSpec()
    assert spec.layout == "centered"
    assert spec.panel_style == "glass"
    assert spec.name_entry == "inline"
    assert spec.logo_image is None
    assert spec.wordmark_style == "classic"
    assert spec.tagline == ""
    assert spec.background_opacity == 205 / 255
    assert spec.show_engine_credit is True
    assert spec.footer_text == ""


def test_parser_preserves_all_authored_values() -> None:
    spec = parse_title_screen_spec({
        "title_screen": {
            "layout": "right_rail",
            "panel_style": "rail",
            "name_entry": "on_new_game",
            "logo_image": "assets/ui/logo.png",
            "wordmark_style": "editorial",
            "tagline": "An authored tagline",
            "background_opacity": 0.5,
            "show_engine_credit": False,
            "footer_text": "Copyright",
        },
    })

    assert spec.model_dump() == {
        "layout": "right_rail",
        "panel_style": "rail",
        "name_entry": "on_new_game",
        "logo_image": "assets/ui/logo.png",
        "wordmark_style": "editorial",
        "tagline": "An authored tagline",
        "background_opacity": 0.5,
        "show_engine_credit": False,
        "footer_text": "Copyright",
    }


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {"layout": "diagonal"},
        {"panel_style": "opaque"},
        {"name_entry": "always"},
        {"wordmark_style": "modern"},
        {"background_opacity": -0.01},
        {"background_opacity": 1.01},
        {"background_opacity": "0.5"},
        {"show_engine_credit": "true"},
        {"unknown": True},
    ],
)
def test_present_malformed_block_is_rejected(raw: object) -> None:
    with pytest.raises(ValidationError):
        parse_title_screen_spec({"title_screen": raw})


def test_validator_reports_unknown_enum_range_and_type_paths(
    tmp_path: Path,
) -> None:
    _write_meta(tmp_path, {
        "pack_format_version": "0.1",
        "title_screen": {
            "layout": "diagonal",
            "background_opacity": 1.2,
            "show_engine_credit": "yes",
            "mystery": 1,
        },
    })

    issues = _title_issues(tmp_path)
    paths = {issue.path for issue in issues if issue.severity == "error"}
    assert {
        "title_screen.layout",
        "title_screen.background_opacity",
        "title_screen.show_engine_credit",
        "title_screen.mystery",
    } <= paths
    mystery = next(i for i in issues if i.path == "title_screen.mystery")
    assert "未知欄位" in mystery.message


def test_validator_reports_non_mapping_title_block(tmp_path: Path) -> None:
    _write_meta(tmp_path, {
        "pack_format_version": "0.1",
        "title_screen": ["centered"],
    })

    issues = _title_issues(tmp_path)
    assert any(
        issue.severity == "error" and issue.path == "title_screen"
        for issue in issues
    )


def test_validator_reports_missing_logo(tmp_path: Path) -> None:
    _write_meta(tmp_path, {
        "pack_format_version": "0.1",
        "title_screen": {
            "logo_image": "assets/ui/missing-logo.png",
        },
    })

    issues = _title_issues(tmp_path)
    issue = next(i for i in issues if i.path == "title_screen.logo_image")
    assert issue.severity == "warning"
    assert "不存在" in issue.message


@pytest.mark.parametrize(
    "ref",
    [
        "/tmp/logo.png",
        "../logo.png",
        r"assets\ui\logo.png",
        "https://example.invalid/logo.png",
    ],
)
def test_validator_rejects_unsafe_logo_paths(
    tmp_path: Path,
    ref: str,
) -> None:
    _write_meta(tmp_path, {
        "pack_format_version": "0.1",
        "title_screen": {"logo_image": ref},
    })

    issue = next(
        i
        for i in _title_issues(tmp_path)
        if i.path == "title_screen.logo_image"
    )
    assert issue.severity == "error"
    assert "安全" in issue.message


def test_validator_reports_logo_that_cannot_decode(tmp_path: Path) -> None:
    logo = tmp_path / "assets" / "ui" / "broken.png"
    logo.parent.mkdir(parents=True)
    logo.write_bytes(b"this is not an image")
    _write_meta(tmp_path, {
        "pack_format_version": "0.1",
        "title_screen": {"logo_image": "assets/ui/broken.png"},
    })

    issue = next(
        i
        for i in _title_issues(tmp_path)
        if i.path == "title_screen.logo_image"
    )
    assert issue.severity == "warning"
    assert "無法解碼" in issue.message


def test_validator_accepts_decodable_pack_relative_logo(
    tmp_path: Path,
) -> None:
    logo = tmp_path / "assets" / "ui" / "logo.png"
    logo.parent.mkdir(parents=True)
    pygame.image.save(pygame.Surface((2, 2)), logo)
    _write_meta(tmp_path, {
        "pack_format_version": "0.1",
        "title_screen": {"logo_image": "assets/ui/logo.png"},
    })

    assert not any(
        issue.path == "title_screen.logo_image"
        for issue in _title_issues(tmp_path)
    )


def test_validator_checks_legacy_title_asset_types(tmp_path: Path) -> None:
    _write_meta(tmp_path, {
        "pack_format_version": "0.1",
        "title_bg": 42,
        "title_bgm": ["assets/bgm/title.ogg"],
    })

    issues = _title_issues(tmp_path)
    assert {
        (issue.path, issue.severity)
        for issue in issues
    } >= {
        ("title_bg", "error"),
        ("title_bgm", "error"),
    }


def test_validator_reports_missing_legacy_title_assets(tmp_path: Path) -> None:
    _write_meta(tmp_path, {
        "pack_format_version": "0.1",
        "title_bg": "assets/backgrounds/missing.png",
        "title_bgm": "assets/bgm/missing.ogg",
    })

    issues = _title_issues(tmp_path)
    assert {
        (issue.path, issue.severity)
        for issue in issues
    } >= {
        ("title_bg", "warning"),
        ("title_bgm", "warning"),
    }
    assert all("不存在" in issue.message for issue in issues)


def test_capability_bundle_exposes_title_screen_json_schema() -> None:
    manifest = build_manifest()
    schema = manifest["metadata_schema"]["title_screen"]

    assert schema["additionalProperties"] is False
    assert schema["properties"]["layout"]["default"] == "centered"
    assert schema["properties"]["background_opacity"]["minimum"] == 0.0
    assert schema["properties"]["background_opacity"]["maximum"] == 1.0
    assert "TitleScreenSpec" in manifest["content_schema"]
    assert (
        schema_document()["models"]["TitleScreenSpec"]["properties"]
        == schema["properties"]
    )
