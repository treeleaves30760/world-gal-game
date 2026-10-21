"""Responsive safe-area and queue contracts for notification toasts."""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.theme import default_theme
from world_gal_game.ui.widgets.toast import Toast, ToastStack


_LONG_TITLE = "新生指引的重要通知：請先完成目前行程，再前往下一個約定地點"
_LONG_DETAIL = (
    "這是一段刻意很長的說明文字，用來確認中文、Latin words and spaces "
    "都會依照實際字寬換行，而且最後一行會在安全區內妥善省略，"
    "不會穿過通知卡片或畫面邊界。"
)


@pytest.fixture(scope="module", autouse=True)
def _pygame_init():
    pygame.display.init()
    pygame.font.init()
    yield
    pygame.quit()


def _make_stack(
    size: tuple[int, int],
    safe: pygame.Rect,
    *,
    reduce_motion: bool = False,
) -> ToastStack:
    return ToastStack(
        safe,
        fonts=FontRegistry(candidates=(), scale=size[1] / 720.0),
        theme=default_theme(),
        reduce_motion=reduce_motion,
    )


@pytest.mark.parametrize("size", [(1280, 720), (1920, 1080)])
def test_toasts_scale_clip_and_queue_inside_safe_area(size, tmp_path) -> None:
    width, height = size
    safe = pygame.Rect(
        round(width * 0.56),
        round(height * 0.07),
        round(width * 0.40),
        round(height * 0.50),
    )
    stack = _make_stack(size, safe)
    for index in range(4):
        stack.push(
            Toast(
                title=f"{_LONG_TITLE} {index + 1}",
                detail=f"{_LONG_DETAIL} {index + 1}",
                duration=4.0,
            )
        )

    stack.update(0.5, None)

    assert [toast.age for toast in stack.toasts] == [0.5, 0.5, 0.0, 0.0]
    assert all(toast.visible_alpha == 255 for toast in stack.toasts[:2])
    assert all(toast.visible_alpha == 0 for toast in stack.toasts[2:])

    surface = pygame.Surface(size, pygame.SRCALPHA)
    stack.draw(surface)
    screenshot = tmp_path / f"toast-safe-area-{width}x{height}.png"
    pygame.image.save(surface, screenshot)
    assert screenshot.exists() and screenshot.stat().st_size > 0

    bounds = sorted(
        pygame.mask.from_surface(surface, threshold=1).get_bounding_rects(),
        key=lambda rect: rect.top,
    )
    assert len(bounds) == ToastStack.MAX_VISIBLE
    assert all(safe.contains(rect) for rect in bounds)
    assert bounds[0].bottom < bounds[1].top

    # Card height follows the live font line metrics at each density.
    title_line_h = stack.fonts.get(20, bold=True).get_linesize()
    detail_line_h = stack.fonts.get(14).get_linesize()
    assert all(rect.height >= title_line_h + detail_line_h * 2 for rect in bounds)

    outside = surface.copy()
    outside.fill((0, 0, 0, 0), safe)
    assert pygame.mask.from_surface(outside, threshold=1).count() == 0


def test_queued_toast_only_ages_after_a_visible_slot_opens() -> None:
    stack = _make_stack((1280, 720), pygame.Rect(40, 40, 560, 300))
    first = Toast("First", duration=0.2)
    second = Toast("Second", duration=3.0)
    queued = Toast("Queued", duration=3.0)
    stack.push(first)
    stack.push(second)
    stack.push(queued)

    stack.update(0.25, None)

    assert stack.toasts == [second, queued]
    assert second.age == pytest.approx(0.25)
    assert queued.age == 0.0

    stack.update(0.1, None)

    assert second.age == pytest.approx(0.35)
    assert queued.age == pytest.approx(0.1)
    assert queued.visible_alpha > 0


@pytest.mark.parametrize("size", [(1280, 720), (1920, 1080)])
def test_long_text_uses_measured_wrap_and_ellipsis(size) -> None:
    safe = pygame.Rect(30, 30, round(size[0] * 0.32), round(size[1] * 0.4))
    stack = _make_stack(size, safe)
    title_font = stack.fonts.get(20, bold=True)
    detail_font = stack.fonts.get(14)
    text_width = round(180 * size[1] / 720.0)

    title = stack._ellipsize(_LONG_TITLE, title_font, text_width)
    lines = stack._wrap_text(
        _LONG_DETAIL,
        detail_font,
        text_width,
        max_lines=2,
    )

    assert title.endswith("…")
    assert title_font.size(title)[0] <= text_width
    assert len(lines) == 2
    assert lines[-1].endswith("…")
    assert all(detail_font.size(line)[0] <= text_width for line in lines)


def test_reduced_motion_keeps_fade_but_removes_geometric_slide() -> None:
    safe = pygame.Rect(100, 80, 500, 260)
    animated = _make_stack((1280, 720), safe)
    reduced = _make_stack((1280, 720), safe, reduce_motion=True)
    animated_toast = Toast("Animated")
    reduced_toast = Toast("Reduced")
    animated.push(animated_toast)
    reduced.push(reduced_toast)

    animated.update(0.1, None)
    reduced.update(0.1, None)

    # Reduced motion keeps the non-geometric state hand-off, but finishes it
    # within the central 120 ms opacity cap.
    assert reduced_toast.visible_alpha > animated_toast.visible_alpha
    assert animated._slide_offset(animated_toast, 360, 1.0) > 0
    assert reduced._slide_offset(reduced_toast, 360, 1.0) == 0


def test_slide_is_clipped_to_offset_constructor_rect(tmp_path) -> None:
    size = (1280, 720)
    safe = pygame.Rect(220, 140, 360, 210)
    stack = _make_stack(size, safe)
    stack.push(Toast(_LONG_TITLE, _LONG_DETAIL))
    stack.update(0.05, None)
    surface = pygame.Surface(size, pygame.SRCALPHA)

    stack.draw(surface)

    bounds = pygame.mask.from_surface(
        surface,
        threshold=1,
    ).get_bounding_rects()
    assert bounds
    assert all(safe.contains(rect) for rect in bounds)
    pygame.image.save(surface, tmp_path / "toast-slide-clipped.png")
