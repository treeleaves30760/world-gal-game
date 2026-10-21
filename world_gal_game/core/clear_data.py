"""Cross-playthrough "clear data" (New Game+).

The union of everything the player has ever seen — scenes and exact dialogue
lines read, endings reached, CGs and music unlocked, routes cleared — persisted
SEPARATELY from save slots (in its own ``clear_data.json``) so it survives a
fresh start. Powers:

- **Skip-already-read on a new game:** a new playthrough's read-log is seeded
  from ``lines_seen``, so skip-read works immediately on the exact content the
  player has seen in any prior run (the Key/Yuzusoft completion loop).
- **After-story gating:** parked on ``state.meta["__clear_data__"]`` so packs
  can gate bonus content on a prior clear (see the ``cleared_ending`` /
  ``cleared_route`` conditions).

This is global, not per-save, so it is intentionally absent from the save schema.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Collection, Literal

from pydantic import (
    BaseModel,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from .cg_gallery import canonical_cg_path
from .music_room import canonical_music_path
from .read_log import ReadLog, ReplayArchiveEntry


_MAX_CLEAR_DATA_BYTES = 32 * 1024 * 1024
_LINE_IDENTITY_SCHEMA_VERSION = 1


class ClearData(BaseModel):
    scenes_seen: set[str] = Field(default_factory=set)
    # Version 1 stores the exact opaque keys used by ReadLog: stable authored
    # lines are ``scene_id:@loc_id`` and legacy lines remain
    # ``scene_id:line_index``. ``None`` is intentionally distinct from version
    # 1 with an empty set: it identifies a pre-line-identity clear-data file
    # that still needs one compatibility snapshot of its completed scenes.
    line_identity_version: Literal[1] | None = 1
    lines_seen: set[str] = Field(default_factory=set)
    endings_seen: set[str] = Field(default_factory=set)
    cgs_seen: set[str] = Field(default_factory=set)
    music_seen: set[str] = Field(default_factory=set)
    cleared_routes: set[str] = Field(default_factory=set)
    # Commercial Extras are cross-playthrough. Preserve the first complete
    # evaluated trace globally so New Game / an older slot cannot silently
    # replace a memory with a later branch.
    replay_archive: dict[str, ReplayArchiveEntry] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _recognize_scene_only_compatibility_shape(cls, value):
        """Preserve the public scene-only constructor as legacy evidence.

        ``ClearData()`` is a new v1 store and records exact live line keys. A
        caller that explicitly supplies ``scenes_seen`` but neither identity
        field is expressing the pre-v1 scene-summary shape, just like an older
        JSON file, so it receives the same one-time freeze on first apply.
        """
        if (
            isinstance(value, dict)
            and "scenes_seen" in value
            and "lines_seen" not in value
            and "line_identity_version" not in value
        ):
            value = dict(value)
            value["line_identity_version"] = None
        return value

    @field_validator("cgs_seen", mode="before")
    @classmethod
    def _canonicalize_cg_paths(cls, value):
        """Drop phantom keys produced by legacy blank ``show_cg`` targets."""
        if not isinstance(value, (list, set, tuple, frozenset)):
            return value
        return {
            canonical_cg_path(path) if isinstance(path, str) else path
            for path in value
            if not isinstance(path, str) or canonical_cg_path(path)
        }

    @field_validator("music_seen", mode="before")
    @classmethod
    def _canonicalize_music_paths(cls, value):
        """Repair aliases and discard blank keys from older integrations."""
        if not isinstance(value, (list, set, tuple, frozenset)):
            return value
        return {
            canonical_music_path(path) if isinstance(path, str) else path
            for path in value
            if not isinstance(path, str) or canonical_music_path(path)
        }

    @field_validator("replay_archive", mode="before")
    @classmethod
    def _validate_replay_archive(cls, value):
        if value is None:
            return {}
        # Reuse ReadLog's version, grammar and global resource budgets.
        return ReadLog.model_validate({"replay_archive": value}).replay_archive

    @field_serializer(
        "scenes_seen",
        "lines_seen",
        "endings_seen",
        "cgs_seen",
        "music_seen",
        "cleared_routes",
    )
    def _ser_set(self, v: set[str]) -> list[str]:
        return sorted(v)

    @property
    def is_cleared(self) -> bool:
        """True once the player has reached at least one ending (NG+ available)."""
        return bool(self.endings_seen)

    # ---- persistence (robust by contract; never raises) ----------------
    @classmethod
    def load(cls, path: Path) -> "ClearData":
        try:
            if path.exists() and path.stat().st_size <= _MAX_CLEAR_DATA_BYTES:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    return cls()
                # Only an on-disk payload that predates ``lines_seen`` needs
                # scene-level compatibility expansion. A brand-new ClearData
                # starts at v1 so record_from_state() preserves exact partial /
                # conditional-line evidence instead of inferring whole scenes.
                if "lines_seen" not in raw:
                    raw["line_identity_version"] = None
                elif "line_identity_version" not in raw:
                    # Adopt the short-lived unversioned exact-key shape without
                    # broadening it from scenes_seen.
                    raw["line_identity_version"] = _LINE_IDENTITY_SCHEMA_VERSION
                return cls(**raw)
        except (OSError, ValueError, TypeError):
            pass
        return cls()

    def save(self, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            payload = self.model_dump_json(indent=2)
            if len(payload.encode("utf-8")) > _MAX_CLEAR_DATA_BYTES:
                return
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, path)        # atomic
        except (OSError, TypeError, ValueError):
            pass

    # ---- update from a live GameState ----------------------------------
    def _freeze_legacy_line_progress(self, state) -> bool:
        """Snapshot exact line keys for a pre-v1 ``clear_data.json`` once.

        Historical clear data knew only that a scene had completed. At the
        first load under the stable-line contract we translate that evidence
        against the *currently authored* scene and then set the schema marker.
        Future releases use only ``lines_seen``; they must never infer that a
        newly inserted line was read merely because its scene was completed in
        an older playthrough.
        """
        if self.line_identity_version is not None:
            return False

        read_log = ReadLog()
        for scene_id in self.scenes_seen:
            scene = getattr(getattr(state, "story", None), "scenes", {}).get(
                scene_id,
            )
            if scene is None:
                continue
            for line_index, line in enumerate(scene.lines):
                read_log.mark_line(
                    scene_id,
                    line_index,
                    loc_id=line.loc_id,
                )
        self.lines_seen.update(read_log.lines)
        self.line_identity_version = _LINE_IDENTITY_SCHEMA_VERSION
        return True

    def record_from_state(
        self,
        state,
        *,
        authored_cg_ids: Collection[str] | None = None,
        authored_music_ids: Collection[str] | None = None,
    ) -> bool:
        """Merge everything the given state has seen.

        The optional authored id collections are the current pack's trusted
        decoder rosters. When supplied, both persisted clear data and incoming
        live unlocks are intersected with them before recording. This prevents
        an older or user-edited save from laundering a decoder path into future
        playthroughs through ``clear_data.json``. Omitting either collection
        preserves the historical union behaviour for third-party callers.

        Returns ``True`` if anything was added or unauthorised CG progress was
        pruned, allowing the caller to skip a redundant save.
        """
        before_cgs = set(self.cgs_seen)
        before_music = set(self.music_seen)
        before = (
            len(self.scenes_seen),
            self.line_identity_version,
            len(self.lines_seen),
            len(self.endings_seen),
            len(self.cgs_seen),
            len(self.music_seen),
            len(self.cleared_routes),
            len(self.replay_archive),
        )
        try:
            self.scenes_seen |= set(getattr(state.read_log, "scenes", set())
                                    or set())
        except Exception:
            pass
        # A legacy clear-data file may reach this path before apply_to_state()
        # in embedding clients. Freeze its scene-level evidence only once, then
        # merge every live line key, including partial scenes and legacy lines.
        try:
            self._freeze_legacy_line_progress(state)
        except Exception:
            # Clear-data recording is deliberately best-effort. A malformed
            # third-party story must not block endings/gallery persistence.
            pass
        try:
            live_lines = set(getattr(state.read_log, "lines", set()) or set())
            self.lines_seen |= {
                key for key in live_lines if isinstance(key, str)
            }
            if self.line_identity_version is None:
                self.line_identity_version = _LINE_IDENTITY_SCHEMA_VERSION
        except Exception:
            pass
        try:
            merged_archive = {
                scene_id: entry.model_copy(deep=True)
                for scene_id, entry in self.replay_archive.items()
            }
            for scene_id in state.read_log.replayable_scene_ids():
                entry = state.read_log.replay_entry(scene_id)
                if entry is not None and scene_id not in merged_archive:
                    merged_archive[scene_id] = entry.model_copy(deep=True)
            self.replay_archive = ReadLog.model_validate({
                "replay_archive": merged_archive,
            }).replay_archive
        except Exception:
            pass
        try:
            unlocked = getattr(state.endings, "unlocked", {}) or {}
            self.endings_seen |= set(unlocked.keys())
            getter = getattr(state.endings, "get", None)
            if callable(getter):
                for eid in unlocked:
                    e = getter(eid)
                    rid = getattr(e, "route_id", None) if e else None
                    if rid:
                        self.cleared_routes.add(rid)
        except Exception:
            pass
        try:
            unlocked = getattr(state.cg_gallery, "unlocked", set()) or set()
            if authored_cg_ids is not None:
                from .media_authority import filter_authorized_cg_ids

                self.cgs_seen = filter_authorized_cg_ids(
                    self.cgs_seen,
                    authored_cg_ids,
                )
                self.cgs_seen |= filter_authorized_cg_ids(
                    unlocked,
                    authored_cg_ids,
                )
            else:
                for path in unlocked:
                    if isinstance(path, str) and canonical_cg_path(path):
                        self.cgs_seen.add(canonical_cg_path(path))
        except Exception:
            pass
        try:
            unlocked = getattr(state.music_room, "unlocked", set()) or set()
            if authored_music_ids is not None:
                from .media_authority import filter_authorized_music_ids

                self.music_seen = filter_authorized_music_ids(
                    self.music_seen,
                    authored_music_ids,
                )
                self.music_seen |= filter_authorized_music_ids(
                    unlocked,
                    authored_music_ids,
                )
            else:
                for path in unlocked:
                    if isinstance(path, str) and canonical_music_path(path):
                        self.music_seen.add(canonical_music_path(path))
        except Exception:
            pass
        after = (
            len(self.scenes_seen),
            self.line_identity_version,
            len(self.lines_seen),
            len(self.endings_seen),
            len(self.cgs_seen),
            len(self.music_seen),
            len(self.cleared_routes),
            len(self.replay_archive),
        )
        return (
            after != before
            or self.cgs_seen != before_cgs
            or self.music_seen != before_music
        )

    def apply_to_state(
        self,
        state,
        *,
        registered_endings: Collection[str] | None = None,
        authored_cg_ids: Collection[str] | None = None,
        authored_music_ids: Collection[str] | None = None,
    ) -> bool:
        """Merge cross-playthrough progress into a live ``GameState``.

        Save files intentionally carry their own read log, ending tracker, and
        CG gallery, and Music Room. Loading an older save must not make global
        completion progress disappear, so this method unions clear data into
        those live trackers without replacing anything already in the save.

        ``registered_endings`` is the current pack's authoritative ending-id
        set.  Callers that are about to swap in a serialized state should
        capture it *before* the swap and pass it here: an old save may still
        contain ending definitions that the current pack removed.  Clear data
        never resurrects those retired endings.  Backfilled endings are also
        marked seen so opening an old save cannot replay a "new ending"
        notification for progress earned in a different playthrough.

        ``authored_cg_ids`` similarly constrains cross-playthrough CG progress
        to identities authorized by the currently loaded pack.  It is optional
        for source compatibility, but load/new-game entry points should pass
        the authority they built before considering any save payload.

        ``authored_music_ids`` provides the equivalent boundary for heard BGM.

        Returns ``True`` when any live tracker changed.
        """
        read_log = state.read_log
        ending_tracker = state.endings
        cg_gallery = state.cg_gallery
        music_room = state.music_room

        before = (
            set(read_log.scenes),
            set(read_log.lines),
            {
                scene_id: entry.model_dump(mode="json")
                for scene_id, entry in read_log.replay_archive.items()
            },
            dict(ending_tracker.unlocked),
            set(ending_tracker.seen),
            set(cg_gallery.unlocked),
            set(music_room.unlocked),
        )

        # Pre-v1 clear data knew only whole-scene completion. Translate that old
        # evidence once against the installed story, then rely exclusively on
        # exact line keys. This is what prevents content added in a later pack
        # release from inheriting an unrelated scene-level "already read" bit.
        self._freeze_legacy_line_progress(state)

        # Keep scene completion monotonic even when content was later removed;
        # replay UIs already filter against the live story registry.
        for scene_id in self.scenes_seen:
            # Do not call mark_scene_done here: an older mid-scene save may
            # carry an incomplete trace, and global 'seen' evidence is not
            # permission to promote that partial payload to a replay.
            read_log.scenes.add(scene_id)
        read_log.lines.update(self.lines_seen)

        # The global first-completed trace is authoritative over an older save
        # slot. Entries remain inert when their scene was retired; the replay
        # UI also requires a current authored scene before showing a row.
        from ..dialogue.replay_engine import (
            rebind_authored_replay_visual_fx,
        )
        rebound_archive = rebind_authored_replay_visual_fx(
            ReadLog(replay_archive=self.replay_archive),
            state.story,
        ).replay_archive
        # Persist legacy index->loc_id hydration (and fail-closed removals) back
        # into the global source of truth. Copying only into this live state
        # would lose the stable evidence on the next save/reload and allow a
        # later scene reorder to bind the old archive to a different line.
        self.replay_archive = {
            scene_id: entry.model_copy(deep=True)
            for scene_id, entry in rebound_archive.items()
        }
        for scene_id, entry in self.replay_archive.items():
            read_log.replay_archive[scene_id] = entry.model_copy(deep=True)

        cgs_to_apply = self.cgs_seen
        if authored_cg_ids is not None:
            from .media_authority import filter_authorized_cg_ids

            cgs_to_apply = filter_authorized_cg_ids(
                self.cgs_seen,
                authored_cg_ids,
            )
        for path in cgs_to_apply:
            cg_gallery.unlock(path)

        music_to_apply = self.music_seen
        if authored_music_ids is not None:
            from .media_authority import filter_authorized_music_ids

            music_to_apply = filter_authorized_music_ids(
                self.music_seen,
                authored_music_ids,
            )
        for path in music_to_apply:
            music_room.unlock(path)

        if registered_endings is None:
            valid_ending_ids = set(ending_tracker.endings)
        else:
            valid_ending_ids = set(registered_endings)
        restored_endings = self.endings_seen & valid_ending_ids
        for ending_id in restored_endings:
            # ClearData stores completion, not the original wall-clock time.
            # An empty timestamp is preferable to inventing one; the tracker
            # only relies on key membership for unlock semantics.
            ending_tracker.unlocked.setdefault(ending_id, "")
        ending_tracker.seen.update(restored_endings)

        # Keep NG+ conditions wired even when this method is used by a loader
        # outside GalGameApp (for example the formal SaveScene load path).
        state.meta["__clear_data__"] = self

        after = (
            set(read_log.scenes),
            set(read_log.lines),
            {
                scene_id: entry.model_dump(mode="json")
                for scene_id, entry in read_log.replay_archive.items()
            },
            dict(ending_tracker.unlocked),
            set(ending_tracker.seen),
            set(cg_gallery.unlocked),
            set(music_room.unlocked),
        )
        return after != before
