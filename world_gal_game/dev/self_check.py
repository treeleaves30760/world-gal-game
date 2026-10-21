"""SelfCheck — the integrated pack verification pipeline.

Stages, in order:

1. **schema** — `validator.validate_pack` (pydantic + extra-field checks)
2. **refs** — same `validate_pack` pass collects cross-file ref errors
3. **dead_ends** — `PackInspector.dead_ends()` (orphan / unreachable / no-op),
   minus strictly validated ``verification.expected_unreachable`` scene
   exceptions that are still objectively unreachable
4. **softlock** — `SoftLockChecker` (every choice-bearing scene must offer ≥1
   selectable option on every reachable path; catches a menu that can become
   all-locked with no fallthrough — the stance-gated-climax defect class)
5. **reachability** — `EndingReachabilityChecker` (strand guard: can ordinary
   play reach each declared ending / route-terminal ending? — catches a route
   that strands mid-arc, which from-anywhere reachability and the smoke runner
   both miss). The default verdict source is the fast, exhaustive *static*
   fixpoint (finishes in seconds with real ok/strand verdicts);
   ``reachability_deep`` opts in to the slow organic planner replay on top.
6. **smoke** — `SmokeRunner.run()` (replays every `scripts/test_*.json`)
7. **visual** — `VisualCheck.run()` (optional, requires SDL working)

Earlier stages gate later ones (default ``stop_on_failure=True``): if the
schema check finds errors we don't bother running the later stages, since
broken YAML almost certainly breaks those too.

Output is a :class:`SelfCheckReport` — JSON-friendly, machine-parseable
for CI gates and AI agents.

Typical usage::

    from world_gal_game.dev.self_check import SelfCheck

    sc = SelfCheck("games/demo_pack")
    report = sc.run()
    if not report.ok:
        for stage in report.stages:
            if not stage.ok:
                print(stage.name, "->", stage.summary)
    sys.exit(0 if report.ok else 1)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

_log = logging.getLogger("world_gal_game.dev.self_check")


StageName = Literal[
    "schema", "refs", "dead_ends", "softlock", "reachability", "smoke",
    "visual"]


@dataclass
class StageResult:
    name: StageName
    ok: bool
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    skipped: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "skipped": self.skipped,
            "summary": self.summary,
            "details": self.details,
        }


@dataclass
class SelfCheckReport:
    pack_root: str
    stages: list[StageResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.stages if not s.skipped)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack_root": self.pack_root,
            "ok": self.ok,
            "stages": [s.to_dict() for s in self.stages],
        }


class SelfCheck:
    """Run the seven verification stages against a pack."""

    def __init__(self, pack_root: Path | str,
                 *, stop_on_failure: bool = True,
                 skip_smoke: bool = False,
                 skip_visual: bool = True,
                 strict_visual_baselines: bool = False,
                 visual_scenarios: list[dict[str, Any]] | None = None,
                 visual_baselines_dir: Path | None = None,
                 visual_candidates_dir: Path | None = None,
                 smoke_seed: int | None = None,
                 skip_softlock: bool = False,
                 skip_reachability: bool = False,
                 reachability_deep: bool = False,
                 reachability_max_nodes: int = 700,
                 reachability_max_depth: int = 120,
                 reachability_time_budget_s: float = 30.0) -> None:
        self.pack_root = Path(pack_root).resolve()
        self.stop_on_failure = stop_on_failure
        # Smoke runs always, visual defaults to off (it needs a working SDL +
        # baselines, and CI doesn't always have those — opt in explicitly).
        self.skip_smoke = skip_smoke
        self.skip_visual = skip_visual
        # When visual checking is enabled, production CI can require every
        # scenario to have a pre-existing, human-reviewed baseline. This avoids
        # the development bootstrap rule ("first render becomes baseline")
        # turning a missing quality gate into a false pass.
        self.strict_visual_baselines = strict_visual_baselines
        # Production verification pins an explicit scenario document instead
        # of silently inheriting a future default-set change.  Ordinary
        # authoring keeps the representative engine defaults.
        self.visual_scenarios = visual_scenarios
        self.visual_baselines_dir = visual_baselines_dir
        self.visual_candidates_dir = visual_candidates_dir
        self.smoke_seed = smoke_seed
        # Soft-lock linter (static, fast) runs by default.
        self.skip_softlock = skip_softlock
        # Reachability (the strand guard) runs by default. The default verdict
        # source is the fast, exhaustive *static* fixpoint (seconds, real
        # ok/strand verdicts); ``reachability_deep`` opts in to the slow organic
        # planner replay on top. The node/time budgets apply only in deep mode —
        # see ``EndingReachabilityChecker.check_all``.
        self.skip_reachability = skip_reachability
        self.reachability_deep = reachability_deep
        self.reachability_max_nodes = reachability_max_nodes
        self.reachability_max_depth = reachability_max_depth
        self.reachability_time_budget_s = reachability_time_budget_s

    # ------------------------------------------------------------------
    # Run

    def run(self) -> SelfCheckReport:
        report = SelfCheckReport(pack_root=str(self.pack_root))

        # Stages 1 + 2: schema + refs (one validator pass; split by issue type)
        schema, refs = self._run_validator()
        report.stages.append(schema)
        if self._should_stop(schema, report):
            return report
        report.stages.append(refs)
        if self._should_stop(refs, report):
            return report

        # Stage 3: dead-ends (PackInspector)
        de = self._run_dead_ends()
        report.stages.append(de)
        if self._should_stop(de, report):
            return report

        # Stage 4: soft-lock linter (static; every choice-bearing scene must
        # offer a selectable option / fallthrough on every reachable path)
        if self.skip_softlock:
            report.stages.append(StageResult(
                name="softlock", ok=True, skipped=True,
                summary="softlock stage skipped",
            ))
        else:
            sl = self._run_softlock()
            report.stages.append(sl)
            if self._should_stop(sl, report):
                return report

        # Stage 5: reachability (organic strand guard)
        if self.skip_reachability:
            report.stages.append(StageResult(
                name="reachability", ok=True, skipped=True,
                summary="reachability stage skipped",
            ))
        else:
            reach = self._run_reachability()
            report.stages.append(reach)
            if self._should_stop(reach, report):
                return report

        # Stage 6: smoke routes
        if self.skip_smoke:
            report.stages.append(StageResult(
                name="smoke", ok=True, skipped=True,
                summary="smoke stage skipped",
            ))
        else:
            smoke = self._run_smoke()
            report.stages.append(smoke)
            if self._should_stop(smoke, report):
                return report

        # Stage 7: visual (optional)
        if self.skip_visual:
            report.stages.append(StageResult(
                name="visual", ok=True, skipped=True,
                summary="visual stage skipped",
            ))
        else:
            visual = self._run_visual()
            report.stages.append(visual)

        return report

    # ------------------------------------------------------------------
    # Stage implementations

    def _run_validator(self) -> tuple[StageResult, StageResult]:
        from ..validator import validate_pack
        issues = validate_pack(self.pack_root)

        schema_issues: list[dict[str, Any]] = []
        ref_issues: list[dict[str, Any]] = []
        for iss in issues:
            payload = {
                "severity": iss.severity,
                "file": iss.file,
                "path": iss.path,
                "message": iss.message,
                "hint": iss.hint,
            }
            # Heuristic: cross-reference findings (unknown id/scene, a scene
            # naming an expression missing from a character's portrait_set, or a
            # speaker↔portrait-character mismatch) go into refs; the rest
            # (schema/type/arg issues) into schema.
            if any(t in iss.message for t in ("不是已知", "next_scene",
                                              "portrait_set 中",
                                              "立繪卻是另一個角色")) \
                    or "scenes[" in iss.path and "next_scene" in iss.path:
                ref_issues.append(payload)
            else:
                schema_issues.append(payload)

        def _result(name: StageName, items: list[dict[str, Any]]) -> StageResult:
            errors = [i for i in items if i["severity"] == "error"]
            warnings = [i for i in items if i["severity"] == "warning"]
            ok = not errors
            return StageResult(
                name=name, ok=ok,
                summary=(f"{len(errors)} errors, {len(warnings)} warnings"
                         if items else "no issues"),
                details={
                    "errors": errors,
                    "warnings": warnings,
                },
            )
        return _result("schema", schema_issues), _result("refs", ref_issues)

    def _run_dead_ends(self) -> StageResult:
        from .pack_inspector import PackInspector
        from .verification_contract import audit_expected_unreachable
        try:
            ins = PackInspector(self.pack_root)
        except Exception as exc:
            return StageResult(
                name="dead_ends", ok=False,
                summary=f"inspector failed to load pack: {exc}",
                details={"error": str(exc)},
            )
        raw_dead_ends = ins.dead_ends()
        reachability = ins.reachability()
        audit = audit_expected_unreachable(
            ins.meta,
            scene_ids=set(ins.scenes_by_id),
            ending_ids={
                str(ending["id"])
                for ending in ins.endings
                if isinstance(ending, dict) and ending.get("id")
            },
            unreachable_scene_ids=set(reachability["unreachable"]),
        )

        raw_items = [
            {"kind": d.kind, "target": d.target, "file": d.file,
             "detail": d.detail}
            for d in raw_dead_ends
        ]
        blocking_items: list[dict[str, Any]] = []
        ignored: list[dict[str, Any]] = []
        for dead_end, payload in zip(raw_dead_ends, raw_items, strict=True):
            expected = (
                audit.scene_ref(dead_end.target)
                if dead_end.kind == "unreachable"
                else None
            )
            if expected is None:
                blocking_items.append(payload)
                continue
            ignored.append({
                **payload,
                "case_id": expected.case_id,
                "reason": expected.reason,
            })

        relevant_issue_codes = {
            "invalid_contract", "unknown_scene", "duplicate_scene",
            "stale_scene",
        }
        contract_issues = [
            issue.to_dict()
            for issue in audit.issues
            if issue.code in relevant_issue_codes
        ]
        stale = [
            ref.to_dict(status="reachable")
            for ref in audit.stale_scenes
        ]
        ok = not blocking_items and not contract_issues
        if blocking_items:
            summary = f"{len(blocking_items)} unexpected dead-end(s)"
        elif contract_issues:
            summary = "expected-unreachable scene contract is invalid or stale"
        elif ignored:
            summary = (
                "no unexpected dead-ends; "
                f"{len(ignored)} expected unreachable scene(s) ignored"
            )
        else:
            summary = "no dead-ends"

        return StageResult(
            name="dead_ends",
            ok=ok,
            summary=summary,
            details={
                # Preserve the raw PackInspector findings for auditability and
                # backward-compatible report consumers.
                "items": raw_items,
                "blocking_items": blocking_items,
                "expected": [
                    ref.to_dict(status="unreachable")
                    for ref in audit.active_scenes
                ],
                "ignored": ignored,
                "stale_expectations": stale,
                "contract_issues": contract_issues,
            },
        )

    def _run_softlock(self) -> StageResult:
        """Static soft-lock linter — fails if any choice-bearing scene can be
        reached in a state with no selectable choice and no fallthrough."""
        from .softlock import SoftLockChecker
        try:
            chk = SoftLockChecker(self.pack_root)
            locks = chk.check()
        except Exception as exc:
            return StageResult(
                name="softlock", ok=False,
                summary=f"soft-lock check raised: {exc}",
                details={"error": str(exc)},
            )
        return StageResult(
            name="softlock",
            ok=not locks,
            summary=(f"{len(locks)} potential soft-lock(s): "
                     + ", ".join(s.scene_id for s in locks)
                     if locks else "no soft-locks"),
            details={"items": [s.to_dict() for s in locks]},
        )

    def _run_smoke(self) -> StageResult:
        from .smoke_runner import SmokeRunner
        try:
            sr = SmokeRunner(self.pack_root, seed=self.smoke_seed)
        except Exception as exc:
            return StageResult(
                name="smoke", ok=False,
                summary=f"smoke runner failed: {exc}",
                details={"error": str(exc)},
            )
        scripts = sr.discover()
        if not scripts:
            return StageResult(
                name="smoke", ok=True, skipped=True,
                summary="no scripts/test_*.json found; skipped",
            )
        rep = sr.run()
        return StageResult(
            name="smoke", ok=rep.ok,
            summary=(f"{sum(1 for r in rep.results if r.ok)}/"
                     f"{len(rep.results)} scripts passed"),
            details=rep.to_dict(),
        )

    def _run_reachability(self) -> StageResult:
        """Organic ending-reachability — the strand guard.

        ``strand`` verdicts (provably / by-exhaustion unreachable endings) fail
        the stage. ``unverified`` verdicts (the bounded organic search ran out
        of budget, or a route had no lock-in flag to seed precisely) are surfaced
        as a warning but do **not** fail the stage — degrade gracefully rather
        than emit a false failure. A strictly validated
        ``verification.expected_unreachable`` ending may remain a strand for
        old-save/direct-replay compatibility; it is reported but does not block
        the stage. If that ending becomes reachable, the stale exception fails
        verification. A pack that declares no endings is skipped.
        """
        from .reachability import EndingReachabilityChecker
        from .verification_contract import audit_expected_unreachable
        try:
            chk = EndingReachabilityChecker(self.pack_root)
            results = chk.check_all(
                deep=self.reachability_deep,
                max_nodes=self.reachability_max_nodes,
                max_depth=self.reachability_max_depth,
                time_budget_s=self.reachability_time_budget_s)
        except Exception as exc:
            return StageResult(
                name="reachability", ok=False,
                summary=f"reachability check raised: {exc}",
                details={"error": str(exc)},
            )

        statuses = {result.ending_id: result.status for result in results}
        audit = audit_expected_unreachable(
            chk._meta,
            ending_ids=set(statuses),
            ending_statuses=statuses,
        )
        relevant_issue_codes = {
            "invalid_contract", "unknown_ending", "duplicate_ending",
            "stale_ending",
        }
        contract_issues = [
            issue.to_dict()
            for issue in audit.issues
            if issue.code in relevant_issue_codes
        ]
        if not results and not audit.declared_endings and not contract_issues:
            return StageResult(
                name="reachability", ok=True, skipped=True,
                summary="no declared endings; skipped",
            )

        strands = [r for r in results if r.status == "strand"]
        blocking_strands = [
            result
            for result in strands
            if result.ending_id not in audit.active_ending_ids
        ]
        ignored: list[dict[str, Any]] = []
        for result in strands:
            expected = audit.ending_ref(result.ending_id)
            if expected is None:
                continue
            ignored.append({
                **result.to_dict(),
                "case_id": expected.case_id,
                "reason": expected.reason,
            })
        unverified = [r for r in results if r.status == "unverified"]
        ok_count = sum(1 for r in results if r.status == "ok")
        ok = not blocking_strands and not contract_issues
        if blocking_strands:
            summary = (f"{len(blocking_strands)} unexpected strand(s): "
                       + ", ".join(r.ending_id for r in blocking_strands))
        elif contract_issues:
            summary = "expected-unreachable ending contract is invalid or stale"
        else:
            summary = (f"{ok_count}/{len(results)} ending(s) reachable"
                       + (f", {len(unverified)} unverified"
                          if unverified else "")
                       + (f", {len(ignored)} expected strand(s) ignored"
                          if ignored else ""))
        return StageResult(
            name="reachability", ok=ok, summary=summary,
            details={
                # ``strands`` remains the objective raw checker output.
                "strands": [r.to_dict() for r in strands],
                "blocking_strands": [
                    r.to_dict() for r in blocking_strands
                ],
                "unverified": [r.to_dict() for r in unverified],
                "reachable": [r.to_dict() for r in results if r.status == "ok"],
                "expected": [
                    ref.to_dict(status="strand")
                    for ref in audit.active_endings
                ],
                "ignored": ignored,
                "stale_expectations": [
                    ref.to_dict(status=statuses.get(ref.target, "missing"))
                    for ref in audit.stale_endings
                ],
                "contract_issues": contract_issues,
            },
        )

    def _run_visual(self) -> StageResult:
        from .visual_check import VisualCheck
        try:
            vc = VisualCheck(
                self.pack_root,
                baselines_dir=self.visual_baselines_dir,
                candidates_dir=self.visual_candidates_dir,
                strict_baselines=self.strict_visual_baselines,
            )
        except Exception as exc:
            return StageResult(
                name="visual", ok=False,
                summary=f"visual_check failed to construct: {exc}",
                details={"error": str(exc)},
            )
        try:
            rep = vc.run(
                self.visual_scenarios
                if self.visual_scenarios is not None
                else VisualCheck.default_scenarios()
            )
        except Exception as exc:
            return StageResult(
                name="visual", ok=False,
                summary=f"visual check raised: {exc}",
                details={"error": str(exc)},
            )
        return StageResult(
            name="visual", ok=rep.ok,
            summary=(f"{sum(1 for r in rep.results if r.ok)}/"
                     f"{len(rep.results)} scenarios passed"
                     + (", created baseline(s)"
                        if any(r.created_baseline for r in rep.results)
                        else "")),
            details=rep.to_dict(),
        )

    def _should_stop(self, stage: StageResult,
                     report: SelfCheckReport) -> bool:
        if not self.stop_on_failure:
            return False
        if stage.ok or stage.skipped:
            return False
        # Append skip placeholders for downstream stages so the report
        # shape stays stable.
        downstream = self._downstream_of(stage.name)
        for n in downstream:
            report.stages.append(StageResult(
                name=n, ok=True, skipped=True,
                summary=f"skipped due to earlier {stage.name} failure",
            ))
        return True

    @staticmethod
    def _downstream_of(name: StageName) -> list[StageName]:
        order: list[StageName] = [
            "schema", "refs", "dead_ends", "softlock", "reachability",
            "smoke", "visual"]
        idx = order.index(name)
        return order[idx + 1:]
