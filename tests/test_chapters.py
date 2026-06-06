"""Chapter/act/route manifest (``world_gal_game.core.chapter_spec``).

Covers the optional ``content/chapters.yaml`` overlay: model + loader helpers,
that load_pack parks it on the private meta bridge, the inspector view + counts
+ cross-check, and that a pack without one is unaffected.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from world_gal_game.config import EngineConfig
from world_gal_game.content_loader import load_pack
from world_gal_game.core.chapter_spec import ChapterManifest, ChapterSpec
from world_gal_game.dev.pack_inspector import PackInspector
from world_gal_game.headless import HeadlessSession

DEMO = Path("games/demo_pack")


# ----------------------------------------------------------------------
# model + loader helpers


def test_manifest_from_list_and_mapping_equivalent() -> None:
    as_list = ChapterManifest.from_items([
        {"id": "a", "route": "r1", "order": 2, "scenes": ["s2"]},
        {"id": "b", "route": "r1", "order": 1, "scenes": ["s1"]},
    ])
    # ordered() sorts by (order, id): b (order 1) before a (order 2).
    assert [c.id for c in as_list.ordered()] == ["b", "a"]
    assert as_list.by_route()["r1"][0].id == "b"
    assert as_list.scene_to_chapter() == {"s1": "b", "s2": "a"}


def test_manifest_rejects_duplicate_ids() -> None:
    with pytest.raises(ValueError):
        ChapterManifest.from_items([{"id": "x"}, {"id": "x"}])


def test_chapter_spec_is_strict() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ChapterSpec(id="x", bogus="y")


def test_missing_file_is_empty_manifest(tmp_path: Path) -> None:
    assert ChapterManifest.load(tmp_path / "nope.yaml").chapters == []


# ----------------------------------------------------------------------
# integration: demo_pack ships a manifest


def test_load_pack_parks_manifest_on_meta() -> None:
    state, _npcs, _meta = load_pack(DEMO / "content")
    manifest = state.meta["__chapters__"]
    assert isinstance(manifest, ChapterManifest)
    assert "ch1_arrival" in manifest.ids()


def test_inspector_chapters_and_counts() -> None:
    inspector = PackInspector(DEMO)
    rows = inspector.chapters()
    assert [r["id"] for r in rows][:2] == ["ch1_arrival", "ch2_investigation"]
    assert inspector.summary()["counts"]["chapters"] == len(rows) == 5


def test_demo_chapter_check_is_clean() -> None:
    issues = PackInspector(DEMO).chapter_issues()
    assert issues["unknown_scenes"] == []
    assert issues["uncovered_scenes"] == []


def test_chapter_issues_flags_unknown_and_uncovered(tmp_path: Path) -> None:
    import shutil
    dest = tmp_path / "demo"
    shutil.copytree(DEMO, dest)
    # A manifest that references a missing scene and covers only one real scene.
    (dest / "content" / "chapters.yaml").write_text(
        "chapters:\n"
        "  - id: only\n"
        "    scenes: [prologue, ghost_scene_that_does_not_exist]\n",
        encoding="utf-8")
    issues = PackInspector(dest).chapter_issues()
    assert issues["unknown_scenes"] == ["ghost_scene_that_does_not_exist"]
    assert "meet_heroine" in issues["uncovered_scenes"]


def test_pack_without_chapters_is_unaffected(tmp_path: Path) -> None:
    import shutil
    dest = tmp_path / "demo"
    shutil.copytree(DEMO, dest)
    (dest / "content" / "chapters.yaml").unlink()
    inspector = PackInspector(dest)
    assert inspector.chapters() == []
    assert inspector.summary()["counts"]["chapters"] == 0
    # And the pack still loads + a session opens fine.
    sess = HeadlessSession.open(EngineConfig(seed=1), pack=str(dest))
    assert sess.state.meta["__chapters__"].chapters == []


# ======================================================================
# Chapter RUNTIME: state field + set_chapter / advance_chapter effects +
# in_chapter / chapter_at_or_after conditions + chapter.change hook +
# headless inspect surface + capability presence + save round-trip.
# ======================================================================

from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import Effect, Condition
from world_gal_game.plugins import (
    EFFECT_REGISTRY, CONDITION_REGISTRY, HookEvent,
    PluginContext, PluginManager, hook, snapshot, restore,
)
from world_gal_game.plugins.builtin_effects import VISUAL_FX_QUEUE


def _manifest() -> ChapterManifest:
    """A small two-chapter hand-built manifest used by the runtime tests."""
    return ChapterManifest.from_items([
        {"id": "c1", "title": "第一章", "route": "common", "act": "act1",
         "order": 10, "entry_scene": "s1", "scenes": ["s1", "s1b"]},
        {"id": "c2", "title": "第二章", "subtitle": "湖畔", "route": "lover",
         "act": "act2", "order": 20, "scenes": ["s2"],
         "endings": ["ending_lover"]},
    ])


def _state_with_manifest() -> GameState:
    state = GameState()
    state.meta["__chapters__"] = _manifest()
    return state


# ----------------------------------------------------------------------
# (a) state field default


def test_current_chapter_defaults_to_none() -> None:
    assert GameState().current_chapter is None


# ----------------------------------------------------------------------
# (b) set_chapter


def test_set_chapter_sets_state_and_queues_card() -> None:
    state = _state_with_manifest()
    result = state.apply(Effect(kind="set_chapter", target="c2"))
    assert state.current_chapter == "c2"
    assert result["kind"] == "set_chapter"
    assert result["chapter"] == "c2"
    assert result["previous"] is None
    assert result["title"] == "第二章"
    # A chapter_card directive is queued on the visual-fx bridge. The subtitle
    # is the pack-authored human line (ChapterSpec.subtitle), never the raw
    # route/act tag.
    queue = state.meta.get(VISUAL_FX_QUEUE)
    assert queue and queue[-1] == {
        "fx": "chapter_card", "chapter": "c2",
        "title": "第二章", "subtitle": "湖畔",
    }


def test_set_chapter_records_event() -> None:
    state = _state_with_manifest()
    state.apply(Effect(kind="set_chapter", target="c1"))
    titles = [e.title for e in state.events.entries]
    assert "第一章" in titles


def test_set_chapter_value_false_suppresses_card() -> None:
    state = _state_with_manifest()
    result = state.apply(Effect(kind="set_chapter", target="c2", value=False))
    assert state.current_chapter == "c2"
    assert result["chapter"] == "c2"
    # No card queued.
    assert VISUAL_FX_QUEUE not in state.meta or not state.meta[VISUAL_FX_QUEUE]


def test_set_chapter_unknown_target_errors_and_leaves_state() -> None:
    state = _state_with_manifest()
    state.current_chapter = "c1"
    result = state.apply(Effect(kind="set_chapter", target="nope"))
    assert "error" in result
    assert "nope" in result["error"]
    # State unchanged, nothing queued.
    assert state.current_chapter == "c1"
    assert VISUAL_FX_QUEUE not in state.meta or not state.meta[VISUAL_FX_QUEUE]


def test_set_chapter_subtitle_is_empty_without_explicit_subtitle() -> None:
    """A chapter with no ``subtitle`` queues an empty subtitle — the card never
    falls back to the raw route/act tag (which reads as machine jargon)."""
    state = GameState()
    state.meta["__chapters__"] = ChapterManifest.from_items(
        [{"id": "c1", "title": "序", "route": "common", "act": "prologue",
          "order": 5}])
    state.apply(Effect(kind="set_chapter", target="c1"))
    assert state.meta[VISUAL_FX_QUEUE][-1]["subtitle"] == ""


def test_set_chapter_uses_explicit_subtitle() -> None:
    """A chapter's authored ``subtitle`` is what the card shows."""
    state = GameState()
    state.meta["__chapters__"] = ChapterManifest.from_items(
        [{"id": "c1", "title": "序章", "subtitle": "搬家當天",
          "route": "common", "order": 5}])
    state.apply(Effect(kind="set_chapter", target="c1"))
    assert state.meta[VISUAL_FX_QUEUE][-1]["subtitle"] == "搬家當天"


