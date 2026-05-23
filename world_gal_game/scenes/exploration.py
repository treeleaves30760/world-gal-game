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
        self._ui_hidden = False
        self._buttons: list[Button] = []
        self._top_buttons: list[Button] = []
        self._npc_cards: list[tuple[pygame.Rect, str]] = []   # (rect, npc_id)
        self.on_map: Callable[[], None] | None = None
        self.on_affection: Callable[[], None] | None = None
        self.on_log: Callable[[], None] | None = None
        self.on_save: Callable[[], None] | None = None
        self.on_settings: Callable[[], None] | None = None
        self.on_menu: Callable[[], None] | None = None
        self.on_clues: Callable[[], None] | None = None
        self.on_quit_to_title: Callable[[], None] | None = None
        self.on_open_npc: Callable[[str], None] | None = None
        self.on_start_scene: Callable[[str], None] | None = None
        self.on_move_to: Callable[[str], None] | None = None
        self.on_advance_time: Callable[[], None] | None = None
        self._info_panel: Panel | None = None
        # list of (button, description_str | None, is_available)
        self._exit_buttons: list[tuple[Button, str | None, bool]] = []
        # State signature the widgets were last built for. The exit buttons,
        # action buttons, and NPC cards all depend on (current location,
        # time-of-day, flags, played scenes); when any changes under us
        # (e.g. exit-button move with no scene hook, "等下個時段"), we must
        # rebuild — otherwise stale lambdas point at the old location.
        self._build_signature: tuple | None = None

    def enter(self, *, on_map=None, on_affection=None, on_log=None,
              on_save=None, on_settings=None, on_menu=None,
              on_achievements=None, on_inventory=None,
              on_quit_to_title=None, on_open_npc=None,
              on_start_scene=None, on_move_to=None, on_advance_time=None,
              on_clues=None,
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
        self.on_clues = on_clues
        self._rebuild_widgets()

    def resume(self) -> None:
        self._rebuild_widgets()

    def _current_signature(self) -> tuple:
        flags = self.ctx.state.events.flags
        return (
            self.ctx.state.map.current_location_id,
            self.ctx.state.time.time_of_day.value,
            frozenset(flags.items()),
            frozenset(self.ctx.state.story.played),
            # Unread clue count: the 線索 top-bar button changes label
            # and style when this changes, so the rebuild needs to fire.
            self.ctx.state.clues.unread_count(),
        )

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
        # Dedicated 線索 button next to the menu so the journal is
        # one click away. Unread count rides in the label.
        clue_unread = self.ctx.state.clues.unread_count()
        clue_label = "線索 (J)"
        if clue_unread > 0:
            clue_label = f"線索 (J) ·{clue_unread}"
        clue_btn = Button(
            pygame.Rect(sw - 130 - 24 - 150, 12, 140, 38),
            clue_label, fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=16,
            style=("primary" if clue_unread > 0 else "ghost"),
            on_click=getattr(self, "on_clues", None),
        )
        self._top_buttons.append(clue_btn)

        # Collect what needs rendering before we can size the panel.
        flags = self.ctx.state.events.flags
        time_of_day = self.ctx.state.time.time_of_day.value
        scene_hooks = self.ctx.state.map.available_scenes(
            time_of_day=time_of_day,
            flags=flags,
            played_scenes=self.ctx.state.story.played,
        )
        actions: list[tuple[str, Callable[[], None]]] = []
        if self.on_advance_time:
            actions.append(("等下個時段", self.on_advance_time))
        for hook in scene_hooks:
            if hook.trigger != "examine":
                continue
            sc = self.ctx.state.story.get(hook.scene_id)
            if sc is None:
                continue
            label = sc.title or sc.id
            actions.append((label, (lambda sid=hook.scene_id:
                                       self._start_scene(sid))))
        all_exits_info = self.ctx.state.map.all_exits_with_status(flags, time_of_day)
        present_ids = self.ctx.state.map.present_npcs(
            time_of_day,
            self.ctx.state.time.day_of_week.value,
            flags,
        )
        # Reserve the NPC row if the location *can* host an NPC (even if
        # none is present right now). Keeps button screen positions stable
        # across time-of-day transitions so the player doesn't see a layout
        # shift when an NPC walks in or out.
        cur_loc = self.ctx.state.map.current
        reserve_npc_slot = bool(cur_loc and cur_loc.npcs)

        # ---- Adaptive layout ------------------------------------------------
        # Old design: 3 fixed 200px-wide columns in the right half of a
        # 220px panel. That overflowed once any location had more than 9
        # exits (e.g. front_lawn now has 10). New design uses the full
        # panel width with column count and panel height that adapt to
        # actual content count.

        panel_w = sw - 64
        PAD_X = 28
        PAD_Y = 14
        BTN_H = 40
        BTN_GAP_X = 8
        BTN_GAP_Y = 8
        SECTION_GAP = 12          # vertical gap between sections
        SECTION_LABEL_H = 22
        NAME_H = 36
        NAME_DESC_GAP = 8
        DESC_LINE_H = 22
        DESC_LINES = 2
        NPC_CARD_W, NPC_CARD_H = 180, 56

        n_exits = len(all_exits_info)
        n_actions = len(actions)
        # Column count scales with exit count. Stay in [3, 6] so labels
        # don't get squashed but we don't waste rows either.
        if n_exits >= 11:
            cols = 6
        elif n_exits >= 7:
            cols = 5
        elif n_exits >= 4:
            cols = 4
        else:
            cols = max(2, max(n_exits, n_actions))

        btn_area_w = panel_w - PAD_X * 2
        btn_w = max(120,
                    (btn_area_w - BTN_GAP_X * (cols - 1)) // cols)

        action_rows = (n_actions + cols - 1) // cols if n_actions else 0
        exit_rows = (n_exits + cols - 1) // cols if n_exits else 0
        npc_rows = 1 if reserve_npc_slot else 0

        # Total height = name + gap + desc + sections + npc + paddings.
        needed_h = PAD_Y + NAME_H + NAME_DESC_GAP \
                   + DESC_LINES * DESC_LINE_H + SECTION_GAP
        if action_rows:
            needed_h += SECTION_LABEL_H + action_rows * BTN_H \
                        + max(0, action_rows - 1) * BTN_GAP_Y \
                        + SECTION_GAP
        if exit_rows:
            needed_h += SECTION_LABEL_H + exit_rows * BTN_H \
                        + max(0, exit_rows - 1) * BTN_GAP_Y
        if npc_rows:
            needed_h += SECTION_GAP + NPC_CARD_H
        needed_h += PAD_Y

        # Cap so the top bar (~62) and some background stays visible.
        max_panel_h = sh - 62 - 60
        panel_h = min(max(needed_h, 180), max_panel_h)

        self._info_rect = pygame.Rect(32, sh - panel_h - 24, panel_w, panel_h)
        self._info_panel = Panel(self._info_rect, self.ctx.theme,
                                 fill=(*self.ctx.theme.bg_panel[:3], 230))

        # ---- Place widgets (BOTTOM-ANCHORED) --------------------------------
        # Anchor strategy: the bottom of every interactive widget is a
        # stable function of screen height — exit row sits at a fixed
        # screen y, action row directly above it, NPC cards at the very
        # bottom of the panel. The name/description strip grows UP into
        # the empty space above. This keeps button click targets stable
        # across rebuilds (NPC appearance, time changes, etc.) so the
        # driver-style "find widget then click" pattern doesn't get
        # tripped by a panel resize between the find and the click.

        self._buttons = []
        self._exit_buttons = []
        x_start = self._info_rect.x + PAD_X

        # Bottom up: paddings -> NPCs -> exits -> actions -> header.
        cursor_bottom = self._info_rect.bottom - PAD_Y
        if reserve_npc_slot:
            cursor_bottom -= NPC_CARD_H
            self._npc_cards_y = cursor_bottom
            cursor_bottom -= SECTION_GAP
        else:
            self._npc_cards_y = None

        if exit_rows:
            exits_block_h = exit_rows * BTN_H \
                            + max(0, exit_rows - 1) * BTN_GAP_Y
            exits_top_y = cursor_bottom - exits_block_h
            self._exit_label_y = exits_top_y - SECTION_LABEL_H + 2
            for i, (exit_obj, exit_loc, available, reason) in \
                    enumerate(all_exits_info):
                row = i // cols
                col = i % cols
                r = pygame.Rect(x_start + col * (btn_w + BTN_GAP_X),
                                exits_top_y + row * (BTN_H + BTN_GAP_Y),
                                btn_w, BTN_H)
                display_label = exit_obj.label or f"→ {exit_loc.name}"
                if available:
                    btn = Button(r, display_label, fonts=self.ctx.fonts,
                                 theme=self.ctx.theme, font_size=15,
                                 on_click=(lambda lid=exit_loc.id:
                                            self._move(lid)))
                else:
                    hint_reason = reason or "目前無法前往"
                    btn = Button(r, display_label, fonts=self.ctx.fonts,
                                 theme=self.ctx.theme, font_size=15,
                                 style="ghost",
                                 on_click=(lambda label=exit_loc.name,
                                                  why=hint_reason:
                                           self._notify_blocked(label, why)))
                desc = exit_obj.description or reason
                self._exit_buttons.append((btn, desc, available))
            cursor_bottom = exits_top_y - SECTION_LABEL_H - SECTION_GAP
        else:
            self._exit_label_y = None

        if action_rows:
            actions_block_h = action_rows * BTN_H \
                              + max(0, action_rows - 1) * BTN_GAP_Y
            actions_top_y = cursor_bottom - actions_block_h
            self._action_label_y = actions_top_y - SECTION_LABEL_H + 2
            for i, (label, cb) in enumerate(actions):
                row = i // cols
                col = i % cols
                r = pygame.Rect(x_start + col * (btn_w + BTN_GAP_X),
                                actions_top_y + row * (BTN_H + BTN_GAP_Y),
                                btn_w, BTN_H)
                self._buttons.append(Button(
                    r, label, fonts=self.ctx.fonts, theme=self.ctx.theme,
                    font_size=15, on_click=cb,
                ))
        else:
            self._action_label_y = None

        # Header (name + description) lives in the top strip of the panel.
        self._layout = {
            "name_y":      self._info_rect.y + PAD_Y,
            "desc_y":      self._info_rect.y + PAD_Y + NAME_H + NAME_DESC_GAP,
            "desc_w":      panel_w - PAD_X * 2,
            "desc_lines":  DESC_LINES,
            "desc_line_h": DESC_LINE_H,
            "action_label_y": self._action_label_y,
            "exit_label_y":   self._exit_label_y,
        }

        self._npc_cards = []
        if present_ids and self._npc_cards_y is not None:
            for i, nid in enumerate(present_ids):
                r = pygame.Rect(x_start + i * (NPC_CARD_W + 8),
                                self._npc_cards_y, NPC_CARD_W, NPC_CARD_H)
                self._npc_cards.append((r, nid))
        self._build_signature = self._current_signature()

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
        # If state mutated under us without an overlay round-trip (e.g. a
        # move into a location with no auto/enter hook, or 等下個時段),
        # rebuild before processing input so stale button lambdas don't
        # silently swallow clicks.
        if self._build_signature != self._current_signature():
            self._rebuild_widgets()
        # Hide-UI (非表示): H toggles; while hidden a click/cancel restores it,
        # so the player can see the full location image unobstructed.
        for e in inp.events:
            if e.type == pygame.KEYDOWN and e.key == pygame.K_h:
                self._ui_hidden = not self._ui_hidden
                return
        if self._ui_hidden:
            if inp.mouse_clicked or inp.cancel:
                self._ui_hidden = False
            return
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
                elif e.key == pygame.K_j and self.on_clues:
                    self.on_clues()
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

        # Hide-UI: show only the full location image (no bars / panel / veil).
        if self._ui_hidden:
            self._draw_hidden_hint(surface)
            return

        # darken bottom for panel readability
        veil = pygame.Surface((sw, sh // 2), pygame.SRCALPHA)
        for y in range(veil.get_height()):
            alpha = int(60 + (y / veil.get_height()) * 140)
            pygame.draw.line(veil, (0, 0, 0, alpha),
                             (0, y), (sw, y))
        surface.blit(veil, (0, sh // 2))

        # top bar (time + resources + menu button)
        top_bar = pygame.Surface((sw, 62), pygame.SRCALPHA)
        top_bar.fill((10, 6, 20, 165))
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
            # Name + region badge — top strip, full panel width.
            name = self.ctx.fonts.render(
                loc.name, self.ctx.config.font_size_header,
                self.ctx.theme.accent, bold=True,
            )
            surface.blit(name, (self._info_rect.x + 28,
                                self._layout["name_y"]))
            if loc.region:
                reg = self.ctx.fonts.render(
                    loc.region, self.ctx.config.font_size_small,
                    self.ctx.theme.text_mute,
                )
                surface.blit(reg, (self._info_rect.x + 32 + name.get_width(),
                                   self._layout["name_y"]
                                   + (name.get_height() - reg.get_height())))
            # Description — full panel width, capped to N lines so it
            # never crowds out the buttons.
            desc_rect = pygame.Rect(
                self._info_rect.x + 28,
                self._layout["desc_y"],
                self._layout["desc_w"],
                self._layout["desc_lines"] * self._layout["desc_line_h"],
            )
            desc = WrappedText(desc_rect, loc.description,
                               fonts=self.ctx.fonts, size=15,
                               color=self.ctx.theme.text_mute)
            desc.draw(surface)
        # Section labels — "動作" above the action row, "前往" above exits.
        section_color = self.ctx.theme.text_mute
        if self._layout.get("action_label_y") is not None and self._buttons:
            lbl = self.ctx.fonts.render("動作", 14, section_color, bold=True)
            surface.blit(lbl, (self._info_rect.x + 28,
                               self._layout["action_label_y"]))
        if self._layout.get("exit_label_y") is not None \
                and self._exit_buttons:
            lbl = self.ctx.fonts.render("前往", 14, section_color, bold=True)
            surface.blit(lbl, (self._info_rect.x + 28,
                               self._layout["exit_label_y"]))
        # action buttons
        for b in self._buttons:
            b.draw(surface)
        # exit buttons with optional description hint underneath
        for btn, desc, available in self._exit_buttons:
            btn.draw(surface)
            if desc:
                hint = self.ctx.fonts.render(desc, 11,
                                             self.ctx.theme.text_mute)
                surface.blit(hint, (btn.rect.x + 4,
                                    btn.rect.bottom + 1))
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

    def _draw_hidden_hint(self, surface: pygame.Surface) -> None:
        sw, sh = surface.get_size()
        hint = self.ctx.fonts.render(
            "點擊 / 按 H 顯示介面", 14,
            (*self.ctx.theme.text_mute[:3], 150))
        surface.blit(hint, (sw - hint.get_width() - 20,
                            sh - hint.get_height() - 16))

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
