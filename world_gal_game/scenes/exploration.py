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

from collections.abc import Callable, Mapping

import pygame

from .base import Scene, SceneContext
from ..ui.input_bindings import BindingProfile, InputAction
from ..ui.widgets import Button, Panel, WrappedText
from ..core.guided_destinations import GuidedDestinationResolver
from ..core.map_system import Location
from ..core.travel import hidden_travel_tags


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
        self.get_advance_time_availability: (
            Callable[[], tuple[bool, str | None]] | None
        ) = None
        self.get_map_availability: (
            Callable[[], tuple[bool, str | None]] | None
        ) = None
        self._info_panel: Panel | None = None
        self._action_hints: list[tuple[Button, str]] = []
        self._action_focus_ids: list[str] = []
        self._hovered_action_hint: tuple[Button, str] | None = None
        self._focus_id: str | None = None
        self._focus_visible = False
        self._objective_text: str | None = None
        # Single "行程地圖" call-to-action that opens the destination picker.
        self._travel_btn: Button | None = None
        self._discovery_text: str | None = None
        self._discovery_pos: tuple[int, int] = (0, 0)
        # Retained (always empty now) so any introspection that scans
        # `_exit_buttons` still finds an iterable. The flat exit grid moved
        # into the card-based DestinationPickerScene.
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
              on_clues=None, get_advance_time_availability=None,
              get_map_availability=None,
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
        self.get_advance_time_availability = get_advance_time_availability
        self.get_map_availability = get_map_availability
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
        previous_focus = self._focus_id
        # Top bar: only a single 選單 button on the right. Everything that
        # used to live up here (map / affection / log / save / settings /
        # leave) now lives inside the menu overlay. Semantic shortcuts still
        # work directly from this scene and their visible keycaps follow the
        # player's live binding profile.
        self._top_buttons = []
        bindings = BindingProfile(
            getattr(self.ctx.config, "input_bindings", {}),
        )
        top_button_h = max(
            44,
            self.ctx.fonts.get(16, bold=True).get_height() + 14,
        )
        top_button_y = max(4, (62 - top_button_h) // 2)
        menu_label = self.ctx.t(
            "menu_item_with_binding",
            "{label} ({binding})",
            label=self.ctx.t("menu_short", "選單"),
            binding=bindings.primary_display(InputAction.OPEN_MENU),
        )
        menu_w = max(
            130,
            self.ctx.fonts.render(
                menu_label, 16, self.ctx.theme.text, bold=True,
            ).get_width() + 24,
        )
        menu_btn = Button(
            pygame.Rect(
                sw - menu_w - 24,
                top_button_y,
                menu_w,
                top_button_h,
            ),
            menu_label, fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=16, style="ghost",
            on_click=getattr(self, "on_menu", None),
            motion=self.ctx.motion,
        )
        # Dedicated 線索 button next to the menu so the journal is
        # one click away. Unread count rides in the label.
        clue_unread = self.ctx.state.clues.unread_count()
        clue_label = self.ctx.t(
            (
                "menu_item_with_binding_unread"
                if clue_unread > 0
                else "menu_item_with_binding"
            ),
            (
                "{label} ({binding}) · 新 {count}"
                if clue_unread > 0
                else "{label} ({binding})"
            ),
            label=self.ctx.t("clues", "線索"),
            binding=bindings.primary_display(InputAction.OPEN_CLUES),
            count=clue_unread,
        )
        clue_w = max(
            140,
            self.ctx.fonts.render(
                clue_label, 16, self.ctx.theme.text, bold=True,
            ).get_width() + 24,
        )
        clue_btn = Button(
            pygame.Rect(
                menu_btn.rect.x - clue_w - 10,
                top_button_y,
                clue_w,
                top_button_h,
            ),
            clue_label, fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=16,
            style="ghost",
            on_click=getattr(self, "on_clues", None),
            motion=self.ctx.motion,
        )
        # Keep tab order aligned with the left-to-right visual order.
        self._top_buttons.extend((clue_btn, menu_btn))

        # Collect what needs rendering before we can size the panel.
        flags = self.ctx.state.events.flags
        time_of_day = self.ctx.state.time.time_of_day.value
        scene_hooks = self.ctx.state.map.available_scenes(
            time_of_day=time_of_day,
            flags=flags,
            played_scenes=self.ctx.state.story.played,
            state=self.ctx.state,
        )
        actions: list[
            tuple[
                str,
                str,
                Callable[[], None] | None,
                bool,
                str | None,
            ]
        ] = []
        if self.on_advance_time:
            advance_enabled = True
            advance_reason = None
            if self.get_advance_time_availability is not None:
                try:
                    advance_enabled, advance_reason = (
                        self.get_advance_time_availability()
                    )
                except Exception:
                    # Optional guidance must never make exploration unusable.
                    advance_enabled, advance_reason = True, None
            actions.append((
                "action:wait",
                self.ctx.t("advance_time", "等下個時段"),
                self.on_advance_time if advance_enabled else None,
                bool(advance_enabled),
                advance_reason,
            ))
        for hook in scene_hooks:
            if hook.trigger != "examine":
                continue
            sc = self.ctx.state.story.get(hook.scene_id)
            if sc is None:
                continue
            label = sc.title or sc.id
            actions.append((
                f"action:scene:{hook.scene_id}",
                label,
                (lambda sid=hook.scene_id: self._start_scene(sid)),
                True,
                None,
            ))
        self._objective_text = self._current_objective()
        all_exits_info = self.ctx.state.map.all_exits_with_status(
            flags, time_of_day,
        )
        default_reachable = bool(
            self.ctx.meta.get("travel_default_reachable_only", False)
        )
        reachable_only = bool(self.ctx.state.meta.get(
            "__travel_filter_reachable__",
            default_reachable,
        ))
        visible_exits_info = (
            [info for info in all_exits_info if info[2]]
            if reachable_only else all_exits_info
        )
        map_enabled, map_reason = self._map_availability()
        if not map_enabled and self.on_move_to is not None:
            # A pack may lock the *whole* map during a directed opening while
            # still authoring one explicit, adjacent tutorial step. Preserve
            # those zero-cost exits as ordinary actions; otherwise removing
            # the old flat exit grid would leave the player unable to reach
            # the very flag that unlocks the map.
            for exit_obj, exit_loc, available, _reason in visible_exits_info:
                if not available or exit_obj.travel_cost != 0:
                    continue
                actions.append((
                    f"action:local:{exit_loc.id}",
                    self.ctx.t(
                        "go_to_location",
                        "前往{location}",
                        location=exit_loc.name,
                    ),
                    lambda lid=exit_loc.id: self._move(lid),
                    True,
                    None,
                ))
        guided_resolver = GuidedDestinationResolver(
            self.ctx.state,
            self.ctx.meta,
        )
        raw_travel_policy = self.ctx.meta.get("travel")
        guided_story_authored = (
            isinstance(raw_travel_policy, Mapping)
            and isinstance(raw_travel_policy.get("guided_story"), Mapping)
        )
        guided_primary = (
            guided_resolver.primary() if guided_resolver.enabled else ()
        )
        if guided_resolver.enabled:
            hidden_tags = hidden_travel_tags(self.ctx.meta)
            current_id = self.ctx.state.map.current_location_id
            guided_destination_count = sum(
                location.id != current_id
                and not hidden_tags.intersection(location.tags)
                for location in self.ctx.state.map.locations.values()
            )
        else:
            guided_destination_count = 0
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

        # ---- Layout ---------------------------------------------------------
        # The flat multi-column exit grid was replaced by a single "行程地圖"
        # call-to-action that opens the card-based destination picker, so
        # the player chooses where to go from a richer view (who's there,
        # what's new, travel cost). Sections, bottom-up: NPC cards, the
        # travel CTA, the action buttons, then the name/description strip.

        panel_w = sw - 64
        PAD_X = 28
        PAD_Y = 14
        fonts = self.ctx.fonts
        BTN_H = max(44, fonts.get(15, bold=True).get_height() + 14)
        BTN_GAP_X = 8
        BTN_GAP_Y = 8
        SECTION_GAP = 12          # vertical gap between sections
        # Scale-aware heights: FontRegistry multiplies point size by
        # screen_h/720, so the title / description / label fonts grow with
        # resolution. Reserve their real rendered heights instead of fixed
        # constants — otherwise the description overlaps the title at 1080p+.
        SECTION_LABEL_H = fonts.get(14, bold=True).get_height() + 6
        NAME_H = fonts.get(self.ctx.config.font_size_header,
                           bold=True).get_height() + 2
        NAME_DESC_GAP = 8
        DESC_LINE_H = fonts.get(15).get_height() + 4
        DESC_LINES = 2
        ui_scale = max(1.0, sh / 720)
        NPC_CARD_W = round(180 * min(ui_scale, 1.5))
        self._npc_thumb_size = round(44 * min(ui_scale, 1.5))
        NPC_CARD_H = max(
            56,
            round(56 * min(ui_scale, 1.5)),
            self._npc_thumb_size + 12,
            fonts.get(18, bold=True).get_height()
            + fonts.get(14).get_height()
            + 14,
        )
        TRAVEL_BTN_H = max(
            48,
            fonts.get(17, bold=True).get_height() + 16,
        )

        n_actions = len(actions)
        # Action buttons keep the adaptive column behaviour.
        if n_actions >= 7:
            cols = 4
        elif n_actions >= 4:
            cols = 3
        else:
            cols = max(2, n_actions) if n_actions else 2

        btn_area_w = panel_w - PAD_X * 2
        btn_w = max(120,
                    (btn_area_w - BTN_GAP_X * (cols - 1)) // cols)

        action_rows = (n_actions + cols - 1) // cols if n_actions else 0
        npc_cols = max(1, (btn_area_w + 8) // (NPC_CARD_W + 8))
        npc_slot_count = (
            max(1, len(cur_loc.npcs))
            if reserve_npc_slot and cur_loc is not None else 0
        )
        npc_rows = (
            (npc_slot_count + npc_cols - 1) // npc_cols
            if npc_slot_count else 0
        )

        # Count reachable destinations worth a look (someone present or a
        # fresh scene hook) so the CTA can hint "● 有新發現".
        discovery_count = 0
        wk = self.ctx.state.time.day_of_week.value
        if guided_resolver.enabled:
            discovery_count = len(guided_primary)
        else:
            for _ex, exit_loc, available, _reason in visible_exits_info:
                if not available:
                    continue
                if self.ctx.state.map.npcs_present_at(
                        exit_loc, time_of_day, wk, flags):
                    discovery_count += 1
                elif self.ctx.state.map.scenes_available_at(
                        exit_loc, time_of_day=time_of_day, flags=flags,
                        played_scenes=self.ctx.state.story.played,
                        state=self.ctx.state):
                    discovery_count += 1

        # Total height = name + gap + desc + sections + npc + paddings.
        needed_h = PAD_Y + NAME_H + NAME_DESC_GAP \
                   + DESC_LINES * DESC_LINE_H + SECTION_GAP
        if action_rows:
            needed_h += SECTION_LABEL_H + action_rows * BTN_H \
                        + max(0, action_rows - 1) * BTN_GAP_Y \
                        + SECTION_GAP
        # Travel section (label + one CTA button) is always present.
        needed_h += SECTION_LABEL_H + TRAVEL_BTN_H
        if npc_rows:
            needed_h += (
                SECTION_GAP
                + npc_rows * NPC_CARD_H
                + (npc_rows - 1) * 8
            )
        needed_h += PAD_Y

        # Cap so the top bar (~62) and some background stays visible.
        max_panel_h = sh - 62 - 60
        panel_h = min(max(needed_h, 180), max_panel_h)

        self._info_rect = pygame.Rect(32, sh - panel_h - 24, panel_w, panel_h)
        self._info_panel = Panel(self._info_rect, self.ctx.theme,
                                 fill=(*self.ctx.theme.bg_panel[:3], 230))

        # ---- Place widgets (BOTTOM-ANCHORED) --------------------------------
        # Anchor strategy: the bottom of every interactive widget is a
        # stable function of screen height — the travel CTA sits at a fixed
        # screen y, action rows directly above it, NPC cards at the very
        # bottom of the panel. The name/description strip grows UP into the
        # empty space above. This keeps click targets stable across rebuilds
        # (NPC appearance, time changes, etc.).

        self._buttons = []
        self._action_hints = []
        self._action_focus_ids = []
        self._hovered_action_hint = None
        # Kept (empty) so the dev driver / older introspection that scans
        # `_exit_buttons` finds an iterable rather than a missing attribute.
        self._exit_buttons = []
        x_start = self._info_rect.x + PAD_X

        # Bottom up: paddings -> NPCs -> travel CTA -> actions -> header.
        cursor_bottom = self._info_rect.bottom - PAD_Y
        if reserve_npc_slot:
            npc_block_h = npc_rows * NPC_CARD_H + (npc_rows - 1) * 8
            cursor_bottom -= npc_block_h
            self._npc_cards_y = cursor_bottom
            cursor_bottom -= SECTION_GAP
        else:
            self._npc_cards_y = None

        # Travel CTA: one primary button that opens the destination picker.
        travel_top_y = cursor_bottom - TRAVEL_BTN_H
        self._travel_label_y = travel_top_y - SECTION_LABEL_H + 2
        travel_w = min(420, btn_area_w)
        if guided_resolver.enabled:
            n_dest = guided_destination_count
            story_count = len(guided_primary)
            cta_label = (
                self.ctx.t(
                    "travel_stories_count",
                    "行程地圖（{count} 個故事）",
                    count=story_count,
                )
                if story_count
                else self.ctx.t(
                    "travel_no_new_stories",
                    "行程地圖（目前無新故事）",
                )
            )
        else:
            n_dest = len(visible_exits_info)
            travel_label = (
                self.ctx.t("travel_map", "行程地圖")
                if guided_story_authored
                else self.ctx.t("go_out", "外出")
            )
            cta_label = (
                self.ctx.t(
                    "travel_destinations_count",
                    "{label}（{count} 個地點）",
                    label=travel_label,
                    count=n_dest,
                )
                if n_dest else travel_label
            )
        self._travel_btn = Button(
            pygame.Rect(x_start, travel_top_y, travel_w, TRAVEL_BTN_H),
            cta_label, fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=17, style="primary",
            on_click=(self._request_map if self.on_map else None),
            enabled=bool(self.on_map) and n_dest > 0 and map_enabled,
            tooltip=map_reason if not map_enabled else None,
            motion=self.ctx.motion,
        )
        self._discovery_text = (
            self.ctx.t("new_discovery", "● 有新發現")
            if discovery_count > 0 else None
        )
        self._discovery_pos = (x_start + travel_w + 16,
                               travel_top_y + (TRAVEL_BTN_H - 18) // 2)
        cursor_bottom = travel_top_y - SECTION_LABEL_H - SECTION_GAP

        if action_rows:
            actions_block_h = action_rows * BTN_H \
                              + max(0, action_rows - 1) * BTN_GAP_Y
            actions_top_y = cursor_bottom - actions_block_h
            self._action_label_y = actions_top_y - SECTION_LABEL_H + 2
            for i, (
                focus_id,
                label,
                cb,
                enabled,
                reason,
            ) in enumerate(actions):
                row = i // cols
                col = i % cols
                r = pygame.Rect(x_start + col * (btn_w + BTN_GAP_X),
                                actions_top_y + row * (BTN_H + BTN_GAP_Y),
                                btn_w, BTN_H)
                button = Button(
                    r, label, fonts=self.ctx.fonts, theme=self.ctx.theme,
                    font_size=15, on_click=cb, enabled=enabled,
                    style="ghost",
                    tooltip=reason,
                    motion=self.ctx.motion,
                )
                self._buttons.append(button)
                self._action_focus_ids.append(focus_id)
                if reason:
                    self._action_hints.append((button, reason))
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
            "travel_label_y": self._travel_label_y,
        }

        self._npc_cards = []
        if present_ids and self._npc_cards_y is not None:
            for i, nid in enumerate(present_ids):
                row, col = divmod(i, npc_cols)
                r = pygame.Rect(
                    x_start + col * (NPC_CARD_W + 8),
                    self._npc_cards_y + row * (NPC_CARD_H + 8),
                    NPC_CARD_W,
                    NPC_CARD_H,
                )
                self._npc_cards.append((r, nid))
        valid_focus_ids = {item[0] for item in self._focus_targets()}
        self._focus_id = (
            previous_focus if previous_focus in valid_focus_ids else None
        )
        if self._focus_id is None:
            self._focus_visible = False
        self._build_signature = self._current_signature()

    def _current_objective(self) -> str | None:
        """Return the highest-priority active authored objective.

        System tutorials and collected records stay in the journal; the
        persistent exploration hint is reserved for actionable story/side
        objectives so it answers the player's immediate "what now?" question.
        """
        tracker = self.ctx.state.clues
        active = [
            clue for clue in tracker.clues.values()
            if clue.id in tracker.seen
            and not clue.record
            and clue.category != "系統"
            and tracker.is_active(clue, self.ctx.state)
        ]
        if not active:
            return None
        clue = max(active, key=lambda item: (item.priority, item.id))
        return clue.title

    def _region_display_name(self, region_id: str | None) -> str | None:
        """Resolve a region foreign key to authored player-facing copy.

        ``Location.region`` is an internal reference such as ``campus``.  An
        unresolved reference is omitted here instead of leaking that opaque id
        into the HUD; pack validation remains responsible for reporting it to
        authors.
        """
        if not region_id:
            return None
        regions = getattr(self.ctx.state.map, "regions", {})
        region = regions.get(region_id) if hasattr(regions, "get") else None
        name = getattr(region, "name", None)
        if not isinstance(name, str):
            return None
        cleaned = name.strip()
        return cleaned or None

    def _fit_hint(self, text: str, max_width: int, size: int = 16) -> str:
        """Ellipsize a one-line UI hint to the available logical width."""
        if self.ctx.fonts.render(
                text, size, self.ctx.theme.accent_warm).get_width() <= max_width:
            return text
        suffix = "…"
        trimmed = text
        while trimmed:
            trimmed = trimmed[:-1]
            candidate = trimmed.rstrip() + suffix
            if self.ctx.fonts.render(
                    candidate, size,
                    self.ctx.theme.accent_warm).get_width() <= max_width:
                return candidate
        return suffix

    def _fit_text(
        self,
        text: str,
        max_width: int,
        *,
        size: int,
        color,
        bold: bool = False,
    ) -> str:
        """Return a single line that fits its card without clipping."""
        if max_width <= 0:
            return ""
        if self.ctx.fonts.render(
                text, size, color, bold=bold).get_width() <= max_width:
            return text
        suffix = "…"
        trimmed = text
        while trimmed:
            trimmed = trimmed[:-1]
            candidate = trimmed.rstrip() + suffix
            if self.ctx.fonts.render(
                    candidate, size, color,
                    bold=bold).get_width() <= max_width:
                return candidate
        return suffix

    def _focus_targets(
        self,
    ) -> list[
        tuple[
            str,
            str,
            pygame.Rect,
            Callable[[], None] | None,
            bool,
            str | None,
        ]
    ]:
        """Return stable, visually ordered exploration focus targets."""
        targets: list[
            tuple[
                str,
                str,
                pygame.Rect,
                Callable[[], None] | None,
                bool,
                str | None,
            ]
        ] = []
        top_ids = ("top:clues", "top:menu")
        for target_id, button in zip(top_ids, self._top_buttons):
            if button.on_click is not None:
                targets.append((
                    target_id,
                    button.label,
                    button.rect,
                    button.on_click,
                    button.enabled,
                    button.tooltip,
                ))
        for index, button in enumerate(self._buttons):
            if button.on_click is not None or button.tooltip:
                target_id = (
                    self._action_focus_ids[index]
                    if index < len(self._action_focus_ids)
                    else f"action:{index}"
                )
                targets.append((
                    target_id,
                    button.label,
                    button.rect,
                    button.on_click,
                    button.enabled,
                    button.tooltip,
                ))
        if (
            self._travel_btn is not None
            and self._travel_btn.on_click is not None
        ):
            button = self._travel_btn
            targets.append((
                "travel",
                button.label,
                button.rect,
                button.on_click,
                button.enabled,
                button.tooltip,
            ))
        if self.on_open_npc is not None:
            for rect, npc_id in self._npc_cards:
                npc = self.ctx.npcs.get(npc_id)
                label = npc.name if npc is not None else npc_id
                targets.append((
                    f"npc:{npc_id}",
                    label,
                    rect,
                    lambda nid=npc_id: self.on_open_npc(nid),
                    True,
                    None,
                ))
        return targets

    def _navigate_focus(
        self,
        action: str,
    ) -> bool:
        targets = self._focus_targets()
        if not targets:
            self._focus_id = None
            self._focus_visible = False
            return False
        target_ids = [target[0] for target in targets]
        if action in (
            InputAction.FOCUS_NEXT,
            InputAction.FOCUS_PREVIOUS,
        ):
            reverse = action == InputAction.FOCUS_PREVIOUS
            step = -1 if reverse else 1
            if self._focus_id not in target_ids:
                self._focus_id = target_ids[-1 if reverse else 0]
            else:
                index = target_ids.index(self._focus_id)
                self._focus_id = target_ids[(index + step) % len(target_ids)]
            self._focus_visible = True
            return True

        if action not in (
            InputAction.NAV_UP,
            InputAction.NAV_DOWN,
            InputAction.NAV_LEFT,
            InputAction.NAV_RIGHT,
        ):
            return False
        if self._focus_id not in target_ids:
            # Arrow navigation starts on the page's single primary action.
            self._focus_id = (
                "travel" if "travel" in target_ids else target_ids[0]
            )
            self._focus_visible = True
            return True

        current = targets[target_ids.index(self._focus_id)]
        cx, cy = current[2].center
        direction = {
            InputAction.NAV_UP: (0, -1),
            InputAction.NAV_DOWN: (0, 1),
            InputAction.NAV_LEFT: (-1, 0),
            InputAction.NAV_RIGHT: (1, 0),
        }[action]
        candidates: list[tuple[tuple[float, float], str]] = []
        vx, vy = direction
        for candidate in targets:
            if candidate[0] == self._focus_id:
                continue
            dx = candidate[2].centerx - cx
            dy = candidate[2].centery - cy
            forward = dx * vx + dy * vy
            if forward <= 0:
                continue
            cross = abs(dx * vy - dy * vx)
            candidates.append(((forward + cross * 2, cross), candidate[0]))
        if candidates:
            self._focus_id = min(candidates, key=lambda item: item[0])[1]
        else:
            step = (
                -1
                if action in (InputAction.NAV_UP, InputAction.NAV_LEFT)
                else 1
            )
            index = target_ids.index(self._focus_id)
            self._focus_id = target_ids[(index + step) % len(target_ids)]
        self._focus_visible = True
        return True

    def _activate_focused(self) -> bool:
        if not self._focus_visible or self._focus_id is None:
            return False
        target = next(
            (
                item for item in self._focus_targets()
                if item[0] == self._focus_id
            ),
            None,
        )
        if target is None:
            return False
        _target_id, label, _rect, callback, enabled, reason = target
        if not enabled:
            if reason:
                self._notify_blocked(label, reason)
                return True
            return False
        if callback is None:
            return False
        callback()
        return True

    def _focused_action_hint(self) -> tuple[Button, str] | None:
        if not self._focus_visible or self._focus_id is None:
            return None
        if not self._focus_id.startswith("action:"):
            return None
        if self._focus_id not in self._action_focus_ids:
            return None
        index = self._action_focus_ids.index(self._focus_id)
        if not 0 <= index < len(self._buttons):
            return None
        button = self._buttons[index]
        if not button.tooltip:
            return None
        return button, button.tooltip

    def _travel_action_hint(self) -> tuple[Button, str] | None:
        """Explain a locked map CTA on hover or keyboard/controller focus."""
        button = self._travel_btn
        if button is None or not button.tooltip:
            return None
        focused = self._focus_visible and self._focus_id == "travel"
        if focused or button._pointer_over:
            return button, button.tooltip
        return None

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

    def _map_availability(self) -> tuple[bool, str | None]:
        if self.get_map_availability is None:
            return True, None
        try:
            return self.get_map_availability()
        except Exception:
            # Optional presentation guidance cannot remove core travel.
            return True, None

    def _request_map(self) -> None:
        available, reason = self._map_availability()
        if not available:
            self._notify_blocked(
                self.ctx.t("travel_map", "行程地圖"),
                reason or self.ctx.t(
                    "complete_current_objective_first",
                    "先完成目前的目標。",
                ),
            )
            return
        if self.on_map is not None:
            self.on_map()

    def update(self, dt: float, inp) -> None:
        # If state mutated under us without an overlay round-trip (e.g. a
        # move into a location with no auto/enter hook, or 等下個時段),
        # rebuild before processing input so stale button lambdas don't
        # silently swallow clicks.
        if self._build_signature != self._current_signature():
            self._rebuild_widgets()
        # Hide-UI (非表示): the bound action toggles; while hidden a
        # click/cancel restores it so the player can inspect the full image.
        if inp.pressed(InputAction.HIDE_UI):
            self._ui_hidden = not self._ui_hidden
            return
        if self._ui_hidden:
            if inp.mouse_clicked or inp.cancel:
                self._ui_hidden = False
            return

        # Exploration shortcuts own their context before the shared
        # navigation aliases. Defaults intentionally include familiar A/S for
        # affection/save while the same physical keys navigate grids on other
        # screens; resolving the shortcut first prevents S from both moving
        # focus and opening Save in one frame.
        if inp.pressed(InputAction.OPEN_MAP) and self.on_map:
            self._request_map()
            return
        if inp.pressed(InputAction.OPEN_AFFECTION) and self.on_affection:
            self.on_affection()
            return
        if inp.pressed(InputAction.OPEN_EVENT_LOG) and self.on_log:
            self.on_log()
            return
        if inp.pressed(InputAction.OPEN_SAVE) and self.on_save:
            self.on_save()
            return
        if (
            inp.pressed(InputAction.OPEN_ACHIEVEMENTS)
            and self.on_achievements
        ):
            self.on_achievements()
            return
        if inp.pressed(InputAction.OPEN_INVENTORY) and self.on_inventory:
            self.on_inventory()
            return
        if inp.pressed(InputAction.OPEN_CLUES) and self.on_clues:
            self.on_clues()
            return
        if inp.pressed(InputAction.OPEN_MENU) and self.on_menu:
            self.on_menu()
            return

        for action in (
            InputAction.FOCUS_PREVIOUS,
            InputAction.FOCUS_NEXT,
            InputAction.NAV_UP,
            InputAction.NAV_DOWN,
            InputAction.NAV_LEFT,
            InputAction.NAV_RIGHT,
        ):
            if inp.pressed(action):
                self._navigate_focus(action)
                break

        if inp.mouse_clicked:
            pointer_target = next(
                (
                    target for target in self._focus_targets()
                    if target[2].collidepoint(inp.mouse_pos)
                ),
                None,
            )
            self._focus_id = (
                pointer_target[0] if pointer_target is not None else None
            )
            self._focus_visible = False

        for b in self._top_buttons:
            b.update(dt, inp)
        for b in self._buttons:
            b.update(dt, inp)
        self._hovered_action_hint = next(
            (
                (button, reason)
                for button, reason in self._action_hints
                if button._pointer_over
            ),
            None,
        )
        if inp.mouse_clicked and self._hovered_action_hint is not None:
            button, reason = self._hovered_action_hint
            self._notify_blocked(button.label, reason)
        if self._travel_btn is not None:
            self._travel_btn.update(dt, inp)
            if (
                inp.mouse_clicked
                and self._travel_btn._pointer_over
                and not self._travel_btn.enabled
                and self._travel_btn.tooltip
            ):
                self._notify_blocked(
                    self._travel_btn.label,
                    self._travel_btn.tooltip,
                )
        # NPC card clicks
        if inp.mouse_clicked:
            for rect, nid in self._npc_cards:
                if rect.collidepoint(inp.mouse_pos) and self.on_open_npc:
                    self.on_open_npc(nid)
                    return
        if inp.confirm and self._activate_focused():
            return

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
            region_name = self._region_display_name(loc.region)
            if region_name:
                reg = self.ctx.fonts.render(
                    region_name, self.ctx.config.font_size_small,
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
        # Section labels — "動作" above the action row, "移動" above the CTA.
        section_color = self.ctx.theme.text_mute
        if self._layout.get("action_label_y") is not None and self._buttons:
            lbl = self.ctx.fonts.render(
                self.ctx.t("actions_heading", "動作"),
                14,
                section_color,
                bold=True,
            )
            surface.blit(lbl, (self._info_rect.x + 28,
                               self._layout["action_label_y"]))
        if self._layout.get("travel_label_y") is not None:
            lbl = self.ctx.fonts.render(
                self.ctx.t("travel_heading", "移動"),
                14,
                section_color,
                bold=True,
            )
            surface.blit(lbl, (self._info_rect.x + 28,
                               self._layout["travel_label_y"]))
            if self._objective_text:
                objective_x = self._info_rect.x + 88
                objective_w = max(
                    1, self._info_rect.right - 28 - objective_x,
                )
                objective_text = self._fit_hint(
                    self.ctx.t(
                        "current_objective",
                        "目前目標：{objective}",
                        objective=self._objective_text,
                    ),
                    objective_w,
                )
                objective = self.ctx.fonts.render(
                    objective_text, 16, self.ctx.theme.accent_warm,
                    bold=True,
                )
                surface.blit(
                    objective,
                    (objective_x, self._layout["travel_label_y"] - 2),
                )
        # action buttons
        for b in self._buttons:
            b.draw(surface)
        self._draw_action_hint(surface)
        # travel CTA + "new here" hint
        if self._travel_btn is not None:
            self._travel_btn.draw(surface)
            if self._discovery_text:
                hint = self.ctx.fonts.render(self._discovery_text, 16,
                                             self.ctx.theme.accent)
                surface.blit(hint, self._discovery_pos)
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
            thumb_size = min(
                getattr(self, "_npc_thumb_size", 44),
                rect.height - 12,
            )
            if npc.portrait:
                img = self.ctx.assets.scaled(npc.portrait, (thumb_size, thumb_size),
                                             fit="cover")
                surface.blit(
                    img,
                    (rect.x + 6, rect.centery - thumb_size // 2),
                )
            nx = rect.x + thumb_size + 12
            text_w = max(1, rect.right - 8 - nx)
            name_text = self._fit_text(
                npc.name,
                text_w,
                size=18,
                color=self.ctx.theme.text,
                bold=True,
            )
            name = self.ctx.fonts.render(
                name_text, 18, self.ctx.theme.text, bold=True,
            )
            if npc.is_heroine:
                aff = self.ctx.state.affection.get(npc.id)
                lvl = self.ctx.state.affection.level_label(npc.id)
                sub_text = self.ctx.t(
                    "affection_summary",
                    "好感 {level} · {value}",
                    level=lvl,
                    value=aff,
                )
                sub_color = self.ctx.theme.accent_warm
            else:
                sub_text = npc.role or ""
                sub_color = self.ctx.theme.text_mute
            sub_text = self._fit_text(
                sub_text,
                text_w,
                size=14,
                color=sub_color,
            )
            sub = self.ctx.fonts.render(sub_text, 14, sub_color)
            block_h = name.get_height() + 2 + sub.get_height()
            text_y = rect.centery - block_h // 2
            surface.blit(name, (nx, text_y))
            surface.blit(sub, (nx, text_y + name.get_height() + 2))
        self._draw_keyboard_focus(surface)

    def _draw_action_hint(self, surface: pygame.Surface) -> None:
        """Draw the disabled-action explanation for pointer or keyboard."""
        pointer_travel_hint = None
        if (
            self._travel_btn is not None
            and self._travel_btn._pointer_over
            and self._travel_btn.tooltip
        ):
            pointer_travel_hint = (
                self._travel_btn,
                self._travel_btn.tooltip,
            )
        active_hint = (
            pointer_travel_hint
            or self._hovered_action_hint
            or self._focused_action_hint()
            or self._travel_action_hint()
        )
        if active_hint is None:
            return
        button, reason = active_hint
        max_w = min(560, self._info_rect.width - 32)
        text = self._fit_hint(reason, max_w - 28, size=16)
        rendered = self.ctx.fonts.render(
            text, 16, self.ctx.theme.text, bold=True,
        )
        tip = pygame.Rect(
            0, 0, rendered.get_width() + 28, rendered.get_height() + 18,
        )
        tip.centerx = button.rect.centerx
        tip.bottom = button.rect.top - 8
        tip.clamp_ip(surface.get_rect().inflate(-16, -16))
        Panel(
            tip,
            self.ctx.theme,
            fill=(*self.ctx.theme.bg_overlay[:3], 248),
            border=self.ctx.theme.accent_warm,
            radius=self.ctx.theme.radius_s,
            border_width=1,
        ).draw(surface)
        surface.blit(rendered, rendered.get_rect(center=tip.center))

    def _draw_keyboard_focus(self, surface: pygame.Surface) -> None:
        """Paint a persistent high-contrast ring around keyboard focus."""
        if not self._focus_visible or self._focus_id is None:
            return
        target = next(
            (
                item for item in self._focus_targets()
                if item[0] == self._focus_id
            ),
            None,
        )
        if target is None:
            return
        ring = target[2].inflate(6, 6)
        ring.clamp_ip(surface.get_rect().inflate(-2, -2))
        pygame.draw.rect(
            surface,
            (8, 6, 18),
            ring.inflate(2, 2),
            width=3,
            border_radius=self.ctx.theme.radius_m + 3,
        )
        pygame.draw.rect(
            surface,
            self.ctx.theme.accent_warm,
            ring,
            width=3,
            border_radius=self.ctx.theme.radius_m + 2,
        )

    def _draw_hidden_hint(self, surface: pygame.Surface) -> None:
        sw, sh = surface.get_size()
        bindings = BindingProfile(
            getattr(self.ctx.config, "input_bindings", {}),
        )
        hint = self.ctx.fonts.render(
            self.ctx.t(
                "show_ui_hint",
                "點擊 / 按 {binding} 顯示介面",
                binding=bindings.primary_display(InputAction.HIDE_UI),
            ),
            14,
            (*self.ctx.theme.text_mute[:3], 150))
        surface.blit(hint, (sw - hint.get_width() - 20,
                            sh - hint.get_height() - 16))

    def describe(self) -> dict:
        loc = self.ctx.state.map.current
        flags = self.ctx.state.events.flags
        time_of_day = self.ctx.state.time.time_of_day.value
        map_available, map_reason = self._map_availability()
        focused = next(
            (
                item for item in self._focus_targets()
                if item[0] == self._focus_id
            ),
            None,
        )
        return {
            "scene": "ExplorationScene",
            "location": loc.id if loc else None,
            "location_name": loc.name if loc else None,
            "time": self.ctx.state.time.label(),
            "exits": [e.id for e in self.ctx.state.map.available_exits(flags, time_of_day)],
            "objective": self._objective_text,
            "focus": (
                {
                    "id": focused[0],
                    "label": focused[1],
                    "visible": self._focus_visible,
                    "enabled": focused[4],
                    "reason": focused[5],
                }
                if focused is not None else None
            ),
            "actions": [
                {
                    "label": button.label,
                    "enabled": button.enabled,
                    "reason": button.tooltip,
                }
                for button in self._buttons
            ],
            "map": {
                "available": map_available,
                "enabled": bool(
                    self._travel_btn is not None
                    and self._travel_btn.enabled
                ),
                "reason": map_reason,
                "label": (
                    self._travel_btn.label
                    if self._travel_btn is not None else None
                ),
            },
            "npcs_present": [n for _, n in self._npc_cards],
            "scenes_available": [
                h.scene_id for h in self.ctx.state.map.available_scenes(
                    time_of_day=self.ctx.state.time.time_of_day.value,
                    flags=self.ctx.state.events.flags,
                    played_scenes=self.ctx.state.story.played,
                )
            ],
        }
