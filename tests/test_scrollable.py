"""Focused regression tests for the reusable vertical scroll buffer."""
from __future__ import annotations

import os

import pygame

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from world_gal_game.ui.fonts import FontRegistry
from world_gal_game.ui.input import InputState
from world_gal_game.ui.theme import default_theme
from world_gal_game.ui.widgets.scrollable import ScrollArea


def _area(
    rect: pygame.Rect | None = None,
) -> ScrollArea:
    pygame.font.init()
    return ScrollArea(
        rect or pygame.Rect(12, 8, 120, 100),
        fonts=FontRegistry(("Arial",)),
        theme=default_theme(),
    )


def test_content_taller_than_9000_pixels_is_visible_at_bottom() -> None:
    area = _area()
    content_height = 9_300
    bottom_color = (230, 40, 70, 255)

    def draw_content(target: pygame.Surface) -> int:
        pygame.draw.rect(
            target,
            bottom_color,
            (0, content_height - 12, 80, 12),
        )
        return content_height

    area.set_drawer(draw_content)
    area.scroll_y = 100_000
    target = pygame.Surface((160, 120), pygame.SRCALPHA)

    area.draw(target)

    assert area.content_height == content_height
    assert area.surface is not None
    assert area.surface.get_height() >= content_height
    assert area.scroll_y == content_height - area.rect.height
    assert target.get_at((area.rect.x + 20, area.rect.bottom - 1)) == bottom_color
    # At maximum scroll the correctly-proportioned knob reaches track bottom.
    assert target.get_at((area.rect.right - 4, area.rect.bottom - 2))[:3] == (
        area.theme.accent[:3]
    )


def test_content_buffer_is_reused_without_per_frame_view_allocation(
    monkeypatch,
) -> None:
    area = _area(pygame.Rect(0, 0, 160, 100))
    content_height = 9_200
    area.set_drawer(lambda target: content_height)
    target = pygame.Surface(area.rect.size, pygame.SRCALPHA)

    real_surface = pygame.Surface
    allocations: list[tuple[int, int]] = []

    def counting_surface(size, *args, **kwargs):
        allocations.append(tuple(size))
        return real_surface(size, *args, **kwargs)

    monkeypatch.setattr(pygame, "Surface", counting_surface)
    area.draw(target)
    buffer = area.surface
    allocations_after_first_draw = list(allocations)

    area.draw(target)

    assert buffer is not None
    assert area.surface is buffer
    assert allocations_after_first_draw == [(160, 100), (160, content_height)]
    assert allocations == allocations_after_first_draw


def test_shorter_content_clamps_scroll_and_reuses_existing_capacity() -> None:
    area = _area()
    content_height = [9_300]
    top_color = (40, 190, 120, 255)

    def draw_content(target: pygame.Surface) -> int:
        pygame.draw.rect(target, top_color, (0, 0, 80, content_height[0]))
        return content_height[0]

    area.set_drawer(draw_content)
    first_target = pygame.Surface((160, 120), pygame.SRCALPHA)
    area.draw(first_target)
    area.scroll_y = area.content_height - area.rect.height
    buffer = area.surface

    content_height[0] = 60
    second_target = pygame.Surface((160, 120), pygame.SRCALPHA)
    area.draw(second_target)

    assert area.content_height == 60
    assert area.scroll_y == 0
    assert area.surface is buffer
    assert second_target.get_at((area.rect.x + 20, area.rect.y + 20)) == top_color
    assert second_target.get_at((area.rect.x + 20, area.rect.bottom - 1)).a == 0


def test_mouse_wheel_scrolls_only_when_pointer_is_inside_and_clamps() -> None:
    area = _area()
    area.set_drawer(lambda target: 400)
    area.draw(pygame.Surface((160, 120), pygame.SRCALPHA))

    area.update(
        0.0,
        InputState(mouse_pos=area.rect.center, mouse_wheel=-2),
    )
    assert area.scroll_y == 72

    area.update(
        0.0,
        InputState(mouse_pos=(-1, -1), mouse_wheel=-2),
    )
    assert area.scroll_y == 72

    area.update(
        0.0,
        InputState(mouse_pos=area.rect.center, mouse_wheel=100),
    )
    assert area.scroll_y == 0

    area.update(
        0.0,
        InputState(mouse_pos=area.rect.center, mouse_wheel=-100),
    )
    assert area.scroll_y == 300
