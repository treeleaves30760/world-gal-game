"""A clickable node-graph map.

Each location is a node; lines connect locations that are reachable from
each other. The player's current location is highlighted in amber;
locations directly reachable in pink; others in muted grey.
"""
from __future__ import annotations

from typing import Callable

import pygame

from .base import Widget
from ..fonts import FontRegistry
from ..theme import Theme


class MapView(Widget):
    def __init__(self, rect: pygame.Rect, *, fonts: FontRegistry, theme: Theme,
                 on_click: Callable[[str], None] | None = None):
        super().__init__(rect)
        self.fonts = fonts
        self.theme = theme
        self.on_click = on_click
        self.nodes: list[dict] = []        # {id, name, region, x, y, visited, accessible, reachable}
        self.current_id: str | None = None
        self._node_rects: dict[str, pygame.Rect] = {}
        self._hover_id: str | None = None

    def set_data(self, *, nodes: list[dict], current_id: str | None,
                 reachable_ids: set[str]) -> None:
        self.nodes = nodes
        self.current_id = current_id
        self.reachable_ids = reachable_ids
        self._recompute()

    def _recompute(self) -> None:
        if not self.nodes:
            self._node_rects = {}
            return
        xs = [n["map_x"] for n in self.nodes]
        ys = [n["map_y"] for n in self.nodes]
        minx, maxx = min(xs), max(xs)
        miny, maxy = min(ys), max(ys)
        dx = max(1, maxx - minx)
        dy = max(1, maxy - miny)
        margin = 60
        inner = pygame.Rect(self.rect.x + margin, self.rect.y + margin,
                            self.rect.width - margin * 2,
                            self.rect.height - margin * 2)
        self._node_rects = {}
        for n in self.nodes:
            px = inner.x + int((n["map_x"] - minx) / dx * inner.width)
            py = inner.y + int((n["map_y"] - miny) / dy * inner.height)
            rect = pygame.Rect(0, 0, 160, 44)
            rect.center = (px, py)
            self._node_rects[n["id"]] = rect

    def update(self, dt: float, inp) -> None:
        self._hover_id = None
        for nid, rect in self._node_rects.items():
            if rect.collidepoint(inp.mouse_pos):
                self._hover_id = nid
                if inp.mouse_clicked and self.on_click is not None:
                    self.on_click(nid)
                break

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        # background frame
        frame = pygame.Surface(self.rect.size, pygame.SRCALPHA)
        pygame.draw.rect(frame, (*self.theme.accent[:3], 12),
                         frame.get_rect(),
                         border_radius=self.theme.radius_l)
        pygame.draw.rect(frame, (*self.theme.accent[:3], 80),
                         frame.get_rect(), width=1,
                         border_radius=self.theme.radius_l)
        surface.blit(frame, self.rect.topleft)

        # connection lines: only draw between known reachable nodes
        node_by_id = {n["id"]: n for n in self.nodes}
        for n in self.nodes:
            for exit_id in n.get("exits", []):
                if exit_id not in self._node_rects:
                    continue
                a = self._node_rects[n["id"]].center
                b = self._node_rects[exit_id].center
                pygame.draw.line(surface, (*self.theme.border_soft[:3], 80),
                                 a, b, 1)

        # nodes
        for n in self.nodes:
            rect = self._node_rects[n["id"]]
            is_current = n["id"] == self.current_id
            is_reachable = n["id"] in self.reachable_ids
            is_locked = not n.get("accessible", True)
            if is_locked:
                fill = (60, 60, 60, 160)
                border = self.theme.border_soft
                text_color = self.theme.text_dim
            elif is_current:
                fill = (*self.theme.accent_warm[:3], 160)
                border = self.theme.accent_warm
                text_color = (255, 255, 255)
            elif is_reachable:
                fill = (*self.theme.accent[:3], 110)
                border = self.theme.accent
                text_color = (255, 255, 255)
            else:
                fill = (*self.theme.accent_alt[:3], 60)
                border = (*self.theme.accent_alt[:3], 130)
                text_color = self.theme.text_mute
            if n["id"] == self._hover_id and (is_reachable or is_current):
                fill = tuple(min(255, c + 40) for c in fill[:3]) + (fill[3],)
            chip = pygame.Surface(rect.size, pygame.SRCALPHA)
            pygame.draw.rect(chip, fill, chip.get_rect(),
                             border_radius=self.theme.radius_m)
            pygame.draw.rect(chip, border, chip.get_rect(),
                             width=2 if is_current else 1,
                             border_radius=self.theme.radius_m)
            surface.blit(chip, rect.topleft)
            label = self.fonts.render(n["name"], 18, text_color, bold=True)
            surface.blit(label, (rect.x + (rect.width - label.get_width()) // 2,
                                 rect.y + 5))
            region = n.get("region") or ""
            if region:
                sub = self.fonts.render(region, 14, self.theme.text_mute)
                surface.blit(sub, (rect.x + (rect.width - sub.get_width()) // 2,
                                   rect.y + 25))
