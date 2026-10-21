"""Commercial save/load guarantees for reconstructible visual tweens."""
from __future__ import annotations

import copy
import json
import os
import types

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from world_gal_game.core.story_graph import Effect, Line, Scene
from world_gal_game.ui.camera import Camera, ColorTint


STARTING_ROOM = "assets/backgrounds/starting_room.png"
PARK = "assets/backgrounds/park.png"


@pytest.fixture
def driver(tmp_path):
    from world_gal_game.dev.driver import GameDriver

    drv = GameDriver(pack="demo_pack")
    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    drv.app.config.save_dir = types.MethodType(
        lambda self, _path=save_dir: _path,
        drv.app.config,
    )
    bridge = drv.app.state.meta.get("__autosave_config__")
    if isinstance(bridge, dict):
        bridge["save_dir"] = save_dir
    drv.app.config.autosave_enabled = False
    drv._visual_resume_save_dir = save_dir
    yield drv
    drv.quit()


def _open(driver, scene: Scene):
    app = driver.app
    app.state.story.add_scene(scene)
    # These tests drive renderer primitives directly. Mirror the equivalent
    # pack-authored effects only in the trusted catalogue so resume provenance
    # sees the same authority without firing the effects during scene setup.
    authored = scene.model_copy(deep=True)
    authored.on_end.extend([
        Effect(kind="set_background", target=PARK),
        Effect(
            kind="set_weather",
            target="rain",
            value={
                "count": 20,
                "seed": 7,
                "alpha": 200,
                "fade": 4.0,
            },
        ),
        Effect(
            kind="set_weather",
            target="rain",
            value={
                "count": 20,
                "seed": 7,
                "alpha": 200,
                "fade": 0.0,
            },
        ),
    ])
    app._authored_story_catalog.add_scene(authored)
    app._start_dialogue(scene.id)
    app.manager.commit_pending()
    driver.advance_frames(1)
    dialogue = app.manager.current
    assert type(dialogue).__name__ == "DialogueScene"
    return dialogue


def _start_camera(dialogue) -> None:
    camera = Camera()
    camera.pan_to(80.0, -36.0, duration=4.0, easing="in_out_cubic")
    camera.zoom_to(1.6, duration=4.0, easing="in_out_cubic")
    camera.update(1.0)
    dialogue._camera = camera


def _start_background_tint_and_blur(dialogue) -> None:
    dialogue._update_background(PARK)
    assert dialogue._bg_fade is not None
    dialogue._bg_fade.update(0.2)

    tint = ColorTint(
        color=(24, 48, 96),
        duration=3.0,
        max_alpha=160,
        easing="out_cubic",
    )
    tint.update(0.75)
    dialogue._tint = tint

    dialogue._start_bg_blur(12.0, 4.0)
    dialogue._advance_bg_blur(1.25)


def _set_fading_weather(dialogue, *, clearing: bool) -> None:
    dialogue._apply_set_weather(
        {
            "fx": "set_weather",
            "backend": "rain",
            "params": {"count": 20, "seed": 7, "alpha": 200},
            "fade": 4.0 if not clearing else 0.0,
        }
    )
    assert dialogue._ambient is not None
    if clearing:
        dialogue._apply_clear_weather({"fx": "clear_weather", "fade": 4.0})
    dialogue._advance_ambient(1.0)


def _install_all_visuals(dialogue, *, clearing_weather: bool = False) -> None:
    _start_camera(dialogue)
    _start_background_tint_and_blur(dialogue)
    _set_fading_weather(dialogue, clearing=clearing_weather)


