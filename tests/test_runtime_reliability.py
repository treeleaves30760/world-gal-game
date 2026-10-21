"""Focused contract tests for the independent runtime reliability gate."""

from __future__ import annotations

import hashlib
import json
import math
import os
import signal
import sys
import time
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from world_gal_game.dev import runtime_reliability
from world_gal_game.dev.runtime_reliability import (
    REPORT_SCHEMA,
    RuntimePolicyError,
    frame_statistics,
    load_runtime_policy,
    percentile_nearest_rank,
    run_runtime_check,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_PACK = REPO_ROOT / "games" / "demo_pack"


def _portable_policy() -> dict[str, object]:
    """Small policy that enforces portable invariants, not host timings."""
    return {
        "schema_version": 1,
        "name": "pytest-dummy-sdl-calibration",
        "claim_scope": "dummy-sdl-source-runtime-calibration",
        "notes": "Host-sensitive numeric limits are evidence only.",
        "profile": {
            "screen_size": [640, 360],
            "seed": 731,
            "fixed_dt_ms": 16.6666666667,
            "warmup_frames": 0,
            "frames_per_view": 1,
            "views": ["title", "new_game"],
            "cold_runs": 1,
            "soak_runs": 2,
            "rss_sample_interval_frames": 1,
            "timeout_s": 60,
        },
        "limits": {
            "require_finite_metrics": True,
            "require_rss": True,
            "require_determinism": True,
            "require_within_configured_cache_budgets": True,
            "require_plugin_errors_zero": True,
            "require_ui_chrome_active": False,
            "max_bgm_slots": 2,
            "cold_start_p95_ms": None,
            "first_frame_p95_ms": None,
            "current_rss_max_bytes": None,
            "peak_rss_max_bytes": None,
            "rss_growth_max_bytes": None,
            "frame_p95_ms": None,
            "frame_p99_ms": None,
            "frame_budget_ms": None,
            "max_over_budget_frames": None,
        },
        "calibrated_fingerprint": None,
    }


def _write_policy(path: Path, policy: dict[str, object]) -> Path:
    path.write_text(
        yaml.safe_dump(policy, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def _assert_canonical_digest(report: dict[str, object]) -> None:
    expected = report["artifact"]["canonical_content_sha256"]  # type: ignore[index]
    digest_payload = deepcopy(report)
    digest_payload["artifact"] = {"canonical_content_sha256": None}
    canonical = json.dumps(
        digest_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert expected == hashlib.sha256(canonical).hexdigest()


def test_runtime_policy_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    path = tmp_path / "runtime.yaml"
    document = yaml.safe_dump(
        _portable_policy(),
        allow_unicode=True,
        sort_keys=False,
    )
    path.write_text(
        document.replace(
            "name: pytest-dummy-sdl-calibration\n",
            "name: first\nname: second\n",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimePolicyError, match="duplicate key 'name'"):
        load_runtime_policy(path)


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("unknown_top_level", "unknown field"),
        ("missing_profile_field", "profile is missing"),
        ("unsupported_view", "unsupported v1 view"),
        ("boolean_numeric_limit", "must be a number"),
        ("unpaired_frame_budget", "must both be null or set"),
        ("non_finite_limit", "must be finite"),
        ("non_finite_fingerprint", "calibrated_fingerprint"),
        ("numeric_without_fingerprint", "numeric limits require"),
        ("fractional_frame_count", "non-negative integer"),
        ("non_string_notes", "notes must be"),
    ],
)
def test_runtime_policy_validation_is_strict(
    tmp_path: Path,
    case: str,
    message: str,
) -> None:
    policy = _portable_policy()
    profile = policy["profile"]
    limits = policy["limits"]
    assert isinstance(profile, dict)
    assert isinstance(limits, dict)

    if case == "unknown_top_level":
        policy["typo"] = True
    elif case == "missing_profile_field":
        profile.pop("seed")
    elif case == "unsupported_view":
        profile["views"] = ["title", "credits"]
    elif case == "boolean_numeric_limit":
        limits["frame_p95_ms"] = True
    elif case == "unpaired_frame_budget":
        limits["frame_budget_ms"] = 16.7
    elif case == "non_finite_limit":
        limits["peak_rss_max_bytes"] = math.inf
    elif case == "non_finite_fingerprint":
        policy["calibrated_fingerprint"] = {"screen_scale": math.nan}
    elif case == "numeric_without_fingerprint":
        limits["frame_p95_ms"] = 20.0
    elif case == "fractional_frame_count":
        limits["frame_budget_ms"] = 16.7
        limits["max_over_budget_frames"] = 1.5
        policy["calibrated_fingerprint"] = {"system": "test"}
    elif case == "non_string_notes":
        policy["notes"] = {"unexpected": "mapping"}
    else:  # pragma: no cover - parameter table and branch must stay in sync
        raise AssertionError(case)

    path = _write_policy(tmp_path / f"{case}.yaml", policy)
    with pytest.raises(RuntimePolicyError, match=message):
        load_runtime_policy(path)


def test_runtime_policy_load_returns_normalized_document_and_source_digest(
    tmp_path: Path,
) -> None:
    path = _write_policy(tmp_path / "policy.yaml", _portable_policy())

    document, digest = load_runtime_policy(path)

    assert document == _portable_policy()
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()


def test_nearest_rank_percentiles_and_frame_statistics_are_exact() -> None:
    descending = list(range(20, 0, -1))

    assert percentile_nearest_rank([], 95) is None
    assert percentile_nearest_rank(descending, 0.1) == 1
    assert percentile_nearest_rank(descending, 50) == 10
    assert percentile_nearest_rank(descending, 95) == 19
    assert percentile_nearest_rank(descending, 99) == 20
    assert percentile_nearest_rank(descending, 100) == 20
    with pytest.raises(ValueError, match="percentile"):
        percentile_nearest_rank(descending, 0)
    with pytest.raises(ValueError, match="percentile"):
        percentile_nearest_rank(descending, 101)

    assert frame_statistics([4.0, 1.0, 3.0, 2.0]) == {
        "samples": 4,
        "min_ms": 1.0,
        "mean_ms": 2.5,
        "p50_ms": 2.0,
        "p95_ms": 4.0,
        "p99_ms": 4.0,
        "max_ms": 4.0,
    }


def test_calibrated_fingerprint_matching_is_type_strict() -> None:
    match, mismatches = runtime_reliability._fingerprint_matches(
        {"mixer_initialized": 1, "screen_width": True},
        {"mixer_initialized": True, "screen_width": 1},
    )

    assert match is False
    assert [item["field"] for item in mismatches] == [
        "mixer_initialized",
        "screen_width",
    ]


def test_negative_frame_metric_cannot_satisfy_a_non_negative_max_policy() -> None:
    policy = _portable_policy()
    limits = policy["limits"]
    assert isinstance(limits, dict)
    limits["frame_p95_ms"] = 10.0
    policy["calibrated_fingerprint"] = {"system": "synthetic-test"}

    def successful_run(run_index: int) -> dict[str, object]:
        child = {
            "timing": {
                "fresh_process_to_app_ready_ms": 1.0,
                "fresh_process_to_first_frame_ms": 2.0,
            },
            "memory": {
                "samples": [
                    {
                        "current_rss_bytes": 1_000,
                        "peak_rss_bytes": 1_200,
                    }
                ],
                "first_frame_to_final_growth_bytes": -100,
            },
            "cache": {"peak": {}, "chrome_peak": {}, "violations": []},
            "plugins": {"failed": [], "runtime_errors": []},
            "ui_chrome": {"active": False},
            "views": [
                {"name": "title", "frame_times_ms": [-1.0]},
                {"name": "new_game", "frame_times_ms": [-1.0]},
            ],
            "semantic_checkpoints": [
                {"label": "title:complete", "sha256": "title"},
                {"label": "new_game:complete", "sha256": "new-game"},
            ],
            "fingerprint": {"system": "synthetic-test"},
        }
        return {
            "run_index": run_index,
            "ok": True,
            "status": "ok",
            "returncode": 0,
            "parent_memory": {"peak_observed_rss_bytes": 1_000},
            "child": child,
        }

    report = {
        "runs": [successful_run(1), successful_run(2)],
        "determinism": {},
        "summary": {},
        "checks": [],
        "failures": [],
        "policy": {},
        "claim": {},
    }

    runtime_reliability._aggregate_runs(report, policy)

    check = next(item for item in report["checks"] if item["id"] == "frame_p95_ms")
    assert check == {
        "id": "frame_p95_ms",
        "status": "fail",
        "actual": None,
        "limit": 10.0,
        "ok": False,
    }
    assert any(
        failure["code"] == "metrics_unavailable"
        and "frame_p95_ms" in failure["metrics"]
        for failure in report["failures"]
    )
    assert any(
        failure.get("metric") == "frame_p95_ms"
        or "frame_p95_ms" in failure.get("metrics", [])
        for failure in report["failures"]
    )
    # Negative RSS growth is valid evidence: memory can shrink during the soak.
    assert report["summary"]["memory"]["first_frame_to_final_growth_max_bytes"] == -100


def test_missing_policy_still_writes_a_machine_readable_report(tmp_path: Path) -> None:
    pack_root = tmp_path / "pack"
    (pack_root / "content").mkdir(parents=True)
    report_path = tmp_path / "nested" / "runtime-report.json"

    report = run_runtime_check(
        pack_root,
        policy_path=None,
        report_path=report_path,
    )

    assert report_path.is_file()
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    assert report["schema"] == REPORT_SCHEMA
    assert report["ok"] is False
    assert report["status"] == "error"
    assert report["runs"] == []
    assert [failure["code"] for failure in report["failures"]] == ["policy_error"]
    assert "runtime policy is required" in report["failures"][0]["message"]
    _assert_canonical_digest(report)


def test_protocol_failure_is_classified_and_keeps_process_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "protocol-failure"
    run_dir.mkdir()
    (run_dir / "result.json").write_text(
        json.dumps({"schema": runtime_reliability.PROBE_SCHEMA, "ok": True}),
        encoding="utf-8",
    )
    (run_dir / "progress.ndjson").write_text(
        "\n".join(
            json.dumps(
                {
                    "schema": runtime_reliability.PROBE_SCHEMA,
                    "seq": index,
                    "event": event,
                }
            )
            for index, event in enumerate(
                ("probe_started", "app_ready", "first_frame"),
                1,
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        runtime_reliability,
        "_probe_command",
        lambda **_kwargs: [sys.executable, "-c", "pass"],
    )

    run = runtime_reliability._run_probe(
        run_index=1,
        spec={"mode": "soak", "seed": 731},
        run_dir=run_dir,
        timeout_s=5,
    )

    assert run["ok"] is False
    assert run["status"] == "protocol_error"
    assert run["returncode"] == 0
    assert run["timed_out"] is False
    assert run["child"] is None
    assert run["result_error"] == "probe exited without a result artifact"
    assert any("missing milestone(s)" in error for error in run["protocol_errors"])
    assert run["stdout"]["bytes"] == 0
    assert run["stderr"]["bytes"] == 0
    assert len(run["stdout"]["sha256"]) == 64
    assert len(run["stderr"]["sha256"]) == 64


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_successful_probe_leader_cannot_leave_a_residual_descendant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descendant_pid_path = tmp_path / "descendant.pid"
    worker = """
import json
from pathlib import Path
import subprocess
import sys

result_path = Path(sys.argv[1])
progress_path = Path(sys.argv[2])
descendant_pid_path = Path(sys.argv[3])
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
descendant_pid_path.write_text(str(child.pid), encoding="utf-8")
events = [
    {"schema": "world-gal-game.runtime-probe.v1", "seq": 1, "event": "probe_started"},
    {"schema": "world-gal-game.runtime-probe.v1", "seq": 2, "event": "app_ready"},
    {"schema": "world-gal-game.runtime-probe.v1", "seq": 3, "event": "first_frame"},
]
progress_path.write_text(
    "".join(json.dumps(event) + "\\n" for event in events),
    encoding="utf-8",
)
result_path.write_text(
    json.dumps({"schema": "world-gal-game.runtime-probe.v1", "ok": True}),
    encoding="utf-8",
)
"""

    monkeypatch.setattr(
        runtime_reliability,
        "_probe_command",
        lambda **paths: [
            sys.executable,
            "-c",
            worker,
            str(paths["result_path"]),
            str(paths["progress_path"]),
            str(descendant_pid_path),
        ],
    )

    descendant_pid: int | None = None
    try:
        run = runtime_reliability._run_probe(
            run_index=1,
            spec={"mode": "startup_only", "seed": 731},
            run_dir=tmp_path / "residual-descendant",
            timeout_s=5,
        )
        descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))

        assert run["returncode"] == 0
        assert run["timed_out"] is False
        assert run["child"]["ok"] is True
        assert run["result_error"] is None
        assert run["status"] == "protocol_error"
        assert run["ok"] is False
        assert any(
            "residual descendant process(es) were terminated" in error
            for error in run["protocol_errors"]
        )

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(descendant_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.01)
        else:
            pytest.fail(f"residual descendant {descendant_pid} is still alive")
    finally:
        if descendant_pid is None and descendant_pid_path.exists():
            descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))
        if descendant_pid is not None:
            try:
                os.kill(descendant_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX sessions")
def test_escaped_descendant_holding_pipes_fails_closed_without_long_wait(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descendant_pid_path = tmp_path / "escaped-descendant.pid"
    worker = """
import json
from pathlib import Path
import subprocess
import sys

result_path = Path(sys.argv[1])
progress_path = Path(sys.argv[2])
descendant_pid_path = Path(sys.argv[3])
child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(30)"],
    start_new_session=True,
)
descendant_pid_path.write_text(str(child.pid), encoding="utf-8")
events = [
    {"schema": "world-gal-game.runtime-probe.v1", "seq": 1, "event": "probe_started"},
    {"schema": "world-gal-game.runtime-probe.v1", "seq": 2, "event": "app_ready"},
    {"schema": "world-gal-game.runtime-probe.v1", "seq": 3, "event": "first_frame"},
]
progress_path.write_text(
    "".join(json.dumps(event) + "\\n" for event in events),
    encoding="utf-8",
)
result_path.write_text(
    json.dumps({"schema": "world-gal-game.runtime-probe.v1", "ok": True}),
    encoding="utf-8",
)
"""
    monkeypatch.setattr(
        runtime_reliability,
        "_probe_command",
        lambda **paths: [
            sys.executable,
            "-c",
            worker,
            str(paths["result_path"]),
            str(paths["progress_path"]),
            str(descendant_pid_path),
        ],
    )
    # Exercise the fail-closed pipe evidence independently of optional psutil
    # tracking. The child escapes the probe's process group on purpose.
    monkeypatch.setattr(runtime_reliability, "_descendant_identities", lambda _pid: set())

    descendant_pid: int | None = None
    started = time.monotonic()
    try:
        run = runtime_reliability._run_probe(
            run_index=1,
            spec={"mode": "startup_only", "seed": 731},
            run_dir=tmp_path / "escaped-descendant",
            timeout_s=5,
        )
        elapsed = time.monotonic() - started
        descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))

        assert elapsed < 3
        assert run["returncode"] == 0
        assert run["status"] == "protocol_error"
        assert run["ok"] is False
        assert run["stdout"]["complete"] is False
        assert run["stderr"]["complete"] is False
        assert run["stdout"]["termination"] == "forced_close"
        assert run["stderr"]["termination"] == "forced_close"
        assert any(
            "capture did not reach EOF" in error
            for error in run["protocol_errors"]
        )
    finally:
        if descendant_pid is None and descendant_pid_path.exists():
            descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))
        if descendant_pid is not None:
            try:
                os.kill(descendant_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX sessions")
def test_escaped_devnull_descendant_without_cleanup_handshake_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descendant_pid_path = tmp_path / "escaped-devnull-descendant.pid"
    worker = """
import json
from pathlib import Path
import subprocess
import sys

result_path = Path(sys.argv[1])
progress_path = Path(sys.argv[2])
descendant_pid_path = Path(sys.argv[3])
child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(30)"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    start_new_session=True,
)
descendant_pid_path.write_text(str(child.pid), encoding="utf-8")
events = [
    {"schema": "world-gal-game.runtime-probe.v1", "seq": 1, "event": "probe_started"},
    {"schema": "world-gal-game.runtime-probe.v1", "seq": 2, "event": "app_ready"},
    {"schema": "world-gal-game.runtime-probe.v1", "seq": 3, "event": "first_frame"},
]
progress_path.write_text(
    "".join(json.dumps(event) + "\\n" for event in events),
    encoding="utf-8",
)
result_path.write_text(
    json.dumps({"schema": "world-gal-game.runtime-probe.v1", "ok": True}),
    encoding="utf-8",
)
"""
    monkeypatch.setattr(
        runtime_reliability,
        "_probe_command",
        lambda **paths: [
            sys.executable,
            "-c",
            worker,
            str(paths["result_path"]),
            str(paths["progress_path"]),
            str(descendant_pid_path),
        ],
    )
    monkeypatch.setattr(runtime_reliability, "_descendant_identities", lambda _pid: set())

    descendant_pid: int | None = None
    try:
        run = runtime_reliability._run_probe(
            run_index=1,
            spec={"mode": "startup_only", "seed": 731},
            run_dir=tmp_path / "escaped-devnull-descendant",
            timeout_s=5,
        )
        descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))

        assert run["returncode"] == 0
        assert run["stdout"]["complete"] is True
        assert run["stderr"]["complete"] is True
        assert run["process_cleanup"]["backend_available"] is False
        assert run["process_cleanup"]["passed"] is False
        assert run["process_cleanup"]["controller_ack_sent"] is False
        assert run["status"] == "protocol_error"
        assert run["ok"] is False
        assert any(
            "cleanup ready/ack handshake" in error
            for error in run["protocol_errors"]
        )
    finally:
        if descendant_pid is None and descendant_pid_path.exists():
            descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))
        if descendant_pid is not None:
            try:
                os.kill(descendant_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX sessions")
def test_cleanup_ready_ack_catches_escaped_devnull_descendant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("psutil", reason="ready/ack containment uses the runtime extra")
    descendant_pid_path = tmp_path / "handshake-descendant.pid"
    worker = """
import json
from pathlib import Path
import subprocess
import sys
import time

result_path = Path(sys.argv[1])
progress_path = Path(sys.argv[2])
cleanup_ack_path = Path(sys.argv[3])
descendant_pid_path = Path(sys.argv[4])
child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(30)"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    start_new_session=True,
)
descendant_pid_path.write_text(str(child.pid), encoding="utf-8")
event_names = [
    "probe_started",
    "app_ready",
    "first_frame",
    "shutdown_started",
    "shutdown_complete",
    "cleanup_ready",
]
events = [
    {"schema": "world-gal-game.runtime-probe.v1", "seq": index, "event": event}
    for index, event in enumerate(event_names, 1)
]
progress_path.write_text(
    "".join(json.dumps(event) + "\\n" for event in events),
    encoding="utf-8",
)
deadline = time.monotonic() + 5
while not cleanup_ack_path.exists() and time.monotonic() < deadline:
    time.sleep(0.01)
ack = json.loads(cleanup_ack_path.read_text(encoding="utf-8"))
with progress_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({
        "schema": "world-gal-game.runtime-probe.v1",
        "seq": 7,
        "event": "cleanup_complete",
    }) + "\\n")
result_path.write_text(
    json.dumps({
        "schema": "world-gal-game.runtime-probe.v1",
        "ok": bool(ack.get("ok")),
        "process_cleanup": ack,
    }),
    encoding="utf-8",
)
raise SystemExit(0 if ack.get("ok") else 1)
"""
    monkeypatch.setattr(
        runtime_reliability,
        "_probe_command",
        lambda **paths: [
            sys.executable,
            "-c",
            worker,
            str(paths["result_path"]),
            str(paths["progress_path"]),
            str(paths["cleanup_ack_path"]),
            str(descendant_pid_path),
        ],
    )
    # Model the exact polling race: only the ready/ack snapshot may find it.
    monkeypatch.setattr(runtime_reliability, "_descendant_identities", lambda _pid: set())

    descendant_pid: int | None = None
    try:
        run = runtime_reliability._run_probe(
            run_index=1,
            spec={"mode": "startup_only", "seed": 731},
            run_dir=tmp_path / "handshake-descendant",
            timeout_s=5,
        )
        descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))
        ack = run["process_cleanup"]["controller_ack"]

        assert run["process_cleanup"]["controller_ack_sent"] is True
        assert ack["backend"] == "controller-ready-ack-psutil"
        assert ack["status"] == "residual_descendants"
        assert descendant_pid in [item["pid"] for item in ack["detected"]]
        assert ack["survivors"] == []
        assert run["status"] == "protocol_error"
        assert run["ok"] is False
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(descendant_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.01)
        else:
            pytest.fail(f"escaped descendant {descendant_pid} is still alive")
    finally:
        if descendant_pid is None and descendant_pid_path.exists():
            descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))
        if descendant_pid is not None:
            try:
                os.kill(descendant_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_timeout_kills_probe_and_preserves_timeout_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime_reliability,
        "_probe_command",
        lambda **_kwargs: [
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
        ],
    )

    run = runtime_reliability._run_probe(
        run_index=1,
        spec={"mode": "startup_only", "seed": 731},
        run_dir=tmp_path / "timeout",
        timeout_s=0.05,
    )

    assert run["ok"] is False
    assert run["status"] == "timeout"
    assert run["timed_out"] is True
    assert run["returncode"] is not None
    assert run["child"] is None
    assert run["result_error"] is None
    assert any("missing milestone(s)" in error for error in run["protocol_errors"])
    assert run["stdout"]["bytes"] == 0
    assert run["stderr"]["bytes"] == 0


