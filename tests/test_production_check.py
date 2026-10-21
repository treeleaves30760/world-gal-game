"""Fail-closed aggregate production evidence and hostile-artifact tests."""
from __future__ import annotations

import json
import struct
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from world_gal_game.dev import production_check


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def _png(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"fixture"
    )


def _scenarios() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label, size in (("720p", [1280, 720]), ("1080p", [1920, 1080])):
        for view in production_check._PRODUCTION_VIEWS:
            rows.append({
                "name": f"{view}_{label}",
                "view": view,
                "screen_size": size,
            })
    return rows


def _runtime_policy() -> dict[str, Any]:
    fingerprint = {
        "system": "FixtureOS",
        "system_release": "1",
        "machine": "fixture64",
        "processor": "fixture64",
        "python": "3.13.9",
        "pygame": "2.6.1",
        "sdl": "2.32.10",
        "video_driver": "dummy",
        "audio_driver": "dummy",
        "screen_width": 1280,
        "screen_height": 720,
        "vsync": False,
        "frozen": False,
        "memory_provider": "fixture-rss",
        "mixer_initialized": True,
        "user_plugin_scope": "isolated",
    }
    return {
        "schema_version": 1,
        "name": "production-fixture-runtime",
        "claim_scope": "dummy-sdl-source-runtime-calibration",
        "notes": "Owner-calibrated fixture budgets.",
        "profile": {
            "screen_size": [1280, 720],
            "seed": 42,
            "fixed_dt_ms": 16.67,
            "warmup_frames": 1,
            "frames_per_view": 2,
            "views": ["title", "new_game"],
            "cold_runs": 1,
            "soak_runs": 2,
            "rss_sample_interval_frames": 1,
            "timeout_s": 30,
        },
        "limits": {
            "require_finite_metrics": True,
            "require_rss": True,
            "require_determinism": True,
            "require_within_configured_cache_budgets": True,
            "require_plugin_errors_zero": True,
            "require_ui_chrome_active": True,
            "max_bgm_slots": 2,
            "cold_start_p95_ms": 1000.0,
            "first_frame_p95_ms": 1000.0,
            "current_rss_max_bytes": 1_000_000_000,
            "peak_rss_max_bytes": 1_000_000_000,
            "rss_growth_max_bytes": 100_000_000,
            "frame_p95_ms": 20.0,
            "frame_p99_ms": 25.0,
            "frame_budget_ms": 20.0,
            "max_over_budget_frames": 0,
        },
        "calibrated_fingerprint": fingerprint,
    }


def _production_policy() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "name": "production-fixture",
        "claim_scope": production_check.CLAIM_SCOPE,
        "max_evidence_age_seconds": 86400,
        "visual": {
            "scenarios_path": "content/production_visual_scenarios.json",
            "approvals_path": "visual_baselines/approved.yaml",
        },
        "runtime": {
            "policy_path": "content/runtime_reliability.yaml",
            "require_numeric_limits": True,
        },
        "coverage": {
            "script_glob": "scripts/test_*.json",
            "seed": 42,
            "minimum_percent": {
                "scenes": 100.0,
                "lines": 100.0,
                "choices": 100.0,
                "endings": 100.0,
            },
        },
        "contract": {"path": "contracts.yaml", "seed": 42},
        "required_asset_roots": ["assets"],
    }


def _clean_git(path: Path) -> dict[str, Any]:
    return {
        "available": True,
        "root": str(path.resolve()),
        "sha": "a" * 40,
        "dirty": False,
        "status_sha256": production_check._sha256_bytes(b""),
    }