def test_mid_camera_resume_keeps_nonlinear_clock_and_does_not_replay_effects(
    driver,
):
    app = driver.app
    dialogue = _open(
        driver,
        Scene(
            id="visual_resume_camera",
            background=STARTING_ROOM,
            lines=[
                Line(
                    text="The effect belongs to the authored line.",
                    effects=[Effect(kind="advance_time", value=1)],
                )
            ],
        ),
    )
    _start_camera(dialogue)
    saved_time = app.state.time.model_dump(mode="json")
    saved_history = app.state.dialogue_history.model_dump(mode="json")
    saved_live = (
        dialogue._camera.zoom,
        dialogue._camera.pan_x,
        dialogue._camera.pan_y,
    )

    resume = dialogue.export_resume_context()
    assert resume is not None
    camera = resume["visual_timeline"]["camera"]
    assert camera == {
        "from": [1.0, 0.0, 0.0],
        "to": [1.6, 80.0, -36.0],
        "elapsed_seconds": 1.0,
        "duration_seconds": 4.0,
        "easing": "in_out_cubic",
    }
    # At one quarter of the clock this curve is intentionally far from the
    # linear interpolation. Saving must retain the authored easing, not merely
    # the live pixels.
    assert saved_live[0] == pytest.approx(1.0375)
    assert saved_live[0] != pytest.approx(1.15)

    dialogue._camera.update(3.0)
    assert dialogue._camera.done is True
    assert dialogue.restore_resume_context(copy.deepcopy(resume)) is True

    assert dialogue._camera.t == pytest.approx(1.0)
    assert dialogue._camera.duration == pytest.approx(4.0)
    assert dialogue._camera.easing_name == "in_out_cubic"
    assert (
        dialogue._camera.zoom,
        dialogue._camera.pan_x,
        dialogue._camera.pan_y,
    ) == pytest.approx(saved_live)
    dialogue._camera.update(3.0)
    assert dialogue._camera.done is True
    assert (
        dialogue._camera.zoom,
        dialogue._camera.pan_x,
        dialogue._camera.pan_y,
    ) == pytest.approx((1.6, 80.0, -36.0))

    assert app.state.time.model_dump(mode="json") == saved_time
    assert app.state.dialogue_history.model_dump(mode="json") == saved_history


def test_background_tint_and_blur_resume_their_original_targets(driver):
    dialogue = _open(
        driver,
        Scene(
            id="visual_resume_layers",
            background=STARTING_ROOM,
            lines=[Line(text="Keep every presentation target.")],
        ),
    )
    _start_background_tint_and_blur(dialogue)
    saved_tint_alpha = dialogue._tint.alpha()

    resume = dialogue.export_resume_context()
    assert resume is not None
    timeline = resume["visual_timeline"]
    assert timeline["version"] == 2
    assert timeline["portrait_backend_phase"] == "restart"
    assert timeline["background_fade"] == {
        "from": STARTING_ROOM,
        "to": PARK,
        "elapsed_seconds": pytest.approx(0.2),
        "duration_seconds": pytest.approx(0.6),
        "easing": "linear",
    }
    assert timeline["tint"]["color"] == [24, 48, 96]
    assert timeline["tint"]["max_alpha"] == 160
    assert timeline["tint"]["elapsed_seconds"] == pytest.approx(0.75)
    assert timeline["background_blur"] == {
        "from": 0.0,
        "to": 12.0,
        "elapsed_seconds": 1.25,
        "duration_seconds": 4.0,
    }

    assert dialogue.restore_resume_context(copy.deepcopy(resume)) is True
    assert dialogue.background_path == PARK
    assert dialogue._bg_fade is not None
    assert dialogue._bg_fade_from_path == STARTING_ROOM
    assert dialogue._bg_fade.t == pytest.approx(0.2)
    assert dialogue._bg_fade.new is dialogue._bg_surface
    assert dialogue._tint.alpha() == saved_tint_alpha
    assert dialogue._tint.easing_name == "out_cubic"
    assert dialogue._bg_blur == pytest.approx(3.75)

    dialogue._bg_fade.update(0.4)
    dialogue._tint.update(2.25)
    dialogue._advance_bg_blur(2.75)
    assert dialogue._bg_fade.done is True
    assert dialogue._tint.done is True
    assert dialogue._tint.alpha() == 160
    assert dialogue._bg_blur == pytest.approx(12.0)
    assert dialogue._bg_blur_target == pytest.approx(12.0)


