"""Exploration scene: see current location, NPCs, exits.

This is the "home" scene the player returns to after every dialogue. It
shows:
- the location background,
- a translucent panel listing description + exits + manual scene hooks,
- portraits of present NPCs along the bottom (clickable: opens the NPC
  action overlay — gift / shop / examine),
- a top bar with the consolidated menu button.
"""
from __future__ import annotations

from typing import Callable

import pygame

from .base import Scene, SceneContext
from ..ui.widgets import Button, Panel, WrappedText, Label
from ..core.map_system import Location, Exit


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
        self.on_open_npc: Callable[[str], None] | None = None
        self.on_start_scene: Callable[[str], None] | None = None
        self.on_move_to: Callable[[str], None] | None = None
        self.on_advance_time: Callable[[], None] | None = None
        self._info_panel: Panel | None = None
        # list of (button, description_str | None, is_available)
        self._exit_buttons: list[tuple[Button, str | None, bool]] = []

    def enter(self, *, on_map=None, on_affection=None, on_log=None,
              on_save=None, on_settings=None, on_menu=None,
              on_achievements=None, on_inventory=None,
              on_quit_to_title=None, on_open_npc=None,
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
        self.on_open_npc = on_open_npc
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
        self._exit_buttons = []
        flags = self.ctx.state.events.flags
        time_of_day = self.ctx.state.time.time_of_day.value
        scene_hooks = self.ctx.state.map.available_scenes(
            time_of_day=time_of_day,
            flags=flags,
            played_scenes=self.ctx.state.story.played,
        )
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

        # Exit buttons: show all exits; grey out unavailable ones rather than hiding.
        all_exits_info = self.ctx.state.map.all_exits_with_status(flags, time_of_day)
        exit_col_w = 200
        exit_col_h = 42
        exit_cols = 3
        exit_row_offset = start_y + len(all_actions) // cols * (col_h + 8) + (
            col_h + 16 if all_actions else 0
        )
        # Re-start exit layout below the action buttons
        exit_start_y = self._info_rect.y + 60 + (
            (len(all_actions) + exit_cols - 1) // exit_cols
        ) * (col_h + 8) if all_actions else self._info_rect.y + 60
        for i, (exit_obj, exit_loc, available, reason) in enumerate(all_exits_info):
            col = i % exit_cols
            row = i // exit_cols
            r = pygame.Rect(start_x + col * (exit_col_w + 8),
                            exit_start_y + row * (exit_col_h + 8),
                            exit_col_w, exit_col_h)
            # Use custom label if set, otherwise default arrow + name
            display_label = exit_obj.label or f"→ {exit_loc.name}"
            if available:
                btn = Button(r, display_label, fonts=self.ctx.fonts,
                             theme=self.ctx.theme, font_size=16,
                             on_click=(lambda lid=exit_loc.id: self._move(lid)))
            else:
                # Disabled style: ghost button, click pushes a toast
                # explaining why the player can't go there.
                hint_reason = reason or "目前無法前往"
                btn = Button(r, display_label, fonts=self.ctx.fonts,
                             theme=self.ctx.theme, font_size=16,
                             style="ghost",
                             on_click=(lambda label=exit_loc.name,
                                              why=hint_reason:
                                       self._notify_blocked(label, why)))
            # Store alongside description and availability for draw-time hint rendering
            desc = exit_obj.description or reason
            self._exit_buttons.append((btn, desc, available))
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

    def _notify_blocked(self, location_name: str, reason: str) -> None:
        """Queue a toast when a disabled exit is clicked, so the player
        knows the click registered and *why* it can't go through."""
        queue = self.ctx.state.meta.setdefault("__pending_toasts__", [])
        queue.append(("notice", location_name, reason))

    def update(self, dt: float, inp) -> None:
        for b in self._top_buttons:
            b.update(dt, inp)
        for b in self._buttons:
            b.update(dt, inp)
        for btn, _desc, _avail in self._exit_buttons:
            btn.update(dt, inp)
        # NPC card clicks
        if inp.mouse_clicked:
            for rect, nid in self._npc_cards:
                if rect.collidepoint(inp.mouse_pos) and self.on_open_npc:
                    self.on_open_npc(nid)
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
        # background: use time-of-day variant if available
        loc: Location | None = self.ctx.state.map.current
        if loc:
            time_val = self.ctx.state.time.time_of_day.value
            bg = self.ctx.assets.location_background(loc, time_val, size=(sw, sh))
            if bg:
                surface.blit(bg, (0, 0))
            else:
                surface.fill(self.ctx.theme.bg_deep)
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
        # Single-line layout: "錢包 $500   體力 100   學識 0" — labels and
        # values share the row, with a thin vertical divider between
        # different resources for legibility.
        rx = 24 + time_label.get_width() + 40
        resources = self.ctx.state.resources.visible_snapshot()
        for i, r in enumerate(resources):
            value_text = f"{r['symbol']}{r['value']}" if r["symbol"] \
                else str(r["value"])
            lbl = self.ctx.fonts.render(
                r["name"], self.ctx.config.font_size_small,
                self.ctx.theme.text_mute,
            )
            val = self.ctx.fonts.render(
                value_text, self.ctx.config.font_size_menu,
                self.ctx.theme.accent_warm, bold=True,
            )
            row_h = max(lbl.get_height(), val.get_height())
            row_y = (62 - row_h) // 2
            # Vertical baseline-ish align: name a touch higher than value
            # since fonts have different cap heights.
            lbl_y = row_y + (row_h - lbl.get_height()) // 2
            val_y = row_y + (row_h - val.get_height()) // 2
            surface.blit(lbl, (rx, lbl_y))
            rx += lbl.get_width() + 6
            surface.blit(val, (rx, val_y))
            rx += val.get_width() + 24
            # Thin separator before next resource
            if i < len(resources) - 1:
                pygame.draw.line(surface,
                                 (*self.ctx.theme.text_mute[:3], 110),
                                 (rx - 12, 18), (rx - 12, 44), 1)
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
        # exit buttons with optional description hint beneath
        for btn, desc, available in self._exit_buttons:
            btn.draw(surface)
            if desc:
                hint_color = (self.ctx.theme.text_mute if not available
                              else self.ctx.theme.text_mute)
                hint = self.ctx.fonts.render(desc, 13, hint_color)
                surface.blit(hint, (btn.rect.x + 4,
                                    btn.rect.bottom + 2))
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
        flags = self.ctx.state.events.flags
        time_of_day = self.ctx.state.time.time_of_day.value
        return {
            "scene": "ExplorationScene",
            "location": loc.id if loc else None,
            "location_name": loc.name if loc else None,
            "time": self.ctx.state.time.label(),
            "exits": [e.id for e in self.ctx.state.map.available_exits(flags, time_of_day)],
            "npcs_present": [n for _, n in self._npc_cards],
            "scenes_available": [
                h.scene_id for h in self.ctx.state.map.available_scenes(
                    time_of_day=self.ctx.state.time.time_of_day.value,
                    flags=self.ctx.state.events.flags,
                    played_scenes=self.ctx.state.story.played,
                )
            ],
        }
