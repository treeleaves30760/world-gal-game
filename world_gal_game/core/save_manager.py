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
import math
import os
import shutil
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

class SaveSerializationError(SaveError):
    """Save metadata contains a value that cannot round-trip through JSON."""


# ---------- schema version & migrations --------------------------------------

CURRENT_SCHEMA_VERSION = 1
MAX_SAVE_JSON_BYTES = 32 * 1024 * 1024

# JSON documents that intentionally share the save directory but are not
# playable slots.  Filename exclusion is kept in addition to structural
# validation so a damaged or hand-edited clear-data document can never become
# a load-menu row merely because it happens to resemble a GameState.
_RESERVED_JSON_STEMS = frozenset({"clear_data"})


def _looks_like_save_payload(data: Any) -> bool:
    """Return whether ``data`` has the shape of a formal save slot.

    Modern saves have the versioned envelope written by :meth:`save`.  The
    legacy branch intentionally accepts both the original unversioned envelope
    and a complete raw ``GameState`` dump, so v0 files remain discoverable and
    can still pass through the existing migration path.
    """
    if not isinstance(data, dict):
        return False
    player = data.get("player")
    meta = data.get("meta")
    if not isinstance(player, dict) or not isinstance(meta, dict):
        return False
    if "name" in player and not isinstance(player["name"], str):
        return False

    envelope_keys = ("_saved_at", "_label", "_summary")
    if "_schema_version" in data:
        version = data["_schema_version"]
        if isinstance(version, bool) or not isinstance(version, int):
            return False
        return all(isinstance(data.get(key), str) for key in envelope_keys)

    # The pre-schema SaveManager already wrote these three fields.  Also
    # support raw historical GameState dumps, whose core subsystem fields have
    # been stable since the first engine baseline.
    if all(isinstance(data.get(key), str) for key in envelope_keys):
        return True
    legacy_core = {"events", "map", "story", "time"}
    return legacy_core.issubset(data)


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


