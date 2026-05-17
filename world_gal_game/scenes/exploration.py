"""Exploration scene: see current location, NPCs, exits.

This is the "home" scene the player returns to after every dialogue. It
shows:
- the location background,
- a translucent panel listing description + exits + manual scene hooks,
- portraits of present NPCs along the bottom (clickable),
- a top bar with shortcut buttons for map / affection / log / save / chat.
"""
from __future__ import annotations

from typing import Callable

import pygame

from .base import Scene, SceneContext
from ..ui.widgets import Button, Panel, WrappedText, Label
from ..core.map_system import Location


class ExplorationScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = False
        self._buttons: list[Button] = []
        self._top_buttons: list[Button] = []
        self._npc_cards: list[tuple[pygame.Rect, str]] = []   # (rect, npc_id)
        self.on_map: Callable[[], None] | None = None
        self.on_affection: Callable[[], None] | None = None
        self.on_log: Callable[[], None] | None = None
        self.on_save: Callable[[], None] | None = None
        self.on_settings: Callable[[], None] | None = None
        self.on_menu: Callable[[], None] | None = None
        self.on_quit_to_title: Callable[[], None] | None = None
        self.on_open_chat: Callable[[str], None] | None = None
        self.on_start_scene: Callable[[str], None] | None = None
        self.on_move_to: Callable[[str], None] | None = None
        self.on_advance_time: Callable[[], None] | None = None
        self._info_panel: Panel | None = None

    def enter(self, *, on_map=None, on_affection=None, on_log=None,
              on_save=None, on_settings=None, on_menu=None,
              on_achievements=None, on_inventory=None,
              on_quit_to_title=None, on_open_chat=None,
              on_start_scene=None, on_move_to=None, on_advance_time=None,
              **_) -> None:
        self.on_map = on_map
        self.on_affection = on_affection
        self.on_log = on_log
        self.on_save = on_save
        self.on_settings = on_settings
        self.on_menu = on_menu
        self.on_achievements = on_achievements
        self.on_inventory = on_inventory
        self.on_quit_to_title = on_quit_to_title
        self.on_open_chat = on_open_chat
        self.on_start_scene = on_start_scene
        self.on_move_to = on_move_to
        self.on_advance_time = on_advance_time
        self._rebuild_widgets()

    def resume(self) -> None:
        self._rebuild_widgets()

    def _rebuild_widgets(self) -> None:
        sw, sh = self.ctx.screen_size
        # Top bar: only a single 選單 button on the right. Everything that
        # used to live up here (map / affection / log / save / settings /
        # leave) now lives inside the menu overlay. Keyboard shortcuts
        # (M / A / L / T / I / S / Esc) still work directly from this scene.
        self._top_buttons = []
        menu_btn = Button(
            pygame.Rect(sw - 130 - 24, 12, 130, 38),
            "選單 (Esc)", fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=16, style="ghost",
            on_click=getattr(self, "on_menu", None),
        )
        self._top_buttons.append(menu_btn)

        # info panel + action buttons
        panel_w = sw - 64
        panel_h = 220
        self._info_rect = pygame.Rect(32, sh - panel_h - 24, panel_w, panel_h)
        self._info_panel = Panel(self._info_rect, self.ctx.theme,
                                 fill=(*self.ctx.theme.bg_panel[:3], 230))
        self._buttons = []
        flags = self.ctx.state.events.flags
        exits = self.ctx.state.map.available_exits(flags)
        scene_hooks = self.ctx.state.map.available_scenes(
            time_of_day=self.ctx.state.time.time_of_day.value,
            flags=flags,
            played_scenes=self.ctx.state.story.played,
        )
        bx = self._info_rect.right - 16
        by = self._info_rect.y + 16
        # right-stack: action buttons
        all_actions: list[tuple[str, Callable[[], None]]] = []
        if self.on_advance_time:
            all_actions.append(("等下個時段", self.on_advance_time))
        for hook in scene_hooks:
            if hook.trigger != "examine":
                continue
            sc = self.ctx.state.story.get(hook.scene_id)
            if sc is None:
                continue
            label = sc.title or sc.id
            all_actions.append((label, (lambda sid=hook.scene_id:
                                         self._start_scene(sid))))
        # exits on the left of the buttons
        for exit_loc in exits:
            label = f"→ {exit_loc.name}"
            all_actions.append((label, (lambda lid=exit_loc.id:
                                          self._move(lid))))
        # Layout: 3-column grid bottom-aligned in the right half of the panel.
        col_w = 200
        col_h = 42
        cols = 3
        start_x = self._info_rect.right - col_w * cols - 24
        start_y = self._info_rect.y + 60
        for i, (label, cb) in enumerate(all_actions):
            col = i % cols
            row = i // cols
            r = pygame.Rect(start_x + col * (col_w + 8),
                            start_y + row * (col_h + 8),
                            col_w, col_h)
            self._buttons.append(Button(r, label, fonts=self.ctx.fonts,
                                        theme=self.ctx.theme,
                                        font_size=16,
                                        on_click=cb))
        # NPCs present
        self._npc_cards = []
        present_ids = self.ctx.state.map.present_npcs(
            self.ctx.state.time.time_of_day.value,
            self.ctx.state.time.day_of_week.value,
            flags,
        )
        npc_card_w, npc_card_h = 180, 56
        for i, nid in enumerate(present_ids):
            r = pygame.Rect(self._info_rect.x + 24 + i * (npc_card_w + 8),
                            self._info_rect.bottom - npc_card_h - 16,
                            npc_card_w, npc_card_h)
            self._npc_cards.append((r, nid))

    def _start_scene(self, scene_id: str) -> None:
        if self.on_start_scene:
            self.on_start_scene(scene_id)

    def _move(self, loc_id: str) -> None:
        if self.on_move_to:
            self.on_move_to(loc_id)

    def update(self, dt: float, inp) -> None:
        for b in self._top_buttons:
            b.update(dt, inp)
        for b in self._buttons:
            b.update(dt, inp)
        # NPC card clicks
        if inp.mouse_clicked:
            for rect, nid in self._npc_cards:
                if rect.collidepoint(inp.mouse_pos) and self.on_open_chat:
                    self.on_open_chat(nid)
                    return
        # keyboard shortcuts
        for e in inp.events:
            if e.type == pygame.KEYDOWN:
                if e.key == pygame.K_m and self.on_map:
                    self.on_map()
                elif e.key == pygame.K_a and self.on_affection:
                    self.on_affection()
                elif e.key == pygame.K_l and self.on_log:
                    self.on_log()
                elif e.key == pygame.K_s and self.on_save:
                    self.on_save()
                elif e.key == pygame.K_t and self.on_achievements:
                    self.on_achievements()
                elif e.key == pygame.K_i and self.on_inventory:
                    self.on_inventory()
                elif e.key == pygame.K_ESCAPE and self.on_menu:
                    self.on_menu()

    def draw(self, surface: pygame.Surface) -> None:
        sw, sh = surface.get_size()
        # background
        loc: Location | None = self.ctx.state.map.current
        if loc and loc.background:
            bg = self.ctx.assets.scaled(loc.background, (sw, sh), fit="cover")
            surface.blit(bg, (0, 0))
        else:
            surface.fill(self.ctx.theme.bg_deep)

        # darken bottom for panel readability
        veil = pygame.Surface((sw, sh // 2), pygame.SRCALPHA)
        for y in range(veil.get_height()):
            alpha = int(60 + (y / veil.get_height()) * 140)
            pygame.draw.line(veil, (0, 0, 0, alpha),
                             (0, y), (sw, y))
        surface.blit(veil, (0, sh // 2))

        # top bar (time + resources + menu button)
        top_bar = pygame.Surface((sw, 62), pygame.SRCALPHA)
        top_bar.fill((10, 6, 20, 200))
        surface.blit(top_bar, (0, 0))
        time_label = self.ctx.fonts.render(
            self.ctx.state.time.label(),
            self.ctx.config.font_size_menu,
            self.ctx.theme.accent_warm,
        )
        surface.blit(time_label, (24, (62 - time_label.get_height()) // 2))
        # Resources (e.g. money, energy) shown right of the time label.
        rx = 24 + time_label.get_width() + 32
        resources = self.ctx.state.resources.visible_snapshot()
        for r in resources:
            value_text = f"{r['symbol']}{r['value']}" if r["symbol"] \
                else str(r["value"])
            # name in mute, value in accent_warm — same visual rhythm.
            lbl = self.ctx.fonts.render(
                r["name"], self.ctx.config.font_size_small,
                self.ctx.theme.text_mute,
            )
            val = self.ctx.fonts.render(
                value_text, self.ctx.config.font_size_menu,
                self.ctx.theme.accent_warm, bold=True,
            )
            lbl_y = (62 - lbl.get_height()) // 2 - 8
            val_y = (62 - val.get_height()) // 2 + 8
            surface.blit(lbl, (rx, lbl_y))
            surface.blit(val, (rx, val_y))
            rx += max(lbl.get_width(), val.get_width()) + 28
        for b in self._top_buttons:
            b.draw(surface)

        # info panel
        if self._info_panel is not None:
            self._info_panel.draw(surface)
        if loc is not None:
            name = self.ctx.fonts.render(
                loc.name, self.ctx.config.font_size_header,
                self.ctx.theme.accent, bold=True,
            )
            surface.blit(name, (self._info_rect.x + 22,
                                self._info_rect.y + 14))
            if loc.region:
                reg = self.ctx.fonts.render(
                    loc.region, self.ctx.config.font_size_small,
                    self.ctx.theme.text_mute,
                )
                surface.blit(reg, (self._info_rect.x + 24 + name.get_width(),
                                   self._info_rect.y + 14 + (name.get_height() - reg.get_height())))
            desc_rect = pygame.Rect(
                self._info_rect.x + 22,
                self._info_rect.y + 18 + name.get_height(),
                int(self._info_rect.width * 0.45),
                self._info_rect.height - 30 - name.get_height() - 70,
            )
            desc = WrappedText(desc_rect, loc.description,
                               fonts=self.ctx.fonts, size=18,
                               color=self.ctx.theme.text_mute)
            desc.draw(surface)
        # action buttons
        for b in self._buttons:
            b.draw(surface)
        # NPC cards
        for rect, nid in self._npc_cards:
            npc = self.ctx.npcs.get(nid)
            if npc is None:
                continue
            card = pygame.Surface(rect.size, pygame.SRCALPHA)
            pygame.draw.rect(card, (*self.ctx.theme.accent[:3], 50),
                             card.get_rect(), border_radius=self.ctx.theme.radius_m)
            pygame.draw.rect(card, self.ctx.theme.border,
                             card.get_rect(), width=1,
                             border_radius=self.ctx.theme.radius_m)
            surface.blit(card, rect.topleft)
            # portrait thumb
            if npc.portrait:
                img = self.ctx.assets.scaled(npc.portrait, (44, 44),
                                             fit="cover")
                surface.blit(img, (rect.x + 6, rect.y + 6))
            nx = rect.x + 56
            name = self.ctx.fonts.render(npc.name, 18,
                                         self.ctx.theme.text, bold=True)
            surface.blit(name, (nx, rect.y + 4))
            if npc.is_heroine:
                aff = self.ctx.state.affection.get(npc.id)
                lvl = self.ctx.state.affection.level_label(npc.id)
                sub = self.ctx.fonts.render(f"好感 {lvl} · {aff}", 14,
                                            self.ctx.theme.accent_warm)
                surface.blit(sub, (nx, rect.y + 26))
            else:
                role = self.ctx.fonts.render(npc.role or "", 14,
                                             self.ctx.theme.text_mute)
                surface.blit(role, (nx, rect.y + 26))

    def describe(self) -> dict:
        loc = self.ctx.state.map.current
        return {
            "scene": "ExplorationScene",
            "location": loc.id if loc else None,
            "location_name": loc.name if loc else None,
            "time": self.ctx.state.time.label(),
            "exits": [e.id for e in self.ctx.state.map.available_exits(
                self.ctx.state.events.flags)],
            "npcs_present": [n for _, n in self._npc_cards],
            "scenes_available": [
                h.scene_id for h in self.ctx.state.map.available_scenes(
                    time_of_day=self.ctx.state.time.time_of_day.value,
                    flags=self.ctx.state.events.flags,
                    played_scenes=self.ctx.state.story.played,
                )
            ],
        }
