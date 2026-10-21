"""Expected-unreachable metadata is strict, current, and fail-closed."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from world_gal_game.dev.pack_inspector import PackInspector
from world_gal_game.dev.self_check import SelfCheck
from world_gal_game.validator import validate_pack


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _expected_contract(
    *,
    scenes: list[str] | None = None,
    endings: list[str] | None = None,
    reason: str = "Retained for old saves and explicit replay only.",
) -> dict:
    return {
        "expected_unreachable": {
            "legacy_branch": {
                "scenes": scenes if scenes is not None else ["legacy_scene"],
                "endings": endings if endings is not None else ["ending_legacy"],
                "reason": reason,
            },
        },
    }


def _make_pack(
    root: Path,
    *,
    verification: object | None = None,
    legacy_reachable: bool = False,
    legacy_dead_choices: bool = False,
) -> Path:
    meta = {
        "pack_format_version": "0.1",
        "title": "Expected unreachable fixture",
        "start_location": "hub",
        "intro_scene": "intro",
    }
    if verification is not None:
        meta["verification"] = verification
    _write(root / "content/meta.yaml", meta)
    _write(root / "content/locations.yaml", {
        "locations": [{
            "id": "hub",
            "name": "Hub",
            "exits": [{"target": "hub"}],
        }],
    })

    intro: dict = {
        "id": "intro",
        "title": "Intro",
        "lines": [{
            "text": "The current route remains reachable.",
            "effects": [{
                "kind": "set_flag",
                "target": "ending_current",
                "value": True,
            }],
        }],
    }
    if legacy_reachable:
        intro["on_end"] = [{
            "kind": "play_scene",
            "target": "legacy_scene",
        }]

    legacy: dict = {
        "id": "legacy_scene",
        "title": "Legacy",
        "lines": [{
            "text": "Old saves may replay this scene.",
            "effects": [{
                "kind": "set_flag",
                "target": "ending_legacy",
                "value": True,
            }],
        }],
    }
    if legacy_dead_choices:
        legacy["choices"] = [{"id": "noop", "text": "No operation"}]

    _write(root / "content/scenes/main.yaml", {
        "scenes": [intro, legacy],
    })
    _write(root / "content/endings.yaml", {
        "endings": [
            {
                "id": "ending_current",
                "title": "Current",
                "requires": [{"kind": "flag", "target": "ending_current"}],
            },
            {
                "id": "ending_legacy",
                "title": "Legacy",
                "requires": [{"kind": "flag", "target": "ending_legacy"}],
            },
        ],
    })
    return root


def _stage(report, name: str):
    return next(stage for stage in report.stages if stage.name == name)


def test_valid_legacy_case_is_reported_but_does_not_fail(
    tmp_path: Path,
) -> None:
    pack = _make_pack(
        tmp_path,
        verification=_expected_contract(),
    )

    # The objective world model is unchanged by the SelfCheck exception.
    raw = PackInspector(pack).dead_ends()
    assert [(item.kind, item.target) for item in raw] == [
        ("unreachable", "legacy_scene"),
    ]

    # A valid, expected orphan is no longer emitted as a generic validator
    # warning; the contract itself is the auditable record.
    issues = validate_pack(pack)
    assert not any(
        issue.path == "unreachable:legacy_scene"
        for issue in issues
    )

    report = SelfCheck(
        pack,
        skip_smoke=True,
        skip_visual=True,
    ).run()
    assert report.ok

    dead_ends = _stage(report, "dead_ends")
    assert dead_ends.ok
    assert dead_ends.details["items"][0]["target"] == "legacy_scene"
    assert dead_ends.details["blocking_items"] == []
    assert dead_ends.details["expected"] == [{
        "case_id": "legacy_branch",
        "kind": "scene",
        "target": "legacy_scene",
        "reason": "Retained for old saves and explicit replay only.",
        "status": "unreachable",
    }]
    assert dead_ends.details["ignored"][0]["case_id"] == "legacy_branch"

    reachability = _stage(report, "reachability")
    assert reachability.ok
    assert [item["ending_id"] for item in reachability.details["strands"]] == [
        "ending_legacy",
    ]
    assert reachability.details["blocking_strands"] == []
    assert reachability.details["ignored"][0]["reason"].startswith("Retained")


@pytest.mark.parametrize(
    "verification",
    [
        {
            "expected_unreachable": {
                "empty_reason": {
                    "scenes": ["legacy_scene"],
                    "reason": "   ",
                },
            },
        },
        {
            "expected_unreachable": {
                "no_targets": {
                    "scenes": [],
                    "endings": [],
                    "reason": "Nothing is actually named.",
                },
            },
        },
        {
            "expected_unreachable": {
                "extra_field": {
                    "scenes": ["legacy_scene"],
                    "reason": "Has an unsupported suppress switch.",
                    "suppress_everything": True,
                },
            },
        },
        [{"reason": "verification must be a mapping"}],
    ],
)
def test_malformed_contract_is_a_validator_error(
    tmp_path: Path,
    verification: object,
) -> None:
    pack = _make_pack(tmp_path, verification=verification)
    errors = [
        issue
        for issue in validate_pack(pack)
        if issue.severity == "error"
        and issue.path.startswith("verification")
    ]
    assert errors


def test_unknown_and_wrong_kind_targets_are_validator_errors(
    tmp_path: Path,
) -> None:
    pack = _make_pack(
        tmp_path,
        verification=_expected_contract(
            # ``hub`` exists, but it is a location rather than a scene.
            scenes=["hub", "typo_scene"],
            # ``legacy_scene`` exists, but it is a scene rather than an ending.
            endings=["legacy_scene", "typo_ending"],
        ),
    )
    errors = [
        issue
        for issue in validate_pack(pack)
        if issue.severity == "error"
    ]
    assert {issue.path for issue in errors} >= {
        "verification.expected_unreachable.legacy_branch.scenes[0]",
        "verification.expected_unreachable.legacy_branch.scenes[1]",
        "verification.expected_unreachable.legacy_branch.endings[0]",
        "verification.expected_unreachable.legacy_branch.endings[1]",
    }
    assert all("不是已知" in issue.message for issue in errors)


def test_target_cannot_be_redeclared_under_another_case(
    tmp_path: Path,
) -> None:
    verification = _expected_contract()
    verification["expected_unreachable"]["duplicate_case"] = {
        "scenes": ["legacy_scene"],
        "endings": ["ending_legacy"],
        "reason": "A competing reason must not silently replace the first.",
    }
    pack = _make_pack(tmp_path, verification=verification)
    errors = [
        issue
        for issue in validate_pack(pack)
        if issue.severity == "error" and "不可跨 case 重複" in issue.message
    ]
    assert {issue.path for issue in errors} == {
        "verification.expected_unreachable.duplicate_case.scenes[0]",
        "verification.expected_unreachable.duplicate_case.endings[0]",
    }


def test_reachable_targets_make_the_exception_stale_and_fail(
    tmp_path: Path,
) -> None:
    pack = _make_pack(
        tmp_path,
        verification=_expected_contract(),
        legacy_reachable=True,
    )
    warnings = [
        issue
        for issue in validate_pack(pack)
        if issue.severity == "warning"
        and "exception 已過期" in issue.message
    ]
    assert [issue.path for issue in warnings] == [
        "verification.expected_unreachable.legacy_branch.scenes[0]",
    ]

    report = SelfCheck(
        pack,
        stop_on_failure=False,
        skip_smoke=True,
        skip_visual=True,
    ).run()
    dead_ends = _stage(report, "dead_ends")
    reachability = _stage(report, "reachability")
    assert not dead_ends.ok
    assert dead_ends.details["stale_expectations"][0]["target"] == "legacy_scene"
    assert not reachability.ok
    assert (
        reachability.details["stale_expectations"][0]["target"]
        == "ending_legacy"
    )
    assert not report.ok


def test_expected_unreachable_only_ignores_the_unreachable_finding(
    tmp_path: Path,
) -> None:
    pack = _make_pack(
        tmp_path,
        verification=_expected_contract(),
        legacy_dead_choices=True,
    )
    stage = SelfCheck(
        pack,
        skip_smoke=True,
        skip_visual=True,
        skip_softlock=True,
    )._run_dead_ends()

    assert not stage.ok
    assert [(item["kind"], item["target"]) for item in stage.details["items"]] == [
        ("scene", "legacy_scene"),
        ("unreachable", "legacy_scene"),
    ]
    assert [(item["kind"], item["target"])
            for item in stage.details["blocking_items"]] == [
        ("scene", "legacy_scene"),
    ]
    assert [(item["kind"], item["target"])
            for item in stage.details["ignored"]] == [
        ("unreachable", "legacy_scene"),
    ]
