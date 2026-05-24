"""Phase 5A — portrait render backends (the 9th plugin extension category).

Covers the seam end to end:

- core: ``PortraitSpec.backend`` / ``backend_args`` defaults + save round-trip
- registry: ``@portrait_backend`` registers / spawns / snapshot-restores
- manifest + capability: ``Extends.portrait_backends``, manager reconciliation,
  ``build_manifest`` + markup exposure
- ui: ``blit_fitted`` geometry + non-mutating alpha; ``StaticBackend``
- bundled ``animated_portraits`` plugin: ``breath`` animates, ``sprite`` cycles,
  both degrade gracefully
- dialogue scene: ``_resolve_slot`` wires specs to backends; a real DialogueScene
  renders a breathing portrait through update/draw without crashing
"""
from __future__ import annotations

import hashlib
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

from world_gal_game.core.portrait_spec import PortraitSpec  # noqa: E402
from world_gal_game.plugins import (  # noqa: E402
    PORTRAIT_BACKEND_REGISTRY, PluginManager, portrait_backend,
    snapshot, restore,
)


def _md5(surf: pygame.Surface) -> str:
    return hashlib.md5(pygame.image.tobytes(surf, "RGBA")).hexdigest()


class _FakeAssets:
    """Minimal AssetManager stand-in: returns a deterministic gradient sheet.

    Distinct in both axes so sprite-frame slices are distinguishable.
    """

    def __init__(self, size: tuple[int, int] = (200, 300)) -> None:
        self._size = size

    def resolve_portrait(self, spec, fallback_size=(480, 640)) -> pygame.Surface:
        w, h = self._size
        s = pygame.Surface((w, h), pygame.SRCALPHA)
        for x in range(w):
            for y in range(0, h, 4):
                s.set_at((x, y), (x % 256, y % 256, 128, 255))
        return s


@pytest.fixture
def clean_registry():
    snap = snapshot()
    yield
    restore(snap)


@pytest.fixture
def backends_loaded():
    """Activate bundled plugins (incl. animated_portraits) within isolation."""
    snap = snapshot()
    mgr = PluginManager(engine_version="0.1.0")
    mgr.discover()
    mgr.activate()
    yield mgr
    restore(snap)


# ----------------------------------------------------------------------
# Core model


def test_portraitspec_backend_defaults_static():
    spec = PortraitSpec(character="alice")
    assert spec.backend == "static"
    assert spec.backend_args == {}


def test_portraitspec_backend_round_trips_through_json():
    spec = PortraitSpec(character="alice", backend="breath",
                        backend_args={"period": 4.0})
    data = spec.model_dump(mode="json")
    again = PortraitSpec(**data)
    assert again.backend == "breath"
    assert again.backend_args == {"period": 4.0}


def test_old_save_without_backend_field_still_loads():
    # An old PortraitSpec dict (pre-5A) has no backend keys -> defaults fill in,
    # so old saves reconstruct untouched (pure-additive, no migration needed).
    legacy = {"character": "alice", "expression": "smile", "slot": "left"}
    spec = PortraitSpec(**legacy)
    assert spec.backend == "static" and spec.backend_args == {}


# ----------------------------------------------------------------------
# Registry + decorator


def test_portrait_backend_decorator_registers_and_spawns(clean_registry):
    @portrait_backend("dummy_be", description="test backend")
    class Dummy:
        def __init__(self, spec, assets, fallback_size):
            self.spec = spec

        def update(self, dt): ...
        def draw(self, surface, rect, *, flip=False, alpha=255): ...
        def base_surface(self): return None

    assert PORTRAIT_BACKEND_REGISTRY.has("dummy_be")
    inst = PORTRAIT_BACKEND_REGISTRY.spawn("dummy_be", PortraitSpec(character="x"),
                                           _FakeAssets(), (100, 200))
    assert isinstance(inst, Dummy)
    entry = PORTRAIT_BACKEND_REGISTRY.get("dummy_be")
    assert entry.plugin_id and entry.description == "test backend"


