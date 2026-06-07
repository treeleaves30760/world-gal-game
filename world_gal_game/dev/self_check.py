"""SelfCheck — the integrated pack verification pipeline.

Stages, in order:

1. **schema** — `validator.validate_pack` (pydantic + extra-field checks)
2. **refs** — same `validate_pack` pass collects cross-file ref errors
3. **dead_ends** — `PackInspector.dead_ends()` (orphan / unreachable / no-op)
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
    """Run the five verification stages against a pack."""

    def __init__(self, pack_root: Path | str,
                 *, stop_on_failure: bool = True,
                 skip_smoke: bool = False,
                 skip_visual: bool = True,
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

        # Stage 5: smoke routes
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

        # Stage 6: visual (optional)
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
        try:
            ins = PackInspector(self.pack_root)
        except Exception as exc:
            return StageResult(
                name="dead_ends", ok=False,
                summary=f"inspector failed to load pack: {exc}",
                details={"error": str(exc)},
            )
        de = ins.dead_ends()
        details = [
            {"kind": d.kind, "target": d.target, "file": d.file,
             "detail": d.detail}
            for d in de
        ]
        return StageResult(
            name="dead_ends",
            ok=not de,
            summary=(f"{len(de)} dead-end(s)" if de else "no dead-ends"),
            details={"items": details},
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
            sr = SmokeRunner(self.pack_root)
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
        than emit a false failure. A pack that declares no endings is skipped.
        """
        from .reachability import EndingReachabilityChecker
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
        if not results:
            return StageResult(
                name="reachability", ok=True, skipped=True,
                summary="no declared endings; skipped",
            )
        strands = [r for r in results if r.status == "strand"]
        unverified = [r for r in results if r.status == "unverified"]
        ok_count = sum(1 for r in results if r.status == "ok")
        ok = not strands
        if strands:
            summary = (f"{len(strands)} strand(s): "
                       + ", ".join(r.ending_id for r in strands))
        else:
            summary = (f"{ok_count}/{len(results)} ending(s) reachable"
                       + (f", {len(unverified)} unverified"
                          if unverified else ""))
        return StageResult(
            name="reachability", ok=ok, summary=summary,
            details={
                "strands": [r.to_dict() for r in strands],
                "unverified": [r.to_dict() for r in unverified],
                "reachable": [r.to_dict() for r in results if r.status == "ok"],
            },
        )

    def _run_visual(self) -> StageResult:
        from .visual_check import VisualCheck
        try:
            vc = VisualCheck(self.pack_root)
        except Exception as exc:
            return StageResult(
                name="visual", ok=False,
                summary=f"visual_check failed to construct: {exc}",
                details={"error": str(exc)},
            )
        try:
            rep = vc.run(VisualCheck.default_scenarios())
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
