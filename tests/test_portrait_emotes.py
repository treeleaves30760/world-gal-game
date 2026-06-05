"""In-place portrait emotes (Pillar 3 of the presentation layer).

Covers the PortraitEmote state machine, the portrait_emote effect, the scene's
rect transform + slot resolution, and a live DialogueScene integration.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from world_gal_game.core.game_state import GameState
from world_gal_game.core.story_graph import Effect, Scene, Line
from world_gal_game.core.portrait_spec import PortraitSpec
from world_gal_game.plugins.builtin_effects import VISUAL_FX_QUEUE
from world_gal_game.ui.portrait_anim import PortraitEmote, PORTRAIT_EMOTES


# ---------------------------------------------------------------------------
# PortraitEmote state machine
# ---------------------------------------------------------------------------

def test_emote_clamps_and_finishes():
    e = PortraitEmote(kind="jump", duration=0.4)
    assert e.done is False
    e.update(0.2)
    assert e.done is False
    e.update(5.0)
    assert e.t == pytest.approx(0.4)
    assert e.done is True
    assert e.transform() == (0, 0, 1.0, 1.0)   # rest transform when done


def test_unknown_emote_falls_back_to_jump():
    assert PortraitEmote(kind="nope").kind == "jump"


@pytest.mark.parametrize("kind", PORTRAIT_EMOTES)
def test_emote_transform_shape(kind):
    e = PortraitEmote(kind=kind, duration=1.0, intensity=40.0)
    e.update(0.5)                       # mid-emote
    dx, dy, sx, sy = e.transform()
    assert isinstance(dx, int) and isinstance(dy, int)
    assert sx > 0 and sy > 0


def test_jump_goes_up_at_midpoint():
    e = PortraitEmote(kind="jump", duration=1.0, intensity=50.0)
    e.update(0.5)
    _dx, dy, _sx, _sy = e.transform()
    assert dy < 0                       # up is negative y, peak at p=0.5


def test_nod_goes_down_at_midpoint():
    e = PortraitEmote(kind="nod", duration=1.0, intensity=50.0)
    e.update(0.5)
    _dx, dy, _sx, _sy = e.transform()
    assert dy > 0                       # a bow dips downward


def test_bounce_hops_without_squash():
    # bounce is a plain hop now: it translates up but must NOT scale-distort the
    # static portrait (the geometric squash was removed — squashing a hand-drawn
    # 立繪 reads as rubber, the same reason breath-scaling was dropped).
    e = PortraitEmote(kind="bounce", duration=1.0, intensity=40.0)
    e.update(0.5)                       # apex of the hop
    _dx, dy, sx, sy = e.transform()
    assert dy < 0                       # hopped up
    assert sx == 1.0 and sy == 1.0      # no geometric distortion


def test_bad_intensity_falls_back():
    e = PortraitEmote(kind="jump", intensity="oops")
    assert e.intensity == 30.0


# ---------------------------------------------------------------------------
# Effect handler — enqueue a directive
# ---------------------------------------------------------------------------

def _queued(state: GameState) -> list[dict]:
    return state.meta.get(VISUAL_FX_QUEUE, [])


def test_portrait_emote_effect_enqueues_directive():
    s = GameState()
    out = s.apply(Effect(kind="portrait_emote", target="center",
                         value={"emote": "shake", "duration": 0.6,
                                "intensity": 18}))
    assert out["target"] == "center" and out["emote"] == "shake"
    d = _queued(s)[0]
    assert d == {"fx": "portrait_emote", "target": "center",
                 "emote": "shake", "duration": 0.6, "intensity": 18.0}


def test_portrait_emote_omits_intensity_when_unset():
    s = GameState()
    s.apply(Effect(kind="portrait_emote", target="hero", value={"emote": "nod"}))
    assert "intensity" not in _queued(s)[0]


def test_capability_manifest_exports_portrait_emote():
    from world_gal_game.dev.capability_manifest import build_manifest
    m = build_manifest()
    by_kind = {e["kind"]: e for e in m["effects"]}
    assert "portrait_emote" in by_kind
    assert "args_schema" in by_kind["portrait_emote"]


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


def test_emote_rect_identity_when_none():
    from world_gal_game.scenes.dialogue_scene import DialogueScene
    rect = pygame.Rect(100, 50, 200, 400)
    out = DialogueScene._emote_rect(rect, None)
    assert out == rect


def test_emote_rect_keeps_feet_planted():
    from world_gal_game.scenes.dialogue_scene import DialogueScene
    rect = pygame.Rect(100, 50, 200, 400)
    e = PortraitEmote(kind="bounce", duration=1.0, intensity=40.0)
    e.update(0.02)                      # squashed, no vertical offset yet
    out = DialogueScene._emote_rect(rect, e)
    # Bottom edge stays put (feet planted) even as width/height change.
    assert out.bottom == pytest.approx(rect.bottom, abs=2)
    assert out.centerx == pytest.approx(rect.centerx, abs=2)


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


def test_emote_effect_plays_on_slot(driver):
    sc = Scene(id="probe_emote_slot", lines=[
        Line(text="hi", speaker="A",
             portraits=[PortraitSpec(character="A", slot="center")],
             effects=[Effect(kind="portrait_emote", target="center",
                             value={"emote": "jump", "duration": 0.5})]),
    ])
    ds = _open(driver, sc)
    assert ds._slot_emotes["center"] is not None
    assert ds._slot_emotes["center"].kind == "jump"


def test_emote_resolves_character_to_slot(driver):
    sc = Scene(id="probe_emote_char", lines=[
        Line(text="hi", speaker="A",
             portraits=[PortraitSpec(character="A", slot="left")]),
    ])
    ds = _open(driver, sc)
    ds._apply_portrait_emote({"target": "A", "emote": "shake"})
    assert ds._slot_emotes["left"] is not None
    assert ds._slot_emotes["left"].kind == "shake"


def test_emote_unknown_target_is_noop(driver):
    sc = Scene(id="probe_emote_bad", lines=[Line(text="hi")])
    ds = _open(driver, sc)
    ds._apply_portrait_emote({"target": "ghost", "emote": "jump"})
    assert all(ds._slot_emotes[s] is None for s in ("left", "center", "right"))
