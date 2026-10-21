"""Static Music Room catalog, compatibility, and authority contracts."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from world_gal_game.content_loader import load_music_room_manifest
from world_gal_game.core.game_state import GameState
from world_gal_game.core.media_authority import build_media_authority
from world_gal_game.core.music_room import MusicRoom
from world_gal_game.core.music_room_spec import (
    MusicRoomEntrySpec,
    MusicRoomManifest,
    music_room_localization_id,
)


def test_entry_defaults_and_manifest_order_are_strict() -> None:
    manifest = MusicRoomManifest.from_items([
        {
            "asset": "assets/bgm/b.ogg",
            "title": "B",
            "artist": "Composer",
            "usage": "Library",
            "order": 20,
        },
        {"asset": "assets/bgm/c.ogg", "title": "C", "order": 10},
        {"asset": "assets/bgm/a.ogg", "title": "A", "order": 20},
    ])

    assert manifest.authored is True
    assert [entry.asset for entry in manifest.ordered()] == [
        "assets/bgm/c.ogg",
        "assets/bgm/b.ogg",
        "assets/bgm/a.ogg",
    ]
    assert manifest.entries[0].artist == "Composer"
    assert manifest.entries[0].usage == "Library"
    assert manifest.entries[0].hidden is False

    with pytest.raises(ValidationError):
        MusicRoomEntrySpec(
            asset="assets/bgm/a.ogg",
            title="A",
            unknown=True,
        )
    with pytest.raises(ValidationError):
        MusicRoomEntrySpec(asset=" ", title="A")
    with pytest.raises(ValidationError):
        MusicRoomEntrySpec(asset="assets/bgm/a.ogg", title=" ")
    with pytest.raises(ValidationError):
        MusicRoomEntrySpec(
            asset="assets/bgm/a.ogg",
            title="A",
            loc_id="has.dot",
        )
    for unsafe in (
        "/tmp/a.ogg",
        "C:/music/a.ogg",
        "assets/bgm/../../outside.ogg",
        "assets/bgm/not-audio.png",
    ):
        with pytest.raises(ValidationError):
            MusicRoomEntrySpec(asset=unsafe, title="A")


def test_manifest_rejects_duplicate_canonical_assets() -> None:
    with pytest.raises(ValidationError, match="duplicate Music Room asset"):
        MusicRoomManifest.from_items([
            {"asset": "assets/bgm/a.ogg", "title": "First"},
            {"asset": "assets/bgm/./a.ogg", "title": "Alias"},
        ])

    fallback_entry = MusicRoomEntrySpec(
        asset="assets/bgm/fallback.ogg",
        title="Fallback",
    )
    fallback_id = music_room_localization_id(fallback_entry)
    with pytest.raises(ValidationError, match="duplicate Music Room loc_id"):
        MusicRoomManifest.from_items([
            fallback_entry.model_dump(),
            {
                "asset": "assets/bgm/explicit.ogg",
                "title": "Explicit collision",
                "loc_id": fallback_id,
            },
        ])
    with pytest.raises(ValidationError, match="duplicate Music Room loc_id"):
        MusicRoomManifest.from_items([
            {
                "asset": "assets/bgm/a.ogg",
                "title": "First",
                "loc_id": "shared",
            },
            {
                "asset": "assets/bgm/b.ogg",
                "title": "Second",
                "loc_id": "shared",
            },
        ])


def test_missing_and_explicit_empty_manifest_are_distinguishable(
    tmp_path: Path,
) -> None:
    missing = MusicRoomManifest.load(tmp_path / "missing.yaml")
    assert missing.entries == []
    assert missing.authored is False
    assert MusicRoomManifest().authored is True

    path = tmp_path / "music_room.yaml"
    path.write_text("entries: []\n", encoding="utf-8")
    assert MusicRoomManifest.load(path).authored is True

    path.write_text("", encoding="utf-8")
    assert MusicRoomManifest.load(path).authored is True


def test_manifest_load_accepts_compact_asset_mapping(tmp_path: Path) -> None:
    path = tmp_path / "music_room.yaml"
    path.write_text(
        "assets/bgm/a.ogg:\n"
        "  title: First theme\n"
        "  artist: Composer\n"
        "  order: 10\n",
        encoding="utf-8",
    )

    manifest = MusicRoomManifest.load(path)

    assert [
        (entry.asset, entry.title, entry.artist, entry.order)
        for entry in manifest.entries
    ] == [("assets/bgm/a.ogg", "First theme", "Composer", 10)]


def test_manifest_rejects_over_budget_file_before_yaml_parse(
    tmp_path: Path,
) -> None:
    path = tmp_path / "music_room.yaml"
    path.write_text("#" + "x" * (2 * 1024 * 1024), encoding="utf-8")

    with pytest.raises(ValueError, match="2 MiB"):
        MusicRoomManifest.load(path)


def test_manifest_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    path = tmp_path / "music_room.yaml"
    path.write_text(
        "entries:\n"
        "  - asset: assets/bgm/a.ogg\n"
        "    title: First\n"
        "    title: Silently overwritten\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate key"):
        MusicRoomManifest.load(path)


def test_loader_parks_catalog_on_private_nonpersistent_bridge(
    tmp_path: Path,
) -> None:
    content = tmp_path / "content"
    content.mkdir()
    (content / "music_room.yaml").write_text(
        "entries:\n"
        "  - asset: assets/bgm/memory.ogg\n"
        "    title: A Memory\n",
        encoding="utf-8",
    )
    state = GameState()

    load_music_room_manifest(content, state)

    manifest = state.meta["__music_room_manifest__"]
    assert isinstance(manifest, MusicRoomManifest)
    assert manifest.entries[0].title == "A Memory"
    assert "__music_room_manifest__" not in state.model_dump(
        mode="json"
    )["meta"]


def test_music_unlock_paths_are_canonical_and_blank_safe() -> None:
    tracker = MusicRoom.model_validate({
        "unlocked": [" assets/bgm/./a.ogg ", "", "assets\\bgm\\b.ogg"],
    })

    assert tracker.unlocked == {
        "assets/bgm/a.ogg",
        "assets/bgm/b.ogg",
    }
    assert tracker.unlock("assets/bgm/../bgm/c.ogg") is True
    assert tracker.unlock("   ") is False
    assert tracker.is_unlocked("assets/bgm/./c.ogg") is True


def test_manifest_assets_extend_current_pack_music_authority() -> None:
    state = GameState()
    manifest = MusicRoomManifest.from_items([
        {
            "asset": "assets/bgm/catalog_only.ogg",
            "title": "Catalog only",
        },
    ])

    authority = build_media_authority(
        state.story,
        music_manifest=manifest,
    )

    assert authority.music_ids == frozenset({
        "assets/bgm/catalog_only.ogg",
    })
    assert authority.filter_music_ids({
        "assets/bgm/catalog_only.ogg",
        "../forged.ogg",
    }) == {"assets/bgm/catalog_only.ogg"}


def test_media_authority_rejects_wrong_music_manifest_type() -> None:
    with pytest.raises(TypeError, match="music_manifest"):
        build_media_authority(
            GameState().story,
            music_manifest=object(),  # type: ignore[arg-type]
        )


def test_capability_schema_exposes_music_catalog_models_and_file_shapes() -> None:
    from world_gal_game.dev.capability_manifest import (
        build_manifest,
        schema_document,
    )

    manifest = build_manifest()
    schemas = schema_document()

    assert "MusicRoomEntrySpec" in manifest["content_schema"]
    assert "MusicRoomManifest" in manifest["content_schema"]
    assert "MusicRoomEntrySpec" in schemas["models"]
    assert (
        schemas["models"]["MusicRoomEntrySpec"]["additionalProperties"]
        is False
    )
    file_schema = schemas["content_files"]["content/music_room.yaml"]
    assert len(file_schema["oneOf"]) == 3
    assert file_schema["oneOf"][0]["properties"]["entries"]["maxItems"] == 4096
    assert file_schema["oneOf"][2]["maxProperties"] == 4096


def _write_minimal_music_pack(root: Path) -> None:
    content = root / "content"
    scenes = content / "scenes"
    bgm = root / "assets" / "bgm"
    scenes.mkdir(parents=True)
    bgm.mkdir(parents=True)
    (content / "meta.yaml").write_text(
        'pack_format_version: "0.1"\n'
        "title_bgm: assets/bgm/title.ogg\n",
        encoding="utf-8",
    )
    (scenes / "start.yaml").write_text(
        "scenes:\n"
        "  - id: start\n"
        "    bgm: assets/bgm/story.ogg\n"
        "    lines:\n"
        "      - text: hello\n",
        encoding="utf-8",
    )
    for name in ("title.ogg", "story.ogg", "physical_extra.ogg"):
        (bgm / name).write_bytes(b"not decoded during validation")


def test_validator_checks_music_assets_and_pack_wide_catalog_coverage(
    tmp_path: Path,
) -> None:
    from world_gal_game.validator import validate_pack

    _write_minimal_music_pack(tmp_path)
    (tmp_path / "content" / "music_room.yaml").write_text(
        "entries:\n"
        "  - asset: assets/bgm/story.ogg\n"
        "    title: Story Theme\n"
        "  - asset: assets/bgm/missing.ogg\n"
        "    title: Missing Theme\n",
        encoding="utf-8",
    )

    issues = validate_pack(tmp_path)

    assert any(
        issue.file.endswith("music_room.yaml")
        and issue.path == "entries[1].asset"
        and "檔案不存在" in issue.message
        for issue in issues
    )
    uncovered = {
        asset
        for asset in (
            "assets/bgm/title.ogg",
            "assets/bgm/physical_extra.ogg",
        )
        if any(
            asset in issue.message
            and "未在 music_room.yaml 宣告" in issue.message
            for issue in issues
        )
    }
    assert uncovered == {
        "assets/bgm/title.ogg",
        "assets/bgm/physical_extra.ogg",
    }
    assert not any(
        "assets/bgm/story.ogg" in issue.message
        and "未在 music_room.yaml 宣告" in issue.message
        for issue in issues
    )


def test_validator_reports_music_schema_paths_and_unsafe_extensions(
    tmp_path: Path,
) -> None:
    from world_gal_game.validator import validate_pack

    _write_minimal_music_pack(tmp_path)
    (tmp_path / "content" / "music_room.yaml").write_text(
        "- asset: ../outside.ogg\n"
        "  title: Unsafe\n"
        "- asset: assets/bgm/image.png\n"
        "  titel: Typo\n",
        encoding="utf-8",
    )

    issues = validate_pack(tmp_path)

    assert any(
        issue.severity == "error" and issue.path == "[0].asset"
        for issue in issues
    )
    assert any(issue.path == "[1].asset" for issue in issues)
    assert any(issue.path == "[1].title" for issue in issues)
    assert any(issue.path == "[1].titel" for issue in issues)


def test_validator_rejects_noncanonical_music_room_filename_case(
    tmp_path: Path,
) -> None:
    from world_gal_game.validator import validate_pack

    _write_minimal_music_pack(tmp_path)
    alias = tmp_path / "content" / "MUSIC_ROOM.YAML"
    alias.write_text("entries: []\n", encoding="utf-8")

    issues = validate_pack(tmp_path)

    assert any(
        issue.severity == "error"
        and issue.file.endswith("MUSIC_ROOM.YAML")
        and issue.path == "filename"
        and "music_room.yaml" in issue.message
        for issue in issues
    )


def test_validator_treats_scene_named_music_room_as_story_yaml(
    tmp_path: Path,
) -> None:
    from world_gal_game.validator import validate_pack

    _write_minimal_music_pack(tmp_path)
    (tmp_path / "content" / "music_room.yaml").write_text(
        "entries: []\n",
        encoding="utf-8",
    )
    (tmp_path / "content" / "scenes" / "music_room.yaml").write_text(
        "scenes:\n"
        "  - id: music_room_story\n"
        "    lines: [{text: story}]\n",
        encoding="utf-8",
    )

    issues = validate_pack(tmp_path)

    assert not any(
        issue.file.endswith("scenes/music_room.yaml")
        and "Music Room manifest" in issue.message
        for issue in issues
    )


def test_validator_rejects_duplicate_music_catalog_yaml_keys(
    tmp_path: Path,
) -> None:
    from world_gal_game.validator import validate_pack

    _write_minimal_music_pack(tmp_path)
    (tmp_path / "content" / "music_room.yaml").write_text(
        "entries:\n"
        "  - asset: assets/bgm/story.ogg\n"
        "    title: First\n"
        "    title: Duplicate\n",
        encoding="utf-8",
    )

    issues = validate_pack(tmp_path)

    assert any(
        issue.severity == "error"
        and issue.file.endswith("music_room.yaml")
        and "duplicate key" in issue.message
        for issue in issues
    )