# ----------------------------------------------------------------------
# (c) advance_chapter


def test_advance_chapter_from_none_to_first() -> None:
    state = _state_with_manifest()
    result = state.apply(Effect(kind="advance_chapter"))
    assert state.current_chapter == "c1"
    assert result["chapter"] == "c1"
    assert result["previous"] is None


def test_advance_chapter_first_to_second() -> None:
    state = _state_with_manifest()
    state.current_chapter = "c1"
    result = state.apply(Effect(kind="advance_chapter"))
    assert state.current_chapter == "c2"
    assert result["chapter"] == "c2"
    assert result["previous"] == "c1"


def test_advance_chapter_at_last_errors() -> None:
    state = _state_with_manifest()
    state.current_chapter = "c2"
    result = state.apply(Effect(kind="advance_chapter"))
    assert "error" in result
    assert result["current"] == "c2"
    assert state.current_chapter == "c2"


def test_advance_chapter_empty_manifest_errors() -> None:
    state = GameState()
    state.meta["__chapters__"] = ChapterManifest()
    result = state.apply(Effect(kind="advance_chapter"))
    assert result["error"] == "no chapter manifest"


def test_advance_chapter_uses_order_not_insertion() -> None:
    """advance follows ordered() (order then id), not list/insertion order."""
    state = GameState()
    state.meta["__chapters__"] = ChapterManifest.from_items([
        {"id": "late", "order": 30},
        {"id": "early", "order": 10},
    ])
    state.apply(Effect(kind="advance_chapter"))   # None -> first by order
    assert state.current_chapter == "early"
    state.apply(Effect(kind="advance_chapter"))   # early -> late
    assert state.current_chapter == "late"


