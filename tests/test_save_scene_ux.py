"""WP-D — Save/Load UX upgrade for ``scenes/save_scene.py``.

Covers the scrollable, grouped slot list:

- every slot on disk (quicksave + autosave_* + manual) becomes a row;
- special slots (quicksave / autosave_*) are tagged + identifiable and pinned
  to the top, ahead of manual slots (manual newest-first);
- special slots are loadable in load mode;
- autosave_* slots are read-only in save mode (no action hit rect), while
  quicksave stays overwritable;
- the "+ 新增存檔" entry only appears in save mode;
- drawn action chips register absolute on-screen hit rects (accounting for
  scroll) so a click dispatches the right slot;
- a wired ``get_screen_surface`` produces a thumbnail PNG that renders.

The scene is exercised against a hand-built ``SceneContext`` (real Theme /
FontRegistry / EngineConfig / GameState / Localization), with slots planted
through a real ``SaveManager`` into a temp dir — fast and display-light, the
same shape as ``tests/test_music_room_scene.py`` + ``tests/test_save_manager.py``.
"""
from __future__ import annotations

import os
import types
from pathlib import Path

import pygame
import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _pygame_init():
    pygame.display.init()
    pygame.font.init()
    # A real (dummy-driver) display surface so convert_alpha() works when a
    # thumbnail is decoded.
    pygame.display.set_mode((1280, 720))
    yield
    pygame.quit()


def _minimal_state() -> dict:
    """Bare-minimum state dict that SaveManager.save() is happy to persist."""
    return {"player": {"name": "TestPlayer"}, "meta": {}}


def _make_ctx(save_dir: Path):
    """A SceneContext wired to a SaveManager pointed at ``save_dir``."""
    from world_gal_game.config import EngineConfig
    from world_gal_game.core.game_state import GameState
    from world_gal_game.core.localization import Localization
    from world_gal_game.scenes.base import SceneContext
    from world_gal_game.ui.fonts import FontRegistry
    from world_gal_game.ui.theme import default_theme

    cfg = EngineConfig()
    cfg.save_dir = types.MethodType(lambda self, _p=save_dir: _p, cfg)
    ctx = SceneContext(
        config=cfg,
        state=GameState(),
        npcs=None,            # type: ignore[arg-type]  (scene never uses these)
        brain=None,           # type: ignore[arg-type]
        dialogue=None,        # type: ignore[arg-type]
        assets=None,          # type: ignore[arg-type]
        fonts=FontRegistry(candidates=()),
        theme=default_theme(),
        localization=Localization(),
        screen_size=(1280, 720),
    )
    return ctx


def _plant_slots(save_dir: Path) -> None:
    """Write a quicksave, two autosaves, and a couple of manual slots."""
    import time as _time
    from world_gal_game.core.save_manager import SaveManager

    sm = SaveManager(save_dir)
    # Manual first, so they are NOT the newest (lets us assert ordering).
    sm.save("slot_aaa", _minimal_state(), label="Manual A", summary="day 1")
    _time.sleep(0.01)
    sm.save("slot_bbb", _minimal_state(), label="Manual B", summary="day 2")
    _time.sleep(0.01)
    sm.save("quicksave", _minimal_state(), label="Quick", summary="qs")
    _time.sleep(0.01)
    sm.save("autosave_2", _minimal_state(), label="Auto 2", summary="a2")
    _time.sleep(0.01)
    sm.save("autosave_1", _minimal_state(), label="Auto 1", summary="a1")


def _open(ctx, *, mode: str, on_close=None, get_screen_surface=None):
    from world_gal_game.scenes.save_scene import SaveScene
    scene = SaveScene(ctx)
    scene.enter(mode=mode, on_close=on_close,
                get_screen_surface=get_screen_surface)
    return scene


def _surface():
    return pygame.Surface((1280, 720), pygame.SRCALPHA)


class _Input:
    """Minimal stand-in for the per-frame InputState the scene reads."""

    def __init__(self, *, cancel=False, mouse_pos=(0, 0),
                 mouse_clicked=False, mouse_wheel=0):
        self.cancel = cancel
        self.mouse_pos = mouse_pos
        self.mouse_clicked = mouse_clicked
        self.mouse_wheel = mouse_wheel


