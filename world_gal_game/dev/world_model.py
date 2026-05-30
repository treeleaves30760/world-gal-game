"""Static world-model snapshot + delta — the *verify-the-edit* half of the loop.

An agent's loop on a pack is **understand -> edit -> verify**. Phase 6 made
*understand* (``build_context`` / ``DataflowAnalyzer``) and *verify-by-play*
(the warm NDJSON session + planner) cheap. This module closes the remaining gap:
after a *structural* edit, answer in one shot "what did I just change about the
pack's reachability, dead-ends, and declared state?" — so an ``edit.*`` op can
report the *consequence* of the edit in the same response that applied it,
instead of the agent having to re-run ``self-check`` / ``build_context`` as a
separate cold pass.

:func:`world_snapshot` captures the parts of a pack's static world model an edit
can plausibly break or fix: the set of scenes, the set of endings, which of each
are reachable, structural dead-ends, and used-but-undeclared flags. It reuses the
exact :class:`~world_gal_game.dev.pack_inspector.PackInspector` +
:class:`~world_gal_game.dev.dataflow.DataflowAnalyzer` machinery behind
``wgg inspect-pack`` / ``build_context`` so the numbers always agree.

:func:`world_delta` diffs two snapshots into an *agent-actionable* report that
separates **regressions** (newly-unreachable endings, new dead-ends, new
undeclared flags, orphaned new scenes) from **improvements** (the reverse), and
exposes a single ``clean`` boolean the caller can branch on without parsing the
detail.

Both functions are pure and disk-based — no running session required — and
swallow any plugin-load chatter on stdout, so the result is safe to emit on the
NDJSON session channel.
"""
from __future__ import annotations

import contextlib
import io
from pathlib import Path
from typing import Any


def world_snapshot(pack_root: Path | str) -> dict[str, Any]:
    """Capture the reachability / dead-end / declared-state view of a pack.

    Returns a compact, JSON-friendly dict whose every collection is sorted, so
    two snapshots diff deterministically. Plugin-load output (the one-line
    summary a pack may print on first load) is redirected away from stdout so a
    caller streaming NDJSON is never corrupted.
    """
    root = Path(pack_root)
    # Imported lazily: keeps this module import-cheap and avoids dragging the
    # full content loader into callers that only want the types.
    from .dataflow import DataflowAnalyzer
    from .pack_inspector import PackInspector

    sink = io.StringIO()
    with contextlib.redirect_stdout(sink):
        inspector = PackInspector(root)
        reach = inspector.reachability()
        dead = inspector.dead_ends()
        counts = inspector.summary().get("counts", {})
        declared = {row["key"] for row in inspector.variables()}
        report = DataflowAnalyzer(root).analyze(declared_flags=declared or None)

    all_scenes = sorted(set(reach["reachable"]) | set(reach["unreachable"]))
    return {
        "scenes": all_scenes,
        "reachable_scenes": sorted(reach["reachable"]),
        "unreachable_scenes": sorted(reach["unreachable"]),
        "endings": sorted(reach["endings"]["all"]),
        "reachable_endings": sorted(reach["endings"]["reachable"]),
        "unreachable_endings": sorted(reach["endings"]["unreachable"]),
        # Stable string keys ("<kind>:<target>") so dead-ends set-diff cleanly.
        "dead_ends": sorted(f"{d.kind}:{d.target}" for d in dead),
        "undeclared_flags": sorted(report.undeclared_flags),
        "counts": dict(counts),
    }