def _self_report(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    stages: list[dict[str, Any]] = []
    for name in production_check._SELF_CHECK_STAGES:
        details: dict[str, Any] = {}
        if name in {"schema", "refs"}:
            details = {"errors": [], "warnings": []}
        elif name == "reachability":
            details = {"unverified": [], "reachable": [{"ending_id": "ending"}]}
        elif name == "smoke":
            details = {"count": 1, "passed": 1, "failed": 0, "results": []}
        elif name == "visual":
            details = {
                "ok": True,
                "count": len(scenarios),
                "passed": len(scenarios),
                "failed": 0,
                "created": 0,
                "results": [
                    {
                        "name": item["name"],
                        "ok": True,
                        "created_baseline": False,
                    }
                    for item in scenarios
                ],
            }
        stages.append({
            "name": name,
            "ok": True,
            "skipped": False,
            "summary": "fixture pass",
            "details": details,
        })
    return {"pack_root": "/fixture", "ok": True, "stages": stages}


def _coverage_report() -> dict[str, Any]:
    return {
        "ok": True,
        "seed": 42,
        "scripts": [{
            "script": "scripts/test_all.json",
            "sha256": "b" * 64,
            "commands": 4,
            "ok": True,
            "errors": [],
        }],
        "coverage": {
            name: {"seen": 1, "total": 1, "pct": 100.0, "missing": []}
            for name in ("scenes", "lines", "choices", "endings")
        },
    }


def _runtime_report(
    pack: Path,
    runtime_policy_path: Path,
    engine_root: Path,
) -> dict[str, Any]:
    document = _runtime_policy()
    checks = [
        {
            "id": check_id,
            "status": "pass",
            "actual": 1,
            "limit": 2,
            "ok": True,
        }
        for check_id in sorted(production_check._RUNTIME_NUMERIC_CHECKS)
    ]
    report: dict[str, Any] = {
        "schema": production_check.RUNTIME_REPORT_SCHEMA,
        "ok": True,
        "status": "pass",
        "generated_at_utc": production_check._format_utc(NOW),
        "claim": {
            "scope": document["claim_scope"],
            "numeric_limits_enforced": True,
        },
        "subject": {
            "engine_revision": _clean_git(engine_root),
            "pack_revision": _clean_git(pack),
        },
        "policy": {
            "sha256": production_check._sha256_file(runtime_policy_path),
            "document": document,
            "fingerprint": {
                "expected": document["calibrated_fingerprint"],
                "actual": document["calibrated_fingerprint"],
                "matches": True,
                "mismatches": [],
            },
        },
        "checks": checks,
        "runs": [
            {
                "run_index": index,
                "mode": "soak",
                "ok": True,
                "status": "ok",
                "returncode": 0,
                "timed_out": False,
                "spawn_error": None,
                "controller_error": None,
                "result_error": None,
                "protocol_errors": [],
                "stdout": {
                    "bytes": 0,
                    "sha256": production_check._sha256_bytes(b""),
                    "tail": "",
                    "truncated": False,
                    "complete": True,
                    "termination": "eof",
                    "error": None,
                },
                "stderr": {
                    "bytes": 0,
                    "sha256": production_check._sha256_bytes(b""),
                    "tail": "",
                    "truncated": False,
                    "complete": True,
                    "termination": "eof",
                    "error": None,
                },
            }
            for index in (1, 2)
        ],
        "failures": [],
        "warnings": [],
        "artifact": {"canonical_content_sha256": None},
    }
    report["artifact"]["canonical_content_sha256"] = (
        production_check._artifact_digest(report)
    )
    return report


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _build_pack(tmp_path: Path) -> tuple[Path, Path, Path, list[dict[str, Any]]]:
    engine_root = tmp_path / "engine-package"
    engine_root.mkdir()
    (engine_root / "engine.py").write_text("VERSION = 1\n", encoding="utf-8")

    pack = tmp_path / "pack"
    (pack / "content").mkdir(parents=True)
    (pack / "content" / "meta.yaml").write_text(
        "pack_format_version: '0.1'\n"
        "title: Production Fixture\n"
        "start_location: fixture_room\n",
        encoding="utf-8",
    )
    (pack / "assets").mkdir()
    (pack / "assets" / "required.bin").write_bytes(b"asset")
    (pack / "scripts").mkdir()
    _write_json(pack / "scripts" / "test_all.json", {"commands": [{"op": "inspect"}]})
    (pack / "contracts.yaml").write_text(
        yaml.safe_dump({
            "expectations": [{
                "name": "fixture holds",
                "kind": "holds",
                "goal": {"flag": "fixture"},
                "expect": False,
            }]
        }, sort_keys=False),
        encoding="utf-8",
    )
    policy_path = pack / "content" / "production.yaml"
    policy_path.write_text(
        yaml.safe_dump(_production_policy(), sort_keys=False),
        encoding="utf-8",
    )
    runtime_path = pack / "content" / "runtime_reliability.yaml"
    runtime_path.write_text(
        yaml.safe_dump(_runtime_policy(), sort_keys=False),
        encoding="utf-8",
    )
    raw_scenarios = _scenarios()
    scenario_path = pack / "content" / "production_visual_scenarios.json"
    _write_json(scenario_path, raw_scenarios)
    scenarios, _digest = production_check.load_visual_scenarios(scenario_path)

    baseline_dir = pack / "visual_baselines"
    baseline_dir.mkdir()
    entries: dict[str, Any] = {}
    for scenario in scenarios:
        name = scenario["name"]
        width, height = scenario["screen_size"]
        baseline = baseline_dir / f"{name}.png"
        baseline.write_bytes(_png(width, height))
        entries[name] = {
            "status": "approved",
            "scenario_sha256": production_check._canonical_digest(scenario),
            "baseline_sha256": production_check._sha256_file(baseline),
            "reviewer": "fixture-reviewer",
            "note": "Reviewed fixture baseline.",
            "approved_at_utc": production_check._format_utc(NOW - timedelta(hours=1)),
        }
    approval = {
        "schema_version": 1,
        "scenario_set_sha256": production_check._canonical_digest(scenarios),
        "engine_content_sha256": production_check._tree_snapshot(engine_root)[
            "manifest_sha256"
        ],
        "pack_runtime_content_sha256": production_check._tree_snapshot(
            pack,
            excluded_top_level={"visual_baselines"},
        )["manifest_sha256"],
        "scenarios": entries,
    }
    (baseline_dir / "approved.yaml").write_text(
        yaml.safe_dump(approval, sort_keys=False),
        encoding="utf-8",
    )
    return pack, policy_path, engine_root, scenarios


def _install_passing_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pack: Path,
    engine_root: Path,
    scenarios: list[dict[str, Any]],
) -> None:
    monkeypatch.setattr(production_check, "_engine_source_root", lambda: engine_root)
    monkeypatch.setattr(production_check, "_git_revision", _clean_git)
    monkeypatch.setattr(production_check, "_untracked_input_paths", lambda _path: [])

    class FakeSelfCheck:
        def __init__(self, _pack: Path, **kwargs: Any):
            assert Path(_pack) == pack
            assert kwargs["stop_on_failure"] is False
            assert kwargs["skip_smoke"] is False
            assert kwargs["skip_visual"] is False
            assert kwargs["strict_visual_baselines"] is True
            assert kwargs["smoke_seed"] == 42
            candidate_root = Path(kwargs["visual_candidates_dir"])
            candidate_root.mkdir(parents=True, exist_ok=True)
            for scenario in scenarios:
                name = scenario["name"]
                source = pack / "visual_baselines" / f"{name}.png"
                (candidate_root / f"{name}.png").write_bytes(source.read_bytes())

        def run(self) -> SimpleNamespace:
            return SimpleNamespace(to_dict=lambda: _self_report(scenarios))

    monkeypatch.setattr(production_check, "SelfCheck", FakeSelfCheck)
    monkeypatch.setattr(
        production_check,
        "_run_coverage_union",
        lambda _pack, seed: _coverage_report(),
    )
    monkeypatch.setattr(
        production_check,
        "_run_narrative_contract",
        lambda _pack, contract_path, seed: {
            "ok": True,
            "passed": 1,
            "total": 1,
            "results": [{"name": "fixture", "kind": "holds", "ok": True}],
            "contract": contract_path.relative_to(pack).as_posix(),
            "sha256": production_check._sha256_file(contract_path),
            "seed": seed,
        },
    )

    def fake_runtime(
        _pack: Path,
        *,
        policy_path: Path,
        report_path: Path,
    ) -> dict[str, Any]:
        report = _runtime_report(pack, policy_path, engine_root)
        _write_json(report_path, report)
        return report

    monkeypatch.setattr(production_check, "run_runtime_check", fake_runtime)


