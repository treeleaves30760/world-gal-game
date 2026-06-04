"""Save / load to JSON with versioned schema, migration, and thumbnail support.

Design choices:
- _schema_version lives in the file so we can evolve the format without
  breaking existing saves (see MIGRATIONS).
- Thumbnail is kept as a sibling PNG to avoid bloating the JSON.
- The exception hierarchy lets callers handle failure cases distinctly
  without catching bare Exception.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# ---------- exception hierarchy ----------------------------------------------

class SaveError(Exception):
    """Base for all save/load failures."""

class SaveNotFoundError(SaveError):
    """Requested slot does not exist on disk."""

class SaveCorruptedError(SaveError):
    """File exists but cannot be parsed as valid JSON."""

class SaveSchemaError(SaveError):
    """File has a schema_version we don't know how to handle."""


# ---------- schema version & migrations --------------------------------------

CURRENT_SCHEMA_VERSION = 1


def _json_default(obj):
    """Make json.dump tolerate the types our state actually contains.

    Pydantic state ships with bare Python sets (story.played, map.visited,
    affection.*.unlocked, achievements.seen, read_log.*). Without this
    helper they'd hit `default=str` and serialize to repr like
    `"{'a','b'}"` — which round-trips back as a string, not a set.
    """
    if isinstance(obj, (set, frozenset)):
        # Sort string sets for deterministic diffs; fall back to list().
        try:
            return sorted(obj)
        except TypeError:
            return list(obj)
    if hasattr(obj, "__fspath__"):
        return str(obj)
    return str(obj)


def _migrate_0_to_1(data: dict) -> dict:
    """Bring a pre-versioned save up to v1.

    Old files simply had no _schema_version key.  Everything else is kept
    as-is so field-level changes can be layered on top in future migrations.
    """
    data["_schema_version"] = 1
    return data


def _flush_web_storage() -> None:
    """Ask the web runtime to persist saves to IndexedDB (no-op off-web).

    Imported lazily so this module stays display- and platform-free at
    import time, and wrapped so a flush failure can never abort a save —
    :func:`world_gal_game.platform_web.flush_storage` is already guarded,
    but the import itself is shielded too for total safety.
    """
    try:
        from ..platform_web import flush_storage
        flush_storage()
    except Exception:
        # Persisting to IndexedDB is best-effort; never let it break a save.
        pass


# ---------- manager ----------------------------------------------------------

