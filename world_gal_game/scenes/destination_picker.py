"""Destination detail/list overlay for previewing travel before commitment.

The spatial :class:`MapScene` is the primary travel entry. Selecting one of
its markers opens this scene directly in detail mode; hosts and accessibility
tools may still use its full card list. In the default policy the list contains
direct exits. Packs that opt into ``travel.scope: graph`` can expose distant
destinations as one atomic itinerary. Every destination is a card showing:

- a thumbnail (the location's time-of-day background),
- who is there *right now* (NPC avatars + the lead heroine's affection),
- a "new event" badge when an unplayed scene hook is available there,
- the travel-time cost, and
- a lock state + reason when the exit is gated.

Clicking a card opens a preview-before-commit panel (description, who's
present, what's available, time cost) with a 前往 / 取消 choice — so the
player decides *whether* to go based on who's there, before committing.

Region headers are collapsible, filters (只看現在可去 / 只看有角色) and the
collapsed-region set persist across re-opens via ``state.meta``, and card
hover is eased for a smooth feel. The 世界地圖 action returns to the primary
spatial map.

This scene reads the data model only (``map.all_exits_with_status``,
``map.npcs_present_at``, ``map.scenes_available_at``); it carries no
game-specific logic and adds no required pack fields. Destinations with
no background art fall back to the asset placeholder.
"""
from __future__ import annotations

from typing import Callable

import pygame

from .base import Scene, SceneContext
from ..ui.system_chrome import ChromeRequest
from ..ui.widgets import Button, Panel
from ..core.map_system import Location, Exit
from ..core.guided_destinations import (
    DestinationOffer,
    GuidedDestinationResolver,
)
from ..core.travel import (
    TravelPlan,
    TravelService,
    graph_travel_enabled,
    hidden_travel_tags,
    minimum_travel_cost,
)
from ..ui.input_bindings import BindingProfile, InputAction


# Geometry is authored against the 1280x720 baseline.  FontRegistry already
# scales glyphs on a taller logical canvas; the picker applies the same density
# to every hit target, image, gap, and modal dimension so 1080p does not put
# 1.5x text inside 1.0x containers.
_CARD_W_TARGET = 300
_CARD_GAP = 18
_THUMB_H = 104
_CARD_H = 316
_REGION_HEADER_H = 44
_AVATAR = 52

# Comfortable touch heights for the picker chrome.
_CHROME_BTN_H = 44
_FILTER_H = 44

# Detail-sheet baseline.  Its body and fixed action footer are laid out
# separately; long copy can never paint under the buttons.
_PREVIEW_W = 920
_PREVIEW_H = 500
_PREVIEW_IMAGE_H = 136
_PREVIEW_FOOTER_H = 64

# Persistence keys (kept in state.meta; JSON-safe primitives only).
_META_REACH = "__travel_filter_reachable__"
_META_NPC = "__travel_filter_with_npc__"
_META_COLLAPSED = "__travel_collapsed_regions__"
_META_GUIDED_ALL = "__travel_guided_show_all__"


def _lerp(a: tuple, b: tuple, t: float) -> tuple:
    """Linear-interpolate two RGB(A) colors; missing alpha defaults to 255."""
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    aa = a[3] if len(a) > 3 else 255
    ba = b[3] if len(b) > 3 else 255
    return (int(a[0] + (b[0] - a[0]) * t),
            int(a[1] + (b[1] - a[1]) * t),
            int(a[2] + (b[2] - a[2]) * t),
            int(aa + (ba - aa) * t))


def _wrapped_lines(
    text: str,
    font: pygame.font.Font,
    max_width: int,
) -> list[str]:
    """CJK-safe character wrapping with no implicit drawing overflow."""
    value = str(text or "")
    if not value:
        return []
    max_width = max(1, int(max_width))
    lines: list[str] = []
    for paragraph in value.splitlines() or [""]:
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for char in paragraph:
            candidate = current + char
            if current and font.size(candidate)[0] > max_width:
                lines.append(current)
                current = char
            else:
                current = candidate
        if current:
            lines.append(current)
    return lines


def _bounded_lines(
    text: str,
    font: pygame.font.Font,
    max_width: int,
    max_lines: int,
) -> list[str]:
    """Wrap to ``max_lines`` and ellipsize the final visible row."""
    if max_lines <= 0:
        return []
    lines = _wrapped_lines(text, font, max_width)
    if len(lines) <= max_lines:
        return lines
    visible = lines[:max_lines]
    last = visible[-1].rstrip()
    ellipsis = "…"
    while last and font.size(last + ellipsis)[0] > max_width:
        last = last[:-1].rstrip()
    visible[-1] = last + ellipsis if last else ellipsis
    return visible


def _ellipsized_text(
    text: str,
    font: pygame.font.Font,
    max_width: int,
) -> str:
    """Return one line that fits ``max_width``, adding an ellipsis if needed."""
    lines = _bounded_lines(str(text or ""), font, max_width, 1)
    return lines[0] if lines else ""


class _Dest:
    """One destination row, precomputed for layout + drawing."""

    __slots__ = ("loc", "exit", "available", "reason", "npc_ids",
                 "available_hook_ids", "arrival_hook_ids", "has_new", "cost",
                 "plan", "offer", "crect")

    def __init__(self, loc: Location, exit_obj: Exit, available: bool,
                 reason: str | None, npc_ids: list[str],
                 available_hook_ids: list[str],
                 arrival_hook_ids: list[str], has_new: bool,
                 cost: int, plan: TravelPlan | None,
                 offer: DestinationOffer | None = None):
        self.loc = loc
        self.exit = exit_obj
        self.available = available
        self.reason = reason
        self.npc_ids = npc_ids
        self.available_hook_ids = available_hook_ids
        self.arrival_hook_ids = arrival_hook_ids
        self.has_new = has_new
        self.cost = cost
        self.plan = plan
        self.offer = offer
        self.crect = pygame.Rect(0, 0, 0, 0)   # content-space rect (pre-scroll)


class DestinationPickerScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        self.on_close: Callable[[], None] | None = None
        self.on_move_to: Callable[[str], None] | None = None
        self.on_travel: Callable[[TravelPlan], None] | None = None
        # One call advances exactly one phase and may return truthy when that
        # phase opened a dialogue or otherwise interrupted waiting.  Keeping
        # this as a single-phase callback preserves older hosts while letting
        # the picker safely orchestrate a deliberate multi-phase wait.
        self.on_wait: Callable[[], bool | None] | None = None
        self.on_world_map: Callable[[], None] | None = None

        self._dests: list[_Dest] = []                # full filtered list
        # Visible (non-collapsed) cards: (_Dest, hit-target Button). Kept as
        # real Buttons so click handling is uniform and the dev driver can
        # find a destination by its location name.
        self._cards: list[tuple[_Dest, Button]] = []
        # (content_y, region_key, label) for each region band.
        self._region_headers: list[tuple[int, str | None, str]] = []
        self._content_h = 0
        self._scroll = 0
        self._selected: str | None = None            # loc_id in preview mode
        # A map marker may request one exact ordinary destination while the
        # guided picker remains story-only. This never expands the full
        # secondary layer and still runs through TravelService below.
        self._forced_initial_destination: str | None = None
        self._detail_only = False

        self._buttons: list[Button] = []             # header chrome
        self._preview_buttons: list[Button] = []
        self._hover_anim: dict[str, float] = {}       # loc_id -> eased [0,1]
        # Keyboard/gamepad focus is kept separate from Button's pointer hover
        # state.  Location ids survive scrolling and layout rebuilds more
        # reliably than a positional card index.
        self._focused_card_id: str | None = None
        self._preview_focus = -1
        # Header/filter chrome is reachable with Tab or controller Y, then
        # left/right + confirm. -1 returns directional input to the card grid.
        self._header_focus = -1

        self._filter_reachable = False
        self._filter_with_npc = False
        self._collapsed: set[str | None] = set()
        self._guided_show_all = False

        # Geometry, filled by _recompute_geometry().
        self._panel = pygame.Rect(0, 0, 0, 0)
        self._viewport = pygame.Rect(0, 0, 0, 0)
        self._cols = 1
        self._card_w = _CARD_W_TARGET
        self._title_h = 0
        self._filter_y = 0
        self._density = 1.0
        self._panel_margin = 32
        self._panel_pad = 28
        self._panel_top_pad = 18
        self._card_gap = _CARD_GAP
        self._card_h = _CARD_H
        self._thumb_h = _THUMB_H
        self._region_header_h = _REGION_HEADER_H
        self._avatar = _AVATAR
        self._filter_h = _FILTER_H
        self._chrome_btn_h = _CHROME_BTN_H
        # Draw-time geometry is retained for deterministic layout tests and
        # developer inspection.  It carries rectangles only, never game state.
        self._card_debug_layouts: dict[str, dict[str, pygame.Rect]] = {}
        self._preview_layout: dict[str, object] = {}
        self._preview_reason_scroll = 0

    # ---- lifecycle ------------------------------------------------------

    def enter(self, *, on_close=None, on_move_to=None, on_travel=None,
              on_wait=None,
              on_world_map=None, initial_destination=None,
              detail_only=False, **_) -> None:
        self.on_close = on_close
        self.on_move_to = on_move_to
        self.on_travel = on_travel
        self.on_wait = on_wait
        self.on_world_map = on_world_map
        self._forced_initial_destination = (
            str(initial_destination)
            if initial_destination is not None else None
        )
        self._detail_only = bool(detail_only)
        self._selected = None
        self._focused_card_id = None
        self._preview_focus = -1
        self._header_focus = -1
        self._scroll = 0
        self._load_prefs()
        self._rebuild()
        target = self._forced_initial_destination
        if target is not None and any(
            d.loc.id == target for d in self._dests
        ):
            self._open_preview(target)

    def resume(self) -> None:
        self._rebuild()

    # ---- persistence ----------------------------------------------------

    def _load_prefs(self) -> None:
        m = self.ctx.state.meta
        # A pack can opt new playthroughs into the low-cognitive-load view
        # without taking the filter away from the player. Once they toggle it,
        # the save-local preference below remains authoritative.
        default_reachable = bool(
            self.ctx.meta.get("travel_default_reachable_only", False)
        )
        self._filter_reachable = bool(
            m.get(_META_REACH, default_reachable)
        )
        self._filter_with_npc = bool(m.get(_META_NPC, False))
        self._guided_show_all = bool(m.get(_META_GUIDED_ALL, False))
        collapsed = m.get(_META_COLLAPSED) or []
        # "" is the JSON-safe sentinel for the region-less ("其他") group.
        self._collapsed = {None if k == "" else k for k in collapsed}

    def _save_prefs(self) -> None:
        m = self.ctx.state.meta
        m[_META_REACH] = self._filter_reachable
        m[_META_NPC] = self._filter_with_npc
        m[_META_GUIDED_ALL] = self._guided_show_all
        m[_META_COLLAPSED] = sorted(
            "" if k is None else k for k in self._collapsed)

    # ---- data + layout --------------------------------------------------

    def _sp(self, value: int | float, *, minimum: int = 1) -> int:
        """Scale one 720p-authored metric with the active FontRegistry."""
        return max(minimum, int(round(float(value) * self._density)))

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

    def _recompute_geometry(self) -> None:
        sw, sh = self.ctx.screen_size
        font_density = float(getattr(self.ctx.fonts, "scale", 1.0))
        self._density = max(1.0, font_density)
        self._panel_margin = self._sp(32)
        self._panel_pad = self._sp(28)
        self._panel_top_pad = self._sp(18)
        self._card_gap = self._sp(_CARD_GAP)
        self._card_h = self._sp(_CARD_H)
        self._thumb_h = self._sp(_THUMB_H)
        self._region_header_h = self._sp(_REGION_HEADER_H)
        self._avatar = self._sp(_AVATAR)
        self._filter_h = self._sp(_FILTER_H)
        self._chrome_btn_h = self._sp(_CHROME_BTN_H)

        margin = min(
            self._panel_margin,
            max(8, min(sw, sh) // 8),
        )
        self._panel = pygame.Rect(
            margin,
            margin,
            max(1, sw - margin * 2),
            max(1, sh - margin * 2),
        )
        pad = min(self._panel_pad, max(8, self._panel.width // 8))
        # Header height is scale-aware: the title font grows with resolution,
        # so measure it and place the filter row (and the card viewport)
        # below the actual title rather than at a fixed offset.
        self._title_h = self.ctx.fonts.render(
            self.ctx.t("destination_title", "要去哪裡？"),
            self.ctx.config.font_size_header,
            self.ctx.theme.accent, bold=True).get_height()
        self._filter_y = (
            self._panel.y
            + self._panel_top_pad
            + self._title_h
            + self._sp(12)
        )
        header_bottom = self._filter_y + self._filter_h + self._sp(14)
        footer_h = self._sp(46)
        self._viewport = pygame.Rect(
            self._panel.x + pad,
            header_bottom,
            self._panel.width - pad * 2,
            max(1, self._panel.bottom - footer_h - header_bottom),
        )
        # Column count adapts to viewport width (responsive on narrow /
        # portrait screens, which fall to fewer columns).
        vw = self._viewport.width
        target_w = self._sp(_CARD_W_TARGET)
        self._cols = max(
            1,
            (vw + self._card_gap) // (target_w + self._card_gap),
        )
        self._card_w = max(
            1,
            (
                vw - self._card_gap * (self._cols - 1)
            ) // self._cols,
        )

    def _uses_graph_scope(self) -> bool:
        """Whether this pack currently opts into atomic distant travel."""
        return graph_travel_enabled(
            self.ctx.meta,
            self.ctx.state.events.flags,
        )

    def _gather(self) -> list[_Dest]:
        st = self.ctx.state
        flags = st.events.flags
        tod = st.time.time_of_day.value
        infos = st.map.all_exits_with_status(flags, tod)
        direct_by_target: dict[str, tuple[Exit, Location, str | None]] = {}
        for exit_obj, loc, _available, reason in infos:
            direct_by_target.setdefault(loc.id, (exit_obj, loc, reason))

        graph_scope = self._uses_graph_scope()
        resolver = GuidedDestinationResolver(st, self.ctx.meta)
        hidden_tags = hidden_travel_tags(self.ctx.meta)
        forced_id = self._forced_initial_destination

        # A world-map click already identifies the only destination whose
        # confirmation details are needed. Validate it before any resolver or
        # arrival preview work, then quote exactly that target. This keeps a
        # detail sheet independent of total map size and prevents hidden or
        # stale marker ids from triggering speculative condition evaluation.
        if self._detail_only:
            forced = st.map.locations.get(forced_id) if forced_id else None
            if (
                forced is None
                or forced.id == st.map.current_location_id
                or hidden_tags.intersection(forced.tags)
            ):
                return []
            candidates = [forced]
            targeted_offer = (
                resolver.resolve_target(forced.id)
                if resolver.enabled else None
            )
            resolved_offers = (
                (targeted_offer,) if targeted_offer is not None else ()
            )
        else:
            resolved_offers = (
                (
                    resolver.resolve()
                    if self._guided_show_all
                    else resolver.primary()
                )
                if resolver.enabled else ()
            )
            if graph_scope:
                if resolver.enabled and not self._guided_show_all:
                    candidates = [
                        st.map.locations[offer.target]
                        for offer in resolved_offers
                        if offer.target in st.map.locations
                    ]
                else:
                    candidates = [
                        loc for loc in st.map.locations.values()
                        if loc.id != st.map.current_location_id
                        and not hidden_tags.intersection(loc.tags)
                    ]
            else:
                candidates = [
                    row[1]
                    for row in direct_by_target.values()
                    if not hidden_tags.intersection(row[1].tags)
                ]
        offers = {offer.target: offer for offer in resolved_offers}

        # MapScene can expose a configured off-campus/ordinary marker without
        # pretending it is a primary story offer. Include exactly that one
        # destination so its existing detail sheet can quote the real plan.
        forced = st.map.locations.get(forced_id) if forced_id else None
        if (
            not self._detail_only
            and forced is not None
            and forced.id != st.map.current_location_id
            and not hidden_tags.intersection(forced.tags)
            and all(loc.id != forced.id for loc in candidates)
        ):
            candidates.append(forced)

        out: list[_Dest] = []
        for loc in candidates:
            offer = offers.get(loc.id)
            direct = direct_by_target.get(loc.id)
            exit_obj = direct[0] if direct is not None else Exit(
                target=loc.id,
                label=loc.name,
            )
            direct_reason = direct[2] if direct is not None else None
            plan = (
                offer.plan
                if offer is not None
                else TravelService.plan(
                    st,
                    loc.id,
                    allow_distant=graph_scope,
                    minimum_cost=(
                        minimum_travel_cost(self.ctx.meta)
                        if graph_scope else 0
                    ),
                )
            )
            available = (
                offer.available if offer is not None else plan is not None
            )
            npc_ids: list[str] = []
            available_hook_ids: list[str] = []
            hook_ids: list[str] = []
            if plan is not None:
                preview = TravelService.preview(st, plan)
                npc_ids = list(preview.npc_ids)
                available_hook_ids = list(preview.available_hook_ids)
                hook_ids = list(preview.arrival_hook_ids)
            if offer is not None and offer.event is not None:
                npc_ids = list(offer.event.npc_ids)
            if offer is not None:
                reason = offer.blocked_reason
            elif available:
                reason = None
            elif direct_reason:
                reason = direct_reason
            elif not loc.is_accessible(flags):
                reason = self.ctx.t(
                    "map_not_yet_available",
                    "尚未開放",
                )
            else:
                reason = self.ctx.t(
                    "map_no_viable_route",
                    "目前沒有可行路線",
                )
            out.append(_Dest(
                loc,
                exit_obj,
                available,
                reason,
                npc_ids,
                available_hook_ids,
                hook_ids,
                (
                    offer.event is not None
                    if offer is not None else bool(available_hook_ids)
                ),
                plan.total_cost if plan is not None else exit_obj.travel_cost,
                plan,
                offer,
            ))
        return out

    def _rebuild(self) -> None:
        self._recompute_geometry()
        dests = self._gather()
        resolver = GuidedDestinationResolver(
            self.ctx.state,
            self.ctx.meta,
        )
        if resolver.enabled and not self._guided_show_all:
            dests = [
                d for d in dests
                if (
                    d.loc.id == self._forced_initial_destination
                    or (
                        d.offer is not None
                        and d.offer.visibility == "primary"
                    )
                )
            ]
        if self._filter_reachable:
            dests = [
                d for d in dests
                if (
                    d.loc.id == self._forced_initial_destination
                    or d.available
                    or (
                        d.offer is not None
                        and d.offer.visibility == "primary"
                    )
                )
            ]
        if self._filter_with_npc:
            dests = [
                d for d in dests
                if (
                    d.loc.id == self._forced_initial_destination
                    or d.npc_ids
                    or (
                        d.offer is not None
                        and d.offer.visibility == "primary"
                    )
                )
            ]
        self._layout(dests)
        self._build_header_buttons()
        self._clamp_scroll()   # filters / collapse can shrink content
        # Drop hover state for cards that no longer exist.
        self._hover_anim = {d.loc.id: self._hover_anim.get(d.loc.id, 0.0)
                            for d in dests}
        visible_ids = {d.loc.id for d, _button in self._cards}
        if self._focused_card_id not in visible_ids:
            self._focused_card_id = None

    def _layout(self, dests: list[_Dest]) -> None:
        """Assign content-space rects, grouped by region in arrival order.

        Collapsed regions contribute only their header band; their member
        cards are skipped (no rect, no hit-target).
        """
        order: list[str | None] = []
        groups: dict[str | None, list[_Dest]] = {}
        for d in dests:
            key = d.loc.region
            if key not in groups:
                groups[key] = []
                order.append(key)
        for d in dests:
            groups[d.loc.region].append(d)

        regions = self.ctx.state.map.regions
        # A single unnamed group needs no header band (and can't collapse).
        show_headers = not (len(order) == 1 and order[0] is None)

        self._region_headers = []
        self._cards = []
        y = 0
        for key in order:
            members = groups[key]
            collapsed = show_headers and key in self._collapsed
            if show_headers:
                if key and key in regions:
                    rname = regions[key].name or key
                elif key:
                    rname = key
                else:
                    rname = self.ctx.t(
                        "destination_other_region",
                        "其他",
                    )
                label = rname
                if collapsed:
                    label = self.ctx.t(
                        "travel_destinations_count",
                        "{label}（{count} 個地點）",
                        label=label,
                        count=len(members),
                    )
                self._region_headers.append((y, key, label))
                y += self._region_header_h
            if collapsed:
                continue
            for i, d in enumerate(members):
                col = i % self._cols
                row = i // self._cols
                x = (
                    self._viewport.x
                    + col * (self._card_w + self._card_gap)
                )
                cy = y + row * (self._card_h + self._card_gap)
                d.crect = pygame.Rect(x, cy, self._card_w, self._card_h)
                btn = Button(pygame.Rect(d.crect), d.loc.name,
                             fonts=self.ctx.fonts, theme=self.ctx.theme,
                             font_size=15,
                             on_click=(lambda lid=d.loc.id:
                                       self._open_preview(lid)),
                             chrome=self.ctx.ui_chrome,
                             screen="travel",
                             role="destination",
                             motion=self.ctx.motion)
                self._cards.append((d, btn))
            rows = (len(members) + self._cols - 1) // self._cols
            y += (
                rows * (self._card_h + self._card_gap)
                + self._card_gap
            )
        self._content_h = max(0, y)
        self._dests = dests

    def _build_header_buttons(self) -> None:
        p = self._panel
        self._buttons = []
        edge = self._sp(16)
        top = p.y + self._sp(14)
        close_label = self.ctx.t(
            "menu_item_with_binding",
            "{label} ({binding})",
            label=self.ctx.t("close", "關閉"),
            binding=self._binding_short(InputAction.BACK),
        )
        close_w = max(
            self._sp(120),
            self.ctx.fonts.get(15, bold=True).size(close_label)[0]
            + self._sp(24),
        )
        close = Button(
            pygame.Rect(
                p.right - close_w - edge,
                top,
                close_w,
                self._chrome_btn_h,
            ),
            close_label,
            fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=15, style="ghost",
            on_click=(lambda: self.on_close() if self.on_close else None),
            chrome=self.ctx.ui_chrome,
            screen="travel",
            role="close",
            motion=self.ctx.motion)
        self._buttons.append(close)
        if self.on_world_map:
            world_label = self.ctx.t(
                "destination_world_map",
                "世界地圖",
            )
            world_w = max(
                self._sp(132),
                self.ctx.fonts.get(15, bold=True).size(world_label)[0]
                + self._sp(24),
            )
            world_gap = self._sp(12)
            wm = Button(
                pygame.Rect(
                    close.rect.left - world_gap - world_w,
                    top,
                    world_w,
                    self._chrome_btn_h,
                ),
                world_label, fonts=self.ctx.fonts, theme=self.ctx.theme,
                font_size=15, style="ghost", on_click=self.on_world_map,
                chrome=self.ctx.ui_chrome,
                screen="travel",
                role="map",
                motion=self.ctx.motion)
            self._buttons.append(wm)
        # Filter chips, left-aligned below the title (scale-aware row).
        fx = p.x + self._panel_pad
        fy = self._filter_y
        filter_gap = self._sp(12)
        filter_font = self.ctx.fonts.get(14, bold=True)
        reach_label = self.ctx.t(
            "destination_reachable_only",
            "只看現在可去",
        )
        reach_w = max(
            self._sp(158),
            filter_font.size(reach_label)[0] + self._sp(24),
        )
        reach = Button(
            pygame.Rect(fx, fy, reach_w, self._filter_h), reach_label,
            fonts=self.ctx.fonts, theme=self.ctx.theme, font_size=14,
            style=("primary" if self._filter_reachable else "ghost"),
            on_click=self._toggle_reachable,
            chrome=self.ctx.ui_chrome,
            screen="travel",
            role="filter",
            selected=self._filter_reachable,
            motion=self.ctx.motion)
        self._buttons.append(reach)
        npc_label = self.ctx.t(
            "destination_with_characters_only",
            "只看有角色",
        )
        npc_w = max(
            self._sp(140),
            filter_font.size(npc_label)[0] + self._sp(24),
        )
        withnpc = Button(
            pygame.Rect(
                reach.rect.right + filter_gap,
                fy,
                npc_w,
                self._filter_h,
            ),
            npc_label,
            fonts=self.ctx.fonts, theme=self.ctx.theme, font_size=14,
            style=("primary" if self._filter_with_npc else "ghost"),
            on_click=self._toggle_with_npc,
            chrome=self.ctx.ui_chrome,
            screen="travel",
            role="filter",
            selected=self._filter_with_npc,
            motion=self.ctx.motion)
        self._buttons.append(withnpc)
        resolver = GuidedDestinationResolver(
            self.ctx.state,
            self.ctx.meta,
        )
        if resolver.enabled:
            all_label = (
                self.ctx.t(
                    "map_story_locations_only",
                    "只看故事地點",
                )
                if self._guided_show_all
                else self.ctx.t(
                    "map_show_ordinary_exploration",
                    "顯示一般探索",
                )
            )
            all_w = max(
                self._sp(154),
                filter_font.size(all_label)[0] + self._sp(24),
            )
            all_locations = Button(
                pygame.Rect(
                    withnpc.rect.right + filter_gap,
                    fy,
                    all_w,
                    self._filter_h,
                ),
                all_label,
                fonts=self.ctx.fonts,
                theme=self.ctx.theme,
                font_size=14,
                style=("primary" if self._guided_show_all else "ghost"),
                on_click=self._toggle_guided_all,
                chrome=self.ctx.ui_chrome,
                screen="travel",
                role="filter",
                selected=self._guided_show_all,
                motion=self.ctx.motion,
            )
            self._buttons.append(all_locations)
        if self._collapsed:
            expand_label = self.ctx.t(
                "destination_expand_all_regions",
                "展開全部",
            )
            expand_w = max(
                self._sp(116),
                filter_font.size(expand_label)[0] + self._sp(24),
            )
            previous = self._buttons[-1]
            expand = Button(
                pygame.Rect(
                    previous.rect.right + filter_gap,
                    fy,
                    expand_w,
                    self._filter_h,
                ),
                expand_label,
                fonts=self.ctx.fonts,
                theme=self.ctx.theme,
                font_size=14,
                style="ghost",
                on_click=self._expand_all_regions,
                chrome=self.ctx.ui_chrome,
                screen="travel",
                role="filter",
                motion=self.ctx.motion,
            )
            self._buttons.append(expand)
        self._sync_header_focus()

    def _toggle_reachable(self) -> None:
        self._filter_reachable = not self._filter_reachable
        self._save_prefs()
        self._rebuild()

    def _toggle_with_npc(self) -> None:
        self._filter_with_npc = not self._filter_with_npc
        self._save_prefs()
        self._rebuild()

    def _toggle_guided_all(self) -> None:
        self._guided_show_all = not self._guided_show_all
        self._save_prefs()
        self._rebuild()

    def _toggle_region(self, key: str | None) -> None:
        if key in self._collapsed:
            self._collapsed.discard(key)
        else:
            self._collapsed.add(key)
        self._save_prefs()
        self._rebuild()

    def _expand_all_regions(self) -> None:
        self._collapsed.clear()
        self._save_prefs()
        self._rebuild()

    # ---- preview --------------------------------------------------------

    def _open_preview(self, loc_id: str) -> None:
        self._focused_card_id = loc_id
        self._selected = loc_id
        self._preview_reason_scroll = 0
        self._build_preview_buttons()

    def _close_preview(self) -> None:
        if self._detail_only and self.on_close is not None:
            self.on_close()
            return
        self._selected = None
        self._preview_buttons = []
        self._preview_layout = {}
        self._preview_reason_scroll = 0
        self._preview_focus = -1

    def _selected_dest(self) -> _Dest | None:
        if self._selected is None:
            return None
        return next((d for d in self._dests if d.loc.id == self._selected), None)

    def _preview_action_state(self, d: _Dest) -> tuple[str, bool]:
        action = d.offer.action if d.offer is not None else "travel"
        if action == "wait":
            return (
                self._future_wait_label(d),
                d.available and self.on_wait is not None,
            )
        if action == "none":
            return (
                self.ctx.t(
                    "destination_cannot_plan",
                    "目前無法安排",
                ),
                False,
            )
        return (
            self.ctx.t("destination_travel", "前往"),
            d.available,
        )

    @staticmethod
    def _future_wait_phases(d: _Dest) -> int:
        """Return the advisory wait count, with a legacy one-phase floor."""
        future = d.offer.future if d.offer is not None else None
        if future is None:
            return 1
        return max(1, int(future.wait_phases))

    def _future_wait_label(self, d: _Dest) -> str:
        """Describe the exact departure moment promised by the future offer."""
        future = d.offer.future if d.offer is not None else None
        if future is None:
            return self.ctx.t(
                "destination_wait_one_slot",
                "等待一個時段",
            )
        departure = future.departure
        return self.ctx.t(
            "destination_wait_until",
            "等待至第 {day} 天 {time_of_day}（共 {count} 時段）",
            day=departure.day,
            time_of_day=self.ctx.localization.time_label(
                departure.time_of_day.value,
            ),
            count=self._future_wait_phases(d),
        )

    def _build_preview_buttons(self) -> None:
        d = self._selected_dest()
        if d is None:
            self._preview_buttons = []
            self._preview_layout = {}
            return
        action_label, action_enabled = self._preview_action_state(d)
        layout = self._compute_preview_layout(d, action_label)
        self._preview_layout = layout
        self._preview_reason_scroll = max(
            0,
            min(
                self._preview_reason_scroll,
                int(layout["reason_scroll_max"]),
            ),
        )
        go_rect = pygame.Rect(layout["go_button"])
        cancel_rect = pygame.Rect(layout["cancel_button"])
        go = Button(
            go_rect,
            action_label, fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=18, style="primary", enabled=action_enabled,
            on_click=self._confirm,
            chrome=self.ctx.ui_chrome,
            screen="travel",
            role="confirm",
            motion=self.ctx.motion)
        cancel = Button(
            cancel_rect,
            self.ctx.t("cancel", "取消"),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=18, style="ghost", on_click=self._close_preview,
            chrome=self.ctx.ui_chrome,
            screen="travel",
            role="cancel",
            motion=self.ctx.motion)
        self._preview_buttons = [go, cancel]
        enabled = [i for i, button in enumerate(self._preview_buttons)
                   if button.enabled]
        # Previewing a destination must never turn a confirm button into an
        # accidental travel commit.  Keep the conservative action selected
        # while left/right still exposes the primary action identically for
        # keyboard and controller users.
        cancel_index = 1
        self._preview_focus = (
            cancel_index
            if cancel_index in enabled
            else enabled[0] if enabled else -1
        )

    def _preview_size(self) -> tuple[int, int]:
        sw, sh = self.ctx.screen_size
        margin_x = self._sp(64)
        margin_y = self._sp(40)
        return (
            max(1, min(self._sp(_PREVIEW_W), sw - margin_x * 2)),
            max(1, min(self._sp(_PREVIEW_H), sh - margin_y * 2)),
        )

    def _compute_preview_layout(
        self,
        d: _Dest,
        action_label: str,
    ) -> dict[str, object]:
        """Measure the sheet once so body copy never enters the footer."""
        sw, sh = self.ctx.screen_size
        pw, ph = self._preview_size()
        panel = pygame.Rect((sw - pw) // 2, (sh - ph) // 2, pw, ph)
        edge = self._sp(4)
        # Pack chrome may carry a small document/header label at the top of
        # the panel. Keep destination art below that band so the final
        # composed confirmation screen cannot paint over pack-owned chrome.
        chrome_header_h = (
            self._sp(28) if self.ctx.ui_chrome is not None else 0
        )
        image_top = panel.y + edge + chrome_header_h
        inset = self._sp(24)
        vertical_gap = self._sp(10)
        summary_pad_x = self._sp(14)
        summary_pad_y = self._sp(8)
        footer_h = min(self._sp(_PREVIEW_FOOTER_H), max(1, ph // 3))
        footer = pygame.Rect(
            panel.x,
            panel.bottom - footer_h,
            panel.width,
            footer_h,
        )

        content_w = max(1, panel.width - inset * 2)
        teaser_text = (
            d.offer.event.teaser
            if d.offer is not None
            and d.offer.event is not None
            and d.offer.event.teaser
            else d.loc.description
        ) or ""
        teaser_font = self.ctx.fonts.get(14)
        teaser_lines = _bounded_lines(
            teaser_text,
            teaser_font,
            content_w,
            2,
        )
        teaser_line_h = teaser_font.get_linesize() + self._sp(2)
        teaser_h = len(teaser_lines) * teaser_line_h

        reason_text = ""
        if d.reason:
            reason_text = (
                self.ctx.t(
                    "destination_arrangement_hint_reason",
                    "安排提示：{reason}",
                    reason=d.reason,
                )
                if d.offer is not None and d.offer.status == "future"
                else self.ctx.t(
                    "destination_cannot_travel_reason",
                    "無法前往：{reason}",
                    reason=d.reason,
                )
            )
        reason_font = self.ctx.fonts.get(14)
        reason_lines = _wrapped_lines(
            reason_text,
            reason_font,
            max(1, content_w - summary_pad_x * 2),
        )
        itinerary_lines = self._preview_itinerary_lines(d)
        status_line_h = reason_font.get_linesize() + self._sp(2)
        status_content_h = max(1, len(itinerary_lines)) * status_line_h
        if reason_lines:
            status_content_h += (
                self._sp(4) + len(reason_lines) * status_line_h
            )
        status_card_h = status_content_h + summary_pad_y * 2

        npc_count = min(3, len(d.npc_ids))
        display_hook_ids = (
            d.arrival_hook_ids
            if d.arrival_hook_ids
            else d.available_hook_ids
        )
        hook_count = min(3, len(display_hook_ids))
        details_head_h = self.ctx.fonts.get(15, bold=True).get_linesize()
        npc_row_h = max(
            self._sp(42),
            self.ctx.fonts.get(15).get_linesize() + self._sp(12),
        )
        hook_row_h = self.ctx.fonts.get(14).get_linesize() + self._sp(5)
        details_need_h = max(
            details_head_h + self._sp(6)
            + max(1, npc_count) * npc_row_h,
            details_head_h + self._sp(6)
            + max(1, hook_count) * hook_row_h,
        )
        title_h = self.ctx.fonts.get(28, bold=True).get_linesize()
        header_h = title_h
        if teaser_lines:
            header_h += self._sp(6) + teaser_h

        image_h = self._sp(_PREVIEW_IMAGE_H)
        image_min_h = self._sp(104)
        image_floor_h = self._sp(64)
        body_top = image_top + image_h + self._sp(12)
        body_bottom = footer.top - self._sp(12)
        available_body_h = max(1, body_bottom - body_top)
        body_need_h = (
            header_h
            + vertical_gap
            + details_need_h
            + vertical_gap
            + status_card_h
        )
        if body_need_h > available_body_h:
            image_h = max(
                image_min_h,
                image_h - (body_need_h - available_body_h),
            )
            body_top = image_top + image_h + self._sp(12)
            available_body_h = max(1, body_bottom - body_top)
        if body_need_h > available_body_h and len(teaser_lines) > 1:
            teaser_lines = _bounded_lines(
                teaser_text,
                teaser_font,
                content_w,
                1,
            )
            teaser_h = len(teaser_lines) * teaser_line_h
            header_h = title_h + (
                self._sp(6) + teaser_h if teaser_lines else 0
            )
            body_need_h = (
                header_h
                + vertical_gap
                + details_need_h
                + vertical_gap
                + status_card_h
            )
        if body_need_h > available_body_h:
            image_h = max(
                image_floor_h,
                image_h - (body_need_h - available_body_h),
            )

        image = pygame.Rect(
            panel.x + edge,
            image_top,
            max(1, panel.width - edge * 2),
            image_h,
        )
        body = pygame.Rect(
            panel.x + inset,
            image.bottom + self._sp(12),
            content_w,
            max(1, footer.top - self._sp(12) - image.bottom - self._sp(12)),
        )
        # Always preserve the complete 3-NPC / 3-event grid. If a truly long
        # reason needs more room than remains, its viewport scrolls instead of
        # pushing those rows into the status or action footer.
        fixed_body_h = (
            header_h
            + vertical_gap
            + details_need_h
            + vertical_gap
        )
        minimum_status_card_h = status_line_h + summary_pad_y * 2
        status_card_h = min(
            status_card_h,
            max(
                minimum_status_card_h,
                body.height - fixed_body_h,
            ),
        )
        status_card_h = max(1, min(status_card_h, body.height))
        title = pygame.Rect(body.x, body.y, body.width, title_h)
        teaser = pygame.Rect(
            body.x,
            title.bottom + (self._sp(6) if teaser_lines else 0),
            body.width,
            teaser_h,
        )
        header_bottom = teaser.bottom if teaser_lines else title.bottom
        status_card = pygame.Rect(
            body.x,
            body.bottom - status_card_h,
            body.width,
            status_card_h,
        )
        status = pygame.Rect(
            status_card.x + summary_pad_x,
            status_card.y + summary_pad_y,
            max(1, status_card.width - summary_pad_x * 2),
            max(1, status_card.height - summary_pad_y * 2),
        )
        details_top = header_bottom + vertical_gap
        details = pygame.Rect(
            body.x,
            details_top,
            body.width,
            max(1, status_card.top - vertical_gap - details_top),
        )
        reason_view_y = min(
            status.bottom,
            status.y
            + max(1, len(itinerary_lines)) * status_line_h
            + self._sp(4),
        )
        reason_view = pygame.Rect(
            status.x,
            reason_view_y,
            status.width,
            max(0, status.bottom - reason_view_y),
        )
        reason_content_h = len(reason_lines) * status_line_h
        reason_scroll_max = max(0, reason_content_h - reason_view.height)

        column_gap = self._sp(24)
        left_w = max(1, (details.width - column_gap) // 2)
        left_column = pygame.Rect(
            details.x,
            details.y,
            left_w,
            details.height,
        )
        right_column = pygame.Rect(
            left_column.right + column_gap,
            details.y,
            max(1, details.right - left_column.right - column_gap),
            details.height,
        )

        button_font = self.ctx.fonts.get(18, bold=True)
        button_h = max(
            self._sp(44),
            button_font.get_linesize() + self._sp(12),
        )
        button_gap = self._sp(10)
        cancel_w = max(
            self._sp(140),
            button_font.size(self.ctx.t("cancel", "取消"))[0]
            + self._sp(32),
        )
        action_w = max(
            self._sp(180),
            button_font.size(action_label)[0] + self._sp(32),
        )
        button_y = footer.y + (footer.height - button_h) // 2
        cancel = pygame.Rect(
            footer.right - inset - cancel_w,
            button_y,
            cancel_w,
            button_h,
        )
        go = pygame.Rect(
            cancel.left - button_gap - action_w,
            button_y,
            action_w,
            button_h,
        )

        return {
            "panel": panel,
            "chrome_header": pygame.Rect(
                panel.x + edge,
                panel.y + edge,
                max(1, panel.width - edge * 2),
                chrome_header_h,
            ),
            "image": image,
            "body": body,
            "title": title,
            "teaser": teaser,
            "details": details,
            "left_column": left_column,
            "right_column": right_column,
            "status_card": status_card,
            "status": status,
            "reason_view": reason_view,
            "footer": footer,
            "go_button": go,
            "cancel_button": cancel,
            "teaser_lines": tuple(teaser_lines),
            # The full reason is intentionally retained.  Card copy is bounded;
            # the confirmation sheet is the authoritative explanation.
            "reason_lines": tuple(reason_lines),
            "itinerary_lines": tuple(itinerary_lines),
            "reason_content_h": reason_content_h,
            "reason_scroll_max": reason_scroll_max,
            "display_hook_ids": tuple(display_hook_ids[:3]),
            "npc_row_h": npc_row_h,
            "npc_avatar_size": self._sp(38),
            "hook_row_h": hook_row_h,
        }

    def _confirm(self) -> None:
        d = self._selected_dest()
        if d is None or not d.available:
            return
        if d.offer is not None and d.offer.action == "wait":
            if self.on_wait is None:
                return
            wait_phases = self._future_wait_phases(d)
            # A detail-only picker is a SceneManager overlay. Close it before
            # advancing the clock because the wait callback may queue an
            # ambient DialogueScene. Queuing ``push`` then ``pop`` would pop
            # that new dialogue and leave this stale confirmation sheet on
            # screen; ``pop`` then ``push`` preserves the event.
            self._close_preview()
            # Never batch the clock mutation: each phase re-runs the host's
            # availability and ambient-hook checks.  A truthy result marks a
            # dialogue or other interruption, so the remaining phases cannot
            # skip a player-visible scene.
            for _phase in range(wait_phases):
                if self.on_wait():
                    break
            if not self._detail_only:
                self._rebuild()
            return
        if d.offer is not None and d.offer.action != "travel":
            return
        if self.on_travel and d.plan is not None:
            self.on_travel(d.plan)
        elif self.on_move_to:
            self.on_move_to(d.loc.id)

    # ---- input ----------------------------------------------------------

    @property
    def focused_card_id(self) -> str | None:
        """Location id currently targeted by keyboard/gamepad navigation."""
        return self._focused_card_id

    @property
    def focused_preview_index(self) -> int | None:
        """Focused preview button index, or ``None`` outside preview mode."""
        if (self._selected is not None
                and 0 <= self._preview_focus < len(self._preview_buttons)):
            return self._preview_focus
        return None

    @staticmethod
    def _navigation_direction(inp) -> tuple[int, int] | None:
        """Return one logical D-pad direction from this frame's input.

        Keyboard, D-pad, stick, and rebinding all reach this scene through the
        same semantic actions. ``InputState.nav`` remains as a narrow
        compatibility fallback for older custom hosts.
        """
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

    def update(self, dt: float, inp) -> None:
        if self._selected is not None:
            if inp.cancel:
                self._close_preview()
                return

            reason_view = self._preview_layout.get("reason_view")
            reason_scroll_max = int(
                self._preview_layout.get("reason_scroll_max", 0)
            )
            page_direction = 0
            pressed = getattr(inp, "pressed", None)
            if callable(pressed):
                if pressed(InputAction.PAGE_UP):
                    page_direction = -1
                elif pressed(InputAction.PAGE_DOWN):
                    page_direction = 1
            if reason_scroll_max > 0 and page_direction:
                reason_height = (
                    pygame.Rect(reason_view).height
                    if reason_view is not None else self._sp(72)
                )
                self._preview_reason_scroll += (
                    page_direction * max(self._sp(36), reason_height)
                )
                self._preview_reason_scroll = max(
                    0,
                    min(self._preview_reason_scroll, reason_scroll_max),
                )
            if (
                reason_view is not None
                and reason_scroll_max > 0
                and int(getattr(inp, "mouse_wheel", 0))
                and pygame.Rect(reason_view).collidepoint(inp.mouse_pos)
            ):
                self._preview_reason_scroll -= (
                    int(inp.mouse_wheel) * self._sp(36)
                )
                self._preview_reason_scroll = max(
                    0,
                    min(self._preview_reason_scroll, reason_scroll_max),
                )

            direction = self._navigation_direction(inp)
            if direction is not None:
                self._move_preview_focus(direction)
            if any(event.type == pygame.MOUSEMOTION
                   for event in getattr(inp, "events", ())):
                hovered = next(
                    (i for i, button in enumerate(self._preview_buttons)
                     if button.enabled
                     and button.rect.collidepoint(inp.mouse_pos)),
                    None,
                )
                self._preview_focus = hovered if hovered is not None else -1
            if inp.confirm and self._activate_preview_focus():
                return

            # Work on a snapshot: clicking 取消 clears the scene-owned list.
            for b in list(self._preview_buttons):
                b.update(dt, inp)
            return

        if inp.cancel:
            if self.on_close:
                self.on_close()
            return

        # Card rects live in content space; keep their Button hit targets in
        # screen space before either pointer or keyboard handling.
        for d, btn in self._cards:
            btn.rect = self._card_screen_rect(d)

        focus_step = self._focus_step(inp)
        if focus_step:
            self._move_header_focus(focus_step)
            return

        direction = self._navigation_direction(inp)
        if self._header_focus >= 0:
            if direction is not None:
                dx, dy = direction
                if dy > 0:
                    self._clear_header_focus()
                    if self._cards and self._focused_card_id is None:
                        self._focused_card_id = self._cards[0][0].loc.id
                else:
                    step = 1 if dx > 0 else -1
                    if dx or dy:
                        self._move_header_focus(step)
                return
            if inp.confirm and self._activate_header_focus():
                return
        elif direction is not None:
            self._move_card_focus(direction)
        if any(event.type == pygame.MOUSEMOTION
               for event in getattr(inp, "events", ())):
            self._clear_header_focus()
            hovered = next(
                (d.loc.id for d, button in self._cards
                 if self._viewport.colliderect(button.rect)
                 and button.rect.collidepoint(inp.mouse_pos)),
                None,
            )
            self._focused_card_id = hovered
        if (
            self._header_focus < 0
            and inp.confirm
            and self._focused_card_id is not None
        ):
            focused = next(
                (d for d, _button in self._cards
                 if d.loc.id == self._focused_card_id),
                None,
            )
            if focused is not None:
                self._open_preview(focused.loc.id)
                return

        for b in list(self._buttons):
            b.update(dt, inp)
        # Wheel scroll while pointer is over the card viewport.
        if self._viewport.collidepoint(inp.mouse_pos):
            self._scroll -= int(getattr(inp, "mouse_wheel", 0)) * 48
            self._clamp_scroll()
        # Region header collapse toggles (checked before cards; bands and
        # cards never overlap, but return early to be safe).
        if inp.mouse_clicked and self._viewport.collidepoint(inp.mouse_pos):
            for cy, key, _label in self._region_headers:
                sy = self._viewport.y + cy - self._scroll
                band = pygame.Rect(self._viewport.x, sy,
                                   self._viewport.width,
                                   self._region_header_h)
                if band.collidepoint(inp.mouse_pos) \
                        and self._viewport.y <= sy <= self._viewport.bottom:
                    self._toggle_region(key)
                    return
        # Cards: sync hit-targets, update visible ones, track hover for easing.
        hovered: str | None = None
        for d, btn in self._cards:
            btn.rect = self._card_screen_rect(d)
            if btn.rect.bottom >= self._viewport.y \
                    and btn.rect.top <= self._viewport.bottom:
                btn.update(dt, inp)
                if btn.rect.collidepoint(inp.mouse_pos):
                    hovered = d.loc.id
        for d, _btn in self._cards:
            cur = self._hover_anim.get(d.loc.id, 0.0)
            tgt = 1.0 if (d.loc.id == hovered
                          or d.loc.id == self._focused_card_id) else 0.0
            self._hover_anim[d.loc.id] = cur + (
                (tgt - cur)
                * self.ctx.motion.feedback_factor(dt, 14.0)
            )

    def _move_card_focus(self, direction: tuple[int, int]) -> None:
        """Move to the nearest card in a D-pad direction, wrapping at edges."""
        if not self._cards:
            self._focused_card_id = None
            return

        ids = [d.loc.id for d, _button in self._cards]
        dx, dy = direction
        if self._focused_card_id not in ids:
            index = 0 if (dx > 0 or dy > 0) else len(self._cards) - 1
            focused = self._cards[index][0]
        else:
            current_index = ids.index(self._focused_card_id)
            current = self._cards[current_index][0]
            cx, cy = current.crect.center
            candidates: list[tuple[tuple[int, int], int, _Dest]] = []
            for index, (candidate, _button) in enumerate(self._cards):
                if index == current_index:
                    continue
                tx, ty = candidate.crect.center
                delta_x, delta_y = tx - cx, ty - cy
                if dx and delta_x * dx > 0:
                    score = (abs(delta_x), abs(delta_y))
                elif dy and delta_y * dy > 0:
                    score = (abs(delta_y), abs(delta_x))
                else:
                    continue
                candidates.append((score, index, candidate))
            if candidates:
                _score, _index, focused = min(candidates,
                                               key=lambda item: item[:2])
            else:
                # A wrap fallback guarantees every card remains reachable even
                # for ragged last rows and region group boundaries.
                step = 1 if (dx > 0 or dy > 0) else -1
                focused = self._cards[
                    (current_index + step) % len(self._cards)
                ][0]

        self._focused_card_id = focused.loc.id
        self._ensure_card_visible(focused)

    def _sync_header_focus(self) -> None:
        enabled = [
            index
            for index, button in enumerate(self._buttons)
            if button.visible and button.enabled
        ]
        if self._header_focus not in enabled:
            self._header_focus = -1
        for index, button in enumerate(self._buttons):
            button.focused = index == self._header_focus

    def _clear_header_focus(self) -> None:
        self._header_focus = -1
        self._sync_header_focus()

    def _move_header_focus(self, step: int) -> None:
        enabled = [
            index
            for index, button in enumerate(self._buttons)
            if button.visible and button.enabled
        ]
        if not enabled:
            self._header_focus = -1
            return
        if self._header_focus not in enabled:
            self._header_focus = enabled[0] if step >= 0 else enabled[-1]
        else:
            position = enabled.index(self._header_focus)
            self._header_focus = enabled[
                (position + (1 if step >= 0 else -1)) % len(enabled)
            ]
        self._sync_header_focus()

    def _activate_header_focus(self) -> bool:
        if not (0 <= self._header_focus < len(self._buttons)):
            return False
        return self._buttons[self._header_focus].activate()

    def _ensure_card_visible(self, d: _Dest) -> None:
        """Scroll just enough to keep the keyboard-focused card on screen."""
        rect = self._card_screen_rect(d)
        if rect.top < self._viewport.top:
            self._scroll -= self._viewport.top - rect.top
        elif rect.bottom > self._viewport.bottom:
            self._scroll += rect.bottom - self._viewport.bottom
        self._clamp_scroll()

    def _move_preview_focus(self, direction: tuple[int, int]) -> None:
        enabled = [i for i, button in enumerate(self._preview_buttons)
                   if button.enabled]
        if not enabled:
            self._preview_focus = -1
            return
        dx, dy = direction
        step = 1 if (dx > 0 or dy > 0) else -1
        if self._preview_focus not in enabled:
            self._preview_focus = enabled[0] if step > 0 else enabled[-1]
            return
        position = enabled.index(self._preview_focus)
        self._preview_focus = enabled[(position + step) % len(enabled)]

    def _activate_preview_focus(self) -> bool:
        if not (0 <= self._preview_focus < len(self._preview_buttons)):
            return False
        button = self._preview_buttons[self._preview_focus]
        return button.activate()

    def _clamp_scroll(self) -> None:
        max_scroll = max(0, self._content_h - self._viewport.height)
        self._scroll = max(0, min(self._scroll, max_scroll))

    def _card_screen_rect(self, d: _Dest) -> pygame.Rect:
        return pygame.Rect(d.crect.x,
                           self._viewport.y + d.crect.y - self._scroll,
                           d.crect.width, d.crect.height)

    # ---- drawing --------------------------------------------------------

    def draw(self, surface: pygame.Surface) -> None:
        if self._detail_only and self._selected is not None:
            # A marker-selected confirmation is a modal over the live spatial
            # map. Do not paint the secondary card browser underneath it; the
            # player keeps visual context for the destination they just chose.
            self._draw_preview(surface)
            return

        th = self.ctx.theme
        chrome = self.ctx.ui_chrome
        backdrop_rect = surface.get_rect()
        backdrop_painted = (
            chrome is not None
            and chrome.paint(
                surface,
                backdrop_rect,
                ChromeRequest(
                    part="backdrop",
                    screen="travel",
                    variant="overlay",
                    size=backdrop_rect.size,
                    density=self._density,
                ),
            )
        )
        if not backdrop_painted:
            veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
            veil.fill((0, 0, 0, 165))
            surface.blit(veil, (0, 0))

        Panel(self._panel, th, fill=(*th.bg_overlay[:3], 240),
              border=th.border_strong, radius=self._sp(th.radius_l),
              border_width=self._sp(2),
              chrome=chrome, screen="travel", role="main",
              density=self._density).draw(surface)

        title = self.ctx.fonts.render(
            self.ctx.t("destination_title", "要去哪裡？"),
            self.ctx.config.font_size_header,
            th.accent,
            bold=True,
        )
        title_pos = (
            self._panel.x + self._panel_pad,
            self._panel.y + self._panel_top_pad,
        )
        surface.blit(title, title_pos)
        tlabel = self.ctx.fonts.render(self.ctx.state.time.label(),
                                       self.ctx.config.font_size_small,
                                       th.accent_warm)
        surface.blit(tlabel, (title_pos[0] + title.get_width() + self._sp(24),
                              title_pos[1]
                              + (title.get_height() - tlabel.get_height())))
        for b in self._buttons:
            b.draw(surface)

        if not self._dests:
            self._draw_empty(surface)
        else:
            self._draw_cards(surface)
            self._draw_scrollbar(surface)

        # Footer: "you are here".
        cur = self.ctx.state.map.current
        if cur is not None:
            here = self.ctx.fonts.render(
                self.ctx.t(
                    "destination_current_location",
                    "你在：{location}",
                    location=cur.name,
                ),
                16,
                th.text_mute,
            )
            surface.blit(here, (
                self._panel.x + self._panel_pad,
                self._panel.bottom - self._sp(34),
            ))

        if self._selected is not None:
            self._draw_preview(surface)

    def _draw_empty(self, surface: pygame.Surface) -> None:
        th = self.ctx.theme
        msg = (
            self.ctx.t(
                "destination_empty_filtered",
                "目前沒有可前往的地點",
            )
            if self._filter_reachable or self._filter_with_npc
            else self.ctx.t(
                "destination_empty_exits",
                "這裡沒有其他出口",
            )
        )
        s = self.ctx.fonts.render(msg, 20, th.text_mute)
        surface.blit(s, (self._viewport.centerx - s.get_width() // 2,
                         self._viewport.centery - s.get_height() // 2))

    def _draw_cards(self, surface: pygame.Surface) -> None:
        th = self.ctx.theme
        prev_clip = surface.get_clip()
        surface.set_clip(self._viewport)
        # Region header bands (clickable to collapse/expand). The disclosure
        # triangle is drawn as a polygon, not a glyph, so it never depends on
        # the CJK font carrying ▸/▾.
        for cy, key, label in self._region_headers:
            sy = self._viewport.y + cy - self._scroll
            if sy + self._region_header_h < self._viewport.y \
                    or sy > self._viewport.bottom:
                continue
            txt = self.ctx.fonts.render(label, 18, th.accent_warm, bold=True)
            self._draw_disclosure(surface, self._viewport.x,
                                  sy + self._sp(6) + txt.get_height() // 2,
                                  key in self._collapsed, txt.get_height())
            tx = self._viewport.x + self._sp(24)
            surface.blit(txt, (tx, sy + self._sp(6)))
            line_y = sy + self._region_header_h - self._sp(10)
            pygame.draw.line(surface, (*th.border_soft[:3], 70),
                             (tx + txt.get_width() + self._sp(16), line_y),
                             (self._viewport.right, line_y),
                             max(1, self._sp(1)))
        # Cards (visible, non-collapsed only).
        for d, _btn in self._cards:
            r = self._card_screen_rect(d)
            if r.bottom < self._viewport.y or r.top > self._viewport.bottom:
                continue
            self._draw_card(surface, d, r)
        surface.set_clip(prev_clip)

    def _draw_card(self, surface: pygame.Surface, d: _Dest,
                   r: pygame.Rect) -> None:
        th = self.ctx.theme
        anim = self._hover_anim.get(d.loc.id, 0.0)
        # Locked and future cards are still inspectable, so they retain the
        # same hover/focus affordance with a warmer colour.
        if anim > 0.01:
            glow_pad = self._sp(6)
            glow = pygame.Surface(
                (r.width + glow_pad * 2, r.height + glow_pad * 2),
                pygame.SRCALPHA,
            )
            glow_color = th.accent if d.available else th.accent_warm
            pygame.draw.rect(
                glow,
                (*glow_color[:3], int(80 * anim)),
                glow.get_rect(),
                border_radius=self._sp(th.radius_m + 3),
            )
            surface.blit(glow, (r.x - glow_pad, r.y - glow_pad))

        # Paint every item into a card-sized surface. Pygame then supplies a
        # hard clipping boundary even if pack-authored copy is unexpectedly
        # long or a font has unusual metrics.
        card = pygame.Surface(r.size, pygame.SRCALPHA)
        geometry = self._card_geometry(d, card.get_rect())
        self._card_debug_layouts[d.loc.id] = {
            key: pygame.Rect(value)
            for key, value in geometry.items()
        }
        radius = self._sp(th.radius_m)
        focused = (
            d.loc.id == self._focused_card_id
            and self._selected is None
        )
        selected = d.loc.id == self._selected
        card_painted = (
            self.ctx.ui_chrome is not None
            and self.ctx.ui_chrome.paint(
                card,
                card.get_rect(),
                ChromeRequest(
                    part="card",
                    screen="travel",
                    variant=(
                        "destination:new"
                        if d.has_new else "destination"
                    ),
                    size=card.get_size(),
                    density=self._density,
                    enabled=d.available,
                    hovered=anim > 0.01,
                    focused=focused,
                    selected=selected,
                    emphasis=max(0, min(3, int(round(anim * 3)))),
                ),
            )
        )
        if not card_painted:
            pygame.draw.rect(
                card,
                (*th.bg_panel[:3], 242),
                card.get_rect(),
                border_radius=radius,
            )

        thumbnail = geometry["thumbnail"]
        arrival_tod = (
            d.plan.arrival.time_of_day.value
            if d.plan is not None
            else self.ctx.state.time.time_of_day.value
        )
        thumb = self.ctx.assets.location_background(
            d.loc,
            arrival_tod,
            size=thumbnail.size,
        )
        if thumb is None:
            thumb = self.ctx.assets.scaled(None, thumbnail.size)
        card.blit(thumb, thumbnail.topleft)
        if not d.available:
            shade = pygame.Surface(thumbnail.size, pygame.SRCALPHA)
            shade.fill((0, 0, 0, 150))
            card.blit(shade, thumbnail.topleft)

        if d.has_new:
            status = d.offer.status if d.offer is not None else "current"
            status_label = {
                "current": self.ctx.t(
                    "destination_new_event",
                    "新事件",
                ),
                "future": self.ctx.t(
                    "destination_later_event",
                    "稍後事件",
                ),
                "locked": self.ctx.t(
                    "map_not_yet_available",
                    "尚未開放",
                ),
                "neutral": self.ctx.t(
                    "destination_location_info",
                    "地點情報",
                ),
            }.get(
                status,
                self.ctx.t("destination_new_event", "新事件"),
            )
            badge_label = self.ctx.t(
                "destination_badge",
                "● {label}",
                label=status_label,
            )
            badge_font = self.ctx.fonts.get(13, bold=True)
            badge_pad_x = self._sp(7)
            badge_pad_y = self._sp(3)
            max_badge_w = max(1, thumbnail.width - self._sp(16))
            badge_label = _ellipsized_text(
                badge_label,
                badge_font,
                max(1, max_badge_w - badge_pad_x * 2),
            )
            badge = badge_font.render(badge_label, True, (255, 255, 255))
            bw = min(max_badge_w, badge.get_width() + badge_pad_x * 2)
            bh = badge.get_height() + badge_pad_y * 2
            chip = pygame.Surface((bw, bh), pygame.SRCALPHA)
            pygame.draw.rect(
                chip,
                (*th.accent[:3], 230),
                chip.get_rect(),
                border_radius=self._sp(th.radius_s),
            )
            chip.blit(badge, (badge_pad_x, badge_pad_y))
            card.blit(
                chip,
                (
                    thumbnail.right - bw - self._sp(5),
                    thumbnail.y + self._sp(5),
                ),
            )

        story_event = (
            d.offer.event
            if d.offer is not None and d.offer.event is not None
            else None
        )
        title_text = story_event.title if story_event else d.loc.name
        region_name = self._region_name(d.loc.region)
        eyebrow_text = (
            d.loc.name
            if story_event
            else (
                region_name
                or self.ctx.t(
                    "map_ordinary_location",
                    "一般地點",
                )
            )
        )
        if story_event and region_name:
            eyebrow_text = f"{d.loc.name} · {region_name}"

        eyebrow_rect = geometry["eyebrow"]
        eyebrow_font = self.ctx.fonts.get(13, bold=True)
        eyebrow = eyebrow_font.render(
            _ellipsized_text(
                eyebrow_text,
                eyebrow_font,
                eyebrow_rect.width,
            ),
            True,
            th.text_mute,
        )
        card.blit(eyebrow, eyebrow_rect.topleft)

        title_rect = geometry["title"]
        title_font = self.ctx.fonts.get(18, bold=True)
        self._draw_text_lines(
            card,
            _bounded_lines(title_text, title_font, title_rect.width, 2),
            title_rect,
            title_font,
            th.text if d.available else th.text_mute,
            self._sp(1),
        )

        identity = geometry["identity"]
        portrait_rect = geometry["portrait"]
        identity_copy = geometry["identity_copy"]
        lead = self._lead_npc(d)
        if story_event:
            if lead is not None:
                portrait = self.ctx.assets.scaled(
                    lead.portrait,
                    portrait_rect.size,
                    fit="cover",
                )
                card.blit(portrait, portrait_rect.topleft)
                pygame.draw.rect(
                    card,
                    (*th.border[:3], 180),
                    portrait_rect,
                    width=self._sp(1),
                    border_radius=self._sp(5),
                )
                name_font = self.ctx.fonts.get(14, bold=True)
                name = _ellipsized_text(
                    lead.name,
                    name_font,
                    identity_copy.width,
                )
                card.blit(
                    name_font.render(
                        name,
                        True,
                        self._npc_name_color(lead, available=d.available),
                    ),
                    identity_copy.topleft,
                )
                copy_y = identity_copy.y + name_font.get_linesize()
            else:
                copy_y = identity_copy.y
            teaser_text = story_event.teaser or d.loc.description
            teaser_font = self.ctx.fonts.get(12)
            teaser_rect = pygame.Rect(
                identity_copy.x,
                copy_y + (self._sp(2) if lead is not None else 0),
                identity_copy.width,
                max(1, identity_copy.bottom - copy_y - self._sp(2)),
            )
            self._draw_text_lines(
                card,
                _bounded_lines(
                    teaser_text or "",
                    teaser_font,
                    teaser_rect.width,
                    2,
                ),
                teaser_rect,
                teaser_font,
                th.text_mute if d.available else th.text_dim,
                self._sp(1),
            )
        elif d.npc_ids:
            ax = identity.x
            drawn: list[str] = []
            for npc_id in d.npc_ids[:3]:
                npc = self.ctx.npcs.get(npc_id)
                if npc is None:
                    continue
                avatar_rect = pygame.Rect(
                    ax,
                    portrait_rect.y,
                    portrait_rect.width,
                    portrait_rect.height,
                )
                avatar = self.ctx.assets.scaled(
                    npc.portrait,
                    avatar_rect.size,
                    fit="cover",
                )
                card.blit(avatar, avatar_rect.topleft)
                pygame.draw.rect(
                    card,
                    (*th.border[:3], 160),
                    avatar_rect,
                    width=self._sp(1),
                    border_radius=self._sp(5),
                )
                ax += avatar_rect.width + self._sp(4)
                drawn.append(npc.name)
            label_rect = pygame.Rect(
                ax + self._sp(4),
                identity.y,
                max(1, identity.right - ax - self._sp(4)),
                identity.height,
            )
            label_font = self.ctx.fonts.get(13)
            self._draw_text_lines(
                card,
                _bounded_lines(
                    "、".join(drawn),
                    label_font,
                    label_rect.width,
                    2,
                ),
                label_rect,
                label_font,
                th.text_mute,
                self._sp(2),
                center_y=True,
            )
        else:
            desc_font = self.ctx.fonts.get(13)
            empty_copy = d.loc.description or self.ctx.t(
                "destination_no_characters_here",
                "目前沒有角色在這裡",
            )
            self._draw_text_lines(
                card,
                _bounded_lines(
                    empty_copy,
                    desc_font,
                    identity.width,
                    2,
                ),
                identity,
                desc_font,
                th.text_dim,
                self._sp(2),
                center_y=True,
            )

        footer = geometry["footer"]
        pygame.draw.line(
            card,
            (*th.border_soft[:3], 90),
            footer.topleft,
            (footer.right, footer.top),
            self._sp(1),
        )
        status_text, status_color = self._card_status(d)
        inspect_text = (
            self.ctx.t(
                "destination_inspect_conditions",
                "查看開放條件",
            )
            if not d.available
            else self.ctx.t(
                "destination_inspect_details",
                "查看詳情",
            )
        )
        footer_font = self.ctx.fonts.get(13, bold=True)
        inspect = footer_font.render(inspect_text, True, th.text_mute)
        inspect_x = footer.right - inspect.get_width()
        status_w = max(1, inspect_x - footer.x - self._sp(12))
        status = footer_font.render(
            _ellipsized_text(status_text, footer_font, status_w),
            True,
            status_color,
        )
        text_y = footer.y + (
            footer.height - max(status.get_height(), inspect.get_height())
        ) // 2
        card.blit(status, (footer.x, text_y))
        card.blit(inspect, (inspect_x, text_y))

        if d.available:
            rest = th.accent if (d.has_new or d.npc_ids) else th.border
            border_color = _lerp(rest, th.border_strong, anim)
        else:
            border_color = _lerp(th.border_soft, th.accent_warm, anim)
        pygame.draw.rect(
            card,
            border_color,
            card.get_rect(),
            width=self._sp(1) + int(round(anim)),
            border_radius=radius,
        )
        surface.blit(card, r.topleft)
        if d.loc.id == self._focused_card_id and self._selected is None:
            self._draw_focus_ring(surface, r)

    def _region_name(self, region_id: str | None) -> str:
        if not region_id:
            return ""
        region = self.ctx.state.map.regions.get(region_id)
        return region.name if region is not None and region.name else region_id

    def _lead_npc(self, d: _Dest):
        """Prefer a heroine for story-card identity, then the first valid NPC."""
        valid = [
            npc
            for npc_id in d.npc_ids
            if (npc := self.ctx.npcs.get(npc_id)) is not None
        ]
        return next((npc for npc in valid if npc.is_heroine), None) or (
            valid[0] if valid else None
        )

    def _npc_name_color(self, npc, *, available: bool) -> tuple:
        if not available:
            return self.ctx.theme.text_mute
        raw = getattr(npc, "name_color", None)
        if raw:
            try:
                from ..dialogue.richtext import _parse_color
                parsed = _parse_color(raw)
                if parsed is not None:
                    return tuple(parsed[:3])
            except (TypeError, ValueError):
                pass
        return self.ctx.theme.accent

    def _card_status(self, d: _Dest) -> tuple[str, tuple]:
        """Return short card copy; the full reason belongs in the detail sheet."""
        th = self.ctx.theme
        offer = d.offer
        if offer is not None and offer.status == "future":
            phases = offer.future.wait_phases if offer.future is not None else 1
            return (
                self.ctx.t(
                    "map_depart_after_time_slots",
                    "等待 {count} 時段後可出發",
                    count=phases,
                ),
                th.accent_warm,
            )
        if not d.available:
            return (
                self.ctx.t(
                    "map_not_yet_available",
                    "尚未開放",
                ),
                th.warn,
            )
        if d.cost == 0:
            return (
                self.ctx.t(
                    "destination_arrive_immediately",
                    "即時抵達",
                ),
                th.good,
            )
        return (
            self.ctx.t(
                "destination_trip_time_slots",
                "行程 {count} 時段",
                count=d.cost,
            ),
            th.accent_warm,
        )

    def _card_geometry(
        self,
        d: _Dest,
        bounds: pygame.Rect,
    ) -> dict[str, pygame.Rect]:
        """Return local, fully-contained rectangles shared by all card types."""
        del d  # Variants deliberately share one visual-density contract.
        pad = self._sp(12)
        edge = self._sp(3)
        thumbnail = pygame.Rect(
            edge,
            edge,
            max(1, bounds.width - edge * 2),
            min(self._thumb_h, max(1, bounds.height - edge * 2)),
        )
        footer_font = self.ctx.fonts.get(13, bold=True)
        footer_h = max(
            self._sp(30),
            footer_font.get_linesize() + self._sp(8),
        )
        footer = pygame.Rect(
            pad,
            max(thumbnail.bottom, bounds.bottom - pad - footer_h),
            max(1, bounds.width - pad * 2),
            footer_h,
        )
        eyebrow_font = self.ctx.fonts.get(13, bold=True)
        eyebrow = pygame.Rect(
            pad,
            thumbnail.bottom + self._sp(7),
            max(1, bounds.width - pad * 2),
            eyebrow_font.get_linesize(),
        )
        title_font = self.ctx.fonts.get(18, bold=True)
        title_line_h = title_font.get_linesize() + self._sp(1)
        title = pygame.Rect(
            pad,
            eyebrow.bottom + self._sp(2),
            max(1, bounds.width - pad * 2),
            title_line_h * 2,
        )
        identity_top = title.bottom + self._sp(6)
        identity = pygame.Rect(
            pad,
            identity_top,
            max(1, bounds.width - pad * 2),
            max(1, footer.top - self._sp(8) - identity_top),
        )
        portrait_size = min(
            self._avatar,
            max(1, identity.height),
            max(1, identity.width),
        )
        portrait = pygame.Rect(
            identity.x,
            identity.y + max(0, (identity.height - portrait_size) // 2),
            portrait_size,
            portrait_size,
        )
        identity_copy = pygame.Rect(
            portrait.right + self._sp(10),
            identity.y,
            max(1, identity.right - portrait.right - self._sp(10)),
            identity.height,
        )
        return {
            "card": pygame.Rect(bounds),
            "thumbnail": thumbnail,
            "eyebrow": eyebrow,
            "title": title,
            "identity": identity,
            "portrait": portrait,
            "identity_copy": identity_copy,
            "footer": footer,
        }

    @staticmethod
    def _draw_text_lines(
        surface: pygame.Surface,
        lines: list[str] | tuple[str, ...],
        rect: pygame.Rect,
        font: pygame.font.Font,
        color: tuple,
        gap: int,
        *,
        center_y: bool = False,
        scroll_y: int = 0,
    ) -> None:
        line_h = font.get_linesize() + gap
        total_h = len(lines) * line_h - (gap if lines else 0)
        y = rect.y + max(0, (rect.height - total_h) // 2) \
            if center_y else rect.y
        y -= max(0, int(scroll_y))
        previous_clip = surface.get_clip()
        surface.set_clip(rect)
        for line in lines:
            surface.blit(font.render(line, True, color), (rect.x, y))
            y += line_h
        surface.set_clip(previous_clip)

    def _draw_focus_ring(self, surface: pygame.Surface,
                         rect: pygame.Rect) -> None:
        """Persistent, high-contrast focus treatment independent of hover."""
        th = self.ctx.theme
        ring = rect.inflate(-self._sp(2), -self._sp(2))
        pygame.draw.rect(
            surface,
            th.accent_warm,
            ring,
            width=self._sp(4),
            border_radius=self._sp(th.radius_m),
        )
        # Corner brackets make focus identifiable by shape as well as colour.
        inset = self._sp(8)
        length = self._sp(12)
        x, y = ring.x + inset, ring.y + inset
        pygame.draw.lines(surface, th.text, False,
                          [(x, y + length), (x, y), (x + length, y)],
                          width=self._sp(2))
        x, y = ring.right - inset, ring.bottom - inset
        pygame.draw.lines(surface, th.text, False,
                          [(x - length, y), (x, y), (x, y - length)],
                          width=self._sp(2))

    def _draw_disclosure(self, surface: pygame.Surface, x: int, cy: int,
                         collapsed: bool, size: int) -> None:
        """A collapse/expand triangle drawn as a polygon (font-independent)."""
        s = max(self._sp(8), int(size * 0.5))
        col = self.ctx.theme.accent_warm
        if collapsed:   # right-pointing
            pts = [(x, cy - s // 2), (x, cy + s // 2), (x + s, cy)]
        else:           # down-pointing
            pts = [(x, cy - s // 2), (x + s, cy - s // 2), (x + s // 2, cy + s // 2)]
        pygame.draw.polygon(surface, col, pts)

    def _draw_scrollbar(self, surface: pygame.Surface) -> None:
        if self._content_h <= self._viewport.height:
            return
        th = self.ctx.theme
        track_h = self._viewport.height
        knob_h = max(
            self._sp(30),
            int(track_h * (self._viewport.height / self._content_h)),
        )
        max_scroll = self._content_h - self._viewport.height
        knob_y = int(self._scroll / max_scroll * (track_h - knob_h)) \
            if max_scroll > 0 else 0
        x = self._viewport.right + self._sp(8)
        bar_w = self._sp(4)
        pygame.draw.rect(surface, (*th.border_soft[:3], 70),
                         (x, self._viewport.y, bar_w, track_h),
                         border_radius=max(1, bar_w // 2))
        pygame.draw.rect(surface, (*th.accent[:3], 190),
                         (x, self._viewport.y + knob_y, bar_w, knob_h),
                         border_radius=max(1, bar_w // 2))

    def _draw_preview(self, surface: pygame.Surface) -> None:
        d = self._selected_dest()
        if d is None:
            return
        th = self.ctx.theme
        st = self.ctx.state
        sw, sh = self.ctx.screen_size
        backdrop_rect = pygame.Rect(0, 0, sw, sh)
        backdrop_painted = (
            self.ctx.ui_chrome is not None
            and self.ctx.ui_chrome.paint(
                surface,
                backdrop_rect,
                ChromeRequest(
                    part="backdrop",
                    screen="travel",
                    variant="modal",
                    size=backdrop_rect.size,
                    density=self._density,
                    emphasis=1,
                ),
            )
        )
        if not backdrop_painted:
            dim = pygame.Surface((sw, sh), pygame.SRCALPHA)
            dim.fill((0, 0, 0, 150))
            surface.blit(dim, (0, 0))

        if not self._preview_layout:
            action_label, _enabled = self._preview_action_state(d)
            self._preview_layout = self._compute_preview_layout(d, action_label)
        layout = self._preview_layout
        panel = pygame.Rect(layout["panel"])
        Panel(panel, th, fill=(*th.bg_panel[:3], 250),
              border=th.border_strong, radius=self._sp(th.radius_l),
              border_width=self._sp(2),
              chrome=self.ctx.ui_chrome,
              screen="travel",
              role="confirmation",
              density=self._density).draw(surface)

        previous_clip = surface.get_clip()
        surface.set_clip(panel)
        image_rect = pygame.Rect(layout["image"])
        arrival_tod = (
            d.plan.arrival.time_of_day.value
            if d.plan is not None
            else st.time.time_of_day.value
        )
        thumb = self.ctx.assets.location_background(
            d.loc,
            arrival_tod,
            size=image_rect.size,
        )
        if thumb is None:
            thumb = self.ctx.assets.scaled(None, image_rect.size)
        surface.blit(thumb, image_rect.topleft)
        if not d.available:
            shade = pygame.Surface(image_rect.size, pygame.SRCALPHA)
            shade.fill((0, 0, 0, 140))
            surface.blit(shade, image_rect.topleft)

        story_event = (
            d.offer.event
            if d.offer is not None and d.offer.event is not None
            else None
        )
        title_rect = pygame.Rect(layout["title"])
        title_font = self.ctx.fonts.get(28, bold=True)
        secondary_font = self.ctx.fonts.get(14, bold=True)
        primary_text = story_event.title if story_event else d.loc.name
        region_name = self._region_name(d.loc.region)
        secondary_text = (
            " · ".join(part for part in (d.loc.name, region_name) if part)
            if story_event
            else region_name
        )
        secondary = secondary_font.render(secondary_text, True, th.text_mute)
        secondary_w = min(secondary.get_width(), title_rect.width // 3)
        primary_w = max(
            1,
            title_rect.width
            - (secondary_w + self._sp(16) if secondary_text else 0),
        )
        primary_text = _ellipsized_text(primary_text, title_font, primary_w)
        primary = title_font.render(
            primary_text,
            True,
            th.accent if d.available else th.text_mute,
        )
        title_y = title_rect.y + max(
            0,
            (title_rect.height - primary.get_height()) // 2,
        )
        surface.blit(primary, (title_rect.x, title_y))
        if secondary_text:
            secondary_text = _ellipsized_text(
                secondary_text,
                secondary_font,
                secondary_w,
            )
            secondary = secondary_font.render(
                secondary_text,
                True,
                th.text_mute,
            )
            surface.blit(
                secondary,
                (
                    title_rect.right - secondary.get_width(),
                    title_rect.y
                    + max(0, (title_rect.height - secondary.get_height()) // 2),
                ),
            )

        teaser_rect = pygame.Rect(layout["teaser"])
        self._draw_text_lines(
            surface,
            layout["teaser_lines"],
            teaser_rect,
            self.ctx.fonts.get(14),
            th.text_mute,
            self._sp(2),
        )

        # Projected NPCs and events share two equal columns, keeping the sheet
        # compact even at the required 3-NPC / 3-event maximum.
        details_rect = pygame.Rect(layout["details"])
        surface.set_clip(details_rect)
        left = pygame.Rect(layout["left_column"])
        right = pygame.Rect(layout["right_column"])
        head_font = self.ctx.fonts.get(15, bold=True)
        body_font = self.ctx.fonts.get(14)
        left_head = head_font.render(
            self.ctx.t(
                "destination_arrival_characters_heading",
                "預計抵達時在這裡",
            ),
            True,
            th.text_dim,
        )
        surface.blit(left_head, left.topleft)
        row_y = left.y + head_font.get_linesize() + self._sp(6)
        npc_row_h = int(layout["npc_row_h"])
        valid_npcs = [
            npc
            for npc_id in d.npc_ids[:3]
            if (npc := self.ctx.npcs.get(npc_id)) is not None
        ]
        if valid_npcs:
            for npc in valid_npcs:
                avatar_size = min(
                    int(layout["npc_avatar_size"]),
                    max(1, npc_row_h - self._sp(4)),
                )
                avatar_rect = pygame.Rect(
                    left.x,
                    row_y + (npc_row_h - avatar_size) // 2,
                    avatar_size,
                    avatar_size,
                )
                avatar = self.ctx.assets.scaled(
                    npc.portrait,
                    avatar_rect.size,
                    fit="cover",
                )
                surface.blit(avatar, avatar_rect.topleft)
                pygame.draw.rect(
                    surface,
                    (*th.border[:3], 150),
                    avatar_rect,
                    width=self._sp(1),
                    border_radius=self._sp(4),
                )
                label = npc.name
                if npc.is_heroine and story_event is None:
                    lvl = st.affection.level_label(npc.id)
                    val = st.affection.get(npc.id)
                    label = self.ctx.t(
                        "destination_character_affection",
                        "{name}  好感 {level}·{value}",
                        name=npc.name,
                        level=lvl,
                        value=val,
                    )
                elif npc.role:
                    label = f"{npc.name}  {npc.role}"
                label_x = avatar_rect.right + self._sp(8)
                label_w = max(1, left.right - label_x)
                label = _ellipsized_text(label, body_font, label_w)
                text = body_font.render(
                    label,
                    True,
                    self._npc_name_color(npc, available=d.available),
                )
                surface.blit(
                    text,
                    (
                        label_x,
                        row_y + max(0, (npc_row_h - text.get_height()) // 2),
                    ),
                )
                row_y += npc_row_h
        else:
            empty = body_font.render(
                self.ctx.t("no_one_here", "這裡沒有人。"),
                True,
                th.text_dim,
            )
            surface.blit(empty, (left.x, row_y))

        right_heading = (
            self.ctx.t(
                "destination_arrival_starts",
                "抵達後將開始",
            )
            if d.arrival_hook_ids
            else self.ctx.t(
                "destination_arrival_available",
                "抵達後可以進行",
            )
        )
        right_head = head_font.render(right_heading, True, th.text_dim)
        surface.blit(right_head, right.topleft)
        hook_y = right.y + head_font.get_linesize() + self._sp(6)
        hook_row_h = int(layout["hook_row_h"])
        display_hook_ids = tuple(layout["display_hook_ids"])
        if display_hook_ids:
            for hook_id in display_hook_ids:
                scene = st.story.get(hook_id)
                label = (scene.title or scene.id) if scene else hook_id
                bullet_size = self._sp(6)
                pygame.draw.circle(
                    surface,
                    th.accent,
                    (
                        right.x + bullet_size // 2,
                        hook_y + hook_row_h // 2,
                    ),
                    max(1, bullet_size // 2),
                )
                label_x = right.x + bullet_size + self._sp(8)
                label = _ellipsized_text(
                    label,
                    body_font,
                    max(1, right.right - label_x),
                )
                text = body_font.render(label, True, th.text)
                surface.blit(
                    text,
                    (
                        label_x,
                        hook_y + max(0, (hook_row_h - text.get_height()) // 2),
                    ),
                )
                hook_y += hook_row_h
        else:
            empty = body_font.render(
                self.ctx.t(
                    "destination_no_triggerable_events",
                    "目前沒有可觸發的事件",
                ),
                True,
                th.text_dim,
            )
            surface.blit(empty, (right.x, hook_y))

        surface.set_clip(panel)
        status_card = pygame.Rect(layout["status_card"])
        card_surface = pygame.Surface(status_card.size, pygame.SRCALPHA)
        pygame.draw.rect(
            card_surface,
            (*th.bg_overlay[:3], 205),
            card_surface.get_rect(),
            border_radius=self._sp(th.radius_m),
        )
        pygame.draw.rect(
            card_surface,
            (*th.border_soft[:3], 155),
            card_surface.get_rect(),
            width=max(1, self._sp(1)),
            border_radius=self._sp(th.radius_m),
        )
        accent_width = self._sp(4)
        pygame.draw.rect(
            card_surface,
            th.accent_warm,
            pygame.Rect(0, 0, accent_width, status_card.height),
            border_top_left_radius=self._sp(th.radius_m),
            border_bottom_left_radius=self._sp(th.radius_m),
        )
        surface.blit(card_surface, status_card.topleft)
        status_rect = pygame.Rect(layout["status"])
        itinerary_font = self.ctx.fonts.get(14)
        itinerary_bold = self.ctx.fonts.get(14, bold=True)
        itinerary_line_h = itinerary_font.get_linesize() + self._sp(2)
        itinerary_y = status_rect.y
        for index, raw_line in enumerate(layout["itinerary_lines"]):
            line = str(raw_line)
            label, separator, value = line.partition("：")
            if not separator:
                label, separator, value = line.partition(":")
            label_text = f"{label}{separator}"
            label_surface = itinerary_bold.render(
                label_text,
                True,
                th.text_dim,
            )
            surface.blit(label_surface, (status_rect.x, itinerary_y))
            value_x = status_rect.x + label_surface.get_width()
            value_w = max(1, status_rect.right - value_x)
            value = _ellipsized_text(
                value,
                itinerary_font,
                value_w,
            )
            value_surface = itinerary_font.render(
                value,
                True,
                (
                    th.accent_warm
                    if index == len(layout["itinerary_lines"]) - 1
                    else th.text
                ),
            )
            surface.blit(value_surface, (value_x, itinerary_y))
            itinerary_y += itinerary_line_h
        reason_rect = pygame.Rect(layout["reason_view"])
        self._draw_text_lines(
            surface,
            layout["reason_lines"],
            reason_rect,
            self.ctx.fonts.get(14),
            th.warn,
            self._sp(2),
            scroll_y=self._preview_reason_scroll,
        )
        reason_scroll_max = int(layout["reason_scroll_max"])
        if reason_scroll_max > 0 and reason_rect.height > 0:
            bar_w = self._sp(4)
            track = pygame.Rect(
                reason_rect.right - bar_w,
                reason_rect.y,
                bar_w,
                reason_rect.height,
            )
            content_h = max(1, int(layout["reason_content_h"]))
            knob_h = max(
                self._sp(18),
                int(track.height * (reason_rect.height / content_h)),
            )
            knob_h = min(track.height, knob_h)
            knob_y = int(
                self._preview_reason_scroll
                / reason_scroll_max
                * max(0, track.height - knob_h)
            )
            pygame.draw.rect(
                surface,
                (*th.border_soft[:3], 100),
                track,
                border_radius=max(1, bar_w // 2),
            )
            pygame.draw.rect(
                surface,
                (*th.warn[:3], 210),
                (track.x, track.y + knob_y, bar_w, knob_h),
                border_radius=max(1, bar_w // 2),
            )

        footer = pygame.Rect(layout["footer"])
        pygame.draw.line(
            surface,
            (*th.border_soft[:3], 110),
            (footer.x + self._sp(24), footer.y),
            (footer.right - self._sp(24), footer.y),
            self._sp(1),
        )
        for i, button in enumerate(self._preview_buttons):
            button.draw(surface)
            if i == self._preview_focus and button.enabled:
                self._draw_focus_ring(surface, button.rect)
        surface.set_clip(previous_clip)

    def _preview_itinerary_lines(self, d: _Dest) -> tuple[str, ...]:
        state = self.ctx.state
        current = state.map.current
        current_name = (
            current.name
            if current is not None
            else self.ctx.t("destination_unset", "未設定")
        )
        departure = (
            d.plan.departure if d.plan is not None else None
        )
        arrival = d.plan.arrival if d.plan is not None else None
        departure_text = self._preview_time_label(departure)
        arrival_text = self._preview_time_label(arrival)
        path_ids = (
            d.plan.location_ids
            if d.plan is not None
            else tuple(
                location_id
                for location_id in (
                    state.map.current_location_id,
                    d.loc.id,
                )
                if location_id is not None
            )
        )
        path_names = [
            (
                state.map.locations[location_id].name
                if location_id in state.map.locations
                else location_id
            )
            for location_id in path_ids
        ]
        return (
            self.ctx.t(
                "destination_itinerary_now",
                "現在：{location} · {time}",
                location=current_name,
                time=departure_text,
            ),
            self.ctx.t(
                "destination_itinerary_arrival",
                "預計抵達：{location} · {time}",
                location=d.loc.name,
                time=arrival_text,
            ),
            self.ctx.t(
                "destination_itinerary_route",
                "路線：{route}",
                route=" → ".join(path_names),
            ),
            self.ctx.t(
                "destination_itinerary_cost",
                "消耗：{cost}",
                cost=self._preview_cost_line(d),
            ),
        )

    def _preview_time_label(self, point) -> str:
        if point is None:
            return self.ctx.state.time.label()
        return self.ctx.t(
            "day_format",
            "第 {day} 天 · {weekday} · {time_of_day}",
            day=point.day,
            weekday=self.ctx.localization.weekday_label(
                point.day_of_week.value,
            ),
            time_of_day=self.ctx.localization.time_label(
                point.time_of_day.value,
            ),
        )

    def _preview_cost_line(self, d: _Dest) -> str:
        if d.offer is not None and d.offer.future is not None:
            return self.ctx.t(
                "destination_cost_future",
                "等待 {wait} 個時段後可出發；行程消耗 {cost} 時段",
                wait=d.offer.future.wait_phases,
                cost=d.cost,
            )
        if d.cost == 0:
            return self.ctx.t(
                "destination_cost_instant",
                "即時，不消耗時段",
            )
        return self.ctx.t(
            "destination_cost_slots",
            "{count} 個時段",
            count=d.cost,
        )

    # ---- introspection --------------------------------------------------

    def describe(self) -> dict:
        return {
            "scene": "DestinationPickerScene",
            "current": (self.ctx.state.map.current.id
                        if self.ctx.state.map.current else None),
            "destinations": [
                {"id": d.loc.id, "name": d.loc.name,
                 "available": d.available, "npcs": list(d.npc_ids),
                 "has_new": d.has_new, "cost": d.cost}
                for d in self._dests
            ],
            # Extended projected-arrival facts live beside the historical
            # destination rows so existing driver/introspection consumers that
            # compare those compact rows exactly remain compatible.
            "travel_previews": {
                d.loc.id: {
                    "path": (
                        list(d.plan.location_ids)
                        if d.plan is not None else None
                    ),
                    "available_hooks": list(d.available_hook_ids),
                    "arrival_hooks": list(d.arrival_hook_ids),
                    "arrival": (
                        {
                            "day": d.plan.arrival.day,
                            "weekday": d.plan.arrival.day_of_week.value,
                            "time_of_day": d.plan.arrival.time_of_day.value,
                        }
                        if d.plan is not None else None
                    ),
                }
                for d in self._dests
            },
            "guided_offers": {
                d.loc.id: d.offer.as_dict()
                for d in self._dests
                if d.offer is not None
            },
            "selected": self._selected,
            "detail_only": self._detail_only,
            "forced_initial_destination": self._forced_initial_destination,
            "focus": {
                "card": self._focused_card_id,
                "header": (
                    self._buttons[self._header_focus].label
                    if 0 <= self._header_focus < len(self._buttons)
                    else None
                ),
                "preview": (
                    self._preview_buttons[self._preview_focus].label
                    if 0 <= self._preview_focus < len(self._preview_buttons)
                    else None
                ),
            },
            "filters": {
                "reachable": self._filter_reachable,
                "with_npc": self._filter_with_npc,
                "guided_show_all": self._guided_show_all,
            },
            "collapsed_regions": sorted(
                "" if k is None else k for k in self._collapsed),
        }
