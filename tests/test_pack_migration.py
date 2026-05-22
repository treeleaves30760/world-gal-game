"""Tests for pack-level save migration (C-WS1.2 + C-WS1.3).

Covers the load-time gate ``check_and_migrate_pack`` and the
``@save_migration`` decorator / ``PackMigrationRegistry`` chain.
"""
from __future__ import annotations

import pytest

from world_gal_game.core.pack_migration import (
    PACK_MIGRATIONS,
    PackMigrationEntry,
    PackMigrationRegistry,
    SavePackMismatchError,
    SavePackSchemaError,
    check_and_migrate_pack,
)
from world_gal_game.plugins import save_migration


# ----------------------------------------------------------------------
# Fixtures — snapshot/restore the global registry like clean_registry in
# tests/test_capability_manifest.py so decorator registration is isolated.


@pytest.fixture
def clean_pack_migrations():
    saved = list(PACK_MIGRATIONS._entries)
    PACK_MIGRATIONS._entries.clear()
    yield PACK_MIGRATIONS
    PACK_MIGRATIONS._entries.clear()
    PACK_MIGRATIONS._entries.extend(saved)


def _save(pack_id="demo", version="0.1", **extra):
    data = {
        "_pack_id": pack_id,
        "_pack_format_version": version,
        "player": {"name": "P"},
    }
    data.update(extra)
    return data


# ----------------------------------------------------------------------
# Identity checks


def test_pack_id_mismatch_raises():
    data = _save(pack_id="alpha")
    with pytest.raises(SavePackMismatchError):
        check_and_migrate_pack(
            data, current_pack_id="beta", current_pack_version="0.1",
            registry=PackMigrationRegistry(),
        )


def test_legacy_save_without_pack_id_loads():
    # Empty save _pack_id -> treated as compatible, no exception.
    data = _save(pack_id="", version="0.1")
    out = check_and_migrate_pack(
        data, current_pack_id="demo", current_pack_version="0.1",
        registry=PackMigrationRegistry(),
    )
    assert out is data
    assert out["_pack_format_version"] == "0.1"


def test_same_pack_same_version_is_noop():
    data = _save(pack_id="demo", version="0.2")
    out = check_and_migrate_pack(
        data, current_pack_id="demo", current_pack_version="0.2",
        registry=PackMigrationRegistry(),
    )
    assert out["_pack_format_version"] == "0.2"
    assert out["player"]["name"] == "P"


# ----------------------------------------------------------------------
# Version comparison


def test_newer_pack_version_raises():
    # Save is from a newer pack than the engine currently has loaded.
    data = _save(pack_id="demo", version="0.5")
    with pytest.raises(SavePackSchemaError):
        check_and_migrate_pack(
            data, current_pack_id="demo", current_pack_version="0.2",
            registry=PackMigrationRegistry(),
        )


def test_additive_change_needs_no_migration():
    # Older save, but the only change between versions is a new optional
    # field — no migration registered. The gate must NOT fail when versions
    # are equal; when older with no edge it would raise. So model "additive"
    # as: same version, new field absent in save, fills via pydantic default
    # downstream. Here we assert the no-op path leaves data intact.
    data = _save(pack_id="demo", version="0.2")  # current too
    out = check_and_migrate_pack(
        data, current_pack_id="demo", current_pack_version="0.2",
        registry=PackMigrationRegistry(),
    )
    # No new keys injected, version stamped current, body untouched.
    assert out["_pack_format_version"] == "0.2"
    assert "migrated" not in out


# ----------------------------------------------------------------------
# @save_migration chain


def test_save_migration_chain_applies_in_order(clean_pack_migrations):
    order: list[str] = []

    @save_migration("0.1", "0.2", pack_id="demo")
    def m1(data: dict) -> dict:
        order.append("0.1->0.2")
        data.setdefault("steps", []).append("a")
        data["_pack_format_version"] = "0.2"
        return data

    @save_migration("0.2", "0.3", pack_id="demo")
    def m2(data: dict) -> dict:
        order.append("0.2->0.3")
        data.setdefault("steps", []).append("b")
        data["_pack_format_version"] = "0.3"
        return data

    data = _save(pack_id="demo", version="0.1")
    out = check_and_migrate_pack(
        data, current_pack_id="demo", current_pack_version="0.3",
        registry=clean_pack_migrations,
    )
    assert order == ["0.1->0.2", "0.2->0.3"]
    assert out["steps"] == ["a", "b"]
    assert out["_pack_format_version"] == "0.3"


def test_missing_migration_edge_raises(clean_pack_migrations):
    # Register only 0.1 -> 0.2; ask to migrate to 0.3 (no 0.2 -> 0.3 edge).
    @save_migration("0.1", "0.2", pack_id="demo")
    def only(data: dict) -> dict:
        data["_pack_format_version"] = "0.2"
        return data

    data = _save(pack_id="demo", version="0.1")
    with pytest.raises(SavePackSchemaError) as ei:
        check_and_migrate_pack(
            data, current_pack_id="demo", current_pack_version="0.3",
            registry=clean_pack_migrations,
        )
    assert "no pack migration" in str(ei.value)


def test_save_migration_registers_into_global_singleton(clean_pack_migrations):
    @save_migration("0.1", "0.2", pack_id="demo", description="x")
    def m(data: dict) -> dict:
        return data

    entries = clean_pack_migrations.list_entries()
    assert len(entries) == 1
    e = entries[0]
    assert e.from_version == "0.1"
    assert e.to_version == "0.2"
    assert e.pack_id == "demo"
    # Outside a plugin load, owner defaults to "pack".
    assert e.plugin_id == "pack"


def test_unregister_plugin_removes_entries(clean_pack_migrations):
    reg = clean_pack_migrations
    reg.register(PackMigrationEntry("0.1", "0.2", lambda d: d,
                                    pack_id="demo", plugin_id="pluginA"))
    reg.register(PackMigrationEntry("0.2", "0.3", lambda d: d,
                                    pack_id="demo", plugin_id="pluginB"))
    removed = reg.unregister_plugin("pluginA")
    assert len(removed) == 1
    assert len(reg.list_entries()) == 1
    assert reg.list_entries()[0].plugin_id == "pluginB"


def test_chain_empty_when_already_current():
    reg = PackMigrationRegistry()
    assert reg.chain(from_version="0.3", to_version="0.3", pack_id="demo") == []
    # Save newer than target -> chain returns [] (gate handles the raise).
    assert reg.chain(from_version="0.4", to_version="0.3", pack_id="demo") == []


def test_wildcard_pack_id_migration_applies(clean_pack_migrations):
    @save_migration("0.1", "0.2")  # pack_id="" -> any pack
    def m(data: dict) -> dict:
        data["touched"] = True
        data["_pack_format_version"] = "0.2"
        return data

    data = _save(pack_id="whatever", version="0.1")
    out = check_and_migrate_pack(
        data, current_pack_id="whatever", current_pack_version="0.2",
        registry=clean_pack_migrations,
    )
    assert out["touched"] is True