@pytest.fixture
def passing_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, Path, Path, list[dict[str, Any]]]:
    pack, policy, engine, scenarios = _build_pack(tmp_path)
    _install_passing_fakes(
        monkeypatch,
        pack=pack,
        engine_root=engine,
        scenarios=scenarios,
    )
    report = tmp_path / "evidence" / "production.json"
    return pack, policy, engine, report, scenarios


def _run(
    fixture: tuple[Path, Path, Path, Path, list[dict[str, Any]]],
) -> dict[str, Any]:
    pack, policy, _engine, report, _scenarios = fixture
    return production_check.run_production_check(
        pack,
        policy_path=policy,
        report_path=report,
        _now=lambda: NOW,
    )


def _reseal(report: dict[str, Any]) -> None:
    for gate in report.get("gates", {}).values():
        gate["structured_evidence_sha256"] = production_check._canonical_digest(
            gate.get("report")
        )
    runtime = report.get("gates", {}).get("runtime_check", {}).get("report")
    if isinstance(runtime, dict):
        runtime["artifact"]["canonical_content_sha256"] = (
            production_check._artifact_digest(runtime)
        )
        report["gates"]["runtime_check"]["structured_evidence_sha256"] = (
            production_check._canonical_digest(runtime)
        )
    report["artifact"]["canonical_content_sha256"] = production_check._artifact_digest(
        report
    )


