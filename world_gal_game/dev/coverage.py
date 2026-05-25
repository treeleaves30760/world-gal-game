"""CoverageTracker — how complete a test / playthrough actually was.

This sits beside :class:`PackInspector` (which answers "what does this
pack contain?") and :meth:`HeadlessSession.inspect` (the player view) at
the developer tier of pillar B. It answers the orthogonal question an
agent asks *after* a run: **how much of the pack did I exercise?**

Concretely it compares the four content totals a pack declares —

- scenes
- dialogue lines
- choices
- endings

— against what a :class:`HeadlessSession` actually reached, and reports
each as a :class:`Bucket` (``seen`` / ``total`` / ``pct`` / ``missing``).
Today the engine only tracks a scene-visited *count*; this turns that into
a full, JSON-friendly coverage report with the exact ids still unseen.

The tracker reads totals through :func:`content_loader.load_pack`, so they
come from the typed pydantic model (real :class:`Scene` / :class:`Choice` /
:class:`Ending` objects), not a re-parse of the YAML. The *observed* side is
derived from a session's execution ``transcript`` plus its evaluated endings,
which keeps the report pure with respect to the session — call
:meth:`report` after any run, or :meth:`report_from` to test the core
logic without a live session.

Coverage is a *progress signal*, not a correctness check: a 100% report
means every id was touched at least once, not that every path is bug-free.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..content_loader import load_pack


# ----------------------------------------------------------------------
# Report model


class Bucket(BaseModel):
    """Coverage of one content dimension (scenes / lines / choices / endings).

    ``seen`` and ``total`` count *known* ids (the intersection with the
    pack's declared set), ``pct`` is ``seen / total`` as a percentage, and
    ``missing`` lists the still-unseen ids, sorted for stable output.
    """

    model_config = ConfigDict(extra="forbid")

    seen: int
    total: int
    pct: float
    missing: list[str] = []

    @classmethod
    def make(cls, seen_ids: set, total_ids: set) -> "Bucket":
        """Build a bucket from an observed set and the pack's total set.

        Only ids present in ``total_ids`` count toward ``seen`` (so a stray
        observed id from an unexpected event can't push coverage past 100%).
        An empty pack dimension reports ``100.0`` — nothing to cover.
        """
        total_ids = set(total_ids)
        seen = len(set(seen_ids) & total_ids)
        total = len(total_ids)
        pct = round(100 * seen / total, 1) if total else 100.0
        missing = sorted(total_ids - set(seen_ids))
        return cls(seen=seen, total=total, pct=pct, missing=missing)


class CoverageReport(BaseModel):
    """The four coverage buckets for one playthrough. JSON-friendly."""

    model_config = ConfigDict(extra="forbid")

    scenes: Bucket
    lines: Bucket
    choices: Bucket
    endings: Bucket

    def summary(self) -> str:
        """A one-line ``scenes A/B · lines C/D · ...`` rollup."""
        return (
            f"scenes {self.scenes.seen}/{self.scenes.total} "
            f"({self.scenes.pct}%) · "
            f"lines {self.lines.seen}/{self.lines.total} "
            f"({self.lines.pct}%) · "
            f"choices {self.choices.seen}/{self.choices.total} "
            f"({self.choices.pct}%) · "
            f"endings {self.endings.seen}/{self.endings.total} "
            f"({self.endings.pct}%)"
        )


# ----------------------------------------------------------------------
# CoverageTracker


class CoverageTracker:
    """Measures playthrough coverage against a pack's declared totals.

    Construct with ``CoverageTracker(pack_root)`` (either the pack root or
    its ``content/`` subdirectory). The constructor loads the pack once via
    :func:`load_pack` and freezes the four total id-sets; :meth:`report`
    then diffs a session's run against them.

    Total id conventions:

    - scenes  — the scene id, e.g. ``"prologue"``
    - lines   — ``"<scene_id>#<line_index>"``, e.g. ``"prologue#0"``
    - choices — ``"<scene_id>.<choice_id>"``, e.g. ``"meet_heroine.accept_quest"``
    - endings — the ending id, e.g. ``"ending_lover"``
    """

    def __init__(self, pack_root: Path) -> None:
        pack_root = Path(pack_root).resolve()
        if (pack_root / "content").is_dir():
            content_root = pack_root / "content"
        else:
            content_root = pack_root
        self.pack_root = pack_root
        self.content_root = content_root

        state, _registry, _meta = load_pack(content_root)
        scenes = state.story.scenes
        endings = state.endings.endings

        self.scene_ids: set[str] = set(scenes.keys())
        self.line_ids: set[str] = {
            f"{sid}#{i}"
            for sid, scene in scenes.items()
            for i in range(len(scene.lines))
        }
        self.choice_ids: set[str] = {
            f"{sid}.{choice.id}"
            for sid, scene in scenes.items()
            for choice in scene.choices
        }
        self.ending_ids: set[str] = set(endings.keys())

    # ------------------------------------------------------------------
    # Convenience totals (read-only views over the frozen id-sets)

    @property
    def total_scenes(self) -> int:
        return len(self.scene_ids)

    @property
    def total_lines(self) -> int:
        return len(self.line_ids)

    @property
    def total_choices(self) -> int:
        return len(self.choice_ids)

    @property
    def total_endings(self) -> int:
        return len(self.ending_ids)

    # ------------------------------------------------------------------
    # Reporting

    def report(self, session: Any) -> CoverageReport:
        """Coverage of one session's run. Call after :meth:`run_script`.

        Scenes seen = the session's ``story.played`` set UNION any scene id
        that appears in a transcript line event (so a scene counts even if
        ``played`` lags). Endings reached = those whose ``requires`` all
        evaluate truthy against the live state.
        """
        played = set(getattr(getattr(session, "state", None), "story", None)
                     and session.state.story.played or set())
        transcript = list(getattr(session, "transcript", None) or [])
        ending_ids = self._reached_endings(session)
        return self.report_from(played=played, transcript=transcript,
                                ending_ids=ending_ids)

    def report_from(self, *, played: set, transcript: list,
                    ending_ids: set) -> CoverageReport:
        """Pure coverage core: build the report from raw observations.

        ``played`` is a set of scene ids, ``transcript`` a list of event
        dicts (line / choice events are mined for line and choice ids), and
        ``ending_ids`` the set of endings already determined to be reached.
        Kept session-free so the bucket logic is testable in isolation.
        Transcript walking is defensive — an event missing the keys it needs
        is skipped rather than raised.
        """
        seen_lines: set[str] = set()
        seen_choices: set[str] = set()
        seen_scenes: set[str] = set(played or set())

        for event in transcript or []:
            if not isinstance(event, dict):
                continue
            kind = event.get("event")
            if kind == "line":
                sid = event.get("scene_id")
                idx = event.get("line_index")
                if sid is not None and idx is not None:
                    seen_lines.add(f"{sid}#{idx}")
                    seen_scenes.add(sid)
            elif kind == "choice":
                sid = event.get("scene_id")
                cid = event.get("choice_id")
                if sid is not None and cid is not None:
                    seen_choices.add(f"{sid}.{cid}")
                    seen_scenes.add(sid)

        return CoverageReport(
            scenes=Bucket.make(seen_scenes, self.scene_ids),
            lines=Bucket.make(seen_lines, self.line_ids),
            choices=Bucket.make(seen_choices, self.choice_ids),
            endings=Bucket.make(set(ending_ids or set()), self.ending_ids),
        )

    # ------------------------------------------------------------------
    # Internals

    def _reached_endings(self, session: Any) -> set[str]:
        """Ending ids whose ``requires`` all evaluate truthy on the state."""
        out: set[str] = set()
        state = getattr(session, "state", None)
        endings = getattr(getattr(state, "endings", None), "endings", None)
        if state is None or not endings:
            return out
        for eid, ending in endings.items():
            try:
                requires = getattr(ending, "requires", None) or []
                if all(state.evaluate(c) for c in requires):
                    out.add(eid)
            except Exception:
                # A malformed condition must not sink the whole report.
                continue
        return out
