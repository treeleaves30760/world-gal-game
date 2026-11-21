"""Turn a finished stage into pack edits.

A stage produces data; this module is the one place that data becomes changes
on disk, and it always goes through :class:`~world_gal_game.dev.pack_editor.
PackEditor` — the same comment-preserving, schema-validating editor every
other structural edit uses. Nothing here writes scene YAML by hand.

Two results are possible:

- a *new scene* (``Stage.to_scene``) is appended with ``add_scene``;
- a *restaged scene* (``Stage.to_edits``) becomes one ``update_line`` per line
  whose wording changed — text only, so effects, conditions, ids, voice clips
  and line positions stay exactly as authored.

Both default to a dry run and return the YAML diff; pass ``write=True`` to
commit. The expression memo is saved alongside a real write so the next run
resolves the same words to the same portraits.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .stage import Stage


def preview_lines(stage: Stage) -> list[dict[str, Any]]:
    """Before/after rows for a restaged scene (changed lines only)."""
    rows = []
    for turn in stage.turns:
        if turn.source_index is None or turn.fixed:
            continue
        if turn.fallback:
            rows.append({"line": turn.source_index, "speaker": turn.speaker,
                         "status": "kept", "before": turn.source_text,
                         "reason": ", ".join(hit["rule"] for hit in turn.hits)})
            continue
        if turn.text.strip() == turn.source_text.strip():
            continue
        rows.append({"line": turn.source_index, "speaker": turn.speaker,
                     "status": "rewritten", "before": turn.source_text,
                     "after": turn.text, "attempts": turn.attempts})
    return rows


def commit_stage(stage: Stage, pack_root: Path, *, write: bool = False,
                 into_file: str | None = None, expressions: bool = False
                 ) -> dict[str, Any]:
    """Validate the stage's result against the pack and (optionally) write it.

    Returns a JSON-friendly report: the YAML ``diff``, the files touched,
    warnings, the linter's review of the result, and — for a restage — the
    before/after of every changed line. Raises ``PackEditError`` when the
    result does not validate (for example a scene id that already exists).
    """
    from ..dev.pack_editor import PackEditor

    editor = PackEditor(Path(pack_root), dry_run=True)
    review = stage.review()
    out: dict[str, Any] = {
        "ok": True, "written": False, "summary": stage.summary(),
        "review": review.to_dict(),
    }
    if stage.is_restage:
        edits = stage.to_edits(expressions=expressions)
        for edit in edits:
            editor.update_line(edit["scene_id"], int(edit["line_index"]),
                               edit["updates"])
        out["kind"] = "restage"
        out["scene_id"] = stage.brief.restage_of
        out["edits"] = edits
        out["lines"] = preview_lines(stage)
    else:
        scene, warnings = stage.to_scene()
        editor.add_scene(scene, into_file=into_file or stage.brief.into_file)
        out["kind"] = "scene"
        out["scene_id"] = scene["id"]
        out["scene"] = scene
        out["warnings"] = warnings
    out["diff"] = editor.diff()
    out["files"] = editor.pending_files()
    if write and editor.has_pending():
        info = editor.commit()
        out["written"] = True
        out["files"] = info.get("files_written", out["files"])
        memo = stage.ctx.tavern.memo.save()
        if memo is not None:
            out["memo"] = str(memo)
    else:
        editor.rollback()
    return out
