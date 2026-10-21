"""Tests for save_manager: round-trip, migration, thumbnails, error paths."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from world_gal_game.core.game_state import GameState
from world_gal_game.core.save_manager import (
    SaveManager,
    SaveCorruptedError,
    SaveNotFoundError,
    SaveSchemaError,
    SaveSerializationError,
    CURRENT_SCHEMA_VERSION,
)
from world_gal_game.core.story_graph import Line, Scene
from world_gal_game.dialogue.dialogue_engine import DialogueEngine


# ---------- fixtures ---------------------------------------------------------


@pytest.fixture
def sm(tmp_path: Path) -> SaveManager:
    return SaveManager(tmp_path)


def _minimal_state() -> dict:
    """Bare-minimum state dict that save() is happy to persist."""
    return {
        "player": {"name": "TestPlayer"},
        "meta": {},
    }


# ---------- round-trip -------------------------------------------------------


def test_save_load_basic(sm: SaveManager) -> None:
    state = _minimal_state()
    sm.save("slot1", state, label="Test save", summary="Day 1 morning")
    loaded = sm.load("slot1")
    # User data is preserved.
    assert loaded["player"]["name"] == "TestPlayer"
    # Save-manager bookkeeping is present.
    assert loaded["_schema_version"] == CURRENT_SCHEMA_VERSION
    assert loaded["_label"] == "Test save"
    assert loaded["_summary"] == "Day 1 morning"
    assert "_saved_at" in loaded


def test_completed_replay_archive_survives_formal_save_roundtrip(
    sm: SaveManager,
) -> None:
    state = GameState()
    state.story.add_scene(
        Scene(id="saved_memory", lines=[Line(text="被保存的回想")])
    )
    engine = DialogueEngine(state)
    assert engine.start_scene("saved_memory").kind == "line"
    assert engine.next_line().kind == "end"

    sm.save("with_replay", state.model_dump(mode="json"))
    restored = GameState.model_validate(sm.load("with_replay"))

    entry = restored.read_log.replay_entry("saved_memory")
    assert entry is not None
    assert entry.presentations[0]["line"]["text"] == "被保存的回想"


def test_save_load_meta_private_keys_stripped(sm: SaveManager) -> None:
    state = {**_minimal_state(), "meta": {"__bridge__": object(), "keep": 1}}
    sm.save("slot2", state)
    loaded = sm.load("slot2")
    # Private meta keys must not survive serialisation.
    assert "__bridge__" not in loaded.get("meta", {})
    assert loaded["meta"].get("keep") == 1


# ---------- resume context ---------------------------------------------------


def test_resume_context_round_trips_at_top_level(sm: SaveManager) -> None:
    resume = {
        "screen": "dialogue",
        "scene_id": "lake_confession",
        "line_index": 12,
        "choice_visible": False,
        "presentation": {
            "mode": "adv",
            "portraits": ["heroine", None],
            "camera": {"zoom": 1.15, "pan": [12, -4]},
        },
    }

    path = sm.save("resume", _minimal_state(), resume_context=resume)

    # The envelope is present in the file and load() returns it unchanged.
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["_resume"] == resume
    assert sm.load("resume")["_resume"] == resume


def test_save_without_resume_context_keeps_legacy_shape(
    sm: SaveManager,
) -> None:
    path = sm.save("legacy-shape", _minimal_state())

    assert "_resume" not in json.loads(path.read_text(encoding="utf-8"))
    assert "_resume" not in sm.load("legacy-shape")


def test_legacy_file_without_resume_context_still_loads(
    sm: SaveManager,
) -> None:
    # Pre-versioned saves migrate as before; the migration does not invent a
    # presentation context that was never recorded.
    path = sm.save_dir / "pre_resume.json"
    path.write_text(
        json.dumps({"player": {"name": "OldPlayer"}, "meta": {}}),
        encoding="utf-8",
    )

    loaded = sm.load("pre_resume")

    assert loaded["_schema_version"] == CURRENT_SCHEMA_VERSION
    assert "_resume" not in loaded


@pytest.mark.parametrize(
    "invalid_resume",
    [
        {"opaque": object()},
        {"set": {"left", "right"}},
        {"tuple": ("left", "right")},
        {1: "non-string key"},
        {"not_finite": float("nan")},
    ],
    ids=["object", "set", "tuple", "non-string-key", "nan"],
)
def test_invalid_resume_context_fails_without_damaging_existing_slot(
    sm: SaveManager,
    invalid_resume,
) -> None:
    original = {"screen": "exploration", "location_id": "park"}
    sm.save("stable", _minimal_state(), resume_context=original)

    with pytest.raises(SaveSerializationError, match="resume_context"):
        sm.save(
            "stable",
            {"player": {"name": "Replacement"}, "meta": {}},
            resume_context=invalid_resume,
        )

    # Validation happens before temp-file creation/backup rotation.
    loaded = sm.load("stable")
    assert loaded["player"]["name"] == "TestPlayer"
    assert loaded["_resume"] == original
    assert not (sm.save_dir / "stable.json.tmp").exists()


# ---------- thumbnail --------------------------------------------------------


def test_list_saves_thumbnail_path_none_when_no_thumbnail(sm: SaveManager) -> None:
    sm.save("slotA", _minimal_state())
    saves = sm.list_saves()
    assert len(saves) == 1
    assert saves[0]["thumbnail_path"] is None


def test_list_saves_thumbnail_path_present_when_png_exists(sm: SaveManager) -> None:
    sm.save("slotB", _minimal_state())
    # Manually plant a PNG sibling to simulate a real thumbnail save.
    thumb = sm.save_dir / "slotB.png"
    thumb.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)  # minimal PNG header
    saves = sm.list_saves()
    assert saves[0]["thumbnail_path"] == str(thumb)


def test_save_with_thumbnail_none_does_not_create_png(sm: SaveManager) -> None:
    sm.save("slotC", _minimal_state(), thumbnail=None)
    assert not (sm.save_dir / "slotC.png").exists()


def test_successful_thumbnail_commit_matches_saved_json(sm: SaveManager) -> None:
    pygame = pytest.importorskip("pygame")
    thumbnail = pygame.Surface((640, 360))
    thumbnail.fill((24, 96, 180))

    json_path = sm.save("slot_thumb", _minimal_state(), thumbnail=thumbnail)

    thumb_path = sm.save_dir / "slot_thumb.png"
    assert thumb_path.exists()
    assert pygame.image.load(thumb_path).get_size() == (320, 180)
    assert json.loads(json_path.read_text(encoding="utf-8"))[
        "_thumbnail_path"
    ] == str(thumb_path)
    assert not (sm.save_dir / ".slot_thumb.tmp.png").exists()
    assert not (sm.save_dir / ".slot_thumb.rollback.png").exists()


def test_overwrite_without_thumbnail_removes_previous_png(
    sm: SaveManager,
) -> None:
    pygame = pytest.importorskip("pygame")
    thumbnail = pygame.Surface((640, 360))
    sm.save("slot_clear", _minimal_state(), thumbnail=thumbnail)
    thumb_path = sm.save_dir / "slot_clear.png"
    assert thumb_path.exists()

    sm.save(
        "slot_clear",
        {"player": {"name": "Replacement"}, "meta": {}},
        thumbnail=None,
    )

    assert sm.load("slot_clear")["player"]["name"] == "Replacement"
    assert sm.load("slot_clear")["_thumbnail_path"] is None
    assert not thumb_path.exists()


def test_json_commit_failure_restores_previous_json_and_thumbnail(
    sm: SaveManager,
    monkeypatch,
) -> None:
    import world_gal_game.core.save_manager as save_manager_module

    pygame = pytest.importorskip("pygame")
    sm.save("slot_atomic", _minimal_state(), summary="old")
    json_path = sm.save_dir / "slot_atomic.json"
    thumb_path = sm.save_dir / "slot_atomic.png"
    thumb_path.write_bytes(b"old-thumbnail")
    old_json = json_path.read_bytes()
    old_thumb = thumb_path.read_bytes()

    real_replace = save_manager_module.os.replace

    def _fail_final_json_replace(src, dst) -> None:
        if Path(src) == json_path.with_suffix(".json.tmp") and Path(dst) == json_path:
            raise OSError("simulated final JSON replace failure")
        real_replace(src, dst)

    monkeypatch.setattr(save_manager_module.os, "replace", _fail_final_json_replace)
    replacement_thumbnail = pygame.Surface((640, 360))
    replacement_thumbnail.fill((180, 40, 24))

    with pytest.raises(OSError, match="simulated final JSON replace failure"):
        sm.save(
            "slot_atomic",
            {"player": {"name": "Replacement"}, "meta": {}},
            summary="new",
            thumbnail=replacement_thumbnail,
        )

    assert json_path.read_bytes() == old_json
    assert thumb_path.read_bytes() == old_thumb
    assert not (sm.save_dir / "slot_atomic.json.tmp").exists()
    assert not (sm.save_dir / "slot_atomic.json.bak.tmp").exists()
    assert not (sm.save_dir / ".slot_atomic.tmp.png").exists()
    assert not (sm.save_dir / ".slot_atomic.rollback.png").exists()


def test_thumbnail_encoding_failure_still_commits_json_without_stale_png(
    sm: SaveManager,
    monkeypatch,
) -> None:
    pygame = pytest.importorskip("pygame")
    sm.save("slot_png_error", _minimal_state())
    thumb_path = sm.save_dir / "slot_png_error.png"
    thumb_path.write_bytes(b"old-thumbnail")

    def _fail_image_save(*_args, **_kwargs) -> None:
        raise OSError("simulated PNG encoder failure")

    monkeypatch.setattr(pygame.image, "save", _fail_image_save)
    requested_thumbnail = pygame.Surface((640, 360))

    sm.save(
        "slot_png_error",
        {"player": {"name": "Replacement"}, "meta": {}},
        thumbnail=requested_thumbnail,
    )

    loaded = sm.load("slot_png_error")
    assert loaded["player"]["name"] == "Replacement"
    assert loaded["_thumbnail_path"] is None
    assert not thumb_path.exists()
    assert not (sm.save_dir / ".slot_png_error.tmp.png").exists()


# ---------- error paths ------------------------------------------------------


def test_load_missing_slot_raises_not_found(sm: SaveManager) -> None:
    with pytest.raises(SaveNotFoundError):
        sm.load("nonexistent_slot")


def test_load_corrupted_json_raises_corrupted(sm: SaveManager, tmp_path: Path) -> None:
    bad = tmp_path / "broken.json"
    bad.write_text("{invalid json", encoding="utf-8")
    sm2 = SaveManager(tmp_path)
    with pytest.raises(SaveCorruptedError):
        sm2.load("broken")


def test_load_rejects_oversized_json_before_parsing(
    sm: SaveManager,
    monkeypatch,
) -> None:
    import world_gal_game.core.save_manager as module

    path = sm.save_dir / "oversized.json"
    path.write_text(
        json.dumps({**_minimal_state(), "padding": "x" * 256}),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "MAX_SAVE_JSON_BYTES", 128)

    with pytest.raises(SaveCorruptedError, match="safety limit"):
        sm.load("oversized")


def test_save_refuses_payload_it_could_not_load(
    sm: SaveManager,
    monkeypatch,
) -> None:
    import world_gal_game.core.save_manager as module

    monkeypatch.setattr(module, "MAX_SAVE_JSON_BYTES", 128)
    with pytest.raises(SaveSerializationError, match="safety limit"):
        sm.save(
            "too_large",
            {**_minimal_state(), "padding": "x" * 512},
        )
    assert not (sm.save_dir / "too_large.json").exists()


# ---------- migration --------------------------------------------------------


def test_load_v0_save_migrates_to_current(sm: SaveManager) -> None:
    """A file without _schema_version (version 0) should be migrated successfully."""
    v0_data = {"player": {"name": "OldPlayer"}, "meta": {}}
    # No _schema_version key — that's a v0 save.
    slot_path = sm.save_dir / "old_save.json"
    slot_path.write_text(json.dumps(v0_data), encoding="utf-8")

    loaded = sm.load("old_save")
    assert loaded["_schema_version"] == CURRENT_SCHEMA_VERSION
    assert loaded["player"]["name"] == "OldPlayer"


def test_load_unknown_future_version_raises_schema_error(
    sm: SaveManager,
) -> None:
    """A save from a future engine version should raise SaveSchemaError."""
    future_data = {"player": {}, "meta": {}, "_schema_version": 999}
    slot_path = sm.save_dir / "future.json"
    slot_path.write_text(json.dumps(future_data), encoding="utf-8")

    with pytest.raises(SaveSchemaError):
        sm.load("future")


# ---------- list_saves -------------------------------------------------------


def test_list_saves_returns_newest_first(sm: SaveManager) -> None:
    import time as _time
    sm.save("alpha", _minimal_state())
    _time.sleep(0.01)  # ensure different mtime
    sm.save("beta", _minimal_state())
    saves = sm.list_saves()
    assert saves[0]["slot"] == "beta"
    assert saves[1]["slot"] == "alpha"


def test_list_saves_skips_corrupted_files(sm: SaveManager) -> None:
    sm.save("good", _minimal_state(), label="ok")
    (sm.save_dir / "bad.json").write_text("{not json", encoding="utf-8")
    saves = sm.list_saves()
    slots = [s["slot"] for s in saves]
    assert "good" in slots
    assert "bad" not in slots


def test_list_saves_skips_oversized_files(sm: SaveManager, monkeypatch) -> None:
    import world_gal_game.core.save_manager as module

    sm.save("good", _minimal_state())
    oversized = sm.save_dir / "oversized.json"
    oversized.write_text(
        json.dumps({
            **_minimal_state(),
            "_schema_version": CURRENT_SCHEMA_VERSION,
            "_saved_at": "2026-08-01T00:00:00+00:00",
            "_label": "large",
            "_summary": "",
            "padding": "x" * 512,
        }),
        encoding="utf-8",
    )
    good_size = (sm.save_dir / "good.json").stat().st_size
    monkeypatch.setattr(module, "MAX_SAVE_JSON_BYTES", good_size + 16)

    assert [item["slot"] for item in sm.list_saves()] == ["good"]


def test_list_saves_excludes_clear_data_even_if_it_resembles_a_save(
    sm: SaveManager,
) -> None:
    # Filename guard: a damaged/hand-edited clear-data file must stay hidden
    # even if its structure happens to pass the formal-save check.
    (sm.save_dir / "clear_data.json").write_text(
        json.dumps({
            **_minimal_state(),
            "_schema_version": CURRENT_SCHEMA_VERSION,
            "_saved_at": "2026-07-27T00:00:00+00:00",
            "_label": "not a slot",
            "_summary": "",
        }),
        encoding="utf-8",
    )

    assert sm.list_saves() == []


@pytest.mark.parametrize(
    ("filename", "payload"),
    [
        ("notes.json", {"notes": ["not", "a", "save"]}),
        ("settings.json", {"player": "keyboard", "volume": 0.5}),
        (
            "metadata_only.json",
            {"_saved_at": "", "_label": "not state", "_summary": ""},
        ),
    ],
)
def test_list_saves_excludes_other_non_save_json(
    sm: SaveManager,
    filename: str,
    payload: dict,
) -> None:
    (sm.save_dir / filename).write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    assert sm.list_saves() == []


def test_list_saves_keeps_legacy_v0_slot_discoverable_and_migratable(
    sm: SaveManager,
) -> None:
    legacy = {
        **_minimal_state(),
        "_saved_at": "2024-01-01T00:00:00+00:00",
        "_label": "Legacy",
        "_summary": "Before save schemas",
    }
    (sm.save_dir / "legacy.json").write_text(
        json.dumps(legacy),
        encoding="utf-8",
    )

    assert [row["slot"] for row in sm.list_saves()] == ["legacy"]
    loaded = sm.load("legacy")
    assert loaded["_schema_version"] == CURRENT_SCHEMA_VERSION
    assert loaded["player"]["name"] == "TestPlayer"


# ---------- delete -----------------------------------------------------------


def test_delete_removes_json_and_png(sm: SaveManager) -> None:
    sm.save("del_slot", _minimal_state())
    png = sm.save_dir / "del_slot.png"
    png.write_bytes(b"FAKE")
    assert sm.delete("del_slot") is True
    assert not (sm.save_dir / "del_slot.json").exists()
    assert not png.exists()


def test_delete_nonexistent_returns_false(sm: SaveManager) -> None:
    assert sm.delete("ghost") is False


def test_delete_json_commit_failure_restores_live_json_and_thumbnail(
    sm: SaveManager,
    monkeypatch,
) -> None:
    import world_gal_game.core.save_manager as module

    sm.save("delete_atomic", _minimal_state(), label="Keep me")
    json_path = sm.save_dir / "delete_atomic.json"
    thumb_path = sm.save_dir / "delete_atomic.png"
    thumb_path.write_bytes(b"original-thumbnail")
    old_json = json_path.read_bytes()
    old_thumb = thumb_path.read_bytes()
    staged_json = sm._delete_tombstone(json_path)
    real_replace = module.os.replace

    def _fail_json_commit(src, dst) -> None:
        if Path(src) == json_path and Path(dst) == staged_json:
            raise OSError("simulated delete commit failure")
        real_replace(src, dst)

    monkeypatch.setattr(module.os, "replace", _fail_json_commit)

    with pytest.raises(OSError, match="delete commit failure"):
        sm.delete("delete_atomic")

    assert json_path.read_bytes() == old_json
    assert thumb_path.read_bytes() == old_thumb
    assert sm.load("delete_atomic")["_label"] == "Keep me"
    assert not staged_json.exists()
    assert not sm._delete_tombstone(thumb_path).exists()


def test_delete_thumbnail_stage_failure_is_a_complete_noop(
    sm: SaveManager,
    monkeypatch,
) -> None:
    import world_gal_game.core.save_manager as module

    sm.save("delete_thumb_fail", _minimal_state())
    json_path = sm.save_dir / "delete_thumb_fail.json"
    thumb_path = sm.save_dir / "delete_thumb_fail.png"
    thumb_path.write_bytes(b"original-thumbnail")
    old_json = json_path.read_bytes()
    staged_thumb = sm._delete_tombstone(thumb_path)
    real_replace = module.os.replace

    def _fail_thumbnail_stage(src, dst) -> None:
        if Path(src) == thumb_path and Path(dst) == staged_thumb:
            raise OSError("simulated thumbnail staging failure")
        real_replace(src, dst)

    monkeypatch.setattr(module.os, "replace", _fail_thumbnail_stage)

    with pytest.raises(OSError, match="thumbnail staging failure"):
        sm.delete("delete_thumb_fail")

    assert json_path.read_bytes() == old_json
    assert thumb_path.read_bytes() == b"original-thumbnail"
    assert not staged_thumb.exists()


def test_manager_recovers_thumbnail_from_interrupted_precommit_delete(
    sm: SaveManager,
) -> None:
    sm.save("delete_recover", _minimal_state())
    json_path = sm.save_dir / "delete_recover.json"
    thumb_path = sm.save_dir / "delete_recover.png"
    thumb_path.write_bytes(b"recoverable-thumbnail")
    staged_thumb = sm._delete_tombstone(thumb_path)
    thumb_path.replace(staged_thumb)

    recovered = SaveManager(sm.save_dir)

    assert json_path.exists()
    assert thumb_path.read_bytes() == b"recoverable-thumbnail"
    assert not staged_thumb.exists()
    assert recovered.load("delete_recover")["player"]["name"] == "TestPlayer"


def test_manager_finishes_cleanup_after_delete_commit_interruption(
    sm: SaveManager,
) -> None:
    sm.save("delete_committed", _minimal_state())
    json_path = sm.save_dir / "delete_committed.json"
    thumb_path = sm.save_dir / "delete_committed.png"
    thumb_path.write_bytes(b"committed-thumbnail")
    staged_json = sm._delete_tombstone(json_path)
    staged_thumb = sm._delete_tombstone(thumb_path)
    thumb_path.replace(staged_thumb)
    json_path.replace(staged_json)

    recovered = SaveManager(sm.save_dir)

    assert recovered.list_saves() == []
    assert not json_path.exists()
    assert not thumb_path.exists()
    assert not staged_json.exists()
    assert not staged_thumb.exists()


# ---------- pack identity ----------------------------------------------------


def test_save_writes_pack_identity(sm: SaveManager) -> None:
    sm.save(
        "p1", _minimal_state(),
        pack_meta={"pack_id": "demo_pack",
                   "pack_format_version": "0.2",
                   "engine_version": "0.1.0"},
    )
    loaded = sm.load("p1")
    assert loaded["_pack_id"] == "demo_pack"
    assert loaded["_pack_format_version"] == "0.2"
    assert loaded["_engine_version"] == "0.1.0"


def test_save_without_pack_meta_uses_safe_defaults(sm: SaveManager) -> None:
    sm.save("p2", _minimal_state())
    loaded = sm.load("p2")
    assert loaded["_pack_id"] == ""
    assert loaded["_pack_format_version"] == "0"
    assert loaded["_engine_version"] == ""


def test_list_saves_includes_pack_identity(sm: SaveManager) -> None:
    sm.save(
        "p3", _minimal_state(),
        pack_meta={"pack_id": "demo_pack",
                   "pack_format_version": "0.1",
                   "engine_version": "0.1.0"},
    )
    rows = sm.list_saves()
    row = next(r for r in rows if r["slot"] == "p3")
    assert row["pack_id"] == "demo_pack"
    assert row["pack_format_version"] == "0.1"
    assert row["engine_version"] == "0.1.0"


# ---------- web storage flush hook -------------------------------------------
# On the web, save()/delete() must trigger an IDBFS->IndexedDB flush. Off-web
# (the test host) it's a no-op, and these tests pin both: that save still
# round-trips, and that the flush hook is invoked exactly where expected.


def test_save_invokes_flush_storage_but_still_round_trips(
    sm: SaveManager, monkeypatch,
) -> None:
    import world_gal_game.platform_web as pw
    calls = {"n": 0}
    monkeypatch.setattr(pw, "flush_storage", lambda: calls.__setitem__("n", calls["n"] + 1))
    sm.save("flushed", _minimal_state())
    # flush hook fired once on save...
    assert calls["n"] == 1
    # ...and the data still round-trips unchanged.
    loaded = sm.load("flushed")
    assert loaded["player"]["name"] == "TestPlayer"


def test_delete_invokes_flush_storage(sm: SaveManager, monkeypatch) -> None:
    import world_gal_game.platform_web as pw
    sm.save("to_delete", _minimal_state())
    calls = {"n": 0}
    monkeypatch.setattr(pw, "flush_storage", lambda: calls.__setitem__("n", calls["n"] + 1))
    assert sm.delete("to_delete") is True
    assert calls["n"] == 1  # delete flushed
    # Deleting a missing slot must NOT flush (nothing changed on disk).
    calls["n"] = 0
    assert sm.delete("never_existed") is False
    assert calls["n"] == 0


def test_save_unaffected_when_flush_raises(sm: SaveManager, monkeypatch) -> None:
    """A flush failure must never abort the save (it's best-effort)."""
    import world_gal_game.platform_web as pw

    def _boom() -> None:
        raise RuntimeError("indexeddb exploded")

    monkeypatch.setattr(pw, "flush_storage", _boom)
    # save() swallows the flush error and still persists.
    sm.save("resilient", _minimal_state())
    assert sm.load("resilient")["player"]["name"] == "TestPlayer"
