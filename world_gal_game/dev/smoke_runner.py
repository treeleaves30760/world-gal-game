"""SmokeRunner — discover and run every ``test_*.json`` script in a pack.

Packs ship one or more scripted routes under ``games/<pack>/scripts/`` (the
demo pack has three: ``test_lover_route.json``, ``test_friend_route.json``,
``test_alone_route.json``). They're plain JSON scripts of HeadlessSession
operations.

SmokeRunner discovers every such file, runs each through
:class:`HeadlessSession`, captures the final snapshot, and reports
pass / fail. A run *passes* when:

- no per-command result carries an ``"error"`` key
- at least one ``ending_*`` flag is set (heuristic for "we reached the end")

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
    """Outcome of a single script run."""

    script: str
    ok: bool
    duration_s: float
    ending_flag: str | None = None
    errors: list[str] = field(default_factory=list)
    commands_run: int = 0
    final_location: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "script": self.script,
            "ok": self.ok,
            "duration_s": round(self.duration_s, 3),
            "ending_flag": self.ending_flag,
            "errors": self.errors,
            "commands_run": self.commands_run,
            "final_location": self.final_location,
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
        for i, result in enumerate(results):
            cmd = commands[i] if i < len(commands) else {}
            if isinstance(result, dict) and result.get("error"):
                errors.append(
                    f"command #{i} ({cmd.get('op')!r}) errored: {result['error']}"
                )
            elif isinstance(result, dict) and result.get("ok") is False:
                errors.append(
                    f"command #{i} ({cmd.get('op')!r}) returned ok=False: "
                    f"{result}"
                )

        snap = sess.inspect()
        ending = next(
            (k for k in snap.get("flags", {}) if k.startswith("ending_")
             and snap["flags"][k]),
            None,
        )
        elapsed = time.monotonic() - start
        ok = not errors and ending is not None
        return ScriptResult(
            script=rel,
            ok=ok,
            duration_s=elapsed,
            ending_flag=ending,
            errors=errors,
            commands_run=len(commands),
            final_location=snap.get("location"),
        )

    def run(self, *, pack_name: str | None = None) -> SmokeReport:
        """Run every discovered script. Aggregate into a :class:`SmokeReport`."""
        report = SmokeReport(pack_root=str(self.pack_root))
        for script in self.discover():
            res = self.run_one(script, pack_name=pack_name)
            _log.info(
                "smoke %-40s %s in %.2fs (ending=%s)",
                res.script,
                "ok " if res.ok else "FAIL",
                res.duration_s,
                res.ending_flag,
            )
            report.results.append(res)
        return report