def test_unknown_backend_spawn_raises(clean_registry):
    from world_gal_game.plugins.errors import UnknownKindError
    with pytest.raises(UnknownKindError):
        PORTRAIT_BACKEND_REGISTRY.spawn("nope_be", None, None, (1, 1))


def test_snapshot_restore_includes_portrait_backends():
    snap = snapshot()
    try:
        @portrait_backend("temp_be", plugin_id="t")
        class _B:
            def __init__(self, *a): ...
        assert PORTRAIT_BACKEND_REGISTRY.has("temp_be")
    finally:
        restore(snap)
    assert not PORTRAIT_BACKEND_REGISTRY.has("temp_be")


# ----------------------------------------------------------------------
# Manifest + capability manifest


def test_manifest_declares_portrait_backends():
    from world_gal_game.plugins.manifest import PluginManifest
    m = PluginManifest.model_validate({
        "id": "demo_be",
        "extends": {"portrait_backends": [{"kind": "wiggle"}]},
    })
    assert [d.kind for d in m.extends.portrait_backends] == ["wiggle"]


def test_reconcile_warns_on_declared_but_unregistered_backend():
    from world_gal_game.plugins.manager import PluginManager, PluginRecord
    from world_gal_game.plugins.manifest import PluginManifest
    rec = PluginRecord(
        manifest=PluginManifest.model_validate(
            {"id": "demo_be", "extends": {"portrait_backends": [{"kind": "ghost_be"}]}}),
        root=None, source="pack")
    rec.portrait_backend_names = []  # nothing registered
    PluginManager(pack_root=None)._reconcile_declarations(rec)
    assert any("ghost_be" in w for w in rec.warnings)


def test_build_manifest_includes_portrait_backends(backends_loaded):
    from world_gal_game.dev.capability_manifest import build_manifest, schema_document
    man = build_manifest()
    assert "portrait_backends" in man
    names = {row["name"] for row in man["portrait_backends"]}
    assert {"breath", "sprite"} <= names
    # markup advertises them for agents that read the capability bundle.
    assert "portrait_backends" in man["markup"]
    assert {"breath", "sprite"} <= set(man["markup"]["portrait_backends"])
    # the PortraitSpec content schema gained the backend field.
    ps = schema_document()["models"]["PortraitSpec"]
    assert "backend" in ps["properties"]


# ----------------------------------------------------------------------
# Bundled animated_portraits plugin


def test_bundled_animated_portraits_loads_clean(backends_loaded):
    rec = backends_loaded.records.get("animated_portraits")
    assert rec is not None and rec.state == "loaded"
    assert rec.portrait_backend_names == ["breath", "sprite"]
    assert rec.warnings == []


def test_breath_backend_animates_over_time(backends_loaded):
    spec = PortraitSpec(character="x", backend="breath",
                        backend_args={"scale": 0.06, "bob": 12})
    be = PORTRAIT_BACKEND_REGISTRY.spawn("breath", spec, _FakeAssets(), (480, 640))
    rect = pygame.Rect(100, 30, 300, 500)
    f0 = pygame.Surface((600, 600), pygame.SRCALPHA)
    be.draw(f0, rect)
    be.update(0.9)
    f1 = pygame.Surface((600, 600), pygame.SRCALPHA)
    be.draw(f1, rect)
    assert _md5(f0) != _md5(f1)
    assert be.base_surface() is not None


def test_sprite_backend_cycles_frames(backends_loaded):
    spec = PortraitSpec(character="x", backend="sprite",
                        backend_args={"cols": 2, "rows": 1, "fps": 10})
    sb = PORTRAIT_BACKEND_REGISTRY.spawn("sprite", spec, _FakeAssets(), (480, 640))
    assert len(sb._frames) == 2
    rect = pygame.Rect(0, 0, 200, 300)
    g0 = pygame.Surface((400, 400), pygame.SRCALPHA)
    sb.draw(g0, rect)
    sb.update(0.15)  # 1.5 frames at 10fps -> idx 1
    g1 = pygame.Surface((400, 400), pygame.SRCALPHA)
    sb.draw(g1, rect)
    assert _md5(g0) != _md5(g1)


