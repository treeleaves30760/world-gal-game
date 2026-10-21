"""Flowchart / chapter-chart overlay (the Steins;Gate チャート).

Renders the pack's declared chapter/act/route structure
(``content/chapters.yaml`` → ``ChapterManifest`` on ``state.meta["__chapters__"]``)
as a branching chart laid out **by act/year (rows)**: each act (``act`` field,
e.g. ``y1``/``y2``…) is a horizontal band; within a band the common spine runs
along the top lane and routes branch into their own lanes only where they
diverge. Chapters the player has read are drawn bright in the route's colour;
only chapters backed by a complete first-view presentation trace are clickable
for a read-only replay. Unread chapters are dimmed. This chart never rewinds or
branches the live playthrough.

Titles wrap to two lines and the cards are sized to fit CJK at a readable font.
The chart can be larger than the viewport in *both* axes, so it pans: drag with
the mouse, or use the wheel (vertical) / shift+wheel (horizontal).

Visual read-state comes from ``state.read_log.scenes``. Replay permission comes
only from ``state.read_log.replayable_scene_ids()`` and calls the ``on_jump``
callback, which the app wires to :class:`SceneReplayScene`'s isolated sandbox.
A pack with no chapter manifest shows a friendly empty state, so this is safe
for any pack.
"""
from __future__ import annotations

import pygame

from .base import Scene, SceneContext
from ..ui.input_bindings import BindingProfile, InputAction
from ..ui.widgets import Button, Panel
from ..ui.widgets.label import _wrap_lines


