"""Flowchart / chapter-chart overlay (the Steins;Gate チャート).

Renders the pack's declared chapter/act/route structure
(``content/chapters.yaml`` → ``ChapterManifest`` on ``state.meta["__chapters__"]``)
as a branching chart laid out **by act/year (rows)**: each act (``act`` field,
e.g. ``y1``/``y2``…) is a horizontal band; within a band the common spine runs
along the top lane and routes branch into their own lanes only where they
diverge. Chapters the player has read are drawn bright in the route's colour and
are clickable to jump to (replay) that scene; unread chapters are dimmed.

Titles wrap to two lines and the cards are sized to fit CJK at a readable font.
The chart can be larger than the viewport in *both* axes, so it pans: drag with
the mouse, or use the wheel (vertical) / shift+wheel (horizontal).

Read-state comes from ``state.read_log.scenes``; jumping calls the ``on_jump``
callback (wired to the app's ``_start_dialogue``). A pack with no chapter
manifest shows a friendly empty state, so this is safe for any pack.
"""
from __future__ import annotations

import pygame

from .base import Scene, SceneContext
from ..ui.widgets import Button, Panel
from ..ui.widgets.label import _wrap_lines


_COMMON_ROUTES = ("common", "", "main")


class FlowchartScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        # Per-chapter click targets in *content* space: (rect, scene_id).
        self._hits: list[tuple[pygame.Rect, str]] = []
        # Pan offsets into the (possibly larger-than-viewport) content surface.
        self._scroll_x = 0
        self._scroll_y = 0
        self._content_w = 0
        self._content_h = 0
        # Drag-to-pan bookkeeping.
        self._dragging = False
        self._drag_origin = (0, 0)
        self._drag_scroll0 = (0, 0)
        self._drag_moved = False

    # ---- data ----------------------------------------------------------
    def _manifest(self):
        return self.ctx.state.meta.get("__chapters__")

    def _route_style(self) -> dict[str, tuple[str, tuple]]:
        """route_id -> (label, rgb colour). Heroine routes borrow the heroine's
        name colour so a branch reads as 'her' path; common/other fall back."""
        theme = self.ctx.theme
        styles: dict[str, tuple[str, tuple]] = {
            "common": ("共通線", tuple(theme.text_mute[:3])),
        }
        npcs = getattr(self.ctx, "npcs", None)
        if npcs is not None:
            try:
                heroines = npcs.heroines()
            except Exception:
                heroines = []
            for npc in heroines:
                if not npc.route_id:
                    continue
                color = tuple(theme.accent[:3])
                raw = getattr(npc, "name_color", None)
                if raw:
                    try:
                        from ..dialogue.richtext import _parse_color
                        parsed = _parse_color(raw)
                        if parsed:
                            color = tuple(parsed[:3])
                    except Exception:
                        pass
                styles[npc.route_id] = (npc.name, color)
        return styles

    @staticmethod
    def _norm_route(route: str) -> str:
        return "common" if (route or "common") in _COMMON_ROUTES else route

    def _act_order(self, chapters) -> list[str]:
        """Act ids in narrative order (first appearance across ordered chapters).
        Chapters with no ``act`` fall into a single trailing '' bucket."""
        order: list[str] = []
        for c in chapters:
            a = c.act or ""
            if a not in order:
                order.append(a)
        return order

    def _act_label(self, act: str) -> str:
        """Human row heading for an act id. Maps the common yN convention to
        '大一'…'大四'; otherwise shows the raw id (or a generic fallback)."""
        names = {"y1": "大一", "y2": "大二", "y3": "大三", "y4": "大四"}
        if act in names:
            return names[act]
        return act or "其他"

    def _chapter_read(self, chapter) -> bool:
        seen = getattr(self.ctx.state.read_log, "scenes", set()) or set()
        if chapter.entry_scene and chapter.entry_scene in seen:
            return True
        return any(s in seen for s in chapter.scenes)

    def _is_current(self, chapter) -> bool:
        """The chapter the player is currently in (``state.current_chapter``),
        for a 'you are here' emphasis. None / no match → not current."""
        cur = getattr(self.ctx.state, "current_chapter", None)
        return cur is not None and chapter.id == cur

    def _jump_scene(self, chapter) -> str | None:
        if chapter.entry_scene:
            return chapter.entry_scene
        return chapter.scenes[0] if chapter.scenes else None

    def _chapter_bg(self, chapter) -> str | None:
        """The background image of the chapter's entry scene, for a node
        thumbnail. None if the scene or its background can't be resolved."""
        sid = self._jump_scene(chapter)
        try:
            sc = self.ctx.state.story.scenes.get(sid) if sid else None
        except Exception:
            sc = None
        return getattr(sc, "background", None) if sc is not None else None

    # ---- lifecycle -----------------------------------------------------
    def enter(self, *, on_close=None, on_jump=None, **_) -> None:
        self.on_close = on_close
        self.on_jump = on_jump
        sw, sh = self.ctx.screen_size
        self._panel_rect = pygame.Rect(70, 44, sw - 140, sh - 88)
        self._panel = Panel(self._panel_rect, self.ctx.theme,
                            fill=(*self.ctx.theme.bg_overlay[:3], 240),
                            border=self.ctx.theme.border_strong,
                            radius=self.ctx.theme.radius_l, border_width=2)
        self.close_btn = Button(
            pygame.Rect(self._panel_rect.right - 120 - 16,
                        self._panel_rect.y + 16, 120, 36),
            self.ctx.localization.t("close", "關閉"),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=15, style="ghost",
            on_click=(lambda: on_close() if on_close else None),
        )
        # The chart viewport (everything below the header band).
        self._view_rect = pygame.Rect(
            self._panel_rect.x + 24, self._panel_rect.y + 78,
            self._panel_rect.width - 48, self._panel_rect.height - 78 - 26)
        self._scroll_x = 0
        self._scroll_y = 0
        # Pre-render the whole chart once on enter; cheap and keeps draw simple.
        self._content: pygame.Surface | None = None
        self._build_content()

    # ---- layout / rendering -------------------------------------------
    # Geometry. Cards are sized to hold two readable CJK lines + a subtitle.
    _CARD_W = 248
    _CARD_H = 96
    _COL_GAP = 30          # horizontal gap between column slots
    _LANE_GAP = 18         # vertical gap between lanes inside an act band
    _ACT_GAP = 30          # vertical gap between act bands
    _ROW_LABEL_W = 64      # left gutter for the act/year heading
    _TITLE_SIZE = 19
    _SUB_SIZE = 13
    _PAD_X = 8             # content padding inside the surface
    _PAD_TOP = 10

    def _build_content(self) -> None:
        """Compute the full chart layout and render it to ``self._content``.

        Layout model: rows = acts (in narrative order). Inside an act band the
        common spine is lane 0; each non-common route gets its own lane below.
        A single column counter advances across the act's chapters in ``order``
        so the chart reads left-to-right as a timeline, with routes dropping into
        their lane where they branch off the spine.
        """
        theme = self.ctx.theme
        self._hits = []
        manifest = self._manifest()
        chapters = manifest.ordered() if manifest is not None else []
        if not chapters:
            self._content = None
            self._content_w = self._content_h = 0
            return

        styles = self._route_style()
        col_pitch = self._CARD_W + self._COL_GAP
        lane_pitch = self._CARD_H + self._LANE_GAP

        # Per-chapter placement: (chapter, route, col, lane, y_top).
        placements: list[tuple] = []
        # Branch + chain connectors as (x1, y1, x2, y2, route): drawn under cards.
        connectors: list[tuple] = []
        act_bands: list[tuple[str, int, int]] = []   # (act, band_top, band_h)

        col = 0
        y = self._PAD_TOP
        max_col_used = 0
        for act in self._act_order(chapters):
            act_chs = [c for c in chapters if (c.act or "") == act]
            # Lanes: common first, then routes by first appearance in this act.
            lane_order: list[str] = ["common"]
            for c in act_chs:
                r = self._norm_route(c.route)
                if r not in lane_order:
                    lane_order.append(r)
            lane_of = {r: i for i, r in enumerate(lane_order)}
            band_top = y
            band_h = len(lane_order) * lane_pitch - self._LANE_GAP

            prev_in_route: dict[str, tuple[int, int]] = {}   # route -> (cx, cy)
            last_common: tuple[int, int] | None = None
            act_start_col = col
            for c in act_chs:
                r = self._norm_route(c.route)
                lane = lane_of[r]
                x = self._ROW_LABEL_W + self._PAD_X + col * col_pitch
                y_top = band_top + lane * lane_pitch
                cx = x + self._CARD_W // 2
                cy = y_top + self._CARD_H // 2
                placements.append((c, r, x, y_top))
                # Connector: chain within a route, else branch from the spine.
                prev = prev_in_route.get(r)
                color = styles.get(r, ("", tuple(theme.accent[:3])))[1]
                if prev is not None:
                    connectors.append((prev[0], prev[1], cx, cy, color, r,
                                       self._chapter_read(c)))
                elif r != "common" and last_common is not None:
                    connectors.append((last_common[0], last_common[1],
                                       cx, cy, color, r, self._chapter_read(c)))
                prev_in_route[r] = (cx, cy)
                if r == "common":
                    last_common = (cx, cy)
                col += 1
            # If an act had no chapters at all (shouldn't happen) keep a min band.
            if col == act_start_col:
                band_h = lane_pitch - self._LANE_GAP
            max_col_used = max(max_col_used, col)
            act_bands.append((act, band_top, band_h))
            y = band_top + band_h + self._ACT_GAP

        content_w = (self._ROW_LABEL_W + self._PAD_X
                     + max_col_used * col_pitch - self._COL_GAP + self._PAD_X)
        content_h = y - self._ACT_GAP + self._PAD_TOP
        content_w = max(content_w, self._view_rect.width)
        content_h = max(content_h, self._view_rect.height)
        self._content_w = content_w
        self._content_h = content_h

        surface = pygame.Surface((content_w, content_h), pygame.SRCALPHA)

        # Act-band backgrounds + left-gutter year headings (drawn first).
        for act, band_top, band_h in act_bands:
            band = pygame.Rect(0, band_top - 4, content_w, band_h + 8)
            shade = pygame.Surface(band.size, pygame.SRCALPHA)
            shade.fill((*theme.text_dim[:3], 14))
            surface.blit(shade, band.topleft)
            label = self._act_label(act)
            lsurf = self.ctx.fonts.render(label, 20, theme.text_mute, bold=True)
            surface.blit(lsurf, (4, band_top + 6))

        # Connectors under the cards (orthogonal elbows read cleanly when a
        # route drops to a lower lane / advances to a later column).
        for x1, y1, x2, y2, color, r, read in connectors:
            line_col = (*color, 170 if read else 56)
            if y1 == y2:
                pygame.draw.line(surface, line_col, (x1, y1), (x2, y2), 3)
            else:
                midx = (x1 + x2) // 2
                pygame.draw.lines(surface, line_col, False,
                                  [(x1, y1), (midx, y1),
                                   (midx, y2), (x2, y2)], 3)

        # Cards on top.
        for c, r, x, y_top in placements:
            self._draw_card(surface, c, r, x, y_top, styles)

        self._content = surface

    def _draw_card(self, surface, chapter, route, x, y_top, styles) -> None:
        theme = self.ctx.theme
        label, color = styles.get(route, (route, tuple(theme.accent[:3])))
        read = self._chapter_read(chapter)
        here = self._is_current(chapter)
        card_w, card_h = self._CARD_W, self._CARD_H
        rad = theme.radius_m
        card = pygame.Surface((card_w, card_h), pygame.SRCALPHA)

        bg = self._chapter_bg(chapter) if read else None
        if bg:
            try:
                thumb = self.ctx.assets.scaled(
                    bg, (card_w, card_h), fit="cover").copy()
                mask = pygame.Surface((card_w, card_h), pygame.SRCALPHA)
                pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(),
                                 border_radius=rad)
                thumb.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
                card.blit(thumb, (0, 0))
                dark = pygame.Surface((card_w, card_h), pygame.SRCALPHA)
                pygame.draw.rect(dark, (10, 8, 16, 150), dark.get_rect(),
                                 border_radius=rad)
                card.blit(dark, (0, 0))
            except Exception:
                bg = None
        if not bg:
            fill = (*color, 70) if read else (*theme.text_dim[:3], 26)
            pygame.draw.rect(card, fill, card.get_rect(), border_radius=rad)

        if here:
            pygame.draw.rect(card, (*theme.accent[:3], 255),
                             card.get_rect(), width=3, border_radius=rad)
        else:
            pygame.draw.rect(card, (*color, 235) if read
                             else (*theme.text_dim[:3], 90),
                             card.get_rect(), width=2, border_radius=rad)

        # Title: wrap to <=2 lines at a readable size; ellipsize the 2nd line
        # only if the title still overflows, so CJK titles stay legible.
        title = chapter.title or chapter.id
        t_col = theme.text if read else theme.text_dim
        font = self.ctx.fonts.get(self._TITLE_SIZE, bold=True)
        maxw = card_w - 20
        lines = _wrap_lines(title, font, maxw)
        if len(lines) > 2:
            second = lines[1]
            while second and font.size(second + "…")[0] > maxw:
                second = second[:-1]
            lines = [lines[0], second + "…"]
        ty = 8
        for ln in lines[:2]:
            tsurf = self.ctx.fonts.render(ln, self._TITLE_SIZE, t_col, bold=True)
            card.blit(tsurf, (10, ty))
            ty += tsurf.get_height() + 1

        # Footer: route label + ending / 'you are here' marker.
        sub = f"{label}" + ("　·　終" if chapter.endings else "")
        if here:
            sub = "現在地　·　" + sub
        ssurf = self.ctx.fonts.render(
            sub, self._SUB_SIZE,
            theme.accent if here
            else (theme.text_mute if read else theme.text_dim))
        if ssurf.get_width() > card_w - 30:
            ssurf = ssurf.subsurface((0, 0, card_w - 30, ssurf.get_height()))
        card.blit(ssurf, (10, card_h - ssurf.get_height() - 7))

        # read / unread sigil (filled dot = read; hollow = unread).
        sx, sy = card_w - 18, 16
        if read:
            pygame.draw.circle(card, (*color, 255), (sx, sy), 7)
        else:
            pygame.draw.circle(card, (*theme.text_dim[:3], 200),
                               (sx, sy), 7, 2)

        surface.blit(card, (x, y_top))
        if read:
            self._hits.append((pygame.Rect(x, y_top, card_w, card_h),
                               self._jump_scene(chapter) or ""))

    # ---- input ---------------------------------------------------------
    def _clamp_scroll(self) -> None:
        max_x = max(0, self._content_w - self._view_rect.width)
        max_y = max(0, self._content_h - self._view_rect.height)
        self._scroll_x = max(0, min(self._scroll_x, max_x))
        self._scroll_y = max(0, min(self._scroll_y, max_y))

    def update(self, dt: float, inp) -> None:
        if inp.cancel and self.on_close:
            self.on_close()
            return
        self.close_btn.update(dt, inp)

        over = self._view_rect.collidepoint(inp.mouse_pos)
        # Wheel pans: shift -> horizontal, else vertical. If the chart only
        # overflows horizontally, a plain wheel still pans it (most useful).
        if over and inp.mouse_wheel:
            shift = bool(inp.keys_down & {pygame.K_LSHIFT, pygame.K_RSHIFT})
            horiz_only = (self._content_h <= self._view_rect.height
                          and self._content_w > self._view_rect.width)
            if shift or horiz_only:
                self._scroll_x -= inp.mouse_wheel * 48
            else:
                self._scroll_y -= inp.mouse_wheel * 48

        # Drag-to-pan. Track whether the pointer moved so a drag isn't read as
        # a click on release.
        pressed = inp.mouse_pressed[0]
        if pressed and not self._dragging and over:
            self._dragging = True
            self._drag_origin = inp.mouse_pos
            self._drag_scroll0 = (self._scroll_x, self._scroll_y)
            self._drag_moved = False
        elif pressed and self._dragging:
            dx = inp.mouse_pos[0] - self._drag_origin[0]
            dy = inp.mouse_pos[1] - self._drag_origin[1]
            if abs(dx) + abs(dy) > 4:
                self._drag_moved = True
            self._scroll_x = self._drag_scroll0[0] - dx
            self._scroll_y = self._drag_scroll0[1] - dy
        elif not pressed:
            self._dragging = False

        self._clamp_scroll()

        # Click a read chapter card to jump (replay) — only on a non-drag click.
        if (inp.mouse_clicked and self.on_jump and self._hits
                and not self._drag_moved
                and self._view_rect.collidepoint(inp.mouse_pos)):
            cx = inp.mouse_pos[0] - self._view_rect.x + self._scroll_x
            cy = inp.mouse_pos[1] - self._view_rect.y + self._scroll_y
            for rect, scene_id in self._hits:
                if scene_id and rect.collidepoint(cx, cy):
                    self.on_jump(scene_id)
                    return

    def draw(self, surface: pygame.Surface) -> None:
        theme = self.ctx.theme
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 214))
        surface.blit(veil, (0, 0))
        self._panel.draw(surface)
        title = self.ctx.fonts.render(
            self.ctx.localization.t("flowchart", "流程圖"),
            self.ctx.config.font_size_header,
            theme.accent, bold=True)
        surface.blit(title, (self._panel_rect.x + 32, self._panel_rect.y + 26))
        hint = self.ctx.fonts.render(
            "點亮的章節可點擊跳轉　·　拖曳或滾輪捲動", 14, theme.text_mute)
        surface.blit(hint, (self._panel_rect.right - hint.get_width() - 160,
                            self._panel_rect.y + 32))
        self.close_btn.draw(surface)

        # Chart viewport: blit a window slice of the pre-rendered content.
        if self._content is not None:
            self._clamp_scroll()
            view = pygame.Surface(self._view_rect.size, pygame.SRCALPHA)
            view.blit(self._content, (-self._scroll_x, -self._scroll_y))
            surface.blit(view, self._view_rect.topleft)
            self._draw_scrollbars(surface)
        else:
            msg = self.ctx.fonts.render(
                "本作沒有章節資料（content/chapters.yaml）。", 18,
                theme.text_mute)
            surface.blit(msg, (self._view_rect.x + 4, self._view_rect.y + 8))

    def _draw_scrollbars(self, surface: pygame.Surface) -> None:
        theme = self.ctx.theme
        vr = self._view_rect
        if self._content_h > vr.height:
            track_h = vr.height
            knob_h = max(28, int(track_h * (vr.height / self._content_h)))
            knob_y = int(self._scroll_y / self._content_h * track_h)
            pygame.draw.rect(surface, (*theme.border_soft[:3], 80),
                             (vr.right - 6, vr.y, 4, track_h), border_radius=2)
            pygame.draw.rect(surface, (*theme.accent[:3], 180),
                             (vr.right - 6, vr.y + knob_y, 4, knob_h),
                             border_radius=2)
        if self._content_w > vr.width:
            track_w = vr.width
            knob_w = max(28, int(track_w * (vr.width / self._content_w)))
            knob_x = int(self._scroll_x / self._content_w * track_w)
            pygame.draw.rect(surface, (*theme.border_soft[:3], 80),
                             (vr.x, vr.bottom - 6, track_w, 4), border_radius=2)
            pygame.draw.rect(surface, (*theme.accent[:3], 180),
                             (vr.x + knob_x, vr.bottom - 6, knob_w, 4),
                             border_radius=2)

    def describe(self) -> dict:
        manifest = self._manifest()
        chapters = manifest.ordered() if manifest is not None else []
        return {
            "scene": "FlowchartScene",
            "chapters": [
                {"id": c.id, "route": c.route, "act": c.act,
                 "read": self._chapter_read(c),
                 "current": self._is_current(c)}
                for c in chapters
            ],
        }
