"""Fresh-process runtime reliability controller.

This module deliberately does not import pygame or the application runtime.
Each trial is executed by :mod:`world_gal_game.dev.runtime_probe` in a new
Python process, so startup and first-frame milestones include interpreter and
engine import cost.  The resulting report is a release artifact, not a
``SelfCheck`` stage: host-sensitive performance work must never slow down or
destabilise the normal structural authoring loop.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .. import __version__ as engine_version

REPORT_SCHEMA = "world-gal-game.runtime-reliability.v1"
POLICY_SCHEMA_VERSION = 1
PROBE_SCHEMA = "world-gal-game.runtime-probe.v1"
_SUPPORTED_VIEWS = ("title", "new_game")
_POLICY_TOP_KEYS = {
    "schema_version",
    "name",
    "claim_scope",
    "profile",
    "limits",
    "calibrated_fingerprint",
    "notes",
}
_PROFILE_KEYS = {
    "screen_size",
    "seed",
    "fixed_dt_ms",
    "warmup_frames",
    "frames_per_view",
    "views",
    "cold_runs",
    "soak_runs",
    "rss_sample_interval_frames",
    "timeout_s",
}
_LIMIT_KEYS = {
    "require_finite_metrics",
    "require_rss",
    "require_determinism",
    "require_within_configured_cache_budgets",
    "require_plugin_errors_zero",
    "require_ui_chrome_active",
    "max_bgm_slots",
    "cold_start_p95_ms",
    "first_frame_p95_ms",
    "current_rss_max_bytes",
    "peak_rss_max_bytes",
    "rss_growth_max_bytes",
    "frame_p95_ms",
    "frame_p99_ms",
    "frame_budget_ms",
    "max_over_budget_frames",
}
_NUMERIC_LIMIT_KEYS = {
    "cold_start_p95_ms",
    "first_frame_p95_ms",
    "current_rss_max_bytes",
    "peak_rss_max_bytes",
    "rss_growth_max_bytes",
    "frame_p95_ms",
    "frame_p99_ms",
    "frame_budget_ms",
    "max_over_budget_frames",
}


class _StrictPolicyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_policy_mapping(
    loader: _StrictPolicyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing runtime policy",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing runtime policy",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_StrictPolicyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_policy_mapping,
)


class RuntimePolicyError(ValueError):
    """The runtime policy is missing, malformed, or internally inconsistent."""


@dataclass(frozen=True)
class _CapturedStream:
    bytes: int
    sha256: str
    tail: str
    truncated: bool
    complete: bool
    termination: str
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "bytes": self.bytes,
            "sha256": self.sha256,
            "tail": self.tail,
            "truncated": self.truncated,
            "complete": self.complete,
            "termination": self.termination,
            "error": self.error,
        }


class _BoundedStreamCollector:
    """Continuously drain a pipe while retaining only a bounded tail."""

    def __init__(self, stream: Any, *, tail_bytes: int = 16_384):
        self._stream = stream
        self._tail_limit = tail_bytes
        self._tail = bytearray()
        self._bytes = 0
        self._hasher = hashlib.sha256()
        self._lock = threading.Lock()
        self._thread_done = False
        self._natural_eof = False
        self._forced_close = False
        self._read_error: str | None = None
        self._thread = threading.Thread(target=self._drain, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _drain(self) -> None:
        try:
            while True:
                chunk = self._stream.read(64 * 1024)
                if not chunk:
                    with self._lock:
                        self._natural_eof = True
                    break
                with self._lock:
                    self._bytes += len(chunk)
                    self._hasher.update(chunk)
                    self._tail.extend(chunk)
                    if len(self._tail) > self._tail_limit:
                        del self._tail[: len(self._tail) - self._tail_limit]
        except (OSError, ValueError) as exc:
            with self._lock:
                self._read_error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self._thread_done = True
            try:
                self._stream.close()
            except (OSError, ValueError):
                pass

    def wait_until(self, deadline: float) -> None:
        self._thread.join(timeout=max(0.0, deadline - time.monotonic()))

    def force_close(self) -> None:
        if not self._thread.is_alive():
            return
        with self._lock:
            if self._natural_eof:
                return
            self._forced_close = True
        try:
            self._stream.close()
        except (OSError, ValueError):
            pass

    def capture(self) -> _CapturedStream:
        with self._lock:
            total = self._bytes
            tail = bytes(self._tail).decode("utf-8", errors="replace")
            digest = self._hasher.hexdigest()
            thread_done = self._thread_done
            natural_eof = self._natural_eof
            forced_close = self._forced_close
            read_error = self._read_error
        complete = thread_done and natural_eof and not forced_close and read_error is None
        termination = (
            "eof"
            if complete
            else "forced_close"
            if forced_close
            else "read_error"
            if read_error is not None
            else "collector_timeout"
        )
        return _CapturedStream(
            bytes=total,
            sha256=digest,
            tail=tail,
            truncated=total > self._tail_limit or not complete,
            complete=complete,
            termination=termination,
            error=read_error,
        )


def _finish_stream_collectors(
    collectors: list[_BoundedStreamCollector],
    *,
    timeout_s: float = 1.0,
) -> list[_CapturedStream]:
    """Finish both pipe drains within one shared deadline.

    A descendant that escapes the worker's process group may keep an inherited
    pipe open after the leader exits.  We must return promptly and report that
    evidence as incomplete instead of waiting once per stream or calling the
    probe successful.
    """
    deadline = time.monotonic() + max(0.0, timeout_s)
    force_at = max(time.monotonic(), deadline - min(0.25, timeout_s / 2))
    for collector in collectors:
        collector.wait_until(force_at)
    for collector in collectors:
        collector.force_close()
    for collector in collectors:
        collector.wait_until(deadline)
    return [collector.capture() for collector in collectors]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _finite_number(value: Any, *, label: str, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimePolicyError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise RuntimePolicyError(f"{label} must be finite")
    if positive and number <= 0:
        raise RuntimePolicyError(f"{label} must be greater than zero")
    return number


def _strict_keys(mapping: dict[str, Any], allowed: set[str], *, label: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise RuntimePolicyError(
            f"{label} has unknown field(s): {', '.join(unknown)}"
        )


def load_runtime_policy(path: Path) -> tuple[dict[str, Any], str]:
    """Load and strictly validate a runtime policy.

    Numeric thresholds may be ``null``.  This is intentional for portable
    dummy-SDL calibration jobs: crash/protocol/determinism/cache invariants
    remain blocking while host-dependent timing and RSS values are recorded
    without pretending that a hosted runner is a target device.
    """
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise RuntimePolicyError(f"cannot read runtime policy {path}: {exc}") from exc
    try:
        raw = yaml.load(raw_bytes, Loader=_StrictPolicyLoader)
    except yaml.YAMLError as exc:
        raise RuntimePolicyError(f"invalid runtime policy YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuntimePolicyError("runtime policy must be a mapping")
    _strict_keys(raw, _POLICY_TOP_KEYS, label="runtime policy")
    if raw.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise RuntimePolicyError(
            f"runtime policy schema_version must be {POLICY_SCHEMA_VERSION}"
        )
    for key in ("name", "claim_scope"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise RuntimePolicyError(f"runtime policy {key} must be a non-empty string")
    if raw.get("notes") is not None and not isinstance(raw["notes"], str):
        raise RuntimePolicyError("runtime policy notes must be a string or null")

    profile = raw.get("profile")
    if not isinstance(profile, dict):
        raise RuntimePolicyError("runtime policy profile must be a mapping")
    _strict_keys(profile, _PROFILE_KEYS, label="runtime policy profile")
    missing_profile = sorted(_PROFILE_KEYS - set(profile))
    if missing_profile:
        raise RuntimePolicyError(
            "runtime policy profile is missing: " + ", ".join(missing_profile)
        )
    screen = profile["screen_size"]
    if (
        not isinstance(screen, list)
        or len(screen) != 2
        or any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in screen)
    ):
        raise RuntimePolicyError("profile.screen_size must be [positive width, height]")
    if screen[0] > 7680 or screen[1] > 4320:
        raise RuntimePolicyError("profile.screen_size exceeds the v1 safety ceiling")
    if isinstance(profile["seed"], bool) or not isinstance(profile["seed"], int):
        raise RuntimePolicyError("profile.seed must be an integer")
    _finite_number(profile["fixed_dt_ms"], label="profile.fixed_dt_ms", positive=True)
    if float(profile["fixed_dt_ms"]) > 100:
        raise RuntimePolicyError("profile.fixed_dt_ms must be at most 100")
    for key, lower, upper in (
        ("warmup_frames", 0, 100_000),
        ("frames_per_view", 1, 1_000_000),
        ("cold_runs", 1, 20),
        ("soak_runs", 2, 20),
        ("rss_sample_interval_frames", 1, 100_000),
    ):
        value = profile[key]
        if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
            raise RuntimePolicyError(
                f"profile.{key} must be an integer between {lower} and {upper}"
            )
    _finite_number(profile["timeout_s"], label="profile.timeout_s", positive=True)
    if float(profile["timeout_s"]) > 86_400:
        raise RuntimePolicyError("profile.timeout_s must be at most 86400")
    views = profile["views"]
    if not isinstance(views, list) or not views:
        raise RuntimePolicyError("profile.views must be a non-empty list")
    if any(not isinstance(view, str) for view in views):
        raise RuntimePolicyError("profile.views entries must be strings")
    if len(set(views)) != len(views):
        raise RuntimePolicyError("profile.views must not contain duplicates")
    unsupported = [view for view in views if view not in _SUPPORTED_VIEWS]
    if unsupported:
        raise RuntimePolicyError(
            "profile.views contains unsupported v1 view(s): " + ", ".join(unsupported)
        )
    if views != sorted(views, key=_SUPPORTED_VIEWS.index):
        raise RuntimePolicyError(
            "profile.views must follow runtime order: title, then new_game"
        )

    limits = raw.get("limits")
    if not isinstance(limits, dict):
        raise RuntimePolicyError("runtime policy limits must be a mapping")
    _strict_keys(limits, _LIMIT_KEYS, label="runtime policy limits")
    missing_limits = sorted(_LIMIT_KEYS - set(limits))
    if missing_limits:
        raise RuntimePolicyError(
            "runtime policy limits is missing: " + ", ".join(missing_limits)
        )
    for key in (
        "require_finite_metrics",
        "require_rss",
        "require_determinism",
        "require_within_configured_cache_budgets",
        "require_plugin_errors_zero",
        "require_ui_chrome_active",
    ):
        if type(limits[key]) is not bool:
            raise RuntimePolicyError(f"limits.{key} must be a boolean")
    max_bgm_slots = limits["max_bgm_slots"]
    if isinstance(max_bgm_slots, bool) or not isinstance(max_bgm_slots, int) or max_bgm_slots < 0:
        raise RuntimePolicyError("limits.max_bgm_slots must be a non-negative integer")
    for key in _NUMERIC_LIMIT_KEYS:
        value = limits[key]
        if value is None:
            continue
        number = _finite_number(value, label=f"limits.{key}")
        if number < 0:
            raise RuntimePolicyError(f"limits.{key} must be non-negative or null")
    max_over_budget = limits["max_over_budget_frames"]
    if max_over_budget is not None and (
        isinstance(max_over_budget, bool) or not isinstance(max_over_budget, int)
    ):
        raise RuntimePolicyError(
            "limits.max_over_budget_frames must be a non-negative integer or null"
        )
    frame_pair = (limits["frame_budget_ms"], limits["max_over_budget_frames"])
    if (frame_pair[0] is None) != (frame_pair[1] is None):
        raise RuntimePolicyError(
            "limits.frame_budget_ms and limits.max_over_budget_frames must both be null or set"
        )

    fingerprint = raw.get("calibrated_fingerprint")
    if fingerprint is not None:
        if not isinstance(fingerprint, dict) or not fingerprint:
            raise RuntimePolicyError("calibrated_fingerprint must be a non-empty mapping or null")
        for key, value in fingerprint.items():
            if not isinstance(key, str) or not isinstance(value, (str, int, float, bool)):
                raise RuntimePolicyError(
                    "calibrated_fingerprint values must be scalar strings/numbers/booleans"
                )
            if isinstance(value, float) and not math.isfinite(value):
                raise RuntimePolicyError(
                    "calibrated_fingerprint numeric values must be finite"
                )
    if any(limits[key] is not None for key in _NUMERIC_LIMIT_KEYS) and fingerprint is None:
        raise RuntimePolicyError(
            "numeric limits require a calibrated_fingerprint; use null limits "
            "for portable observational jobs"
        )

    normalized = json.loads(json.dumps(raw, ensure_ascii=False))
    digest = hashlib.sha256(raw_bytes).hexdigest()
    return normalized, digest


def percentile_nearest_rank(values: Iterable[float], percentile: float) -> float | None:
    """Return a deterministic nearest-rank percentile, or ``None`` if empty."""
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if not 0 < percentile <= 100:
        raise ValueError("percentile must be in (0, 100]")
    rank = max(1, math.ceil(percentile / 100 * len(ordered)))
    return ordered[rank - 1]


def frame_statistics(values: Iterable[float]) -> dict[str, float | int | None]:
    samples = [float(value) for value in values]
    if not samples:
        return {
            "samples": 0,
            "min_ms": None,
            "mean_ms": None,
            "p50_ms": None,
            "p95_ms": None,
            "p99_ms": None,
            "max_ms": None,
        }
    return {
        "samples": len(samples),
        "min_ms": min(samples),
        "mean_ms": sum(samples) / len(samples),
        "p50_ms": percentile_nearest_rank(samples, 50),
        "p95_ms": percentile_nearest_rank(samples, 95),
        "p99_ms": percentile_nearest_rank(samples, 99),
        "max_ms": max(samples),
    }


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


def _read_current_rss_bytes(pid: int) -> tuple[int | None, str]:
    """Best-effort current RSS without adding a mandatory runtime dependency."""
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        psutil = None  # type: ignore[assignment]
    if psutil is not None:
        try:
            return int(psutil.Process(pid).memory_info().rss), "psutil"
        except (psutil.Error, OSError, ValueError):
            pass
    if sys.platform.startswith("linux"):
        try:
            for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024, "proc-status-kib"
        except (OSError, ValueError, IndexError):
            return None, "unavailable"
    return None, "unavailable"


def _terminate_process_tree(proc: subprocess.Popen[Any]) -> None:
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        psutil = None  # type: ignore[assignment]
    terminated_with_psutil = False
    if psutil is not None:
        try:
            parent = psutil.Process(proc.pid)
            children = parent.children(recursive=True)
            for child in children:
                child.terminate()
            parent.terminate()
            _, alive = psutil.wait_procs([*children, parent], timeout=1.0)
            for process in alive:
                process.kill()
            terminated_with_psutil = True
        except (psutil.Error, OSError, ValueError):
            pass
    if not terminated_with_psutil:
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
        except OSError:
            pass
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            try:
                if os.name == "posix":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            except OSError:
                pass
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        pass


def _psutil_available() -> bool:
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        return False
    return psutil is not None


def _descendant_identities(pid: int) -> set[tuple[int, float]]:
    """Snapshot descendants using PID plus creation time to defeat PID reuse."""
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        return set()
    try:
        return {
            (child.pid, float(child.create_time()))
            for child in psutil.Process(pid).children(recursive=True)
        }
    except (psutil.Error, OSError, ValueError):
        return set()


def _contain_live_descendants(parent_pid: int) -> dict[str, Any]:
    """Inspect and terminate descendants while the cooperative leader waits."""
    payload: dict[str, Any] = {
        "schema": PROBE_SCHEMA,
        "status": "unavailable",
        "ok": False,
        "available": False,
        "backend": None,
        "detected": [],
        "terminated": [],
        "killed": [],
        "zombies": [],
        "survivors": [],
        "errors": [],
        "error": None,
    }
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        payload["error"] = "psutil is unavailable; install the runtime extra"
        return payload

    payload["available"] = True
    payload["backend"] = "controller-ready-ack-psutil"
    try:
        children = psutil.Process(parent_pid).children(recursive=True)
    except (psutil.Error, OSError, ValueError) as exc:
        payload["status"] = "snapshot_error"
        payload["error"] = f"{type(exc).__name__}: {exc}"
        return payload

    identities: dict[int, float | None] = {}
    for child in children:
        try:
            identities[child.pid] = float(child.create_time())
        except (psutil.Error, OSError, ValueError) as exc:
            identities[child.pid] = None
            payload["errors"].append(
                f"identity {child.pid}: {type(exc).__name__}: {exc}"
            )
    payload["detected"] = [
        {"pid": pid, "create_time": identities[pid]}
        for pid in sorted(identities)
    ]

    alive: list[Any] = []
    for child in reversed(children):
        try:
            child.terminate()
            payload["terminated"].append(child.pid)
            alive.append(child)
        except psutil.NoSuchProcess:
            continue
        except (psutil.Error, OSError, ValueError) as exc:
            payload["errors"].append(
                f"terminate {child.pid}: {type(exc).__name__}: {exc}"
            )
    if alive:
        _, alive = psutil.wait_procs(alive, timeout=0.5)
    for child in alive:
        try:
            child.kill()
            payload["killed"].append(child.pid)
        except psutil.NoSuchProcess:
            continue
        except (psutil.Error, OSError, ValueError) as exc:
            payload["errors"].append(
                f"kill {child.pid}: {type(exc).__name__}: {exc}"
            )
    if alive:
        _, alive = psutil.wait_procs(alive, timeout=0.5)
    survivors: list[int] = []
    for child in alive:
        try:
            if child.status() == psutil.STATUS_ZOMBIE:
                payload["zombies"].append(child.pid)
            else:
                survivors.append(child.pid)
        except psutil.NoSuchProcess:
            continue
        except (psutil.Error, OSError, ValueError) as exc:
            payload["errors"].append(
                f"status {child.pid}: {type(exc).__name__}: {exc}"
            )
            survivors.append(child.pid)
    payload["zombies"].sort()
    payload["survivors"] = sorted(survivors)

    if payload["detected"]:
        payload["status"] = "residual_descendants"
        payload["error"] = "the probe retained descendant processes at cleanup_ready"
    elif payload["errors"] or payload["survivors"]:
        payload["status"] = "cleanup_error"
        payload["error"] = "descendant cleanup did not complete cleanly"
    else:
        payload["status"] = "clean"
        payload["ok"] = True
    return payload


def _cleanup_residual_descendants(
    proc: subprocess.Popen[Any],
    tracked_identities: set[tuple[int, float]],
) -> list[int]:
    """Terminate descendants left behind after the probe leader exits."""
    leaked: set[int] = set()
    if os.name == "posix":
        try:
            os.killpg(proc.pid, 0)
        except OSError:
            pass
        else:
            # Give a helper that is already shutting down a short grace period
            # before classifying it as a leaked process.  The leader itself has
            # already been reaped, so a group that survives this recheck is a
            # real lifecycle violation.
            time.sleep(0.2)
            try:
                os.killpg(proc.pid, 0)
            except OSError:
                pass
            else:
                leaked.add(-proc.pid)  # negative id denotes the process group
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    time.sleep(0.05)
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
    if tracked_identities:
        try:
            import psutil  # type: ignore[import-not-found]
        except ImportError:
            psutil = None  # type: ignore[assignment]
        if psutil is not None:
            alive: list[Any] = []
            for pid, expected_create_time in tracked_identities:
                try:
                    process = psutil.Process(pid)
                    same_process = process.create_time() == expected_create_time
                    if (
                        same_process
                        and process.is_running()
                        and process.status() != psutil.STATUS_ZOMBIE
                    ):
                        leaked.add(pid)
                        process.terminate()
                        alive.append(process)
                except (psutil.Error, OSError, ValueError):
                    continue
            if alive:
                _, survivors = psutil.wait_procs(alive, timeout=0.5)
                for process in survivors:
                    try:
                        process.kill()
                    except (psutil.Error, OSError):
                        pass
    return sorted(leaked)


def _read_progress(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    events: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return events, errors
    last_seq = 0
    for index, line in enumerate(lines, 1):
        try:
            event = json.loads(line)
        except ValueError as exc:
            errors.append(f"progress line {index} is malformed: {exc}")
            continue
        if not isinstance(event, dict):
            errors.append(f"progress line {index} is not an object")
            continue
        if event.get("schema") != PROBE_SCHEMA:
            errors.append(f"progress line {index} has an unsupported schema")
            continue
        seq = event.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq <= last_seq:
            errors.append(f"progress line {index} has invalid/out-of-order seq")
            continue
        last_seq = seq
        events.append(event)
    return events, errors


def _progress_has_event(path: Path, target: str) -> bool:
    """Best-effort live check; final strict parsing still happens after exit."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if (
            isinstance(event, dict)
            and event.get("schema") == PROBE_SCHEMA
            and event.get("event") == target
        ):
            return True
    return False


