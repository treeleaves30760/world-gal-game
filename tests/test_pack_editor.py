"""Tests for PackEditor — comment-preserving structured edits."""
from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from world_gal_game.dev.pack_editor import PackEditor, PackEditError


# ----------------------------------------------------------------------
# Fixtures


@pytest.fixture
def fresh_demo_pack(tmp_path: Path) -> Path:
    """Copy demo_pack into a tmp dir so tests can mutate it freely."""
    dst = tmp_path / "demo_pack"
    shutil.copytree("games/demo_pack", dst)
    return dst


@pytest.fixture
def tiny_pack(tmp_path: Path) -> Path:
    """Minimal synthetic pack with a couple of scenes + chars + locs."""
    pack = tmp_path / "tinypack"
    (pack / "content/scenes").mkdir(parents=True)
    (pack / "content/meta.yaml").write_text(
        'pack_format_version: "0.1"\ntitle: tiny\nintro_scene: a\n',
        encoding="utf-8",
    )
    (pack / "content/scenes/all.yaml").write_text(textwrap.dedent("""
        # A starter scene
        scenes:
          - id: a
            title: A
            lines:
              - {text: hi}
            choices:
              - {id: c, text: go b, next_scene: b}
          - id: b
            title: B
            lines: [{text: hello}]
    """).strip() + "\n", encoding="utf-8")
    (pack / "content/characters.yaml").write_text(textwrap.dedent("""
        characters:
          - id: hero
            name: Hero
            role: protagonist
    """).strip() + "\n", encoding="utf-8")
    (pack / "content/locations.yaml").write_text(textwrap.dedent("""
        locations:
          - id: home
            name: Home
    """).strip() + "\n", encoding="utf-8")
    return pack


# ----------------------------------------------------------------------
# add_scene


def test_add_scene_writes_to_default_file(tiny_pack: Path):
    ed = PackEditor(tiny_pack)
    ed.add_scene({"id": "new", "title": "New", "lines": [{"text": "x"}]})
    generated = tiny_pack / "content/scenes/_generated.yaml"
    assert generated.is_file()
    content = generated.read_text(encoding="utf-8")
    assert "id: new" in content


def test_add_scene_into_specific_file(tiny_pack: Path):
    ed = PackEditor(tiny_pack)
    ed.add_scene({"id": "side", "title": "Side", "lines": [{"text": "x"}]},
                 into_file="side.yaml")
    target = tiny_pack / "content/scenes/side.yaml"
    assert target.is_file()
    assert "id: side" in target.read_text(encoding="utf-8")


def test_add_scene_duplicate_id_raises(tiny_pack: Path):
    ed = PackEditor(tiny_pack)
    with pytest.raises(PackEditError) as ei:
        ed.add_scene({"id": "a", "title": "Dup", "lines": [{"text": "x"}]})
    assert ei.value.op == "add_scene"
    assert "already exists" in ei.value.message


def test_add_scene_invalid_payload_raises(tiny_pack: Path):
    ed = PackEditor(tiny_pack)
    with pytest.raises(PackEditError) as ei:
        ed.add_scene({"id": "bad", "title": "Bad",
                      "unknown_field": "oops", "lines": []})
    assert ei.value.op == "add_scene"
    assert "unknown_field" in ei.value.field
    assert "valid fields" in ei.value.hint


# ----------------------------------------------------------------------
# update_scene + remove_scene


def test_update_scene_changes_title(tiny_pack: Path):
    ed = PackEditor(tiny_pack)
    ed.update_scene("a", {"title": "Renamed"})
    content = (tiny_pack / "content/scenes/all.yaml").read_text(encoding="utf-8")
    assert "title: Renamed" in content
    # Other keys preserved
    assert "id: b" in content


def test_remove_scene(tiny_pack: Path):
    ed = PackEditor(tiny_pack)
    ed.remove_scene("b")
    content = (tiny_pack / "content/scenes/all.yaml").read_text(encoding="utf-8")
    assert "id: b" not in content
    assert "id: a" in content


def test_remove_unknown_scene_raises(tiny_pack: Path):
    ed = PackEditor(tiny_pack)
    with pytest.raises(PackEditError):
        ed.remove_scene("does_not_exist")


# ----------------------------------------------------------------------
# add_choice


def test_add_choice_appends(tiny_pack: Path):
    ed = PackEditor(tiny_pack)
    ed.add_choice("a", {"id": "c2", "text": "another",
                        "next_scene": "b"})
    content = (tiny_pack / "content/scenes/all.yaml").read_text(encoding="utf-8")
    assert "id: c2" in content
    # The first choice (c) should still be there
    assert "id: c" in content


def test_add_choice_unknown_scene_raises(tiny_pack: Path):
    ed = PackEditor(tiny_pack)
    with pytest.raises(PackEditError):
        ed.add_choice("ghost", {"id": "x", "text": "hi"})


# ----------------------------------------------------------------------
# update_line