def test_production_check_passes_all_gates_and_verifies_against_current_inputs(
    passing_fixture: tuple[Path, Path, Path, Path, list[dict[str, Any]]],
) -> None:
    pack, _policy, _engine, report_path, _scenarios = passing_fixture

    report = _run(passing_fixture)

    assert report["ok"] is True
    assert report["status"] == "pass"
    assert set(report["gates"]) == {
        "self_check",
        "visual_approval",
        "coverage",
        "contract",
        "runtime_check",
    }
    assert all(gate["ok"] for gate in report["gates"].values())
    assert report_path.is_file()
    assert not (pack / "visual_candidates").exists()
    assert report["subject"]["before"] == report["subject"]["after"]
    assert report["artifact"]["canonical_content_sha256"] == (
        production_check._artifact_digest(report)
    )

    verified = production_check.verify_production_report(
        report_path,
        pack_root=pack,
        now=NOW,
    )
    assert verified == {
        "ok": True,
        "report": str(report_path.resolve()),
        "pack_root": str(pack.resolve()),
        "failures": [],
    }


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("missing_gate", "required_gate_set"),
        ("skipped_stage", "self_check_not_strict"),
        ("coverage_99", "coverage_not_strict"),
        ("candidate_approval", "visual_approval_semantics"),
        ("runtime_observed", "runtime_numeric_limits_missing"),
    ],
)
def test_verifier_rejects_semantic_forgery_even_after_all_digests_are_resealed(
    passing_fixture: tuple[Path, Path, Path, Path, list[dict[str, Any]]],
    mutation: str,
    expected_code: str,
) -> None:
    pack, _policy, _engine, report_path, _scenarios = passing_fixture
    report = _run(passing_fixture)
    forged = deepcopy(report)
    if mutation == "missing_gate":
        forged["gates"].pop("coverage")
    elif mutation == "skipped_stage":
        forged["gates"]["self_check"]["report"]["stages"][0]["skipped"] = True
    elif mutation == "coverage_99":
        forged["gates"]["coverage"]["report"]["coverage"]["lines"].update(
            {"seen": 0, "pct": 99.0, "missing": ["scene#0"]}
        )
    elif mutation == "candidate_approval":
        forged["visual_evidence"]["scenarios"][0]["approval"]["status"] = (
            "review_candidate"
        )
        forged["gates"]["visual_approval"]["report"] = forged["visual_evidence"]
    elif mutation == "runtime_observed":
        runtime = forged["gates"]["runtime_check"]["report"]
        runtime["policy"]["document"]["limits"]["frame_p95_ms"] = None
        next(item for item in runtime["checks"] if item["id"] == "frame_p95_ms").update(
            {"status": "observed", "limit": None, "ok": None}
        )
    _reseal(forged)
    _write_json(report_path, forged)

    result = production_check.verify_production_report(
        report_path,
        pack_root=pack,
        now=NOW,
    )

    assert result["ok"] is False
    codes = {failure["code"] for failure in result["failures"]}
    assert expected_code in codes
    if mutation == "runtime_observed":
        assert "runtime_policy_document_mismatch" in codes


@pytest.mark.parametrize(
    "payload",
    [
        "{",
        '{"schema":"a","schema":"b"}',
        '{"schema":NaN}',
        "[]",
    ],
)
def test_verifier_rejects_truncated_duplicate_nonfinite_and_nonobject_json(
    passing_fixture: tuple[Path, Path, Path, Path, list[dict[str, Any]]],
    payload: str,
) -> None:
    pack, _policy, _engine, report_path, _scenarios = passing_fixture
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(payload, encoding="utf-8")

    result = production_check.verify_production_report(
        report_path,
        pack_root=pack,
        now=NOW,
    )

    assert result["ok"] is False
    assert result["failures"][0]["code"] in {"report_unreadable", "report_shape"}


def test_verifier_rejects_expired_report_even_when_digest_is_resealed(
    passing_fixture: tuple[Path, Path, Path, Path, list[dict[str, Any]]],
) -> None:
    pack, _policy, _engine, report_path, _scenarios = passing_fixture
    report = _run(passing_fixture)
    _write_json(report_path, report)

    result = production_check.verify_production_report(
        report_path,
        pack_root=pack,
        now=NOW + timedelta(days=2),
    )

    assert result["ok"] is False
    assert "report_stale" in {failure["code"] for failure in result["failures"]}


def test_policy_failure_still_writes_a_self_digested_fail_artifact(
    tmp_path: Path,
) -> None:
    pack = tmp_path / "pack"
    (pack / "content").mkdir(parents=True)
    policy = pack / "content" / "production.yaml"
    policy.write_text("schema_version: 999\n", encoding="utf-8")
    report_path = tmp_path / "out" / "failed.json"

    report = production_check.run_production_check(
        pack,
        policy_path=policy,
        report_path=report_path,
        _now=lambda: NOW,
    )

    assert report["ok"] is False
    assert report["status"] == "fail"
    assert report_path.is_file()
    assert report["artifact"]["canonical_content_sha256"] == (
        production_check._artifact_digest(report)
    )
    assert {item["code"] for item in report["failures"]} >= {
        "production_policy_error",
        "required_gate_set",
    }


