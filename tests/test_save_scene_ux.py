"""WP-D — Save/Load UX upgrade for ``scenes/save_scene.py``.

Covers the scrollable, grouped slot list:

- every slot on disk (quicksave + autosave_* + manual) becomes a row;
- special slots (quicksave / autosave_*) are tagged + identifiable and pinned
  to the top, ahead of manual slots (manual newest-first);
- special slots are loadable in load mode;
- autosave_* slots are read-only in save mode, while every existing slot still
  has a distinct delete affordance and quicksave stays overwritable;
- the "+ 新增存檔" entry only appears in save mode;
- drawn action chips register absolute on-screen hit rects (accounting for
  scroll) so a click requests the right slot;
- occupied save/load slots require an explicit confirmation while the empty
  save row commits directly;
- keyboard/controller focus moves between rows vertically and between primary
  and delete horizontally; row changes always restore the safe primary action;
- 360p, 720p, and 1080p layouts use measured line heights, keep every action
  target at least 44 px high, and do not overlap card text regions;
- save/load success and failure remain visible in the fixed status band;
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


def _make_ctx(
    save_dir: Path,
    *,
    screen_size: tuple[int, int] = (1280, 720),
):
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
        fonts=FontRegistry(
            candidates=(),
            scale=max(1.0, screen_size[1] / 720.0),
        ),
        theme=default_theme(),
        localization=Localization(),
        screen_size=screen_size,
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


def _surface(size=(1280, 720)):
    return pygame.Surface(size, pygame.SRCALPHA)


class _Input:
    """Minimal stand-in for the per-frame InputState the scene reads."""

    def __init__(self, *, cancel=False, mouse_pos=(0, 0),
                 mouse_clicked=False, mouse_wheel=0, nav=0,
                 confirm=False, actions=()):
        self.cancel = cancel
        self.mouse_pos = mouse_pos
        self.mouse_clicked = mouse_clicked
        self.mouse_wheel = mouse_wheel
        self.nav = nav
        self.confirm = confirm
        self._actions = set(actions)

    def pressed(self, action: str) -> bool:
        return action in self._actions


def _slots(scene) -> list:
    return [it.get("slot") for it in scene._items]


def _hit_rect_for(scene, slot: str):
    """Draw a frame, then return the action hit rect for ``slot`` (or None)."""
    scene.draw(_surface())
    for rect, item in scene._row_rects:
        if item.get("slot") == slot:
            return rect
    return None


def _delete_rect_for(scene, slot: str):
    """Draw a frame, then return the delete hit rect for ``slot``."""
    scene.draw(_surface(scene.ctx.screen_size))
    for rect, item in scene._delete_rects:
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


@pytest.mark.parametrize("mode", ["save", "load"])
def test_every_existing_slot_has_an_explicit_delete_affordance(
    tmp_path,
    mode,
):
    _plant_slots(tmp_path)
    scene = _open(_make_ctx(tmp_path), mode=mode)
    scene.draw(_surface())

    delete_slots = {
        item.get("slot")
        for _rect, item in scene._delete_rects
    }
    existing_slots = {
        item.get("slot")
        for item in scene._items
        if item.get("slot")
    }
    assert delete_slots == existing_slots
    assert all(rect.height >= 44 for rect, _item in scene._delete_rects)
    # The synthetic new-save row is the one list entry that must not expose a
    # destructive action.
    if mode == "save":
        new_region = next(
            region for region in scene._card_regions
            if region["slot"] is None
        )
        assert new_region["delete"] is None


def test_autosave_delete_stays_available_beside_readonly_primary(tmp_path):
    _plant_slots(tmp_path)
    scene = _open(_make_ctx(tmp_path), mode="save")

    assert _hit_rect_for(scene, "autosave_1") is None
    delete_rect = _delete_rect_for(scene, "autosave_1")
    assert delete_rect is not None
    scene.update(
        0.0,
        _Input(mouse_clicked=True, mouse_pos=delete_rect.center),
    )
    assert scene.describe()["confirmation"] == {
        "open": True,
        "slot": "autosave_1",
        "action": "delete",
        "default": "cancel",
    }
    assert scene._confirm_buttons[0].style == "danger"
    assert scene._confirm_focus == 1


# ---------------------------------------------------------------------------
# Hit-testing inside the ScrollArea
# ---------------------------------------------------------------------------

def test_click_on_action_requests_correct_slot(tmp_path, monkeypatch):
    _plant_slots(tmp_path)
    scene = _open(_make_ctx(tmp_path), mode="load")
    fired: list = []
    monkeypatch.setattr(scene, "_request_action",
                        lambda item: fired.append(item.get("slot")))
    rect = _hit_rect_for(scene, "slot_bbb")
    assert rect is not None
    scene.update(0.0, _Input(mouse_clicked=True, mouse_pos=rect.center))
    assert fired == ["slot_bbb"]


def test_click_outside_any_action_is_inert(tmp_path, monkeypatch):
    _plant_slots(tmp_path)
    scene = _open(_make_ctx(tmp_path), mode="load")
    fired: list = []
    monkeypatch.setattr(scene, "_request_action",
                        lambda item: fired.append(item.get("slot")))
    scene.draw(_surface())
    # (5, 5) is the panel header — well clear of any card action chip.
    scene.update(0.0, _Input(mouse_clicked=True, mouse_pos=(5, 5)))
    assert fired == []


def test_mouse_delete_uses_independent_cancel_first_guard(tmp_path):
    from world_gal_game.core.save_manager import SaveManager

    _plant_slots(tmp_path)
    scene = _open(_make_ctx(tmp_path), mode="load")
    target = "slot_bbb"
    delete_rect = _delete_rect_for(scene, target)
    assert delete_rect is not None

    scene.update(
        0.0,
        _Input(mouse_clicked=True, mouse_pos=delete_rect.center),
    )
    assert scene._pending_action == "delete"
    assert scene._confirm_focus == 1
    assert SaveManager(tmp_path).load(target)["_label"] == "Manual B"

    # Clicking the default Cancel button is a true no-op and returns row
    # sub-focus to Primary, so a later plain Enter cannot delete.
    cancel_rect = scene._confirm_buttons[1].rect
    scene.update(
        0.0,
        _Input(mouse_clicked=True, mouse_pos=cancel_rect.center),
    )
    assert scene._pending_item is None
    assert scene.describe()["focus"]["action"] == "primary"
    assert SaveManager(tmp_path).load(target)["_label"] == "Manual B"

    # Reopen with the mouse and click the distinct danger confirmation.
    delete_rect = _delete_rect_for(scene, target)
    scene.update(
        0.0,
        _Input(mouse_clicked=True, mouse_pos=delete_rect.center),
    )
    confirm_rect = scene._confirm_buttons[0].rect
    scene.update(
        0.0,
        _Input(mouse_clicked=True, mouse_pos=confirm_rect.center),
    )

    assert target not in _slots(scene)
    assert not (tmp_path / f"{target}.json").exists()
    assert scene._status_kind == "success"
    assert "已刪除" in scene._status_msg


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


# ---------------------------------------------------------------------------
# Responsive measured layout / minimum targets
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "screen_size",
    [(640, 360), (960, 540), (1280, 720), (1920, 1080)],
)
def test_layout_uses_measured_lines_without_card_overlap(
    tmp_path,
    screen_size,
):
    _plant_slots(tmp_path)
    ctx = _make_ctx(tmp_path, screen_size=screen_size)
    scene = _open(ctx, mode="save")
    scene.draw(_surface(screen_size))

    # P0 input target floor: card rows, row actions, and close all remain at
    # least 44 physical canvas pixels even on the compact 540p canvas.
    assert scene._card_h >= 44
    assert scene._action_h >= 44
    assert scene.close_btn.rect.height >= 44
    assert scene._scroll.rect.height >= 44

    # Header, list and persistent feedback band are disjoint.
    title_font = ctx.fonts.get(scene._title_font_size, bold=True)
    title_rect = pygame.Rect(
        scene._title_pos,
        title_font.size("存檔"),
    )
    assert not title_rect.colliderect(scene.close_btn.rect)
    assert scene._scroll.rect.bottom <= scene._status_rect.top

    # Regions record the actual rendered glyph bounds after measured
    # ellipsizing. Each text line stays vertically ordered and every text
    # column stops before the action control.
    assert scene._card_regions
    for region in scene._card_regions:
        card = region["card"]
        label = region["label"]
        summary = region["summary"]
        timestamp = region["timestamp"]
        tag = region["tag"]
        action = region["action"]
        delete = region["delete"]
        assert card.contains(action)
        assert label.bottom <= summary.y
        assert summary.right <= action.x
        if timestamp is not None:
            assert summary.bottom <= timestamp.y
            assert timestamp.right <= action.x
        if tag is not None:
            assert label.right <= tag.x
            assert tag.right <= action.x
        if region["slot"] is None:
            assert delete is None
        else:
            assert delete is not None
            assert card.contains(delete)
            assert action.right <= delete.x
            assert action.height >= 44
            assert delete.height >= 44


def test_reflow_resizes_thumbnail_and_preserves_minimum_targets(tmp_path):
    from world_gal_game.core.save_manager import SaveManager

    sm = SaveManager(tmp_path)
    thumb = pygame.Surface((320, 180))
    thumb.fill((20, 40, 60))
    sm.save("slot_art", _minimal_state(), label="Art", thumbnail=thumb)
    scene = _open(_make_ctx(tmp_path), mode="load")
    row = scene._items[0]
    first = scene._load_thumbnail(row)
    assert first is not None and first.get_size() == (120, 68)

    scene.draw(_surface((1920, 1080)))
    second = scene._load_thumbnail(row)
    assert second is not None and second.get_size() == (180, 102)
    assert scene.close_btn.rect.height >= 44
    assert scene._action_h >= 44


# ---------------------------------------------------------------------------
# Keyboard/controller focus and automatic scrolling
# ---------------------------------------------------------------------------

def test_keyboard_focus_skips_readonly_and_enter_opens_confirmation(tmp_path):
    _plant_slots(tmp_path)
    scene = _open(_make_ctx(tmp_path), mode="save")
    autosave_index = next(
        i for i, row in enumerate(scene._items)
        if row.get("slot") == "autosave_1"
    )
    actionable = scene._actionable_indices()
    assert autosave_index not in actionable

    # Up from an unfocused list lands on the last actionable row and scrolls
    # it fully into view.
    scene.update(0.0, _Input(nav=-1))
    assert scene._focused_index == actionable[-1]
    top = scene._focused_index * (scene._card_h + scene._card_gap)
    bottom = top + scene._card_h
    assert top >= scene._scroll.scroll_y
    assert bottom <= scene._scroll.scroll_y + scene._scroll.rect.height

    # Enter follows the same guarded action path as a pointer click.
    scene.update(0.0, _Input(confirm=True))
    assert scene._pending_item is not None
    assert scene.describe()["confirmation"]["open"] is True
    scene.draw(_surface())  # focus ring + modal both render without error


def test_confirmation_escape_cancels_then_escape_closes(tmp_path):
    _plant_slots(tmp_path)
    closed = {"n": 0}
    scene = _open(
        _make_ctx(tmp_path),
        mode="load",
        on_close=lambda: closed.__setitem__("n", closed["n"] + 1),
    )
    scene._request_action(scene._items[0])
    assert scene._pending_item is not None

    scene.update(0.0, _Input(cancel=True))
    assert scene._pending_item is None
    assert closed["n"] == 0
    assert "未變更" in scene._status_msg

    scene.update(0.0, _Input(cancel=True))
    assert closed["n"] == 1


def test_keyboard_left_right_requires_deliberate_delete_selection(tmp_path):
    from world_gal_game.core.save_manager import SaveManager
    from world_gal_game.ui.input_bindings import InputAction

    SaveManager(tmp_path).save(
        "slot_keyboard",
        _minimal_state(),
        label="Keyboard",
    )
    scene = _open(_make_ctx(tmp_path), mode="load")

    # Initial Enter owns the primary Load action. It may open Load confirmation
    # but must never select Delete implicitly.
    assert scene.describe()["focus"]["action"] == "primary"
    scene.update(0.0, _Input(confirm=True))
    assert scene._pending_action == "primary"
    assert (tmp_path / "slot_keyboard.json").exists()
    scene.update(0.0, _Input(cancel=True))

    scene.update(0.0, _Input(actions={InputAction.NAV_RIGHT}))
    assert scene.describe()["focus"]["action"] == "delete"
    scene.update(0.0, _Input(confirm=True))
    assert scene._pending_action == "delete"
    assert scene._confirm_focus == 1

    # Plain Enter at the guard activates its safe default Cancel.
    scene.update(0.0, _Input(confirm=True))
    assert scene._pending_item is None
    assert scene.describe()["focus"]["action"] == "primary"
    assert (tmp_path / "slot_keyboard.json").exists()

    # A second deliberate Right + Enter, then Left inside the dialog, commits.
    scene.update(0.0, _Input(actions={InputAction.NAV_RIGHT}))
    scene.update(0.0, _Input(confirm=True))
    scene.update(0.0, _Input(actions={InputAction.NAV_LEFT}))
    scene.update(0.0, _Input(confirm=True))
    assert not (tmp_path / "slot_keyboard.json").exists()


def test_gamepad_dpad_and_a_cover_delete_without_unsafe_default(tmp_path):
    from world_gal_game.core.save_manager import SaveManager
    from world_gal_game.ui.input import InputState

    SaveManager(tmp_path).save("slot_gamepad", _minimal_state())
    scene = _open(_make_ctx(tmp_path), mode="load")

    def _hat(x: int, y: int) -> InputState:
        event = pygame.event.Event(
            pygame.JOYHATMOTION,
            {"joy": 0, "instance_id": 0, "hat": 0, "value": (x, y)},
        )
        state = InputState.collect([event])
        state.mouse_pos = (-1000, -1000)
        return state

    def _a() -> InputState:
        event = pygame.event.Event(
            pygame.JOYBUTTONDOWN,
            {"joy": 0, "instance_id": 0, "button": 0},
        )
        state = InputState.collect([event])
        state.mouse_pos = (-1000, -1000)
        return state

    scene.update(0.0, _hat(1, 0))
    assert scene.describe()["focus"]["action"] == "delete"
    scene.update(0.0, _a())
    assert scene._pending_action == "delete"
    assert scene._confirm_focus == 1

    # Controller A first cancels. The slot remains and focus resets safely.
    scene.update(0.0, _a())
    assert (tmp_path / "slot_gamepad.json").exists()
    assert scene.describe()["focus"]["action"] == "primary"

    scene.update(0.0, _hat(1, 0))
    scene.update(0.0, _a())
    scene.update(0.0, _hat(-1, 0))
    scene.update(0.0, _a())
    assert not (tmp_path / "slot_gamepad.json").exists()


def test_vertical_navigation_always_resets_to_primary_action(tmp_path):
    from world_gal_game.ui.input_bindings import InputAction

    _plant_slots(tmp_path)
    scene = _open(_make_ctx(tmp_path), mode="load")
    original_index = scene._focused_index
    scene.update(0.0, _Input(actions={InputAction.NAV_RIGHT}))
    assert scene._focused_action == "delete"

    scene.update(0.0, _Input(nav=1))

    assert scene._focused_index != original_index
    assert scene._focused_action == "primary"


# ---------------------------------------------------------------------------
# Confirmation transaction semantics
# ---------------------------------------------------------------------------

def test_overwrite_cancel_is_noop_then_keyboard_confirm_replaces_slot(tmp_path):
    from world_gal_game.core.save_manager import SaveManager

    sm = SaveManager(tmp_path)
    sm.save(
        "slot_existing",
        {"player": {"name": "Old"}, "meta": {}},
        label="Existing",
    )
    path = tmp_path / "slot_existing.json"
    before = path.read_bytes()

    ctx = _make_ctx(tmp_path)
    ctx.state.player.name = "New"
    scene = _open(ctx, mode="save")
    row = next(
        item for item in scene._items
        if item.get("slot") == "slot_existing"
    )
    scene._request_action(row)
    assert scene._pending_item is row
    assert scene._confirm_buttons
    assert all(button.rect.height >= 44 for button in scene._confirm_buttons)

    # Escape dismisses the guard without even rewriting the JSON timestamp.
    scene.update(0.0, _Input(cancel=True))
    assert path.read_bytes() == before
    assert sm.load("slot_existing")["player"]["name"] == "Old"

    # Cancel is the default; one Up selects Confirm, then Enter commits.
    scene._request_action(row)
    assert scene._confirm_focus == 1
    scene.update(0.0, _Input(nav=-1))
    assert scene._confirm_focus == 0
    scene.update(0.0, _Input(confirm=True))
    assert sm.load("slot_existing")["player"]["name"] == "New"
    assert scene._status_kind == "success"
    assert "儲存完成" in scene._status_msg


def test_load_cancel_keeps_live_state_then_confirm_swaps_it(tmp_path):
    from world_gal_game.core.save_manager import SaveManager

    SaveManager(tmp_path).save(
        "slot_existing",
        {"player": {"name": "Loaded"}, "meta": {}},
        label="Existing",
    )
    ctx = _make_ctx(tmp_path)
    ctx.state.player.name = "Current"
    scene = _open(ctx, mode="load")
    row = scene._items[0]

    scene._request_action(row)
    scene.update(0.0, _Input(cancel=True))
    assert ctx.state.player.name == "Current"

    scene._request_action(row)
    scene.update(0.0, _Input(nav=-1))
    scene.update(0.0, _Input(confirm=True))
    assert ctx.state.player.name == "Loaded"
    assert scene._status_kind == "success"
    assert "載入完成" in scene._status_msg


@pytest.mark.parametrize("failure", ["false", "exception"])
def test_delete_failure_keeps_list_focus_scroll_and_thumbnail_cache(
    tmp_path,
    monkeypatch,
    failure,
):
    from world_gal_game.core.save_manager import SaveManager

    _plant_slots(tmp_path)
    scene = _open(_make_ctx(tmp_path), mode="load")
    target = "slot_aaa"
    index = next(
        i for i, item in enumerate(scene._items)
        if item.get("slot") == target
    )
    scene._focused_index = index
    scene._focused_action = "delete"
    scene._scroll.scroll_y = min(25, scene._max_scroll())
    scroll_before = scene._scroll.scroll_y
    cache_value = pygame.Surface((3, 3))
    scene._thumb_cache[target] = cache_value
    slots_before = _slots(scene)

    if failure == "false":
        monkeypatch.setattr(scene.sm, "delete", lambda _slot: False)
    else:
        def _raise(_slot):
            raise OSError("disk is busy")

        monkeypatch.setattr(scene.sm, "delete", _raise)

    scene._delete_item(scene._items[index])

    assert _slots(scene) == slots_before
    assert scene._focused_index == index
    assert scene._focused_action == "delete"
    assert scene._scroll.scroll_y == scroll_before
    assert scene._thumb_cache[target] is cache_value
    assert scene._status_kind == "error"
    assert "刪除失敗" in scene._status_msg
    assert SaveManager(tmp_path).load(target)


def test_successful_delete_selects_neighbour_clamps_scroll_and_clears_cache(
    tmp_path,
):
    from world_gal_game.core.save_manager import SaveManager

    sm = SaveManager(tmp_path)
    for i in range(14):
        sm.save(f"slot_{i:02d}", _minimal_state(), label=f"S{i}")
    scene = _open(_make_ctx(tmp_path), mode="load")
    target = "slot_06"
    old_index = next(
        index for index, item in enumerate(scene._items)
        if item.get("slot") == target
    )
    old_slots = _slots(scene)
    scene._focused_index = old_index
    scene._focused_action = "delete"
    scene._thumb_cache[target] = pygame.Surface((3, 3))
    scene._scroll.scroll_y = scene._max_scroll()

    scene._delete_item(scene._items[old_index])

    expected_slots = [slot for slot in old_slots if slot != target]
    expected_index = min(old_index, len(expected_slots) - 1)
    assert _slots(scene) == expected_slots
    assert scene._focused_index == expected_index
    assert scene._items[expected_index]["slot"] in {
        old_slots[old_index - 1],
        old_slots[old_index + 1],
    }
    assert scene._focused_action == "primary"
    assert 0 <= scene._scroll.scroll_y <= scene._max_scroll()
    assert target not in scene._thumb_cache
    assert scene._status_kind == "success"
    assert scene._status_msg == "已刪除：S6"


def test_formal_load_unions_clear_data_without_reviving_removed_ending(
    tmp_path,
):
    """The SaveScene load path shares the app's global-completion contract."""
    from world_gal_game.core.clear_data import ClearData
    from world_gal_game.core.endings import Ending
    from world_gal_game.core.game_state import GameState
    from world_gal_game.core.media_authority import build_media_authority
    from world_gal_game.core.save_manager import SaveManager

    ctx = _make_ctx(tmp_path)
    ctx.state.endings.register(Ending(
        id="ending_current",
        title="Current",
        hidden=True,
    ))
    ctx.state.endings.register(Ending(
        id="ending_save",
        title="Save",
    ))
    ctx.state.meta["__pack_ending_ids__"] = frozenset({
        "ending_current",
        "ending_save",
    })
    clear_data = ClearData(
        scenes_seen={"global_scene"},
        endings_seen={"ending_current", "ending_removed_from_pack"},
        cgs_seen={"assets/cgs/global.png"},
    )
    ctx.state.meta["__clear_data__"] = clear_data
    ctx.state.meta["__pack_media_authority__"] = build_media_authority(
        ctx.state.story,
        additional_cg_ids={
            "assets/cgs/save-only.png",
            "assets/cgs/global.png",
        },
    )

    old_save = GameState()
    # This save predates ending_current. Formal load must rebind the current
    # pack catalog before clear data backfills that newly-authored ending.
    for ending in (
        Ending(id="ending_save", title="Save"),
        Ending(id="ending_retired_in_save", title="Retired"),
    ):
        old_save.endings.register(ending)
    old_save.read_log.mark_scene_done("save_scene")
    old_save.cg_gallery.unlock("assets/cgs/save-only.png")
    old_save.endings.unlocked.update({
        "ending_save": "2026-01-01T00:00:00",
        "ending_retired_in_save": "2025-01-01T00:00:00",
    })
    old_save.endings.seen.update({
        "ending_save",
        "ending_retired_in_save",
    })
    SaveManager(tmp_path).save(
        "slot_old",
        old_save.model_dump(mode="json"),
        label="Old save",
    )

    scene = _open(ctx, mode="load")
    row = next(
        item for item in scene._items
        if item.get("slot") == "slot_old"
    )
    scene._on_action(row)

    assert ctx.state.read_log.scenes == {"save_scene", "global_scene"}
    assert ctx.state.cg_gallery.is_unlocked("assets/cgs/save-only.png")
    assert ctx.state.cg_gallery.is_unlocked("assets/cgs/global.png")
    assert ctx.state.endings.is_unlocked("ending_save")
    # Existing save progress is a union, even if it came from older content.
    assert ctx.state.endings.is_unlocked("ending_retired_in_save")
    # Clear data only backfills ids that the currently installed pack exposes.
    assert ctx.state.endings.is_unlocked("ending_current")
    assert not ctx.state.endings.is_unlocked("ending_removed_from_pack")
    assert "ending_current" in {
        ending.id for ending in ctx.state.endings.visible_to_player()
    }
    assert "ending_current" not in {
        ending.id for ending in ctx.state.endings.newly_unlocked()
    }