# ----------------------------------------------------------------------
# (d) conditions


def test_in_chapter_none_is_false() -> None:
    state = _state_with_manifest()
    assert state.evaluate(Condition(kind="in_chapter", value="c1")) is False


def test_in_chapter_single_and_list() -> None:
    state = _state_with_manifest()
    state.current_chapter = "c2"
    assert state.evaluate(Condition(kind="in_chapter", value="c2")) is True
    assert state.evaluate(Condition(kind="in_chapter", value="c1")) is False
    assert state.evaluate(Condition(kind="in_chapter", value=["c1", "c2"])) is True
    assert state.evaluate(Condition(kind="in_chapter", value=["c1"])) is False


def test_chapter_at_or_after_ge_lt() -> None:
    state = _state_with_manifest()
    state.current_chapter = "c2"   # order 20
    # c2 (20) >= c1 (10) -> True; c2 (20) >= c2 (20) -> True
    assert state.evaluate(Condition(kind="chapter_at_or_after", target="c1")) is True
    assert state.evaluate(Condition(kind="chapter_at_or_after", target="c2")) is True
    # cur at c1 (10) is NOT at-or-after c2 (20).
    state.current_chapter = "c1"
    assert state.evaluate(Condition(kind="chapter_at_or_after", target="c2")) is False


def test_chapter_at_or_after_none_is_false() -> None:
    state = _state_with_manifest()
    assert state.evaluate(Condition(kind="chapter_at_or_after", target="c1")) is False


def test_chapter_at_or_after_unknown_warns_and_false(caplog) -> None:
    import logging
    state = _state_with_manifest()
    state.current_chapter = "c1"
    with caplog.at_level(logging.WARNING):
        result = state.evaluate(Condition(kind="chapter_at_or_after", target="ghost"))
    assert result is False
    assert any("chapter_at_or_after" in r.message for r in caplog.records)


# ----------------------------------------------------------------------
# (e) chapter.change hook fires with payload


