"""SlotAnimation: rect + alpha interpolation driven by easing."""
from __future__ import annotations

import os

import pygame
import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


@pytest.fixture(autouse=True, scope="module")
def init_pygame():
    pygame.init()
    pygame.display.set_mode((1, 1))
    yield
    pygame.quit()


from world_gal_game.ui.portrait_anim import SlotAnimation


def _surf(w=64, h=128):
    s = pygame.Surface((w, h), pygame.SRCALPHA)
    s.fill((200, 100, 50, 255))
    return s


def test_done_lifecycle():
    a = SlotAnimation(kind="enter", rect=pygame.Rect(0, 0, 100, 200),
                      duration=0.3, new=_surf(), anim="fade")
    assert not a.done
    a.update(0.15)
    assert not a.done
    a.update(0.15)
    assert a.done
    # Overshoot must not raise nor exceed duration.
    a.update(10.0)
    assert a.done


def test_progress_uses_linear_when_unspecified():
    # crossfade has no default ease -> resolve(None) == linear.
    a = SlotAnimation(kind="crossfade", rect=pygame.Rect(0, 0, 10, 10),
                      duration=1.0, old=_surf(), new=_surf())
    assert a.progress == pytest.approx(0.0)
    a.update(0.5)
    assert a.progress == pytest.approx(0.5)
    a.update(0.5)
    assert a.progress == pytest.approx(1.0)


def test_fade_entry_alpha_ramps():
    # fade entry uses out_quad by default; alpha at t=0 is 0, at end full.
    target = pygame.Rect(10, 20, 100, 200)
    a = SlotAnimation(kind="enter", rect=target, duration=1.0,
                      new=_surf(), anim="fade")
    r0, alpha0 = a._entry_state(0.0)
    r1, alpha1 = a._entry_state(1.0)
    assert alpha0 == 0
    assert alpha1 == 255
    # Rect is unchanged across a fade (only alpha animates).
    assert r0 == target == r1


def test_slide_left_entry_rect_moves_to_target():
    target = pygame.Rect(100, 50, 80, 160)
    a = SlotAnimation(kind="enter", rect=target, duration=1.0,
                      new=_surf(), anim="slide_left")
    r_start, _ = a._entry_state(0.0)
    r_end, _ = a._entry_state(1.0)
    # Starts offset to the right of target, lands on target.
    assert r_start.x > target.x
    assert r_end.x == target.x
    assert r_end.y == target.y


def test_pop_entry_scales_up_centered():
    target = pygame.Rect(0, 0, 100, 200)
    a = SlotAnimation(kind="enter", rect=target, duration=1.0,
                      new=_surf(), anim="pop")
    r_start, _ = a._entry_state(0.0)
    r_end, _ = a._entry_state(1.0)
    # Smaller at the start, full size centered at the end.
    assert r_start.width < target.width
    assert r_end.width == target.width
    assert r_end.center == target.center


def test_draw_does_not_raise_for_each_kind():
    screen = pygame.Surface((400, 400))
    target = pygame.Rect(50, 50, 100, 200)
    for kind, kwargs in [
        ("enter", dict(new=_surf(), anim="bounce")),
        ("exit", dict(old=_surf(), anim="slide_right")),
        ("move", dict(old=_surf(), new=_surf(),
                      from_rect=pygame.Rect(0, 0, 100, 200))),
        ("crossfade", dict(old=_surf(), new=_surf())),
    ]:
        a = SlotAnimation(kind=kind, rect=target, duration=0.4, **kwargs)
        a.update(0.2)
        a.draw(screen)   # mid-animation
        a.update(0.4)
        a.draw(screen)   # done


def test_flip_applies_without_raising():
    screen = pygame.Surface((400, 400))
    a = SlotAnimation(kind="crossfade", rect=pygame.Rect(0, 0, 80, 160),
                      duration=0.3, old=_surf(), new=_surf(), flip=True)
    a.update(0.15)
    a.draw(screen)