def _probe_command(
    *,
    spec_path: Path,
    result_path: Path,
    progress_path: Path,
    cleanup_ack_path: Path,
    cleanup_ack_timeout_s: float,
    parent_start_ns: int,
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "world_gal_game.dev.runtime_probe",
        "--spec",
        str(spec_path),
        "--result",
        str(result_path),
        "--progress",
        str(progress_path),
        "--cleanup-ack",
        str(cleanup_ack_path),
        "--cleanup-ack-timeout-s",
        str(cleanup_ack_timeout_s),
        "--parent-start-ns",
        str(parent_start_ns),
    ]


def _run_probe(
    *,
    run_index: int,
    spec: dict[str, Any],
    run_dir: Path,
    timeout_s: float,
) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    spec_path = run_dir / "spec.json"
    result_path = run_dir / "result.json"
    progress_path = run_dir / "progress.ndjson"
    cleanup_ack_path = run_dir / "cleanup-ack.json"
    # Private callers may intentionally reuse a run directory.  Evidence from
    # a previous process must never satisfy the new process protocol.
    for stale_path in (result_path, progress_path, cleanup_ack_path):
        try:
            stale_path.unlink()
        except FileNotFoundError:
            pass
    _atomic_write_json(spec_path, spec)

    process: subprocess.Popen[Any] | None = None
    stdout_collector: _BoundedStreamCollector | None = None
    stderr_collector: _BoundedStreamCollector | None = None
    stdout_capture: _CapturedStream | None = None
    stderr_capture: _CapturedStream | None = None
    returncode: int | None = None
    timed_out = False
    spawn_error: str | None = None
    controller_error: str | None = None
    parent_samples: list[dict[str, Any]] = []
    tracked_descendants: set[tuple[int, float]] = set()
    leaked_descendants: list[int] = []
    cleanup_ack: dict[str, Any] | None = None
    cleanup_ack_sent = False
    parent_started_ns = time.monotonic_ns()
    command = _probe_command(
        spec_path=spec_path,
        result_path=result_path,
        progress_path=progress_path,
        cleanup_ack_path=cleanup_ack_path,
        cleanup_ack_timeout_s=max(0.1, min(30.0, timeout_s / 2)),
        parent_start_ns=parent_started_ns,
    )
    environment = os.environ.copy()
    environment.update(
        {
            "SDL_VIDEODRIVER": "dummy",
            "SDL_AUDIODRIVER": "dummy",
            "PYGAME_HIDE_SUPPORT_PROMPT": "1",
            "PYTHONHASHSEED": str(int(spec["seed"]) % (2**32)),
        }
    )
    environment.pop("WGG_DEV", None)
    environment.pop("WGG_STEAM", None)
    # Per-user plugins are outside the selected engine+pack subject. Keep each
    # trial hermetic while still loading engine-bundled and pack plugins.
    environment["WGG_USER_PLUGIN_ROOT"] = str(run_dir / "isolated-user-plugins")
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            shell=False,
            start_new_session=(os.name == "posix"),
            creationflags=creationflags,
            bufsize=0,
        )
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_collector = _BoundedStreamCollector(process.stdout)
        stderr_collector = _BoundedStreamCollector(process.stderr)
        stdout_collector.start()
        stderr_collector.start()
        deadline = time.monotonic() + timeout_s
        while True:
            if (
                not cleanup_ack_sent
                and _progress_has_event(progress_path, "cleanup_ready")
            ):
                cleanup_ack = _contain_live_descendants(process.pid)
                _atomic_write_json(cleanup_ack_path, cleanup_ack)
                cleanup_ack_sent = True
            tracked_descendants.update(_descendant_identities(process.pid))
            returncode = process.poll()
            if returncode is not None:
                break
            current_rss, provider = _read_current_rss_bytes(process.pid)
            if current_rss is not None:
                parent_samples.append(
                    {
                        "elapsed_ms": (time.monotonic_ns() - parent_started_ns) / 1_000_000,
                        "current_rss_bytes": current_rss,
                        "provider": provider,
                    }
                )
            if time.monotonic() >= deadline:
                timed_out = True
                _terminate_process_tree(process)
                returncode = process.poll()
                break
            time.sleep(0.01)
    except OSError as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        if process is None:
            spawn_error = error_text
        else:
            controller_error = error_text
        if process is not None and process.poll() is None:
            _terminate_process_tree(process)
        returncode = process.poll() if process is not None else None
    finally:
        # Unexpected controller exceptions (including cancellation) must not
        # strand the fresh child or any plugin-spawned descendants.
        if process is not None and process.poll() is None:
            _terminate_process_tree(process)
            returncode = process.poll()
        if process is not None and process.poll() is not None:
            leaked_descendants = _cleanup_residual_descendants(
                process,
                tracked_descendants,
            )
        collectors = [
            collector
            for collector in (stdout_collector, stderr_collector)
            if collector is not None
        ]
        captures = _finish_stream_collectors(collectors) if collectors else []
        if stdout_collector is not None:
            stdout_capture = captures.pop(0)
        if stderr_collector is not None:
            stderr_capture = captures.pop(0)

    empty_stream = _CapturedStream(
        bytes=0,
        sha256=hashlib.sha256(b"").hexdigest(),
        tail="",
        truncated=False,
        complete=True,
        termination="eof",
        error=None,
    )
    stdout = stdout_capture or empty_stream
    stderr = stderr_capture or empty_stream
    progress, progress_errors = _read_progress(progress_path)
    child: dict[str, Any] | None = None
    result_error: str | None = None
    if result_path.exists():
        try:
            loaded = json.loads(result_path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise TypeError("result is not an object")
            if loaded.get("schema") != PROBE_SCHEMA:
                raise ValueError("result schema is missing or unsupported")
            child = loaded
        except (OSError, TypeError, ValueError) as exc:
            result_error = f"malformed probe result: {exc}"
    elif not timed_out and spawn_error is None:
        result_error = "probe exited without a result artifact"

    required_events = (
        "probe_started",
        "app_ready",
        "first_frame",
        "shutdown_started",
        "shutdown_complete",
        "cleanup_ready",
        "cleanup_complete",
    )
    event_sequence = [
        event.get("event") for event in progress if isinstance(event.get("event"), str)
    ]
    event_names = set(event_sequence)
    missing_events = [event for event in required_events if event not in event_names]
    protocol_errors = list(progress_errors)
    if controller_error is not None:
        protocol_errors.append(f"controller protocol error: {controller_error}")
    for stream_name, capture in (("stdout", stdout), ("stderr", stderr)):
        if not capture.complete:
            protocol_errors.append(
                f"{stream_name} capture did not reach EOF: {capture.termination}"
            )
    child_cleanup = (
        child.get("process_cleanup")
        if isinstance(child, dict) and isinstance(child.get("process_cleanup"), dict)
        else None
    )
    if not cleanup_ack_sent:
        protocol_errors.append(
            "controller did not complete the cleanup ready/ack handshake"
        )
    elif child_cleanup != cleanup_ack:
        protocol_errors.append(
            "probe cleanup evidence does not match the controller acknowledgement"
        )
    if cleanup_ack is not None and cleanup_ack.get("ok") is not True:
        protocol_errors.append(
            "cleanup ready/ack failed: "
            + str(cleanup_ack.get("error") or cleanup_ack.get("status"))
        )
    if leaked_descendants:
        protocol_errors.append(
            "residual descendant process(es) were terminated: "
            + ", ".join(str(pid) for pid in leaked_descendants)
        )
    if missing_events:
        protocol_errors.append("missing milestone(s): " + ", ".join(missing_events))
    for event_name in required_events:
        if event_sequence.count(event_name) > 1:
            protocol_errors.append(f"duplicate required milestone: {event_name}")
    if not missing_events:
        required_positions = [event_sequence.index(name) for name in required_events]
        if required_positions != sorted(required_positions):
            protocol_errors.append("required milestones are out of order")
    if child is not None and child.get("ok") is True and returncode != 0:
        protocol_errors.append("probe reported ok but process exit was non-zero")

    ok = (
        spawn_error is None
        and not timed_out
        and returncode == 0
        and result_error is None
        and child is not None
        and child.get("ok") is True
        and not protocol_errors
    )
    status = (
        "spawn_error"
        if spawn_error is not None
        else "timeout"
        if timed_out
        else "protocol_error"
        if result_error is not None or protocol_errors
        else "child_error"
        if not ok
        else "ok"
    )
    return {
        "run_index": run_index,
        "mode": spec["mode"],
        "ok": ok,
        "status": status,
        "returncode": returncode,
        "signal": -returncode if isinstance(returncode, int) and returncode < 0 else None,
        "timed_out": timed_out,
        "spawn_error": spawn_error,
        "controller_error": controller_error,
        "result_error": result_error,
        "protocol_errors": protocol_errors,
        "progress": progress,
        "parent_memory": {
            "samples": parent_samples,
            "peak_observed_rss_bytes": max(
                (sample["current_rss_bytes"] for sample in parent_samples),
                default=None,
            ),
        },
        "stdout": stdout.to_dict(),
        "stderr": stderr.to_dict(),
        "process_cleanup": {
            "posix_process_group": os.name == "posix",
            "psutil_available": _psutil_available(),
            "controller_ack_sent": cleanup_ack_sent,
            "controller_ack": cleanup_ack,
            "tracked_descendants": [
                {"pid": pid, "create_time": create_time}
                for pid, create_time in sorted(tracked_descendants)
            ],
            "terminated_residuals": leaked_descendants,
            "backend_available": bool(
                cleanup_ack_sent
                and cleanup_ack is not None
                and cleanup_ack.get("available") is True
            ),
            "passed": bool(
                cleanup_ack_sent
                and cleanup_ack is not None
                and cleanup_ack.get("ok") is True
            ),
        },
        "child": child,
    }


def _git_revision(path: Path) -> dict[str, Any]:
    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(path), *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=5,
        )

    try:
        root_result = git("rev-parse", "--show-toplevel")
        if root_result.returncode != 0:
            return {"available": False, "root": None, "sha": None, "dirty": None}
        root = Path(root_result.stdout.strip()).resolve()
        sha_result = git("rev-parse", "HEAD")
        status_result = git("status", "--porcelain", "--untracked-files=normal")
        return {
            "available": sha_result.returncode == 0,
            "root": str(root),
            "sha": sha_result.stdout.strip() if sha_result.returncode == 0 else None,
            "dirty": bool(status_result.stdout.strip()) if status_result.returncode == 0 else None,
        }
    except (OSError, subprocess.SubprocessError):
        return {"available": False, "root": None, "sha": None, "dirty": None}