def _validate_json_safe(value: Any, *, path: str = "_resume",
                        active: set[int] | None = None) -> None:
    """Reject values that would not round-trip through JSON unchanged.

    ``json.dump(default=_json_default)`` intentionally tolerates engine-state
    sets and paths. Resume metadata is a stricter application/UI contract:
    silently stringifying an opaque object would make an apparently successful
    save impossible to resume. Only native JSON values, string-keyed mappings,
    and finite numbers are accepted here.
    """
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SaveSerializationError(
                f"resume_context value at {path} must be finite."
            )
        return

    active = active if active is not None else set()
    if isinstance(value, list):
        marker = id(value)
        if marker in active:
            raise SaveSerializationError(
                f"resume_context contains a cycle at {path}."
            )
        active.add(marker)
        try:
            for index, item in enumerate(value):
                _validate_json_safe(
                    item, path=f"{path}[{index}]", active=active,
                )
        finally:
            active.remove(marker)
        return

    if isinstance(value, dict):
        marker = id(value)
        if marker in active:
            raise SaveSerializationError(
                f"resume_context contains a cycle at {path}."
            )
        active.add(marker)
        try:
            for key, item in value.items():
                if not isinstance(key, str):
                    raise SaveSerializationError(
                        f"resume_context key at {path} must be a string."
                    )
                _validate_json_safe(
                    item, path=f"{path}.{key}", active=active,
                )
        finally:
            active.remove(marker)
        return

    raise SaveSerializationError(
        f"resume_context value at {path} is not JSON-safe: "
        f"{type(value).__name__}."
    )


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
        # A delete commits by moving the live JSON to a hidden tombstone only
        # after its thumbnail has been staged.  Recover the one possible
        # pre-commit crash shape (live JSON + staged thumbnail) eagerly; a
        # post-commit tombstone is merely deferred cleanup and stays deleted.
        self._recover_interrupted_deletes()

    # -- internal helpers -----------------------------------------------------

    def _json_path(self, slot: str) -> Path:
        return self.save_dir / f"{slot}.json"

    def _thumb_path(self, slot: str) -> Path:
        return self.save_dir / f"{slot}.png"

    @staticmethod
    def _delete_tombstone(path: Path) -> Path:
        # Keep the original suffix before ``.delete`` so ``glob('*.json')``
        # and ``glob('*.png')`` never expose a staged deletion as a live slot.
        return path.with_name(f".{path.name}.delete")

    @staticmethod
    def _unlink_quietly(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _restore_staged_file(staged: Path, live: Path) -> bool:
        """Best-effort rollback for a file moved into a delete tombstone."""
        if not staged.exists():
            return True
        try:
            os.replace(staged, live)
            return True
        except OSError:
            # Some filesystems reject replace during error recovery.  A copy
            # fallback still restores the player-visible file while retaining
            # the tombstone until the copy is known to be complete.
            try:
                shutil.copyfile(staged, live)
                staged.unlink(missing_ok=True)
                return True
            except OSError:
                return False

    def _recover_delete_transaction(self, slot: str) -> None:
        path = self._json_path(slot)
        thumb = self._thumb_path(slot)
        staged_json = self._delete_tombstone(path)
        staged_thumb = self._delete_tombstone(thumb)

        if path.exists():
            # JSON is the commit point.  If it is still live, a staged
            # thumbnail came from an interrupted *pre-commit* delete and must
            # be put back so the save remains a matching pair.
            if staged_thumb.exists() and not thumb.exists():
                self._restore_staged_file(staged_thumb, thumb)
            else:
                self._unlink_quietly(staged_thumb)
            self._unlink_quietly(staged_json)
            return

        # No live JSON means deletion reached its commit point.  Hidden files
        # are cleanup debris, not a slot to resurrect on the next launch.
        self._unlink_quietly(staged_json)
        self._unlink_quietly(staged_thumb)

    def _recover_interrupted_deletes(self) -> None:
        slots: set[str] = set()
        for suffix in (".json.delete", ".png.delete"):
            for candidate in self.save_dir.glob(f".*{suffix}"):
                name = candidate.name
                if name.startswith(".") and name.endswith(suffix):
                    slots.add(name[1:-len(suffix)])
        for slot in slots:
            self._recover_delete_transaction(slot)

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
                if p.stem in _RESERVED_JSON_STEMS:
                    continue
                if p.stat().st_size > MAX_SAVE_JSON_BYTES:
                    continue
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)
                if not _looks_like_save_payload(data):
                    continue
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
        resume_context: dict[str, Any] | None = None,
    ) -> Path:
        """Persist state_dict to <slot>.json and optionally a sibling <slot>.png.

        thumbnail is accepted as an optional pygame.Surface; when provided it
        is smoothscaled to 320x180 and saved as PNG.  We deliberately avoid
        importing pygame at the top of this module so the manager can be used
        (and tested) without a display.

        ``resume_context`` is optional JSON-safe application metadata used to
        reconstruct the active presentation after loading. When supplied it is
        stored verbatim under the top-level ``_resume`` key; omitting it keeps
        legacy save files unchanged.
        """
        if resume_context is not None:
            # Validate before creating a thumbnail/temp file or rotating an
            # existing slot, so a bad context cannot damage the last good save.
            _validate_json_safe(resume_context)

        payload = dict(state_dict)
        # _resume is envelope metadata owned by the explicit argument, never by
        # the state model. This also prevents a stale loaded envelope from being
        # carried into a new save unless the caller intentionally passes it.
        payload.pop("_resume", None)
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
        if resume_context is not None:
            payload["_resume"] = resume_context

        path = self._json_path(slot)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        bak_path = path.with_suffix(path.suffix + ".bak")
        bak_tmp_path = path.with_suffix(path.suffix + ".bak.tmp")
        final_thumb_path = self._thumb_path(slot)
        thumb_tmp_path = final_thumb_path.with_name(
            f".{final_thumb_path.stem}.tmp{final_thumb_path.suffix}"
        )
        thumb_rollback_path = final_thumb_path.with_name(
            f".{final_thumb_path.stem}.rollback{final_thumb_path.suffix}"
        )

        def _unlink_quietly(candidate: Path) -> None:
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass

        # Render a requested thumbnail into an independent PNG first. A
        # renderer/codec failure degrades this save to "no thumbnail"; it must
        # never prevent the JSON state from being persisted.
        staged_thumbnail = False
        if thumbnail is not None:
            try:
                import pygame  # local import to keep the module display-free
                THUMB_W, THUMB_H = 320, 180
                scaled = pygame.transform.smoothscale(thumbnail, (THUMB_W, THUMB_H))
                pygame.image.save(scaled, str(thumb_tmp_path))
                staged_thumbnail = thumb_tmp_path.exists()
            except Exception:
                _unlink_quietly(thumb_tmp_path)

        payload["_thumbnail_path"] = (
            str(final_thumb_path) if staged_thumbnail else None
        )

        # Fully serialize and sync the JSON before changing either live file.
        # A serialization/write failure therefore leaves the previous JSON and
        # thumbnail pair untouched.
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2,
                          default=_json_default)
                f.flush()
                if os.fstat(f.fileno()).st_size > MAX_SAVE_JSON_BYTES:
                    raise SaveSerializationError(
                        "Save payload exceeds the 32 MiB safety limit."
                    )
                try:
                    os.fsync(f.fileno())
                except (OSError, AttributeError):
                    pass   # unsupported (e.g. emscripten) — best effort
        except Exception:
            _unlink_quietly(tmp_path)
            _unlink_quietly(thumb_tmp_path)
            raise

        # Keep the previous JSON one-deep without moving the live slot out of
        # the way. Consequently, a failed final replace cannot strand the slot
        # with only a .bak file. Backup rotation remains best-effort, matching
        # the historical save contract.
        if path.exists():
            try:
                shutil.copyfile(path, bak_tmp_path)
                os.replace(bak_tmp_path, bak_path)
            except OSError:
                _unlink_quietly(bak_tmp_path)

        # There is no portable atomic primitive spanning a JSON and a PNG.
        # Commit the staged thumbnail first, retain a private rollback copy,
        # then atomically replace the JSON. If that last replace fails, restore
        # the previous thumbnail while the previous live JSON is still intact.
        had_previous_thumbnail = final_thumb_path.exists()
        thumbnail_backed_up = False
        try:
            if had_previous_thumbnail:
                shutil.copyfile(final_thumb_path, thumb_rollback_path)
                thumbnail_backed_up = True

            if staged_thumbnail:
                os.replace(thumb_tmp_path, final_thumb_path)
            else:
                # Saving without a thumbnail intentionally clears stale art
                # from an earlier generation of this slot.
                final_thumb_path.unlink(missing_ok=True)

            os.replace(tmp_path, path)
        except Exception:
            # Direct os.replace(tmp_path, path) leaves the old JSON untouched
            # when it fails. Restore the matching old thumbnail as well.
            thumbnail_restored = False
            try:
                if thumbnail_backed_up:
                    os.replace(thumb_rollback_path, final_thumb_path)
                    thumbnail_restored = True
                elif not had_previous_thumbnail:
                    final_thumb_path.unlink(missing_ok=True)
                    thumbnail_restored = True
                else:
                    # Backup creation failed before the live thumbnail could
                    # be changed, so the previous file is already intact.
                    thumbnail_restored = True
            except OSError:
                # Last-chance copy keeps the rollback artifact recoverable if
                # the platform rejects replace during error handling.
                if thumbnail_backed_up:
                    try:
                        shutil.copyfile(thumb_rollback_path, final_thumb_path)
                        thumbnail_restored = True
                        _unlink_quietly(thumb_rollback_path)
                    except OSError:
                        pass
            _unlink_quietly(tmp_path)
            _unlink_quietly(thumb_tmp_path)
            if thumbnail_restored:
                _unlink_quietly(thumb_rollback_path)
            raise

        _unlink_quietly(bak_tmp_path)
        _unlink_quietly(thumb_tmp_path)
        _unlink_quietly(thumb_rollback_path)
        # On the web, push the freshly-written file from the in-memory IDBFS
        # cache (including a thumbnail addition/removal) down to IndexedDB so
        # it survives a hard reload. No-op on desktop / headless.
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

        def _read_candidate(candidate: Path) -> dict[str, Any]:
            if candidate.stat().st_size > MAX_SAVE_JSON_BYTES:
                raise SaveCorruptedError(
                    f"Save slot '{slot}' exceeds the 32 MiB safety limit."
                )
            with open(candidate, encoding="utf-8") as stream:
                return json.load(stream)

        try:
            data = _read_candidate(path)
        except (json.JSONDecodeError, SaveCorruptedError) as exc:
            # The live file is damaged (e.g. an interrupted pre-atomic write).
            # Fall back to the last-good ``.bak`` the atomic save left behind
            # rather than losing the slot outright.
            bak = path.with_suffix(path.suffix + ".bak")
            if bak.exists():
                try:
                    return self._migrate(_read_candidate(bak))
                except (json.JSONDecodeError, OSError, SaveCorruptedError):
                    pass
            raise SaveCorruptedError(
                f"Save slot '{slot}' is unsafe or contains invalid JSON: {exc}"
            ) from exc
        return self._migrate(data)

    def delete(self, slot: str) -> bool:
        path = self._json_path(slot)
        thumb = self._thumb_path(slot)
        staged_json = self._delete_tombstone(path)
        staged_thumb = self._delete_tombstone(thumb)

        # Resolve debris from a process interruption before deciding whether
        # this call has a live slot to delete.
        self._recover_delete_transaction(slot)
        if not path.exists():
            return False

        thumb_staged = False
        try:
            # Stage the thumbnail first.  The JSON remains discoverable until
            # the final replace below, so any failure can restore a complete
            # player-visible slot without rebuilding metadata.
            if thumb.exists():
                os.replace(thumb, staged_thumb)
                thumb_staged = True
            # Moving the JSON is the transaction's commit point.  Both
            # tombstones are outside list_saves()'s live-file globs.
            os.replace(path, staged_json)
        except Exception as exc:
            if thumb_staged and not self._restore_staged_file(
                staged_thumb,
                thumb,
            ):
                raise SaveError(
                    f"Delete of save slot '{slot}' failed and its thumbnail "
                    "could not be restored."
                ) from exc
            raise

        # The live JSON is gone, so deletion is committed.  Cleanup is
        # deliberately best-effort: an antivirus lock on a backup/tombstone
        # must not turn a completed logical deletion into a reported failure.
        for sibling in (
            staged_json,
            staged_thumb,
            path.with_suffix(path.suffix + ".bak"),
            path.with_suffix(path.suffix + ".tmp"),
            path.with_suffix(path.suffix + ".bak.tmp"),
            thumb.with_name(f".{thumb.stem}.tmp{thumb.suffix}"),
            thumb.with_name(f".{thumb.stem}.rollback{thumb.suffix}"),
        ):
            self._unlink_quietly(sibling)
        # Mirror the save path: persist the deletion to IndexedDB on the web
        # so a hard reload doesn't resurrect the slot. No-op off-web.
        _flush_web_storage()
        return True
