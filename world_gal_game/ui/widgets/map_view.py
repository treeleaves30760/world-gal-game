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

        # connection lines between nodes
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
            is_visited = n.get("visited", False)

            # Region-tinted base for unvisited accessible nodes
            region_color: tuple[int, int, int] | None = n.get("region_color")

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
            elif is_visited and region_color:
                # Visited, not reachable right now: use region color at medium alpha
                fill = (*region_color, 90)
                border = (*region_color, 160)
                text_color = self.theme.text_mute
            elif is_visited:
                fill = (*self.theme.accent_alt[:3], 60)
                border = (*self.theme.accent_alt[:3], 130)
                text_color = self.theme.text_mute
            else:
                # Unvisited: muted grey + "?" marker
                fill = (50, 50, 60, 140)
                border = (80, 80, 90, 120)
                text_color = self.theme.text_dim

            if n["id"] == self._hover_id and (is_reachable or is_current):
                fill = tuple(min(255, c + 40) for c in fill[:3]) + (fill[3],)

            chip = pygame.Surface(rect.size, pygame.SRCALPHA)
            pygame.draw.rect(chip, fill, chip.get_rect(),
                             border_radius=self.theme.radius_m)
            pygame.draw.rect(chip, border, chip.get_rect(),
                             width=2 if is_current else 1,
                             border_radius=self.theme.radius_m)
            surface.blit(chip, rect.topleft)

            if is_visited or is_current or is_reachable:
                label = self.fonts.render(n["name"], 18, text_color, bold=True)
                surface.blit(label, (rect.x + (rect.width - label.get_width()) // 2,
                                     rect.y + 5))
                region_display = n.get("region_name") or n.get("region") or ""
                if region_display:
                    sub = self.fonts.render(region_display, 14, self.theme.text_mute)
                    surface.blit(sub, (rect.x + (rect.width - sub.get_width()) // 2,
                                       rect.y + 25))
            else:
                # Unknown location: show "?" to indicate unexplored
                q = self.fonts.render("?", 22, text_color, bold=True)
                surface.blit(q, (rect.x + (rect.width - q.get_width()) // 2,
                                 rect.y + (rect.height - q.get_height()) // 2))

        # Hover tooltip: show description below the hovered node
        if self._hover_id and self._hover_id in self._node_rects:
            hovered_node = next((n for n in self.nodes if n["id"] == self._hover_id), None)
            if hovered_node:
                desc = hovered_node.get("description") or ""
                if desc:
                    hover_rect = self._node_rects[self._hover_id]
                    tip_surf = self.fonts.render(desc, 14, self.theme.text)
                    tip_x = max(self.rect.x + 4,
                                hover_rect.centerx - tip_surf.get_width() // 2)
                    tip_y = hover_rect.bottom + 6
                    # clamp to right edge
                    tip_x = min(tip_x, self.rect.right - tip_surf.get_width() - 4)
                    bg = pygame.Surface((tip_surf.get_width() + 12,
                                         tip_surf.get_height() + 8), pygame.SRCALPHA)
                    bg.fill((*self.theme.bg_overlay[:3], 210))
                    surface.blit(bg, (tip_x - 6, tip_y - 4))
                    surface.blit(tip_surf, (tip_x, tip_y))
