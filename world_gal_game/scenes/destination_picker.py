"""Destination picker overlay — the card-based "where do I go?" surface.

Replaces the flat exit-button grid (in ExplorationScene) and the
node-graph map as the *primary* travel UI. Every reachable destination is
a card showing:

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
hover is eased for a smooth feel. The node-graph map remains reachable as
a secondary "世界地圖" overview.

This scene reads the data model only (``map.all_exits_with_status``,
``map.npcs_present_at``, ``map.scenes_available_at``); it carries no
game-specific logic and adds no required pack fields. Destinations with
no background art fall back to the asset placeholder.
"""
from __future__ import annotations

from typing import Callable

import pygame

from .base import Scene, SceneContext
from ..ui.widgets import Button, Panel, WrappedText
from ..core.map_system import Location, Exit


# Card geometry (logical px on the 1920x1080 canvas; scaled with the UI).
_CARD_W_TARGET = 300
_CARD_GAP = 18
_THUMB_H = 116
_CARD_H = 208
_REGION_HEADER_H = 40
_AVATAR = 26

# Comfortable touch heights for the picker chrome.
_CHROME_BTN_H = 44
_FILTER_H = 40

# Persistence keys (kept in state.meta; JSON-safe primitives only).
_META_REACH = "__travel_filter_reachable__"
_META_NPC = "__travel_filter_with_npc__"
_META_COLLAPSED = "__travel_collapsed_regions__"


def _lerp(a: tuple, b: tuple, t: float) -> tuple:
    """Linear-interpolate two RGB(A) colors; missing alpha defaults to 255."""
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    aa = a[3] if len(a) > 3 else 255
    ba = b[3] if len(b) > 3 else 255
    return (int(a[0] + (b[0] - a[0]) * t),
            int(a[1] + (b[1] - a[1]) * t),
            int(a[2] + (b[2] - a[2]) * t),
            int(aa + (ba - aa) * t))


class _Dest:
    """One destination row, precomputed for layout + drawing."""

    __slots__ = ("loc", "exit", "available", "reason", "npc_ids",
                 "has_new", "cost", "crect")

    def __init__(self, loc: Location, exit_obj: Exit, available: bool,
                 reason: str | None, npc_ids: list[str], has_new: bool,
                 cost: int):
        self.loc = loc
        self.exit = exit_obj
        self.available = available
        self.reason = reason
        self.npc_ids = npc_ids
        self.has_new = has_new
        self.cost = cost
        self.crect = pygame.Rect(0, 0, 0, 0)   # content-space rect (pre-scroll)


class DestinationPickerScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        self.on_close: Callable[[], None] | None = None
        self.on_move_to: Callable[[str], None] | None = None
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

        self._buttons: list[Button] = []             # header chrome
        self._preview_buttons: list[Button] = []
        self._hover_anim: dict[str, float] = {}       # loc_id -> eased [0,1]

        self._filter_reachable = False
        self._filter_with_npc = False
        self._collapsed: set[str | None] = set()

        # Geometry, filled by _recompute_geometry().
        self._panel = pygame.Rect(0, 0, 0, 0)
        self._viewport = pygame.Rect(0, 0, 0, 0)
        self._cols = 1
        self._card_w = _CARD_W_TARGET
        self._title_h = 0
        self._filter_y = 0

    # ---- lifecycle ------------------------------------------------------

    def enter(self, *, on_close=None, on_move_to=None, on_world_map=None,
              **_) -> None:
        self.on_close = on_close
        self.on_move_to = on_move_to
        self.on_world_map = on_world_map
        self._selected = None
        self._scroll = 0
        self._load_prefs()
        self._rebuild()

    def resume(self) -> None:
        self._rebuild()

    # ---- persistence ----------------------------------------------------

    def _load_prefs(self) -> None:
        m = self.ctx.state.meta
        self._filter_reachable = bool(m.get(_META_REACH, False))
        self._filter_with_npc = bool(m.get(_META_NPC, False))
        collapsed = m.get(_META_COLLAPSED) or []
        # "" is the JSON-safe sentinel for the region-less ("其他") group.
        self._collapsed = {None if k == "" else k for k in collapsed}

    def _save_prefs(self) -> None:
        m = self.ctx.state.meta
        m[_META_REACH] = self._filter_reachable
        m[_META_NPC] = self._filter_with_npc
        m[_META_COLLAPSED] = sorted(
            "" if k is None else k for k in self._collapsed)

    # ---- data + layout --------------------------------------------------

    def _recompute_geometry(self) -> None:
        sw, sh = self.ctx.screen_size
        self._panel = pygame.Rect(32, 32, sw - 64, sh - 64)
        pad = 28
        # Header height is scale-aware: the title font grows with resolution,
        # so measure it and place the filter row (and the card viewport)
        # below the actual title rather than at a fixed offset.
        self._title_h = self.ctx.fonts.render(
            "要去哪裡？", self.ctx.config.font_size_header,
            self.ctx.theme.accent, bold=True).get_height()
        self._filter_y = self._panel.y + 18 + self._title_h + 12
        header_bottom = self._filter_y + _FILTER_H + 14
        footer_h = 46
        self._viewport = pygame.Rect(
            self._panel.x + pad,
            header_bottom,
            self._panel.width - pad * 2,
            self._panel.bottom - footer_h - header_bottom,
        )
        # Column count adapts to viewport width (responsive on narrow /
        # portrait screens, which fall to fewer columns).
        vw = self._viewport.width
        self._cols = max(1, (vw + _CARD_GAP) // (_CARD_W_TARGET + _CARD_GAP))
        self._card_w = (vw - _CARD_GAP * (self._cols - 1)) // self._cols

    def _gather(self) -> list[_Dest]:
        st = self.ctx.state
        flags = st.events.flags
        tod = st.time.time_of_day.value
        weekday = st.time.day_of_week.value
        infos = st.map.all_exits_with_status(flags, tod)
        out: list[_Dest] = []
        for exit_obj, loc, available, reason in infos:
            npc_ids = st.map.npcs_present_at(loc, tod, weekday, flags)
            new_hooks = st.map.scenes_available_at(
                loc, time_of_day=tod, flags=flags,
                played_scenes=st.story.played, state=st)
            out.append(_Dest(loc, exit_obj, available, reason, npc_ids,
                             bool(new_hooks), exit_obj.travel_cost))
        return out

    def _rebuild(self) -> None:
        self._recompute_geometry()
        dests = self._gather()
        if self._filter_reachable:
            dests = [d for d in dests if d.available]
        if self._filter_with_npc:
            dests = [d for d in dests if d.npc_ids]
        self._layout(dests)
        self._build_header_buttons()
        self._clamp_scroll()   # filters / collapse can shrink content
        # Drop hover state for cards that no longer exist.
        self._hover_anim = {d.loc.id: self._hover_anim.get(d.loc.id, 0.0)
                            for d in dests}

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
                    rname = "其他"
                label = rname
                if collapsed:
                    label += f"（{len(members)}）"
                self._region_headers.append((y, key, label))
                y += _REGION_HEADER_H
            if collapsed:
                continue
            for i, d in enumerate(members):
                col = i % self._cols
                row = i // self._cols
                x = self._viewport.x + col * (self._card_w + _CARD_GAP)
                cy = y + row * (_CARD_H + _CARD_GAP)
                d.crect = pygame.Rect(x, cy, self._card_w, _CARD_H)
                btn = Button(pygame.Rect(d.crect), d.loc.name,
                             fonts=self.ctx.fonts, theme=self.ctx.theme,
                             font_size=15,
                             on_click=(lambda lid=d.loc.id:
                                       self._open_preview(lid)))
                self._cards.append((d, btn))
            rows = (len(members) + self._cols - 1) // self._cols
            y += rows * (_CARD_H + _CARD_GAP) + _CARD_GAP
        self._content_h = max(0, y)
        self._dests = dests

    def _build_header_buttons(self) -> None:
        p = self._panel
        self._buttons = []
        close = Button(
            pygame.Rect(p.right - 120 - 16, p.y + 14, 120, _CHROME_BTN_H),
            "關閉 (Esc)", fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=15, style="ghost",
            on_click=(lambda: self.on_close() if self.on_close else None))
        self._buttons.append(close)
        if self.on_world_map:
            wm = Button(
                pygame.Rect(p.right - 120 - 16 - 144, p.y + 14, 132,
                            _CHROME_BTN_H),
                "世界地圖", fonts=self.ctx.fonts, theme=self.ctx.theme,
                font_size=15, style="ghost", on_click=self.on_world_map)
            self._buttons.append(wm)
        # Filter chips, left-aligned below the title (scale-aware row).
        fx = p.x + 28
        fy = self._filter_y
        reach = Button(
            pygame.Rect(fx, fy, 158, _FILTER_H), "只看現在可去",
            fonts=self.ctx.fonts, theme=self.ctx.theme, font_size=14,
            style=("primary" if self._filter_reachable else "ghost"),
            on_click=self._toggle_reachable)
        self._buttons.append(reach)
        withnpc = Button(
            pygame.Rect(fx + 170, fy, 140, _FILTER_H), "只看有角色",
            fonts=self.ctx.fonts, theme=self.ctx.theme, font_size=14,
            style=("primary" if self._filter_with_npc else "ghost"),
            on_click=self._toggle_with_npc)
        self._buttons.append(withnpc)

    def _toggle_reachable(self) -> None:
        self._filter_reachable = not self._filter_reachable
        self._save_prefs()
        self._rebuild()

    def _toggle_with_npc(self) -> None:
        self._filter_with_npc = not self._filter_with_npc
        self._save_prefs()
        self._rebuild()

    def _toggle_region(self, key: str | None) -> None:
        if key in self._collapsed:
            self._collapsed.discard(key)
        else:
            self._collapsed.add(key)
        self._save_prefs()
        self._rebuild()

    # ---- preview --------------------------------------------------------

    def _open_preview(self, loc_id: str) -> None:
        self._selected = loc_id
        self._build_preview_buttons()

    def _close_preview(self) -> None:
        self._selected = None
        self._preview_buttons = []

    def _selected_dest(self) -> _Dest | None:
        if self._selected is None:
            return None
        return next((d for d in self._dests if d.loc.id == self._selected), None)

    def _build_preview_buttons(self) -> None:
        d = self._selected_dest()
        if d is None:
            self._preview_buttons = []
            return
        pw, ph = self._preview_size()
        sw, sh = self.ctx.screen_size
        px = (sw - pw) // 2
        py = (sh - ph) // 2
        btn_y = py + ph - 60
        go = Button(
            pygame.Rect(px + pw - 320, btn_y, 150, 44),
            "前往", fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=18, style="primary", enabled=d.available,
            on_click=self._confirm)
        cancel = Button(
            pygame.Rect(px + pw - 160, btn_y, 140, 44),
            "取消", fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=18, style="ghost", on_click=self._close_preview)
        self._preview_buttons = [go, cancel]

    def _preview_size(self) -> tuple[int, int]:
        sw, sh = self.ctx.screen_size
        return min(780, sw - 200), min(580, sh - 160)

    def _confirm(self) -> None:
        d = self._selected_dest()
        if d is None or not d.available:
            return
        if self.on_move_to:
            self.on_move_to(d.loc.id)

    # ---- input ----------------------------------------------------------

    def update(self, dt: float, inp) -> None:
        if self._selected is not None:
            for b in self._preview_buttons:
                b.update(dt, inp)
            if inp.cancel:
                self._close_preview()
            return

        if inp.cancel:
            if self.on_close:
                self.on_close()
            return
        for b in self._buttons:
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
                                   self._viewport.width, _REGION_HEADER_H)
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
            tgt = 1.0 if d.loc.id == hovered else 0.0
            self._hover_anim[d.loc.id] = cur + (tgt - cur) * min(1.0, dt * 14.0)

    def _clamp_scroll(self) -> None:
        max_scroll = max(0, self._content_h - self._viewport.height)
        self._scroll = max(0, min(self._scroll, max_scroll))

    def _card_screen_rect(self, d: _Dest) -> pygame.Rect:
        return pygame.Rect(d.crect.x,
                           self._viewport.y + d.crect.y - self._scroll,
                           d.crect.width, d.crect.height)

    # ---- drawing --------------------------------------------------------

    def draw(self, surface: pygame.Surface) -> None:
        th = self.ctx.theme
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 165))
        surface.blit(veil, (0, 0))

        Panel(self._panel, th, fill=(*th.bg_overlay[:3], 238),
              border=th.border_strong, radius=th.radius_l,
              border_width=2).draw(surface)

        title = self.ctx.fonts.render("要去哪裡？",
                                      self.ctx.config.font_size_header,
                                      th.accent, bold=True)
        surface.blit(title, (self._panel.x + 28, self._panel.y + 18))
        tlabel = self.ctx.fonts.render(self.ctx.state.time.label(),
                                       self.ctx.config.font_size_small,
                                       th.accent_warm)
        surface.blit(tlabel, (self._panel.x + 28 + title.get_width() + 24,
                              self._panel.y + 18
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
            here = self.ctx.fonts.render(f"你在：{cur.name}", 16, th.text_mute)
            surface.blit(here, (self._panel.x + 28,
                                self._panel.bottom - 34))

        if self._selected is not None:
            self._draw_preview(surface)

    def _draw_empty(self, surface: pygame.Surface) -> None:
        th = self.ctx.theme
        msg = "目前沒有可前往的地點" if (self._filter_reachable
                                       or self._filter_with_npc) \
            else "這裡沒有其他出口"
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
            if sy + _REGION_HEADER_H < self._viewport.y \
                    or sy > self._viewport.bottom:
                continue
            txt = self.ctx.fonts.render(label, 18, th.accent_warm, bold=True)
            self._draw_disclosure(surface, self._viewport.x,
                                  sy + 6 + txt.get_height() // 2,
                                  key in self._collapsed, txt.get_height())
            tx = self._viewport.x + 24
            surface.blit(txt, (tx, sy + 6))
            line_y = sy + _REGION_HEADER_H - 10
            pygame.draw.line(surface, (*th.border_soft[:3], 70),
                             (tx + txt.get_width() + 16, line_y),
                             (self._viewport.right, line_y), 1)
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
        # Hover glow behind the card (eased).
        if anim > 0.01 and d.available:
            glow = pygame.Surface((r.width + 12, r.height + 12),
                                  pygame.SRCALPHA)
            pygame.draw.rect(glow, (*th.accent[:3], int(80 * anim)),
                             glow.get_rect(),
                             border_radius=th.radius_m + 3)
            surface.blit(glow, (r.x - 6, r.y - 6))
        # Card body.
        card = pygame.Surface(r.size, pygame.SRCALPHA)
        pygame.draw.rect(card, (*th.bg_panel[:3], 235), card.get_rect(),
                         border_radius=th.radius_m)
        # Thumbnail (inset so the rounded card corners read cleanly).
        thumb = self.ctx.assets.location_background(
            d.loc, self.ctx.state.time.time_of_day.value,
            size=(r.width - 6, _THUMB_H))
        if thumb is None:
            thumb = self.ctx.assets.scaled(None, (r.width - 6, _THUMB_H))
        card.blit(thumb, (3, 3))
        if not d.available:
            shade = pygame.Surface((r.width - 6, _THUMB_H), pygame.SRCALPHA)
            shade.fill((0, 0, 0, 150))
            card.blit(shade, (3, 3))
        # "New event" badge over the thumbnail.
        if d.has_new and d.available:
            badge = self.ctx.fonts.render("● 新事件", 13, (255, 255, 255),
                                          bold=True)
            bw, bh = badge.get_width() + 14, badge.get_height() + 6
            chip = pygame.Surface((bw, bh), pygame.SRCALPHA)
            pygame.draw.rect(chip, (*th.accent[:3], 230), chip.get_rect(),
                             border_radius=th.radius_s)
            chip.blit(badge, (7, 3))
            card.blit(chip, (r.width - bw - 8, 8))
        # Border: eased toward the strong highlight on hover.
        if not d.available:
            bcol, bw_ = th.border_soft, 1
        else:
            rest = th.accent if (d.has_new or d.npc_ids) else th.border
            bcol = _lerp(rest, th.border_strong, anim)
            bw_ = 1 + int(round(anim))
        pygame.draw.rect(card, bcol, card.get_rect(), width=bw_,
                         border_radius=th.radius_m)
        surface.blit(card, r.topleft)

        # Name.
        name_color = th.text if d.available else th.text_dim
        name = self.ctx.fonts.render(d.loc.name, 20, name_color, bold=True)
        surface.blit(name, (r.x + 12, r.y + _THUMB_H + 8))

        # NPC presence row: avatars + the lead heroine's affection.
        npc_y = r.y + _THUMB_H + 40
        ax = r.x + 12
        heroine_text: str | None = None
        if d.npc_ids:
            for nid in d.npc_ids[:3]:
                npc = self.ctx.npcs.get(nid)
                if npc is None:
                    continue
                av = self.ctx.assets.scaled(npc.portrait, (_AVATAR, _AVATAR),
                                            fit="cover")
                surface.blit(av, (ax, npc_y))
                pygame.draw.rect(surface, (*th.border[:3], 160),
                                 (ax, npc_y, _AVATAR, _AVATAR), width=1,
                                 border_radius=4)
                ax += _AVATAR + 4
                if heroine_text is None and npc.is_heroine:
                    lvl = self.ctx.state.affection.level_label(npc.id)
                    heroine_text = f"{npc.name}·{lvl}"
            first = self.ctx.npcs.get(d.npc_ids[0])
            label = heroine_text or (first.name if first else "")
            if label:
                t = self.ctx.fonts.render(label, 14, th.text_mute)
                surface.blit(t, (ax + 4,
                                 npc_y + (_AVATAR - t.get_height()) // 2))
        else:
            t = self.ctx.fonts.render("沒有人在", 14, th.text_dim)
            surface.blit(t, (ax, npc_y + 4))

        # Bottom strip: travel cost (left) + lock reason (right).
        strip_y = r.bottom - 26
        cost_txt = "即時" if d.cost == 0 else f"走路 {d.cost} 時段"
        cost = self.ctx.fonts.render(cost_txt, 13,
                                     th.good if d.cost == 0 else th.accent_warm)
        surface.blit(cost, (r.x + 12, strip_y))
        if not d.available and d.reason:
            why = self.ctx.fonts.render(d.reason, 13, th.warn)
            surface.blit(why, (r.right - why.get_width() - 12, strip_y))

    def _draw_disclosure(self, surface: pygame.Surface, x: int, cy: int,
                         collapsed: bool, size: int) -> None:
        """A collapse/expand triangle drawn as a polygon (font-independent)."""
        s = max(8, int(size * 0.5))
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
        knob_h = max(30, int(track_h * (self._viewport.height / self._content_h)))
        max_scroll = self._content_h - self._viewport.height
        knob_y = int(self._scroll / max_scroll * (track_h - knob_h)) \
            if max_scroll > 0 else 0
        x = self._viewport.right + 8
        pygame.draw.rect(surface, (*th.border_soft[:3], 70),
                         (x, self._viewport.y, 4, track_h), border_radius=2)
        pygame.draw.rect(surface, (*th.accent[:3], 190),
                         (x, self._viewport.y + knob_y, 4, knob_h),
                         border_radius=2)

    def _draw_preview(self, surface: pygame.Surface) -> None:
        d = self._selected_dest()
        if d is None:
            return
        th = self.ctx.theme
        st = self.ctx.state
        sw, sh = self.ctx.screen_size
        dim = pygame.Surface((sw, sh), pygame.SRCALPHA)
        dim.fill((0, 0, 0, 150))
        surface.blit(dim, (0, 0))

        pw, ph = self._preview_size()
        px = (sw - pw) // 2
        py = (sh - ph) // 2
        panel = pygame.Rect(px, py, pw, ph)
        Panel(panel, th, fill=(*th.bg_panel[:3], 250),
              border=th.border_strong, radius=th.radius_l,
              border_width=2).draw(surface)

        # Hero image.
        img_h = 220
        thumb = self.ctx.assets.location_background(
            d.loc, st.time.time_of_day.value, size=(pw - 8, img_h))
        if thumb is None:
            thumb = self.ctx.assets.scaled(None, (pw - 8, img_h))
        surface.blit(thumb, (px + 4, py + 4))
        if not d.available:
            shade = pygame.Surface((pw - 8, img_h), pygame.SRCALPHA)
            shade.fill((0, 0, 0, 140))
            surface.blit(shade, (px + 4, py + 4))

        cx = px + 24
        ty = py + img_h + 16
        name = self.ctx.fonts.render(d.loc.name,
                                     self.ctx.config.font_size_header,
                                     th.accent, bold=True)
        surface.blit(name, (cx, ty))
        if d.loc.region:
            regions = st.map.regions
            rname = regions[d.loc.region].name if d.loc.region in regions \
                else d.loc.region
            reg = self.ctx.fonts.render(rname, 15, th.text_mute)
            surface.blit(reg, (cx + name.get_width() + 14,
                               ty + (name.get_height() - reg.get_height())))
        ty += name.get_height() + 8

        if d.loc.description:
            desc = WrappedText(pygame.Rect(cx, ty, pw - 48, 48),
                               d.loc.description, fonts=self.ctx.fonts,
                               size=15, color=th.text_mute)
            desc.draw(surface)
        ty += 56

        # Who's here.
        flags = st.events.flags
        tod = st.time.time_of_day.value
        weekday = st.time.day_of_week.value
        npc_ids = st.map.npcs_present_at(d.loc, tod, weekday, flags)
        head = self.ctx.fonts.render("現在在這裡", 15, th.text_dim, bold=True)
        surface.blit(head, (cx, ty))
        ty += 26
        if npc_ids:
            for nid in npc_ids:
                npc = self.ctx.npcs.get(nid)
                if npc is None:
                    continue
                av = self.ctx.assets.scaled(npc.portrait, (34, 34), fit="cover")
                surface.blit(av, (cx, ty))
                label = npc.name
                if npc.is_heroine:
                    lvl = st.affection.level_label(npc.id)
                    val = st.affection.get(npc.id)
                    label = f"{npc.name}  好感 {lvl}·{val}"
                elif npc.role:
                    label = f"{npc.name}  {npc.role}"
                t = self.ctx.fonts.render(label, 15, th.text)
                surface.blit(t, (cx + 42, ty + (34 - t.get_height()) // 2))
                ty += 42
        else:
            t = self.ctx.fonts.render("沒有人在", 15, th.text_dim)
            surface.blit(t, (cx, ty))
            ty += 30

        # What's available.
        hooks = st.map.scenes_available_at(
            d.loc, time_of_day=tod, flags=flags,
            played_scenes=st.story.played, state=st)
        examineable = [h for h in hooks if h.trigger == "examine"]
        if examineable:
            head2 = self.ctx.fonts.render("可以做", 15, th.text_dim, bold=True)
            surface.blit(head2, (cx, ty))
            ty += 26
            for h in examineable[:3]:
                sc = st.story.get(h.scene_id)
                label = (sc.title or sc.id) if sc else h.scene_id
                bullet = self.ctx.fonts.render(f"●  {label}", 15, th.accent)
                surface.blit(bullet, (cx, ty))
                ty += 26

        # Travel cost (with the resulting time-of-day).
        if d.cost == 0:
            cost_line = "移動花費：即時"
        else:
            future = st.time.model_copy(deep=True)
            future.advance(d.cost)
            cost_line = (f"移動花費：走路 {d.cost} 時段"
                         f"（→ {future.time_of_day.label}）")
        cl = self.ctx.fonts.render(cost_line, 15, th.accent_warm)
        surface.blit(cl, (cx, py + ph - 96))
        if not d.available and d.reason:
            wl = self.ctx.fonts.render(f"無法前往：{d.reason}", 14, th.warn)
            surface.blit(wl, (cx, py + ph - 70))

        for b in self._preview_buttons:
            b.draw(surface)

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
            "selected": self._selected,
            "filters": {"reachable": self._filter_reachable,
                        "with_npc": self._filter_with_npc},
            "collapsed_regions": sorted(
                "" if k is None else k for k in self._collapsed),
        }
