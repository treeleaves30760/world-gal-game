"""Map overlay scene."""
from __future__ import annotations

from typing import Callable

import pygame

from .base import Scene, SceneContext
from ..ui.system_chrome import ChromeRequest
from ..ui.widgets import Button, Panel, MapView
from ..core.guided_destinations import (
    GuidedDestinationResolver,
    guided_story_progress,
)
from ..core.spatial_map import SpatialMapPresentation, spatial_map_from_meta
from ..core.travel import (
    TravelService,
    graph_travel_enabled,
    hidden_travel_tags,
    minimum_travel_cost,
)
from ..ui.input_bindings import BindingProfile, InputAction


class MapScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        self.map_view: MapView | None = None
        self.close_btn: Button | None = None
        self.mode_btn: Button | None = None
        self.on_close: Callable[[], None] | None = None
        self.on_move_to: Callable[[str], None] | None = None
        self._guided_offers: dict[str, dict] = {}
        self._spatial_map: SpatialMapPresentation | None = None
        self._guided_enabled = False
        self._guide_progress: dict | None = None
        self._show_ordinary = False
        self._marker_kinds: dict[str, str] = {}
        self._reachable_ids: set[str] = set()
        self._density = 1.0
        self._panel_rect = pygame.Rect(0, 0, 0, 0)
        self._title_rect = pygame.Rect(0, 0, 0, 0)
        self._hint_rect = pygame.Rect(0, 0, 0, 0)
        self._legend_rect = pygame.Rect(0, 0, 0, 0)
        # -1 leaves D-pad focus on the map. Tab / controller Y enters the
        # top-row controls so the story/ordinary toggle is never pointer-only.
        self._chrome_focus = -1

    def enter(self, *, on_close=None, on_move_to=None, **_) -> None:
        self.on_close = on_close
        self.on_move_to = on_move_to
        sw, sh = self.ctx.screen_size
        # Geometry must track the exact same scale as typography.  Capping
        # layout density at 1.5 while FontRegistry continued to 3.0 at 4K
        # produced large glyphs inside half-sized marker/card rectangles,
        # turning ordinary labels into misleading ellipses.  SceneContext
        # normally carries FontRegistry; the viewport ratio is only a safe
        # compatibility fallback for custom registries.
        font_density = getattr(
            self.ctx.fonts,
            "scale",
            min(sw / 1280.0, sh / 720.0),
        )
        self._density = max(0.75, float(font_density))
        outer_margin = max(20, int(round(32 * self._density)))
        self._panel_rect = pygame.Rect(
            outer_margin,
            outer_margin,
            sw - outer_margin * 2,
            sh - outer_margin * 2,
        )
        self._panel = Panel(
            self._panel_rect,
            self.ctx.theme,
            fill=(*self.ctx.theme.bg_overlay[:3], 240),
            border=self.ctx.theme.border_strong,
            radius=max(
                1,
                int(round(
                    self.ctx.theme.radius_l * self._density,
                )),
            ),
            border_width=max(1, int(round(2 * self._density))),
            chrome=self.ctx.ui_chrome,
            screen="map",
            role="main",
            density=self._density,
        )

        content_pad = max(18, int(round(28 * self._density)))
        # Pack-owned panels may use their top edge for a compact screen code
        # or document index. Keep the map's title, legend, and controls below
        # that paint-only band so custom chrome never competes with engine
        # content. The reserved space scales with the same density authority
        # as fonts and controls.
        chrome_header_clearance = max(
            24,
            int(round(28 * self._density)),
        )
        header_top = (
            self._panel_rect.y
            + chrome_header_clearance
        )
        control_gap = max(8, int(round(12 * self._density)))
        control_pad = max(10, int(round(16 * self._density)))
        close_font_size = 15
        mode_font_size = 14
        close_label = self.ctx.t(
            "menu_item_with_binding",
            "{label} ({binding})",
            label=self.ctx.t("close", "關閉"),
            binding=self._binding_short(InputAction.BACK),
        )
        mode_labels = (
            self.ctx.t(
                "map_show_ordinary_exploration",
                "顯示一般探索",
            ),
            self.ctx.t(
                "map_story_locations_only",
                "只看故事地點",
            ),
        )
        close_text_w = self.ctx.fonts.render(
            close_label,
            close_font_size,
            self.ctx.theme.text,
            bold=True,
        ).get_width()
        mode_text_w = max(
            self.ctx.fonts.render(
                label,
                mode_font_size,
                self.ctx.theme.text,
                bold=True,
            ).get_width()
            for label in mode_labels
        )
        control_line_h = max(
            self.ctx.fonts.get(
                close_font_size,
                bold=True,
            ).get_linesize(),
            self.ctx.fonts.get(
                mode_font_size,
                bold=True,
            ).get_linesize(),
        )
        control_h = max(
            44,
            self._scaled(44),
            control_line_h + self._scaled(14),
        )
        close_w = max(
            self._scaled(120),
            close_text_w + control_pad * 2,
        )
        mode_w = max(
            self._scaled(176),
            mode_text_w + control_pad * 2,
        )
        controls_right = self._panel_rect.right - self._scaled(16)
        self.close_btn = Button(
            pygame.Rect(
                controls_right - close_w,
                header_top,
                close_w,
                control_h,
            ),
            close_label,
            fonts=self.ctx.fonts,
            theme=self.ctx.theme,
            font_size=close_font_size,
            style="ghost",
            on_click=(lambda: on_close() if on_close else None),
            chrome=self.ctx.ui_chrome,
            screen="map",
            role="close",
            motion=self.ctx.motion,
        )
        self.mode_btn = Button(
            pygame.Rect(
                self.close_btn.rect.x - control_gap - mode_w,
                header_top,
                mode_w,
                control_h,
            ),
            mode_labels[0],
            fonts=self.ctx.fonts,
            theme=self.ctx.theme,
            font_size=mode_font_size,
            style="ghost",
            on_click=self._toggle_ordinary,
            chrome=self.ctx.ui_chrome,
            screen="map",
            role="filter",
            motion=self.ctx.motion,
        )

        self._title_text = self.ctx.t("map", "地圖")
        legend_labels = self._map_legend_labels()
        title = self.ctx.fonts.render(
            self._title_text,
            self.ctx.config.font_size_header,
            self.ctx.theme.accent,
            bold=True,
        )
        title_x = self._panel_rect.x + content_pad
        title_y = header_top + max(0, (control_h - title.get_height()) // 2)
        self._title_rect = title.get_rect(topleft=(title_x, title_y))
        row_bottom = max(
            self._title_rect.bottom,
            self.close_btn.rect.bottom,
            self.mode_btn.rect.bottom,
        )
        hint_top = row_bottom + self._scaled(6)
        hint_h = self.ctx.fonts.get(
            self.ctx.config.font_size_small,
        ).get_linesize()
        self._hint_rect = pygame.Rect(
            title_x,
            hint_top,
            self._panel_rect.right - content_pad - title_x,
            hint_h,
        )
        legend_left = self._title_rect.right + self._scaled(18)
        legend_right = self.mode_btn.rect.left - self._scaled(12)
        header_legend = pygame.Rect(
            legend_left,
            header_top,
            max(0, legend_right - legend_left),
            control_h,
        )
        one_line_legend_w, header_legend_h, _header_rows = (
            MapView.legend_metrics(
                fonts=self.ctx.fonts,
                labels=legend_labels,
                density=self._density,
                max_width=header_legend.width,
            )
        )
        if (
            header_legend.width >= one_line_legend_w
            and control_h >= header_legend_h
        ):
            self._legend_rect = header_legend
        else:
            _full_w, legend_h, _rows = MapView.legend_metrics(
                fonts=self.ctx.fonts,
                labels=legend_labels,
                density=self._density,
                max_width=self._hint_rect.width,
            )
            # The fallback row owns the hint band while visible. Growing the
            # shared rect lets localized labels wrap as complete icon/text
            # pairs and keeps the map canvas below passive chrome.
            self._hint_rect.height = max(self._hint_rect.height, legend_h)
            self._legend_rect = pygame.Rect(self._hint_rect)
        map_top = max(
            self._hint_rect.bottom,
            self._legend_rect.bottom,
        ) + self._scaled(8)
        map_bottom = self._panel_rect.bottom - content_pad
        map_rect = pygame.Rect(
            title_x,
            map_top,
            self._panel_rect.width - content_pad * 2,
            max(1, map_bottom - map_top),
        )
        self.map_view = MapView(
            map_rect,
            fonts=self.ctx.fonts,
            theme=self.ctx.theme,
            on_click=self._on_node_click,
            assets=self.ctx.assets,
            chrome=self.ctx.ui_chrome,
            motion=self.ctx.motion,
            density=self._density,
            legend_labels=legend_labels,
        )
        self._refresh()

    def _scaled(self, value: int) -> int:
        return max(1, int(round(value * self._density)))

    def _binding_profile(self) -> BindingProfile:
        return BindingProfile(
            getattr(self.ctx.config, "input_bindings", {}) or {}
        )

    def _binding_short(self, action: str) -> str:
        profile = self._binding_profile()
        primary_display = getattr(profile, "primary_display", None)
        if callable(primary_display):
            return primary_display(action)
        return profile.display(action).split(" / ", 1)[0]

    def _map_legend_labels(self) -> dict[str, str]:
        """Resolve passive map chrome once and inject it into ``MapView``."""
        return {
            "story": self.ctx.t("map_story_location", "故事地點"),
            "current": self.ctx.t(
                "map_current_location",
                "目前位置",
            ),
            "ordinary": self.ctx.t(
                "map_ordinary_location",
                "一般地點",
            ),
            "off_campus": self.ctx.t(
                "map_off_campus_location",
                "校外地點",
            ),
            "focus": self.ctx.t("map_focus", "操作焦點"),
        }

    def resume(self) -> None:
        self._refresh()

    def _toggle_ordinary(self) -> None:
        if not self._guided_enabled:
            return
        self._show_ordinary = not self._show_ordinary
        self._refresh()
        # Switching layers is itself a navigation action. Hand focus back
        # from the chrome to the map and disclose one useful ordinary target
        # immediately, using the same persistent focus shared by keyboard and
        # gamepad input. Returning to story-only lets ``set_data`` clear an
        # ordinary focus that is no longer visible.
        self._set_chrome_focus(-1)
        if self._show_ordinary and self.map_view is not None:
            self.map_view.focus_first(
                location_id
                for location_id, marker_kind in self._marker_kinds.items()
                if (
                    marker_kind == "ordinary"
                    and location_id in self._reachable_ids
                )
            )

    def _refresh(self) -> None:
        flags = self.ctx.state.events.flags
        cur = self.ctx.state.map.current
        resolver = GuidedDestinationResolver(
            self.ctx.state,
            self.ctx.meta,
        )
        self._guided_enabled = resolver.enabled
        progress = guided_story_progress(self.ctx.meta, flags)
        self._guide_progress = (
            progress.as_dict() if progress is not None else None
        )
        self._spatial_map = spatial_map_from_meta(self.ctx.meta)
        if self.map_view is None:
            return
        self.map_view.set_presentation(self._spatial_map)
        self.map_view.set_legend_visible(
            not resolver.enabled or self._show_ordinary,
            rect=self._legend_rect,
        )
        guided_by_target = (
            {
                offer.target: offer
                for offer in resolver.primary()
            }
            if resolver.enabled else {}
        )
        self._guided_offers = {
            target: offer.as_dict()
            for target, offer in guided_by_target.items()
        }
        edge_ids = (
            set(self._spatial_map.edge_destinations)
            if self._spatial_map is not None else set()
        )
        graph_scope = graph_travel_enabled(self.ctx.meta, flags)
        hidden_tags = hidden_travel_tags(self.ctx.meta)
        minimum_cost = minimum_travel_cost(self.ctx.meta) if graph_scope else 0
        # Presentation never grants reachability. Every story marker,
        # ordinary node, and configured off-campus card gets its state from the
        # same exact planner used by the confirmation sheet. Authored hidden
        # tags remain authoritative even when a location has edge-card art;
        # presentation metadata must not reveal a route spoiler.
        reachable: set[str] = set()
        for loc in self.ctx.state.map.locations.values():
            if (
                loc.id == (cur.id if cur else None)
                or hidden_tags.intersection(loc.tags)
            ):
                continue
            if TravelService.can_reach(
                self.ctx.state,
                loc.id,
                allow_distant=graph_scope,
                minimum_cost=minimum_cost,
            ):
                reachable.add(loc.id)
        self._reachable_ids = reachable

        if self.mode_btn is not None:
            # The secondary ordinary layer belongs to free travel.  During the
            # pre-graph map lessons it would either reveal nothing or add an
            # irrelevant control beside the one authored objective.
            self.mode_btn.visible = resolver.enabled and graph_scope
            if not self.mode_btn.visible:
                self._show_ordinary = False
            self.mode_btn.label = (
                self.ctx.t(
                    "map_story_locations_only",
                    "只看故事地點",
                )
                if self._show_ordinary
                else self.ctx.t(
                    "map_show_ordinary_exploration",
                    "顯示一般探索",
                )
            )
            self.mode_btn.style = (
                "primary" if self._show_ordinary else "ghost"
            )
            self.mode_btn.selected = self._show_ordinary

        regions = self.ctx.state.map.regions
        nodes = []
        marker_kinds: dict[str, str] = {}
        for loc in self.ctx.state.map.locations.values():
            offer = guided_by_target.get(loc.id)
            is_current = loc.id == (cur.id if cur else None)
            is_edge = loc.id in edge_ids
            presentation_node = (
                self._spatial_map.nodes.get(loc.id)
                if self._spatial_map is not None else None
            )
            map_layer = (
                presentation_node.layer
                if presentation_node is not None else "campus"
            )
            map_parent = (
                presentation_node.parent
                if presentation_node is not None else None
            )
            display_name = (
                presentation_node.label
                if (
                    presentation_node is not None
                    and presentation_node.label is not None
                )
                else loc.name
            )
            is_hidden = bool(hidden_tags.intersection(loc.tags))
            ordinary_reachable = loc.id in reachable and not is_hidden
            # A guided story map is a decision surface, not a catalogue.  Its
            # primary layer therefore contains only the current position and
            # authored story offers.  Off-campus cards join the ordinary
            # exploration layer after the graph opens; otherwise an optional
            # two-slot outing competes visually with the one story action the
            # guide is asking the player to take.  Non-guided maps retain the
            # legacy behaviour and show their authored perimeter cards.
            visible_edge = (
                is_edge
                and not is_hidden
                and (
                    not resolver.enabled
                    or is_current
                    or offer is not None
                    or (graph_scope and self._show_ordinary)
                )
            )
            if resolver.enabled:
                include = (
                    is_current
                    or offer is not None
                    or visible_edge
                    or (self._show_ordinary and ordinary_reachable)
                )
            else:
                # A non-guided pack has no story subset to start from, so its
                # primary map remains immediately usable.
                include = is_current or visible_edge or ordinary_reachable
            if include and not visible_edge:
                if map_layer == "story_only":
                    # Presentation-only story nodes never become ordinary
                    # exploration merely because the graph can route to them.
                    # The current node and real guided offers remain visible.
                    include = is_current or offer is not None
                elif map_layer == "interior":
                    # Keep a campus overview legible.  A room is disclosed
                    # globally when it carries a current story offer, and as
                    # ordinary navigation only while the player is standing
                    # at its authored parent building.
                    include = (
                        is_current
                        or offer is not None
                        or (
                            ordinary_reachable
                            and map_parent
                            == (cur.id if cur is not None else None)
                            and (
                                self._show_ordinary
                                or not resolver.enabled
                            )
                        )
                    )
            if not include:
                continue

            if offer is not None:
                marker_kind = "story"
                wait_phases = (
                    offer.future.wait_phases
                    if (
                        offer.status == "future"
                        and offer.future is not None
                    )
                    else None
                )
                story_status = {
                    "current": self.ctx.t(
                        "map_depart_now",
                        "現在可出發",
                    ),
                    "future": (
                        self.ctx.t(
                            "map_depart_after_time_slots",
                            "等待 {count} 時段後可出發",
                            count=wait_phases,
                        )
                        if wait_phases is not None
                        else self.ctx.t(
                            "map_depart_after_wait",
                            "需等待後出發",
                        )
                    ),
                    "locked": self.ctx.t(
                        "map_not_yet_available",
                        "尚未開放",
                    ),
                }.get(
                    offer.status,
                    self.ctx.t("map_story_location", "故事地點"),
                )
                marker_name = self._story_marker_name(display_name, offer)
                # Put actionable timing first: narrow map labels must never
                # truncate "wait" away while retaining only a place name.
                marker_label = self.ctx.t(
                    "map_marker_status",
                    "{status} · {location}",
                    status=story_status,
                    location=display_name,
                )
            elif is_current:
                marker_kind = "current"
                marker_name = display_name
                marker_label = self.ctx.t(
                    "map_current_location",
                    "目前位置",
                )
            elif visible_edge:
                marker_kind = "off_campus"
                marker_name = display_name
                marker_label = (
                    self.ctx.t("map_available_now", "現在可前往")
                    if ordinary_reachable
                    else self.ctx.t(
                        "map_not_yet_available",
                        "尚未開放",
                    )
                )
            else:
                marker_kind = "ordinary"
                marker_name = display_name
                marker_label = self.ctx.t(
                    "map_ordinary_exploration",
                    "一般探索",
                )
            marker_kinds[loc.id] = marker_kind

            marker_npcs = self._story_marker_npcs(offer)
            lead_npc = marker_npcs[0] if marker_npcs else None
            # Location accessibility and route availability are deliberately
            # separate. A configured off-campus card can represent a real
            # place while the current graph has no legal route to it; in that
            # case the overview must say "尚未開放" instead of presenting a
            # misleading active-looking "校外探索" card.
            marker_accessible = loc.is_accessible(flags)
            if visible_edge and offer is None:
                marker_accessible = (
                    marker_accessible and ordinary_reachable
                )
            travel_available = (
                is_current
                or ordinary_reachable
                or (
                    offer is not None
                    and offer.status == "current"
                    and offer.available
                )
            )
            blocked_reason = (
                None
                if travel_available
                else (
                    offer.blocked_reason
                    if offer is not None
                    else self.ctx.t(
                        "map_no_viable_route",
                        "目前沒有可行路線",
                    )
                )
            )

            region_color = None
            if loc.region and loc.region in regions:
                region_color = regions[loc.region].color
            nodes.append({
                "id": loc.id,
                "name": marker_name,
                "region": loc.region,
                "region_name": regions[loc.region].name if loc.region and loc.region in regions else loc.region,
                "region_color": region_color,
                "description": loc.description,
                "map_x": loc.map_x,
                "map_y": loc.map_y,
                "exits": loc.exit_targets,
                "visited": loc.id in self.ctx.state.map.visited,
                "accessible": marker_accessible,
                "travel_available": travel_available,
                "blocked_reason": blocked_reason,
                "offer_status": offer.status if offer is not None else None,
                "future_wait_phases": (
                    offer.future.wait_phases
                    if (
                        offer is not None
                        and offer.future is not None
                    )
                    else None
                ),
                "marker_kind": marker_kind,
                "marker_label": marker_label,
                "map_layer": map_layer,
                "map_parent": map_parent,
                "event_title": (
                    offer.event.title
                    if offer is not None and offer.event is not None else None
                ),
                # Story markers borrow only the lead cast member's existing
                # identity assets. The pack still owns every portrait and
                # colour; the engine merely gives the map the same at-a-glance
                # character recognition already present in the detail sheet.
                "portrait": (
                    lead_npc.portrait
                    if lead_npc is not None else None
                ),
                "marker_color": self._npc_marker_color(lead_npc),
            })
        self._marker_kinds = marker_kinds
        self.map_view.set_data(nodes=nodes,
                               current_id=cur.id if cur else None,
                               reachable_ids=reachable)

    def _story_marker_name(self, fallback: str, offer) -> str:
        """Use authored cast names as the primary story-map information."""
        names = [npc.name for npc in self._story_marker_npcs(offer)]
        if not names:
            return fallback
        if len(names) <= 2:
            return " × ".join(names)
        return self.ctx.t(
            "map_people_count",
            "{name}等 {count} 人",
            name=names[0],
            count=len(names),
        )

    def _story_marker_npcs(self, offer) -> list:
        """Resolve the visible cast without changing event selection."""
        event = getattr(offer, "event", None)
        npc_ids = getattr(event, "npc_ids", ()) if event is not None else ()
        return [
            npc
            for npc_id in npc_ids
            if (npc := self.ctx.npcs.get(npc_id)) is not None
        ]

    @staticmethod
    def _npc_marker_color(npc) -> tuple[int, int, int] | None:
        """Parse an optional pack-authored route colour for map accents."""
        raw = getattr(npc, "name_color", None) if npc is not None else None
        if not isinstance(raw, str):
            return None
        try:
            from ..dialogue.richtext import _parse_color

            return _parse_color(raw)
        except (TypeError, ValueError):
            return None

    def _on_node_click(self, loc_id: str) -> None:
        if loc_id == (self.ctx.state.map.current.id
                      if self.ctx.state.map.current else None):
            # The highlighted current marker is informational. Closing here
            # made it behave unlike every other marker and caused accidental
            # exits on touch; Esc/B and the explicit close button own closing.
            return
        if self.on_move_to:
            self.on_move_to(loc_id)

    def _chrome_buttons(self) -> list[Button]:
        buttons: list[Button] = []
        if self.mode_btn is not None and self.mode_btn.visible:
            buttons.append(self.mode_btn)
        if self.close_btn is not None and self.close_btn.visible:
            buttons.append(self.close_btn)
        return buttons

    def _set_chrome_focus(self, index: int) -> None:
        buttons = self._chrome_buttons()
        for button in buttons:
            button.focused = False
        if not buttons or index < 0:
            self._chrome_focus = -1
            return
        self._chrome_focus = index % len(buttons)
        buttons[self._chrome_focus].focused = True

    @staticmethod
    def _focus_step(inp) -> int:
        pressed = getattr(inp, "pressed", None)
        if not callable(pressed):
            return 0
        if pressed(InputAction.FOCUS_PREVIOUS):
            return -1
        if pressed(InputAction.FOCUS_NEXT):
            return 1
        return 0

    @staticmethod
    def _direction(inp) -> tuple[int, int] | None:
        pressed = getattr(inp, "pressed", None)
        if callable(pressed):
            for action, direction in (
                (InputAction.NAV_LEFT, (-1, 0)),
                (InputAction.NAV_RIGHT, (1, 0)),
                (InputAction.NAV_UP, (0, -1)),
                (InputAction.NAV_DOWN, (0, 1)),
            ):
                if pressed(action):
                    return direction
        nav = int(getattr(inp, "nav", 0))
        if nav:
            return (0, 1 if nav > 0 else -1)
        return None

    def update(self, dt: float, inp) -> None:
        if inp.cancel and self.on_close:
            self.on_close()
            return

        buttons = self._chrome_buttons()
        for button in buttons:
            button.update(dt, inp)

        if any(
            event.type == pygame.MOUSEMOTION
            for event in getattr(inp, "events", ())
        ):
            self._set_chrome_focus(-1)

        focus_step = self._focus_step(inp)
        if focus_step:
            if self._chrome_focus < 0:
                next_index = 0 if focus_step > 0 else len(buttons) - 1
            else:
                next_index = self._chrome_focus + focus_step
            if next_index < 0 or next_index >= len(buttons):
                self._set_chrome_focus(-1)
            else:
                self._set_chrome_focus(next_index)
            return

        direction = self._direction(inp)
        if self._chrome_focus >= 0:
            if direction is not None:
                dx, dy = direction
                if dy > 0:
                    self._set_chrome_focus(-1)
                else:
                    step = 1 if dx > 0 else -1
                    if dx or dy:
                        self._set_chrome_focus(
                            self._chrome_focus + step,
                        )
                return
            if inp.confirm and self._chrome_focus < len(buttons):
                buttons[self._chrome_focus].activate()
                return
            return

        if self.map_view:
            self.map_view.update(dt, inp)

    def draw(self, surface: pygame.Surface) -> None:
        chrome = self.ctx.ui_chrome
        backdrop_rect = surface.get_rect()
        backdrop_painted = (
            chrome is not None
            and chrome.paint(
                surface,
                backdrop_rect,
                ChromeRequest(
                    part="backdrop",
                    screen="map",
                    variant="overlay",
                    size=backdrop_rect.size,
                    density=self._density,
                ),
            )
        )
        if not backdrop_painted:
            veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
            veil.fill((0, 0, 0, 160))
            surface.blit(veil, (0, 0))
        if self._panel:
            self._panel.draw(surface)
        title = self.ctx.fonts.render(
            self._title_text,
            self.ctx.config.font_size_header,
            self.ctx.theme.accent,
            bold=True,
        )
        surface.blit(title, self._title_rect.topleft)
        legend_uses_hint = (
            self.map_view is not None
            and self.map_view.legend_visible
            and self.map_view.legend_rect == self._hint_rect
        )
        if not legend_uses_hint:
            hint_text = self._hint_text()
            hint_text = self._fit_text(
                hint_text,
                self.ctx.config.font_size_small,
                self._hint_rect.width,
            )
            hint = self.ctx.fonts.render(
                hint_text,
                self.ctx.config.font_size_small,
                self.ctx.theme.text_mute,
            )
            surface.blit(hint, self._hint_rect.topleft)
        if self.map_view:
            self.map_view.draw(surface)
        if self.close_btn:
            self.close_btn.draw(surface)
        if self.mode_btn and self.mode_btn.visible:
            self.mode_btn.draw(surface)

    def _hint_text(self) -> str:
        focus = self._binding_short(InputAction.FOCUS_NEXT)
        confirm = self._binding_short(InputAction.CONFIRM_ADVANCE)
        nav = " / ".join(
            self._binding_short(action)
            for action in (
                InputAction.NAV_UP,
                InputAction.NAV_DOWN,
                InputAction.NAV_LEFT,
                InputAction.NAV_RIGHT,
            )
        )
        if self._guide_progress is not None:
            progress = self._guide_progress
            if progress["complete"]:
                status = self.ctx.t(
                    "map_progress_complete",
                    "已完成",
                )
            elif progress["completed"] == progress["total"]:
                status = self.ctx.t(
                    "map_progress_pending_wrap",
                    "{completed} / {total} · 尚待本輪收束",
                    completed=progress["completed"],
                    total=progress["total"],
                )
            else:
                status = self.ctx.t(
                    "map_progress_count",
                    "{completed} / {total}",
                    completed=progress["completed"],
                    total=progress["total"],
                )
            return self.ctx.t(
                "map_hint_progress",
                (
                    "{label} · {stage} {status} · 標記會顯示出發時機；"
                    "選擇後查看安排 · {focus}/{controller} 上方控制"
                ),
                label=progress["label"],
                stage=progress["stage"],
                status=status,
                focus=focus,
                controller="Y",
            )
        if self._guided_enabled and self._show_ordinary:
            return self.ctx.t(
                "map_hint_guided_all",
                (
                    "圖例說明故事、目前位置與探索地點；選擇後查看安排"
                    " · {focus}/{controller} 上方控制"
                ),
                focus=focus,
                controller="Y",
            )
        if self._guided_enabled:
            return self.ctx.t(
                "map_hint_guided_story",
                (
                    "故事地點優先；等待時段標記不能立即出發，可先查看安排"
                    " · {focus}/{controller} 上方控制"
                ),
                focus=focus,
                controller="Y",
            )
        return self.ctx.t(
            "map_hint_standard",
            (
                "圖例說明地點狀態；{nav} 選擇，{confirm} 查看安排"
                " · {focus}/{controller} 上方控制"
            ),
            nav=nav,
            confirm=confirm,
            focus=focus,
            controller="Y",
        )

    def _fit_text(self, text: str, size: int, max_width: int) -> str:
        value = str(text)
        if max_width <= 0:
            return ""
        if (
            self.ctx.fonts.render(
                value,
                size,
                self.ctx.theme.text,
            ).get_width()
            <= max_width
        ):
            return value
        while value:
            value = value[:-1]
            candidate = value.rstrip() + "…"
            if (
                self.ctx.fonts.render(
                    candidate,
                    size,
                    self.ctx.theme.text,
                ).get_width()
                <= max_width
            ):
                return candidate
        return "…"

    def describe(self) -> dict:
        cur = self.ctx.state.map.current
        flags = self.ctx.state.events.flags
        time_of_day = self.ctx.state.time.time_of_day.value
        return {
            "scene": "MapScene",
            "current": cur.id if cur else None,
            "exits": [
                e.id for e in
                self.ctx.state.map.available_exits(flags, time_of_day)
            ],
            "focus": {
                "node": (
                    self.map_view.focused_id
                    if self.map_view is not None else None
                ),
                "chrome": (
                    self._chrome_buttons()[self._chrome_focus].label
                    if (
                        0 <= self._chrome_focus
                        < len(self._chrome_buttons())
                    )
                    else None
                ),
            },
            "guided_offers": self._guided_offers,
            "guide_progress": self._guide_progress,
            "view_mode": (
                "all_reachable"
                if self._show_ordinary or not self._guided_enabled
                else "story_only"
            ),
            "show_ordinary": self._show_ordinary,
            "markers": dict(self._marker_kinds),
            "marker_details": {
                node["id"]: {
                    "kind": node.get("marker_kind"),
                    "label": node.get("marker_label"),
                    "travel_available": node.get("travel_available"),
                    "blocked_reason": node.get("blocked_reason"),
                    "portrait": node.get("portrait"),
                }
                for node in (
                    self.map_view.nodes
                    if self.map_view is not None else ()
                )
            },
            "reachable": sorted(self._reachable_ids),
            "spatial_map": (
                {
                    "enabled": True,
                    "background": self._spatial_map.background,
                    "background_available": (
                        self.map_view.background_available
                        if self.map_view is not None else False
                    ),
                    "attribution": self._spatial_map.attribution,
                    "bounds": list(self._spatial_map.bounds),
                    "edge_destinations": {
                        location_id: {
                            "direction": edge.direction,
                            "thumbnail": edge.thumbnail,
                            "caption": edge.caption,
                        }
                        for location_id, edge
                        in self._spatial_map.edge_destinations.items()
                    },
                    "nodes": {
                        location_id: {
                            "layer": node.layer,
                            "parent": node.parent,
                            "label": node.label,
                        }
                        for location_id, node
                        in self._spatial_map.nodes.items()
                    },
                }
                if self._spatial_map is not None else {"enabled": False}
            ),
        }
