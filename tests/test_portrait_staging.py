"""Portrait STAGING / direction (slots, two-shots, dim, CG-suppress, motion).

Covers the engine seam added for left/center/right placement and two-character
scenes:

- PortraitSpec author aliases (``id`` -> expression, ``position`` -> slot) and
  the parallel ``Line.portrait_pos`` shorthand, with back-compat for the bare
  string / expression forms (which still centre).
- A single ``portrait:`` spec is placed at its own ``slot`` (was always centre).
- Non-speaker slots dim while the speaker stays full-brightness.
- A full-screen CG suppresses the standing portrait (no double-draw).
- The entrance animation is gated on the reduce-motion accessibility setting.

These exercise the rendering data-model (which slot a portrait lands in, which
slot dims, whether an entrance anim spawns) directly via DialogueScene helpers,
with a minimal SimpleNamespace context — no window, no full app.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

from world_gal_game.core.portrait_spec import PortraitSpec  # noqa: E402
from world_gal_game.core.story_graph import Line as StoryLine  # noqa: E402
from world_gal_game.dialogue.script_loader import (  # noqa: E402
    load_scenes_from_yaml,
)


@pytest.fixture(autouse=True, scope="session")
def _init_pygame():
    pygame.init()
    pygame.display.set_mode((1, 1))
    yield
    pygame.quit()


class _FakeAssets:
    """Returns an opaque coloured surface for any portrait request."""

    def resolve_portrait(self, spec, fallback_size=(480, 640)) -> pygame.Surface:
        s = pygame.Surface((200, 300), pygame.SRCALPHA)
        s.fill((180, 120, 90, 255))
        return s

    def image(self, path, fallback_size=None, **_) -> pygame.Surface:
        s = pygame.Surface(fallback_size or (200, 300), pygame.SRCALPHA)
        s.fill((90, 140, 180, 255))
        return s


class _FakeNPC:
    def __init__(self, npc_id: str) -> None:
        self.id = npc_id

    def portrait_for(self, expression) -> str:
        return f"assets/characters/{self.id}/{expression or 'default'}.png"


class _FakeNPCs:
    """Minimal NPC registry so the bare-``expression:`` resolution path works."""

    def by_name(self, name):
        return _FakeNPC(name) if name else None

    def get(self, key):
        return _FakeNPC(key) if key else None


def _make_scene(*, config=None, assets=None, npcs=None):
    """A DialogueScene wired to a minimal context (no app / window)."""
    from world_gal_game.scenes.dialogue_scene import DialogueScene
    ctx = SimpleNamespace(
        assets=assets or _FakeAssets(),
        screen_size=(1920, 1080),
        config=config,
        npcs=npcs,
    )
    return DialogueScene(ctx)


def _line(**kw):
    """A line stand-in carrying exactly the attrs _update_portraits reads."""
    base = dict(portrait=None, portraits=[], portrait_pos=None,
                speaker=None, expression=None)
    base.update(kw)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# PortraitSpec author-friendly aliases (id / position)
# ---------------------------------------------------------------------------

def test_spec_alias_id_and_position():
    spec = PortraitSpec(**{"character": "qingyi", "id": "smile",
                           "position": "left"})
    assert spec.expression == "smile"
    assert spec.slot == "left"


def test_spec_canonical_names_still_work():
    # populate_by_name keeps the canonical field names working (Python code and
    # the existing dict form both rely on this).
    spec = PortraitSpec(character="qingyi", expression="smile", slot="right")
    assert spec.expression == "smile"
    assert spec.slot == "right"


def test_spec_dump_uses_canonical_names_for_save_round_trip():
    # Saves serialise by field name; aliases must not leak into the dump, and a
    # reload from the dump must reproduce the same spec.
    spec = PortraitSpec(**{"character": "q", "id": "sad", "position": "left"})
    dumped = spec.model_dump()
    assert "expression" in dumped and "slot" in dumped
    assert "id" not in dumped and "position" not in dumped
    again = PortraitSpec(**dumped)
    assert again.expression == "sad" and again.slot == "left"


# ---------------------------------------------------------------------------
# script_loader: position shorthand parses; string form stays back-compat
# ---------------------------------------------------------------------------

def test_loader_string_portrait_backcompat(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text(
        "id: s\nlines:\n"
        "  - speaker: h\n    text: hi\n    portrait: assets/x.png\n",
        encoding="utf-8")
    line = load_scenes_from_yaml(p)[0].lines[0]
    assert isinstance(line.portrait, str)
    assert line.portrait == "assets/x.png"
    assert line.portrait_pos is None      # unset -> centre downstream


def test_loader_portrait_pos_shorthand(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text(
        "id: s\nlines:\n"
        "  - speaker: h\n    text: hi\n    expression: smile\n"
        "    portrait_pos: left\n",
        encoding="utf-8")
    line = load_scenes_from_yaml(p)[0].lines[0]
    assert line.portrait_pos == "left"
    assert line.expression == "smile"


def test_loader_spec_position_alias(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text(
        "id: s\nlines:\n"
        "  - speaker: h\n    text: hi\n"
        "    portrait: {character: q, id: smile, position: right}\n",
        encoding="utf-8")
    line = load_scenes_from_yaml(p)[0].lines[0]
    assert isinstance(line.portrait, PortraitSpec)
    assert line.portrait.expression == "smile"
    assert line.portrait.slot == "right"


def test_story_line_portrait_pos_defaults_none():
    # The new field is optional with a safe default, so every legacy line is
    # unaffected.
    ln = StoryLine(text="x")
    assert ln.portrait_pos is None


# ---------------------------------------------------------------------------
# Single-portrait slot placement (the core fix: spec.slot is honoured)
# ---------------------------------------------------------------------------

def test_single_spec_centers_by_default():
    sc = _make_scene()
    sc._update_portraits(_line(
        portrait=PortraitSpec(character="q", expression="smile"),
        speaker="q"))
    assert sc._slot_surfaces["center"] is not None
    assert sc._slot_surfaces["left"] is None
    assert sc._slot_surfaces["right"] is None
    assert sc._speaking_slot == "center"


def test_single_spec_honours_explicit_left_slot():
    sc = _make_scene()
    sc._update_portraits(_line(
        portrait=PortraitSpec(character="q", expression="smile", slot="left"),
        speaker="q"))
    assert sc._slot_surfaces["left"] is not None
    assert sc._slot_surfaces["center"] is None
    assert sc._speaking_slot == "left"


def test_single_spec_position_alias_routes_right():
    sc = _make_scene()
    spec = PortraitSpec(**{"character": "q", "id": "smile", "position": "right"})
    sc._update_portraits(_line(portrait=spec, speaker="q"))
    assert sc._slot_surfaces["right"] is not None
    assert sc._slot_surfaces["center"] is None
    assert sc._speaking_slot == "right"


def test_portrait_pos_places_bare_expression_form():
    # expression-only line + portrait_pos -> placed off-centre (the author
    # shorthand for the simplest authoring style). The bare-expression path
    # resolves through the NPC registry, so provide a minimal one.
    sc = _make_scene(npcs=_FakeNPCs())
    sc._update_portraits(_line(speaker="q", expression="smile",
                               portrait_pos="left"))
    assert sc._slot_surfaces["left"] is not None
    assert sc._slot_surfaces["center"] is None
    assert sc._speaking_slot == "left"


def test_string_portrait_with_portrait_pos_places_off_center():
    sc = _make_scene()
    sc._update_portraits(_line(portrait="assets/x.png", speaker="q",
                               portrait_pos="right"))
    assert sc._slot_surfaces["right"] is not None
    assert sc._slot_surfaces["center"] is None


def test_string_portrait_without_pos_stays_centered():
    # Pure back-compat: a bare string portrait still centres.
    sc = _make_scene()
    sc._update_portraits(_line(portrait="assets/x.png", speaker="q"))
    assert sc._slot_surfaces["center"] is not None
    assert sc._slot_surfaces["left"] is None
    assert sc._slot_surfaces["right"] is None


# ---------------------------------------------------------------------------
# Multi-portrait / two-shot placement
# ---------------------------------------------------------------------------

def test_two_shot_populates_both_slots():
    sc = _make_scene()
    sc._update_portraits(_line(
        portraits=[
            PortraitSpec(character="q", expression="smile", slot="left"),
            PortraitSpec(character="x", expression="shy", slot="right"),
        ],
        speaker="q"))
    assert sc._slot_surfaces["left"] is not None
    assert sc._slot_surfaces["right"] is not None
    assert sc._slot_surfaces["center"] is None


# ---------------------------------------------------------------------------
# Speaker emphasis / non-speaker dim selection
# ---------------------------------------------------------------------------

def test_non_speaker_slot_dims_speaker_full():
    cfg = SimpleNamespace(dim_inactive_speakers=True)
    sc = _make_scene(config=cfg)
    sc._update_portraits(_line(
        portraits=[
            PortraitSpec(character="q", slot="left"),
            PortraitSpec(character="x", slot="right"),
        ],
        speaker="q"))         # q speaks -> left full, right dimmed
    assert sc._speaking_slot == "left"
    assert sc._slot_dim_factor("left") == 1.0
    assert sc._slot_dim_factor("right") < 1.0


def test_dim_disabled_keeps_all_full_brightness():
    cfg = SimpleNamespace(dim_inactive_speakers=False)
    sc = _make_scene(config=cfg)
    sc._update_portraits(_line(
        portraits=[
            PortraitSpec(character="q", slot="left"),
            PortraitSpec(character="x", slot="right"),
        ],
        speaker="q"))
    assert sc._slot_dim_factor("left") == 1.0
    assert sc._slot_dim_factor("right") == 1.0   # dimming off -> nobody dims


def test_single_speaker_never_dims():
    cfg = SimpleNamespace(dim_inactive_speakers=True)
    sc = _make_scene(config=cfg)
    sc._update_portraits(_line(
        portrait=PortraitSpec(character="q", slot="center"), speaker="q"))
    assert sc._slot_dim_factor("center") == 1.0


def test_narration_does_not_dim_persisted_portrait():
    # A two-shot, then a pure-narration line (no speaker): nobody is "the
    # speaker", so neither persisted portrait is dimmed.
    cfg = SimpleNamespace(dim_inactive_speakers=True)
    sc = _make_scene(config=cfg)
    sc._update_portraits(_line(
        portraits=[PortraitSpec(character="q", slot="left"),
                   PortraitSpec(character="x", slot="right")],
        speaker="q"))
    sc._update_portraits(_line())   # narration keeps both, clears speaker
    assert sc._speaking_slot is None
    assert sc._slot_dim_factor("left") == 1.0
    assert sc._slot_dim_factor("right") == 1.0


# ---------------------------------------------------------------------------
# CG suppresses the standing portrait (no double-draw)
# ---------------------------------------------------------------------------

def _driven_dialogue_scene():
    """A real DialogueScene from a booted (headless) app — full theme / fonts /
    state — for tests that must call draw()."""
    from world_gal_game.dev.driver import GameDriver
    d = GameDriver(pack="demo_pack")
    d.new_game()
    d.advance_frames(10)
    sc = d.app.manager.current
    assert type(sc).__name__ == "DialogueScene"
    return d, sc


def test_cg_suppresses_standing_portrait_in_draw():
    d, sc = _driven_dialogue_scene()
    try:
        sc._update_portraits(_line(
            portrait=PortraitSpec(character="heroine_1_normal", slot="center"),
            speaker="heroine_1_normal"))
        d.advance_frames(40)              # settle past the entrance transition
        assert sc._slot_surfaces["center"] is not None

        sw, sh = sc.ctx.screen_size

        def _render() -> pygame.Surface:
            surf = pygame.Surface((sw, sh))
            surf.fill((0, 0, 0))
            sc.draw(surf)
            return surf

        # Sample where a centred portrait sits (mid-screen, above the box).
        sample = (sw // 2, int(sh * 0.55))

        sc.cg_surface_path = None
        no_cg = _render().get_at(sample)

        sc.cg_surface_path = "assets/cgs/lover_lakeside.png"
        with_cg = _render().get_at(sample)

        # The standing portrait is drawn without the CG and suppressed with it,
        # so the sampled pixel differs — proving no double-draw over the CG.
        assert with_cg != no_cg
    finally:
        d.quit()


def test_cg_suppress_does_not_clear_slot_state():
    # Suppression is a draw-time skip, not a state clear: the staged portrait is
    # retained so it reappears the instant the CG is hidden.
    d, sc = _driven_dialogue_scene()
    try:
        sc._update_portraits(_line(
            portrait=PortraitSpec(character="heroine_1_normal", slot="center"),
            speaker="heroine_1_normal"))
        sc.cg_surface_path = "assets/cgs/lover_lakeside.png"
        surf = pygame.Surface(sc.ctx.screen_size)
        sc.draw(surf)                      # draws CG, skips portrait
        assert sc._slot_surfaces["center"] is not None   # state intact
    finally:
        d.quit()


# ---------------------------------------------------------------------------
# Entrance motion gated on reduce_motion
# ---------------------------------------------------------------------------

def test_entrance_spawns_rise_anim_by_default():
    sc = _make_scene(config=SimpleNamespace(reduce_motion=False))
    sc._update_portraits(_line(
        portrait=PortraitSpec(character="q", slot="center"), speaker="q"))
    anim = sc._slot_anims["center"]
    assert anim is not None
    assert anim.kind == "enter"
    assert anim.anim == "rise"             # the baseline arrival lift


def test_reduce_motion_suppresses_rise_entrance():
    sc = _make_scene(config=SimpleNamespace(reduce_motion=True))
    sc._update_portraits(_line(
        portrait=PortraitSpec(character="q", slot="center"), speaker="q"))
    anim = sc._slot_anims["center"]
    # No sliding/rising entrance under reduce-motion. Either no slot-anim (a
    # plain alpha crossfade is used) or, if one exists, it must not translate.
    if anim is not None:
        assert anim.kind == "crossfade"
        assert anim.anim != "rise"


def test_reduce_motion_suppresses_named_enter_anim():
    sc = _make_scene(config=SimpleNamespace(reduce_motion=True))
    sc._update_portraits(_line(
        portrait=PortraitSpec(character="q", slot="left", enter="slide_left"),
        speaker="q"))
    anim = sc._slot_anims["left"]
    assert anim is not None
    assert anim.kind == "crossfade"        # authored slide downgraded to a fade
    assert anim.anim != "slide_left"


def test_named_enter_anim_used_when_motion_allowed():
    sc = _make_scene(config=SimpleNamespace(reduce_motion=False))
    sc._update_portraits(_line(
        portrait=PortraitSpec(character="q", slot="left", enter="slide_left"),
        speaker="q"))
    anim = sc._slot_anims["left"]
    assert anim is not None
    assert anim.kind == "enter"
    assert anim.anim == "slide_left"


def test_reduce_motion_missing_config_defaults_off():
    # A context without a config must not raise (motion treated as allowed).
    sc = _make_scene(config=None)
    sc._update_portraits(_line(
        portrait=PortraitSpec(character="q", slot="center"), speaker="q"))
    # No exception == pass; entrance proceeds with the default lift.
    assert sc._slot_anims["center"] is not None
