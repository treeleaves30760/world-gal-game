"""Pack-level save migration — the content-version layer above schema.

The engine already migrates the *save schema* (envelope shape) in
:mod:`world_gal_game.core.save_manager` via ``CURRENT_SCHEMA_VERSION`` /
``MIGRATIONS`` / ``_migrate``. This module is the parallel mechanism for the
*pack content* version: a save records which pack produced it
(``_pack_id``) and the pack's ``pack_format_version`` (as ``major.minor``).
On load we:

- reject a save that came from a *different* pack (id mismatch), and
- walk a registry of ``@save_migration`` transforms to bring an *older*
  content version up to the pack's current version (or fail fast on a
  missing edge / a *newer* save than the engine pack knows about).

This mirrors the engine ``_migrate`` pattern exactly, but the registry is
plugin-extensible: packs register migrations with the
:func:`world_gal_game.plugins.save_migration` decorator, which stamps them
into the module-level :data:`PACK_MIGRATIONS` singleton.

Design notes:

- **Pure-additive content changes need no migration.** New *optional* fields
  on Line / Scene / PortraitSpec etc. are filled by pydantic defaults when
  ``GameState(**data)`` reconstructs the state, so a save written before the
  field existed still loads. Migrations exist only for *breaking* changes:
  renames, restructures, removals.
- The module is intentionally **pygame-free and import-light** (same layer as
  ``save_manager``): it depends only on ``save_manager.SaveError`` for the
  exception base.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

from .save_manager import SaveError


# ---------- exceptions -------------------------------------------------------

class SavePackMismatchError(SaveError):
    """The save was produced by a different pack than the one loaded."""


class SavePackSchemaError(SaveError):
    """The save's pack content version can't be migrated to the current one.

    Raised when the save is *newer* than the loaded pack, or when the
    migration registry has no edge bridging two adjacent versions.
    """


# ---------- version helpers --------------------------------------------------

def _parse_version(raw: str) -> tuple[int, int]:
    """Parse a ``"major.minor"`` string into a comparable ``(major, minor)``.

    Missing / malformed parts degrade to ``0`` so a bare ``"1"`` becomes
    ``(1, 0)`` and an empty string becomes ``(0, 0)``. Anything entirely
    unparseable is treated as ``(0, 0)`` rather than raising — version
    comparison should never crash a load.
    """
    s = str(raw or "0").strip()
    parts = s.split(".")
    major = _to_int(parts[0]) if parts else 0
    minor = _to_int(parts[1]) if len(parts) > 1 else 0
    return (major, minor)


def _to_int(part: str) -> int:
    try:
        return int(part)
    except (TypeError, ValueError):
        return 0


def _format_version(ver: tuple[int, int]) -> str:
    return f"{ver[0]}.{ver[1]}"


# ---------- registry entry ---------------------------------------------------

@dataclass(frozen=True)
class PackMigrationEntry:
    """One ``from_version -> to_version`` transform for a pack's saves.

    ``fn`` takes the loaded save ``dict`` and returns the transformed
    ``dict`` (typically mutating in place and returning it). ``pack_id``
    scopes the migration to a single pack (empty = applies to any pack,
    handy for engine-wide content shims). ``plugin_id`` records the owning
    plugin so :meth:`PackMigrationRegistry.unregister_plugin` can clean up.
    """

    from_version: str
    to_version: str
    fn: Callable[[dict], dict]
    pack_id: str = ""
    plugin_id: str = "pack"
    description: str = ""

    @property
    def from_tuple(self) -> tuple[int, int]:
        return _parse_version(self.from_version)

    @property
    def to_tuple(self) -> tuple[int, int]:
        return _parse_version(self.to_version)


# ---------- registry ---------------------------------------------------------

class PackMigrationRegistry:
    """Ordered, plugin-extensible set of pack save migrations.

    Mirrors the dedupe / ``unregister_plugin`` semantics of
    :class:`world_gal_game.plugins.registry._KindRegistry`. Lookups build a
    migration *chain* from a starting version up to a target version,
    raising :class:`SavePackSchemaError` on a missing edge.
    """

    def __init__(self) -> None:
        self._entries: list[PackMigrationEntry] = []
        self._lock = threading.RLock()

    # -- mutating ------------------------------------------------------

    def register(self, entry: PackMigrationEntry) -> None:
        """Register ``entry``. Re-registering the identical (from, to, fn,
        pack_id, plugin_id) tuple is a no-op so module re-import (hot reload,
        repeated tests) stays idempotent. A *different* fn for the same
        (from, to, pack_id) edge replaces nothing and is rejected as a
        duplicate edge.
        """
        with self._lock:
            for existing in self._entries:
                same_edge = (
                    existing.from_version == entry.from_version
                    and existing.to_version == entry.to_version
                    and existing.pack_id == entry.pack_id
                )
                if same_edge:
                    if (existing.fn is entry.fn
                            and existing.plugin_id == entry.plugin_id):
                        return  # idempotent
                    raise ValueError(
                        f"duplicate pack migration edge "
                        f"{entry.from_version} -> {entry.to_version} "
                        f"for pack_id={entry.pack_id!r} "
                        f"(owned by {existing.plugin_id!r}, "
                        f"new {entry.plugin_id!r})"
                    )
            self._entries.append(entry)

    def unregister_plugin(self, plugin_id: str) -> list[PackMigrationEntry]:
        """Remove every entry owned by ``plugin_id``. Return those removed."""
        removed: list[PackMigrationEntry] = []
        with self._lock:
            kept: list[PackMigrationEntry] = []
            for entry in self._entries:
                if entry.plugin_id == plugin_id:
                    removed.append(entry)
                else:
                    kept.append(entry)
            self._entries = kept
        return removed

    # -- reading -------------------------------------------------------

    def list_entries(self) -> list[PackMigrationEntry]:
        """Snapshot of all entries (registration order)."""
        return list(self._entries)

    def _edges_for(self, pack_id: str) -> dict[tuple[int, int], PackMigrationEntry]:
        """Edges applicable to ``pack_id`` keyed by ``from_tuple``.

        Pack-scoped edges (matching ``pack_id``) take precedence over
        wildcard edges (empty ``pack_id``) when both exist for the same
        starting version.
        """
        out: dict[tuple[int, int], PackMigrationEntry] = {}
        # First fill wildcards, then let pack-specific overwrite.
        for entry in self._entries:
            if entry.pack_id == "":
                out.setdefault(entry.from_tuple, entry)
        for entry in self._entries:
            if pack_id and entry.pack_id == pack_id:
                out[entry.from_tuple] = entry
        return out

    def chain(self, *, from_version: str, to_version: str,
              pack_id: str) -> list[PackMigrationEntry]:
        """Return the ordered migrations from ``from_version`` to
        ``to_version`` for ``pack_id``.

        Walks edges by matching each step's ``from_tuple`` to the current
        version. Raises :class:`SavePackSchemaError` if a step has no edge
        before reaching the target. Returns ``[]`` when already current.
        """
        start = _parse_version(from_version)
        target = _parse_version(to_version)
        if start >= target:
            return []
        edges = self._edges_for(pack_id)
        out: list[PackMigrationEntry] = []
        cur = start
        # Guard against pathological cycles: a migration whose to <= from.
        seen: set[tuple[int, int]] = set()
        while cur < target:
            if cur in seen:
                raise SavePackSchemaError(
                    f"pack migration cycle detected at {_format_version(cur)}"
                )
            seen.add(cur)
            entry = edges.get(cur)
            if entry is None:
                raise SavePackSchemaError(
                    f"no pack migration from {_format_version(cur)} "
                    f"to {_format_version(target)}"
                )
            out.append(entry)
            nxt = entry.to_tuple
            if nxt <= cur:
                raise SavePackSchemaError(
                    f"pack migration {entry.from_version} -> "
                    f"{entry.to_version} does not advance the version"
                )
            cur = nxt
        return out


# Module singleton — the "global" pack migration registry. Decorators in
# plugins/registry.py register into this; tests snapshot/restore it.
PACK_MIGRATIONS = PackMigrationRegistry()


# ---------- the load-time gate ----------------------------------------------

def check_and_migrate_pack(
    data: dict,
    *,
    current_pack_id: str,
    current_pack_version: str,
    registry: PackMigrationRegistry = PACK_MIGRATIONS,
) -> dict:
    """Verify pack identity + migrate the save's content version.

    Steps (mirrors :meth:`SaveManager._migrate`):

    1. **Identity.** If both ``data["_pack_id"]`` and ``current_pack_id``
       are non-empty and differ, raise :class:`SavePackMismatchError`. An
       empty save id (legacy save written before identity tracking) skips
       the check and is treated as compatible.
    2. **Version.** Compare ``major.minor`` tuples:
       - save > current  -> :class:`SavePackSchemaError` (can't downgrade);
       - save < current  -> walk the registry chain, applying each
         migration fn in order (missing edge -> ``SavePackSchemaError``);
       - equal           -> no-op.

    Returns the (possibly transformed) ``data`` with ``_pack_format_version``
    set to ``current_pack_version``.
    """
    save_pack_id = str(data.get("_pack_id", "") or "")
    cur_pack_id = str(current_pack_id or "")
    if save_pack_id and cur_pack_id and save_pack_id != cur_pack_id:
        raise SavePackMismatchError(
            f"此存檔來自 pack '{save_pack_id}'，"
            f"與目前載入的 pack '{cur_pack_id}' 不符。"
        )

    save_ver_raw = str(data.get("_pack_format_version", "0") or "0")
    save_ver = _parse_version(save_ver_raw)
    cur_ver = _parse_version(current_pack_version)

    if save_ver > cur_ver:
        raise SavePackSchemaError(
            f"此存檔的 pack 版本為 {_format_version(save_ver)}，"
            f"高於目前 pack 版本 {_format_version(cur_ver)}，無法載入。"
        )

    if save_ver < cur_ver:
        steps = registry.chain(
            from_version=save_ver_raw,
            to_version=current_pack_version,
            pack_id=cur_pack_id,
        )
        for entry in steps:
            result = entry.fn(data)
            if isinstance(result, dict):
                data = result

    data["_pack_format_version"] = _format_version(cur_ver)
    return data