def test_manual_load_rebinds_authored_story_and_choice_resume(tmp_path):
    from world_gal_game.core.game_state import GameState
    from world_gal_game.core.save_manager import SaveManager
    from world_gal_game.core.story_graph import (
        Choice,
        ChoiceCardSpec,
        Condition,
        Effect,
        Line,
        Scene,
        StoryGraph,
    )
    from world_gal_game.dialogue.dialogue_engine import (
        ChoiceOption,
        ScenePresentation,
        presentation_to_dict,
    )

    scene_id = "manual_load_choice_card_rebind_probe"
    choice_id = "keep_manual_save_logic"
    saved_choice = Choice(
        id=choice_id,
        text="手動存檔的功能文字",
        requires=[Condition(kind="flag", target="manual_saved_gate")],
        forbids=[Condition(kind="flag", target="manual_saved_block")],
        effects=[
            Effect(
                kind="set_flag",
                target="manual_saved_effect",
                value=True,
            ),
        ],
        next_scene="manual_saved_destination",
        hidden_if_locked=True,
        card=ChoiceCardSpec(title="手動存檔中的舊卡片"),
    )
    saved_state = GameState()
    saved_state.player.name = "手動存檔玩家"
    saved_state.events.set_flag("manual_saved_gate", True)
    saved_state.dialogue_history.lines = [{
        "speaker": "竄改者",
        "text": "這個路徑不可進入音訊後端",
        "voice": "/tmp/forged-history-voice.ogg",
    }]
    saved_state.story = StoryGraph(
        scenes={
            scene_id: Scene(
                id=scene_id,
                lines=[Line(text="手動存檔的選擇前一行。")],
                choices=[saved_choice],
            ),
        },
        current_scene=scene_id,
        current_line_index=1,
    )

    old_resume_card = ChoiceCardSpec(title="手動存檔中的舊卡片")
    saved_resume = {
        "version": 1,
        "screen": "dialogue",
        "scene_id": scene_id,
        "story_scene": scene_id,
        "story_line_index": 1,
        "presentation": presentation_to_dict(ScenePresentation(
            kind="choice",
            scene_id=scene_id,
            choices=[
                ChoiceOption(
                    id=choice_id,
                    text="玩家當時實際看到的選項文字",
                    enabled=False,
                    reason="玩家當時實際看到的鎖定理由",
                    card=old_resume_card,
                ),
            ],
        )),
        "last_line": None,
    }
    SaveManager(tmp_path).save(
        "slot_choice_card_old",
        saved_state.model_dump(mode="json"),
        label="Old choice-card save",
        resume_context=saved_resume,
    )

    current_card = ChoiceCardSpec(
        title="{player_name}目前版本的手動載入卡片",
        subtitle="只更新卡片，不更新遊戲語意",
        action="查看新版卡片",
    )
    current_choice = Choice(
        id=choice_id,
        text="新版 authored 功能文字",
        requires=[
            Condition(kind="flag", target="manual_current_gate"),
        ],
        effects=[
            Effect(
                kind="set_flag",
                target="manual_current_effect",
                value=True,
            ),
        ],
        next_scene="manual_current_destination",
        card=current_card,
    )
    authored_story = StoryGraph(scenes={
        scene_id: Scene(
            id=scene_id,
            lines=[Line(text="新版 authored line 取代存檔定義。")],
            choices=[current_choice],
        ),
    })
    ctx = _make_ctx(tmp_path)
    ctx.state.story = authored_story.model_copy(deep=True)
    ctx.state.meta["__pack_story_catalog__"] = authored_story

    scene = _open(ctx, mode="load")
    row = next(
        item for item in scene._items
        if item.get("slot") == "slot_choice_card_old"
    )
    scene._on_action(row)

    loaded_choice = ctx.state.story.get(scene_id).choices[0]
    assert loaded_choice == current_choice
    assert ctx.state.story.get(scene_id).lines[0].text == \
        "新版 authored line 取代存檔定義。"
    assert ctx.state.dialogue_history.lines[0]["voice"] is None

    loaded_resume = ctx.state.meta["__loaded_resume__"]
    loaded_row = loaded_resume["presentation"]["choices"][0]
    saved_row = saved_resume["presentation"]["choices"][0]
    assert loaded_row["text"] == saved_row["text"]
    assert loaded_row["enabled"] == saved_row["enabled"]
    assert loaded_row["reason"] == saved_row["reason"]
    assert loaded_row["card"]["title"] == \
        "手動存檔玩家目前版本的手動載入卡片"
    assert loaded_row["card"]["subtitle"] == "只更新卡片，不更新遊戲語意"


