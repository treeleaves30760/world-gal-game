"""VisualCheck — capture + diff screenshots against baselines.

Two responsibilities:

1. **Capture**: render a pack to a particular pre-state (location,
   flags, scene) using the dummy SDL driver, save the resulting
   Surface as PNG. Named ``view`` scenarios build real, representative story,
   travel, system-menu, save/load, settings, galleries, and populated history
   stacks without running a pack's intro underneath an unrelated overlay.
2. **Compare**: byte-for-byte md5 + pixel-by-pixel diff against a
   stored baseline PNG. Development mode creates a missing baseline on the
   first run. ``strict_baselines=True`` instead fails when a baseline is
   absent, so CI/release gates cannot approve their own unreviewed render.

Baselines live under ``<pack_root>/visual_baselines/<scenario>.png``
by default. Generated candidates use the dedicated
``<pack_root>/visual_candidates/visual_check/<scenario>.png`` namespace so a
routine visual check cannot overwrite root-level, source-bound review
candidates produced by a pack's art pipeline. Override either directory via
the constructor; an explicit directory is used exactly as supplied.

Usage::

    from world_gal_game.dev.visual_check import VisualCheck

    vc = VisualCheck("games/demo_pack")
    scenarios = VisualCheck.default_scenarios()
    report = vc.run(scenarios)
    if not report.ok:
        for r in report.results:
            if not r.ok:
                print(r.name, r.detail)
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_log = logging.getLogger("world_gal_game.dev.visual")

_VISUAL_SAVE_TIMESTAMP = "2026-01-01T00:00:00+00:00"


@dataclass
class ComparisonResult:
    name: str
    ok: bool                    # True if baseline matched OR baseline was created
    baseline_path: str
    candidate_path: str
    md5_match: bool = False
    pixel_diff: int = 0          # number of non-matching pixels
    detail: str = ""
    created_baseline: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "ok": self.ok,
            "baseline_path": self.baseline_path,
            "candidate_path": self.candidate_path,
            "md5_match": self.md5_match,
            "pixel_diff": self.pixel_diff,
            "detail": self.detail,
            "created_baseline": self.created_baseline,
        }


@dataclass
class VisualReport:
    pack_root: str
    results: list[ComparisonResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results) and bool(self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack_root": self.pack_root,
            "ok": self.ok,
            "count": len(self.results),
            "passed": sum(1 for r in self.results if r.ok),
            "failed": sum(1 for r in self.results if not r.ok),
            "created": sum(1 for r in self.results if r.created_baseline),
            "results": [r.to_dict() for r in self.results],
        }


class VisualCheck:
    """Render a pack into specific states and diff against baselines.

    The default candidate directory is deliberately namespaced below
    ``visual_candidates``. Pack-owned review pipelines may keep curated,
    source-bound candidates at the root without colliding with the generic
    semantic scenario names used here.
    """

    DEFAULT_CANDIDATE_NAMESPACE = "visual_check"

    def __init__(self, pack_root: Path | str,
                 *, baselines_dir: Path | None = None,
                 candidates_dir: Path | None = None,
                 tolerance: int = 0,
                 strict_baselines: bool = False) -> None:
        self.pack_root = Path(pack_root).resolve()
        self.baselines_dir = (
            Path(baselines_dir).resolve()
            if baselines_dir is not None
            else self.pack_root / "visual_baselines"
        )
        self.candidates_dir = (
            Path(candidates_dir).resolve()
            if candidates_dir is not None
            else (
                self.pack_root
                / "visual_candidates"
                / self.DEFAULT_CANDIDATE_NAMESPACE
            )
        )
        # Allow up to N differing pixels before flagging a regression.
        # Useful for non-deterministic effects (e.g. text antialiasing).
        self.tolerance = max(0, tolerance)
        # Development keeps the historical bootstrap behaviour. Release CI
        # opts into strict mode so a missing, never-reviewed baseline is a
        # blocking failure instead of being silently promoted and accepted.
        self.strict_baselines = bool(strict_baselines)

    # ------------------------------------------------------------------
    # Capture

    def capture(self, *, name: str, view: str | None = None,
                scene_id: str | None = None,
                dev_start: str | None = None,
                dev_location: str | None = None,
                dev_time: str | None = None,
                dev_flags: dict[str, Any] | None = None,
                locale: str | None = None,
                text_expansion_factor: float | None = None,
                screen_size: tuple[int, int] | None = None,
                autoplay: float = 0.6,
                settle_frames: int | None = None,
                include_toasts: bool = False,
                pack_name: str | None = None) -> Path:
        """Render one deterministic scenario and save a PNG.

        ``view`` is the preferred visual-check driver. It accepts the primary
        story screens, the real first-run onboarding, story-priority/all-reachable spatial maps, a real
        travel-confirmation sheet, save/load, settings, CG gallery, Music Room,
        Replay Library, and populated dialogue-history product screens. Dialogue/choice
        automatically select representative authored scenes, or callers can
        pin ``scene_id``. ``dev_start`` remains for backwards-compatible
        custom scenario files. ``locale`` selects one of the pack's declared
        runtime languages without mutating persisted player preferences.
        ``text_expansion_factor`` installs a deterministic display-only pseudo
        policy for layout pressure; it never enters locale state or saves.
        ``screen_size`` makes the logical canvas an explicit part of the
        baseline contract.

        ``autoplay`` is retained as the historical name for settle *game
        seconds*. Frames now advance with a fixed ``1 / fps`` delta instead of
        wall-clock time. ``settle_frames`` can pin the frame count explicitly.
        """
        previous_sdl = {
            "SDL_VIDEODRIVER": os.environ.get("SDL_VIDEODRIVER"),
            "SDL_AUDIODRIVER": os.environ.get("SDL_AUDIODRIVER"),
        }
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        os.environ["SDL_AUDIODRIVER"] = "dummy"

        import pygame
        from world_gal_game.app import GalGameApp
        from world_gal_game.config import EngineConfig
        from world_gal_game.ui.input import InputState

        sandbox = tempfile.TemporaryDirectory(prefix="wgg-visual-check-")
        try:
            logical_size = tuple(screen_size or (1280, 720))
            if (
                len(logical_size) != 2
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or value <= 0
                    for value in logical_size
                )
            ):
                raise ValueError(
                    "visual-check screen_size must contain two positive ints"
                )
            config = EngineConfig(
                screen_size=logical_size,
                locale=locale or "",
                presentation_text_expansion=(
                    1.0
                    if text_expansion_factor is None
                    else float(text_expansion_factor)
                ),
                writable_root_override=Path(sandbox.name),
                # Dev-start scenarios should capture the requested screen, not
                # a first-run controls modal whose presence depends on local
                # settings. The onboarding itself can be a dedicated authored
                # scenario when a pack wants a baseline for it.
                seen_intro=True,
                # GameState.rng()-aware content receives a stable seed during
                # visual regression captures.
                seed=0,
            )
            # Keep the resolved path authoritative. Re-resolving only the final
            # directory name can silently capture a different installed/sibling
            # pack with the same basename, or fail for packs outside search roots.
            # ``pack_name`` is retained only as an API-compatibility argument;
            # the constructor's resolved absolute path is always authoritative.
            _ = pack_name
            pack = str(self.pack_root)
            config.default_pack = pack

            app = GalGameApp(config=config, pack=pack, headless=False)
            app.manager.transitions_enabled = False
            # App startup queues the title scene. Commit it before replacing it
            # with a scenario stack so setup never depends on queue ordering.
            app.manager.commit_pending()
            app.state.meta["__seed__"] = 0

            # Apply pre-state before selecting representative content so
            # authored choice visibility and destination locks see it.
            if dev_flags:
                for k, v in dev_flags.items():
                    app.state.events.set_flag(k, v)
            if dev_time:
                from world_gal_game.core.time_system import TimeOfDay
                try:
                    app.state.time.set_phase(TimeOfDay(dev_time))
                except ValueError:
                    pass
            if dev_location and dev_location in app.state.map.locations:
                app.state.map.move_to(dev_location)
            if view is not None:
                self._prepare_view(app, view, scene_id=scene_id)
            elif dev_start:
                self._apply_dev_start(app, dev_start)
            app.manager.commit_pending()
            if not include_toasts:
                self._silence_notifications(app)

            # Run a fixed number of fixed-delta frames. Reading the real event
            # queue, real mouse position, or wall clock made identical captures
            # differ based on host activity and frame scheduling.
            target_path = self.candidates_dir / f"{name}.png"
            target_path.parent.mkdir(parents=True, exist_ok=True)
            fps = max(1, int(app.config.fps))
            dt = 1.0 / fps
            frame_count = (
                max(1, int(settle_frames))
                if settle_frames is not None
                else max(1, math.ceil(max(0.3, float(autoplay)) * fps))
            )
            pygame.event.clear()
            for _frame in range(frame_count):
                inp = InputState(events=[])
                inp.mouse_pos = (-10_000, -10_000)
                app.manager.update(dt, inp)
                app._poll_achievement_toasts()
                app.toast_stack.update(dt, inp)
                app.manager.draw(app.screen)
                app.toast_stack.draw(app.screen)
            pygame.image.save(app.screen, str(target_path))
            return target_path
        finally:
            pygame.quit()
            sandbox.cleanup()
            for key, previous in previous_sdl.items():
                if previous is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = previous

    @classmethod
    def _prepare_view(cls, app: Any, view: str,
                      *, scene_id: str | None = None) -> None:
        """Build a representative, semantically-correct scene stack.

        This is intentionally independent of ``_start_new_game``: that method
        starts the pack intro, so opening a map/menu immediately afterwards
        produces an overlay on top of dialogue rather than the exploration
        screen the overlay actually belongs to.
        """
        normalized = {
            "explore": "exploration",
            "map": "spatial_map_story_priority",
            "destination": "spatial_map_story_priority",
            "destination_map": "spatial_map_story_priority",
            "spatial_map": "spatial_map_story_priority",
            "map_story": "spatial_map_story_priority",
            "map_all": "spatial_map_all_reachable",
            "destination_all": "spatial_map_all_reachable",
            "travel_confirm": "travel_confirmation",
            "destination_picker": "destination_list",
            "pause_menu": "menu",
            "backlog": "history",
            "scrollback": "history",
            "gallery": "cg_gallery",
            "cg": "cg_gallery",
            "music": "music_room",
            "jukebox": "music_room",
            "replay": "scene_replay",
        }.get(view, view)
        allowed = {
            "title", "onboarding", "dialogue", "choice",
            "exploration",
            "spatial_map_story_priority",
            "spatial_map_all_reachable",
            "travel_confirmation",
            "destination_list",
            "menu", "history", "save", "load", "settings", "cg_gallery",
            "music_room", "scene_replay", "flowchart",
        }
        if normalized not in allowed:
            raise ValueError(
                f"unknown visual-check view {view!r}; expected one of "
                f"{sorted(allowed)}"
            )
        if normalized == "title":
            return
        if normalized == "onboarding":
            # Exercise the same first-New-Game path as a player.  Constructing
            # OnboardingScene directly would miss its ordering over the pack
            # intro and could leave a visually-correct but unreachable modal
            # in the release baselines.
            app.config.seen_intro = False
            app._start_new_game()
            return
        if normalized == "dialogue":
            cls._prepare_dialogue_view(app, scene_id=scene_id)
            return
        if normalized == "choice":
            cls._prepare_choice_view(app, scene_id=scene_id)
            return

        cls._complete_intro_state(app)
        if normalized == "scene_replay":
            cls._ensure_replay_visual_state(app, scene_id=scene_id)
        if normalized in {
            "spatial_map_story_priority",
            "spatial_map_all_reachable",
            "travel_confirmation",
        }:
            cls._complete_spatial_map_entry_state(app)
        cls._replace_with_exploration(app)
        if normalized in {
            "spatial_map_story_priority",
            "spatial_map_all_reachable",
            "travel_confirmation",
        }:
            map_scene = cls._prepare_spatial_map_view(
                app,
                show_all=normalized == "spatial_map_all_reachable",
            )
            if normalized == "travel_confirmation":
                cls._prepare_travel_confirmation_view(app, map_scene)
        elif normalized == "destination_list":
            from ..scenes.destination_picker import DestinationPickerScene

            app.manager.push(
                DestinationPickerScene(app.ctx),
                on_close=app.manager.pop,
                on_move_to=app._move_from_map,
                on_travel=app._travel_from_map_plan,
                on_wait=app._advance_time,
                on_world_map=app._open_world_map,
            )
            app.manager.commit_pending()
        elif normalized == "menu":
            app._open_menu()
            app.manager.commit_pending()
        elif normalized == "history":
            # ``_complete_intro_state`` above drives real authored dialogue,
            # so this captures a populated product surface instead of a
            # synthetic empty panel.  The disposable visual state prevents
            # those lines from touching player saves or clear data.
            app._open_scrollback()
            app.manager.commit_pending()
        elif normalized in {"save", "load"}:
            # Isolated visual-check storage starts empty. Seed one real slot so
            # both screens exercise row typography, special-slot badges and
            # action controls rather than capturing only an empty state. Draw
            # the clean exploration base once first so the saved thumbnail is
            # representative instead of an uninitialised black canvas.
            app.manager.draw(app.screen)
            app._quicksave()
            cls._pin_visual_save_timestamp(app)
            if normalized == "save":
                app._open_save_menu()
            else:
                app._open_load_menu()
            app.manager.commit_pending()
        elif normalized == "settings":
            app._open_settings()
            app.manager.commit_pending()
        elif normalized == "cg_gallery":
            cls._seed_gallery_visual_state(app)
            app._open_cg_gallery()
            app.manager.commit_pending()
        elif normalized == "music_room":
            cls._seed_music_room_visual_state(app)
            app._open_music_room()
            app.manager.commit_pending()
        elif normalized == "scene_replay":
            app._open_scene_replay()
            app.manager.commit_pending()
        elif normalized == "flowchart":
            app._open_flowchart()
            app.manager.commit_pending()

    @classmethod
    def _ensure_replay_visual_state(
        cls,
        app: Any,
        *,
        scene_id: str | None = None,
    ) -> None:
        """Create a genuine complete trace for the Replay Library capture.

        Completing the intro normally supplies the representative trace. A
        pack without ``intro_scene`` (or a scenario pinning ``scene_id``)
        still follows real authored dialogue to its terminal boundary. This
        keeps the visual scenario on the same product path as a player and
        avoids manufacturing a read-log/archive entry by hand.
        """
        replayable = app.state.read_log.replayable_scene_ids()
        if scene_id is None and replayable:
            return

        selected = cls._representative_dialogue_scene(app, scene_id)
        if selected not in replayable:
            cls._drive_story(app, selected, stop_at_choice=False)
        if selected not in app.state.read_log.replayable_scene_ids():
            raise RuntimeError(
                f"scene {selected!r} did not produce a complete replay trace"
            )

    @staticmethod
    def _seed_gallery_visual_state(app: Any) -> None:
        """Unlock representative authored entries for a meaningful capture.

        The operation only touches the disposable visual-check state.  It is
        derived from manifest order rather than pack-specific ids so gallery
        candidates exercise real titles, subtitles, thumbnails, and hidden
        entries whenever a pack declares them.
        """
        from ..core.cg_gallery_spec import CGGalleryManifest

        manifest = app.state.meta.get("__cg_gallery_manifest__")
        if not isinstance(manifest, CGGalleryManifest) or not manifest.entries:
            return
        entries = manifest.ordered()
        indices = {0, len(entries) // 2, len(entries) - 1}
        for index in sorted(indices):
            app.state.cg_gallery.unlock(entries[index].asset)

    @staticmethod
    def _seed_music_room_visual_state(app: Any) -> None:
        """Unlock a stable representative subset without starting playback.

        ``MediaAuthority`` is the installed pack's decoder boundary, so it is
        also the only safe source for a generic production capture.  Sorting
        its immutable ids and selecting the first, middle, and final entries
        keeps the visual state deterministic across filesystems while
        exercising both heard and locked rows when the roster is large enough.
        This helper deliberately touches only progress state; opening
        :class:`MusicRoomScene` never invokes its playback action.
        """
        from ..core.media_authority import MediaAuthority

        authority = app.state.meta.get("__pack_media_authority__")
        if not isinstance(authority, MediaAuthority) or not authority.music_ids:
            return
        tracks = sorted(authority.music_ids)
        if len(tracks) <= 2:
            indices = {0}
        elif len(tracks) == 3:
            indices = {0, 2}
        else:
            indices = {0, len(tracks) // 2, len(tracks) - 1}
        for index in sorted(indices):
            app.state.music_room.unlock(tracks[index])

    @staticmethod
    def _pin_visual_save_timestamp(app: Any) -> None:
        """Remove wall-clock variance from the disposable visual-test slot."""
        slot = app.config.quicksave_slot
        save_path = app.config.save_dir() / f"{slot}.json"
        try:
            payload = json.loads(save_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"visual-check quicksave was not readable: {save_path}"
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"visual-check quicksave was not an object: {save_path}"
            )
        payload["_saved_at"] = _VISUAL_SAVE_TIMESTAMP
        save_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def _complete_spatial_map_entry_state(cls, app: Any) -> None:
        """Reach the authored boundary that normally reveals the spatial map.

        Some packs finish their title ``intro_scene`` before an in-world
        onboarding sequence. ``spatial_map.auto_open_after_scenes`` is the
        generic contract identifying where that sequence hands control to the
        schedule map. Starting its scene directly would bypass requirements and
        lie about player state, so this driver walks real available exits,
        prefers exits whose exact plan triggers an arrival scene, and completes
        each resulting authored dialogue with deterministic first-enabled
        choices. A bounded wait fallback covers calendar-gated handoffs.

        Packs that do not author an auto-open boundary retain the historical
        post-intro state.
        """
        from world_gal_game.core.action_policy import action_availability
        from world_gal_game.core.spatial_map import parse_spatial_map_spec
        from world_gal_game.core.travel import TravelService

        spatial_map = parse_spatial_map_spec(app.meta)
        targets = (
            tuple(spatial_map.auto_open_after_scenes)
            if spatial_map is not None else ()
        )
        if not targets or any(
            app.state.story.is_played(scene_id) for scene_id in targets
        ):
            return

        attempted: set[tuple[tuple[Any, ...], str]] = set()
        for _step in range(256):
            if any(
                app.state.story.is_played(scene_id) for scene_id in targets
            ):
                return
            current = app.state.map.current
            signature = (
                app.state.map.current_location_id,
                app.state.time.day,
                app.state.time.phase_index,
                tuple(sorted(
                    str(key)
                    for key, value in app.state.events.flags.items()
                    if value
                )),
                tuple(sorted(app.state.story.played)),
            )
            plans = []
            if current is not None:
                for authored_index, exit_obj in enumerate(current.exits):
                    plan = TravelService.plan(
                        app.state,
                        exit_obj.target,
                        allow_distant=False,
                    )
                    if (
                        plan is None
                        or (signature, exit_obj.target) in attempted
                    ):
                        continue
                    plans.append((
                        plan.arrival_hook_id is None,
                        authored_index,
                        exit_obj.target,
                        plan,
                    ))

            if plans:
                _without_hook, _index, destination, plan = min(plans)
                attempted.add((signature, destination))
                result = TravelService.commit(app.state, plan)
                if not result.ok:
                    continue
                if result.scene_triggered:
                    cls._drive_story(
                        app,
                        result.scene_triggered,
                        stop_at_choice=False,
                    )
                continue

            can_wait, _reason = action_availability(
                app.meta,
                app.state,
                "advance_time",
            )
            if can_wait:
                app.state.time.advance(1)
                continue
            break

        pending = ", ".join(
            scene_id for scene_id in targets
            if not app.state.story.is_played(scene_id)
        )
        raise RuntimeError(
            "spatial-map visual scenario could not organically reach "
            f"auto-open scene(s): {pending}"
        )

    @staticmethod
    def _prepare_spatial_map_view(app: Any, *, show_all: bool) -> Any:
        """Open the real map in one explicit, reproducible display scope.

        ``MapScene`` intentionally defaults to the low-cognitive-load story
        layer when a pack enables guided destinations. A visual regression
        needs both that product default and the expanded reachable layer, so
        the developer driver pins the scene-owned toggle before the first
        captured frame. Packs without guided destinations honestly render their
        normal all-reachable fallback for both scopes.
        """
        from world_gal_game.scenes.map_scene import MapScene

        app._open_map()
        app.manager.commit_pending()
        map_scene = app.manager.current
        if not isinstance(map_scene, MapScene):
            raise RuntimeError(
                "spatial-map visual scenario did not open MapScene"
            )
        if (
            map_scene._guided_enabled
            and map_scene._show_ordinary != bool(show_all)
        ):
            # Exercise the same transition as a player activating the layer
            # control. This also covers its focus handoff and one-at-a-time
            # ordinary-destination disclosure instead of manufacturing a
            # visually different private state.
            map_scene._toggle_ordinary()
        else:
            map_scene._show_ordinary = bool(show_all)
            map_scene._refresh()
            if show_all and map_scene.map_view is not None:
                map_scene.map_view.focus_first(
                    location_id
                    for location_id, marker_kind
                    in map_scene._marker_kinds.items()
                    if (
                        marker_kind == "ordinary"
                        and location_id in map_scene._reachable_ids
                    )
                )
        return map_scene

    @staticmethod
    def _representative_travel_target(app: Any, map_scene: Any) -> str:
        """Select one exact, authored-order destination without pack IDs.

        Prefer an immediately travelable primary story offer. If the current
        story layer only contains future/locked offers (or guidance is not
        configured), choose the first visible reachable marker, then the first
        reachable authored location. All candidates come from ``MapScene``'s
        exact planner result; the visual tool never invents reachability.
        """
        described = map_scene.describe()
        ordered_ids = list(app.state.map.locations)
        guided = described.get("guided_offers", {})
        for location_id in ordered_ids:
            offer = guided.get(location_id)
            if not isinstance(offer, dict):
                continue
            if (
                offer.get("action") == "travel"
                and offer.get("available") is True
                and isinstance(offer.get("travel"), dict)
            ):
                return location_id

        reachable = set(described.get("reachable", ()))
        markers = described.get("markers", {})
        for location_id in ordered_ids:
            offer = guided.get(location_id)
            if (
                location_id in reachable
                and location_id in markers
                and (
                    not isinstance(offer, dict)
                    or offer.get("action") == "travel"
                )
            ):
                return location_id
        for location_id in ordered_ids:
            if location_id in reachable:
                return location_id
        raise ValueError(
            "travel-confirmation visual scenario requires at least one "
            "currently reachable destination"
        )

    @staticmethod
    def _advance_to_first_story_departure(
        app: Any,
        map_scene: Any,
    ) -> bool:
        """Advance an allowed clock-only setup to the first future story trip."""
        from world_gal_game.core.action_policy import action_availability

        guided = map_scene.describe().get("guided_offers", {})
        for location_id in app.state.map.locations:
            offer = guided.get(location_id)
            if not isinstance(offer, dict):
                continue
            future = offer.get("future")
            wait_phases = (
                future.get("wait_phases")
                if isinstance(future, dict) else None
            )
            if (
                offer.get("action") != "wait"
                or offer.get("available") is not True
                or isinstance(wait_phases, bool)
                or not isinstance(wait_phases, int)
                or wait_phases <= 0
            ):
                continue
            for _phase in range(wait_phases):
                can_wait, _reason = action_availability(
                    app.meta,
                    app.state,
                    "advance_time",
                )
                if not can_wait:
                    return False
                app.state.time.advance(1)
            map_scene._refresh()
            refreshed = map_scene.describe().get(
                "guided_offers",
                {},
            ).get(location_id)
            return bool(
                isinstance(refreshed, dict)
                and refreshed.get("action") == "travel"
                and refreshed.get("available") is True
            )
        return False

    @classmethod
    def _prepare_travel_confirmation_view(
        cls,
        app: Any,
        map_scene: Any,
    ) -> None:
        """Open a non-mutating confirmation over the exact selected map node."""
        from world_gal_game.scenes.destination_picker import (
            DestinationPickerScene,
        )

        # The story-priority map often opens one evening before tomorrow's
        # heroine events. For the confirmation baseline, deterministically
        # follow the first advisory wait to its legal departure so the modal
        # exercises an actual "前往" quote rather than the separate wait UI.
        cls._advance_to_first_story_departure(app, map_scene)
        before_location = app.state.map.current_location_id
        before_time = app.state.time.model_dump(mode="json")
        target = cls._representative_travel_target(app, map_scene)
        if target not in map_scene.describe().get("markers", {}):
            # An ordinary fallback target hidden by a guided story filter must
            # be visible behind its confirmation sheet. This mirrors a player
            # expanding the map before selecting it.
            if map_scene._guided_enabled and not map_scene._show_ordinary:
                map_scene._toggle_ordinary()
            else:
                map_scene._show_ordinary = True
                map_scene._refresh()
        map_scene._on_node_click(target)
        app.manager.commit_pending()

        picker = app.manager.current
        if not isinstance(picker, DestinationPickerScene):
            raise RuntimeError(
                "travel-confirmation visual scenario did not open "
                "DestinationPickerScene"
            )
        selected = picker._selected_dest()
        if (
            selected is None
            or selected.loc.id != target
            or not selected.available
            or selected.plan is None
            or (
                selected.offer is not None
                and selected.offer.action != "travel"
            )
        ):
            raise RuntimeError(
                f"destination {target!r} did not produce a travel confirmation"
            )
        if (
            app.state.map.current_location_id != before_location
            or app.state.time.model_dump(mode="json") != before_time
        ):
            raise RuntimeError(
                "opening a travel confirmation mutated location or time"
            )

    @staticmethod
    def _replace_with_exploration(app: Any) -> None:
        """Install one clean exploration scene without firing an intro."""
        from world_gal_game.scenes.exploration import ExplorationScene

        app.manager.clear_to(
            ExplorationScene(app.ctx),
            **app._exploration_callbacks(),
        )
        app.manager.commit_pending()

    @staticmethod
    def _discard_pending_visual_fx(app: Any) -> None:
        """Drop UI directives accumulated by headless story preparation."""
        from world_gal_game.plugins.builtin_effects import VISUAL_FX_QUEUE

        app.state.meta.pop(VISUAL_FX_QUEUE, None)

    @staticmethod
    def _silence_notifications(app: Any) -> None:
        """Keep screen baselines focused on the requested view.

        Story preparation can legitimately unlock clues/achievements. Those
        transient cards deserve their own opt-in scenario, but must not cover
        the menu/destination screen a default baseline is meant to review.
        """
        for achievement in app.state.achievements.newly_unlocked():
            app.state.achievements.mark_seen(achievement.id)
        app.state.meta.pop("__pending_toasts__", None)
        app.toast_stack.toasts.clear()

    @classmethod
    def _drive_story(cls, app: Any, scene_id: str,
                     *, stop_at_choice: bool) -> tuple[Any, Any | None]:
        """Advance authored story state with deterministic choice selection.

        Returns ``(presentation, last_line_presentation)``. With
        ``stop_at_choice=True``, the first element is the choice presentation.
        Otherwise it is the terminal ``end`` presentation. The first enabled
        authored option is selected when completing an intro; no UI timing or
        synthetic input is involved.
        """
        presentation = app.dialogue.start_scene(scene_id)
        last_line = None
        for _step in range(10_000):
            if presentation.kind == "line":
                last_line = presentation
                presentation = app.dialogue.next_line()
                continue
            if presentation.kind == "choice":
                if stop_at_choice:
                    cls._discard_pending_visual_fx(app)
                    return presentation, last_line
                enabled = [
                    option for option in presentation.choices
                    if option.enabled
                ]
                if not enabled:
                    raise RuntimeError(
                        f"intro path is blocked at scene "
                        f"{presentation.scene_id!r}: no enabled choice"
                    )
                presentation = app.dialogue.choose(enabled[0].id)
                continue
            if presentation.kind == "transition":
                if not presentation.next_scene:
                    break
                presentation = app.dialogue.start_scene(
                    presentation.next_scene
                )
                continue
            if presentation.kind == "end":
                cls._discard_pending_visual_fx(app)
                return presentation, last_line
            raise RuntimeError(
                f"unsupported dialogue presentation kind: "
                f"{presentation.kind!r}"
            )
        raise RuntimeError(
            f"story preparation exceeded 10000 steps from {scene_id!r}"
        )

    @classmethod
    def _complete_intro_state(cls, app: Any) -> None:
        """Reach the state players receive when the authored intro finishes."""
        intro = app.meta.get("intro_scene")
        if intro is None:
            return
        if not isinstance(intro, str) or intro not in app.state.story.scenes:
            raise ValueError(f"unknown intro_scene in pack metadata: {intro!r}")
        cls._drive_story(app, intro, stop_at_choice=False)

    @staticmethod
    def _install_empty_dialogue_scene(app: Any) -> Any:
        """Push a fully wired DialogueScene without starting story content."""
        from world_gal_game.scenes.dialogue_scene import DialogueScene

        dialogue_scene = DialogueScene(app.ctx)
        app.manager.push(
            dialogue_scene,
            **app._dialogue_enter_callbacks(),
        )
        app.manager.commit_pending()
        return dialogue_scene

    @classmethod
    def _restore_presentations(cls, app: Any, dialogue_scene: Any,
                               *presentations: Any) -> None:
        """Stage already-evaluated presentations without replaying UI effects."""
        dialogue_scene._restoring_resume = True
        try:
            for presentation in presentations:
                if presentation is not None:
                    dialogue_scene._render_presentation(presentation)
        finally:
            dialogue_scene._restoring_resume = False
            cls._discard_pending_visual_fx(app)

    @staticmethod
    def _representative_dialogue_scene(app: Any,
                                       scene_id: str | None) -> str:
        scenes = app.state.story.scenes
        if scene_id is not None:
            scene = scenes.get(scene_id)
            if scene is None:
                raise ValueError(f"unknown dialogue scene: {scene_id}")
            if not scene.lines:
                raise ValueError(
                    f"dialogue scenario scene has no lines: {scene_id}"
                )
            return scene_id

        intro = app.meta.get("intro_scene")
        if isinstance(intro, str):
            scene = scenes.get(intro)
            if scene is not None and scene.lines:
                return intro
        for candidate in scenes.values():
            if candidate.lines:
                return candidate.id
        raise ValueError("pack has no authored dialogue lines")

    @classmethod
    def _representative_choice_scene(cls, app: Any,
                                     scene_id: str | None) -> str:
        scenes = app.state.story.scenes
        if scene_id is not None:
            scene = scenes.get(scene_id)
            if scene is None:
                raise ValueError(f"unknown choice scene: {scene_id}")
            if scene.choices:
                return scene.id
            raise ValueError(
                f"choice scenario scene has no authored choices: {scene_id}"
            )

        first_authored = None
        for scene in scenes.values():
            if not scene.choices:
                continue
            if first_authored is None:
                first_authored = scene.id
            # Prefer a scene that already exposes an option in the requested
            # pre-state. If none does, fall back to the first authored choice:
            # its preceding line effects may unlock the decision while the
            # story driver advances to the boundary.
            for choice in scene.choices:
                try:
                    enabled = (
                        app.state.evaluate_all(choice.requires)
                        and app.state.evaluate_none(choice.forbids)
                    )
                except Exception:
                    enabled = False
                if enabled or not choice.hidden_if_locked:
                    return scene.id
        if first_authored is not None:
            return first_authored
        raise ValueError("pack has no authored choices")

    @classmethod
    def _prepare_dialogue_view(cls, app: Any,
                               *, scene_id: str | None = None) -> None:
        """Render the first line of an authored scene over exploration."""
        selected = cls._representative_dialogue_scene(app, scene_id)
        cls._replace_with_exploration(app)
        current = cls._install_empty_dialogue_scene(app)
        presentation = app.dialogue.start_scene(selected)
        if presentation.kind != "line":
            raise RuntimeError(
                f"scene {selected!r} did not produce a dialogue line"
            )
        current.scene_id = selected
        current._scene_started = True
        cls._restore_presentations(app, current, presentation)
        # Baselines should contain readable content, not a host-speed-dependent
        # fraction of the typewriter animation.
        if current.box is not None:
            current.box.force_reveal()

    @classmethod
    def _prepare_choice_view(cls, app: Any,
                             *, scene_id: str | None = None) -> None:
        """Render an authored decision point without replaying its route.

        Entering a choice scene and fast-forwarding every preceding line would
        apply narrative effects and could open movies/chapter cards. Instead we
        create the genuine DialogueScene/ChoiceMenu, place StoryGraph at that
        scene's authored choice boundary, and ask DialogueEngine for the same
        choice presentation normal play uses.
        """
        selected = cls._representative_choice_scene(app, scene_id)
        cls._replace_with_exploration(app)
        presentation, last_line = cls._drive_story(
            app, selected, stop_at_choice=True
        )
        dialogue_scene = cls._install_empty_dialogue_scene(app)
        dialogue_scene.scene_id = selected
        dialogue_scene._scene_started = True
        cls._restore_presentations(
            app, dialogue_scene, last_line, presentation
        )
        if not (
            dialogue_scene.choices is not None
            and dialogue_scene.choices.visible
        ):
            raise RuntimeError(
                f"scene {selected!r} did not produce a visible choice screen"
            )

    @staticmethod
    def _apply_dev_start(app: Any, dev_start: str) -> None:
        """Mirror cli._run_screenshot_mode's dev-start switch table."""
        ds = dev_start
        if ds == "explore":
            app._start_new_game()
        elif ds.startswith("scene:"):
            app._start_new_game()
            app.manager.commit_pending()
            app._start_dialogue(ds.split(":", 1)[1])
        elif ds == "map":
            app._start_new_game()
            app.manager.commit_pending()
            app._open_map()
        elif ds == "affection":
            app._start_new_game()
            app.manager.commit_pending()
            app._open_affection()
        elif ds == "log":
            app._start_new_game()
            app.manager.commit_pending()
            app._open_event_log()
        elif ds == "settings":
            app._start_new_game()
            app.manager.commit_pending()
            app._open_settings()
        elif ds == "menu":
            app._start_new_game()
            app.manager.commit_pending()
            app._open_menu()
        elif ds == "achievements":
            app._start_new_game()
            app.manager.commit_pending()
            app.state.achievements.check(app.state)
            app._open_achievements()
        elif ds == "inventory":
            app._start_new_game()
            app.manager.commit_pending()
            app._open_inventory()

    # ------------------------------------------------------------------
    # Compare

    def compare(self, *, name: str) -> ComparisonResult:
        baseline = self.baselines_dir / f"{name}.png"
        candidate = self.candidates_dir / f"{name}.png"
        if not candidate.is_file():
            return ComparisonResult(
                name=name, ok=False,
                baseline_path=str(baseline), candidate_path=str(candidate),
                detail=f"candidate PNG missing: {candidate}",
            )
        if not baseline.is_file():
            if self.strict_baselines:
                return ComparisonResult(
                    name=name, ok=False,
                    baseline_path=str(baseline), candidate_path=str(candidate),
                    detail=(
                        "required baseline PNG missing; review a candidate and "
                        "promote it explicitly with --update-baselines: "
                        f"{baseline}"
                    ),
                )
            # First run: promote candidate to baseline.
            baseline.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(candidate, baseline)
            return ComparisonResult(
                name=name, ok=True,
                baseline_path=str(baseline), candidate_path=str(candidate),
                md5_match=True,
                detail=f"baseline created from current render at {baseline}",
                created_baseline=True,
            )

        b_md5 = _file_md5(baseline)
        c_md5 = _file_md5(candidate)
        if b_md5 == c_md5:
            return ComparisonResult(
                name=name, ok=True,
                baseline_path=str(baseline), candidate_path=str(candidate),
                md5_match=True, detail="exact md5 match",
            )

        diff_count = self._pixel_diff(baseline, candidate)
        ok = diff_count <= self.tolerance
        return ComparisonResult(
            name=name, ok=ok,
            baseline_path=str(baseline), candidate_path=str(candidate),
            md5_match=False, pixel_diff=diff_count,
            detail=(
                f"md5 mismatch; pixel diff = {diff_count} "
                f"(tolerance={self.tolerance})"
            ),
        )

    @staticmethod
    def _pixel_diff(a: Path, b: Path) -> int:
        """Count differing pixels between two PNGs (must have same size)."""
        import pygame

        # pygame.image load/convert APIs do not need video or audio
        # initialisation. Keeping this function display-free lets comparison run
        # after a hermetic capture has called pygame.quit(), and avoids changing
        # the caller's SDL environment.
        sa = pygame.image.load(str(a))
        sb = pygame.image.load(str(b))
        if sa.get_size() != sb.get_size():
            return max(sa.get_size()[0] * sa.get_size()[1],
                       sb.get_size()[0] * sb.get_size()[1])
        diff = 0
        # ``capture`` deliberately quits pygame after each hermetic app. Using
        # convert_alpha() here would require a live display pixel format and
        # crash precisely when a mismatch needs a pixel diff. tobytes()
        # performs the RGBA conversion without a display surface.
        a_bytes = pygame.image.tobytes(sa, "RGBA")
        b_bytes = pygame.image.tobytes(sb, "RGBA")
        # Compare 4 bytes (one pixel) at a time.
        for i in range(0, len(a_bytes), 4):
            if a_bytes[i:i + 4] != b_bytes[i:i + 4]:
                diff += 1
        return diff

    def update_baseline(self, *, name: str) -> Path:
        """Force-promote the current candidate to the baseline."""
        candidate = self.candidates_dir / f"{name}.png"
        baseline = self.baselines_dir / f"{name}.png"
        baseline.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(candidate, baseline)
        return baseline

    # ------------------------------------------------------------------
    # Batch runner

    def capture_scenario(self, scenario: dict[str, Any]) -> Path:
        """Capture one JSON scenario using the same schema as :meth:`run`.

        Keeping this adapter in one place prevents ``visual-check
        --update-baselines`` from silently ignoring new scenario fields.
        """
        return self.capture(
            name=scenario["name"],
            view=scenario.get("view"),
            scene_id=scenario.get("scene_id"),
            dev_start=scenario.get("dev_start"),
            dev_location=scenario.get("dev_location"),
            dev_time=scenario.get("dev_time"),
            dev_flags=scenario.get("dev_flags"),
            locale=scenario.get("locale"),
            text_expansion_factor=scenario.get("text_expansion_factor"),
            screen_size=(
                tuple(scenario["screen_size"])
                if scenario.get("screen_size") is not None
                else None
            ),
            autoplay=scenario.get("autoplay", 0.6),
            settle_frames=scenario.get("settle_frames"),
            include_toasts=bool(scenario.get("include_toasts", False)),
        )

    def run(self, scenarios: list[dict[str, Any]],
            *, pack_name: str | None = None) -> VisualReport:
        """Capture + compare every scenario. Default ``scenarios`` cover the
        most useful screens of a typical pack."""
        # Kept for source compatibility; capture_scenario always drives the
        # resolved absolute constructor path.
        _ = pack_name
        report = VisualReport(pack_root=str(self.pack_root))
        for sc in scenarios:
            name = sc["name"]
            try:
                self.capture_scenario(sc)
            except Exception as exc:
                report.results.append(ComparisonResult(
                    name=name, ok=False,
                    baseline_path="", candidate_path="",
                    detail=f"capture failed: {exc}",
                ))
                continue
            report.results.append(self.compare(name=name))
        return report

    @staticmethod
    def default_scenarios() -> list[dict[str, Any]]:
        """Representative default screens, with no pack-specific IDs.

        Dialogue uses ``meta.intro_scene`` (falling back to the first scene
        with lines); choice uses the first scene with authored choices and
        evaluates its preceding lines to reach the real decision boundary.
        Product overlays deterministically complete the intro before sharing a
        clean exploration base. The map contract gets three separate semantic
        views: story priority, all reachable, and a real non-mutating travel
        confirmation selected from an exact ``TravelPlan`` without a
        pack-specific location ID. Every default view is captured at both the
        1280x720 shipping baseline and a 1920x1080 high-density baseline; font
        scaling bugs therefore fail the gate instead of hiding at 720p.
        """
        views = [
            ("title", "title"),
            ("onboarding", "onboarding"),
            ("dialogue", "dialogue"),
            ("choice", "choice"),
            ("exploration", "exploration"),
            (
                "spatial_map_story_priority",
                "spatial_map_story_priority",
            ),
            (
                "spatial_map_all_reachable",
                "spatial_map_all_reachable",
            ),
            ("travel_confirmation", "travel_confirmation"),
            ("pause_menu", "menu"),
            ("dialogue_history", "history"),
            ("save_screen", "save"),
            ("load_screen", "load"),
            ("settings", "settings"),
            ("cg_gallery", "cg_gallery"),
            ("music_room", "music_room"),
            ("scene_replay", "scene_replay"),
            ("flowchart", "flowchart"),
        ]
        sizes = [
            ("720p", (1280, 720)),
            ("1080p", (1920, 1080)),
        ]
        return [
            {
                "name": f"{name}_{size_label}",
                "view": view,
                "screen_size": size,
            }
            for size_label, size in sizes
            for name, view in views
        ]


# ----------------------------------------------------------------------
# Helpers


def _file_md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
