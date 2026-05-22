"""Tests for the portrait spec system."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pygame
import pytest

# Use headless SDL so no display is needed.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


@pytest.fixture(autouse=True, scope="session")
def init_pygame():
    """Initialise pygame once for the whole test session."""
    pygame.init()
    pygame.display.set_mode((1, 1))
    yield
    pygame.quit()


# ---------------------------------------------------------------------------
# PortraitSpec.candidate_paths
# ---------------------------------------------------------------------------

from world_gal_game.core.portrait_spec import PortraitSpec


def test_candidate_paths_order():
    spec = PortraitSpec(character="heroine_1", expression="smile", pose="stand", outfit="uniform")
    paths = spec.candidate_paths()
    assert len(paths) == 4
    # Most specific first.
    assert paths[0] == "assets/characters/heroine_1/smile_stand_uniform.png"
    assert paths[1] == "assets/characters/heroine_1/smile_stand.png"
    assert paths[2] == "assets/characters/heroine_1/smile.png"
    assert paths[3] == "assets/characters/heroine_1/heroine_1.png"


def test_candidate_paths_defaults():
    spec = PortraitSpec(character="heroine_2")
    paths = spec.candidate_paths()
    assert paths[0] == "assets/characters/heroine_2/default_stand_default.png"
    assert paths[-1] == "assets/characters/heroine_2/heroine_2.png"


def test_slot_default():
    spec = PortraitSpec(character="heroine_2")
    assert spec.slot == "center"


def test_slot_explicit():
    spec = PortraitSpec(character="heroine_1", slot="left")
    assert spec.slot == "left"


# ---------------------------------------------------------------------------
# Staging fields: defaults are neutral; legacy candidate_paths unchanged
# ---------------------------------------------------------------------------


def test_staging_fields_default_neutral():
    spec = PortraitSpec(character="heroine_1")
    assert spec.offset == (0, 0)
    assert spec.scale == 1.0
    assert spec.flip is False
    assert spec.enter is None
    assert spec.exit is None


def test_staging_fields_explicit():
    spec = PortraitSpec(character="heroine_1", offset=(10, -20), scale=1.2,
                        flip=True, enter="slide_left", exit="fade")
    assert spec.offset == (10, -20)
    assert spec.scale == 1.2
    assert spec.flip is True
    assert spec.enter == "slide_left"
    assert spec.exit == "fade"


def test_staging_does_not_change_candidate_paths():
    legacy = PortraitSpec(character="hero", expression="smile", pose="stand",
                          outfit="uniform")
    staged = PortraitSpec(character="hero", expression="smile", pose="stand",
                          outfit="uniform", offset=(5, 5), scale=2.0,
                          flip=True, enter="pop", exit="bounce")
    assert legacy.candidate_paths() == staged.candidate_paths()


def test_portrait_animations_constant():
    from world_gal_game.core.portrait_spec import PORTRAIT_ANIMATIONS
    assert "none" in PORTRAIT_ANIMATIONS
    assert "fade" in PORTRAIT_ANIMATIONS
    assert "slide_left" in PORTRAIT_ANIMATIONS
    assert "slide_right" in PORTRAIT_ANIMATIONS
    assert "bounce" in PORTRAIT_ANIMATIONS
    assert "pop" in PORTRAIT_ANIMATIONS


# ---------------------------------------------------------------------------
# script_loader three YAML forms
# ---------------------------------------------------------------------------

import yaml
import textwrap
from world_gal_game.dialogue.script_loader import load_scenes_from_yaml


def _write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "scene.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def test_loader_old_string_portrait(tmp_path):
    p = _write_yaml(tmp_path, """
        id: s1
        lines:
          - speaker: heroine_1
            text: Hello
            portrait: assets/characters/heroine_1_smile.png
    """)
    scenes = load_scenes_from_yaml(p)
    line = scenes[0].lines[0]
    assert isinstance(line.portrait, str)
    assert line.portrait == "assets/characters/heroine_1_smile.png"


def test_loader_spec_dict_portrait(tmp_path):
    p = _write_yaml(tmp_path, """
        id: s2
        lines:
          - speaker: heroine_1
            text: Hi
            portrait:
              character: heroine_1
              expression: smile
    """)
    scenes = load_scenes_from_yaml(p)
    line = scenes[0].lines[0]
    assert isinstance(line.portrait, PortraitSpec)
    assert line.portrait.character == "heroine_1"
    assert line.portrait.expression == "smile"


def test_loader_multi_portrait(tmp_path):
    p = _write_yaml(tmp_path, """
        id: s3
        lines:
          - text: Scene
            portraits:
              - character: heroine_1
                expression: smile
                slot: left
              - character: heroine_2
                expression: neutral
                slot: right
    """)
    scenes = load_scenes_from_yaml(p)
    line = scenes[0].lines[0]
    assert len(line.portraits) == 2
    assert line.portraits[0].character == "heroine_1"
    assert line.portraits[0].slot == "left"
    assert line.portraits[1].character == "heroine_2"
    assert line.portraits[1].slot == "right"


# ---------------------------------------------------------------------------
# AssetManager.resolve_portrait — missing files return placeholder, not raise
# ---------------------------------------------------------------------------

from world_gal_game.ui.assets import AssetManager


def test_resolve_portrait_missing_returns_placeholder(tmp_path):
    assets = AssetManager(pack_root=tmp_path)
    spec = PortraitSpec(character="ghost", expression="sad")
    result = assets.resolve_portrait(spec)
    assert isinstance(result, pygame.Surface)
    # Must not raise; surface should have non-zero size.
    assert result.get_width() > 0
    assert result.get_height() > 0


def test_resolve_portrait_finds_existing(tmp_path):
    # Create the most-specific candidate so it is resolved.
    char_dir = tmp_path / "assets" / "characters" / "hero"
    char_dir.mkdir(parents=True)
    # Create a minimal 1x1 PNG.
    surf = pygame.Surface((1, 1))
    img_path = char_dir / "happy_stand_default.png"
    pygame.image.save(surf, str(img_path))

    assets = AssetManager(pack_root=tmp_path)
    spec = PortraitSpec(character="hero", expression="happy", pose="stand")
    result = assets.resolve_portrait(spec)
    assert isinstance(result, pygame.Surface)


# ---------------------------------------------------------------------------
# PortraitCrossfade
# ---------------------------------------------------------------------------

from world_gal_game.ui.transitions import PortraitCrossfade


def test_crossfade_not_done_initially():
    cf = PortraitCrossfade(None, None, duration=0.25)
    assert not cf.done


def test_crossfade_done_after_duration():
    cf = PortraitCrossfade(None, None, duration=0.25)
    cf.update(0.25)
    assert cf.done


def test_crossfade_done_after_exceeding_duration():
    cf = PortraitCrossfade(None, None, duration=0.25)
    # Overshooting should not raise and should still be done.
    cf.update(10.0)
    assert cf.done


def test_crossfade_partial_update_not_done():
    cf = PortraitCrossfade(None, None, duration=0.5)
    cf.update(0.1)
    assert not cf.done


def test_crossfade_draw_with_surfaces():
    """draw() with real surfaces must not raise."""
    screen = pygame.Surface((800, 600))
    old_surf = pygame.Surface((480, 400), pygame.SRCALPHA)
    new_surf = pygame.Surface((480, 400), pygame.SRCALPHA)
    cf = PortraitCrossfade(old_surf, new_surf, duration=0.3)
    cf.update(0.15)
    rect = pygame.Rect(100, 50, 480, 400)
    cf.draw(screen, rect)   # must not raise


def test_crossfade_draw_with_none_surfaces():
    """draw() with None old/new must not raise."""
    screen = pygame.Surface((800, 600))
    cf = PortraitCrossfade(None, None, duration=0.25)
    cf.update(0.1)
    cf.draw(screen, pygame.Rect(0, 0, 100, 100))


# ---------------------------------------------------------------------------
# BackgroundFade
# ---------------------------------------------------------------------------

from world_gal_game.ui.transitions import BackgroundFade


def test_bg_fade_done_after_duration():
    bf = BackgroundFade(None, None, duration=0.6)
    bf.update(0.6)
    assert bf.done


def test_bg_fade_not_done_before():
    bf = BackgroundFade(None, None, duration=0.6)
    bf.update(0.3)
    assert not bf.done
