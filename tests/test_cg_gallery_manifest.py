"""Static CG-gallery catalog, compatibility, and validation contracts."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from world_gal_game.content_loader import load_cg_gallery_manifest
from world_gal_game.core.cg_gallery_spec import (
    CGGalleryEntrySpec,
    CGGalleryManifest,
)
from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import Effect


def test_entry_defaults_and_manifest_order_are_strict() -> None:
    manifest = CGGalleryManifest.from_items([
        {
            "asset": "assets/cgs/b.png",
            "title": "B",
            "order": 20,
        },
        {
            "asset": "assets/cgs/c.png",
            "title": "C",
            "order": 10,
        },
        {
            "asset": "assets/cgs/a.png",
            "title": "A",
            "order": 20,
        },
    ])

    assert manifest.authored is True
    assert [entry.asset for entry in manifest.ordered()] == [
        "assets/cgs/c.png",
        "assets/cgs/b.png",
        "assets/cgs/a.png",
    ]
    assert manifest.entries[0].asset == "assets/cgs/b.png"
    assert manifest.entries[0].title == "B"
    assert manifest.entries[0].subtitle == ""
    assert manifest.entries[0].description == ""
    assert manifest.entries[0].hidden is False
    assert manifest.entries[0].thumbnail is None

    with pytest.raises(ValidationError):
        CGGalleryEntrySpec(
            asset="assets/cgs/a.png",
            title="A",
            unknown=True,
        )
    with pytest.raises(ValidationError):
        CGGalleryEntrySpec(asset=" ", title="A")
    with pytest.raises(ValidationError):
        CGGalleryEntrySpec(
            asset="assets/cgs/a.png",
            title="A",
            character_ids=["heroine", "heroine"],
        )


def test_manifest_rejects_duplicate_assets() -> None:
    with pytest.raises(ValidationError, match="duplicate CG gallery asset"):
        CGGalleryManifest.from_items([
            {"asset": "assets/cgs/a.png", "title": "First"},
            {"asset": "assets/cgs/a.png", "title": "Second"},
        ])
    with pytest.raises(ValidationError, match="duplicate CG gallery asset"):
        CGGalleryManifest.from_items([
            {"asset": "assets/cgs/a.png", "title": "First"},
            {"asset": "assets/cgs/./a.png", "title": "Alias"},
        ])


def test_missing_and_explicit_empty_manifest_are_distinguishable(
    tmp_path: Path,
) -> None:
    missing = CGGalleryManifest.load(tmp_path / "missing.yaml")
    assert missing.entries == []
    assert missing.authored is False
    assert CGGalleryManifest().authored is True

    path = tmp_path / "cg_gallery.yaml"
    path.write_text("entries: []\n", encoding="utf-8")
    authored = CGGalleryManifest.load(path)
    assert authored.entries == []
    assert authored.authored is True

    path.write_text("", encoding="utf-8")
    authored_blank = CGGalleryManifest.load(path)
    assert authored_blank.entries == []
    assert authored_blank.authored is True


def test_manifest_load_accepts_compact_asset_mapping(tmp_path: Path) -> None:
    path = tmp_path / "cg_gallery.yaml"
    path.write_text(
        "assets/cgs/a.png:\n"
        "  title: First memory\n"
        "  order: 10\n",
        encoding="utf-8",
    )

    manifest = CGGalleryManifest.load(path)

    assert manifest.authored is True
    assert [
        (entry.asset, entry.title, entry.order)
        for entry in manifest.entries
    ] == [("assets/cgs/a.png", "First memory", 10)]


def test_loader_parks_catalog_on_private_nonpersistent_bridge(
    tmp_path: Path,
) -> None:
    content = tmp_path / "content"
    content.mkdir()
    (content / "cg_gallery.yaml").write_text(
        "entries:\n"
        "  - asset: assets/cgs/memory.png\n"
        "    title: A Memory\n",
        encoding="utf-8",
    )
    state = GameState()

    load_cg_gallery_manifest(content, state)

    manifest = state.meta["__cg_gallery_manifest__"]
    assert isinstance(manifest, CGGalleryManifest)
    assert manifest.entries[0].title == "A Memory"
    assert "__cg_gallery_manifest__" not in state.model_dump(
        mode="json"
    )["meta"]


def test_show_cg_effect_unlocks_the_same_asset_path() -> None:
    state = GameState()
    effect = Effect(
        kind="show_cg",
        target="assets/cgs/effect_only.png",
        value={"style": "dissolve"},
    )

    first = state.apply(effect)
    second = state.apply(effect)

    assert first["path"] == "assets/cgs/effect_only.png"
    assert second["path"] == "assets/cgs/effect_only.png"
    assert state.cg_gallery.unlocked == {
        "assets/cgs/effect_only.png",
    }

    aliased = state.apply(Effect(
        kind="show_cg",
        target=" assets/cgs/./alias.png ",
    ))
    assert aliased["path"] == "assets/cgs/alias.png"
    assert state.cg_gallery.is_unlocked("assets/cgs/alias.png")


def test_show_cg_rejects_blank_target_without_persistent_or_visual_state() -> None:
    state = GameState()

    result = state.apply(Effect(kind="show_cg", target="   "))

    assert "error" in result
    assert state.cg_gallery.unlocked == set()
    assert state.meta.get("__visual_fx__", []) == []


def test_capability_schema_exposes_catalog_models() -> None:
    from world_gal_game.dev.capability_manifest import (
        build_manifest,
        schema_document,
    )

    content = build_manifest()["content_schema"]
    schemas = schema_document()["models"]

    assert "CGGalleryEntrySpec" in content
    assert "CGGalleryManifest" in content
    assert "CGGalleryEntrySpec" in schemas
    assert schemas["CGGalleryEntrySpec"]["additionalProperties"] is False
    assert set(schemas["CGGalleryEntrySpec"]["required"]) == {
        "asset",
        "title",
    }


def _write_minimal_catalog_pack(root: Path) -> None:
    content = root / "content"
    scenes = content / "scenes"
    scenes.mkdir(parents=True)
    (content / "meta.yaml").write_text(
        'pack_format_version: "0.1"\n',
        encoding="utf-8",
    )
    (content / "characters.yaml").write_text(
        "characters:\n"
        "  - id: heroine\n"
        "    name: Heroine\n",
        encoding="utf-8",
    )
    (content / "chapters.yaml").write_text(
        "chapters:\n"
        "  - id: chapter_one\n"
        "    scenes: [start]\n",
        encoding="utf-8",
    )
    (scenes / "start.yaml").write_text(
        "scenes:\n"
        "  - id: start\n"
        "    cg: assets/cgs/scene_missing.png\n"
        "    lines:\n"
        "      - text: hello\n"
        "        effects:\n"
        "          - kind: show_cg\n"
        "            target: assets/cgs/effect_only.png\n",
        encoding="utf-8",
    )


def test_validator_checks_catalog_assets_context_and_story_coverage(
    tmp_path: Path,
) -> None:
    from world_gal_game.validator import validate_pack

    _write_minimal_catalog_pack(tmp_path)
    (tmp_path / "content" / "cg_gallery.yaml").write_text(
        "entries:\n"
        "  - asset: ../outside.png\n"
        "    title: Unsafe\n"
        "    character_ids: [missing_character]\n"
        "    chapter_id: missing_chapter\n",
        encoding="utf-8",
    )

    issues = validate_pack(tmp_path)

    assert any(
        issue.severity == "error"
        and issue.path == "entries[0].asset"
        and "安全" in issue.message
        for issue in issues
    )
    assert any(
        issue.severity == "error"
        and issue.path == "entries[0].character_ids"
        for issue in issues
    )
    assert any(
        issue.severity == "error"
        and issue.path == "entries[0].chapter_id"
        for issue in issues
    )
    assert any(
        issue.severity == "warning"
        and issue.path == "scenes[0].cg"
        and "scene_missing.png" in issue.message
        for issue in issues
    )
    assert any(
        issue.severity == "warning"
        and issue.path == "scenes[0].lines[0].effects[0].target"
        and "effect_only.png" in issue.message
        for issue in issues
    )
    unlisted = [
        issue
        for issue in issues
        if issue.file.endswith("cg_gallery.yaml")
        and "未在 cg_gallery.yaml 宣告" in issue.message
    ]
    assert {
        "assets/cgs/scene_missing.png",
        "assets/cgs/effect_only.png",
    } == {
        next(
            asset
            for asset in (
                "assets/cgs/scene_missing.png",
                "assets/cgs/effect_only.png",
            )
            if asset in issue.message
        )
        for issue in unlisted
    }


def test_validator_accepts_compact_asset_mapping_catalog(
    tmp_path: Path,
) -> None:
    from world_gal_game.validator import validate_pack

    _write_minimal_catalog_pack(tmp_path)
    (tmp_path / "content" / "cg_gallery.yaml").write_text(
        "assets/cgs/scene_missing.png:\n"
        "  title: Scene memory\n"
        "  character_ids: [heroine]\n"
        "  chapter_id: chapter_one\n",
        encoding="utf-8",
    )

    issues = validate_pack(tmp_path)

    assert not any(
        "manifest 無效" in issue.message
        or "Extra inputs are not permitted" in issue.message
        for issue in issues
    )
    assert not any(
        "scene_missing.png" in issue.message
        and "未在 cg_gallery.yaml 宣告" in issue.message
        for issue in issues
    )


def test_validator_reports_source_path_for_top_level_list_catalog(
    tmp_path: Path,
) -> None:
    from world_gal_game.validator import validate_pack

    _write_minimal_catalog_pack(tmp_path)
    (tmp_path / "content" / "cg_gallery.yaml").write_text(
        "- asset: ../outside.png\n"
        "  title: Unsafe\n",
        encoding="utf-8",
    )

    issues = validate_pack(tmp_path)

    assert any(
        issue.severity == "error"
        and issue.path == "[0].asset"
        for issue in issues
    )
    assert not any(
        issue.path == "entries[0].asset"
        for issue in issues
    )


def test_validator_checks_and_catalogs_item_show_cg_effects(
    tmp_path: Path,
) -> None:
    from world_gal_game.validator import validate_pack

    _write_minimal_catalog_pack(tmp_path)
    (tmp_path / "content" / "items.yaml").write_text(
        "items:\n"
        "  - id: memory_token\n"
        "    name: Memory token\n"
        "    consumable: true\n"
        "    use_effects:\n"
        "      - kind: show_cg\n"
        "        target: assets/cgs/item_reveal.png\n",
        encoding="utf-8",
    )
    (tmp_path / "content" / "cg_gallery.yaml").write_text(
        "entries: []\n",
        encoding="utf-8",
    )

    issues = validate_pack(tmp_path)

    assert any(
        issue.file.endswith("items.yaml")
        and issue.path == "items[0].use_effects[0].target"
        and "item_reveal.png" in issue.message
        for issue in issues
    )
    assert any(
        issue.file.endswith("cg_gallery.yaml")
        and "item_reveal.png" in issue.message
        and "未在 cg_gallery.yaml 宣告" in issue.message
        for issue in issues
    )


def test_validator_rejects_blank_show_cg_target(tmp_path: Path) -> None:
    from world_gal_game.validator import validate_pack

    _write_minimal_catalog_pack(tmp_path)
    scene_path = tmp_path / "content" / "scenes" / "start.yaml"
    scene_path.write_text(
        "scenes:\n"
        "  - id: start\n"
        "    lines:\n"
        "      - text: hello\n"
        "        effects:\n"
        "          - kind: show_cg\n"
        "            target: '   '\n",
        encoding="utf-8",
    )

    issues = validate_pack(tmp_path)

    assert any(
        issue.severity == "error"
        and issue.path == "scenes[0].lines[0].effects[0].target"
        and "非空白" in issue.message
        for issue in issues
    )


def test_validator_normalizes_show_cg_target_like_runtime(
    tmp_path: Path,
) -> None:
    import pygame

    from world_gal_game.validator import validate_pack

    _write_minimal_catalog_pack(tmp_path)
    cg_path = tmp_path / "assets" / "cgs" / "memory.png"
    cg_path.parent.mkdir(parents=True)
    pygame.image.save(pygame.Surface((2, 2)), cg_path)
    (tmp_path / "content" / "cg_gallery.yaml").write_text(
        "entries:\n"
        "  - asset: assets/cgs/memory.png\n"
        "    title: Memory\n",
        encoding="utf-8",
    )
    scene_path = tmp_path / "content" / "scenes" / "start.yaml"
    scene_path.write_text(
        "scenes:\n"
        "  - id: start\n"
        "    lines:\n"
        "      - text: hello\n"
        "        effects:\n"
        "          - kind: show_cg\n"
        "            target: ' assets/cgs/./memory.png '\n",
        encoding="utf-8",
    )

    issues = validate_pack(tmp_path)

    assert not any(
        issue.path == "scenes[0].lines[0].effects[0].target"
        for issue in issues
    )
    assert not any(
        "memory.png" in issue.message
        and "未在 cg_gallery.yaml 宣告" in issue.message
        for issue in issues
    )


def test_validator_treats_blank_catalog_as_authored_empty(
    tmp_path: Path,
) -> None:
    from world_gal_game.validator import validate_pack

    _write_minimal_catalog_pack(tmp_path)
    (tmp_path / "content" / "cg_gallery.yaml").write_text(
        "",
        encoding="utf-8",
    )

    issues = validate_pack(tmp_path)
    unlisted_messages = {
        issue.message
        for issue in issues
        if "未在 cg_gallery.yaml 宣告" in issue.message
    }

    assert any(
        "scene_missing.png" in message
        for message in unlisted_messages
    )
    assert any(
        "effect_only.png" in message
        for message in unlisted_messages
    )


def test_validator_catalogs_show_cg_from_top_level_item_list(
    tmp_path: Path,
) -> None:
    from world_gal_game.validator import validate_pack

    _write_minimal_catalog_pack(tmp_path)
    (tmp_path / "content" / "items.yaml").write_text(
        "- id: memory_token\n"
        "  name: Memory token\n"
        "  consumable: true\n"
        "  use_effects:\n"
        "    - kind: show_cg\n"
        "      target: assets/cgs/list_item_reveal.png\n",
        encoding="utf-8",
    )
    (tmp_path / "content" / "cg_gallery.yaml").write_text(
        "entries: []\n",
        encoding="utf-8",
    )

    issues = validate_pack(tmp_path)

    assert any(
        "list_item_reveal.png" in issue.message
        and "未在 cg_gallery.yaml 宣告" in issue.message
        for issue in issues
    )


def test_validator_treats_scene_named_cg_gallery_as_scene(
    tmp_path: Path,
) -> None:
    from world_gal_game.validator import validate_pack

    _write_minimal_catalog_pack(tmp_path)
    (tmp_path / "content" / "cg_gallery.yaml").write_text(
        "entries: []\n",
        encoding="utf-8",
    )
    (tmp_path / "content" / "scenes" / "cg_gallery.yaml").write_text(
        "scenes:\n"
        "  - id: gallery_story_scene\n"
        "    lines:\n"
        "      - text: This is executable story content.\n",
        encoding="utf-8",
    )

    issues = validate_pack(tmp_path)

    assert not any(
        issue.file.endswith("scenes/cg_gallery.yaml")
        and (
            "CG gallery" in issue.message
            or "mapping entry" in issue.message
        )
        for issue in issues
    )


def test_catalog_coverage_ignores_non_executable_yaml_records(
    tmp_path: Path,
) -> None:
    from world_gal_game.validator import validate_pack

    _write_minimal_catalog_pack(tmp_path)
    (tmp_path / "content" / "cg_gallery.yaml").write_text(
        "entries: []\n",
        encoding="utf-8",
    )
    (tmp_path / "content" / "design_notes.yaml").write_text(
        "- id: decorative_record\n"
        "  cg: assets/cgs/not_executable.png\n",
        encoding="utf-8",
    )

    issues = validate_pack(tmp_path)

    assert not any(
        "not_executable.png" in issue.message
        and "未在 cg_gallery.yaml 宣告" in issue.message
        for issue in issues
    )


def test_catalog_cross_refs_do_not_crash_on_non_string_authored_ids(
    tmp_path: Path,
) -> None:
    from world_gal_game.validator import validate_pack

    _write_minimal_catalog_pack(tmp_path)
    (tmp_path / "content" / "characters.yaml").write_text(
        "characters:\n"
        "  - id: 7\n"
        "    name: Invalid numeric id\n",
        encoding="utf-8",
    )
    (tmp_path / "content" / "chapters.yaml").write_text(
        "chapters:\n"
        "  - id: 9\n"
        "    scenes: [start]\n",
        encoding="utf-8",
    )
    (tmp_path / "content" / "cg_gallery.yaml").write_text(
        "entries:\n"
        "  - asset: assets/cgs/memory.png\n"
        "    title: Memory\n"
        "    character_ids: [hero]\n"
        "    chapter_id: chapter_one\n",
        encoding="utf-8",
    )

    issues = validate_pack(tmp_path)

    assert isinstance(issues, list)
    assert any(
        issue.path == "entries[0].character_ids"
        for issue in issues
    )
    assert any(
        issue.path == "entries[0].chapter_id"
        for issue in issues
    )


def test_validator_rejects_noncanonical_cg_gallery_filename_case(
    tmp_path: Path,
) -> None:
    from world_gal_game.validator import validate_pack

    _write_minimal_catalog_pack(tmp_path)
    alias = tmp_path / "content" / "CG_GALLERY.YAML"
    alias.write_text("entries: []\n", encoding="utf-8")

    issues = validate_pack(tmp_path)

    assert any(
        issue.severity == "error"
        and issue.file.endswith("CG_GALLERY.YAML")
        and issue.path == "filename"
        and "cg_gallery.yaml" in issue.message
        for issue in issues
    )


def test_validator_preserves_list_index_in_catalog_schema_errors(
    tmp_path: Path,
) -> None:
    from world_gal_game.validator import validate_pack

    _write_minimal_catalog_pack(tmp_path)
    (tmp_path / "content" / "cg_gallery.yaml").write_text(
        "- asset: assets/cgs/a.png\n"
        "  title: A\n"
        "- asset: assets/cgs/b.png\n"
        "  titel: Typo\n",
        encoding="utf-8",
    )

    issues = validate_pack(tmp_path)

    assert any(issue.path == "[1].title" for issue in issues)
    assert any(issue.path == "[1].titel" for issue in issues)


def test_validator_preserves_compact_mapping_key_in_schema_errors(
    tmp_path: Path,
) -> None:
    from world_gal_game.validator import validate_pack

    _write_minimal_catalog_pack(tmp_path)
    (tmp_path / "content" / "cg_gallery.yaml").write_text(
        "assets/cgs/missing_title.png:\n"
        "  order: 10\n",
        encoding="utf-8",
    )

    issues = validate_pack(tmp_path)

    assert any(
        issue.path == "['assets/cgs/missing_title.png'].title"
        for issue in issues
    )


def test_validator_does_not_treat_uppercase_extension_as_runtime_scene(
    tmp_path: Path,
) -> None:
    from world_gal_game.validator import validate_pack

    _write_minimal_catalog_pack(tmp_path)
    (tmp_path / "content" / "cg_gallery.yaml").write_text(
        "entries: []\n",
        encoding="utf-8",
    )
    (tmp_path / "content" / "scenes" / "IGNORED.YAML").write_text(
        "scenes:\n"
        "  - id: ignored_on_linux\n"
        "    cg: assets/cgs/not_runtime_content.png\n"
        "    lines: [{text: ignored}]\n",
        encoding="utf-8",
    )

    issues = validate_pack(tmp_path)

    assert not any(
        "not_runtime_content.png" in issue.message
        for issue in issues
    )
