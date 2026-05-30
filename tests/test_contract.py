"""Narrative-contract checks (``world_gal_game.dev.contract``).

Covers the four expectation kinds (reachable / unreachable / holds /
path_reaches), the pass/fail rollup, the no-contract marker, and the bundled
demo_pack contract — the behavioural regression gate that pairs with the warm
edit loop's structural impact.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from world_gal_game.dev.contract import (
    ContractChecker,
    Expectation,
    NarrativeContract,
    check_contract,
)

DEMO = "demo_pack"
DEMO_CONTRACT = "games/demo_pack/contracts.yaml"


def test_demo_contract_passes() -> None:
    report = check_contract(DEMO, contract_path=DEMO_CONTRACT)
    assert report["ok"] is True
    assert report["passed"] == report["total"] == 4


def test_reachable_and_unreachable() -> None:
    contract = NarrativeContract(expectations=[
        Expectation(name="quest startable", kind="reachable",
                    setup=[{"op": "start_scene", "scene": "prologue"}],
                    goal={"flag": "quest_started"}, max_steps=20),
        Expectation(name="nonsense unreachable", kind="unreachable",
                    setup=[{"op": "start_scene", "scene": "prologue"}],
                    goal={"flag": "zzz_never"}, max_nodes=400),
    ])
    report = ContractChecker(DEMO).check(contract)
    assert report["ok"] is True
    assert {r["name"]: r["ok"] for r in report["results"]} == {
        "quest startable": True, "nonsense unreachable": True}


def test_holds_and_path_reaches_deterministic() -> None:
    contract = NarrativeContract(expectations=[
        # `holds` with expect:false — fresh state, flag unset.
        Expectation(name="flag unset at start", kind="holds",
                    goal={"flag": "anything"}, expect=False),
        # `path_reaches` — replaying the path lands on the goal.
        Expectation(name="set flag via path", kind="path_reaches",
                    path=[{"op": "set_flag", "key": "qa_done", "value": True}],
                    goal={"flag": "qa_done"}),
    ])
    report = ContractChecker(DEMO).check(contract)
    assert report["ok"] is True


def test_failing_expectation_reported() -> None:
    contract = NarrativeContract(expectations=[
        Expectation(name="bogus reachable", kind="reachable",
                    setup=[{"op": "start_scene", "scene": "prologue"}],
                    goal={"flag": "zzz_never"}, max_nodes=300),
    ])
    report = ContractChecker(DEMO).check(contract)
    assert report["ok"] is False
    assert report["passed"] == 0 and report["total"] == 1
    assert report["results"][0]["ok"] is False


def test_no_contract_marker() -> None:
    report = check_contract(DEMO, contract_path="does/not/exist.yaml")
    assert report["ok"] is True
    assert report["no_contract"] is True
    assert report["total"] == 0


def test_expectation_is_strict() -> None:
    with pytest.raises(ValidationError):
        Expectation(name="x", kind="reachable", surprise="y")
    with pytest.raises(ValidationError):
        Expectation(name="x", kind="not_a_kind")