class SaveManager:
    MIGRATIONS: dict[int, Callable[[dict], dict]] = {
        0: _migrate_0_to_1,
    }

    def __init__(self, save_dir: Path):
        self.save_dir = Path(save_dir)
        self.save_dir.mkdir(parents=True, exist_ok=True)

    # -- internal helpers -----------------------------------------------------

    def _json_path(self, slot: str) -> Path:
        return self.save_dir / f"{slot}.json"

    def _thumb_path(self, slot: str) -> Path:
        return self.save_dir / f"{slot}.png"

    def _migrate(self, data: dict) -> dict:
        """Run all pending migrations in order until we reach CURRENT_SCHEMA_VERSION."""
        version: int = data.get("_schema_version", 0)
        if version > CURRENT_SCHEMA_VERSION:
            raise SaveSchemaError(
                f"Save has schema version {version}, "
                f"but engine only supports up to {CURRENT_SCHEMA_VERSION}."
            )
        while version < CURRENT_SCHEMA_VERSION:
            migrate_fn = self.MIGRATIONS.get(version)
            if migrate_fn is None:
                raise SaveSchemaError(
                    f"No migration from schema version {version}."
                )
            data = migrate_fn(data)
            version = data.get("_schema_version", version + 1)
        return data

    # -- public API -----------------------------------------------------------

    def list_saves(self) -> list[dict[str, Any]]:
        """Return metadata for all valid slots, newest-first by mtime."""
        out: list[dict[str, Any]] = []
        for p in sorted(
            self.save_dir.glob("*.json"),
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        ):
            try:
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)
                slot = p.stem
                thumb_path = str(self._thumb_path(slot))
                out.append({
                    "slot": slot,
                    "path": str(p),
                    "saved_at": data.get("_saved_at"),
                    "summary": data.get("_summary", ""),
                    "label": data.get("_label", slot),
                    "thumbnail_path": thumb_path if Path(thumb_path).exists() else None,
                    "pack_id": data.get("_pack_id", ""),
                    "pack_format_version": data.get("_pack_format_version", ""),
                    "engine_version": data.get("_engine_version", ""),
                })
            except Exception:
                continue
        return out

    def save(
        self,
        slot: str,
        state_dict: dict[str, Any],
        *,
        label: str = "",
        summary: str = "",
        thumbnail=None,  # pygame.Surface | None  (avoid hard import at module level)
        pack_meta: dict[str, Any] | None = None,
    ) -> Path:
        """Persist state_dict to <slot>.json and optionally a sibling <slot>.png.

        thumbnail is accepted as an optional pygame.Surface; when provided it
        is smoothscaled to 320x180 and saved as PNG.  We deliberately avoid
        importing pygame at the top of this module so the manager can be used
        (and tested) without a display.
        """
        payload = dict(state_dict)
        # Strip internal bridge objects that must not survive serialization.
        if "meta" in payload and isinstance(payload["meta"], dict):
            payload["meta"] = {
                k: v for k, v in payload["meta"].items()
                if not k.startswith("__")
            }

        payload["_schema_version"] = CURRENT_SCHEMA_VERSION
        payload["_saved_at"] = datetime.now(timezone.utc).isoformat()
        payload["_label"] = label or slot
        payload["_summary"] = summary
        # Pack identity: which pack + content version + engine produced this
        # save. Used on load to detect a mismatched pack or run pack-level
        # content migrations. Empty defaults keep legacy saves valid.
        pm = pack_meta or {}
        payload["_pack_id"] = str(pm.get("pack_id", ""))
        payload["_pack_format_version"] = str(pm.get("pack_format_version", "0"))
        payload["_engine_version"] = str(pm.get("engine_version", ""))

        # Save thumbnail first so we can record its path in the JSON.
        thumb_path: str | None = None
        if thumbnail is not None:
            try:
                import pygame  # local import to keep the module display-free
                THUMB_W, THUMB_H = 320, 180
                scaled = pygame.transform.smoothscale(thumbnail, (THUMB_W, THUMB_H))
                tp = self._thumb_path(slot)
                pygame.image.save(scaled, str(tp))
                thumb_path = str(tp)
            except Exception:
                # Thumbnail failure must never abort the save itself.
                thumb_path = None

        payload["_thumbnail_path"] = thumb_path

        path = self._json_path(slot)
        # Atomic write: serialize to a temp file, flush+fsync, then os.replace()
        # it over the slot (atomic on POSIX + NTFS). A crash / power loss / the
        # per-frame autosave firing mid-write can no longer truncate a slot to
        # corrupt JSON. The previous good file is kept one-deep as ``.bak`` so
        # load() can fall back if the live file is ever damaged.
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2,
                      default=_json_default)
            f.flush()
            try:
                os.fsync(f.fileno())
            except (OSError, AttributeError):
                pass   # fsync unsupported (e.g. emscripten) — best effort
        if path.exists():
            try:
                os.replace(path, path.with_suffix(path.suffix + ".bak"))
            except OSError:
                pass
        os.replace(tmp_path, path)
        # On the web, push the freshly-written file from the in-memory IDBFS
        # cache down to IndexedDB so it survives a hard reload. No-op on
        # desktop / headless.
        _flush_web_storage()
        return path

    def load(self, slot: str) -> dict[str, Any]:
        """Load and return the state dict for slot.

        Raises:
            SaveNotFoundError   - file does not exist
            SaveCorruptedError  - JSON parse failure
            SaveSchemaError     - unknown or future schema version
        """
        path = self._json_path(slot)
        if not path.exists():
            raise SaveNotFoundError(f"Save slot '{slot}' not found.")
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            # The live file is damaged (e.g. an interrupted pre-atomic write).
            # Fall back to the last-good ``.bak`` the atomic save left behind
            # rather than losing the slot outright.
            bak = path.with_suffix(path.suffix + ".bak")
            if bak.exists():
                try:
                    with open(bak, encoding="utf-8") as f:
                        return self._migrate(json.load(f))
                except (json.JSONDecodeError, OSError):
                    pass
            raise SaveCorruptedError(
                f"Save slot '{slot}' contains invalid JSON: {exc}"
            ) from exc
        return self._migrate(data)

    def delete(self, slot: str) -> bool:
        path = self._json_path(slot)
        # Clean up the atomic-write siblings (.bak / .tmp) alongside the slot.
        for sib in (path.with_suffix(path.suffix + ".bak"),
                    path.with_suffix(path.suffix + ".tmp")):
            try:
                sib.unlink(missing_ok=True)
            except OSError:
                pass
        if path.exists():
            path.unlink()
            thumb = self._thumb_path(slot)
            if thumb.exists():
                thumb.unlink()
            # Mirror the save path: persist the deletion to IndexedDB on the
            # web so a hard reload doesn't resurrect the slot. No-op off-web.
            _flush_web_storage()
            return True
        return False
