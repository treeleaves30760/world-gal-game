"""Chapter/act/route manifest (``world_gal_game.core.chapter_spec``).

Covers the optional ``content/chapters.yaml`` overlay: model + loader helpers,
that load_pack parks it on the private meta bridge, the inspector view + counts
+ cross-check, and that a pack without one is unaffected.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from world_gal_game.config import EngineConfig
from world_gal_game.content_loader import load_pack
from world_gal_game.core.chapter_spec import ChapterManifest, ChapterSpec
from world_gal_game.dev.pack_inspector import PackInspector
from world_gal_game.headless import HeadlessSession

DEMO = Path("games/demo_pack")


# ----------------------------------------------------------------------
# model + loader helpers


def test_manifest_from_list_and_mapping_equivalent() -> None:
    as_list = ChapterManifest.from_items([
        {"id": "a", "route": "r1", "order": 2, "scenes": ["s2"]},
        {"id": "b", "route": "r1", "order": 1, "scenes": ["s1"]},
    ])
    # ordered() sorts by (order, id): b (order 1) before a (order 2).
    assert [c.id for c in as_list.ordered()] == ["b", "a"]
    assert as_list.by_route()["r1"][0].id == "b"
    assert as_list.scene_to_chapter() == {"s1": "b", "s2": "a"}


def test_manifest_rejects_duplicate_ids() -> None:
    with pytest.raises(ValueError):
        ChapterManifest.from_items([{"id": "x"}, {"id": "x"}])


def test_chapter_spec_is_strict() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ChapterSpec(id="x", bogus="y")


def test_missing_file_is_empty_manifest(tmp_path: Path) -> None:
    assert ChapterManifest.load(tmp_path / "nope.yaml").chapters == []


# ----------------------------------------------------------------------
# integration: demo_pack ships a manifest


def test_load_pack_parks_manifest_on_meta() -> None:
    state, _npcs, _meta = load_pack(DEMO / "content")
    manifest = state.meta["__chapters__"]
    assert isinstance(manifest, ChapterManifest)
    assert "ch1_arrival" in manifest.ids()


def test_inspector_chapters_and_counts() -> None:
    inspector = PackInspector(DEMO)
    rows = inspector.chapters()
    assert [r["id"] for r in rows][:2] == ["ch1_arrival", "ch2_investigation"]
    assert inspector.summary()["counts"]["chapters"] == len(rows) == 5


def test_demo_chapter_check_is_clean() -> None:
    issues = PackInspector(DEMO).chapter_issues()
    assert issues["unknown_scenes"] == []
    assert issues["uncovered_scenes"] == []


def test_chapter_issues_flags_unknown_and_uncovered(tmp_path: Path) -> None:
    import shutil
    dest = tmp_path / "demo"
    shutil.copytree(DEMO, dest)
    # A manifest that references a missing scene and covers only one real scene.
    (dest / "content" / "chapters.yaml").write_text(
        "chapters:\n"
        "  - id: only\n"
        "    scenes: [prologue, ghost_scene_that_does_not_exist]\n",
        encoding="utf-8")
    issues = PackInspector(dest).chapter_issues()
    assert issues["unknown_scenes"] == ["ghost_scene_that_does_not_exist"]
    assert "meet_heroine" in issues["uncovered_scenes"]


def test_pack_without_chapters_is_unaffected(tmp_path: Path) -> None:
    import shutil
    dest = tmp_path / "demo"
    shutil.copytree(DEMO, dest)
    (dest / "content" / "chapters.yaml").unlink()
    inspector = PackInspector(dest)
    assert inspector.chapters() == []
    assert inspector.summary()["counts"]["chapters"] == 0
    # And the pack still loads + a session opens fine.
    sess = HeadlessSession.open(EngineConfig(seed=1), pack=str(dest))
    assert sess.state.meta["__chapters__"].chapters == []