def test_chapter_change_hook_fires_with_payload() -> None:
    snap = snapshot()
    try:
        fired: list[dict] = []

        @hook(HookEvent.CHAPTER_CHANGE, plugin_id="testbed")
        def on_chapter(ctx, chapter=None, previous=None, title=None,
                       route=None, order=None, **_kw):
            fired.append({"chapter": chapter, "previous": previous,
                          "title": title, "route": route, "order": order})

        state = GameState()
        mgr = PluginManager(engine_version="0.1.0")
        ctx = PluginContext(state=state, manager=mgr)
        mgr.set_context(ctx)
        state.meta["__plugin_manager__"] = mgr
        state.meta["__chapters__"] = _manifest()

        state.apply(Effect(kind="set_chapter", target="c2"))
        assert fired == [{
            "chapter": "c2", "previous": None, "title": "第二章",
            "route": "lover", "order": 20,
        }]
    finally:
        restore(snap)


# ----------------------------------------------------------------------
# (f) + (g) save round-trip / old-save backward compat


def test_save_round_trip_preserves_current_chapter() -> None:
    state = _state_with_manifest()
    state.apply(Effect(kind="set_chapter", target="c2"))
    dumped = state.model_dump(mode="json")
    assert dumped["current_chapter"] == "c2"
    # transient meta bridge stripped on serialise.
    assert "__chapters__" not in dumped.get("meta", {})
    restored = GameState.model_validate(dumped)
    assert restored.current_chapter == "c2"


def test_old_save_without_key_loads_as_none() -> None:
    old = GameState().model_dump(mode="json")
    old.pop("current_chapter", None)
    assert "current_chapter" not in old
    loaded = GameState.model_validate(old)
    assert loaded.current_chapter is None   # no migration needed


# ----------------------------------------------------------------------
# (h) no-manifest backward compat


def test_no_manifest_advance_and_in_chapter_degrade() -> None:
    state = GameState()   # no __chapters__ parked
    assert state.apply(Effect(kind="advance_chapter"))["error"] == "no chapter manifest"
    assert state.evaluate(Condition(kind="in_chapter", value="c1")) is False
    assert state.evaluate(
        Condition(kind="chapter_at_or_after", target="c1")) is False


# ----------------------------------------------------------------------
# (i) capability presence


def test_chapter_effects_and_conditions_registered() -> None:
    assert "set_chapter" in EFFECT_REGISTRY
    assert "advance_chapter" in EFFECT_REGISTRY
    assert "in_chapter" in CONDITION_REGISTRY
    assert "chapter_at_or_after" in CONDITION_REGISTRY


def test_chapter_change_in_hook_events() -> None:
    assert "chapter.change" in HookEvent.all()
    assert HookEvent.CHAPTER_CHANGE == "chapter.change"


# ----------------------------------------------------------------------
# (j) headless inspect surface


def test_inspect_chapter_block() -> None:
    sess = HeadlessSession.open(EngineConfig(seed=1), pack=str(DEMO))
    sess.state.apply(Effect(kind="set_chapter", target="ch1_arrival"))
    chapter = sess.inspect()["chapter"]
    assert chapter["current"] == "ch1_arrival"
    assert chapter["current_title"]
    assert isinstance(chapter["ordered"], list) and chapter["ordered"]
    row = next(r for r in chapter["ordered"] if r["id"] == "ch1_arrival")
    assert row["is_current"] is True
    assert {"id", "title", "route", "order", "is_current", "reached"} <= set(row)


def test_inspect_chapter_block_no_manifest(tmp_path: Path) -> None:
    import shutil
    dest = tmp_path / "demo"
    shutil.copytree(DEMO, dest)
    (dest / "content" / "chapters.yaml").unlink()
    sess = HeadlessSession.open(EngineConfig(seed=1), pack=str(dest))
    chapter = sess.inspect()["chapter"]
    assert chapter["current"] is None
    assert chapter["ordered"] == []
