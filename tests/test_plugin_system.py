"""Plugin system test suite.

Covers Phase 1 surface:

- registry + decorator round-trip (effect / condition / hook / inspect_field)
- builtin kind population
- :meth:`GameState.apply` / :meth:`evaluate` dispatch and isolation
- :class:`PluginManifest` schema + semver compatibility
- :class:`PluginManager` discovery, topo sort, dependency errors,
  load failures, hook fire order
- :func:`snapshot` / :func:`restore` registry isolation primitive
- step_counter demo plugin loading inside demo_pack

Each test that mutates global state uses :func:`snapshot` and
:func:`restore` around its body so other tests aren't affected.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import Condition, Effect
from world_gal_game.plugins import (
    EFFECT_REGISTRY, CONDITION_REGISTRY, HOOK_REGISTRY, INSPECT_FIELD_REGISTRY,
    EffectEntry, ConditionEntry,
    PluginContext, PluginManager, PluginManifest, HookEvent,
    DuplicateKindError, DependencyError, IncompatibleEngineError,
    ManifestError, PluginLoadError, UnknownKindError,
    effect, condition, hook, inspect_field,
    snapshot, restore,
)
from world_gal_game.plugins.manifest import _semver_matches


# ----------------------------------------------------------------------
# Fixtures


@pytest.fixture
def clean_registry():
    """Snapshot the global registry, run the test, restore afterwards."""
    snap = snapshot()
    yield
    restore(snap)


@pytest.fixture
def state():
    """A fresh GameState (no plugin manager attached)."""
    return GameState()


# ----------------------------------------------------------------------
# Builtin coverage


def test_all_39_builtin_kinds_registered():
    """The 39 builtin kinds (23 effects + 16 conditions) are present."""
    assert len(EFFECT_REGISTRY) >= 23
    assert len(CONDITION_REGISTRY) >= 16
    expected_effects = {
        "affection", "stat", "set_flag", "increment_flag", "advance_time",
        "move_to", "unlock_location", "play_scene", "end_scene", "log_event",
        "give_item", "take_item", "use_item", "gain_resource",
        "spend_resource", "set_resource", "buy_item", "sell_item", "gift",
        "start_quest", "complete_objective", "complete_quest", "fail_quest",
    }
    assert expected_effects.issubset(set(EFFECT_REGISTRY.list_kinds()))
    expected_conds = {
        "flag", "not_flag", "flag_eq", "affection_gte", "affection_lt",
        "time_in", "visited", "scene_played", "has_item", "achievement",
        "resource_gte", "resource_lt", "resource_eq",
        "quest_active", "quest_completed", "objective_completed",
    }
    assert expected_conds.issubset(set(CONDITION_REGISTRY.list_kinds()))


def test_builtin_entries_have_plugin_id():
    """Every builtin entry should be owned by 'builtin'."""
    for kind in EFFECT_REGISTRY.list_kinds():
        entry = EFFECT_REGISTRY.get(kind)
        # Builtin and demo step_counter could both be loaded in this
        # process; only assert that builtin-owned ones look right.
        if entry.plugin_id == "builtin":
            assert callable(entry.fn)
            assert entry.kind == kind


def test_builtin_entries_have_signatures():
    """Builtin handlers ship a non-empty signature dict where applicable."""
    aff = EFFECT_REGISTRY.get("affection")
    assert aff.signature.get("target") == "character_id"


# ----------------------------------------------------------------------
# Registry dispatch


def test_apply_dispatches_through_registry(state):
    out = state.apply(Effect(kind="set_flag", target="x", value=True))
    assert out["kind"] == "set_flag"
    assert state.events.get_flag("x") is True


def test_evaluate_dispatches_through_registry(state):
    state.events.set_flag("a", True)
    assert state.evaluate(Condition(kind="flag", target="a")) is True
    assert state.evaluate(Condition(kind="not_flag", target="a")) is False


def test_apply_unknown_kind_returns_error_dict(state):
    out = state.apply(Effect(kind="totally_made_up_kind", target="x"))
    assert out == {"kind": "totally_made_up_kind", "error": "unknown effect"}


def test_evaluate_unknown_kind_returns_false(state, caplog):
    with caplog.at_level("WARNING", logger="world_gal_game.core.game_state"):
        assert state.evaluate(Condition(kind="bogus_kind")) is False
    assert any("unknown condition kind" in r.message for r in caplog.records)


def test_handler_exception_is_isolated(state, clean_registry, caplog):
    """A handler raising must not crash apply; result carries the error."""
    @effect("__boom__", plugin_id="testbed")
    def boom(state, eff):
        raise RuntimeError("kaboom")
    with caplog.at_level("ERROR", logger="world_gal_game.core.game_state"):
        out = state.apply(Effect(kind="__boom__"))
    assert out["kind"] == "__boom__"
    assert "error" in out
    assert "kaboom" in out["error"]


def test_condition_exception_is_isolated(state, clean_registry, caplog):
    @condition("__bad_cond__", plugin_id="testbed")
    def bad(state, cond):
        raise ValueError("nope")
    with caplog.at_level("ERROR", logger="world_gal_game.core.game_state"):
        assert state.evaluate(Condition(kind="__bad_cond__")) is False


# ----------------------------------------------------------------------
# Decorator surface


def test_effect_decorator_registers_with_metadata(clean_registry):
    @effect("__inc_counter__", plugin_id="testbed",
            description="bump a flag", signature={"target": "flag_name"})
    def fn(state, eff):
        state.events.increment(eff.target, 1)
        return {"ok": True}
    entry = EFFECT_REGISTRY.get("__inc_counter__")
    assert entry is not None
    assert entry.plugin_id == "testbed"
    assert entry.description == "bump a flag"
    assert entry.signature == {"target": "flag_name"}
    assert fn.__wgg_effect_kind__ == "__inc_counter__"  # stamp visible


def test_condition_decorator_registers(clean_registry):
    @condition("__odd_flag__", plugin_id="testbed")
    def fn(state, cond):
        return int(state.events.get_flag(cond.target) or 0) % 2 == 1
    entry = CONDITION_REGISTRY.get("__odd_flag__")
    assert entry is not None and entry.plugin_id == "testbed"


def test_hook_decorator_registers_with_priority(clean_registry):
    fired = []

    @hook("__test_event__", plugin_id="a", priority=200)
    def later(ctx, **kw):
        fired.append("a")

    @hook("__test_event__", plugin_id="b", priority=50)
    def earlier(ctx, **kw):
        fired.append("b")

    ctx = PluginContext()
    HOOK_REGISTRY.fire("__test_event__", ctx)
    # priority 50 runs before 200
    assert fired == ["b", "a"]


def test_inspect_field_decorator_round_trip(state, clean_registry):
    @inspect_field("__demo_field__", plugin_id="testbed")
    def fn(state):
        return {"hello": True}
    out = INSPECT_FIELD_REGISTRY.collect(state)
    assert out["__demo_field__"] == {"hello": True}


# ----------------------------------------------------------------------
# Duplicates


def test_duplicate_kind_raises(clean_registry):
    @effect("__dup__", plugin_id="alpha")
    def a(state, eff):
        return {"ok": True}
    with pytest.raises(DuplicateKindError) as ei:
        @effect("__dup__", plugin_id="beta")
        def b(state, eff):
            return {"ok": True}
    assert ei.value.kind == "__dup__"
    assert ei.value.existing_plugin == "alpha"
    assert ei.value.new_plugin == "beta"
    assert ei.value.category == "effect"


def test_same_function_same_plugin_is_idempotent(clean_registry):
    """Re-registering the exact same fn/plugin pair should be a no-op."""
    def fn(state, eff):
        return {"ok": True}
    EFFECT_REGISTRY.register(EffectEntry(
        kind="__redo__", fn=fn, plugin_id="x",
    ))
    EFFECT_REGISTRY.register(EffectEntry(
        kind="__redo__", fn=fn, plugin_id="x",
    ))
    assert "__redo__" in EFFECT_REGISTRY


# ----------------------------------------------------------------------
# Unregister / cleanup


def test_unregister_plugin_removes_only_its_entries(clean_registry):
    @effect("__a__", plugin_id="x")
    def a(state, eff): return {}
    @effect("__b__", plugin_id="y")
    def b(state, eff): return {}
    removed = EFFECT_REGISTRY.unregister_plugin("x")
    assert removed == ["__a__"]
    assert "__a__" not in EFFECT_REGISTRY
    assert "__b__" in EFFECT_REGISTRY


def test_hook_unregister_plugin_clears_event(clean_registry):
    @hook("__h__", plugin_id="x")
    def fn(ctx, **kw): pass
    assert len(HOOK_REGISTRY) >= 1
    n = HOOK_REGISTRY.unregister_plugin("x")
    assert n == 1
    assert "__h__" not in HOOK_REGISTRY.list_events()


# ----------------------------------------------------------------------
# Manifest validation


def test_manifest_id_must_be_slug():
    with pytest.raises(Exception):
        PluginManifest(id="Bad ID with spaces")
    with pytest.raises(Exception):
        PluginManifest(id="123_starts_with_digit")
    PluginManifest(id="ok_name_42")  # success


def test_manifest_version_must_be_semverish():
    PluginManifest(id="xx", version="1")
    PluginManifest(id="xx", version="1.2")
    PluginManifest(id="xx", version="1.2.3")
    PluginManifest(id="xx", version="1.2.3-beta")
    with pytest.raises(Exception):
        PluginManifest(id="xx", version="not-a-version")


def test_manifest_from_yaml_parses(tmp_path: Path):
    p = tmp_path / "plugin.yaml"
    p.write_text(textwrap.dedent("""
        id: tiny
        name: Tiny
        version: 0.1.0
        extends:
          effects:
            - kind: my_kind
              description: example
              signature:
                target: <unused>
    """).strip(), encoding="utf-8")
    m = PluginManifest.from_yaml(p)
    assert m.id == "tiny"
    assert m.extends.effects[0].kind == "my_kind"
    assert m.extends.effects[0].signature == {"target": "<unused>"}


def test_manifest_from_yaml_bad_yaml_raises(tmp_path: Path):
    p = tmp_path / "plugin.yaml"
    p.write_text("id: ok\nthis is: : not valid: yaml\n", encoding="utf-8")
    with pytest.raises(ManifestError):
        PluginManifest.from_yaml(p)


def test_manifest_extra_fields_forbidden(tmp_path: Path):
    p = tmp_path / "plugin.yaml"
    p.write_text("id: ok\nrandom_unknown_field: 42\n", encoding="utf-8")
    with pytest.raises(ManifestError):
        PluginManifest.from_yaml(p)


# ----------------------------------------------------------------------
# Semver range matching


@pytest.mark.parametrize("version,spec,expected", [
    ("0.1.0", "*", True),
    ("0.1.0", "", True),
    ("0.1.5", "0.1", True),       # prefix match
    ("0.2.0", "0.1", False),       # 0.2 != 0.1 prefix
    ("0.1.5", ">=0.1.0", True),
    ("0.0.5", ">=0.1.0", False),
    ("1.0.0", ">=0.1.0,<2.0.0", True),
    ("2.0.0", ">=0.1.0,<2.0.0", False),
    # PEP 440 ~=: ~=0.1 → >=0.1, <1.0; ~=0.1.5 → >=0.1.5, <0.2
    ("0.1.5", "~=0.1", True),
    ("0.2.0", "~=0.1", True),
    ("1.0.0", "~=0.1", False),
    ("0.1.6", "~=0.1.5", True),
    ("0.2.0", "~=0.1.5", False),
])
def test_semver_matching(version, spec, expected):
    assert _semver_matches(version, spec) is expected


def test_engine_version_incompatible_raises():
    m = PluginManifest(id="xx", engine_version=">=99.0.0")
    with pytest.raises(IncompatibleEngineError):
        m.check_engine_compatible("0.1.0")


# ----------------------------------------------------------------------
# PluginManager: discover + activate


def _write_plugin(root: Path, plugin_id: str, *,
                  depends: list[str] | None = None,
                  body: str = "",
                  engine_version: str = "*",
                  entry_module: str = "plugin") -> Path:
    """Helper: create a minimal valid plugin dir + return its path."""
    plugin_dir = root / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest = [
        f"id: {plugin_id}",
        f"name: {plugin_id}",
        "version: 0.1.0",
        f"engine_version: \"{engine_version}\"",
        f"entry_module: {entry_module}",
    ]
    if depends:
        manifest.append(f"depends: {depends!r}")
    (plugin_dir / "plugin.yaml").write_text("\n".join(manifest) + "\n",
                                            encoding="utf-8")
    (plugin_dir / f"{entry_module}.py").write_text(body, encoding="utf-8")
    return plugin_dir


def test_manager_discovers_pack_local_plugin(tmp_path: Path, clean_registry):
    pack_root = tmp_path / "mypack"
    plugins_root = pack_root / "plugins"
    _write_plugin(plugins_root, "alpha", body=textwrap.dedent("""
        from world_gal_game.plugins import effect
        @effect("alpha_kind", description="hi")
        def fn(state, eff): return {"ok": True}
    """))
    mgr = PluginManager(pack_root=pack_root, engine_version="0.1.0")
    records = mgr.discover()
    assert any(r.id == "alpha" for r in records)
    loaded = mgr.activate(PluginContext(pack_root=pack_root))
    assert len(loaded) == 1
    assert loaded[0].id == "alpha"
    assert "alpha_kind" in EFFECT_REGISTRY


def test_manager_topo_sort_respects_depends(tmp_path: Path, clean_registry):
    pack_root = tmp_path / "mypack"
    plugins_root = pack_root / "plugins"
    _write_plugin(plugins_root, "first", body=textwrap.dedent("""
        from world_gal_game.plugins import effect
        @effect("first_kind", description="one")
        def fn(state, eff): return {"ok": 1}
    """))
    _write_plugin(plugins_root, "second", depends=["first"],
                  body=textwrap.dedent("""
        from world_gal_game.plugins import effect
        @effect("second_kind", description="two")
        def fn(state, eff): return {"ok": 2}
    """))
    mgr = PluginManager(pack_root=pack_root, engine_version="0.1.0")
    mgr.discover()
    loaded = mgr.activate(PluginContext(pack_root=pack_root))
    # both loaded, first should appear before second
    ids = [r.id for r in loaded]
    assert ids.index("first") < ids.index("second")


def test_manager_circular_dependency_marks_failed(tmp_path: Path, clean_registry):
    pack_root = tmp_path / "mypack"
    plugins_root = pack_root / "plugins"
    _write_plugin(plugins_root, "loop_a", depends=["loop_b"], body=textwrap.dedent("""
        from world_gal_game.plugins import effect
        @effect("loop_a_kind")
        def fn(state, eff): return {}
    """))
    _write_plugin(plugins_root, "loop_b", depends=["loop_a"], body=textwrap.dedent("""
        from world_gal_game.plugins import effect
        @effect("loop_b_kind")
        def fn(state, eff): return {}
    """))
    mgr = PluginManager(pack_root=pack_root, engine_version="0.1.0")
    mgr.discover()
    mgr.activate(PluginContext(pack_root=pack_root))
    failed = mgr.failed()
    assert {r.id for r in failed} == {"loop_a", "loop_b"}
    assert all(isinstance(r.error, DependencyError) for r in failed)


def test_manager_missing_dependency_marks_failed(tmp_path: Path, clean_registry):
    pack_root = tmp_path / "mypack"
    plugins_root = pack_root / "plugins"
    _write_plugin(plugins_root, "lonely", depends=["nonexistent_dep"],
                  body=textwrap.dedent("""
        from world_gal_game.plugins import effect
        @effect("lonely_kind")
        def fn(state, eff): return {}
    """))
    mgr = PluginManager(pack_root=pack_root, engine_version="0.1.0")
    mgr.discover()
    mgr.activate(PluginContext(pack_root=pack_root))
    failed = mgr.failed()
    assert any(r.id == "lonely" for r in failed)
    assert "lonely" not in EFFECT_REGISTRY  # nothing leaked


def test_manager_engine_incompatible_marks_failed(tmp_path: Path, clean_registry):
    pack_root = tmp_path / "mypack"
    plugins_root = pack_root / "plugins"
    _write_plugin(plugins_root, "too_new",
                  engine_version=">=99.0.0",
                  body="from world_gal_game.plugins import effect\n")
    mgr = PluginManager(pack_root=pack_root, engine_version="0.1.0")
    mgr.discover()
    mgr.activate(PluginContext(pack_root=pack_root))
    failed = mgr.failed()
    assert any(r.id == "too_new" for r in failed)
    assert isinstance(failed[0].error, IncompatibleEngineError)


def test_manager_import_failure_marks_failed(tmp_path: Path, clean_registry):
    pack_root = tmp_path / "mypack"
    plugins_root = pack_root / "plugins"
    _write_plugin(plugins_root, "broken",
                  body="raise ValueError('intentional')\n")
    mgr = PluginManager(pack_root=pack_root, engine_version="0.1.0")
    mgr.discover()
    mgr.activate(PluginContext(pack_root=pack_root))
    failed = mgr.failed()
    assert any(r.id == "broken" for r in failed)
    assert isinstance(failed[0].error, PluginLoadError)


def test_manager_malformed_manifest_marks_failed(tmp_path: Path, clean_registry):
    pack_root = tmp_path / "mypack"
    plugins_root = pack_root / "plugins"
    plugins_root.mkdir(parents=True, exist_ok=True)
    bad_dir = plugins_root / "bad_yaml"
    bad_dir.mkdir()
    (bad_dir / "plugin.yaml").write_text("not: valid: yaml: at: all\n",
                                          encoding="utf-8")
    mgr = PluginManager(pack_root=pack_root, engine_version="0.1.0")
    mgr.discover()
    failed = mgr.failed()
    assert any(r.state == "failed" for r in mgr.records.values())
    # Synthetic id is allowed to be anything; the key thing is it didn't crash.


def test_manager_deactivate_unregisters_everything(tmp_path: Path, clean_registry):
    pack_root = tmp_path / "mypack"
    plugins_root = pack_root / "plugins"
    _write_plugin(plugins_root, "zeta", body=textwrap.dedent("""
        from world_gal_game.plugins import effect, condition, hook
        @effect("zeta_eff")
        def e(state, eff): return {}
        @condition("zeta_cond")
        def c(state, cond): return True
        @hook("zeta_event")
        def h(ctx, **kw): pass
    """))
    mgr = PluginManager(pack_root=pack_root, engine_version="0.1.0")
    mgr.discover()
    mgr.activate(PluginContext(pack_root=pack_root))
    assert "zeta_eff" in EFFECT_REGISTRY
    assert "zeta_cond" in CONDITION_REGISTRY
    mgr.deactivate()
    assert "zeta_eff" not in EFFECT_REGISTRY
    assert "zeta_cond" not in CONDITION_REGISTRY
    assert "zeta_event" not in HOOK_REGISTRY.list_events()


# ----------------------------------------------------------------------
# Manager summary


def test_manager_summary_includes_loaded_and_failed(tmp_path: Path, clean_registry):
    pack_root = tmp_path / "mypack"
    plugins_root = pack_root / "plugins"
    _write_plugin(plugins_root, "good", body=textwrap.dedent("""
        from world_gal_game.plugins import effect
        @effect("good_kind")
        def fn(state, eff): return {}
    """))
    _write_plugin(plugins_root, "broken", body="raise ValueError('x')\n")
    mgr = PluginManager(pack_root=pack_root, engine_version="0.1.0")
    mgr.discover()
    mgr.activate(PluginContext(pack_root=pack_root))
    summ = mgr.summary()
    loaded_ids = [p["id"] for p in summ["loaded"]]
    failed_ids = [p["id"] for p in summ["failed"]]
    assert "good" in loaded_ids
    assert "broken" in failed_ids


# ----------------------------------------------------------------------
# Hooks: effect.before/after_apply


def test_apply_fires_before_and_after_hooks(state, clean_registry):
    fired = []

    @hook(HookEvent.EFFECT_BEFORE_APPLY, plugin_id="testbed")
    def before(ctx, eff=None, **kw):
        fired.append(("before", eff.kind))

    @hook(HookEvent.EFFECT_AFTER_APPLY, plugin_id="testbed")
    def after(ctx, eff=None, result=None, **kw):
        fired.append(("after", eff.kind, result.get("kind")))

    # Wire a manager into state.meta so the hooks fire
    mgr = PluginManager(engine_version="0.1.0")
    ctx = PluginContext(state=state, manager=mgr)
    mgr.set_context(ctx)
    state.meta["__plugin_manager__"] = mgr

    state.apply(Effect(kind="set_flag", target="x", value=True))
    assert fired == [("before", "set_flag"), ("after", "set_flag", "set_flag")]


def test_hook_exception_does_not_crash_apply(state, clean_registry):
    @hook(HookEvent.EFFECT_AFTER_APPLY, plugin_id="testbed")
    def bad(ctx, eff=None, result=None, **kw):
        raise RuntimeError("hook explodes")
    mgr = PluginManager(engine_version="0.1.0")
    ctx = PluginContext(state=state, manager=mgr)
    mgr.set_context(ctx)
    state.meta["__plugin_manager__"] = mgr
    # apply still returns successfully despite hook failure
    out = state.apply(Effect(kind="set_flag", target="ok", value=True))
    assert out["kind"] == "set_flag"
    assert state.events.get_flag("ok") is True


def test_no_manager_means_hooks_are_silent(state, clean_registry):
    """GameState without a manager attached should never try to fire hooks."""
    fired = []

    @hook(HookEvent.EFFECT_AFTER_APPLY, plugin_id="testbed")
    def after(ctx, **kw):
        fired.append(1)

    state.apply(Effect(kind="set_flag", target="x", value=True))
    assert fired == []  # no manager → no fire


# ----------------------------------------------------------------------
# PluginContext


def test_plugin_context_per_plugin_state_is_namespaced(state):
    ctx = PluginContext(state=state)
    slot = ctx.get_plugin_state("alpha")
    slot["x"] = 1
    assert state.meta["__plugin:alpha__"] == {"x": 1}
    # different plugin gets a different slot
    other = ctx.get_plugin_state("beta")
    other["x"] = 99
    assert ctx.get_plugin_state("alpha") == {"x": 1}


def test_hook_event_all_returns_known_events():
    events = HookEvent.all()
    assert HookEvent.EFFECT_AFTER_APPLY in events
    assert HookEvent.PACK_AFTER_LOAD in events
    assert HookEvent.GAME_STATE_READY in events


# ----------------------------------------------------------------------
# Snapshot / restore


def test_snapshot_restore_round_trip():
    """snapshot() + restore() returns the registry to its prior state."""
    before = sorted(EFFECT_REGISTRY.list_kinds())
    snap = snapshot()

    @effect("__transient__", plugin_id="testbed")
    def fn(state, eff): return {}
    assert "__transient__" in EFFECT_REGISTRY

    restore(snap)
    assert "__transient__" not in EFFECT_REGISTRY
    assert sorted(EFFECT_REGISTRY.list_kinds()) == before


# ----------------------------------------------------------------------
# step_counter demo end-to-end


def test_step_counter_demo_plugin_loads_with_demo_pack():
    """The demo_pack ships a step_counter plugin; it should load + work."""
    from world_gal_game.config import EngineConfig
    from world_gal_game.headless import HeadlessSession

    snap = snapshot()
    try:
        sess = HeadlessSession.open(EngineConfig(), pack="demo_pack")
        # The plugin's extension points are visible
        assert "reset_step_counter" in EFFECT_REGISTRY
        assert "steps_gte" in CONDITION_REGISTRY
        assert "step_counter" in INSPECT_FIELD_REGISTRY.list_keys()

        # Use the apply path so the hook fires
        sess.state.apply(Effect(kind="move_to", target="town_square"))
        sess.state.apply(Effect(kind="move_to", target="starting_room"))
        slot = sess.state.meta.get("__plugin:step_counter__")
        assert slot["count"] == 2

        # The new condition kind works
        assert sess.state.evaluate(Condition(kind="steps_gte", value=1)) is True
        assert sess.state.evaluate(Condition(kind="steps_gte", value=99)) is False

        # And the reset effect resets it
        out = sess.state.apply(Effect(kind="reset_step_counter"))
        assert out == {"kind": "reset_step_counter", "ok": True,
                       "old": 2, "new": 0}
    finally:
        restore(snap)


def test_step_counter_inspect_field_in_collect():
    """INSPECT_FIELD_REGISTRY.collect() yields step_counter when present."""
    from world_gal_game.config import EngineConfig
    from world_gal_game.headless import HeadlessSession

    snap = snapshot()
    try:
        sess = HeadlessSession.open(EngineConfig(), pack="demo_pack")
        sess.state.apply(Effect(kind="move_to", target="town_square"))
        fields = INSPECT_FIELD_REGISTRY.collect(sess.state)
        assert "step_counter" in fields
        assert fields["step_counter"]["count"] == 1
    finally:
        restore(snap)


# ----------------------------------------------------------------------
# Plugin pre-pack-load hooks see meta


def test_pack_after_load_hook_sees_state_and_meta(tmp_path: Path, clean_registry):
    """Plugins hooked on PACK_AFTER_LOAD should get a populated context."""
    pack_root = tmp_path / "mypack"
    plugins_root = pack_root / "plugins"
    _write_plugin(plugins_root, "watcher", body=textwrap.dedent("""
        from world_gal_game.plugins import hook, HookEvent
        seen = {}
        @hook(HookEvent.PACK_AFTER_LOAD)
        def fn(ctx, pack_root=None, meta=None, **kw):
            seen['pack_root'] = str(pack_root) if pack_root else None
            seen['has_state'] = ctx.state is not None
            seen['meta_keys'] = sorted(list(meta.keys())) if meta else []
    """))

    # Minimal content/ so load_pack works
    (pack_root / "content").mkdir()
    (pack_root / "content" / "meta.yaml").write_text(
        "id: testpack\nname: test\n", encoding="utf-8")

    from world_gal_game.content_loader import load_pack
    state, _registry, _meta = load_pack(pack_root / "content")
    # Reach into the loaded module to read its captured dict
    mgr = state.meta["__plugin_manager__"]
    rec = mgr.records["watcher"]
    seen = rec.module.seen  # type: ignore[union-attr]
    assert seen["has_state"] is True
    assert "id" in seen["meta_keys"]


# ----------------------------------------------------------------------
# Registry direct API


def test_registry_kinds_by_plugin_groups_correctly(clean_registry):
    @effect("__one__", plugin_id="alpha")
    def a(state, eff): return {}
    @effect("__two__", plugin_id="alpha")
    def b(state, eff): return {}
    @effect("__three__", plugin_id="beta")
    def c(state, eff): return {}
    grp = EFFECT_REGISTRY.kinds_by_plugin()
    assert "alpha" in grp
    assert "__one__" in grp["alpha"] and "__two__" in grp["alpha"]
    assert grp["beta"] == ["__three__"]


def test_registry_require_raises_on_unknown():
    with pytest.raises(UnknownKindError):
        EFFECT_REGISTRY.require("__definitely_not_there__")