@pytest.mark.parametrize(
    ("clearing", "expected_alpha", "expected_direction"),
    [
        (False, 50, 1),
        (True, 150, -1),
    ],
    ids=["fade-in", "fade-out"],
)
def test_weather_resume_continues_its_original_lifecycle_target(
    driver,
    clearing,
    expected_alpha,
    expected_direction,
):
    dialogue = _open(
        driver,
        Scene(
            id=f"visual_resume_weather_{clearing}",
            lines=[Line(text="Weather owns a finite hand-off.")],
        ),
    )
    _set_fading_weather(dialogue, clearing=clearing)
    assert dialogue._ambient.alpha == expected_alpha

    resume = dialogue.export_resume_context()
    assert resume is not None
    checkpoint = resume["visual_timeline"]["weather"]
    assert checkpoint["lifecycle"] == (
        "clearing" if clearing else "active"
    )
    assert checkpoint["backend_phase"] == "restart"
    assert checkpoint["elapsed_seconds"] == pytest.approx(1.0)
    assert checkpoint["duration_seconds"] == pytest.approx(4.0)

    dialogue._advance_ambient(0.5)
    assert dialogue.restore_resume_context(copy.deepcopy(resume)) is True
    assert dialogue._ambient is not None
    assert dialogue._ambient_name == "rain"
    assert dialogue._ambient.alpha == expected_alpha
    assert dialogue._ambient_fade_dir == expected_direction
    assert dialogue._ambient_fade_t == pytest.approx(1.0)

    dialogue._advance_ambient(3.0)
    if clearing:
        assert dialogue._ambient is None
        assert dialogue._ambient_name is None
        assert dialogue._ambient_directive is None
    else:
        assert dialogue._ambient is not None
        assert dialogue._ambient.alpha == 200
        assert dialogue._ambient_fade_dir == 0


def test_reduced_motion_quickload_settles_or_drops_each_tween_by_policy(driver):
    app = driver.app
    dialogue = _open(
        driver,
        Scene(
            id="visual_resume_reduced",
            background=STARTING_ROOM,
            lines=[Line(text="Reduced motion chooses semantic targets.")],
        ),
    )
    _install_all_visuals(dialogue, clearing_weather=True)
    app._quicksave()

    app.config.reduce_motion = True
    assert app._load_slot(app.config.quicksave_slot) is True
    app.manager.commit_pending()
    restored = app.manager.current
    assert type(restored).__name__ == "DialogueScene"

    # Opacity background hand-off is already committed to its destination;
    # spatial camera movement snaps to the authored target; persistent tint
    # settles; spatial blur is removed; a clearing weather overlay is dropped.
    assert restored.background_path == PARK
    assert restored._bg_fade is None
    assert (
        restored._camera.zoom,
        restored._camera.pan_x,
        restored._camera.pan_y,
    ) == pytest.approx((1.6, 80.0, -36.0))
    assert restored._camera.done is True
    assert restored._tint.done is True
    assert restored._tint.alpha() == 160
    assert restored._bg_blur == 0.0
    assert restored._bg_blur_target == 0.0
    assert restored._ambient is None
    assert restored._ambient_name is None
    assert restored._ambient_directive is None


def test_reduced_motion_settles_weather_fade_in_at_authored_opacity(driver):
    app = driver.app
    dialogue = _open(
        driver,
        Scene(
            id="visual_resume_reduced_weather_active",
            lines=[Line(text="Rain remains visible without continuous motion.")],
        ),
    )
    _set_fading_weather(dialogue, clearing=False)
    resume = dialogue.export_resume_context()
    assert resume is not None

    app.config.reduce_motion = True
    assert dialogue.restore_resume_context(copy.deepcopy(resume)) is True
    assert dialogue._ambient is not None
    assert dialogue._ambient.alpha == 200
    assert dialogue._ambient_base_alpha == 200
    assert dialogue._ambient_fade_dir == 0
    assert dialogue._ambient_fade_t == 0.0
    assert dialogue._ambient_fade_dur == 0.0


