"""Tests for world_gal_game.dev.coverage.CoverageTracker.

Exercises the tracker against the bundled demo_pack: confirms the
declared totals, then runs the lover route through a HeadlessSession and
checks the resulting coverage report (endings/scenes/choices/lines), plus
the empty-run baseline where nothing has been exercised.
"""
from __future__ import annotations

import json
from pathlib import Path

from world_gal_game.config import EngineConfig
from world_gal_game.dev.coverage import Bucket, CoverageReport, CoverageTracker
from world_gal_game.headless import HeadlessSession

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_PACK = REPO_ROOT / "games" / "demo_pack"
LOVER_ROUTE = DEMO_PACK / "scripts" / "test_lover_route.json"


def _lover_commands() -> list[dict]:
    data = json.loads(LOVER_ROUTE.read_text(encoding="utf-8"))
    return data["commands"]


def test_totals_are_sane() -> None:
    tracker = CoverageTracker(DEMO_PACK)
    assert tracker.total_scenes > 0
    assert tracker.total_endings == 3
    # Totals must agree with the frozen id-sets.
    assert tracker.total_scenes == len(tracker.scene_ids)
    assert tracker.total_lines == len(tracker.line_ids)
    assert tracker.total_choices == len(tracker.choice_ids)
    assert tracker.total_endings == len(tracker.ending_ids)
    # Line and choice ids follow the documented "sid#i" / "sid.cid" shapes.
    assert all("#" in lid for lid in tracker.line_ids)
    assert "meet_heroine.accept_quest" in tracker.choice_ids


def test_lover_route_coverage() -> None:
    tracker = CoverageTracker(DEMO_PACK)
    sess = HeadlessSession.open(EngineConfig(seed=1), pack="demo_pack")
    sess.run_script(_lover_commands())
    report = tracker.report(sess)

    assert isinstance(report, CoverageReport)

    # Endings: the lover route reaches exactly ending_lover.
    assert report.endings.seen == 1
    assert report.endings.missing == ["ending_alone", "ending_friend"]

    # Scenes: the route walked prologue -> meet -> ... -> lover_event.
    assert report.scenes.seen >= 1
    for sid in ("prologue", "meet_heroine", "lover_event"):
        assert sid not in report.scenes.missing
    assert 0 <= report.scenes.pct <= 100

    # Choices: the route picks accept_quest / chat_more / confess, etc.
    assert report.choices.seen >= 1
    assert "meet_heroine.accept_quest" not in report.choices.missing

    # Lines: at least some dialogue was shown.
    assert report.lines.seen > 0
    assert report.lines.total >= report.lines.seen

    # summary() is a non-empty one-liner.
    assert isinstance(report.summary(), str)
    assert report.summary()


def test_empty_run_has_zero_coverage() -> None:
    tracker = CoverageTracker(DEMO_PACK)
    sess = HeadlessSession.open(EngineConfig(seed=1), pack="demo_pack")
    # No run_script call -> nothing played, empty transcript.
    report = tracker.report(sess)

    assert report.endings.seen == 0
    assert report.scenes.seen == 0
    assert report.lines.seen == 0
    assert report.choices.seen == 0
    # Totals still reflect the pack even with zero coverage.
    assert report.scenes.total == tracker.total_scenes
    assert report.endings.total == 3
    # Every id is reported missing when nothing was seen.
    assert len(report.endings.missing) == report.endings.total


def test_bucket_make_arithmetic() -> None:
    # Pure unit check of the bucket math, independent of any pack.
    b = Bucket.make({"a", "b", "x"}, {"a", "b", "c", "d"})
    assert b.seen == 2  # only a, b are known
    assert b.total == 4
    assert b.pct == 50.0
    assert b.missing == ["c", "d"]
    # Empty total dimension reports full coverage, not a divide-by-zero.
    empty = Bucket.make(set(), set())
    assert empty.total == 0
    assert empty.pct == 100.0
    assert empty.missing == []


def test_report_from_is_session_free() -> None:
    # The pure core can be driven with hand-built observations.
    tracker = CoverageTracker(DEMO_PACK)
    transcript = [
        {"seq": 1, "event": "line", "scene_id": "prologue", "line_index": 0,
         "speaker": None, "text": "..."},
        {"seq": 2, "event": "choice", "scene_id": "meet_heroine",
         "choice_id": "accept_quest"},
        # A malformed event must be skipped, not crash the report.
        {"seq": 3, "event": "line"},
        {"seq": 4, "event": "mystery_op"},
        "not-a-dict",
    ]
    report = tracker.report_from(
        played={"prologue"},
        transcript=transcript,
        ending_ids={"ending_lover"},
    )
    assert report.lines.seen == 1
    assert report.choices.seen == 1
    assert report.endings.seen == 1
    # The choice's scene is folded into seen scenes even if not in `played`.
    assert "meet_heroine" not in report.scenes.missing
    assert "prologue" not in report.scenes.missing