def test_probe_output_is_drained_but_only_a_bounded_tail_is_retained(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime_reliability,
        "_probe_command",
        lambda **_kwargs: [
            sys.executable,
            "-c",
            "import os; os.write(1, b'x' * 100000)",
        ],
    )

    run = runtime_reliability._run_probe(
        run_index=1,
        spec={"mode": "startup_only", "seed": 731},
        run_dir=tmp_path / "bounded-output",
        timeout_s=5,
    )

    assert run["status"] == "protocol_error"
    assert run["stdout"]["bytes"] == 100_000
    assert run["stdout"]["truncated"] is True
    assert len(run["stdout"]["tail"].encode()) <= 16_384
    assert len(run["stdout"]["sha256"]) == 64


def test_user_plugin_root_can_be_hermetically_redirected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from world_gal_game.plugins import manager as plugin_manager

    isolated = tmp_path / "isolated-user-plugins"
    monkeypatch.setenv("WGG_USER_PLUGIN_ROOT", str(isolated))

    assert plugin_manager._user_plugin_root() == isolated.resolve()


def test_tiny_real_fresh_process_gate_emits_deterministic_runtime_evidence(
    tmp_path: Path,
) -> None:
    pytest.importorskip(
        "psutil",
        reason="the ready/ack runtime gate requires the optional runtime extra",
    )
    policy_path = _write_policy(tmp_path / "runtime-policy.yaml", _portable_policy())
    report_path = tmp_path / "artifacts" / "runtime-report.json"

    report = run_runtime_check(
        DEMO_PACK,
        policy_path=policy_path,
        report_path=report_path,
    )

    assert report_path.is_file()
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    assert report["ok"] is True, report["failures"]
    assert report["status"] == "pass"
    assert report["claim"]["dummy_sdl"] is True
    assert report["claim"]["numeric_limits_enforced"] is False
    assert len(report["runs"]) == 2
    assert all(run["ok"] and run["status"] == "ok" for run in report["runs"])

    startup = report["summary"]["fresh_process_startup"]
    assert startup["samples"] == 1
    assert math.isfinite(startup["app_ready_p95_ms"])
    assert math.isfinite(startup["first_frame_p95_ms"])
    assert startup["first_frame_p95_ms"] >= startup["app_ready_p95_ms"] > 0

    assert report["determinism"] == {
        "required": True,
        "ok": True,
        "reference_run": 1,
        "compared_runs": [2],
        "first_mismatch": None,
    }
    checkpoint_sets = []
    for run in report["runs"]:
        child = run["child"]
        checkpoints = child["semantic_checkpoints"]
        assert [checkpoint["label"] for checkpoint in checkpoints] == [
            "title:complete",
            "new_game:complete",
        ]
        hashes = [checkpoint["sha256"] for checkpoint in checkpoints]
        assert all(len(digest) == 64 for digest in hashes)
        assert all(
            len(checkpoint["semantic"]["persistent_state_sha256"]) == 64
            for checkpoint in checkpoints
        )
        assert all(
            checkpoint["semantic"]["persistent_state_json_bytes"] > 0
            for checkpoint in checkpoints
        )
        checkpoint_sets.append(hashes)
        assert [view["measured_frames"] for view in child["views"]] == [1, 1]
        assert child["fingerprint"]["user_plugin_scope"] == "isolated"
        assert child["plugins"]["failed"] == []
        assert child["plugins"]["runtime_errors"] == []
        assert [item["phase"] for item in child["plugins"]["observations"]] == [
            "first_frame",
            "title:complete",
            "new_game:complete",
            "final",
        ]
    assert checkpoint_sets[0] == checkpoint_sets[1]

    memory = report["summary"]["memory"]
    assert memory["current_rss_max_bytes"] > 0
    assert memory["peak_rss_max_bytes"] >= memory["current_rss_max_bytes"]
    for run in report["runs"]:
        samples = run["child"]["memory"]["samples"]
        assert samples
        assert any(sample["current_rss_bytes"] for sample in samples)
        assert all(sample["peak_rss_bytes"] for sample in samples)
        assert all(sample["peak_raw_unit"] in {"bytes", "kib"} for sample in samples)

    cache = report["summary"]["cache"]
    assert cache["violations"] == []
    assert cache["image_peak_bytes"] >= 0
    assert cache["sound_peak_bytes"] >= 0
    assert cache["retained_audio_peak_bytes"] >= cache["sound_peak_bytes"]
    assert cache["bgm_slots_peak"] <= 2
    for run in report["runs"]:
        child_cache = run["child"]["cache"]
        assert child_cache["start"] is not None
        assert child_cache["end"] is not None
        assert child_cache["peak"]["image_budget_bytes"] > 0
        assert child_cache["peak"]["sound_budget_bytes"] > 0

    assert all(check["status"] == "observed" for check in report["checks"])
    _assert_canonical_digest(report)
