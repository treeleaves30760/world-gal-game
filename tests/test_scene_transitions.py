"""First-class scene transitions (Pillar 1 of the presentation layer).

Three layers are covered:

1. The :class:`SceneTransition` primitive (``ui/transitions.py``) — its clock,
   ``done``/``progress`` maths, graceful degradation, and a draw smoke over
   every style (mirrors ``test_camera_effects.py``).
2. The builtin effect handlers (``set_background`` / ``show_cg`` / ``hide_cg`` /
   ``transition``) — they must only enqueue a JSON-able directive onto the
   visual-fx queue and never touch the display (mirrors the camera effects).
3. Integration through a real ``DialogueScene`` (via ``GameDriver``) — a
   ``set_background`` effect mid-scene spawns the transition, takes over the
   background, and is not reverted by the next line's scene-level background.
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
from world_gal_game.ui.transitions import SceneTransition, SCENE_TRANSITION_STYLES


# ---------------------------------------------------------------------------
# SceneTransition primitive
# ---------------------------------------------------------------------------

def _surf(size=(64, 48), color=(10, 20, 30)):
    s = pygame.Surface(size)
    s.fill(color)
    return s


def test_cut_is_instant_and_done():
    st = SceneTransition(_surf(), style="cut", duration=0.6)
    assert st.duration == 0.0
    assert st.done is True
    assert st.progress == pytest.approx(1.0)


def test_dissolve_progress_clamps_to_duration():
    st = SceneTransition(_surf(), style="dissolve", duration=1.0)
    assert st.done is False
    st.update(0.5)
    assert st.progress == pytest.approx(0.5, abs=1e-3)
    st.update(5.0)                       # frame-rate spike past the end
    assert st.t == pytest.approx(1.0)
    assert st.done is True
    assert st.progress == pytest.approx(1.0)


def test_unknown_style_falls_back_to_dissolve():
    st = SceneTransition(_surf(), style="not-a-style", duration=0.4)
    assert st.style == "dissolve"


def test_mask_without_numpy_or_mask_degrades_to_dissolve():
    # No mask surface supplied → cannot image-dissolve → dissolve.
    st = SceneTransition(_surf(), style="mask", duration=0.4)
    assert st.style == "dissolve"


def test_none_old_frame_draws_nothing():
    st = SceneTransition(None, style="dissolve", duration=0.4)
    st.update(0.2)
    # Must not raise even though there is nothing to overlay.
    st.draw(_surf())


@pytest.mark.parametrize("style", SCENE_TRANSITION_STYLES)
def test_draw_smoke_every_style(style):
    pygame.display.init()
    old = _surf((200, 150), (10, 20, 30))
    target = _surf((200, 150), (200, 180, 160))   # the live "new" frame
    st = SceneTransition(old, style=style, duration=0.4, color=(0, 0, 0))
    st.update(0.2)
    st.draw(target)                       # mid-transition
    st.update(0.4)
    st.draw(target)                       # finished (most styles draw nothing)


def test_draw_rescales_mismatched_old_frame():
    pygame.display.init()
    old = _surf((100, 100))               # different size from the target
    target = _surf((200, 150))
    st = SceneTransition(old, style="dissolve", duration=0.4)
    st.update(0.2)
    st.draw(target)                       # must scale ``old`` to fit, not raise


# ---------------------------------------------------------------------------
# Builtin effect handlers — enqueue a directive, never touch the display
# ---------------------------------------------------------------------------

def _queued(state: GameState) -> list[dict]:
    return state.meta.get(VISUAL_FX_QUEUE, [])


def test_set_background_enqueues_directive():
    s = GameState()
    out = s.apply(Effect(kind="set_background", target="bg/office.png",
                         value={"style": "wipe_left", "duration": 0.5}))
    assert out["path"] == "bg/office.png"
    assert out["transition"] == "wipe_left"
    q = _queued(s)
    assert len(q) == 1
    d = q[0]
    assert d["fx"] == "set_background"
    assert d["path"] == "bg/office.png"
    assert d["transition"]["style"] == "wipe_left"
    assert d["transition"]["duration"] == pytest.approx(0.5)


def test_show_and_hide_cg_enqueue_directives():
    s = GameState()
    s.apply(Effect(kind="show_cg", target="cg/kiss.png",
                   value={"style": "fade", "color": [255, 255, 255]}))
    s.apply(Effect(kind="hide_cg", value={"style": "dissolve"}))
    q = _queued(s)
    assert [d["fx"] for d in q] == ["show_cg", "hide_cg"]
    assert q[0]["path"] == "cg/kiss.png"
    assert q[0]["transition"]["color"] == [255, 255, 255]


def test_transition_beat_enqueues_directive_without_target():
    s = GameState()
    s.apply(Effect(kind="transition", value={"style": "fade", "duration": 1.0}))
    q = _queued(s)
    assert len(q) == 1
    assert q[0]["fx"] == "transition"
    assert q[0]["transition"]["style"] == "fade"


def test_transition_directive_is_json_able():
    import json
    s = GameState()
    s.apply(Effect(kind="set_background", target="bg/x.png",
                   value={"style": "iris_out", "mask": "masks/star.png"}))
    # The whole queue must serialise (directives are persisted/transcribed).
    json.dumps(_queued(s))


def test_bad_value_degrades_to_dissolve_directive():
    s = GameState()
    s.apply(Effect(kind="set_background", target="bg/x.png", value="garbage"))
    assert _queued(s)[0]["transition"]["style"] == "dissolve"


# ---------------------------------------------------------------------------
# JSON-Schema export — agents validate transition args offline
# ---------------------------------------------------------------------------

def test_capability_manifest_exports_transition_schema():
    from world_gal_game.dev.capability_manifest import build_manifest
    m = build_manifest()
    by_kind = {e["kind"]: e for e in m["effects"]}
    for kind in ("set_background", "show_cg", "hide_cg", "transition"):
        assert kind in by_kind, kind
        assert "args_schema" in by_kind[kind]
    # The shared TransitionValue structure is reachable from the schema bundle.
    schema = by_kind["set_background"]["args_schema"]
    assert "$defs" in schema and "TransitionValue" in schema["$defs"]
    tv = schema["$defs"]["TransitionValue"]["properties"]
    assert {"style", "duration", "easing", "color", "mask"} <= set(tv)


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


def _open_dialogue(driver, scene: Scene):
    app = driver.app
    app.state.story.add_scene(scene)
    app._start_dialogue(scene.id)
    app.manager.commit_pending()
    driver.advance_frames(1)
    ds = app.manager.current
    assert type(ds).__name__ == "DialogueScene"
    if ds.box and not ds.box.fully_revealed():
        ds.box.force_reveal()
    return ds


def _advance(ds, driver):
    if ds.box and not ds.box.fully_revealed():
        ds.box.force_reveal()
    ds._advance()
    driver.advance_frames(1)   # let update() consume the queue + draw()
    if ds.box and not ds.box.fully_revealed():
        ds.box.force_reveal()


def test_set_background_effect_spawns_transition_and_takes_over(driver):
    sc = Scene(
        id="probe_transition",
        background="backgrounds/home.png",
        lines=[
            Line(text="before"),
            Line(text="after", effects=[Effect(
                kind="set_background", target="backgrounds/school.png",
                value={"style": "wipe_left", "duration": 0.5})]),
            Line(text="still school"),
        ],
    )
    ds = _open_dialogue(driver, sc)
    assert ds._current_line.text == "before"
    assert ds._bg_overridden is False

    _advance(ds, driver)                      # line "after" fires set_background
    assert ds._current_line.text == "after"
    assert ds.background_path == "backgrounds/school.png"
    assert ds._bg_overridden is True
    assert ds._scene_transition is not None   # transition in flight

    # The next line's scene-level background must NOT revert the override.
    _advance(ds, driver)
    assert ds._current_line.text == "still school"
    assert ds.background_path == "backgrounds/school.png"
    assert ds._bg_overridden is True


def test_show_cg_effect_overrides_per_line_cg(driver):
    sc = Scene(
        id="probe_cg",
        lines=[
            Line(text="open", effects=[Effect(
                kind="show_cg", target="cg/special.png",
                value={"style": "dissolve"})]),
            Line(text="next has no cg field"),     # must not clear the CG
        ],
    )
    ds = _open_dialogue(driver, sc)
    driver.advance_frames(1)
    assert ds.cg_surface_path == "cg/special.png"
    assert ds._cg_overridden is True

    _advance(ds, driver)
    assert ds._current_line.text == "next has no cg field"
    assert ds.cg_surface_path == "cg/special.png"   # not reverted to None


def test_describe_reports_transition_and_layers(driver):
    sc = Scene(id="probe_describe", background="backgrounds/home.png",
               lines=[Line(text="hi")])
    ds = _open_dialogue(driver, sc)
    info = ds.describe()
    assert "background" in info and "cg" in info
    assert "transition" in info["fx_active"]