def world_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Diff two :func:`world_snapshot` results into an actionable impact report.

    "Newly unreachable" is computed only over content that exists in *both*
    snapshots, so adding a scene that simply isn't wired up yet shows as an
    ``orphan_scenes`` regression rather than polluting the unreachable-delta of
    pre-existing content. ``clean`` is ``True`` exactly when there are no
    regressions — the cheap signal an edit op / agent branches on.
    """
    def as_set(snap: dict[str, Any], key: str) -> set[str]:
        return set(snap.get(key, []))

    scenes_before, scenes_after = as_set(before, "scenes"), as_set(after, "scenes")
    endings_before, endings_after = as_set(before, "endings"), as_set(after, "endings")
    kept_scenes = scenes_before & scenes_after
    kept_endings = endings_before & endings_after

    reach_s_before, reach_s_after = as_set(before, "reachable_scenes"), as_set(after, "reachable_scenes")
    reach_e_before, reach_e_after = as_set(before, "reachable_endings"), as_set(after, "reachable_endings")
    dead_before, dead_after = as_set(before, "dead_ends"), as_set(after, "dead_ends")
    undecl_before, undecl_after = as_set(before, "undeclared_flags"), as_set(after, "undeclared_flags")

    scenes_added = sorted(scenes_after - scenes_before)
    scenes_removed = sorted(scenes_before - scenes_after)
    endings_added = sorted(endings_after - endings_before)
    endings_removed = sorted(endings_before - endings_after)

    newly_unreachable_scenes = sorted((kept_scenes & reach_s_before) - reach_s_after)
    newly_reachable_scenes = sorted((kept_scenes & reach_s_after) - reach_s_before)
    newly_unreachable_endings = sorted((kept_endings & reach_e_before) - reach_e_after)
    newly_reachable_endings = sorted((kept_endings & reach_e_after) - reach_e_before)

    new_dead_ends = sorted(dead_after - dead_before)
    resolved_dead_ends = sorted(dead_before - dead_after)
    new_undeclared_flags = sorted(undecl_after - undecl_before)
    resolved_undeclared_flags = sorted(undecl_before - undecl_after)

    # A freshly-added scene that is already unreachable is an orphan: the edit
    # created content nothing routes to. Worth flagging distinctly from the
    # "this used-to-be-reachable scene broke" case above.
    orphan_scenes = sorted(set(scenes_added) & as_set(after, "unreachable_scenes"))

    regressions: list[dict[str, Any]] = []
    if newly_unreachable_endings:
        regressions.append({"kind": "unreachable_endings", "items": newly_unreachable_endings})
    if newly_unreachable_scenes:
        regressions.append({"kind": "unreachable_scenes", "items": newly_unreachable_scenes})
    if new_dead_ends:
        regressions.append({"kind": "dead_ends", "items": new_dead_ends})
    if new_undeclared_flags:
        regressions.append({"kind": "undeclared_flags", "items": new_undeclared_flags})
    if orphan_scenes:
        regressions.append({"kind": "orphan_scenes", "items": orphan_scenes})

    improvements: list[dict[str, Any]] = []
    if newly_reachable_endings:
        improvements.append({"kind": "reachable_endings", "items": newly_reachable_endings})
    if resolved_dead_ends:
        improvements.append({"kind": "resolved_dead_ends", "items": resolved_dead_ends})
    if resolved_undeclared_flags:
        improvements.append({"kind": "resolved_undeclared_flags", "items": resolved_undeclared_flags})

    counts_before = before.get("counts", {})
    counts_after = after.get("counts", {})
    counts_delta = {
        key: counts_after.get(key, 0) - counts_before.get(key, 0)
        for key in set(counts_before) | set(counts_after)
        if counts_after.get(key, 0) != counts_before.get(key, 0)
    }

    return {
        "clean": not regressions,
        "regressions": regressions,
        "improvements": improvements,
        "scenes_added": scenes_added,
        "scenes_removed": scenes_removed,
        "endings_added": endings_added,
        "endings_removed": endings_removed,
        "newly_unreachable_scenes": newly_unreachable_scenes,
        "newly_reachable_scenes": newly_reachable_scenes,
        "newly_unreachable_endings": newly_unreachable_endings,
        "newly_reachable_endings": newly_reachable_endings,
        "new_dead_ends": new_dead_ends,
        "resolved_dead_ends": resolved_dead_ends,
        "new_undeclared_flags": new_undeclared_flags,
        "resolved_undeclared_flags": resolved_undeclared_flags,
        "counts_delta": counts_delta,
    }