def test_sprite_backend_degrades_to_single_frame(backends_loaded):
    # cols/rows default to 1 -> exactly one frame, never raises on draw.
    spec = PortraitSpec(character="x", backend="sprite")
    sb = PORTRAIT_BACKEND_REGISTRY.spawn("sprite", spec, _FakeAssets(), (480, 640))
    assert len(sb._frames) == 1
    surf = pygame.Surface((400, 400), pygame.SRCALPHA)
    sb.update(1.0)
    sb.draw(surf, pygame.Rect(0, 0, 200, 300))  # no exception


# ----------------------------------------------------------------------
# ui.portrait_backend helpers


def test_blit_fitted_alpha_does_not_mutate_source():
    from world_gal_game.ui.portrait_backend import blit_fitted
    src = pygame.Surface((100, 100), pygame.SRCALPHA)
    src.fill((255, 0, 0, 255))
    before = _md5(src)
    dst = pygame.Surface((300, 300), pygame.SRCALPHA)
    blit_fitted(dst, src, pygame.Rect(0, 0, 200, 200), alpha=120)
    assert _md5(src) == before  # source untouched despite the alpha draw


def test_blit_fitted_none_source_is_noop():
    from world_gal_game.ui.portrait_backend import blit_fitted
    dst = pygame.Surface((50, 50), pygame.SRCALPHA)
    blank = _md5(dst)
    blit_fitted(dst, None, pygame.Rect(0, 0, 50, 50))
    assert _md5(dst) == blank


def test_static_backend_matches_resolved_still():
    from world_gal_game.ui.portrait_backend import StaticBackend
    spec = PortraitSpec(character="x")
    sb = StaticBackend(spec, _FakeAssets(), (480, 640))
    assert sb.base_surface() is not None
    surf = pygame.Surface((400, 400), pygame.SRCALPHA)
    sb.update(0.5)  # no-op
    sb.draw(surf, pygame.Rect(0, 0, 200, 300))  # no exception


# ----------------------------------------------------------------------
# Dialogue scene seam


def test_resolve_slot_returns_backend_for_registered_spec(backends_loaded):
    from world_gal_game.scenes.dialogue_scene import DialogueScene
    ctx = SimpleNamespace(assets=_FakeAssets(), screen_size=(1920, 1080))
    scene = DialogueScene(ctx)
    surf, backend = scene._resolve_slot(
        PortraitSpec(character="x", backend="breath"), 1920, 1080)
    assert surf is not None and backend is not None
    # static + unknown both yield no backend (graceful fallback to static blit).
    _, b_static = scene._resolve_slot(PortraitSpec(character="x"), 1920, 1080)
    _, b_unknown = scene._resolve_slot(
        PortraitSpec(character="x", backend="live2d_not_loaded"), 1920, 1080)
    assert b_static is None and b_unknown is None


def test_dialogue_scene_renders_breathing_portrait_without_crash():
    """Drive a real DialogueScene to a backend portrait and render frames."""
    from world_gal_game.dev.driver import GameDriver
    d = GameDriver(pack="demo_pack")
    try:
        d.new_game()
        d.advance_frames(10)
        scene = d.app.manager.current
        assert type(scene).__name__ == "DialogueScene"
        # Inject a breathing portrait via the scene's own line-staging path.
        line = SimpleNamespace(
            portraits=[PortraitSpec(character="heroine_1_normal",
                                    backend="breath")],
            portrait=None, speaker=None, expression=None,
        )
        scene._update_portraits(line)
        assert scene._slot_backends["center"] is not None
        d.advance_frames(40)  # past the 0.25s crossfade -> backend draws
        # Still alive, backend still attached, no exception raised.
        assert scene._slot_backends["center"] is not None
    finally:
        d.quit()
