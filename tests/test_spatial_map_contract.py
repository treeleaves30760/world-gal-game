"""Strict, discoverable authoring contract for ``travel.spatial_map``."""
from __future__ import annotations

from pathlib import Path

import pygame
import pytest
import yaml
from pydantic import ValidationError

from world_gal_game.core.spatial_map import (
    SpatialMapSpec,
    parse_spatial_map_spec,
    spatial_map_from_meta,
)
from world_gal_game.dev.capability_manifest import (
    build_manifest,
    schema_document,
)
from world_gal_game.validator import validate_pack


def _write_yaml(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _save_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pygame.image.save(pygame.Surface((2, 2)), path)


def _write_pack(
    root: Path,
    *,
    spatial_map: object,
    locations: list[dict] | None = None,
) -> None:
    _write_yaml(root / "content" / "meta.yaml", {
        "pack_format_version": "0.4",
        "travel": {"spatial_map": spatial_map},
    })
    _write_yaml(root / "content" / "locations.yaml", {
        "locations": locations or [
            {
                "id": "campus",
                "name": "Campus",
                "map_x": 50,
                "map_y": 50,
            },
            {
                "id": "library",
                "name": "Library",
                "map_x": 60,
                "map_y": 30,
            },
            {
                "id": "library_2f",
                "name": "Library 2F",
            },
            {
                "id": "story_garden",
                "name": "Story Garden",
                "map_x": 70,
                "map_y": 40,
            },
            {
                "id": "market",
                "name": "Market",
            },
        ],
    })


def _spatial_issues(root: Path):
    return [
        issue
        for issue in validate_pack(root)
        if (
            issue.path.startswith("travel.spatial_map")
            or "中央地圖節點" in issue.message
            or "map_x" in issue.message
            or "map_y" in issue.message
        )
    ]


def test_strict_parser_normalises_authored_aliases_and_preserves_nodes() -> None:
    meta = {
        "travel": {
            "spatial_map": {
                "background": " assets/maps/campus.png ",
                "attribution": " Original campus art ",
                "bounds": [0, 0, 100, 100],
                "background_fit": "contain",
                "edge_destinations": {
                    "market": {
                        "direction": "South-West",
                        "thumbnail": " assets/maps/market.png ",
                        "caption": " Market district ",
                        "attribution": " Photo: Example ",
                    },
                },
                "nodes": {
                    "campus": {"label": " Main Campus "},
                    "library_2f": {
                        "layer": "interior",
                        "parent": " library ",
                    },
                    "story_garden": {"layer": "story_only"},
                },
            },
        },
    }

    spec = parse_spatial_map_spec(meta)
    assert spec is not None
    assert spec.bounds == (0.0, 0.0, 100.0, 100.0)
    assert spec.edge_destinations["market"].direction == "sw"
    assert spec.edge_destinations["market"].thumbnail == (
        "assets/maps/market.png"
    )
    assert spec.nodes["campus"].layer == "campus"
    assert spec.nodes["campus"].label == "Main Campus"
    assert spec.nodes["library_2f"].parent == "library"
    assert spec.nodes["story_garden"].layer == "story_only"

    presentation = spatial_map_from_meta(meta)
    assert presentation is not None
    assert presentation.background == "assets/maps/campus.png"
    assert presentation.edge_destinations["market"].location_id == "market"
    assert presentation.nodes["library_2f"].layer == "interior"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {"bounds": [0, 0, 0, 100]},
        {"bounds": [0, 0, "100", 100]},
        {"background_fit": "tile"},
        {"edge_destinations": {"market": {"direction": "sideways"}}},
        {"nodes": {"room": {"layer": "underground"}}},
        {"unknown": True},
    ],
)
def test_explicit_malformed_spatial_block_is_strictly_rejected(
    raw: object,
) -> None:
    with pytest.raises(ValidationError):
        parse_spatial_map_spec({"travel": {"spatial_map": raw}})


def test_runtime_adapter_keeps_legacy_nonfatal_fallback() -> None:
    assert parse_spatial_map_spec({}) is None
    assert spatial_map_from_meta({}) is None

    presentation = spatial_map_from_meta({
        "travel": {
            "spatial_map": {
                "bounds": [10, 10, 0, 0],
                "background_fit": "tile",
                "edge_destinations": {
                    "market": {"direction": "west"},
                    "bad": {"direction": "sideways"},
                },
                "unknown": "ignored only at runtime",
            },
        },
    })
    assert presentation is not None
    assert presentation.bounds == (0.0, 0.0, 100.0, 100.0)
    assert presentation.background_fit == "cover"
    assert set(presentation.edge_destinations) == {"market"}


def test_validator_reports_strict_schema_paths(tmp_path: Path) -> None:
    _write_pack(tmp_path, spatial_map={
        "bounds": [0, 0, 0, 100],
        "mystery": True,
    })

    issues = _spatial_issues(tmp_path)
    assert any(
        issue.severity == "error"
        and issue.path == "travel.spatial_map.bounds"
        for issue in issues
    )
    mystery = next(
        issue
        for issue in issues
        if issue.path == "travel.spatial_map.mystery"
    )
    assert mystery.severity == "error"
    assert "未知欄位" in mystery.message


