"""Narrative contracts — cheap behavioural regression checks for a pack.

The warm edit loop's ``impact`` delta (``dev/world_model``) answers "did my edit
break the *structure*?" — reachability, dead-ends, declared state. A narrative
contract answers the complementary question: "does the pack still *behave* the
way the author intends?" — e.g. "from the prologue, the lover route still
reaches ``ending_lover``", "the bad-end is still unreachable without betraying
the heroine", "after accepting the quest, ``quest_started`` holds".

A contract is a small declarative file (``contracts.yaml``) of named
expectations, each one of four kinds:

- ``reachable``   — a goal predicate is reachable (planner BFS from ``setup``).
- ``unreachable`` — a goal predicate is *not* reachable within the caps.
- ``holds``       — after running ``setup``, the goal predicate holds (``expect:
  false`` asserts it must *not* hold).
- ``path_reaches``— replaying ``setup + path`` lands on the goal (a pinned,
  deterministic walkthrough — the cheapest, most precise check).

Goals use the same predicate vocabulary as ``HeadlessSession.assert_expect`` /
the planner: ``{flag, equals?}`` · ``{affection, gte|lt|equals, stat?}`` ·
``{scene_played}`` · ``{condition: {...}}``. The whole file is checked in one
call (``wgg contract <pack>``), so an agent gets behavioural regression safety
for the cost of one command after an edit — no per-check process spawn.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class Expectation(BaseModel):
    """One named narrative invariant to verify against the pack."""

    model_config = ConfigDict(extra="forbid")

    name: str
    kind: Literal["reachable", "unreachable", "holds", "path_reaches"]
    goal: dict[str, Any] = Field(default_factory=dict)
    setup: list[dict[str, Any]] = Field(default_factory=list)
    path: list[dict[str, Any]] = Field(default_factory=list)
    # For `holds` / `path_reaches`: whether the goal is expected to hold.
    expect: bool = True
    # Search caps for `reachable` / `unreachable`.
    max_steps: int = 30
    max_nodes: int = 4000


class NarrativeContract(BaseModel):
    """A list of expectations, loaded from a pack's ``contracts.yaml``."""

    model_config = ConfigDict(extra="forbid")

    expectations: list[Expectation] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Path | str) -> "NarrativeContract":
        """Load a contract from a YAML file (a bare list or ``{expectations: [...]}``)."""
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict) and "expectations" in data:
            data = data["expectations"]
        return cls(expectations=[Expectation(**e) for e in (data or [])])

    @staticmethod
    def default_path(pack_root: Path | str) -> Path | None:
        """Locate a pack's contract file: ``contracts.yaml`` then ``content/``."""
        root = Path(pack_root)
        for candidate in (root / "contracts.yaml", root / "content" / "contracts.yaml"):
            if candidate.is_file():
                return candidate
        return None


class ContractChecker:
    """Run a :class:`NarrativeContract` against a pack and report pass/fail.

    Each expectation opens against the same ``pack`` + ``seed`` so the forward
    model is deterministic. ``reachable`` / ``unreachable`` use the planner;
    ``holds`` / ``path_reaches`` run a fresh session and assert the goal.
    """

    def __init__(self, pack: str, *, seed: int | None = 42) -> None:
        self.pack = pack
        self.seed = seed

    def check(self, contract: NarrativeContract) -> dict[str, Any]:
        results = [self._check_one(exp) for exp in contract.expectations]
        passed = sum(1 for r in results if r["ok"])
        return {"ok": passed == len(results), "passed": passed,
                "total": len(results), "results": results}

    def _check_one(self, exp: Expectation) -> dict[str, Any]:
        try:
            if exp.kind in ("reachable", "unreachable"):
                return self._check_search(exp)
            return self._check_state(exp)
        except Exception as exc:  # a broken expectation fails loudly, not fatally
            return {"name": exp.name, "kind": exp.kind, "ok": False,
                    "error": str(exc)}

    def _check_search(self, exp: Expectation) -> dict[str, Any]:
        from .planner import Planner
        res = Planner(self.pack, seed=self.seed).find_path(
            exp.goal, setup=exp.setup,
            max_depth=exp.max_steps, max_nodes=exp.max_nodes)
        want_reachable = exp.kind == "reachable"
        ok = (res.found == want_reachable)
        detail: dict[str, Any] = {"found": res.found,
                                  "nodes_explored": res.nodes_explored}
        if res.found:
            detail["depth"] = res.depth
            if want_reachable:
                detail["path"] = res.path
        return {"name": exp.name, "kind": exp.kind, "ok": ok, "detail": detail}

    def _check_state(self, exp: Expectation) -> dict[str, Any]:
        from ..config import EngineConfig
        from ..headless import HeadlessSession
        sess = HeadlessSession.open(EngineConfig(seed=self.seed), pack=self.pack)
        ops = list(exp.setup)
        if exp.kind == "path_reaches":
            ops += list(exp.path)
        if ops:
            sess.run_script(ops)
        verdict = sess.assert_expect(exp.goal)
        holds = bool(verdict.get("ok"))
        ok = (holds == exp.expect)
        return {"name": exp.name, "kind": exp.kind, "ok": ok,
                "detail": {"holds": holds, "expected": exp.expect,
                           "actual": verdict.get("actual"),
                           "assert": verdict.get("assert")}}


def check_contract(pack: str, *, contract_path: Path | str | None = None,
                   seed: int | None = 42) -> dict[str, Any]:
    """Load + check a pack's contract; convenience for the CLI.

    Resolves ``contract_path`` (or the pack's default ``contracts.yaml``) and
    returns the :meth:`ContractChecker.check` report. Returns a ``no_contract``
    marker (rather than failing) when a pack ships none.
    """
    path = Path(contract_path) if contract_path else NarrativeContract.default_path(pack)
    if path is None or not Path(path).is_file():
        return {"ok": True, "no_contract": True, "passed": 0, "total": 0,
                "results": []}
    contract = NarrativeContract.load(path)
    report = ContractChecker(pack, seed=seed).check(contract)
    report["contract"] = str(path)
    return report