def _base_report(pack_root: Path, policy_path: Path | None) -> dict[str, Any]:
    engine_root = Path(__file__).resolve().parents[2]
    return {
        "schema": REPORT_SCHEMA,
        "ok": False,
        "status": "error",
        "generated_at_utc": _utc_now(),
        "duration_ms": None,
        "claim": {
            "scope": "unverified",
            "dummy_sdl": True,
            "statement": (
                "Fresh-process source-runtime calibration; not time-to-visible, "
                "GPU/VRAM, compositor, real audio, packaged-binary, or target-device evidence."
            ),
        },
        "subject": {
            "pack_root": str(pack_root),
            "pack_id": pack_root.name,
            "engine_version": engine_version,
            "engine_revision": _git_revision(engine_root),
            "pack_revision": _git_revision(pack_root),
        },
        "controller_environment": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "executable": sys.executable,
        },
        "policy": {
            "path": str(policy_path) if policy_path is not None else None,
            "sha256": None,
            "document": None,
        },
        "runs": [],
        "determinism": {
            "required": None,
            "ok": None,
            "reference_run": None,
            "compared_runs": [],
            "first_mismatch": None,
        },
        "summary": {},
        "checks": [],
        "failures": [],
        "warnings": [],
        "artifact": {"canonical_content_sha256": None},
    }


