"""Map overlay scene."""
from __future__ import annotations

from typing import Callable

import pygame

from .base import Scene, SceneContext
from ..ui.widgets import Button, Panel, MapView


class MapScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        self.map_view: MapView | None = None
        self.close_btn: Button | None = None
        self.on_close: Callable[[], None] | None = None
        self.on_move_to: Callable[[str], None] | None = None

    def enter(self, *, on_close=None, on_move_to=None, **_) -> None:
        self.on_close = on_close
        self.on_move_to = on_move_to
        sw, sh = self.ctx.screen_size
        panel_rect = pygame.Rect(32, 32, sw - 64, sh - 64)
        self._panel = Panel(panel_rect, self.ctx.theme,
                            fill=(*self.ctx.theme.bg_overlay[:3], 235),
                            border=self.ctx.theme.border_strong,
                            radius=self.ctx.theme.radius_l, border_width=2)
        inner = panel_rect.inflate(-60, -100)
        inner.move_ip(0, 30)
        self.map_view = MapView(inner, fonts=self.ctx.fonts,
                                theme=self.ctx.theme,
                                on_click=self._on_node_click)
        self.close_btn = Button(
            pygame.Rect(panel_rect.right - 120 - 16, panel_rect.y + 16, 120, 36),
            "關閉 (Esc)", fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=15, style="ghost",
            on_click=(lambda: on_close() if on_close else None),
        )
        self._refresh()

    def resume(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        flags = self.ctx.state.events.flags
        time_of_day = self.ctx.state.time.time_of_day.value
        cur = self.ctx.state.map.current
        reachable = {l.id for l in self.ctx.state.map.available_exits(flags, time_of_day)}
        regions = self.ctx.state.map.regions
        nodes = []
        for loc in self.ctx.state.map.locations.values():
            region_color = None
            if loc.region and loc.region in regions:
                region_color = regions[loc.region].color
            nodes.append({
                "id": loc.id,
                "name": loc.name,
                "region": loc.region,
                "region_name": regions[loc.region].name if loc.region and loc.region in regions else loc.region,
                "region_color": region_color,
                "description": loc.description,
                "map_x": loc.map_x,
                "map_y": loc.map_y,
                "exits": loc.exit_targets,
                "visited": loc.id in self.ctx.state.map.visited,
                "accessible": loc.is_accessible(flags),
            })
        self.map_view.set_data(nodes=nodes,
                               current_id=cur.id if cur else None,
                               reachable_ids=reachable)

    def _on_node_click(self, loc_id: str) -> None:
        if loc_id == (self.ctx.state.map.current.id
                      if self.ctx.state.map.current else None):
            if self.on_close:
                self.on_close()
            return
        if self.on_move_to:
            self.on_move_to(loc_id)

    def update(self, dt: float, inp) -> None:
        if inp.cancel and self.on_close:
            self.on_close()
            return
        if self.close_btn:
            self.close_btn.update(dt, inp)
        if self.map_view:
            self.map_view.update(dt, inp)

    def draw(self, surface: pygame.Surface) -> None:
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 160))
        surface.blit(veil, (0, 0))
        if self._panel:
            self._panel.draw(surface)
        title = self.ctx.fonts.render("地圖",
                                      self.ctx.config.font_size_header,
                                      self.ctx.theme.accent, bold=True)
        surface.blit(title, (64, 50))
        hint = self.ctx.fonts.render("點擊可前往的地點 (粉色 = 直接可達，灰色 = 未訪問/不可達，? = 未探索)",
                                     self.ctx.config.font_size_small,
                                     self.ctx.theme.text_mute)
        surface.blit(hint, (64, 90))
        if self.map_view:
            self.map_view.draw(surface)
        if self.close_btn:
            self.close_btn.draw(surface)

    def describe(self) -> dict:
        cur = self.ctx.state.map.current
        flags = self.ctx.state.events.flags
        time_of_day = self.ctx.state.time.time_of_day.value
        return {"scene": "MapScene",
                "current": cur.id if cur else None,
                "exits": [e.id for e in
                          self.ctx.state.map.available_exits(flags, time_of_day)]}
