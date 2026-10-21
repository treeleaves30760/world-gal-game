"""Runtime seams governed by the live reduced-motion policy."""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from world_gal_game.scenes.base import SceneManager
from world_gal_game.scenes.dialogue_scene import DialogueScene
from world_gal_game.scenes.movie_scene import MoviePlayerScene
from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.input import InputState
from world_gal_game.ui.motion import MotionPolicy
from world_gal_game.ui.theme import default_theme
from world_gal_game.ui.transitions import SceneTransition
from world_gal_game.ui.widgets.button import Button
from world_gal_game.ui.widgets.dialogue_box import DialogueBox
from world_gal_game.ui.widgets.map_view import MapView
from world_gal_game.ui.widgets.portrait_view import PortraitView
from world_gal_game.ui.widgets.rich_text_view import RichText
from world_gal_game.ui.widgets.text_input import TextInput


@pytest.fixture(scope="module", autouse=True)
def _pygame() -> None:
    pygame.display.init()
    pygame.font.init()
    yield
    pygame.quit()


def _fonts() -> FontRegistry:
    return FontRegistry(candidates=(), scale=1.0)


def test_scene_manager_uses_policy_for_new_and_active_transitions() -> None:
    config = SimpleNamespace(reduce_motion=True)
    policy = MotionPolicy.from_config(config)
    manager = SceneManager(motion=policy)
    manager._last_frame = pygame.Surface((32, 18))
    # A queued mutation with an empty stack is sufficient to exercise the
    # generic hand-off without pulling a concrete scene into this unit test.
    manager._pending.append(("pop", None, {}))
    manager.commit_pending()

    assert manager._transition is not None
    assert manager._transition.style == "dissolve"
    assert manager._transition.duration == pytest.approx(
        MotionPolicy.REDUCED_OPACITY_CAP,
    )

    config.reduce_motion = False
    manager._transition = SceneTransition(
        pygame.Surface((32, 18)),
        style="dissolve",
        duration=1.0,
    )
    manager._transition.update(0.25)
    config.reduce_motion = True
    manager.update(0.0, None)

    assert manager._transition is not None
    assert manager._transition.duration <= 0.37 + 1e-9


def test_button_feedback_snaps_after_live_toggle() -> None:
    config = SimpleNamespace(reduce_motion=False)
    policy = MotionPolicy.from_config(config)
    button = Button(
        pygame.Rect(0, 0, 180, 48),
        "前往",
        fonts=_fonts(),
        theme=default_theme(),
        motion=policy,
    )
    over = InputState(mouse_pos=(20, 20))

    button.update(1 / 120, over)
    assert 0.0 < button._hover_t < 1.0

    config.reduce_motion = True
    button.update(0.0, over)
    assert button._hover_t == 1.0


def test_map_feedback_is_finite_motion_safe_and_never_moves_hit_geometry() -> None:
    config = SimpleNamespace(reduce_motion=False)
    policy = MotionPolicy.from_config(config)
    view = MapView(
        pygame.Rect(0, 0, 640, 360),
        fonts=_fonts(),
        theme=default_theme(),
        motion=policy,
    )
    view.set_data(
        nodes=[{
            "id": "library",
            "name": "圖書館",
            "region": "campus",
            "region_name": "校本部",
            "description": "閱讀與故事地點",
            "map_x": 50,
            "map_y": 50,
            "exits": [],
            "visited": True,
            "accessible": True,
            "travel_available": True,
            "offer_status": "current",
            "marker_kind": "story",
            "marker_label": "現在可出發",
        }],
        current_id=None,
        reachable_ids={"library"},
    )
    hit_rect = view._node_rects["library"].copy()
    pointer = InputState(mouse_pos=hit_rect.center)

    view.update(1 / 120, pointer)
    assert 0.0 < view.interaction_progress("library") < 1.0
    assert view._node_rects["library"] == hit_rect

    for _ in range(120):
        view.update(1 / 120, pointer)
    assert view.interaction_progress("library") == pytest.approx(
        1.0,
        abs=1e-4,
    )
    assert view._node_rects["library"] == hit_rect

    config.reduce_motion = True
    view.update(0.0, InputState(mouse_pos=(-1, -1)))
    assert view.interaction_progress("library") == 0.0
    assert view._node_rects["library"] == hit_rect


