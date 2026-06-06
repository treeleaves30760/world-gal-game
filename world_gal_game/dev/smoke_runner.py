"""SmokeRunner — discover and run every ``test_*.json`` script in a pack.

Packs ship one or more scripted routes under ``games/<pack>/scripts/`` (the
demo pack has three: ``test_lover_route.json``, ``test_friend_route.json``,
``test_alone_route.json``). They're plain JSON scripts of HeadlessSession
operations.

SmokeRunner discovers every such file, runs each through
:class:`HeadlessSession`, captures the final snapshot, and reports
pass / fail.

**Pass criterion (per script).** A script always fails if any command errored
(an ``"error"`` key or an ``ok == False`` result). Beyond that, the criterion
depends on whether the script makes explicit assertions:

- **Assertion-based scripts** — any script containing one or more ``assert``
  ops passes iff it ran clean **and every** ``assert`` passed. A script whose
  asserts *fail* is reported FAIL even if it happens to set an ``ending_*``
  flag, so a real expectation regression can never hide behind the
  ending heuristic.
- **Heuristic scripts** — a script with no ``assert`` ops keeps the original
  heuristic: it passes iff it ran clean and at least one ``ending_*`` flag is
  set ("we reached the end").

This split is the fix for the original heuristic-only rule, which silently
"passed" assertion scripts that never set an ending flag (so a failing assert
read as a false alarm rather than a real failure) and "passed" any script that
set an ending flag even when its asserts had failed.

Use it as a CI gate; the ``wgg smoke`` CLI exits non-zero on any failure.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_log = logging.getLogger("world_gal_game.dev.smoke")


@dataclass
class ScriptResult:
    """Outcome of a single script run.

    ``criterion`` records which rule decided the pass/fail — ``"assert"`` when
    the script carried ``assert`` ops (so its verdict is "all asserts passed"),
    ``"ending_flag"`` for the legacy heuristic, or ``"error"`` when a command
    failed outright. ``asserts_total`` / ``asserts_passed`` summarise the
    assertions, and ``failed_asserts`` lists each failed assertion (its op index,
    the human-readable ``assert`` description, and the actual value) so a CI log
    points straight at the broken expectation.
    """

    script: str
    ok: bool
    duration_s: float
    ending_flag: str | None = None
    errors: list[str] = field(default_factory=list)
    commands_run: int = 0
    final_location: str | None = None
    criterion: str = "ending_flag"
    asserts_total: int = 0
    asserts_passed: int = 0
    failed_asserts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "script": self.script,
            "ok": self.ok,
            "duration_s": round(self.duration_s, 3),
            "ending_flag": self.ending_flag,
            "errors": self.errors,
            "commands_run": self.commands_run,
            "final_location": self.final_location,
            "criterion": self.criterion,
            "asserts_total": self.asserts_total,
            "asserts_passed": self.asserts_passed,
            "failed_asserts": self.failed_asserts,
        }


@dataclass
class SmokeReport:
    pack_root: str
    results: list[ScriptResult] = field(default_factory=list)

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
            "results": [r.to_dict() for r in self.results],
        }


class SmokeRunner:
    """Discover + run scripted routes against a pack."""

    def __init__(self, pack_root: Path | str) -> None:
        self.pack_root = Path(pack_root).resolve()
        if not self.pack_root.exists():
            raise FileNotFoundError(f"pack root not found: {self.pack_root}")

    # ------------------------------------------------------------------
    # Discovery

    def discover(self) -> list[Path]:
        """Return every ``scripts/test_*.json`` under the pack root, sorted."""
        scripts_dir = self.pack_root / "scripts"
        if not scripts_dir.is_dir():
            return []
        return sorted(scripts_dir.glob("test_*.json"))

    # ------------------------------------------------------------------
    # Run

    def run_one(self, script_path: Path,
                *, pack_name: str | None = None) -> ScriptResult:
        """Execute one script via :class:`HeadlessSession`."""
        from world_gal_game.config import EngineConfig
        from world_gal_game.headless import HeadlessSession

        rel = str(script_path.relative_to(self.pack_root))
        start = time.monotonic()
        commands: list[dict[str, Any]] = []
        try:
            commands = json.loads(script_path.read_text(encoding="utf-8")).get(
                "commands", [])
        except Exception as exc:
            return ScriptResult(
                script=rel, ok=False, duration_s=0.0,
                errors=[f"failed to parse script: {exc}"],
            )

        pack = pack_name or self.pack_root.name
        sess = HeadlessSession.open(EngineConfig(), pack=pack)
        try:
            results = sess.run_script(commands)
        except Exception as exc:
            return ScriptResult(
                script=rel, ok=False, duration_s=time.monotonic() - start,
                errors=[f"run_script raised: {exc}"],
                commands_run=len(commands),
            )

        errors: list[str] = []
        asserts_total = 0
        asserts_passed = 0
        failed_asserts: list[dict[str, Any]] = []
        for i, result in enumerate(results):
            cmd = commands[i] if i < len(commands) else {}
            op = cmd.get("op")
            if not isinstance(result, dict):
                continue
            # ``assert`` ops report their pass/fail in ``ok``; they are an
            # expectation, not a hard error, so a failed assert is collected
            # separately rather than as a command error. (An assert that *errors*
            # — e.g. an unknown assert form — carries an ``error`` key and is
            # caught by the generic error branch below.)
            if op == "assert" and "error" not in result:
                asserts_total += 1
                if result.get("ok"):
                    asserts_passed += 1
                else:
                    failed_asserts.append({
                        "index": i,
                        "assert": result.get("assert", str(cmd)),
                        "actual": result.get("actual"),
                    })
                continue
            if result.get("error"):
                errors.append(
                    f"command #{i} ({op!r}) errored: {result['error']}"
                )
            elif result.get("ok") is False:
                errors.append(
                    f"command #{i} ({op!r}) returned ok=False: {result}"
                )

        snap = sess.inspect()
        ending = next(
            (k for k in snap.get("flags", {}) if k.startswith("ending_")
             and snap["flags"][k]),
            None,
        )
        elapsed = time.monotonic() - start

        # Pass criterion: a clean run is required either way. If the script made
        # any assertions, it passes iff *all* of them passed (the ending
        # heuristic no longer applies — an assert script states its own success
        # condition). Otherwise fall back to the "an ending_* flag was set"
        # heuristic.
        if errors:
            criterion = "error"
            ok = False
        elif asserts_total > 0:
            criterion = "assert"
            ok = not failed_asserts
        else:
            criterion = "ending_flag"
            ok = ending is not None
        return ScriptResult(
            script=rel,
            ok=ok,
            duration_s=elapsed,
            ending_flag=ending,
            errors=errors,
            commands_run=len(commands),
            final_location=snap.get("location"),
            criterion=criterion,
            asserts_total=asserts_total,
            asserts_passed=asserts_passed,
            failed_asserts=failed_asserts,
        )

    def run(self, *, pack_name: str | None = None) -> SmokeReport:
        """Run every discovered script. Aggregate into a :class:`SmokeReport`."""
        report = SmokeReport(pack_root=str(self.pack_root))
        for script in self.discover():
            res = self.run_one(script, pack_name=pack_name)
            if res.criterion == "assert":
                detail = f"asserts={res.asserts_passed}/{res.asserts_total}"
            else:
                detail = f"ending={res.ending_flag}"
            _log.info(
                "smoke %-40s %s in %.2fs (%s)",
                res.script,
                "ok " if res.ok else "FAIL",
                res.duration_s,
                detail,
            )
            report.results.append(res)
        return report