def _slots(scene) -> list:
    return [it.get("slot") for it in scene._items]


def _hit_rect_for(scene, slot: str):
    """Draw a frame, then return the action hit rect for ``slot`` (or None)."""
    scene.draw(_surface())
    for rect, item in scene._row_rects:
        if item.get("slot") == slot:
            return rect
    return None


# ---------------------------------------------------------------------------
# List assembly: every slot becomes a row
# ---------------------------------------------------------------------------

def test_load_mode_builds_a_row_for_every_slot(tmp_path):
    _plant_slots(tmp_path)
    scene = _open(_make_ctx(tmp_path), mode="load")
    slots = set(_slots(scene))
    assert {"quicksave", "autosave_1", "autosave_2",
            "slot_aaa", "slot_bbb"} <= slots
    # Load mode has no synthetic "new save" row.
    assert None not in slots
    assert scene.describe()["save_count"] == 5


def test_save_mode_has_new_save_entry_first(tmp_path):
    _plant_slots(tmp_path)
    scene = _open(_make_ctx(tmp_path), mode="save")
    # The synthetic "new save" row is item 0 (preserves the legacy contract
    # other tests rely on: scene._items[0] is the new-save item in save mode).
    assert scene._items[0]["slot"] is None
    assert "新增" in scene._items[0]["label"]


# ---------------------------------------------------------------------------
# Special slots: tagged, grouped, identifiable
# ---------------------------------------------------------------------------

def test_special_slots_are_tagged_and_pinned_to_top(tmp_path):
    _plant_slots(tmp_path)
    scene = _open(_make_ctx(tmp_path), mode="load")
    slots = _slots(scene)
    # quicksave first, then autosave_1, autosave_2 (numeric order), then the
    # manual slots — and the manual slots stay newest-first (bbb before aaa).
    assert slots[:3] == ["quicksave", "autosave_1", "autosave_2"]
    assert slots.index("slot_bbb") < slots.index("slot_aaa")


def test_describe_reports_special_slots(tmp_path):
    _plant_slots(tmp_path)
    scene = _open(_make_ctx(tmp_path), mode="load")
    special = set(scene.describe()["special_slots"])
    assert special == {"quicksave", "autosave_1", "autosave_2"}
    # describe() stays JSON-able (no surfaces / sets leak in).
    import json
    json.loads(json.dumps(scene.describe()))


def test_classification_helpers(tmp_path):
    scene = _open(_make_ctx(tmp_path), mode="load")
    assert scene._is_quicksave("quicksave") is True
    assert scene._is_autosave("autosave_3") is True
    assert scene._is_special("autosave_3") is True
    assert scene._is_special("slot_xyz") is False
    # Each special slot carries a non-empty badge; manual slots carry none.
    assert scene._slot_tag("quicksave")
    assert scene._slot_tag("autosave_1")
    assert scene._slot_tag("slot_xyz") is None


# ---------------------------------------------------------------------------
# Action gating: load all; save protects autosave; quicksave overwritable
# ---------------------------------------------------------------------------

def test_special_slots_loadable_in_load_mode(tmp_path):
    _plant_slots(tmp_path)
    scene = _open(_make_ctx(tmp_path), mode="load")
    for slot in ("quicksave", "autosave_1", "autosave_2",
                 "slot_aaa", "slot_bbb"):
        item = next(it for it in scene._items if it.get("slot") == slot)
        assert scene._action_enabled(item) is True
        # ...and each renders an actionable hit rect.
        assert _hit_rect_for(scene, slot) is not None


def test_autosave_readonly_quicksave_overwritable_in_save_mode(tmp_path):
    _plant_slots(tmp_path)
    scene = _open(_make_ctx(tmp_path), mode="save")
    auto = next(it for it in scene._items if it.get("slot") == "autosave_1")
    quick = next(it for it in scene._items if it.get("slot") == "quicksave")
    manual = next(it for it in scene._items if it.get("slot") == "slot_aaa")
    # Autosave is read-only in save mode; quicksave + manual are overwritable.
    assert scene._action_enabled(auto) is False
    assert scene._action_enabled(quick) is True
    assert scene._action_enabled(manual) is True
    # The disabled autosave registers NO hit rect, so a click there is inert.
    assert _hit_rect_for(scene, "autosave_1") is None
    assert _hit_rect_for(scene, "quicksave") is not None