def test_legacy_resume_without_visual_timeline_keeps_settled_behavior(driver):
    app = driver.app
    dialogue = _open(
        driver,
        Scene(
            id="visual_resume_legacy",
            background=STARTING_ROOM,
            lines=[
                Line(
                    text="Old saves carry only settled presentation values.",
                    effects=[Effect(kind="advance_time", value=1)],
                )
            ],
        ),
    )
    _install_all_visuals(dialogue)
    resume = dialogue.export_resume_context()
    assert resume is not None
    saved_camera = copy.deepcopy(resume["camera"])
    saved_tint_alpha = resume["tint"]["alpha"]
    saved_blur = resume["background_blur"]
    saved_time = app.state.time.model_dump(mode="json")
    resume.pop("visual_timeline")

    assert app._resume_matches_loaded_state(resume) is True
    assert dialogue.restore_resume_context(copy.deepcopy(resume)) is True
    assert dialogue.background_path == PARK
    assert dialogue._bg_fade is None
    assert dialogue._camera.done is True
    assert (
        dialogue._camera.zoom,
        dialogue._camera.pan_x,
        dialogue._camera.pan_y,
    ) == pytest.approx(
        (
            saved_camera["zoom"],
            saved_camera["pan_x"],
            saved_camera["pan_y"],
        )
    )
    assert dialogue._tint.done is True
    assert dialogue._tint.alpha() == saved_tint_alpha
    assert dialogue._bg_blur == pytest.approx(saved_blur)
    assert dialogue._bg_blur_dur == 0.0
    # Legacy weather reconstruction was settled, not clock-resumed.
    assert dialogue._ambient is not None
    assert dialogue._ambient.alpha == 200
    assert dialogue._ambient_fade_dir == 0
    assert app.state.time.model_dump(mode="json") == saved_time


@pytest.mark.parametrize(
    "case",
    [
        "timeline_shape",
        "huge_weather_count",
        "timeline_numeric",
        "timeline_easing",
        "background_path_mismatch",
        "background_source_untrusted",
        "background_target_untrusted",
        "background_catalog_poison",
    ],
)
def test_hostile_visual_timeline_fails_before_state_or_scene_swap(driver, case):
    app = driver.app
    dialogue = _open(
        driver,
        Scene(
            id=f"visual_resume_hostile_{case}",
            background=STARTING_ROOM,
            lines=[
                Line(text="Saved state."),
                Line(text="Live state must survive a hostile load."),
            ],
        ),
    )
    _install_all_visuals(dialogue)
    app._quicksave()
    slot = app.config.quicksave_slot
    path = driver._visual_resume_save_dir / f"{slot}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    resume = raw["_resume"]
    timeline = resume["visual_timeline"]

    if case == "timeline_shape":
        timeline["unexpected"] = "must fail closed"
    elif case == "huge_weather_count":
        resume["weather"]["params"]["count"] = 10 ** 100
    elif case == "timeline_numeric":
        timeline["camera"]["elapsed_seconds"] = 1e308
    elif case == "timeline_easing":
        timeline["camera"]["easing"] = "plugin_curve_without_serializer"
    elif case == "background_path_mismatch":
        timeline["background_fade"]["to"] = STARTING_ROOM
    elif case == "background_source_untrusted":
        timeline["background_fade"]["from"] = "/etc/passwd"
    elif case == "background_target_untrusted":
        resume["background"] = "/etc/passwd"
        timeline["background_fade"]["to"] = "/etc/passwd"
    else:
        poisoned = copy.deepcopy(
            raw["story"]["scenes"][f"visual_resume_hostile_{case}"]
        )
        poisoned["id"] = "save_supplied_background_authority"
        poisoned["background"] = "/etc/passwd"
        raw["story"]["scenes"][poisoned["id"]] = poisoned
        resume["background"] = "/etc/passwd"
        timeline["background_fade"]["to"] = "/etc/passwd"

    path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    dialogue.box.force_reveal()
    dialogue._advance()
    assert dialogue._current_line.text == (
        "Live state must survive a hostile load."
    )
    state_before = app.state.model_dump(mode="json")
    stack_before = app.manager.stack()

    assert app._load_slot(slot) is False
    assert app.state.model_dump(mode="json") == state_before
    assert app.manager.stack() == stack_before
    assert app.manager.current is dialogue
    assert dialogue._current_line.text == (
        "Live state must survive a hostile load."
    )