_COMMON_ROUTES = ("common", "", "main")
_MIN_TARGET = 44


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
        self._hit_chapter_ids: list[str] = []
        # Keyboard/controller focus is intentionally separate from pointer
        # hover.  The stored index addresses ``_hits`` in content space.
        self._focused_hit = -1
        self._last_card_focus = -1
        self._focus_close = True
        self._hovered_hit = -1
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

    def _replay_scene(self, chapter) -> str | None:
        """Return a trace-backed scene from ``chapter`` for safe replay.

        ``read_log.scenes`` remains the visual history signal, including for
        legacy and New Game Plus saves. It is not replay evidence: an older
        save may have no evaluated presentation trace at all. Such a card may
        still light up, but it must remain inert instead of opening an empty
        one-shot replay overlay.
        """
        replayable = self.ctx.state.read_log.replayable_scene_ids()
        candidates = [chapter.entry_scene, *chapter.scenes]
        return next(
            (
                scene_id
                for scene_id in candidates
                if scene_id and scene_id in replayable
            ),
            None,
        )

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
        self._configure_layout()
        self._panel = Panel(
            self._panel_rect,
            self.ctx.theme,
            fill=(*self.ctx.theme.bg_overlay[:3], 240),
            border=self.ctx.theme.border_strong,
            radius=self.ctx.theme.radius_l,
            border_width=2,
        )
        self.close_btn = Button(
            self._close_rect,
            self._close_label,
            fonts=self.ctx.fonts,
            theme=self.ctx.theme,
            font_size=self._close_size,
            style="ghost",
            on_click=self._request_close,
            motion=self.ctx.motion,
        )
        self._scroll_x = 0
        self._scroll_y = 0
        # Pre-render the whole chart once on enter; cheap and keeps draw simple.
        self._content: pygame.Surface | None = None
        self._build_content()
        self._reset_focus()

    def _request_close(self) -> None:
        if self.on_close is not None:
            self.on_close()

    def _bindings(self) -> BindingProfile:
        return BindingProfile(
            getattr(self.ctx.config, "input_bindings", {}) or {},
        )

    def _sp(self, value: int) -> int:
        return max(1, int(round(value * self._layout_scale)))

    def _configure_layout(self) -> None:
        """Fit the chart, header, and controls to the logical canvas.

        The 1280x720 geometry remains the design baseline.  Small canvases do
        not inherit the old fixed 70px margins or 36px close target; 1080p
        scales card geometry alongside the application's scaled typography.
        """
        sw, sh = (max(1, int(value)) for value in self.ctx.screen_size)
        self._layout_scale = max(0.72, min(3.0, sh / 720.0))

        margin_x = max(12, min(self._sp(70), max(12, sw // 12)))
        margin_y = max(12, min(self._sp(44), max(12, sh // 12)))
        self._panel_rect = pygame.Rect(
            margin_x,
            margin_y,
            max(1, sw - margin_x * 2),
            max(1, sh - margin_y * 2),
        )
        inner = max(12, self._sp(24))
        header_top = self._panel_rect.y + inner

        self._title_label = self.ctx.localization.t("flowchart", "流程圖")
        self._title_size = max(20, self.ctx.config.font_size_header)
        title_font = self.ctx.fonts.get(self._title_size, bold=True)
        title_w, title_h = title_font.size(self._title_label)

        self._close_size = max(13, int(round(15 * min(
            1.0,
            self._layout_scale,
        ))))
        back_hint = self._bindings().primary_display(InputAction.BACK)
        close_text = self.ctx.localization.t("close", "關閉")
        self._close_label = f"{close_text}  {back_hint}" if back_hint else close_text
        close_font = self.ctx.fonts.get(self._close_size, bold=True)
        close_h = max(_MIN_TARGET, self._sp(36))
        close_w = max(
            _MIN_TARGET,
            self._sp(120),
            close_font.size(self._close_label)[0] + self._sp(24),
        )
        close_w = min(close_w, max(1, self._panel_rect.width - inner * 2))
        header_h = max(title_h, close_h)
        self._header_rect = pygame.Rect(
            self._panel_rect.x + inner,
            header_top,
            max(1, self._panel_rect.width - inner * 2),
            header_h,
        )
        self._close_rect = pygame.Rect(
            self._header_rect.right - close_w,
            self._header_rect.y + (header_h - close_h) // 2,
            close_w,
            close_h,
        )
        title_available = max(
            1,
            self._close_rect.x - self._header_rect.x - self._sp(12),
        )
        self._title_rect = pygame.Rect(
            self._header_rect.x,
            self._header_rect.y + (header_h - title_h) // 2,
            min(title_w, title_available),
            title_h,
        )

        # Keep guidance on one non-overlapping line.  A concise instruction is
        # preferred on 360p/450p; if even that cannot fit, the semantic labels
        # remain available through ``describe`` and the close-button key hint.
        hint_gap = self._sp(14)
        hint_x = self._title_rect.right + hint_gap
        hint_w = max(0, self._close_rect.x - hint_gap - hint_x)
        self._hint_size = max(11, int(round(14 * min(
            1.0,
            self._layout_scale,
        ))))
        hint_font = self.ctx.fonts.get(self._hint_size)
        hint_candidates = (
            "有回想記錄的章節可重溫　·　方向鍵移動　·　確認開啟",
            "方向鍵選擇　·　確認重溫",
            "選擇回想記錄",
        )
        self._hint_text = next(
            (
                candidate
                for candidate in hint_candidates
                if hint_font.size(candidate)[0] <= hint_w
            ),
            "",
        )
        hint_h = hint_font.get_linesize()
        self._hint_rect = pygame.Rect(
            hint_x,
            self._header_rect.y + (header_h - hint_h) // 2,
            hint_w,
            hint_h,
        )

        view_top = self._header_rect.bottom + max(8, self._sp(12))
        view_bottom = self._panel_rect.bottom - inner
        self._view_rect = pygame.Rect(
            self._panel_rect.x + inner,
            view_top,
            max(1, self._panel_rect.width - inner * 2),
            max(1, view_bottom - view_top),
        )

        # Instance geometry lets high-resolution canvases scale with fonts,
        # while 360p retains a readable two-line title and >=44px cards.
        self._card_w = max(176, self._sp(self._CARD_W))
        self._card_h = max(76, self._sp(self._CARD_H))
        self._col_gap = max(16, self._sp(self._COL_GAP))
        self._lane_gap = max(12, self._sp(self._LANE_GAP))
        self._act_gap = max(18, self._sp(self._ACT_GAP))
        self._row_label_w = max(52, self._sp(self._ROW_LABEL_W))
        self._content_pad_x = max(6, self._sp(self._PAD_X))
        self._content_pad_top = max(7, self._sp(self._PAD_TOP))
        self._card_title_size = max(16, int(round(
            self._TITLE_SIZE * min(1.0, self._layout_scale),
        )))
        self._card_sub_size = max(11, int(round(
            self._SUB_SIZE * min(1.0, self._layout_scale),
        )))
        self._row_label_size = max(16, int(round(
            20 * min(1.0, self._layout_scale),
        )))

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
        self._hit_chapter_ids = []
        manifest = self._manifest()
        chapters = manifest.ordered() if manifest is not None else []
        if not chapters:
            self._content = None
            self._content_w = self._content_h = 0
            return

        styles = self._route_style()
        col_pitch = self._card_w + self._col_gap
        lane_pitch = self._card_h + self._lane_gap

        # Per-chapter placement: (chapter, route, col, lane, y_top).
        placements: list[tuple] = []
        # Branch + chain connectors as (x1, y1, x2, y2, route): drawn under cards.
        connectors: list[tuple] = []
        act_bands: list[tuple[str, int, int]] = []   # (act, band_top, band_h)

        col = 0
        y = self._content_pad_top
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
            band_h = len(lane_order) * lane_pitch - self._lane_gap

            prev_in_route: dict[str, tuple[int, int]] = {}   # route -> (cx, cy)
            last_common: tuple[int, int] | None = None
            act_start_col = col
            for c in act_chs:
                r = self._norm_route(c.route)
                lane = lane_of[r]
                x = self._row_label_w + self._content_pad_x + col * col_pitch
                y_top = band_top + lane * lane_pitch
                cx = x + self._card_w // 2
                cy = y_top + self._card_h // 2
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
                band_h = lane_pitch - self._lane_gap
            max_col_used = max(max_col_used, col)
            act_bands.append((act, band_top, band_h))
            y = band_top + band_h + self._act_gap

        content_w = (
            self._row_label_w
            + self._content_pad_x
            + max_col_used * col_pitch
            - self._col_gap
            + self._content_pad_x
        )
        content_h = y - self._act_gap + self._content_pad_top
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
            lsurf = self.ctx.fonts.render(
                label,
                self._row_label_size,
                theme.text_mute,
                bold=True,
            )
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
        card_w, card_h = self._card_w, self._card_h
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
        font = self.ctx.fonts.get(self._card_title_size, bold=True)
        maxw = card_w - self._sp(20)
        lines = _wrap_lines(title, font, maxw)
        if len(lines) > 2:
            second = lines[1]
            while second and font.size(second + "…")[0] > maxw:
                second = second[:-1]
            lines = [lines[0], second + "…"]
        ty = self._sp(8)
        for ln in lines[:2]:
            tsurf = self.ctx.fonts.render(
                ln,
                self._card_title_size,
                t_col,
                bold=True,
            )
            card.blit(tsurf, (self._sp(10), ty))
            ty += tsurf.get_height() + 1

        # Footer: route label + ending / 'you are here' marker.
        sub = f"{label}" + ("　·　終" if chapter.endings else "")
        if here:
            sub = "現在地　·　" + sub
        ssurf = self.ctx.fonts.render(
            sub, self._card_sub_size,
            theme.accent if here
            else (theme.text_mute if read else theme.text_dim))
        if ssurf.get_width() > card_w - self._sp(30):
            ssurf = ssurf.subsurface(
                (0, 0, card_w - self._sp(30), ssurf.get_height()),
            )
        card.blit(
            ssurf,
            (
                self._sp(10),
                card_h - ssurf.get_height() - self._sp(7),
            ),
        )

        # read / unread sigil (filled dot = read; hollow = unread).
        sx, sy = card_w - self._sp(18), self._sp(16)
        sigil_radius = max(5, self._sp(7))
        if read:
            pygame.draw.circle(
                card,
                (*color, 255),
                (sx, sy),
                sigil_radius,
            )
        else:
            pygame.draw.circle(
                card,
                (*theme.text_dim[:3], 200),
                (sx, sy),
                sigil_radius,
                2,
            )

        surface.blit(card, (x, y_top))
        if read:
            self._hits.append((pygame.Rect(x, y_top, card_w, card_h),
                               self._replay_scene(chapter) or ""))
            self._hit_chapter_ids.append(chapter.id)

    # ---- input ---------------------------------------------------------
    def _clamp_scroll(self) -> None:
        max_x = max(0, self._content_w - self._view_rect.width)
        max_y = max(0, self._content_h - self._view_rect.height)
        self._scroll_x = max(0, min(self._scroll_x, max_x))
        self._scroll_y = max(0, min(self._scroll_y, max_y))

    def _interactive_hits(self) -> list[int]:
        if self.on_jump is None:
            return []
        return [
            index
            for index, (_rect, scene_id) in enumerate(self._hits)
            if scene_id
        ]

    def _reset_focus(self) -> None:
        interactive = self._interactive_hits()
        if not interactive:
            self._focused_hit = -1
            self._focus_close = True
            self._sync_focus()
            return
        current = getattr(self.ctx.state, "current_chapter", None)
        initial = next(
            (
                index
                for index in interactive
                if self._hit_chapter_ids[index] == current
            ),
            interactive[-1],
        )
        self._set_card_focus(initial)

    def _sync_focus(self) -> None:
        if hasattr(self, "close_btn"):
            self.close_btn.focused = self._focus_close

    def _set_card_focus(self, hit_index: int) -> bool:
        if hit_index not in self._interactive_hits():
            return False
        self._focused_hit = hit_index
        self._last_card_focus = hit_index
        self._focus_close = False
        self._ensure_focus_visible()
        self._sync_focus()
        return True

    def _set_close_focus(self) -> None:
        self._focused_hit = -1
        self._focus_close = True
        self._sync_focus()

    def _cycle_focus(self, step: int) -> None:
        interactive = self._interactive_hits()
        if not interactive:
            self._set_close_focus()
            return
        # Visual/tab order is every playable card, then the close action.
        targets: list[int | None] = [*interactive, None]
        current: int | None = None if self._focus_close else self._focused_hit
        try:
            position = targets.index(current)
        except ValueError:
            position = len(targets) - 1
        target = targets[(position + step) % len(targets)]
        if target is None:
            self._set_close_focus()
        else:
            self._set_card_focus(target)

    def _move_focus(self, dx: int, dy: int) -> None:
        interactive = self._interactive_hits()
        if not interactive:
            self._set_close_focus()
            return
        if self._focus_close or self._focused_hit not in interactive:
            fallback = (
                self._last_card_focus
                if self._last_card_focus in interactive
                else interactive[-1]
            )
            self._set_card_focus(fallback)
            return

        source = self._hits[self._focused_hit][0].center
        candidates: list[tuple[float, int]] = []
        for index in interactive:
            if index == self._focused_hit:
                continue
            center = self._hits[index][0].center
            delta_x = center[0] - source[0]
            delta_y = center[1] - source[1]
            primary = delta_x * dx + delta_y * dy
            if primary <= 0:
                continue
            secondary = abs(delta_y) if dx else abs(delta_x)
            candidates.append((primary + secondary * 0.35, index))
        if candidates:
            self._set_card_focus(min(candidates)[1])
        elif dy < 0:
            # The close action is visually above the chart and remains a
            # reachable escape even for players who do not use Tab.
            self._set_close_focus()

    def _ensure_focus_visible(self) -> None:
        if self._focus_close or not (0 <= self._focused_hit < len(self._hits)):
            return
        rect = self._hits[self._focused_hit][0]
        pad_x = min(self._sp(12), max(0, (self._view_rect.width - rect.width) // 2))
        pad_y = min(self._sp(12), max(0, (self._view_rect.height - rect.height) // 2))
        if rect.left - pad_x < self._scroll_x:
            self._scroll_x = rect.left - pad_x
        elif rect.right + pad_x > self._scroll_x + self._view_rect.width:
            self._scroll_x = rect.right + pad_x - self._view_rect.width
        if rect.top - pad_y < self._scroll_y:
            self._scroll_y = rect.top - pad_y
        elif rect.bottom + pad_y > self._scroll_y + self._view_rect.height:
            self._scroll_y = rect.bottom + pad_y - self._view_rect.height
        self._clamp_scroll()

    def _visible_hit_indices(self) -> list[int]:
        visible = pygame.Rect(
            self._scroll_x,
            self._scroll_y,
            self._view_rect.width,
            self._view_rect.height,
        )
        return [
            index
            for index, (rect, _scene_id) in enumerate(self._hits)
            if rect.colliderect(visible)
        ]

    def _move_page(self, direction: int) -> None:
        horizontal_only = (
            self._content_h <= self._view_rect.height
            and self._content_w > self._view_rect.width
        )
        if horizontal_only:
            self._scroll_x += direction * max(
                _MIN_TARGET,
                int(self._view_rect.width * 0.8),
            )
        else:
            self._scroll_y += direction * max(
                _MIN_TARGET,
                int(self._view_rect.height * 0.8),
            )
        self._clamp_scroll()
        visible = [
            index
            for index in self._visible_hit_indices()
            if index in self._interactive_hits()
        ]
        if visible:
            view_center = (
                self._scroll_x + self._view_rect.width // 2,
                self._scroll_y + self._view_rect.height // 2,
            )
            target = min(
                visible,
                key=lambda index: (
                    abs(self._hits[index][0].centerx - view_center[0])
                    + abs(self._hits[index][0].centery - view_center[1])
                ),
            )
            self._set_card_focus(target)

    def _activate_focus(self) -> bool:
        if self._focus_close:
            return self.close_btn.activate()
        if (
            self.on_jump is not None
            and 0 <= self._focused_hit < len(self._hits)
        ):
            scene_id = self._hits[self._focused_hit][1]
            if scene_id:
                self.on_jump(scene_id)
                return True
        return False

    def _hit_at_screen_point(self, point: tuple[int, int]) -> int:
        if not self._view_rect.collidepoint(point):
            return -1
        content_point = (
            point[0] - self._view_rect.x + self._scroll_x,
            point[1] - self._view_rect.y + self._scroll_y,
        )
        return next(
            (
                index
                for index, (rect, _scene_id) in enumerate(self._hits)
                if rect.collidepoint(content_point)
            ),
            -1,
        )

    def _hit_screen_rect(self, hit_index: int) -> pygame.Rect:
        rect = self._hits[hit_index][0]
        return rect.move(
            self._view_rect.x - self._scroll_x,
            self._view_rect.y - self._scroll_y,
        )

    def update(self, dt: float, inp) -> None:
        if inp.pressed(InputAction.BACK) or inp.cancel:
            self._request_close()
            return
        self.close_btn.update(dt, inp)
        if inp.mouse_clicked and self._close_rect.collidepoint(inp.mouse_pos):
            return

        if inp.pressed(InputAction.FOCUS_NEXT):
            self._cycle_focus(1)
        elif inp.pressed(InputAction.FOCUS_PREVIOUS):
            self._cycle_focus(-1)
        elif inp.pressed(InputAction.NAV_LEFT):
            self._move_focus(-1, 0)
        elif inp.pressed(InputAction.NAV_RIGHT):
            self._move_focus(1, 0)
        elif inp.pressed(InputAction.NAV_UP):
            self._move_focus(0, -1)
        elif inp.pressed(InputAction.NAV_DOWN):
            self._move_focus(0, 1)
        elif inp.pressed(InputAction.PAGE_UP):
            self._move_page(-1)
        elif inp.pressed(InputAction.PAGE_DOWN):
            self._move_page(1)

        if inp.pressed(InputAction.CONFIRM_ADVANCE) or inp.confirm:
            if self._activate_focus():
                return

        over = self._view_rect.collidepoint(inp.mouse_pos)
        # Wheel pans: shift -> horizontal, else vertical. If the chart only
        # overflows horizontally, a plain wheel still pans it (most useful).
        if over and inp.mouse_wheel:
            shift = bool(
                getattr(inp, "keys_held", set())
                & {pygame.K_LSHIFT, pygame.K_RSHIFT}
            )
            horiz_only = (self._content_h <= self._view_rect.height
                          and self._content_w > self._view_rect.width)
            if shift or horiz_only:
                self._scroll_x -= inp.mouse_wheel * max(48, self._sp(48))
            else:
                self._scroll_y -= inp.mouse_wheel * max(48, self._sp(48))

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
        self._hovered_hit = self._hit_at_screen_point(inp.mouse_pos)

        # Click a trace-backed chapter card — only on a non-drag click.
        if (
            inp.mouse_clicked
            and self.on_jump is not None
            and not self._drag_moved
            and self._hovered_hit >= 0
        ):
            scene_id = self._hits[self._hovered_hit][1]
            if scene_id:
                self._set_card_focus(self._hovered_hit)
                self.on_jump(scene_id)
                return

    def draw(self, surface: pygame.Surface) -> None:
        theme = self.ctx.theme
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 214))
        surface.blit(veil, (0, 0))
        self._panel.draw(surface)
        title = self.ctx.fonts.render(
            self._title_label,
            self._title_size,
            theme.accent,
            bold=True,
        )
        surface.blit(title, self._title_rect.topleft)
        if self._hint_text:
            hint = self.ctx.fonts.render(
                self._hint_text,
                self._hint_size,
                theme.text_mute,
            )
            surface.blit(hint, self._hint_rect.topleft)
        self.close_btn.draw(surface)

        # Chart viewport: blit a window slice of the pre-rendered content.
        if self._content is not None:
            self._clamp_scroll()
            view = pygame.Surface(self._view_rect.size, pygame.SRCALPHA)
            view.blit(self._content, (-self._scroll_x, -self._scroll_y))
            surface.blit(view, self._view_rect.topleft)
            self._draw_card_focus(surface)
            self._draw_scrollbars(surface)
        else:
            empty_font = self.ctx.fonts.get(18)
            empty_lines = _wrap_lines(
                "本作沒有章節資料（content/chapters.yaml）。",
                empty_font,
                max(1, self._view_rect.width - self._sp(16)),
            )
            y = self._view_rect.y + self._sp(8)
            for line in empty_lines:
                msg = self.ctx.fonts.render(line, 18, theme.text_mute)
                surface.blit(msg, (self._view_rect.x + self._sp(4), y))
                y += empty_font.get_linesize()

    def _draw_card_focus(self, surface: pygame.Surface) -> None:
        old_clip = surface.get_clip()
        surface.set_clip(self._view_rect)
        if 0 <= self._hovered_hit < len(self._hits):
            hover_pad = max(2, self._sp(2))
            hover_rect = self._hit_screen_rect(self._hovered_hit).inflate(
                hover_pad,
                hover_pad,
            )
            pygame.draw.rect(
                surface,
                self.ctx.theme.border_strong,
                hover_rect,
                width=max(2, self._sp(2)),
                border_radius=self.ctx.theme.radius_m,
            )
        if (
            not self._focus_close
            and 0 <= self._focused_hit < len(self._hits)
        ):
            focus_pad = max(4, self._sp(4))
            focus_rect = self._hit_screen_rect(self._focused_hit).inflate(
                focus_pad,
                focus_pad,
            )
            pygame.draw.rect(
                surface,
                self.ctx.theme.bg_deep,
                focus_rect,
                width=max(6, self._sp(6)),
                border_radius=self.ctx.theme.radius_m + 2,
            )
            pygame.draw.rect(
                surface,
                self.ctx.theme.accent_warm,
                focus_rect,
                width=max(3, self._sp(3)),
                border_radius=self.ctx.theme.radius_m + 2,
            )
        surface.set_clip(old_clip)

    def _draw_scrollbars(self, surface: pygame.Surface) -> None:
        theme = self.ctx.theme
        vr = self._view_rect
        bar_width = max(4, self._sp(4))
        bar_inset = max(2, self._sp(2))
        if self._content_h > vr.height:
            track_h = vr.height
            knob_h = min(
                track_h,
                max(self._sp(28), int(track_h * vr.height / self._content_h)),
            )
            travel = max(0, track_h - knob_h)
            limit = max(1, self._content_h - vr.height)
            knob_y = int(self._scroll_y / limit * travel)
            track = pygame.Rect(
                vr.right - bar_inset - bar_width,
                vr.y,
                bar_width,
                track_h,
            )
            pygame.draw.rect(
                surface,
                (*theme.border_soft[:3], 80),
                track,
                border_radius=max(2, bar_width // 2),
            )
            pygame.draw.rect(
                surface,
                (*theme.accent[:3], 180),
                (track.x, track.y + knob_y, bar_width, knob_h),
                border_radius=max(2, bar_width // 2),
            )
        if self._content_w > vr.width:
            track_w = vr.width
            knob_w = min(
                track_w,
                max(self._sp(28), int(track_w * vr.width / self._content_w)),
            )
            travel = max(0, track_w - knob_w)
            limit = max(1, self._content_w - vr.width)
            knob_x = int(self._scroll_x / limit * travel)
            track = pygame.Rect(
                vr.x,
                vr.bottom - bar_inset - bar_width,
                track_w,
                bar_width,
            )
            pygame.draw.rect(
                surface,
                (*theme.border_soft[:3], 80),
                track,
                border_radius=max(2, bar_width // 2),
            )
            pygame.draw.rect(
                surface,
                (*theme.accent[:3], 180),
                (track.x + knob_x, track.y, knob_w, bar_width),
                border_radius=max(2, bar_width // 2),
            )

    def describe(self) -> dict:
        manifest = self._manifest()
        chapters = manifest.ordered() if manifest is not None else []
        visible_indices = self._visible_hit_indices()
        visible_cards = [
            self._hit_chapter_ids[index]
            for index in visible_indices
        ]
        if self._focus_close:
            focus = {"target": "close"}
        elif 0 <= self._focused_hit < len(self._hits):
            focus = {
                "target": "chapter",
                "hit_index": self._focused_hit,
                "chapter_id": self._hit_chapter_ids[self._focused_hit],
                "scene_id": self._hits[self._focused_hit][1],
                "visible": self._focused_hit in visible_indices,
            }
        else:
            focus = {"target": "none"}
        return {
            "scene": "FlowchartScene",
            "replay_enabled": self.on_jump is not None,
            "interactive_cards": len(self._interactive_hits()),
            "focus": focus,
            "visible_cards": visible_cards,
            "scroll": {"x": self._scroll_x, "y": self._scroll_y},
            "scroll_limits": {
                "x": max(0, self._content_w - self._view_rect.width),
                "y": max(0, self._content_h - self._view_rect.height),
            },
            "viewport": {
                "x": self._view_rect.x,
                "y": self._view_rect.y,
                "width": self._view_rect.width,
                "height": self._view_rect.height,
            },
            "content_size": {
                "width": self._content_w,
                "height": self._content_h,
            },
            "chapters": [
                {
                    "id": c.id,
                    "route": c.route,
                    "act": c.act,
                    "read": self._chapter_read(c),
                    "current": self._is_current(c),
                    "replay_scene": self._replay_scene(c),
                }
                for c in chapters
            ],
        }