@pytest.mark.parametrize(
    ("key", "forged"),
    [
        ("current_bgm", "assets/bgm/manual-save-only-carry.ogg"),
        ("current_ambient", "assets/sfx/manual-save-only-carry.ogg"),
    ],
)
def test_manual_load_legacy_save_drops_unowned_audio_carry(
    tmp_path,
    key,
    forged,
):
    from world_gal_game.core.game_state import GameState
    from world_gal_game.core.save_manager import SaveManager

    saved = GameState()
    saved.meta[key] = forged
    SaveManager(tmp_path).save(
        "slot_legacy_audio",
        saved.model_dump(mode="json"),
        label="Legacy audio save",
    )

    ctx = _make_ctx(tmp_path)
    scene = _open(ctx, mode="load")
    row = next(
        item for item in scene._items
        if item.get("slot") == "slot_legacy_audio"
    )
    scene._on_action(row)

    assert key not in ctx.state.meta
    assert scene._status_kind == "success"


def test_manual_load_rebinds_media_progress_and_filters_clear_data(
    tmp_path,
):
    from world_gal_game.core.clear_data import ClearData
    from world_gal_game.core.game_state import GameState
    from world_gal_game.core.media_authority import build_media_authority
    from world_gal_game.core.save_manager import SaveManager

    allowed_cg = "assets/cgs/manual-current.png"
    allowed_music = "assets/bgm/manual-current.ogg"
    saved = GameState()
    saved.cg_gallery.unlocked.update({
        allowed_cg,
        "/tmp/manual-save-forged.png",
        "assets/cgs/../../outside.png",
    })
    saved.music_room.unlocked.update({
        allowed_music,
        "/tmp/manual-save-forged.ogg",
        "../outside.ogg",
    })
    SaveManager(tmp_path).save(
        "slot_media_authority",
        saved.model_dump(mode="json"),
        label="Media authority save",
    )

    ctx = _make_ctx(tmp_path)
    authority = build_media_authority(
        ctx.state.story,
        additional_cg_ids={allowed_cg},
        additional_music_ids={allowed_music},
    )
    ctx.state.meta["__pack_media_authority__"] = authority
    ctx.state.meta["__clear_data__"] = ClearData(cgs_seen={
        allowed_cg,
        "/tmp/clear-data-forged.png",
    })

    scene = _open(ctx, mode="load")
    row = next(
        item for item in scene._items
        if item.get("slot") == "slot_media_authority"
    )
    scene._on_action(row)

    assert ctx.state.cg_gallery.unlocked == {allowed_cg}
    assert ctx.state.music_room.unlocked == {allowed_music}
    assert scene._status_kind == "success"