def test_update_line_changes_text_and_keeps_siblings(tiny_pack: Path):
    # Scene 'a' has one line {text: hi}; scene 'b' has {text: hello}.
    # Add a second line to 'a' first so we can prove siblings + comments
    # survive the in-place edit.
    ed = PackEditor(tiny_pack)
    # The starter comment "# A starter scene" must remain after the edit.
    ed.update_line("a", 0, {"text": "greetings", "speaker": "Hero"})
    content = (tiny_pack / "content/scenes/all.yaml").read_text(encoding="utf-8")
    assert "greetings" in content
    assert "speaker: Hero" in content
    # Sibling scene b's line untouched.
    assert "hello" in content
    # Author comment preserved.
    assert "# A starter scene" in content


def test_update_line_returns_validated_line(tiny_pack: Path):
    ed = PackEditor(tiny_pack)
    line = ed.update_line("b", 0, {"text": "new body"})
    assert line.text == "new body"


def test_update_line_out_of_range_raises_with_field_hint(tiny_pack: Path):
    ed = PackEditor(tiny_pack)
    with pytest.raises(PackEditError) as ei:
        ed.update_line("a", 5, {"text": "x"})
    assert ei.value.op == "update_line"
    assert ei.value.field == "line_index"
    assert ei.value.hint  # actionable
    assert "out of range" in ei.value.message


def test_update_line_unknown_field_raises(tiny_pack: Path):
    ed = PackEditor(tiny_pack)
    with pytest.raises(PackEditError) as ei:
        ed.update_line("a", 0, {"not_a_field": "oops"})
    assert ei.value.op == "update_line"
    assert "not_a_field" in ei.value.field
    assert "valid fields" in ei.value.hint


def test_update_line_unknown_scene_raises(tiny_pack: Path):
    ed = PackEditor(tiny_pack)
    with pytest.raises(PackEditError):
        ed.update_line("ghost", 0, {"text": "x"})


def test_update_line_dry_run_shows_diff_without_writing(tiny_pack: Path):
    before = (tiny_pack / "content/scenes/all.yaml").read_text(encoding="utf-8")
    ed = PackEditor(tiny_pack, dry_run=True)
    ed.update_line("a", 0, {"text": "deferred edit"})
    after = (tiny_pack / "content/scenes/all.yaml").read_text(encoding="utf-8")
    assert before == after  # nothing written
    diff = ed.diff()
    assert "deferred edit" in diff
    assert diff.startswith("---")


# ----------------------------------------------------------------------
# scaffold_save_migration


def test_scaffold_save_migration_bumps_meta_and_writes_stub(tiny_pack: Path):
    ed = PackEditor(tiny_pack)
    out = ed.scaffold_save_migration(
        from_version="0.1", to_version="0.2",
        reason="rename hp to health", pack_id="tinypack",
    )
    # meta.yaml bumped, comment-preserving path.
    meta = (tiny_pack / "content/meta.yaml").read_text(encoding="utf-8")
    assert 'pack_format_version: "0.2"' in meta or "pack_format_version: 0.2" in meta
    # title comment / other keys preserved.
    assert "title: tiny" in meta
    # Stub file written under plugins/<id>_migrations/.
    stub_path = tiny_pack / "plugins/tinypack_migrations/migration_0_1_0_2.py"
    assert stub_path.is_file()
    stub = stub_path.read_text(encoding="utf-8")
    assert '@save_migration("0.1", "0.2"' in stub
    assert "rename hp to health" in stub
    assert "from world_gal_game.plugins import save_migration" in stub
    assert out["stub_file"].endswith("migration_0_1_0_2.py")


def test_scaffold_save_migration_dry_run_writes_nothing(tiny_pack: Path):
    meta_before = (tiny_pack / "content/meta.yaml").read_text(encoding="utf-8")
    ed = PackEditor(tiny_pack, dry_run=True)
    ed.scaffold_save_migration(from_version="0.1", to_version="0.2",
                               pack_id="tinypack")
    # meta.yaml untouched on disk.
    assert (tiny_pack / "content/meta.yaml").read_text(encoding="utf-8") == meta_before
    # Stub NOT written.
    stub_path = tiny_pack / "plugins/tinypack_migrations/migration_0_1_0_2.py"
    assert not stub_path.exists()
    # But the diff shows both pending changes.
    diff = ed.diff()
    assert "pack_format_version" in diff
    assert "@save_migration" in diff


def test_scaffold_save_migration_default_pack_id(tiny_pack: Path):
    ed = PackEditor(tiny_pack)
    ed.scaffold_save_migration(from_version="0.1", to_version="0.2")
    # pack_id="" -> dir slug "pack".
    assert (tiny_pack / "plugins/pack_migrations/migration_0_1_0_2.py").is_file()


