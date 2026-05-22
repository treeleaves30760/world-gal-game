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
