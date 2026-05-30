"""Ambient / weather overlays (Pillar 2 of the presentation layer).

Covers the tenth extension category end to end:

1. The shared helpers (``ui/ambient_backend.py``) — the deterministic LCG and
   the ParticleBackend lifecycle.
2. The bundled weather backends (rain/snow/petals/sparkles/fireflies) — they
   construct, update, and draw without raising, and are deterministic for a
   fixed seed (the engine's replay/determinism rule).
3. The builtin effects (``set_weather`` / ``clear_weather``) — enqueue a
   JSON-able directive, never touch the display.
4. Registration plumbing — the ``@ambient_backend`` decorator, the manifest,
   snapshot/restore, and the bundled plugin's declarations.
5. Integration through a live ``DialogueScene`` (via ``GameDriver``).
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import Effect, Scene, Line
from world_gal_game.plugins.builtin_effects import VISUAL_FX_QUEUE
from world_gal_game.ui.ambient_backend import Lcg, coerce_color, ParticleBackend


# ---------------------------------------------------------------------------
# Helpers: Lcg + ParticleBackend
# ---------------------------------------------------------------------------

def test_lcg_is_deterministic_for_a_seed():
    a = Lcg(42)
    b = Lcg(42)
    seq_a = [a.next_float() for _ in range(8)]
    seq_b = [b.next_float() for _ in range(8)]
    assert seq_a == seq_b
    assert all(0.0 <= v < 1.0 for v in seq_a)


def test_lcg_different_seeds_diverge():
    assert [Lcg(1).next_u32() for _ in range(4)] != \
           [Lcg(2).next_u32() for _ in range(4)]


def test_coerce_color_bad_value_falls_back():
    assert coerce_color("nope", default=(1, 2, 3)) == (1, 2, 3)
    assert coerce_color([300, -5, 40]) == (255, 0, 40)


def test_particle_backend_wraps_particles_on_screen():
    class Dots(ParticleBackend):
        default_count = 5
        def _spawn(self, rng, w, h):
            return {"x": rng.uniform(0, w), "y": 0.0, "vx": 0.0, "vy": 1000.0}
        def _draw(self, surface, p):
            pass
    b = Dots({"seed": 1}, (100, 100))
    b.update(1.0)                       # vy*dt pushes well past the bottom
    assert all(-40 <= p["y"] <= 140 for p in b.particles)  # wrapped, not gone


# ---------------------------------------------------------------------------
# Bundled weather backends — construct / update / draw / determinism
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def weather_classes():
    # Importing the plugin module registers the backends as a side effect under
    # the "builtin" owner (no plugin-loading context). Snapshot/restore around
    # the import so the global registry stays clean — otherwise the later
    # GameDriver pack load can't register the same names under its own plugin id
    # (DuplicateKindError). The class *objects* remain usable after restore.
    import importlib
    from world_gal_game.plugins import (snapshot, restore,
                                         AMBIENT_BACKEND_REGISTRY)
    snap = snapshot()
    # The plugin manager loads this plugin under a file-based module name, so a
    # package-path import re-runs its @ambient_backend decorators. Clear the
    # ambient registry first so that re-registration doesn't raise
    # DuplicateKindError when a prior test has already loaded the pack.
    AMBIENT_BACKEND_REGISTRY._entries.clear()
    mod = importlib.import_module(
        "world_gal_game.plugins_user.ambient_weather.plugin")
    classes = {
        "rain": mod.RainBackend, "snow": mod.SnowBackend,
        "petals": mod.PetalsBackend, "sparkles": mod.SparklesBackend,
        "fireflies": mod.FirefliesBackend,
    }
    restore(snap)
    return classes


@pytest.mark.parametrize("name", ["rain", "snow", "petals", "sparkles", "fireflies"])
def test_weather_backend_constructs_updates_draws(weather_classes, name):
    pygame.display.init()
    cls = weather_classes[name]
    b = cls({"count": 25, "seed": 3}, (320, 240))
    surf = pygame.Surface((320, 240), pygame.SRCALPHA)
    for _ in range(4):
        b.update(0.05)
        b.draw(surf)                    # must not raise


@pytest.mark.parametrize("name", ["rain", "snow", "petals", "sparkles", "fireflies"])
def test_weather_backend_is_deterministic(weather_classes, name):
    cls = weather_classes[name]
    a = cls({"count": 20, "seed": 99}, (320, 240))
    b = cls({"count": 20, "seed": 99}, (320, 240))
    for _ in range(10):
        a.update(0.1)
        b.update(0.1)
    pa = [(round(p["x"], 4), round(p["y"], 4)) for p in a.particles]
    pb = [(round(p["x"], 4), round(p["y"], 4)) for p in b.particles]
    assert pa == pb


def test_weather_zero_alpha_draws_nothing(weather_classes):
    pygame.display.init()
    b = weather_classes["rain"]({"count": 10, "seed": 1, "alpha": 0}, (100, 100))
    before = pygame.Surface((100, 100))
    before.fill((5, 5, 5))
    after = before.copy()
    b.draw(after)
    # alpha 0 → nothing blitted, surface unchanged.
    assert pygame.image.tobytes(before, "RGB") == pygame.image.tobytes(after, "RGB")


# ---------------------------------------------------------------------------
# Effects — enqueue directives, never touch the display
# ---------------------------------------------------------------------------

def _queued(state: GameState) -> list[dict]:
    return state.meta.get(VISUAL_FX_QUEUE, [])


def test_set_weather_enqueues_directive_and_extracts_fade():
    s = GameState()
    out = s.apply(Effect(kind="set_weather", target="rain",
                         value={"count": 50, "fade": 1.5, "wind": -300}))
    assert out["backend"] == "rain"
    d = _queued(s)[0]
    assert d["fx"] == "set_weather"
    assert d["backend"] == "rain"
    assert d["fade"] == pytest.approx(1.5)
    # fade is popped out of params; backend-specific keys pass through.
    assert "fade" not in d["params"]
    assert d["params"]["wind"] == -300
    assert d["params"]["count"] == 50


def test_clear_weather_enqueues_directive():
    s = GameState()
    s.apply(Effect(kind="clear_weather", value={"fade": 0.8}))
    d = _queued(s)[0]
    assert d["fx"] == "clear_weather"
    assert d["fade"] == pytest.approx(0.8)


def test_weather_directives_are_json_able():
    import json
    s = GameState()
    s.apply(Effect(kind="set_weather", target="snow", value={"color": [200, 200, 255]}))
    json.dumps(_queued(s))


# ---------------------------------------------------------------------------
# Registration plumbing — manifest, decorator, snapshot/restore
# ---------------------------------------------------------------------------

def test_decorator_registers_and_manifest_lists_ambient_backend():
    from world_gal_game.plugins import ambient_backend, AMBIENT_BACKEND_REGISTRY
    from world_gal_game.dev.capability_manifest import (
        build_manifest, all_ambient_backend_names)

    @ambient_backend("unit_fog", description="test")
    class Fog:
        def __init__(self, params, screen_size): pass
        def update(self, dt): pass
        def draw(self, surface): pass

    assert "unit_fog" in AMBIENT_BACKEND_REGISTRY.list_names()
    assert "unit_fog" in all_ambient_backend_names()
    m = build_manifest()
    names = {e["name"] for e in m["ambient_backends"]}
    assert "unit_fog" in names
    assert "unit_fog" in m["markup"]["ambient_backends"]


def test_snapshot_restore_roundtrips_ambient_registry():
    from world_gal_game.plugins import (ambient_backend, snapshot, restore,
                                         AMBIENT_BACKEND_REGISTRY)
    snap = snapshot()
    try:
        @ambient_backend("unit_temp_weather")
        class W:
            def __init__(self, params, screen_size): pass
            def update(self, dt): pass
            def draw(self, surface): pass
        assert "unit_temp_weather" in AMBIENT_BACKEND_REGISTRY.list_names()
    finally:
        restore(snap)
    assert "unit_temp_weather" not in AMBIENT_BACKEND_REGISTRY.list_names()


# ---------------------------------------------------------------------------
# Integration through a live DialogueScene
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="module")
def _restore_pygame_after_module():
    yield
    if not pygame.display.get_init():
        pygame.init()
        pygame.display.set_mode((10, 10))


@pytest.fixture
def driver():
    from world_gal_game.dev.driver import GameDriver
    d = GameDriver(pack="demo_pack")
    yield d
    d.quit()


def test_bundled_plugin_registers_five_weathers(driver):
    from world_gal_game.plugins.registry import AMBIENT_BACKEND_REGISTRY
    names = set(AMBIENT_BACKEND_REGISTRY.list_names())
    assert {"rain", "snow", "petals", "sparkles", "fireflies"} <= names


def _open(driver, scene: Scene):
    app = driver.app
    app.state.story.add_scene(scene)
    app._start_dialogue(scene.id)
    app.manager.commit_pending()
    driver.advance_frames(2)
    ds = app.manager.current
    if ds.box and not ds.box.fully_revealed():
        ds.box.force_reveal()
    return ds


def test_set_then_clear_weather_through_scene(driver):
    sc = Scene(id="probe_w", lines=[
        Line(text="rain on", effects=[Effect(
            kind="set_weather", target="rain", value={"count": 20, "seed": 5})]),
        Line(text="rain off", effects=[Effect(kind="clear_weather")]),
    ])
    ds = _open(driver, sc)
    assert ds._ambient_name == "rain"
    assert ds._ambient is not None

    ds.box.force_reveal()
    ds._advance()
    driver.advance_frames(2)
    assert ds._ambient_name is None


def test_set_weather_with_fade_starts_transparent(driver):
    sc = Scene(id="probe_w_fade", lines=[
        Line(text="fade in snow", effects=[Effect(
            kind="set_weather", target="snow",
            value={"count": 20, "seed": 5, "fade": 1.0, "alpha": 200})]),
    ])
    ds = _open(driver, sc)
    # One frame into a 1.0s fade-in: live alpha well below the 200 target.
    assert ds._ambient is not None
    assert getattr(ds._ambient, "alpha", 200) < 200


def test_unknown_weather_degrades_to_none(driver):
    sc = Scene(id="probe_w_bad", lines=[
        Line(text="bad", effects=[Effect(
            kind="set_weather", target="does_not_exist")]),
    ])
    ds = _open(driver, sc)
    driver.advance_frames(1)
    assert ds._ambient is None      # unknown backend → no overlay, no crash