def test_validator_checks_auto_open_scene_references(
    tmp_path: Path,
) -> None:
    _write_pack(tmp_path, spatial_map={
        "auto_open_after_scenes": ["missing_tutorial"],
    })

    issue = next(
        issue
        for issue in _spatial_issues(tmp_path)
        if issue.path == (
            "travel.spatial_map.auto_open_after_scenes[0]"
        )
    )
    assert issue.severity == "error"
    assert "missing_tutorial" in issue.message


def test_validator_accepts_valid_assets_hierarchy_and_coordinates(
    tmp_path: Path,
) -> None:
    _save_image(tmp_path / "assets" / "maps" / "campus.png")
    _save_image(tmp_path / "assets" / "maps" / "market.png")
    _write_pack(tmp_path, spatial_map={
        "background": "assets/maps/campus.png",
        "attribution": "Original campus art and market photo",
        "bounds": [0, 0, 100, 100],
        "edge_destinations": {
            "market": {
                "direction": "west",
                "thumbnail": "assets/maps/market.png",
            },
        },
        "nodes": {
            "campus": {"label": "Main Campus"},
            "library": {"layer": "campus"},
            "library_2f": {
                "layer": "interior",
                "parent": "library",
            },
            "story_garden": {"layer": "story_only"},
        },
    })

    assert _spatial_issues(tmp_path) == []


def test_validator_checks_safe_existing_and_decodable_map_assets(
    tmp_path: Path,
) -> None:
    broken = tmp_path / "assets" / "maps" / "broken.png"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"not an image")
    _write_pack(tmp_path, spatial_map={
        "background": "https://example.invalid/campus.png",
        "attribution": "Example attribution",
        "edge_destinations": {
            "market": {
                "direction": "west",
                "thumbnail": "assets/maps/broken.png",
            },
        },
    })

    issues = _spatial_issues(tmp_path)
    background = next(
        issue
        for issue in issues
        if issue.path == "travel.spatial_map.background"
    )
    thumbnail = next(
        issue
        for issue in issues
        if issue.path.endswith("market.thumbnail")
    )
    assert background.severity == "error"
    assert "安全" in background.message
    assert thumbnail.severity == "warning"
    assert "無法解碼" in thumbnail.message


def test_validator_checks_location_refs_parent_cycles_and_exclusive_kind(
    tmp_path: Path,
) -> None:
    _write_pack(tmp_path, spatial_map={
        "edge_destinations": {
            "missing_edge": {"direction": "north"},
            "campus": {"direction": "south"},
        },
        "nodes": {
            "campus": {"layer": "campus"},
            "missing_node": {"layer": "campus"},
            "library": {
                "layer": "interior",
                "parent": "library_2f",
            },
            "library_2f": {
                "layer": "interior",
                "parent": "library",
            },
            "story_garden": {
                "layer": "story_only",
                "parent": "missing_parent",
            },
        },
    })

    issues = _spatial_issues(tmp_path)
    assert any(
        issue.path.endswith("edge_destinations.missing_edge")
        and "不是已知" in issue.message
        for issue in issues
    )
    assert any(
        issue.path.endswith("nodes.missing_node")
        and "不是已知" in issue.message
        for issue in issues
    )
    assert any(
        issue.path.endswith("story_garden.parent")
        and "missing_parent" in issue.message
        for issue in issues
    )
    assert any("形成循環" in issue.message for issue in issues)
    assert any(
        issue.path.endswith("nodes.campus")
        and "同時" in issue.message
        for issue in issues
    )


def test_validator_checks_attribution_and_authored_bounds(
    tmp_path: Path,
) -> None:
    _save_image(tmp_path / "assets" / "maps" / "market.png")
    _write_pack(
        tmp_path,
        spatial_map={
            "bounds": [0, 0, 100, 100],
            "edge_destinations": {
                "market": {
                    "direction": "west",
                    "thumbnail": "assets/maps/market.png",
                },
            },
        },
        locations=[
            {
                "id": "campus",
                "name": "Campus",
                "map_x": 101,
                "map_y": 50,
            },
            {"id": "library", "name": "Library"},
            {"id": "market", "name": "Market"},
        ],
    )

    issues = _spatial_issues(tmp_path)
    attribution = next(
        issue
        for issue in issues
        if issue.path.endswith("market.attribution")
    )
    outside = next(
        issue
        for issue in issues
        if "超出 spatial_map.bounds" in issue.message
    )
    missing_coordinates = [
        issue
        for issue in issues
        if "沒有明確指定 map_" in issue.message
    ]
    assert attribution.severity == "error"
    assert outside.severity == "warning"
    assert len(missing_coordinates) == 2


def test_capability_bundle_exposes_spatial_map_json_schema() -> None:
    manifest = build_manifest()
    schema = manifest["metadata_schema"]["travel.spatial_map"]

    assert schema["additionalProperties"] is False
    assert schema["properties"]["background_fit"]["default"] == "cover"
    assert (
        schema["properties"]["auto_open_after_scenes"]["type"]
        == "array"
    )
    assert schema["properties"]["bounds"]["default"] == [
        0.0,
        0.0,
        100.0,
        100.0,
    ]
    assert "SpatialMapSpec" in manifest["content_schema"]
    assert "SpatialMapNodeSpec" in manifest["content_schema"]
    assert "EdgeDestinationSpec" in manifest["content_schema"]
    assert (
        schema_document()["models"]["SpatialMapSpec"]["properties"]
        == schema["properties"]
    )
    assert (
        SpatialMapSpec.model_json_schema()["additionalProperties"]
        is False
    )
