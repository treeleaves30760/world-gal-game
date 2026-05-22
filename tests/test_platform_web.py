"""Tests for world_gal_game.platform_web.

These run on a desktop test host, so ``is_web()`` is False and
``flush_storage()`` must be a no-op that never raises. The on-web branch is
exercised only inside the browser (it touches the pygbag runtime), so here
we just pin the off-web contract the rest of the engine relies on.
"""
from __future__ import annotations

import world_gal_game.platform_web as pw


def test_import_never_fails() -> None:
    # Importing the module on desktop must have no side effects / no error.
    import importlib
    importlib.reload(pw)
    assert hasattr(pw, "is_web")
    assert hasattr(pw, "flush_storage")


def test_is_web_false_in_test_env() -> None:
    # The test runner is not Emscripten.
    assert pw.is_web() is False


def test_flush_storage_is_noop_off_web() -> None:
    # Off-web flush returns None and raises nothing, repeatedly.
    assert pw.flush_storage() is None
    assert pw.flush_storage() is None


def test_flush_storage_does_not_touch_runtime_off_web(monkeypatch) -> None:
    """Even if `import platform` were to misbehave, off-web returns early.

    We force is_web() False and assert flush_storage exits before touching
    any pygbag-style runtime probing.
    """
    monkeypatch.setattr(pw, "is_web", lambda: False)
    # Should be a pure no-op; no exception.
    assert pw.flush_storage() is None
