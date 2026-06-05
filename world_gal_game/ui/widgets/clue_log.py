"""Clue / journal widget.

Two-pane viewer for the player's clue journal:
- Left column: scrollable list of seen clues, grouped by status (active
  first, then resolved). Selecting one shows its body on the right.
- Right column: detail body of the selected clue.

Unread clues (just unlocked, not yet viewed) get a small badge dot in
the list and are marked read when the player selects them.
"""
from __future__ import annotations

import pygame

from .base import Widget
from .scrollable import ScrollArea
from ..fonts import FontRegistry
from ..theme import Theme
from ...core.clue import Clue, ClueTracker


class ClueLog(Widget):
    LIST_WIDTH_RATIO = 0.42

    def __init__(self, rect: pygame.Rect, *,
                 fonts: FontRegistry, theme: Theme):
        super().__init__(rect)
        self.fonts = fonts
        self.theme = theme
        self._selected_id: str | None = None
        self._tracker: ClueTracker | None = None
        self._state = None   # GameState — needed to evaluate live status

        split = int(rect.width * self.LIST_WIDTH_RATIO)
        gap = 8
        list_rect = pygame.Rect(rect.x, rect.y, split, rect.height)
        detail_rect = pygame.Rect(rect.x + split + gap, rect.y,
                                  rect.width - split - gap, rect.height)
        self._list_scroll = ScrollArea(list_rect, fonts=fonts, theme=theme)
        self._list_scroll.set_drawer(self._draw_list)
        self._detail_scroll = ScrollArea(detail_rect, fonts=fonts, theme=theme)
        self._detail_scroll.set_drawer(self._draw_detail)

    def bind(self, tracker: ClueTracker, state) -> None:
        self._tracker = tracker
        self._state = state
        # Auto-select the first active clue so the journal doesn't open
        # to an empty detail pane.
        entries = self._entries()
        if entries and self._selected_id is None:
            self._selected_id = entries[0][0].id
            tracker.mark_read(entries[0][0].id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _entries(self) -> list[tuple[Clue, str]]:
        if self._tracker is None or self._state is None:
            return []
        return self._tracker.journal(self._state)

    _ROW_H = 60
    _ROW_GAP = 4
    _PAD = 8

    def _draw_list(self, surface: pygame.Surface) -> int:
        entries = self._entries()
        y = 0
        w = surface.get_width() - 12   # leave room for scrollbar
        last_cat: str | None = None
        last_status: str | None = None

        for clue, status in entries:
            # Status section header (active / resolved)
            if status != last_status:
                hdr_text = "目前線索" if status == "active" else "已解開"
                hdr_color = (self.theme.accent if status == "active"
                             else self.theme.text_dim)
                hdr = self.fonts.render(hdr_text, 14, hdr_color, bold=True)
                surface.blit(hdr, (self._PAD, y + 6))
                y += hdr.get_height() + 6
                last_status = status
                last_cat = None
            # Category sub-header
            if clue.category and clue.category != last_cat:
                cat = self.fonts.render(f"· {clue.category}", 13,
                                        self.theme.text_mute)
                surface.blit(cat, (self._PAD + 8, y + 4))
                y += cat.get_height() + 4
                last_cat = clue.category

            selected = clue.id == self._selected_id
            unread = (self._tracker is not None
                      and clue.id in self._tracker.unread)
            is_record = getattr(clue, "record", False) and status == "active"
            base_color = (self.theme.accent_warm if is_record
                          else self.theme.accent if status == "active"
                          else self.theme.text_dim)
            bg_alpha = 80 if selected else 24
            row_rect = pygame.Rect(self._PAD, y, w - self._PAD,
                                   self._ROW_H)
            pygame.draw.rect(surface, (*base_color[:3], bg_alpha),
                             row_rect, border_radius=self.theme.radius_s)
            if selected:
                pygame.draw.rect(surface, (*base_color[:3], 200),
                                 row_rect, width=2,
                                 border_radius=self.theme.radius_s)

            # Title
            title_color = (self.theme.text if status == "active"
                           else self.theme.text_dim)
            title_surf = self.fonts.render(clue.title, 17, title_color,
                                           bold=selected)
            surface.blit(title_surf,
                         (row_rect.x + 12, row_rect.y + 8))

            # Status pill
            pill_text = ("已收錄" if is_record
                         else "進行中" if status == "active"
                         else "已解開")
            pill = self.fonts.render(pill_text, 12, base_color)
            surface.blit(pill, (row_rect.x + 12, row_rect.y + 32))

            # Unread badge
            if unread:
                badge_x = row_rect.right - 18
                badge_y = row_rect.y + 14
                pygame.draw.circle(surface, self.theme.accent_warm,
                                   (badge_x, badge_y), 5)

            y += self._ROW_H + self._ROW_GAP

        if not entries:
            empty = self.fonts.render(
                "（目前還沒有任何線索 — 多走走看看吧。）",
                15, self.theme.text_mute,
            )
            surface.blit(empty, (self._PAD, 8))
            y = empty.get_height() + 16
        return y

    def _draw_detail(self, surface: pygame.Surface) -> int:
        if self._tracker is None or self._selected_id is None:
            hint = self.fonts.render(
                "← 從左側挑一條線索看",
                16, self.theme.text_mute,
            )
            surface.blit(hint, (0, 0))
            return hint.get_height()

        clue = self._tracker.get(self._selected_id)
        if clue is None:
            return 0

        w = surface.get_width() - 16
        y = 0
        gap = 6

        title = self.fonts.render(clue.title, 22, self.theme.accent,
                                  bold=True)
        surface.blit(title, (0, y))
        y += title.get_height() + gap

        if clue.category:
            cat = self.fonts.render(f"分類：{clue.category}", 13,
                                    self.theme.text_mute)
            surface.blit(cat, (0, y))
            y += cat.get_height() + gap

        # Status badge
        is_active = (self._state is not None
                     and self._tracker.is_active(clue, self._state))
        badge_text = "進行中" if is_active else "已解開 — 暫時沒有下一步"
        badge_color = (self.theme.accent if is_active
                       else self.theme.text_dim)
        badge = self.fonts.render(badge_text, 14, badge_color, bold=True)
        surface.blit(badge, (0, y))
        y += badge.get_height() + gap * 2

        # Divider
        pygame.draw.line(surface, (*self.theme.border_soft[:3], 120),
                         (0, y), (w, y))
        y += gap

        # Body. Wrap manually since the body may be multi-line.
        from .label import WrappedText
        body_rect = pygame.Rect(0, y, w, max(40, surface.get_height() - y))
        body = WrappedText(body_rect, clue.text or "(沒有內文)",
                           fonts=self.fonts, size=16,
                           color=self.theme.text)
        body.draw(surface)
        # Estimate height (WrappedText doesn't expose used_height publicly)
        # — we just return enough so scrollarea sizes correctly.
        line_h = 22
        lines = max(1, (clue.text or "").count("\n") + 1)
        y += line_h * (lines + 2)
        return y

    # ------------------------------------------------------------------
    # Hit-testing for clicks on the list
    # ------------------------------------------------------------------

    def _list_hit_test(self, mouse_pos: tuple[int, int]) -> str | None:
        list_rect = self._list_scroll.rect
        if not list_rect.collidepoint(mouse_pos):
            return None
        entries = self._entries()
        # We have to walk the same y-layout the draw uses since the list
        # contains variable-height status / category headers.
        y = -self._list_scroll.scroll_y
        last_cat: str | None = None
        last_status: str | None = None
        for clue, status in entries:
            if status != last_status:
                hdr_h = 14 + 6 + 6   # font height + padding
                y += hdr_h
                last_status = status
                last_cat = None
            if clue.category and clue.category != last_cat:
                y += 13 + 4 + 4
                last_cat = clue.category
            row_top = list_rect.y + y
            row_bot = row_top + self._ROW_H
            if row_top <= mouse_pos[1] <= row_bot:
                return clue.id
            y += self._ROW_H + self._ROW_GAP
        return None

    # ------------------------------------------------------------------

    def update(self, dt: float, inp) -> None:
        if inp.mouse_clicked:
            hit = self._list_hit_test(inp.mouse_pos)
            if hit is not None:
                self._selected_id = hit
                self._detail_scroll.scroll_y = 0
                if self._tracker is not None:
                    self._tracker.mark_read(hit)
        self._list_scroll.update(dt, inp)
        self._detail_scroll.update(dt, inp)

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        mid_x = self._detail_scroll.rect.x - 4
        pygame.draw.line(
            surface,
            (*self.theme.border_soft[:3], 120),
            (mid_x, self.rect.y),
            (mid_x, self.rect.bottom),
        )
        self._list_scroll.draw(surface)
        self._detail_scroll.draw(surface)
