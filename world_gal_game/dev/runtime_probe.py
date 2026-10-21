"""Isolated child process for the runtime reliability controller.

The controller owns policy and subprocess failure handling.  This worker owns
only one hermetic app lifecycle and writes milestones to a dedicated NDJSON
file, so pack/plugin output on stdout cannot forge the protocol.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

try:
    import resource
except ImportError:  # Windows
    resource = None  # type: ignore[assignment]


_MODULE_ENTRY_NS = time.monotonic_ns()
PROBE_SCHEMA = "world-gal-game.runtime-probe.v1"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name is not None:
            try:
                Path(temp_name).unlink()
            except OSError:
                pass


def _wait_for_cleanup_ack(path: Path, *, timeout_s: float) -> dict[str, Any]:
    """Wait while the controller inspects descendants with this leader alive."""
    deadline = time.monotonic() + max(0.1, timeout_s)
    last_error: str | None = None
    while time.monotonic() < deadline:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise TypeError("cleanup acknowledgement is not an object")
            if payload.get("schema") != PROBE_SCHEMA:
                raise ValueError("cleanup acknowledgement schema is unsupported")
            return payload
        except FileNotFoundError:
            pass
        except (OSError, TypeError, ValueError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.01)
    suffix = f"; last read error: {last_error}" if last_error else ""
    raise TimeoutError(f"controller cleanup acknowledgement timed out{suffix}")


def _windows_memory() -> tuple[int | None, int | None, str]:
    if os.name != "nt":
        return None, None, "unavailable"

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    try:
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        success = ctypes.windll.psapi.GetProcessMemoryInfo(  # type: ignore[attr-defined]
            ctypes.windll.kernel32.GetCurrentProcess(),  # type: ignore[attr-defined]
            ctypes.byref(counters),
            counters.cb,
        )
        if not success:
            return None, None, "windows-psapi-error"
        return (
            int(counters.WorkingSetSize),
            int(counters.PeakWorkingSetSize),
            "windows-psapi-working-set",
        )
    except (AttributeError, OSError, ValueError):
        return None, None, "unavailable"


def _current_rss_bytes() -> tuple[int | None, str]:
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/self/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024, "proc-self-status-kib"
        except (OSError, ValueError, IndexError):
            return None, "unavailable"
    if sys.platform == "darwin":
        try:
            completed = subprocess.run(
                ["ps", "-o", "rss=", "-p", str(os.getpid())],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
                timeout=2,
            )
            if completed.returncode == 0:
                return int(completed.stdout.strip()) * 1024, "ps-rss-kib"
        except (OSError, ValueError, subprocess.SubprocessError):
            return None, "unavailable"
    current, _, provider = _windows_memory()
    return current, provider


def _peak_rss_bytes() -> tuple[int | None, int | None, str, str]:
    if os.name == "nt":
        _, peak, provider = _windows_memory()
        return peak, peak, provider, "bytes"
    if resource is None:
        return None, None, "unavailable", "unknown"
    try:
        raw = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except (OSError, ValueError):
        return None, None, "unavailable", "unknown"
    if sys.platform == "darwin":
        return raw, raw, "getrusage-ru_maxrss", "bytes"
    return raw * 1024, raw, "getrusage-ru_maxrss", "kib"


def _memory_reading(phase: str, frame: int | None = None) -> dict[str, Any]:
    current, current_provider = _current_rss_bytes()
    peak, peak_raw, peak_provider, peak_raw_unit = _peak_rss_bytes()
    return {
        "phase": phase,
        "frame": frame,
        "current_rss_bytes": current,
        "current_provider": current_provider,
        "peak_rss_bytes": peak,
        "peak_rss_raw": peak_raw,
        "peak_provider": peak_provider,
        "peak_raw_unit": peak_raw_unit,
    }


class _Progress:
    def __init__(self, path: Path, *, parent_start_ns: int):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        self._seq = 0
        self._parent_start_ns = parent_start_ns

    def emit(self, event: str, **detail: Any) -> None:
        self._seq += 1
        now_ns = time.monotonic_ns()
        payload = {
            "schema": PROBE_SCHEMA,
            "seq": self._seq,
            "event": event,
            "monotonic_ns": now_ns,
            "fresh_process_elapsed_ms": (now_ns - self._parent_start_ns) / 1_000_000,
            **detail,
        }
        data = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
            + "\n"
        ).encode("utf-8")
        offset = 0
        while offset < len(data):
            written = os.write(self._fd, data[offset:])
            if written <= 0:
                raise OSError("progress write made no forward progress")
            offset += written

    def close(self) -> None:
        try:
            os.close(self._fd)
        except OSError:
            pass


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("semantic projection contains a non-finite float")
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, set):
        return sorted((_jsonable(item) for item in value), key=repr)
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if hasattr(value, "value") and isinstance(value.value, (str, int, float, bool)):
        return _jsonable(value.value)
    raise TypeError(f"unsupported semantic value: {type(value).__name__}")


def _semantic_projection(app: Any) -> dict[str, Any]:
    """Stable semantic state with a digest of every persistent subsystem."""
    state = app.state
    persistent = state.model_dump(mode="json")
    # These three fields retain *which* events/unlocks happened, while
    # normalising the engine-owned wall clock that intentionally differs in
    # every process. Plugin-authored public state is not stripped: seeded runs
    # that write nondeterministic values must produce a digest mismatch.
    for entry in (persistent.get("events") or {}).get("entries") or []:
        if isinstance(entry, dict) and "timestamp" in entry:
            entry["timestamp"] = "<wall-clock>"
    for tracker_name in ("achievements", "endings"):
        unlocked = (persistent.get(tracker_name) or {}).get("unlocked")
        if isinstance(unlocked, dict):
            for key in unlocked:
                unlocked[key] = "<wall-clock>"
    persistent = _jsonable(persistent)
    persistent_bytes = json.dumps(
        persistent,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    from world_gal_game.plugins import INSPECT_FIELD_REGISTRY

    inspect_errors: list[Any] = []
    plugin_inspect = _jsonable(
        INSPECT_FIELD_REGISTRY.collect(state, capture=inspect_errors)
    )
    if inspect_errors:
        first = inspect_errors[0]
        raise RuntimeError(
            "plugin inspect field failed: "
            f"{getattr(first, 'plugin_id', 'unknown')} at "
            f"{getattr(first, 'where', 'unknown')}: "
            f"{getattr(first, 'message', first)}"
        )
    resources = {
        item["id"]: item["value"]
        for item in state.resources.visible_snapshot()
    }
    projection = {
        "pack_id": app.config.pack_id,
        "persistent_state_sha256": hashlib.sha256(persistent_bytes).hexdigest(),
        "persistent_state_json_bytes": len(persistent_bytes),
        "plugin_inspect": plugin_inspect,
        "scene_stack": app.manager.describe(),
        "location": state.map.current_location_id,
        "time": {
            "day": state.time.day,
            "time_of_day": state.time.time_of_day.value,
        },
        "story": {
            "current_scene": state.story.current_scene,
            "current_line_index": state.story.current_line_index,
            "played": sorted(state.story.played),
            "route": state.route,
            "current_chapter": state.current_chapter,
        },
        "flags": dict(state.events.flags),
        "affection": state.affection.all_stats(),
        "resources": resources,
        "inventory": dict(state.inventory.list_owned()),
        "achievements_unlocked": sorted(state.achievements.unlocked),
        "cache": app.assets.cache_stats(),
    }
    return _jsonable(projection)


def _checkpoint(app: Any, label: str) -> dict[str, Any]:
    semantic = _semantic_projection(app)
    canonical = json.dumps(
        semantic,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return {
        "label": label,
        "sha256": hashlib.sha256(canonical).hexdigest(),
        "semantic": semantic,
    }


def _chrome_stats(app: Any) -> dict[str, Any]:
    host = getattr(app, "ui_chrome", None)
    return {
        "configured": getattr(app, "ui_skin_spec", None) is not None,
        "active": bool(host is not None and host.active),
        "entries": int(host.cache_size) if host is not None else 0,
        "bytes": int(host.cache_bytes) if host is not None else 0,
        "budget_entries": int(getattr(host, "_max_cache_entries", 0)) if host is not None else 0,
        "budget_bytes": int(getattr(host, "_max_cache_bytes", 0)) if host is not None else 0,
    }


def _plugin_stats(app: Any) -> dict[str, Any]:
    manager = app.state.meta.get("__plugin_manager__")
    if manager is None:
        return {"loaded": [], "failed": [], "runtime_errors": []}
    summary = manager.summary()
    runtime_errors = [
        {
            "plugin_id": getattr(error, "plugin_id", "unknown"),
            "where": getattr(error, "where", "unknown"),
            "message": getattr(error, "message", str(error)),
        }
        for error in getattr(manager, "errors", [])
    ]
    return {
        "loaded": summary.get("loaded") or [],
        "failed": summary.get("failed") or [],
        "runtime_errors": runtime_errors,
    }


def _merge_plugin_observations(
    observations: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    def dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=repr)
            if key not in seen:
                seen.add(key)
                out.append(item)
        return out

    loaded: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    runtime_errors: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for phase, stats in observations:
        phase_loaded = stats.get("loaded") or []
        phase_failed = stats.get("failed") or []
        phase_errors = stats.get("runtime_errors") or []
        loaded.extend(phase_loaded)
        failed.extend(phase_failed)
        runtime_errors.extend(phase_errors)
        summaries.append(
            {
                "phase": phase,
                "loaded_ids": [item.get("id") for item in phase_loaded],
                "failed": phase_failed,
                "runtime_errors": phase_errors,
            }
        )
    return {
        "loaded": dedupe(loaded),
        "failed": dedupe(failed),
        "runtime_errors": dedupe(runtime_errors),
        "observations": summaries,
    }


def _platform_fingerprint(app: Any, pygame: Any, memory_provider: str) -> dict[str, Any]:
    try:
        video_driver = pygame.display.get_driver()
    except pygame.error:
        video_driver = None
    mixer_init = pygame.mixer.get_init()
    return {
        "system": platform.system(),
        "system_release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor() or platform.machine(),
        "python": platform.python_version(),
        "pygame": pygame.version.ver,
        "sdl": ".".join(str(part) for part in pygame.get_sdl_version()),
        "video_driver": video_driver,
        "audio_driver": os.environ.get("SDL_AUDIODRIVER"),
        "screen_width": int(app.config.screen_size[0]),
        "screen_height": int(app.config.screen_size[1]),
        "vsync": bool(app.config.vsync),
        "frozen": bool(getattr(sys, "frozen", False)),
        "memory_provider": memory_provider,
        "mixer_initialized": mixer_init is not None,
        "user_plugin_scope": "isolated",
    }


def _cache_snapshot(app: Any) -> dict[str, Any]:
    return {
        "assets": dict(app.assets.cache_stats()),
        "chrome": _chrome_stats(app),
    }


def _cache_violations(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    assets = snapshot["assets"]
    chrome = snapshot["chrome"]
    violations: list[dict[str, Any]] = []
    for name, actual_key, budget_key in (
        ("image", "image_bytes", "image_budget_bytes"),
        ("sound", "sound_bytes", "sound_budget_bytes"),
    ):
        actual = int(assets.get(actual_key, 0) or 0)
        budget = int(assets.get(budget_key, 0) or 0)
        if actual > budget:
            violations.append(
                {"cache": name, "actual_bytes": actual, "budget_bytes": budget}
            )
    if chrome["bytes"] > chrome["budget_bytes"]:
        violations.append(
            {
                "cache": "ui_chrome",
                "actual_bytes": chrome["bytes"],
                "budget_bytes": chrome["budget_bytes"],
            }
        )
    if chrome["entries"] > chrome["budget_entries"]:
        violations.append(
            {
                "cache": "ui_chrome_entries",
                "actual_entries": chrome["entries"],
                "budget_entries": chrome["budget_entries"],
            }
        )
    return violations


def _run_probe(
    spec: dict[str, Any],
    *,
    parent_start_ns: int,
    progress: _Progress,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": PROBE_SCHEMA,
        "ok": False,
        "mode": spec.get("mode"),
        "error": None,
        "timing": {},
        "fingerprint": {},
        "memory": {"samples": [], "first_frame_to_final_growth_bytes": None},
        "views": [],
        "semantic_checkpoints": [],
        "cache": {
            "start": None,
            "end": None,
            "peak": {},
            "chrome_peak": {"entries": 0, "bytes": 0},
            "violations": [],
        },
        "plugins": {
            "loaded": [],
            "failed": [],
            "runtime_errors": [],
            "observations": [],
        },
        "ui_chrome": {"configured": False, "active": False},
        "shutdown": {"ok": None, "duration_ms": None, "error": None},
    }
    app: Any = None
    finalized = False
    plugin_observations: list[tuple[str, dict[str, Any]]] = []

    def record_plugins(phase: str) -> None:
        if app is None:
            return
        plugin_observations.append((phase, _plugin_stats(app)))
        result["plugins"] = _merge_plugin_observations(plugin_observations)

    try:
        if spec.get("schema") != PROBE_SCHEMA:
            raise ValueError("probe spec schema is missing or unsupported")
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        os.environ["SDL_AUDIODRIVER"] = "dummy"
        os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
        os.environ.pop("WGG_DEV", None)
        os.environ.pop("WGG_STEAM", None)
        progress.emit("probe_started")

        import_started_ns = time.monotonic_ns()
        import pygame

        from world_gal_game.app import GalGameApp
        from world_gal_game.config import EngineConfig

        imports_ready_ns = time.monotonic_ns()
        progress.emit("imports_ready")

        screen = spec["screen_size"]
        config = EngineConfig(
            default_pack=spec["pack_root"],
            screen_size=(int(screen[0]), int(screen[1])),
            seed=int(spec["seed"]),
            writable_root_override=Path(spec["writable_root"]),
            fullscreen=False,
            vsync=False,
        )
        app_construct_started_ns = time.monotonic_ns()
        # headless=False is intentional: dummy SDL still creates/presents a
        # display surface, covering the real App _present()/flip path.
        app = GalGameApp(
            config=config,
            pack=spec["pack_root"],
            headless=False,
        )
        app_ready_ns = time.monotonic_ns()
        progress.emit("app_ready")

        dt = float(spec["fixed_dt_ms"]) / 1000.0
        first_frame_started_ns = time.monotonic_ns()
        app._step(dt)
        first_frame_ns = time.monotonic_ns()
        first_memory = _memory_reading("first_frame", 0)
        result["memory"]["samples"].append(first_memory)
        record_plugins("first_frame")
        progress.emit("first_frame", memory=first_memory)
        result["timing"] = {
            "fresh_process_to_module_entry_ms": (_MODULE_ENTRY_NS - parent_start_ns) / 1_000_000,
            "engine_import_ms": (imports_ready_ns - import_started_ns) / 1_000_000,
            "app_construct_ms": (app_ready_ns - app_construct_started_ns) / 1_000_000,
            "fresh_process_to_app_ready_ms": (app_ready_ns - parent_start_ns) / 1_000_000,
            "first_frame_work_ms": (first_frame_ns - first_frame_started_ns) / 1_000_000,
            "fresh_process_to_first_frame_ms": (first_frame_ns - parent_start_ns) / 1_000_000,
        }
        result["cache"]["start"] = _cache_snapshot(app)

        peak_assets: dict[str, int] = {
            key: int(value)
            for key, value in app.assets.cache_stats().items()
        }
        chrome = _chrome_stats(app)
        chrome_peak = {"entries": chrome["entries"], "bytes": chrome["bytes"]}
        all_cache_violations: list[dict[str, Any]] = []

        def sample_runtime(phase: str, frame: int | None = None) -> None:
            memory = _memory_reading(phase, frame)
            result["memory"]["samples"].append(memory)
            snapshot = _cache_snapshot(app)
            for key, value in snapshot["assets"].items():
                peak_assets[key] = max(peak_assets.get(key, 0), int(value))
            chrome_peak["entries"] = max(chrome_peak["entries"], snapshot["chrome"]["entries"])
            chrome_peak["bytes"] = max(chrome_peak["bytes"], snapshot["chrome"]["bytes"])
            for violation in _cache_violations(snapshot):
                if violation not in all_cache_violations:
                    all_cache_violations.append(violation)

        if spec["mode"] == "soak":
            started_new_game = False
            global_frame = 0
            for view_name in spec["views"]:
                setup_started_ns = time.monotonic_ns()
                state_origin = "boot_title"
                if view_name == "title":
                    if started_new_game:
                        raise ValueError("title view cannot run after new_game")
                elif view_name == "new_game":
                    if not started_new_game:
                        app._start_new_game()
                        app.manager.commit_pending()
                        started_new_game = True
                    state_origin = "direct_runtime_new_game_callback"
                else:
                    raise ValueError(f"unsupported semantic view: {view_name}")
                setup_ms = (time.monotonic_ns() - setup_started_ns) / 1_000_000
                sample_runtime(f"{view_name}:setup", global_frame)
                for _ in range(int(spec["warmup_frames"])):
                    app._step(dt)
                    global_frame += 1
                frame_times: list[float] = []
                for local_frame in range(int(spec["frames_per_view"])):
                    frame_started_ns = time.perf_counter_ns()
                    app._step(dt)
                    elapsed_ms = (time.perf_counter_ns() - frame_started_ns) / 1_000_000
                    frame_times.append(elapsed_ms)
                    global_frame += 1
                    if (
                        (local_frame + 1) % int(spec["rss_sample_interval_frames"]) == 0
                        or local_frame + 1 == int(spec["frames_per_view"])
                    ):
                        sample_runtime(f"{view_name}:measured", global_frame)
                checkpoint = _checkpoint(app, f"{view_name}:complete")
                result["semantic_checkpoints"].append(checkpoint)
                result["views"].append(
                    {
                        "name": view_name,
                        "state_origin": state_origin,
                        "setup_ms": setup_ms,
                        "warmup_frames": int(spec["warmup_frames"]),
                        "measured_frames": int(spec["frames_per_view"]),
                        "frame_times_ms": frame_times,
                        "semantic_sha256": checkpoint["sha256"],
                    }
                )
                record_plugins(f"{view_name}:complete")
                progress.emit(
                    "view_complete",
                    view=view_name,
                    measured_frames=len(frame_times),
                    semantic_sha256=checkpoint["sha256"],
                )

        sample_runtime("final")
        final_memory = result["memory"]["samples"][-1]
        first_current = first_memory.get("current_rss_bytes")
        final_current = final_memory.get("current_rss_bytes")
        if isinstance(first_current, int) and isinstance(final_current, int):
            result["memory"]["first_frame_to_final_growth_bytes"] = final_current - first_current
        result["cache"]["end"] = _cache_snapshot(app)
        result["cache"]["peak"] = peak_assets
        result["cache"]["chrome_peak"] = chrome_peak
        result["cache"]["violations"] = all_cache_violations
        record_plugins("final")
        result["ui_chrome"] = _chrome_stats(app)
        result["fingerprint"] = _platform_fingerprint(
            app,
            pygame,
            str(final_memory.get("current_provider") or "unavailable"),
        )

        shutdown_started_ns = time.monotonic_ns()
        progress.emit("shutdown_started")
        try:
            app._quit_app()
            app._finalize_shutdown()
            finalized = True
            result["shutdown"] = {
                "ok": True,
                "duration_ms": (time.monotonic_ns() - shutdown_started_ns) / 1_000_000,
                "error": None,
            }
        except BaseException as exc:
            result["shutdown"] = {
                "ok": False,
                "duration_ms": (time.monotonic_ns() - shutdown_started_ns) / 1_000_000,
                "error": f"{type(exc).__name__}: {exc}",
            }
            raise
        progress.emit("shutdown_complete")
        result["ok"] = True
    except BaseException as exc:  # noqa: BLE001 - child must report SystemExit too
        try:
            record_plugins("error")
        except Exception as plugin_exc:  # noqa: BLE001 - preserve the primary failure
            result["plugins"]["observation_error"] = (
                f"{type(plugin_exc).__name__}: {plugin_exc}"
            )
        result["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback_tail": traceback.format_exc().splitlines()[-40:],
        }
        try:
            progress.emit("probe_error", error_type=type(exc).__name__, message=str(exc))
        except BaseException:  # noqa: BLE001,S110 - primary error is already captured
            pass
    finally:
        if app is not None and not finalized:
            try:
                app._finalize_shutdown()
            except BaseException as exc:  # noqa: BLE001 - preserve teardown evidence
                if result["shutdown"]["error"] is None:
                    result["shutdown"] = {
                        "ok": False,
                        "duration_ms": None,
                        "error": f"fallback finalizer: {type(exc).__name__}: {exc}",
                    }
                result["ok"] = False
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m world_gal_game.dev.runtime_probe")
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--cleanup-ack", type=Path, required=True)
    parser.add_argument("--cleanup-ack-timeout-s", type=float, default=30.0)
    parser.add_argument("--parent-start-ns", type=int, required=True)
    args = parser.parse_args(argv)

    progress = _Progress(args.progress, parent_start_ns=args.parent_start_ns)
    try:
        try:
            spec = json.loads(args.spec.read_text(encoding="utf-8"))
            if not isinstance(spec, dict):
                raise TypeError("probe spec is not an object")
            result = _run_probe(
                spec,
                parent_start_ns=args.parent_start_ns,
                progress=progress,
            )
        except BaseException as exc:  # noqa: BLE001 - always emit a child artifact
            result = {
                "schema": PROBE_SCHEMA,
                "ok": False,
                "mode": None,
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "traceback_tail": traceback.format_exc().splitlines()[-40:],
                },
            }
            try:
                progress.emit("probe_error", error_type=type(exc).__name__, message=str(exc))
            except BaseException:  # noqa: BLE001,S110 - primary error is in result
                pass
        # Keep the leader alive while the controller takes its final recursive
        # descendant snapshot. This ready/ack barrier closes the race where a
        # setsid child could be spawned between periodic polls and leader exit.
        result["process_cleanup"] = {
            "status": "awaiting_controller",
            "ok": None,
        }
        _atomic_write_json(args.result, result)
        progress.emit("cleanup_ready")
        try:
            cleanup_ack = _wait_for_cleanup_ack(
                args.cleanup_ack,
                timeout_s=args.cleanup_ack_timeout_s,
            )
        except BaseException as exc:  # noqa: BLE001 - preserve handshake evidence
            cleanup_ack = {
                "schema": PROBE_SCHEMA,
                "status": "ack_error",
                "ok": False,
                "available": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        result["process_cleanup"] = cleanup_ack
        if cleanup_ack.get("ok") is not True:
            result["ok"] = False
            if result.get("error") is None:
                result["error"] = {
                    "type": "ProcessCleanupError",
                    "message": str(
                        cleanup_ack.get("error")
                        or "controller found a residual descendant or lacked containment"
                    ),
                    "traceback_tail": [],
                }
        progress.emit("cleanup_complete", ok=cleanup_ack.get("ok") is True)
        _atomic_write_json(args.result, result)
        return 0 if result.get("ok") is True else 1
    finally:
        progress.close()


if __name__ == "__main__":
    raise SystemExit(main())