def test_manual_load_strips_save_only_replay_visual_effects(tmp_path):
    import json

    from world_gal_game.core.game_state import GameState
    from world_gal_game.core.save_manager import SaveManager
    from world_gal_game.core.story_graph import Line, Scene, StoryGraph
    from world_gal_game.dialogue.dialogue_engine import (
        DialogueEngine,
        REPLAY_FX_PAYLOAD_KEY,
    )

    scene_id = "manual_replay_fx_authority_probe"
    authored_story = StoryGraph(scenes={
        scene_id: Scene(
            id=scene_id,
            lines=[Line(text="目前版本沒有背景切換效果。")],
        ),
    })
    saved = GameState(story=authored_story.model_copy(deep=True))
    engine = DialogueEngine(saved)
    engine.start_scene(scene_id)
    engine.next_line()
    SaveManager(tmp_path).save(
        "slot_replay_fx",
        saved.model_dump(mode="json"),
        label="Replay FX save",
    )

    path = tmp_path / "slot_replay_fx.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["read_log"]["replay_archive"][scene_id]["presentations"][0][
        REPLAY_FX_PAYLOAD_KEY
    ] = [{
        "fx": "set_weather",
        "backend": "manual_save_only_backend",
        "params": {
            "asset_path": "assets/weather/manual-save-only.bin",
        },
        "fade": 0.0,
    }]
    path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    ctx = _make_ctx(tmp_path)
    ctx.state.story = authored_story.model_copy(deep=True)
    ctx.state.meta["__pack_story_catalog__"] = authored_story
    scene = _open(ctx, mode="load")
    row = next(
        item for item in scene._items
        if item.get("slot") == "slot_replay_fx"
    )
    scene._on_action(row)

    loaded = ctx.state.read_log.replay_entry(scene_id)
    assert loaded is not None
    assert REPLAY_FX_PAYLOAD_KEY not in loaded.presentations[0]
    assert scene._status_kind == "success"


