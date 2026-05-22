"""Camera + screen-FX primitives (ui/camera.py).

These are pure update/progress state machines (modelled on FadeTransition), so
they need no display surface for their *math*; the draw() smoke at the end uses
a dummy SDL surface to confirm blits don't raise.
"""
import math

import pytest

from world_gal_game.ui.camera import (
    Camera, ScreenShake, ScreenFlash, ColorTint,
)


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------

def test_camera_starts_neutral_and_done():
    c = Camera()
    assert c.zoom == pytest.approx(1.0)
    assert c.pan_x == 0.0 and c.pan_y == 0.0
    assert c.done is True
    assert c.is_neutral is True


def test_camera_zoom_progresses_to_target_then_done():
    c = Camera()
    c.zoom_to(2.0, duration=1.0)
    assert c.done is False
    c.update(0.5)
    # Linear (default easing) → halfway.
    assert c.zoom == pytest.approx(1.5, abs=1e-3)
    assert c.done is False
    c.update(0.5)
    assert c.zoom == pytest.approx(2.0, abs=1e-3)
    assert c.done is True
    # Past-duration updates are inert.
    c.update(1.0)
    assert c.zoom == pytest.approx(2.0, abs=1e-3)


def test_camera_pan_progresses_on_both_axes():
    c = Camera()
    c.pan_to(100.0, -40.0, duration=1.0)
    c.update(0.25)
    assert c.pan_x == pytest.approx(25.0, abs=1e-2)
    assert c.pan_y == pytest.approx(-10.0, abs=1e-2)
    c.update(0.75)
    assert c.pan_x == pytest.approx(100.0, abs=1e-2)
    assert c.pan_y == pytest.approx(-40.0, abs=1e-2)


def test_camera_zero_duration_snaps_instantly():
    c = Camera()
    c.zoom_to(3.0, duration=0.0)
    assert c.zoom == pytest.approx(3.0)
    assert c.done is True
    assert c.is_neutral is False


def test_camera_reset_returns_to_neutral():
    c = Camera()
    c.zoom_to(2.0, duration=0.0)
    c.pan_to(50.0, 50.0, duration=0.0)
    assert c.is_neutral is False
    c.reset(duration=0.0)
    assert c.is_neutral is True


def test_camera_apply_neutral_returns_source_unchanged():
    pygame = pytest.importorskip("pygame")
    pygame.display.init()
    src = pygame.Surface((64, 32))
    c = Camera()
    out, topleft = c.apply(src)
    assert out is src
    assert topleft == (0, 0)


def test_camera_apply_zoom_scales_about_center():
    pygame = pytest.importorskip("pygame")
    pygame.display.init()
    src = pygame.Surface((100, 100))
    c = Camera()
    c.zoom_to(2.0, duration=0.0)
    out, (ox, oy) = c.apply(src)
    assert out.get_size() == (200, 200)
    # Centre stays anchored: shift back by half the size delta.
    assert ox == -50 and oy == -50


# ---------------------------------------------------------------------------
# ScreenShake
# ---------------------------------------------------------------------------

def test_screen_shake_offset_decays_to_zero():
    s = ScreenShake(intensity=20.0, duration=1.0)
    assert s.done is False
    early = s.offset()
    early_mag = math.hypot(*early)
    s.update(0.5)
    mid = s.offset()
    mid_mag = math.hypot(*mid)
    # Magnitude decays as time advances (out_cubic) → later is smaller.
    assert mid_mag <= early_mag + 1e-6
    s.update(0.5)
    assert s.done is True
    assert s.offset() == (0, 0)


def test_screen_shake_offset_within_intensity():
    s = ScreenShake(intensity=10.0, duration=1.0)
    for _ in range(20):
        dx, dy = s.offset()
        assert abs(dx) <= 11 and abs(dy) <= 11   # +1 px rounding slack
        s.update(0.05)


def test_screen_shake_clamps_time():
    s = ScreenShake(intensity=10.0, duration=0.2)
    s.update(5.0)
    assert s.t == pytest.approx(0.2)
    assert s.done is True


# ---------------------------------------------------------------------------
# ScreenFlash
# ---------------------------------------------------------------------------

def test_screen_flash_alpha_fades_full_to_zero():
    f = ScreenFlash(color=(255, 255, 255), duration=1.0, max_alpha=255)
    assert f.alpha() == 255          # full at t=0
    f.update(0.5)
    assert 0 < f.alpha() < 255       # mid-fade
    f.update(0.5)
    assert f.alpha() == 0
    assert f.done is True


def test_screen_flash_respects_max_alpha():
    f = ScreenFlash(duration=1.0, max_alpha=128)
    assert f.alpha() == 128


def test_screen_flash_bad_color_falls_back():
    f = ScreenFlash(color="not-a-color", duration=0.5)
    assert f.color == (255, 255, 255)


# ---------------------------------------------------------------------------
# ColorTint
# ---------------------------------------------------------------------------

def test_color_tint_fades_in_then_persists():
    t = ColorTint(color=(120, 0, 0), duration=1.0, max_alpha=120)
    assert t.alpha() == 0            # starts transparent
    t.update(0.5)
    assert 0 < t.alpha() < 120
    t.update(0.5)
    assert t.alpha() == 120
    # done == fade-in finished; the tint then *persists* (alpha stays).
    assert t.done is True
    t.update(2.0)
    assert t.alpha() == 120


def test_color_tint_zero_duration_is_instant_and_persistent():
    t = ColorTint(color=(0, 0, 0), duration=0.0, max_alpha=200)
    assert t.alpha() == 200
    assert t.done is True
    t.update(1.0)
    assert t.alpha() == 200


def test_color_tint_bad_color_falls_back():
    t = ColorTint(color=None, duration=0.0)
    assert t.color == (0, 0, 0)


# ---------------------------------------------------------------------------
# draw() smoke — confirm overlays blit without error on a real surface
# ---------------------------------------------------------------------------

def test_fx_draw_smoke():
    pygame = pytest.importorskip("pygame")
    pygame.display.init()
    surf = pygame.Surface((200, 150))
    ScreenFlash(duration=0.5).draw(surf)
    ColorTint(duration=0.0).draw(surf)
    # A neutral/finished flash draws nothing but must not raise.
    f = ScreenFlash(duration=0.1)
    f.update(0.2)
    f.draw(surf)