def test_subject_mutation_during_check_fails_closed(
    passing_fixture: tuple[Path, Path, Path, Path, list[dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack, _policy, _engine, _report_path, _scenarios = passing_fixture
    original_runtime = production_check.run_runtime_check

    def mutating_runtime(*args: Any, **kwargs: Any) -> dict[str, Any]:
        result = original_runtime(*args, **kwargs)
        (pack / "assets" / "required.bin").write_bytes(b"changed during run")
        return result

    monkeypatch.setattr(production_check, "run_runtime_check", mutating_runtime)

    report = _run(passing_fixture)

    assert report["ok"] is False
    assert "subject_changed_during_run" in {
        item["code"] for item in report["failures"]
    }


def test_atomic_writer_preserves_previous_artifact_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "report.json"
    target.write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(
        production_check.os,
        "replace",
        lambda _source, _target: (_ for _ in ()).throw(OSError("replace failed")),
    )

    with pytest.raises(OSError, match="replace failed"):
        production_check._atomic_write_json(target, {"new": True})

    assert target.read_text(encoding="utf-8") == "old\n"
    assert list(tmp_path.glob(".report.json.*.tmp")) == []


def test_file_digest_rejects_symlinks(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"source")
    link = tmp_path / "link.bin"
    link.symlink_to(source)

    with pytest.raises(
        production_check.ProductionPolicyError,
        match="not a regular file",
    ):
        production_check._sha256_file(link)


def test_tree_snapshot_excludes_uv_environment_but_rejects_product_symlinks(
    tmp_path: Path,
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    product = root / "engine.py"
    product.write_text("print('product')\n", encoding="utf-8")
    environment = root / ".venv" / "bin"
    environment.mkdir(parents=True)
    (environment / "python").symlink_to(product)

    snapshot = production_check._tree_snapshot(root)

    assert snapshot["files"] == 1
    assert snapshot["bytes"] == product.stat().st_size

    product_link = root / "engine-link.py"
    product_link.symlink_to(product)
    with pytest.raises(
        production_check.ProductionPolicyError,
        match="production inputs must not use symlinks: engine-link.py",
    ):
        production_check._tree_snapshot(root)


def test_tree_snapshot_rejects_portable_directory_collisions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "collision"
    (root / "Straße").mkdir(parents=True)
    try:
        (root / "strasse").mkdir()
    except FileExistsError:
        pytest.skip("filesystem already prevents portable case-fold collisions")
    with pytest.raises(
        production_check.ProductionPolicyError,
        match="portable path collision",
    ):
        production_check._tree_snapshot(root)


def test_visual_scenario_document_requires_full_core_matrix_and_strict_json(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scenarios.json"
    scenarios = _scenarios()
    scenarios.pop()
    _write_json(path, scenarios)

    with pytest.raises(
        production_check.ProductionPolicyError,
        match="missing required views",
    ):
        production_check.load_visual_scenarios(path)

    path.write_text('[{"name":"x","name":"y"}]', encoding="utf-8")
    with pytest.raises(
        production_check.ProductionPolicyError,
        match="duplicate JSON key",
    ):
        production_check.load_visual_scenarios(path)

    path.write_text('[{"name":"x","autoplay":NaN}]', encoding="utf-8")
    with pytest.raises(
        production_check.ProductionPolicyError,
        match="non-finite JSON constant",
    ):
        production_check.load_visual_scenarios(path)


def test_production_visual_matrix_requires_music_room_at_both_sizes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scenarios.json"
    scenarios = [
        item for item in _scenarios()
        if item["view"] != "music_room"
    ]
    _write_json(path, scenarios)

    with pytest.raises(
        production_check.ProductionPolicyError,
        match=r"music_room@1280x720.*music_room@1920x1080",
    ):
        production_check.load_visual_scenarios(path)


def test_visual_scenario_locale_is_normalized_and_path_safe(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scenarios.json"
    scenarios = _scenarios()
    scenarios[0]["locale"] = "EN-us"
    _write_json(path, scenarios)

    normalized, _digest = production_check.load_visual_scenarios(path)
    assert normalized[0]["locale"] == "en-US"

    scenarios[0]["locale"] = "../../en"
    _write_json(path, scenarios)
    with pytest.raises(
        production_check.ProductionPolicyError,
        match="invalid locale id",
    ):
        production_check.load_visual_scenarios(path)


def test_visual_scenario_text_expansion_is_finite_and_bounded(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scenarios.json"
    scenarios = _scenarios()
    scenarios[0]["text_expansion_factor"] = 1.4
    _write_json(path, scenarios)

    normalized, _digest = production_check.load_visual_scenarios(path)
    assert normalized[0]["text_expansion_factor"] == 1.4

    scenarios[0]["text_expansion_factor"] = 2.01
    _write_json(path, scenarios)
    with pytest.raises(
        production_check.ProductionPolicyError,
        match="text_expansion_factor",
    ):
        production_check.load_visual_scenarios(path)


def test_production_policy_rejects_duplicate_keys_escape_paths_and_weak_coverage(
    tmp_path: Path,
) -> None:
    path = tmp_path / "production.yaml"
    document = yaml.safe_dump(_production_policy(), sort_keys=False)
    path.write_text(
        document.replace(
            "name: production-fixture\n",
            "name: first\nname: second\n",
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(
        production_check.ProductionPolicyError,
        match="duplicate key 'name'",
    ):
        production_check.load_production_policy(path)

    escaped = _production_policy()
    escaped["visual"]["scenarios_path"] = "../outside.json"
    path.write_text(yaml.safe_dump(escaped, sort_keys=False), encoding="utf-8")
    with pytest.raises(
        production_check.ProductionPolicyError,
        match="must stay inside the pack",
    ):
        production_check.load_production_policy(path)

    transient = _production_policy()
    transient["visual"]["scenarios_path"] = "artifacts/scenarios.json"
    path.write_text(yaml.safe_dump(transient, sort_keys=False), encoding="utf-8")
    with pytest.raises(
        production_check.ProductionPolicyError,
        match="must not use an excluded transient path",
    ):
        production_check.load_production_policy(path)

    weak = _production_policy()
    weak["coverage"]["minimum_percent"]["lines"] = 99.0
    path.write_text(yaml.safe_dump(weak, sort_keys=False), encoding="utf-8")
    with pytest.raises(
        production_check.ProductionPolicyError,
        match="Input should be 100",
    ):
        production_check.load_production_policy(path)


def test_report_path_inside_pack_is_rejected_without_mutating_the_input_tree(
    passing_fixture: tuple[Path, Path, Path, Path, list[dict[str, Any]]],
) -> None:
    pack, policy, _engine, _report, _scenarios = passing_fixture
    unsafe = pack / "artifacts" / "production.json"

    result = production_check.run_production_check(
        pack,
        policy_path=policy,
        report_path=unsafe,
        _now=lambda: NOW,
    )

    assert result["ok"] is False
    assert "unsafe_report_path" in {
        failure["code"] for failure in result["failures"]
    }
    assert not unsafe.exists()


def test_approval_manifest_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    path = tmp_path / "approvals.yaml"
    path.write_text(
        "schema_version: 1\n"
        f"scenario_set_sha256: {'a' * 64}\n"
        f"scenario_set_sha256: {'b' * 64}\n"
        f"engine_content_sha256: {'c' * 64}\n"
        f"pack_runtime_content_sha256: {'d' * 64}\n"
        "scenarios: {}\n",
        encoding="utf-8",
    )

    with pytest.raises(
        production_check.ProductionPolicyError,
        match="duplicate key 'scenario_set_sha256'",
    ):
        production_check.load_visual_approvals(path)


@pytest.mark.parametrize(
    ("available", "dirty", "expected_code"),
    [
        (True, True, "engine_worktree_dirty"),
        (False, None, "engine_git_unavailable"),
    ],
)
def test_production_check_rejects_dirty_or_unavailable_git(
    passing_fixture: tuple[Path, Path, Path, Path, list[dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
    available: bool,
    dirty: bool | None,
    expected_code: str,
) -> None:
    def hostile_git(path: Path) -> dict[str, Any]:
        revision = _clean_git(path)
        revision.update({"available": available, "dirty": dirty})
        if not available:
            revision.update({"root": None, "sha": None, "status_sha256": None})
        return revision

    monkeypatch.setattr(production_check, "_git_revision", hostile_git)

    report = _run(passing_fixture)

    assert report["ok"] is False
    assert expected_code in {item["code"] for item in report["failures"]}


def test_verifier_rejects_current_source_and_approval_binding_changes(
    passing_fixture: tuple[Path, Path, Path, Path, list[dict[str, Any]]],
) -> None:
    pack, _policy, _engine, report_path, _scenarios = passing_fixture
    report = _run(passing_fixture)
    assert report["ok"] is True
    (pack / "assets" / "required.bin").write_bytes(b"post-check mutation")

    result = production_check.verify_production_report(
        report_path,
        pack_root=pack,
        now=NOW,
    )

    codes = {failure["code"] for failure in result["failures"]}
    assert result["ok"] is False
    assert "current_subject_mismatch" in codes
    assert "current_visual_approval_stale" in codes


def test_visual_gate_rejects_wrong_png_dimensions_even_with_matching_hash(
    passing_fixture: tuple[Path, Path, Path, Path, list[dict[str, Any]]],
) -> None:
    pack, _policy, _engine, _report_path, scenarios = passing_fixture
    name = scenarios[0]["name"]
    baseline = pack / "visual_baselines" / f"{name}.png"
    baseline.write_bytes(_png(1, 1))
    approval_path = pack / "visual_baselines" / "approved.yaml"
    approval = yaml.safe_load(approval_path.read_text(encoding="utf-8"))
    approval["scenarios"][name]["baseline_sha256"] = (
        production_check._sha256_file(baseline)
    )
    approval_path.write_text(
        yaml.safe_dump(approval, sort_keys=False),
        encoding="utf-8",
    )

    report = _run(passing_fixture)

    codes = {failure["code"] for failure in report["failures"]}
    assert report["ok"] is False
    assert {"visual_baseline_dimensions", "visual_candidate_dimensions"} <= codes


def test_visual_gate_rejects_stale_approval_source_binding(
    passing_fixture: tuple[Path, Path, Path, Path, list[dict[str, Any]]],
) -> None:
    pack, _policy, _engine, _report_path, _scenarios = passing_fixture
    approval_path = pack / "visual_baselines" / "approved.yaml"
    approval = yaml.safe_load(approval_path.read_text(encoding="utf-8"))
    approval["engine_content_sha256"] = "f" * 64
    approval_path.write_text(
        yaml.safe_dump(approval, sort_keys=False),
        encoding="utf-8",
    )

    report = _run(passing_fixture)

    assert report["ok"] is False
    assert "visual_approval_source_mismatch" in {
        failure["code"] for failure in report["failures"]
    }


def test_visual_gate_rejects_nested_or_case_variant_unlisted_png(
    passing_fixture: tuple[Path, Path, Path, Path, list[dict[str, Any]]],
) -> None:
    pack, _policy, _engine, _report_path, _scenarios = passing_fixture
    nested = pack / "visual_baselines" / "nested"
    nested.mkdir()
    (nested / "orphan.PNG").write_bytes(_png(1280, 720))

    report = _run(passing_fixture)

    assert report["ok"] is False
    assert "visual_baseline_unlisted" in {
        failure["code"] for failure in report["failures"]
    }


def test_runtime_verifier_recomputes_fingerprint_and_requires_complete_logs(
    passing_fixture: tuple[Path, Path, Path, Path, list[dict[str, Any]]],
) -> None:
    pack, _policy, _engine, report_path, _scenarios = passing_fixture
    forged = deepcopy(_run(passing_fixture))
    runtime = forged["gates"]["runtime_check"]["report"]
    runtime["policy"]["fingerprint"]["actual"]["system"] = "ForgedOS"
    runtime["runs"][0]["stdout"]["complete"] = False
    _reseal(forged)
    _write_json(report_path, forged)

    result = production_check.verify_production_report(
        report_path,
        pack_root=pack,
        now=NOW,
    )

    codes = {failure["code"] for failure in result["failures"]}
    assert result["ok"] is False
    assert {"runtime_fingerprint_mismatch", "runtime_log_incomplete"} <= codes


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("future", "report_from_future"),
        ("top_extra", "report_top_level_shape"),
        ("artifact_extra", "report_digest"),
        ("runtime_artifact_extra", "runtime_report_digest"),
    ],
)
def test_verifier_rejects_future_or_unknown_fields_after_resealing(
    passing_fixture: tuple[Path, Path, Path, Path, list[dict[str, Any]]],
    mutation: str,
    expected_code: str,
) -> None:
    pack, _policy, _engine, report_path, _scenarios = passing_fixture
    forged = deepcopy(_run(passing_fixture))
    if mutation == "future":
        forged["generated_at_utc"] = production_check._format_utc(
            NOW + timedelta(minutes=10)
        )
    elif mutation == "top_extra":
        forged["unexpected"] = True
    elif mutation == "artifact_extra":
        forged["artifact"]["unexpected"] = True
    else:
        forged["gates"]["runtime_check"]["report"]["artifact"]["unexpected"] = True
    _reseal(forged)
    _write_json(report_path, forged)

    result = production_check.verify_production_report(
        report_path,
        pack_root=pack,
        now=NOW,
    )

    assert result["ok"] is False
    assert expected_code in {failure["code"] for failure in result["failures"]}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("claim", []),
        ("invocation", []),
        ("visual_evidence", []),
        ("policy", []),
    ],
)
def test_verifier_fails_closed_instead_of_crashing_on_hostile_nested_shapes(
    passing_fixture: tuple[Path, Path, Path, Path, list[dict[str, Any]]],
    field: str,
    value: Any,
) -> None:
    pack, _policy, _engine, report_path, _scenarios = passing_fixture
    forged = deepcopy(_run(passing_fixture))
    forged[field] = value
    _reseal(forged)
    _write_json(report_path, forged)

    result = production_check.verify_production_report(
        report_path,
        pack_root=pack,
        now=NOW,
    )

    assert result["ok"] is False
    assert result["failures"]
    assert all("code" in failure for failure in result["failures"])


def test_verifier_rejects_report_copied_inside_pack_transient_directory(
    passing_fixture: tuple[Path, Path, Path, Path, list[dict[str, Any]]],
) -> None:
    pack, _policy, _engine, report_path, _scenarios = passing_fixture
    report = _run(passing_fixture)
    assert report["ok"] is True
    unsafe = pack / "artifacts" / "copied-production.json"
    _write_json(unsafe, report)

    result = production_check.verify_production_report(
        unsafe,
        pack_root=pack,
        now=NOW,
    )

    assert result["ok"] is False
    assert result["failures"][0]["code"] == "unsafe_report_path"


def test_report_path_inside_enclosing_engine_git_worktree_is_rejected(
    passing_fixture: tuple[Path, Path, Path, Path, list[dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack, policy, engine, _report, _scenarios = passing_fixture
    enclosing_git_root = engine.parent

    def enclosing_git(path: Path) -> dict[str, Any]:
        revision = _clean_git(path)
        if path.resolve() == engine.resolve():
            revision["root"] = str(enclosing_git_root.resolve())
        return revision

    monkeypatch.setattr(production_check, "_git_revision", enclosing_git)
    unsafe = enclosing_git_root / "release-evidence" / "production.json"

    result = production_check.run_production_check(
        pack,
        policy_path=policy,
        report_path=unsafe,
        _now=lambda: NOW,
    )

    assert result["ok"] is False
    assert "unsafe_report_path" in {
        failure["code"] for failure in result["failures"]
    }
    assert not unsafe.exists()


def test_approval_mutation_after_load_fails_the_same_production_run(
    passing_fixture: tuple[Path, Path, Path, Path, list[dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack, _policy, _engine, _report_path, _scenarios = passing_fixture
    real_load = production_check.load_visual_approvals

    def mutating_load(path: Path) -> tuple[Any, str]:
        loaded = real_load(path)
        path.write_text(
            path.read_text(encoding="utf-8") + "\n# changed after load\n",
            encoding="utf-8",
        )
        return loaded

    monkeypatch.setattr(production_check, "load_visual_approvals", mutating_load)

    report = _run(passing_fixture)

    codes = {failure["code"] for failure in report["failures"]}
    assert report["ok"] is False
    assert "verification_input_changed" in codes
    assert "subject_changed_during_run" in codes


def test_cli_production_check_requires_an_explicit_external_report_path() -> None:
    from world_gal_game import cli

    with pytest.raises(SystemExit) as captured:
        cli.production_check_main(["demo_pack"])

    assert captured.value.code == 2


def test_cli_production_check_and_verify_dispatch_return_zero_for_valid_evidence(
    passing_fixture: tuple[Path, Path, Path, Path, list[dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from world_gal_game import cli

    pack, policy, _engine, report_path, _scenarios = passing_fixture
    real_run = production_check.run_production_check
    real_verify = production_check.verify_production_report
    monkeypatch.setattr(
        production_check,
        "run_production_check",
        lambda pack_root, *, policy_path, report_path: real_run(
            pack_root,
            policy_path=policy_path,
            report_path=report_path,
            _now=lambda: NOW,
        ),
    )
    monkeypatch.setattr(
        production_check,
        "verify_production_report",
        lambda path, *, pack_root: real_verify(
            path,
            pack_root=pack_root,
            now=NOW,
        ),
    )

    check_code = cli.main([
        "production-check",
        str(pack),
        "--policy",
        str(policy),
        "--report",
        str(report_path),
        "--format",
        "text",
    ])
    verify_code = cli.main([
        "production-verify",
        str(report_path),
        "--pack",
        str(pack),
        "--format",
        "text",
    ])

    assert check_code == 0
    assert verify_code == 0
    output = capsys.readouterr().out
    assert "production-check: pass" in output
    assert "production-verify: pass" in output