def test_new_save_row_commits_without_confirmation(tmp_path):
    from world_gal_game.core.save_manager import SaveManager

    scene = _open(_make_ctx(tmp_path), mode="save")
    assert scene._items[0]["slot"] is None
    scene._request_action(scene._items[0])
    assert scene._pending_item is None
    assert SaveManager(tmp_path).list_saves()
    assert scene._status_kind == "success"


def test_mouse_can_confirm_occupied_load(tmp_path):
    _plant_slots(tmp_path)
    ctx = _make_ctx(tmp_path)
    ctx.state.player.name = "Current"
    scene = _open(ctx, mode="load")
    row = next(
        item for item in scene._items if item.get("slot") == "slot_aaa"
    )
    scene._request_action(row)
    confirm_rect = scene._confirm_buttons[0].rect
    scene.update(
        0.0,
        _Input(mouse_clicked=True, mouse_pos=confirm_rect.center),
    )
    assert scene._pending_item is None
    assert ctx.state.player.name == "TestPlayer"


# ---------------------------------------------------------------------------
# Persistent visible success / error feedback
# ---------------------------------------------------------------------------

def test_save_failure_becomes_visible_error_text(tmp_path, monkeypatch):
    scene = _open(_make_ctx(tmp_path), mode="save")

    def _fail(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(scene.sm, "save", _fail)
    scene._request_action(scene._items[0])
    assert scene._error_msg is not None
    assert scene._status_kind == "error"
    assert "disk full" in scene._status_msg

    surface = _surface()
    scene.draw(surface)
    # The fixed status band always paints a solid semantic marker, making
    # feedback visible independently of font anti-aliasing or colour alone.
    marker_x = scene._status_rect.x + 1
    marker_y = scene._status_rect.centery
    assert surface.get_at((marker_x, marker_y))[:3] == scene.ctx.theme.warn[:3]
