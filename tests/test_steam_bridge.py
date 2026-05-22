"""Tests for the Steam integration.

The contract under test (no real Steam present in CI):

- importing ``integrations.steam_bridge`` / ``steam_plugin`` never raises;
- ``SteamBridge.try_init`` returns None when the steam_api lib can't load;
- the achievement hook + app integration are no-ops when no bridge is
  parked on the state, so the whole engine still runs without Steam.
"""
from __future__ import annotations

import ctypes

import pytest

from world_gal_game.plugins import snapshot, restore, HookEvent, HOOK_REGISTRY
from world_gal_game.plugins.context import PluginContext


# ---------- import safety ----------------------------------------------------

def test_imports_never_raise() -> None:
    import world_gal_game.integrations  # noqa: F401
    import world_gal_game.integrations.steam_bridge as sb  # noqa: F401
    import world_gal_game.integrations.steam_plugin as sp  # noqa: F401
    assert hasattr(sb, "SteamBridge")
    assert hasattr(sp, "push_achievements")


# ---------- try_init degrades gracefully -------------------------------------

def test_try_init_returns_none_when_lib_missing(monkeypatch) -> None:
    """When ctypes.CDLL always raises, the bridge can't load → None."""
    from world_gal_game.integrations import steam_bridge as sb

    def _boom(*_a, **_k):
        raise OSError("no steam_api here")

    monkeypatch.setattr(ctypes, "CDLL", _boom)
    bridge = sb.SteamBridge.try_init(480)
    assert bridge is None


def test_try_init_returns_none_when_init_false(monkeypatch) -> None:
    """Lib loads but SteamAPI_Init returns false → None (Steam not running)."""
    from world_gal_game.integrations import steam_bridge as sb

    class _FakeFn:
        restype = None
        argtypes = None
        def __call__(self, *a, **k):
            return False  # SteamAPI_Init -> false

    class _FakeLib:
        def __getattr__(self, name):
            return _FakeFn()

    monkeypatch.setattr(ctypes, "CDLL", lambda *_a, **_k: _FakeLib())
    bridge = sb.SteamBridge.try_init(480)
    assert bridge is None


# ---------- bridge methods are exception-safe --------------------------------

def test_bridge_methods_safe_on_fake_lib() -> None:
    """A bridge over a do-nothing lib still behaves: unlock dedupes, no raise."""
    from world_gal_game.integrations import steam_bridge as sb

    class _FakeFn:
        restype = None
        argtypes = None
        def __call__(self, *a, **k):
            return True

    class _FakeLib:
        def __getattr__(self, name):
            return _FakeFn()

    bridge = sb.SteamBridge(_FakeLib())
    bridge.unlock("ach_a")
    bridge.unlock("ach_a")  # repeat is a no-op
    bridge.unlock("ach_b")
    assert bridge.pushed_ids() == {"ach_a", "ach_b"}
    # push_unlocked returns count of newly pushed.
    assert bridge.push_unlocked(["ach_a", "ach_c"]) == 1
    bridge.run_callbacks()
    bridge.shutdown()
    # After shutdown, methods are inert no-ops.
    bridge.unlock("ach_d")
    bridge.run_callbacks()


def test_bridge_mapping_translates_engine_id() -> None:
    from world_gal_game.integrations import steam_bridge as sb

    class _FakeLib:
        def __getattr__(self, name):
            return lambda *a, **k: True

    bridge = sb.SteamBridge(_FakeLib(), mapping={"eng_id": "STEAM_NAME"})
    assert bridge.steam_name("eng_id") == "STEAM_NAME"
    assert bridge.steam_name("other") == "other"  # identity fallback


# ---------- the hook is a no-op without a bridge -----------------------------

@pytest.fixture
def clean_registry():
    snap = snapshot()
    yield
    restore(snap)


def _make_state_with_unlocked():
    from world_gal_game.core.game_state import GameState
    from world_gal_game.core.achievements import Achievement
    state = GameState()
    state.achievements.register(Achievement(id="ach_x", title="X"))
    state.achievements.unlocked["ach_x"] = "2026-01-01T00:00:00+00:00"
    return state


def test_hook_noop_without_bridge(clean_registry) -> None:
    """Firing EFFECT_AFTER_APPLY with no bridge parked does nothing + no raise."""
    import world_gal_game.integrations.steam_plugin  # noqa: F401 (registers hook)

    state = _make_state_with_unlocked()
    ctx = PluginContext(state=state)
    # No __steam_bridge__ on the state → handler returns immediately.
    errors = HOOK_REGISTRY.fire(HookEvent.EFFECT_AFTER_APPLY, ctx, eff=None, result=None)
    assert errors == []  # isolate captured no exceptions


def test_hook_pushes_to_parked_bridge(clean_registry) -> None:
    """When a bridge IS parked, the hook diffs unlocked onto it."""
    import world_gal_game.integrations.steam_plugin as sp

    class _RecordingBridge:
        def __init__(self):
            self.calls = []
            self._pushed = set()
        def push_unlocked(self, ids):
            new = [i for i in ids if i not in self._pushed]
            self._pushed.update(new)
            self.calls.append(list(ids))
            return len(new)

    state = _make_state_with_unlocked()
    bridge = _RecordingBridge()
    state.meta[sp.STEAM_BRIDGE_META_KEY] = bridge

    ctx = PluginContext(state=state)
    HOOK_REGISTRY.fire(HookEvent.EFFECT_AFTER_APPLY, ctx, eff=None, result=None)
    assert bridge._pushed == {"ach_x"}
