"""Tests for save_manager: round-trip, migration, thumbnails, error paths."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from world_gal_game.core.save_manager import (
    SaveManager,
    SaveCorruptedError,
    SaveNotFoundError,
    SaveSchemaError,
    CURRENT_SCHEMA_VERSION,
)


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


def test_save_load_meta_private_keys_stripped(sm: SaveManager) -> None:
    state = {**_minimal_state(), "meta": {"__bridge__": object(), "keep": 1}}
    sm.save("slot2", state)
    loaded = sm.load("slot2")
    # Private meta keys must not survive serialisation.
    assert "__bridge__" not in loaded.get("meta", {})
    assert loaded["meta"].get("keep") == 1


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
    (sm.save_dir / "good.json").write_text(
        json.dumps({"_label": "ok", "_summary": "", "_saved_at": ""}),
        encoding="utf-8",
    )
    (sm.save_dir / "bad.json").write_text("{not json", encoding="utf-8")
    saves = sm.list_saves()
    slots = [s["slot"] for s in saves]
    assert "good" in slots
    assert "bad" not in slots


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