# ---------------------------------------------------------------------------
# Hit-testing inside the ScrollArea
# ---------------------------------------------------------------------------

def test_click_on_action_dispatches_correct_slot(tmp_path, monkeypatch):
    _plant_slots(tmp_path)
    scene = _open(_make_ctx(tmp_path), mode="load")
    fired: list = []
    monkeypatch.setattr(scene, "_on_action",
                        lambda item: fired.append(item.get("slot")))
    rect = _hit_rect_for(scene, "slot_bbb")
    assert rect is not None
    scene.update(0.0, _Input(mouse_clicked=True, mouse_pos=rect.center))
    assert fired == ["slot_bbb"]


def test_click_outside_any_action_is_inert(tmp_path, monkeypatch):
    _plant_slots(tmp_path)
    scene = _open(_make_ctx(tmp_path), mode="load")
    fired: list = []
    monkeypatch.setattr(scene, "_on_action",
                        lambda item: fired.append(item.get("slot")))
    scene.draw(_surface())
    # (5, 5) is the panel header — well clear of any card action chip.
    scene.update(0.0, _Input(mouse_clicked=True, mouse_pos=(5, 5)))
    assert fired == []


def test_scroll_shifts_hit_rects(tmp_path):
    # Plant enough manual slots that the list overflows the scroll viewport.
    from world_gal_game.core.save_manager import SaveManager
    sm = SaveManager(tmp_path)
    for i in range(20):
        sm.save(f"slot_{i:02d}", _minimal_state(), label=f"S{i}")
    scene = _open(_make_ctx(tmp_path), mode="load")
    target = "slot_00"  # oldest manual slot → near the bottom of the list

    before = _hit_rect_for(scene, target)
    # Scroll the list down; clamp logic runs in ScrollArea.update().
    scene.update(0.0, _Input(mouse_wheel=-5,
                             mouse_pos=scene._scroll.rect.center))
    after = _hit_rect_for(scene, target)
    assert before is not None and after is not None
    # A non-zero scroll moves the row's on-screen rect upward.
    assert scene._scroll.scroll_y > 0
    assert after.y < before.y


# ---------------------------------------------------------------------------
# Thumbnails render now that get_screen_surface is wired
# ---------------------------------------------------------------------------

def test_thumbnail_renders_for_saved_slot(tmp_path):
    from world_gal_game.core.save_manager import SaveManager

    ctx = _make_ctx(tmp_path)
    grab = pygame.Surface((640, 360))
    grab.fill((30, 60, 90))
    scene = _open(ctx, mode="save", get_screen_surface=lambda: grab)
    assert scene._get_screen is not None
    # Save through the "new save" row → writes a thumbnail PNG.
    scene._on_action(scene._items[0])
    rows = SaveManager(tmp_path).list_saves()
    assert any(r["thumbnail_path"] is not None for r in rows)
    # The new slot's thumbnail decodes to the 120x68 display surface.
    thumbed = next(r for r in rows if r["thumbnail_path"] is not None)
    surf = scene._load_thumbnail(thumbed)
    assert surf is not None
    assert surf.get_size() == (120, 68)
    # And a full draw with that thumbnail present must not raise.
    scene.draw(_surface())


# ---------------------------------------------------------------------------
# Close behavior
# ---------------------------------------------------------------------------

def test_cancel_input_closes(tmp_path):
    closed = {"n": 0}
    scene = _open(_make_ctx(tmp_path), mode="load",
                  on_close=lambda: closed.__setitem__("n", closed["n"] + 1))
    scene.update(0.0, _Input(cancel=True))
    assert closed["n"] == 1


def test_empty_dir_draws_without_rows(tmp_path):
    scene = _open(_make_ctx(tmp_path), mode="load")
    assert _slots(scene) == []
    # Drawing the empty case must not raise.
    scene.draw(_surface())
    assert scene.describe()["save_count"] == 0
