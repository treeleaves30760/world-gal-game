"""Flowchart / chapter-chart overlay (the Steins;Gate チャート).

Renders the pack's declared chapter/act/route structure
(``content/chapters.yaml`` → ``ChapterManifest`` on ``state.meta["__chapters__"]``)
as a branching chart: each route is a column, chapters cascade down by their
``order``, and connectors link a route's chapters and the branch from the common
route. Chapters the player has read are drawn bright in the route's colour and
are clickable to jump to (replay) that scene; unread chapters are dimmed.

Read-state comes from ``state.read_log.scenes``; jumping calls the ``on_jump``
callback (wired to the app's ``_start_dialogue``). A pack with no chapter
manifest shows a friendly empty state, so this is safe for any pack.
"""
from __future__ import annotations

import pygame

from .base import Scene, SceneContext
from ..ui.widgets import Button, Panel, ScrollArea


_COMMON_ROUTES = ("common", "", "main")


class FlowchartScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        # Per-chapter click targets in content space: (rect, scene_id).
        self._hits: list[tuple[pygame.Rect, str]] = []

    # ---- data ----------------------------------------------------------
    def _manifest(self):
        return self.ctx.state.meta.get("__chapters__")

    def _route_style(self) -> dict[str, tuple[str, tuple]]:
        """route_id -> (label, rgb colour). Heroine routes borrow the heroine's
        name colour so a column reads as 'her' path; common/other fall back."""
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

    def _routes_in_order(self, chapters) -> list[str]:
        """Column order: common first, then routes by first appearance."""
        order: list[str] = []
        for c in chapters:
            r = c.route or "common"
            r = "common" if r in _COMMON_ROUTES else r
            if r not in order:
                order.append(r)
        if "common" in order:
            order.remove("common")
            order.insert(0, "common")
        return order

    @staticmethod
    def _norm_route(route: str) -> str:
        return "common" if (route or "common") in _COMMON_ROUTES else route

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
        self._panel_rect = pygame.Rect(90, 50, sw - 180, sh - 100)
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
        self._scroll = ScrollArea(
            pygame.Rect(self._panel_rect.x + 24, self._panel_rect.y + 78,
                        self._panel_rect.width - 48,
                        self._panel_rect.height - 78 - 28),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
        )
        self._scroll.set_drawer(self._draw_content)

    # ---- layout / rendering -------------------------------------------
    _ROW_H = 88
    _CARD_H = 64

    def _draw_content(self, surface: pygame.Surface) -> int:
        theme = self.ctx.theme
        self._hits = []
        manifest = self._manifest()
        chapters = manifest.ordered() if manifest is not None else []
        if not chapters:
            msg = self.ctx.fonts.render(
                "本作沒有章節資料（content/chapters.yaml）。", 18,
                theme.text_mute)
            surface.blit(msg, (4, 8))
            return msg.get_height() + 16

        styles = self._route_style()
        routes = self._routes_in_order(chapters)
        width = surface.get_width()
        col_w = max(160, width // max(1, len(routes)))
        col_center = {r: int((i + 0.5) * col_w) for i, r in enumerate(routes)}
        card_w = min(col_w - 24, 280)

        # Pre-compute each chapter's centre point (content space).
        pts: dict[str, tuple[int, int]] = {}
        last_common_pt: tuple[int, int] | None = None
        prev_in_route: dict[str, str] = {}
        y = 14
        # First pass: positions + connectors (drawn first, under the cards).
        positions: list[tuple] = []
        for ch in chapters:
            r = self._norm_route(ch.route)
            cx = col_center.get(r, width // 2)
            cy = y + self._CARD_H // 2
            pts[ch.id] = (cx, cy)
            positions.append((ch, r, cx, y))
            y += self._ROW_H

        # Connectors: within-route chains + branch from common. Orthogonal
        # ("elbow") routing reads far cleaner than diagonals when a route
        # branches to a column further right.
        for ch, r, cx, top in positions:
            cur = pts[ch.id]
            prev_id = prev_in_route.get(r)
            color = styles.get(r, ("", tuple(theme.accent[:3])))[1]
            read = self._chapter_read(ch)
            line_col = (*color, 160 if read else 48)
            top_y = cur[1] - self._CARD_H // 2
            if prev_id is not None:
                px, py = pts[prev_id]
                by = py + self._CARD_H // 2
                if px == cur[0]:
                    pygame.draw.line(surface, line_col, (px, by),
                                     (cur[0], top_y), 3)
                else:
                    midy = (by + top_y) // 2
                    pygame.draw.lines(surface, line_col, False,
                                      [(px, by), (px, midy),
                                       (cur[0], midy), (cur[0], top_y)], 3)
            elif r != "common" and last_common_pt is not None:
                lx, ly = last_common_pt
                midy = (ly + top_y) // 2
                pygame.draw.lines(surface, line_col, False,
                                  [(lx, ly), (lx, midy),
                                   (cur[0], midy), (cur[0], top_y)], 3)
            prev_in_route[r] = ch.id
            if r == "common":
                last_common_pt = (cur[0], cur[1] + self._CARD_H // 2)

        # Second pass: the chapter cards on top of the connectors.
        for ch, r, cx, top in positions:
            label, color = styles.get(r, (r, tuple(theme.accent[:3])))
            read = self._chapter_read(ch)
            x = cx - card_w // 2
            card = pygame.Surface((card_w, self._CARD_H), pygame.SRCALPHA)
            rad = theme.radius_m
            bg = self._chapter_bg(ch) if read else None
            if bg:
                # A darkened, round-cornered thumbnail of the chapter's scene as
                # the card base — a "scene select" look. Corners rounded by
                # multiplying the thumbnail's alpha through a rounded mask.
                thumb = self.ctx.assets.scaled(
                    bg, (card_w, self._CARD_H), fit="cover").copy()
                mask = pygame.Surface((card_w, self._CARD_H), pygame.SRCALPHA)
                pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(),
                                 border_radius=rad)
                thumb.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
                card.blit(thumb, (0, 0))
                dark = pygame.Surface((card_w, self._CARD_H), pygame.SRCALPHA)
                pygame.draw.rect(dark, (10, 8, 16, 140), dark.get_rect(),
                                 border_radius=rad)
                card.blit(dark, (0, 0))            # keep the title legible
            else:
                fill = (*color, 70) if read else (*theme.text_dim[:3], 26)
                pygame.draw.rect(card, fill, card.get_rect(),
                                 border_radius=rad)
            here = self._is_current(ch)
            if here:
                # "You are here": a brighter, thicker accent border so the
                # current chapter stands out from merely-read ones.
                pygame.draw.rect(card, (*theme.accent[:3], 255),
                                 card.get_rect(), width=3, border_radius=rad)
            else:
                pygame.draw.rect(card, (*color, 235) if read
                                 else (*theme.text_dim[:3], 90),
                                 card.get_rect(), width=2, border_radius=rad)
            title = ch.title or ch.id
            t_col = theme.text if read else theme.text_dim
            # Truncate to fit the card, leaving room for the read/unread sigil.
            maxw = card_w - 46
            tsurf = self.ctx.fonts.render(title, 18, t_col, bold=True)
            if tsurf.get_width() > maxw:
                while len(title) > 1 and self.ctx.fonts.render(
                        title + "…", 18, t_col, bold=True).get_width() > maxw:
                    title = title[:-1]
                tsurf = self.ctx.fonts.render(title + "…", 18, t_col, bold=True)
            card.blit(tsurf, (12, 9))
            sub = f"{label}" + ("　·　終" if ch.endings else "")
            if here:
                sub = "現在地　·　" + sub
            ssurf = self.ctx.fonts.render(
                sub[:20], 13,
                theme.accent if here
                else (theme.text_mute if read else theme.text_dim))
            card.blit(ssurf, (12, self._CARD_H - 22))
            # read / unread sigil: a drawn dot (filled = read, hollow = unread)
            # rather than a glyph, so a CJK font without ✓ shows no tofu.
            sx, sy = card_w - 20, 18
            if read:
                pygame.draw.circle(card, (*color, 255), (sx, sy), 7)
            else:
                pygame.draw.circle(card, (*theme.text_dim[:3], 200),
                                   (sx, sy), 7, 2)
            surface.blit(card, (x, top))
            if read:
                self._hits.append((pygame.Rect(x, top, card_w, self._CARD_H),
                                   self._jump_scene(ch) or ""))

        return y + 14

    # ---- input ---------------------------------------------------------
    def update(self, dt: float, inp) -> None:
        if inp.cancel and self.on_close:
            self.on_close()
            return
        self.close_btn.update(dt, inp)
        self._scroll.update(dt, inp)
        # Click a read chapter card to jump to (replay) that scene.
        if (inp.mouse_clicked and self.on_jump and self._hits
                and self._scroll.rect.collidepoint(inp.mouse_pos)):
            cx = inp.mouse_pos[0] - self._scroll.rect.x
            cy = inp.mouse_pos[1] - self._scroll.rect.y + self._scroll.scroll_y
            for rect, scene_id in self._hits:
                if scene_id and rect.collidepoint(cx, cy):
                    self.on_jump(scene_id)
                    return

    def draw(self, surface: pygame.Surface) -> None:
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 214))
        surface.blit(veil, (0, 0))
        self._panel.draw(surface)
        title = self.ctx.fonts.render(
            self.ctx.localization.t("flowchart", "流程圖"),
            self.ctx.config.font_size_header,
            self.ctx.theme.accent, bold=True)
        surface.blit(title, (self._panel_rect.x + 32, self._panel_rect.y + 26))
        hint = self.ctx.fonts.render(
            "點亮的章節可點擊跳轉", 14, self.ctx.theme.text_mute)
        surface.blit(hint, (self._panel_rect.right - hint.get_width() - 160,
                            self._panel_rect.y + 32))
        self.close_btn.draw(surface)
        self._scroll.draw(surface)

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