def test_text_caret_becomes_steady_after_live_toggle() -> None:
    config = SimpleNamespace(reduce_motion=False)
    field = TextInput(
        pygame.Rect(0, 0, 220, 48),
        fonts=_fonts(),
        theme=default_theme(),
        motion=MotionPolicy.from_config(config),
    )
    field.focused = True
    field.cursor_t = 0.75
    assert field._caret_visible() is False

    config.reduce_motion = True
    assert field._caret_visible() is True


@pytest.mark.parametrize("effect", ["shake", "wave", "fadein"])
def test_rich_text_effects_settle_but_typewriter_content_continues(
    effect: str,
) -> None:
    config = SimpleNamespace(reduce_motion=False)
    policy = MotionPolicy.from_config(config)
    rich = RichText(
        pygame.Rect(0, 0, 400, 100),
        f"[fx={effect}]文字[/fx]",
        fonts=_fonts(),
        size=24,
        reveal_chars=0,
        text_speed=10.0,
        motion=policy,
    )
    rich.update(0.1)
    assert rich.reveal_chars == 1
    rich._ensure_layout()
    run = rich._rows[0][0]
    rich._reveal_time[0] = 0.0
    rich._clock = 0.05

    config.reduce_motion = True

    assert rich._glyph_offset_alpha(run, 0, 0) == (0, 0, 255)
    rich.update(0.1)
    assert rich.reveal_chars == 2


def test_dialogue_hint_stops_pulsing_without_disappearing() -> None:
    config = SimpleNamespace(reduce_motion=True)
    box = DialogueBox(
        pygame.Rect(20, 20, 760, 220),
        fonts=_fonts(),
        theme=default_theme(),
        text_speed=0.0,
        motion=MotionPolicy.from_config(config),
    )
    box.set_line("測試", "已經完整顯示的文字")
    box.force_reveal()

    box._hint_t = 0.0
    assert box._hint_alpha() == 200
    box._hint_t = 0.5
    assert box._hint_alpha() == 200

    config.reduce_motion = False
    assert box._hint_alpha() == 120


def test_live_toggle_settles_camera_and_clears_hazardous_fx() -> None:
    config = SimpleNamespace(reduce_motion=False)
    scene = DialogueScene(SimpleNamespace(config=config))
    scene._spawn_visual_fx({
        "fx": "camera_pan",
        "x": 72,
        "y": -18,
        "duration": 1.0,
    })
    scene._spawn_visual_fx({
        "fx": "screen_shake",
        "duration": 1.0,
    })
    scene._spawn_visual_fx({
        "fx": "screen_flash",
        "duration": 1.0,
    })
    scene._spawn_visual_fx({
        "fx": "screen_blur",
        "radius": 8.0,
        "duration": 1.0,
    })
    scene._camera.update(0.2)
    scene._advance_bg_blur(0.2)
    assert scene._shakes and scene._flashes and scene._bg_blur > 0.0

    config.reduce_motion = True
    scene._advance_visual_fx(0.0)

    assert scene._camera.done is True
    assert scene._camera.pan_x == pytest.approx(72)
    assert scene._camera.pan_y == pytest.approx(-18)
    assert scene._shakes == []
    assert scene._flashes == []
    assert scene._bg_blur == 0.0


def test_reduced_camera_reset_and_blur_clear_are_not_filtered() -> None:
    config = SimpleNamespace(reduce_motion=True)
    scene = DialogueScene(SimpleNamespace(config=config))
    scene._camera.pan_to(50, 20, duration=0.0)
    scene._camera.zoom_to(2.0, duration=0.0)
    scene._bg_blur = scene._bg_blur_target = 8.0

    scene._spawn_visual_fx({
        "fx": "camera_pan",
        "x": 0,
        "y": 0,
        "duration": 2.0,
    })
    scene._spawn_visual_fx({
        "fx": "camera_zoom",
        "scale": 1.0,
        "duration": 2.0,
    })
    scene._spawn_visual_fx({
        "fx": "screen_blur",
        "clear": True,
        "duration": 2.0,
    })

    assert scene._camera.is_neutral is True
    assert scene._bg_blur == 0.0