def _add_failure(report: dict[str, Any], code: str, message: str, **detail: Any) -> None:
    item = {"code": code, "message": message}
    item.update(detail)
    report["failures"].append(item)


def _metric_is_finite(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _metric_is_nonnegative_finite(value: Any) -> bool:
    return (
        _metric_is_finite(value)
        and float(value) >= 0
    )


def _json_sanitize(value: Any) -> Any:
    """Convert unexpected controller data into a last-resort JSON artifact."""
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_sanitize(item) for item in value]
    return repr(value)


def _fingerprint_matches(expected: dict[str, Any] | None, actual: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    if expected is None:
        return True, []
    mismatches: list[dict[str, Any]] = []
    for key, expected_value in expected.items():
        actual_value = actual.get(key)
        if type(actual_value) is not type(expected_value) or actual_value != expected_value:
            mismatches.append({"field": key, "expected": expected_value, "actual": actual_value})
    return not mismatches, mismatches


def _aggregate_runs(report: dict[str, Any], policy: dict[str, Any]) -> None:
    profile = policy["profile"]
    limits = policy["limits"]
    runs = report["runs"]
    cold_runs = runs[: profile["cold_runs"]]
    soak_runs = runs[: profile["soak_runs"]]

    for run in runs:
        if not run["ok"]:
            _add_failure(
                report,
                "probe_failed",
                f"runtime probe {run['run_index']} failed with {run['status']}",
                run_index=run["run_index"],
                probe_status=run["status"],
                returncode=run["returncode"],
            )

    cold_values: list[float] = []
    first_frame_values: list[float] = []
    current_rss_values: list[int] = []
    peak_rss_values: list[int] = []
    rss_growth_values: list[int] = []
    frame_values: list[float] = []
    image_cache_peak = 0
    sound_cache_peak = 0
    retained_audio_peak = 0
    chrome_cache_peak = 0
    bgm_slots_peak = 0
    cache_violations: list[dict[str, Any]] = []
    plugin_failures: list[dict[str, Any]] = []
    chrome_inactive_runs: list[int] = []

    for run in cold_runs:
        child = run.get("child") or {}
        timing = child.get("timing") or {}
        for destination, key in (
            (cold_values, "fresh_process_to_app_ready_ms"),
            (first_frame_values, "fresh_process_to_first_frame_ms"),
        ):
            value = timing.get(key)
            if _metric_is_nonnegative_finite(value):
                destination.append(float(value))
    for run in runs:
        child = run.get("child") or {}
        memory = child.get("memory") or {}
        for sample in memory.get("samples") or []:
            current = sample.get("current_rss_bytes")
            peak = sample.get("peak_rss_bytes")
            if isinstance(current, int) and current >= 0:
                current_rss_values.append(current)
            if isinstance(peak, int) and peak >= 0:
                peak_rss_values.append(peak)
        parent_peak = (run.get("parent_memory") or {}).get("peak_observed_rss_bytes")
        if isinstance(parent_peak, int) and parent_peak >= 0:
            current_rss_values.append(parent_peak)
            # A sampled current RSS is also a lower bound for the process's
            # true peak, including late phases between child checkpoints.
            peak_rss_values.append(parent_peak)
        growth = memory.get("first_frame_to_final_growth_bytes")
        if isinstance(growth, int):
            rss_growth_values.append(growth)

        cache = child.get("cache") or {}
        peak_cache = cache.get("peak") or {}
        image_cache_peak = max(image_cache_peak, int(peak_cache.get("image_bytes", 0) or 0))
        sound_cache_peak = max(sound_cache_peak, int(peak_cache.get("sound_bytes", 0) or 0))
        retained_audio_peak = max(
            retained_audio_peak,
            int(peak_cache.get("retained_audio_bytes", 0) or 0),
        )
        bgm_slots_peak = max(bgm_slots_peak, int(peak_cache.get("bgm_slots", 0) or 0))
        chrome_cache_peak = max(
            chrome_cache_peak,
            int((cache.get("chrome_peak") or {}).get("bytes", 0) or 0),
        )
        for violation in cache.get("violations") or []:
            cache_violations.append({"run_index": run["run_index"], **violation})
        plugins = child.get("plugins") or {}
        if plugins.get("failed") or plugins.get("runtime_errors"):
            plugin_failures.append(
                {
                    "run_index": run["run_index"],
                    "failed": plugins.get("failed") or [],
                    "runtime_errors": plugins.get("runtime_errors") or [],
                }
            )
        chrome = child.get("ui_chrome") or {}
        if limits["require_ui_chrome_active"] and chrome.get("active") is not True:
            chrome_inactive_runs.append(run["run_index"])

    for run in soak_runs:
        child = run.get("child") or {}
        actual_views = [view.get("name") for view in child.get("views") or []]
        if actual_views != profile["views"]:
            _add_failure(
                report,
                "semantic_views_incomplete",
                f"probe {run['run_index']} did not complete the declared views",
                run_index=run["run_index"],
                expected=profile["views"],
                actual=actual_views,
            )
        for view in child.get("views") or []:
            values = view.get("frame_times_ms") or []
            if len(values) != profile["frames_per_view"]:
                _add_failure(
                    report,
                    "frame_sample_count",
                    f"probe {run['run_index']} view {view.get('name')} has the wrong frame count",
                    run_index=run["run_index"],
                    view=view.get("name"),
                    expected=profile["frames_per_view"],
                    actual=len(values),
                )
            frame_values.extend(
                float(value)
                for value in values
                if _metric_is_nonnegative_finite(value)
            )

    reference_hashes: list[tuple[str, str]] | None = None
    reference_run: int | None = None
    compared_runs: list[int] = []
    mismatch: dict[str, Any] | None = None
    for run in soak_runs:
        child = run.get("child") or {}
        hashes = [
            (str(item.get("label")), str(item.get("sha256")))
            for item in child.get("semantic_checkpoints") or []
        ]
        if reference_hashes is None:
            reference_hashes = hashes
            reference_run = run["run_index"]
            continue
        compared_runs.append(run["run_index"])
        if hashes != reference_hashes and mismatch is None:
            mismatch_index = next(
                (
                    index
                    for index, pair in enumerate(hashes)
                    if index >= len(reference_hashes) or pair != reference_hashes[index]
                ),
                min(len(hashes), len(reference_hashes)),
            )
            mismatch = {
                "run_index": run["run_index"],
                "checkpoint_index": mismatch_index,
                "expected": (
                    reference_hashes[mismatch_index]
                    if mismatch_index < len(reference_hashes)
                    else None
                ),
                "actual": hashes[mismatch_index] if mismatch_index < len(hashes) else None,
            }
    determinism_ok = reference_hashes is not None and mismatch is None
    report["determinism"] = {
        "required": limits["require_determinism"],
        "ok": determinism_ok,
        "reference_run": reference_run,
        "compared_runs": compared_runs,
        "first_mismatch": mismatch,
    }
    if limits["require_determinism"] and not determinism_ok:
        _add_failure(
            report,
            "determinism_mismatch",
            "fresh-process semantic checkpoints are not deterministic",
            mismatch=mismatch,
        )

    frame_stats = frame_statistics(frame_values)
    summary = {
        "fresh_process_startup": {
            "samples": len(cold_values),
            "app_ready_p95_ms": percentile_nearest_rank(cold_values, 95),
            "app_ready_values_ms": cold_values,
            "first_frame_p95_ms": percentile_nearest_rank(first_frame_values, 95),
            "first_frame_values_ms": first_frame_values,
        },
        "frame_time": frame_stats,
        "memory": {
            "current_rss_max_bytes": max(current_rss_values, default=None),
            "peak_rss_max_bytes": max(peak_rss_values, default=None),
            "first_frame_to_final_growth_max_bytes": max(rss_growth_values, default=None),
            "growth_values_bytes": rss_growth_values,
        },
        "cache": {
            "image_peak_bytes": image_cache_peak,
            "sound_peak_bytes": sound_cache_peak,
            "retained_audio_peak_bytes": retained_audio_peak,
            "chrome_peak_bytes": chrome_cache_peak,
            "bgm_slots_peak": bgm_slots_peak,
            "violations": cache_violations,
        },
        "plugins": {"violating_runs": plugin_failures},
        "ui_chrome": {"inactive_runs": chrome_inactive_runs},
    }
    report["summary"] = summary

    required_metrics = {
        "cold_start_p95_ms": summary["fresh_process_startup"]["app_ready_p95_ms"],
        "first_frame_p95_ms": summary["fresh_process_startup"]["first_frame_p95_ms"],
        "frame_p95_ms": frame_stats["p95_ms"],
        "frame_p99_ms": frame_stats["p99_ms"],
    }
    if limits["require_finite_metrics"]:
        missing = [
            key
            for key, value in required_metrics.items()
            if not _metric_is_nonnegative_finite(value)
        ]
        if missing:
            _add_failure(
                report,
                "metrics_unavailable",
                "required timing metrics are missing or non-finite",
                metrics=missing,
            )
    rss_summary = summary["memory"]
    if limits["require_rss"] and any(
        not isinstance(rss_summary[key], int)
        for key in ("current_rss_max_bytes", "peak_rss_max_bytes")
    ):
        _add_failure(report, "rss_unavailable", "required current/peak RSS telemetry is unavailable")
    if limits["require_within_configured_cache_budgets"] and cache_violations:
        _add_failure(
            report,
            "cache_budget_exceeded",
            "one or more runtime caches exceeded their configured budget",
            violations=cache_violations,
        )
    if bgm_slots_peak > limits["max_bgm_slots"]:
        _add_failure(
            report,
            "bgm_slot_budget_exceeded",
            "live BGM slots exceeded policy",
            actual=bgm_slots_peak,
            limit=limits["max_bgm_slots"],
        )
    if limits["require_plugin_errors_zero"] and plugin_failures:
        _add_failure(
            report,
            "plugin_errors",
            "plugin load/runtime errors occurred during a probe",
            runs=plugin_failures,
        )
    if chrome_inactive_runs:
        _add_failure(
            report,
            "ui_chrome_inactive",
            "the policy requires an active pack-owned UI chrome renderer",
            runs=chrome_inactive_runs,
        )

    actual_fingerprint: dict[str, Any] = {}
    first_child = next((run.get("child") for run in runs if run.get("child")), None)
    if isinstance(first_child, dict):
        actual_fingerprint = dict(first_child.get("fingerprint") or {})
    expected_fingerprint = policy.get("calibrated_fingerprint")
    fingerprint_ok, fingerprint_mismatches = _fingerprint_matches(
        expected_fingerprint,
        actual_fingerprint,
    )
    report["policy"]["fingerprint"] = {
        "expected": expected_fingerprint,
        "actual": actual_fingerprint,
        "matches": fingerprint_ok,
        "mismatches": fingerprint_mismatches,
    }
    numeric_limits_present = any(limits[key] is not None for key in _NUMERIC_LIMIT_KEYS)
    if expected_fingerprint is not None and not fingerprint_ok:
        _add_failure(
            report,
            "calibrated_fingerprint_mismatch",
            "numeric policy is calibrated for a different runtime fingerprint",
            mismatches=fingerprint_mismatches,
        )

    numeric_actuals = {
        "cold_start_p95_ms": summary["fresh_process_startup"]["app_ready_p95_ms"],
        "first_frame_p95_ms": summary["fresh_process_startup"]["first_frame_p95_ms"],
        "current_rss_max_bytes": rss_summary["current_rss_max_bytes"],
        "peak_rss_max_bytes": rss_summary["peak_rss_max_bytes"],
        "rss_growth_max_bytes": rss_summary["first_frame_to_final_growth_max_bytes"],
        "frame_p95_ms": frame_stats["p95_ms"],
        "frame_p99_ms": frame_stats["p99_ms"],
    }
    for key, actual in numeric_actuals.items():
        limit = limits[key]
        if limit is None:
            report["checks"].append(
                {"id": key, "status": "observed", "actual": actual, "limit": None, "ok": None}
            )
            continue
        valid_actual = (
            _metric_is_finite(actual)
            if key == "rss_growth_max_bytes"
            else _metric_is_nonnegative_finite(actual)
        )
        ok = fingerprint_ok and valid_actual and float(actual) <= float(limit)
        report["checks"].append(
            {"id": key, "status": "pass" if ok else "fail", "actual": actual, "limit": limit, "ok": ok}
        )
        if not ok and fingerprint_ok:
            _add_failure(
                report,
                "numeric_limit_exceeded",
                f"{key} exceeded runtime policy",
                metric=key,
                actual=actual,
                limit=limit,
            )
    frame_budget = limits["frame_budget_ms"]
    over_budget_limit = limits["max_over_budget_frames"]
    if frame_budget is None:
        report["checks"].append(
            {
                "id": "over_budget_frames",
                "status": "observed",
                "actual": None,
                "limit": None,
                "frame_budget_ms": None,
                "ok": None,
            }
        )
    else:
        count = sum(1 for value in frame_values if value > float(frame_budget))
        ok = fingerprint_ok and count <= int(over_budget_limit)
        report["checks"].append(
            {
                "id": "over_budget_frames",
                "status": "pass" if ok else "fail",
                "actual": count,
                "limit": int(over_budget_limit),
                "frame_budget_ms": frame_budget,
                "ok": ok,
            }
        )
        if not ok and fingerprint_ok:
            _add_failure(
                report,
                "frame_budget_exceeded",
                "too many measured frames exceeded the configured frame budget",
                actual=count,
                limit=int(over_budget_limit),
                frame_budget_ms=frame_budget,
            )

    report["claim"]["scope"] = policy["claim_scope"]
    report["claim"]["numeric_limits_enforced"] = numeric_limits_present and fingerprint_ok


def run_runtime_check(
    pack_root: Path,
    *,
    policy_path: Path | None,
    report_path: Path,
) -> dict[str, Any]:
    """Run the independent reliability gate and always attempt a JSON report."""
    started_ns = time.monotonic_ns()
    pack_root = pack_root.expanduser().resolve()
    policy_path = policy_path.expanduser().resolve() if policy_path is not None else None
    report_path = report_path.expanduser().resolve()
    report = _base_report(pack_root, policy_path)
    try:
        if not (pack_root / "content").is_dir():
            raise RuntimePolicyError(f"pack has no content directory: {pack_root}")
        if policy_path is None:
            raise RuntimePolicyError(
                "runtime policy is required; pass --policy or add content/runtime_reliability.yaml"
            )
        policy, policy_sha = load_runtime_policy(policy_path)
        report["policy"].update(
            {"sha256": policy_sha, "document": policy}
        )
        profile = policy["profile"]
        run_count = max(profile["cold_runs"], profile["soak_runs"])
        with tempfile.TemporaryDirectory(prefix="wgg-runtime-reliability-") as temp_root_raw:
            temp_root = Path(temp_root_raw)
            for index in range(run_count):
                mode = "soak" if index < profile["soak_runs"] else "startup_only"
                run_dir = temp_root / f"run-{index + 1:02d}"
                spec = {
                    "schema": PROBE_SCHEMA,
                    "mode": mode,
                    "pack_root": str(pack_root),
                    "writable_root": str(run_dir / "user-data"),
                    "screen_size": profile["screen_size"],
                    "seed": profile["seed"],
                    "fixed_dt_ms": profile["fixed_dt_ms"],
                    "warmup_frames": profile["warmup_frames"],
                    "frames_per_view": profile["frames_per_view"],
                    "views": profile["views"],
                    "rss_sample_interval_frames": profile["rss_sample_interval_frames"],
                }
                report["runs"].append(
                    _run_probe(
                        run_index=index + 1,
                        spec=spec,
                        run_dir=run_dir,
                        timeout_s=float(profile["timeout_s"]),
                    )
                )
        _aggregate_runs(report, policy)
    except RuntimePolicyError as exc:
        _add_failure(report, "policy_error", str(exc))
    except BaseException as exc:  # noqa: BLE001 - artifact must survive controller bugs
        _add_failure(
            report,
            "controller_error",
            f"{type(exc).__name__}: {exc}",
        )

    report["duration_ms"] = (time.monotonic_ns() - started_ns) / 1_000_000
    if report["failures"]:
        statuses = {run.get("status") for run in report["runs"]}
        report["status"] = (
            "timeout"
            if "timeout" in statuses
            else "error"
            if any(status in statuses for status in ("spawn_error", "protocol_error", "child_error"))
            or any(item["code"] in ("policy_error", "controller_error") for item in report["failures"])
            else "fail"
        )
        report["ok"] = False
    else:
        report["status"] = "pass"
        report["ok"] = True
    digest_payload = dict(report)
    digest_payload["artifact"] = {"canonical_content_sha256": None}
    try:
        canonical = json.dumps(
            digest_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _add_failure(
            report,
            "artifact_serialization_error",
            f"report required last-resort JSON sanitization: {exc}",
        )
        report["ok"] = False
        report["status"] = "error"
        report = _json_sanitize(report)
        digest_payload = dict(report)
        digest_payload["artifact"] = {"canonical_content_sha256": None}
        canonical = json.dumps(
            digest_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    report["artifact"]["canonical_content_sha256"] = hashlib.sha256(canonical).hexdigest()
    try:
        _atomic_write_json(report_path, report)
    except (OSError, TypeError, ValueError) as exc:
        report["ok"] = False
        report["status"] = "error"
        _add_failure(report, "artifact_write_error", f"cannot write report {report_path}: {exc}")
    return report


__all__ = [
    "POLICY_SCHEMA_VERSION",
    "REPORT_SCHEMA",
    "RuntimePolicyError",
    "frame_statistics",
    "load_runtime_policy",
    "percentile_nearest_rank",
    "run_runtime_check",
]
