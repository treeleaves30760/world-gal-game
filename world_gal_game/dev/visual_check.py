"""VisualCheck — capture + diff screenshots against baselines.

Two responsibilities:

1. **Capture**: render a pack to a particular pre-state (location,
   flags, scene) using the dummy SDL driver, save the resulting
   Surface as PNG. This is the same flow ``wgg --screenshot`` does,
   wrapped into a callable so CI / tests can loop over many states.
2. **Compare**: byte-for-byte md5 + pixel-by-pixel diff against a
   stored baseline PNG. First run for a scenario creates the
   baseline; subsequent runs fail when content drifts (catches
   regressions in rendering, layout, theme).

Baselines live under ``<pack_root>/visual_baselines/<scenario>.png``
by default. Override via the constructor.

Usage::

    from world_gal_game.dev.visual_check import VisualCheck

    vc = VisualCheck("games/demo_pack")
    scenarios = [
        {"name": "title", "dev_start": None},
        {"name": "explore_start", "dev_start": "explore"},
        {"name": "map_open", "dev_start": "map"},
    ]
    report = vc.run(scenarios)
    if not report.ok:
        for r in report.results:
            if not r.ok:
                print(r.name, r.detail)
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_log = logging.getLogger("world_gal_game.dev.visual")


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
    """Render a pack into specific states and diff against baselines."""

    def __init__(self, pack_root: Path | str,
                 *, baselines_dir: Path | None = None,
                 candidates_dir: Path | None = None,
                 tolerance: int = 0) -> None:
        self.pack_root = Path(pack_root).resolve()
        self.baselines_dir = (
            Path(baselines_dir).resolve()
            if baselines_dir is not None
            else self.pack_root / "visual_baselines"
        )
        self.candidates_dir = (
            Path(candidates_dir).resolve()
            if candidates_dir is not None
            else self.pack_root / "visual_candidates"
        )
        # Allow up to N differing pixels before flagging a regression.
        # Useful for non-deterministic effects (e.g. text antialiasing).
        self.tolerance = max(0, tolerance)

    # ------------------------------------------------------------------
    # Capture

    def capture(self, *, name: str, dev_start: str | None = None,
                dev_location: str | None = None,
                dev_time: str | None = None,
                dev_flags: dict[str, Any] | None = None,
                autoplay: float = 0.6,
                pack_name: str | None = None) -> Path:
        """Render the pack to a particular state and save a PNG."""
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

        import pygame
        from world_gal_game.app import GalGameApp
        from world_gal_game.config import EngineConfig
        from world_gal_game.ui.input import InputState

        config = EngineConfig()
        pack = pack_name or self.pack_root.name
        config.default_pack = pack

        app = GalGameApp(config=config, pack=pack, headless=False)

        # Apply pre-state (same as cli._run_screenshot_mode).
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
        if dev_start:
            self._apply_dev_start(app, dev_start)

        # Run a few frames so animations / transitions settle.
        target_path = self.candidates_dir / f"{name}.png"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()
        while True:
            dt = app.clock.tick(app.config.fps) / 1000.0
            events = pygame.event.get()
            inp = InputState.collect(events)
            app.manager.update(dt, inp)
            app._poll_achievement_toasts()
            app.toast_stack.update(dt, inp)
            app.manager.draw(app.screen)
            app.toast_stack.draw(app.screen)
            pygame.display.flip()
            if time.monotonic() - start >= max(0.3, autoplay):
                break
        pygame.image.save(app.screen, str(target_path))
        pygame.quit()
        return target_path

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
            app._start_new_game(); app.manager.commit_pending()
            app._open_map()
        elif ds == "affection":
            app._start_new_game(); app.manager.commit_pending()
            app._open_affection()
        elif ds == "log":
            app._start_new_game(); app.manager.commit_pending()
            app._open_event_log()
        elif ds == "settings":
            app._start_new_game(); app.manager.commit_pending()
            app._open_settings()
        elif ds == "menu":
            app._start_new_game(); app.manager.commit_pending()
            app._open_menu()
        elif ds == "achievements":
            app._start_new_game(); app.manager.commit_pending()
            app.state.achievements.check(app.state)
            app._open_achievements()
        elif ds == "inventory":
            app._start_new_game(); app.manager.commit_pending()
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
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        pygame.init()
        sa = pygame.image.load(str(a))
        sb = pygame.image.load(str(b))
        if sa.get_size() != sb.get_size():
            return max(sa.get_size()[0] * sa.get_size()[1],
                       sb.get_size()[0] * sb.get_size()[1])
        # Use PixelArray for fast comparison.
        w, h = sa.get_size()
        diff = 0
        sa = sa.convert_alpha()
        sb = sb.convert_alpha()
        a_bytes = pygame.image.tostring(sa, "RGBA")
        b_bytes = pygame.image.tostring(sb, "RGBA")
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

    def run(self, scenarios: list[dict[str, Any]],
            *, pack_name: str | None = None) -> VisualReport:
        """Capture + compare every scenario. Default ``scenarios`` cover the
        most useful screens of a typical pack."""
        report = VisualReport(pack_root=str(self.pack_root))
        for sc in scenarios:
            name = sc["name"]
            try:
                self.capture(
                    name=name,
                    dev_start=sc.get("dev_start"),
                    dev_location=sc.get("dev_location"),
                    dev_time=sc.get("dev_time"),
                    dev_flags=sc.get("dev_flags"),
                    autoplay=sc.get("autoplay", 0.6),
                    pack_name=pack_name,
                )
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
        """A sensible default scenario set: title + 4 common overlays."""
        return [
            {"name": "title", "dev_start": None},
            {"name": "explore_start", "dev_start": "explore"},
            {"name": "map_open", "dev_start": "map"},
            {"name": "menu_open", "dev_start": "menu"},
        ]


# ----------------------------------------------------------------------
# Helpers


def _file_md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