def test_scaffold_save_migration_existing_stub_raises(tiny_pack: Path):
    ed = PackEditor(tiny_pack)
    ed.scaffold_save_migration(from_version="0.1", to_version="0.2",
                               pack_id="tinypack")
    ed2 = PackEditor(tiny_pack)
    with pytest.raises(PackEditError) as ei:
        ed2.scaffold_save_migration(from_version="0.1", to_version="0.2",
                                    pack_id="tinypack")
    assert "already exists" in ei.value.message


# ----------------------------------------------------------------------
# add_npc / add_location / add_item


def test_add_npc(tiny_pack: Path):
    ed = PackEditor(tiny_pack)
    ed.add_npc({"id": "rival", "name": "Rival", "role": "antagonist"})
    content = (tiny_pack / "content/characters.yaml").read_text(encoding="utf-8")
    assert "id: rival" in content
    # Existing hero preserved
    assert "id: hero" in content


def test_add_npc_duplicate_raises(tiny_pack: Path):
    ed = PackEditor(tiny_pack)
    with pytest.raises(PackEditError):
        ed.add_npc({"id": "hero", "name": "Dup", "role": "x"})


def test_add_location(tiny_pack: Path):
    ed = PackEditor(tiny_pack)
    ed.add_location({"id": "park", "name": "Park"})
    content = (tiny_pack / "content/locations.yaml").read_text(encoding="utf-8")
    assert "id: park" in content
    assert "id: home" in content


def test_add_item(tiny_pack: Path):
    ed = PackEditor(tiny_pack)
    ed.add_item({"id": "coin", "name": "Coin", "value": 1})
    content = (tiny_pack / "content/items.yaml").read_text(encoding="utf-8")
    assert "id: coin" in content


# ----------------------------------------------------------------------
# dry_run + diff + commit + rollback


def test_dry_run_does_not_touch_disk(tiny_pack: Path):
    before = (tiny_pack / "content/characters.yaml").read_text(encoding="utf-8")
    ed = PackEditor(tiny_pack, dry_run=True)
    ed.add_npc({"id": "ghost", "name": "Ghost", "role": "haunting"})
    after = (tiny_pack / "content/characters.yaml").read_text(encoding="utf-8")
    assert before == after
    # But diff reports the pending change
    diff = ed.diff()
    assert "id: ghost" in diff
    assert diff.startswith("---")


def test_dry_run_commit_writes(tiny_pack: Path):
    ed = PackEditor(tiny_pack, dry_run=True)
    ed.add_npc({"id": "later", "name": "Later", "role": "deferred"})
    ed.commit()
    content = (tiny_pack / "content/characters.yaml").read_text(encoding="utf-8")
    assert "id: later" in content


def test_dry_run_rollback_discards(tiny_pack: Path):
    ed = PackEditor(tiny_pack, dry_run=True)
    ed.add_npc({"id": "transient", "name": "Transient", "role": "x"})
    ed.rollback()
    assert ed.diff() == ""
    content = (tiny_pack / "content/characters.yaml").read_text(encoding="utf-8")
    assert "id: transient" not in content


def test_diff_empty_when_no_changes(tiny_pack: Path):
    ed = PackEditor(tiny_pack, dry_run=True)
    assert ed.diff() == ""
    assert not ed.has_pending()


def test_list_changes_summarises(tiny_pack: Path):
    ed = PackEditor(tiny_pack, dry_run=True)
    ed.add_scene({"id": "s2", "title": "S2", "lines": [{"text": "x"}]})
    ed.add_npc({"id": "n2", "name": "N2", "role": "x"})
    changes = ed.list_changes()
    assert len(changes) == 2
    assert {c["op"] for c in changes} == {"add_scene", "add_npc"}


# ----------------------------------------------------------------------
# Comment preservation against real demo_pack


def test_comments_preserved_on_round_trip(fresh_demo_pack: Path):
    """Round-tripping a real pack should not destroy author comments."""
    meta_path = fresh_demo_pack / "content/characters.yaml"
    before = meta_path.read_text(encoding="utf-8")
    # Count comment markers as a proxy for "structure preserved"
    comment_count = before.count("#")

    ed = PackEditor(fresh_demo_pack)
    ed.add_npc({"id": "tester_one", "name": "Tester",
                "role": "qa", "is_heroine": False})
    after = meta_path.read_text(encoding="utf-8")
    # Comment markers should still be there (possibly +/- 0)
    assert after.count("#") >= comment_count - 1


def test_demo_pack_still_loads_after_edit(fresh_demo_pack: Path):
    """An edited pack must still load via load_pack."""
    from world_gal_game.content_loader import load_pack
    ed = PackEditor(fresh_demo_pack)
    ed.add_npc({"id": "post_edit_npc", "name": "PostEdit",
                "role": "test"})
    ed.add_scene({"id": "new_chat", "title": "After edit",
                  "lines": [{"text": "ok"}]})
    state, registry, meta = load_pack(fresh_demo_pack / "content")
    assert registry.get("post_edit_npc") is not None
    assert "new_chat" in state.story.scenes