def test_ambient_backend_freezes_and_resumes_from_same_live_policy() -> None:
    class Ambient:
        alpha = 255

        def __init__(self) -> None:
            self.updates = 0

        def update(self, _dt: float) -> None:
            self.updates += 1

    config = SimpleNamespace(reduce_motion=True)
    scene = DialogueScene(SimpleNamespace(config=config))
    ambient = Ambient()
    scene._ambient = ambient

    scene._advance_ambient(0.5)
    assert ambient.updates == 0

    config.reduce_motion = False
    scene._advance_ambient(0.5)
    assert ambient.updates == 1


def test_live_toggle_caps_an_active_ambient_opacity_handoff() -> None:
    class Ambient:
        alpha = 0

        def update(self, _dt: float) -> None:
            pass

    config = SimpleNamespace(reduce_motion=False)
    scene = DialogueScene(SimpleNamespace(config=config))
    scene._ambient = Ambient()
    scene._ambient_base_alpha = 255
    scene._ambient_fade_dir = 1
    scene._ambient_fade_t = 0.25
    scene._ambient_fade_dur = 1.0

    config.reduce_motion = True
    scene._advance_ambient(0.0)
    assert scene._ambient_fade_dur == pytest.approx(
        0.25 + MotionPolicy.REDUCED_OPACITY_CAP,
    )

    scene._advance_ambient(MotionPolicy.REDUCED_OPACITY_CAP)
    assert scene._ambient_fade_dir == 0
    assert scene._ambient.alpha == 255


def test_portrait_backend_update_is_engine_forced_to_freeze() -> None:
    class Backend:
        def __init__(self) -> None:
            self.updates = 0

        def update(self, _dt: float, **_ctx) -> None:
            self.updates += 1

    config = SimpleNamespace(reduce_motion=True)
    ctx = SimpleNamespace(
        config=config,
        state=SimpleNamespace(meta={}),
    )
    scene = DialogueScene(ctx)
    backend = Backend()
    scene._slot_backends["left"] = backend

    scene.update(0.1, InputState())
    assert backend.updates == 0

    config.reduce_motion = False
    scene.update(0.1, InputState())
    assert backend.updates == 1


def test_legacy_portrait_keeps_short_opacity_but_removes_bounce() -> None:
    reduced = PortraitView(
        pygame.Rect(0, 0, 100, 200),
        None,
        motion=MotionPolicy(True),
    )
    reduced.show("portrait.png")
    reduced.update(MotionPolicy.REDUCED_OPACITY_CAP, None)

    assert reduced.alpha == 255
    assert reduced.motion.reduced is True


def test_reduced_motion_makes_unskippable_movie_escapable() -> None:
    class Player:
        def __init__(self) -> None:
            self.done = False

        def update(self, _dt: float) -> None:
            pass

        def skip(self) -> None:
            self.done = True

    finished: list[bool] = []
    ctx = SimpleNamespace(motion=MotionPolicy(True))
    scene = MoviePlayerScene(ctx)
    scene._player = Player()
    scene._skippable = False
    scene._ended = False
    scene._on_done = lambda: finished.append(True)

    scene.update(
        0.0,
        InputState(cancel=True),
    )

    assert scene._ended is True
    assert finished == [True]


def test_app_scene_manager_and_toasts_share_one_live_policy() -> None:
    from world_gal_game.dev.driver import GameDriver

    driver = GameDriver(pack="demo_pack")
    try:
        app = driver.app
        assert app.manager.motion is app.ctx.motion
        assert app.toast_stack.motion is app.ctx.motion
        plugin_manager = app.state.meta["__plugin_manager__"]
        assert plugin_manager._ctx.config is app.config
        assert plugin_manager._ctx.motion is app.ctx.motion

        app.config.reduce_motion = True
        assert app.manager.motion.reduced is True
        assert app.toast_stack.reduce_motion is True
    finally:
        driver.quit()
