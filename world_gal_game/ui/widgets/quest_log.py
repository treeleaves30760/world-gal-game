"""Quest log widget: left-column list + right-column detail panel."""
from __future__ import annotations

import pygame

from .base import Widget
from .scrollable import ScrollArea
from ..fonts import FontRegistry
from ..theme import Theme
from ...core.quest import Quest, QuestTracker


class QuestLog(Widget):
    """Two-pane quest log.

    Left column: scrollable list of active/completed quests.
    Right column: detail view for the selected quest.
    """

    LIST_WIDTH_RATIO = 0.38   # fraction of total widget width

    def __init__(self, rect: pygame.Rect, *,
                 fonts: FontRegistry, theme: Theme):
        super().__init__(rect)
        self.fonts = fonts
        self.theme = theme
        self._selected_id: str | None = None

        split = int(rect.width * self.LIST_WIDTH_RATIO)
        gap = 8
        list_rect = pygame.Rect(rect.x, rect.y, split, rect.height)
        detail_rect = pygame.Rect(rect.x + split + gap, rect.y,
                                  rect.width - split - gap, rect.height)

        self._list_scroll = ScrollArea(list_rect, fonts=fonts, theme=theme)
        self._list_scroll.set_drawer(self._draw_list)

        self._detail_scroll = ScrollArea(detail_rect, fonts=fonts, theme=theme)
        self._detail_scroll.set_drawer(self._draw_detail)

        self._tracker: QuestTracker | None = None

    def bind(self, tracker: QuestTracker) -> None:
        self._tracker = tracker
        # Select the first active quest by default.
        if self._selected_id is None and tracker.active():
            self._selected_id = tracker.active()[0].id

    # ------------------------------------------------------------------
    # Internal draw helpers

    def _visible_quests(self) -> list[Quest]:
        if self._tracker is None:
            return []
        out: list[Quest] = []
        for q in self._tracker.quests.values():
            if q.status == "inactive" and q.hidden:
                continue
            if q.status != "inactive":
                out.append(q)
        # active first, then completed, then failed
        order = {"active": 0, "completed": 1, "failed": 2, "inactive": 3}
        out.sort(key=lambda q: (order.get(q.status, 9), q.title))
        return out

    def _draw_list(self, surface: pygame.Surface) -> int:
        quests = self._visible_quests()
        y = 0
        row_h = 54
        pad = 8
        w = surface.get_width() - 12   # leave room for scrollbar

        for q in quests:
            selected = q.id == self._selected_id
            status_color = {
                "active":    self.theme.accent,
                "completed": self.theme.accent_warm,
                "failed":    self.theme.text_dim,
            }.get(q.status, self.theme.text_mute)

            bg_alpha = 80 if selected else 30
            bg_color = (*status_color[:3], bg_alpha)
            rect = pygame.Rect(0, y, w, row_h)
            pygame.draw.rect(surface, bg_color, rect,
                             border_radius=self.theme.radius_s)
            if selected:
                pygame.draw.rect(surface, (*status_color[:3], 200),
                                 rect, width=2,
                                 border_radius=self.theme.radius_s)

            # Status dot
            dot_x, dot_y = pad + 6, y + row_h // 2
            pygame.draw.circle(surface, status_color, (dot_x, dot_y), 5)

            title_surf = self.fonts.render(
                q.title, 17,
                self.theme.text if selected else self.theme.text_mute,
                bold=selected,
            )
            surface.blit(title_surf, (pad + 18, y + 8))

            status_label = {"active": "進行中",
                            "completed": "完成",
                            "failed": "失敗"}.get(q.status, "")
            sl = self.fonts.render(status_label, 13, status_color)
            surface.blit(sl, (pad + 18, y + 30))

            y += row_h + 4

        if not quests:
            empty = self.fonts.render("（沒有進行中的任務）", 16,
                                      self.theme.text_mute)
            surface.blit(empty, (pad, 8))
            y = empty.get_height() + 16

        return y

    def _draw_detail(self, surface: pygame.Surface) -> int:
        if self._tracker is None or self._selected_id is None:
            hint = self.fonts.render("← 選擇左側任務", 16, self.theme.text_mute)
            surface.blit(hint, (0, 0))
            return hint.get_height()

        q = self._tracker.quests.get(self._selected_id)
        if q is None:
            return 0

        w = surface.get_width() - 16
        y = 0
        line_gap = 6

        # Title
        title_surf = self.fonts.render(q.title, 22, self.theme.accent, bold=True)
        surface.blit(title_surf, (0, y))
        y += title_surf.get_height() + line_gap

        # Status badge
        status_color = {
            "active":    self.theme.accent,
            "completed": self.theme.accent_warm,
            "failed":    self.theme.text_dim,
        }.get(q.status, self.theme.text_mute)
        badge = self.fonts.render(
            {"active": "進行中", "completed": "已完成",
             "failed": "失敗", "inactive": "未啟動"}.get(q.status, ""),
            14, status_color, bold=True,
        )
        surface.blit(badge, (0, y))
        y += badge.get_height() + line_gap * 2

        # Description
        if q.description:
            desc = self.fonts.render(q.description, 15, self.theme.text_mute)
            surface.blit(desc, (0, y))
            y += desc.get_height() + line_gap * 2

        # Giver
        if q.giver:
            giver_surf = self.fonts.render(f"任務來源：{q.giver}", 14,
                                           self.theme.text_dim)
            surface.blit(giver_surf, (0, y))
            y += giver_surf.get_height() + line_gap * 2

        # Divider
        pygame.draw.line(surface, (*self.theme.border_soft[:3], 120),
                         (0, y), (w, y))
        y += line_gap + 4

        # Objectives
        if q.objectives:
            sec = self.fonts.render("目標", 15, self.theme.text, bold=True)
            surface.blit(sec, (0, y))
            y += sec.get_height() + line_gap

            for obj in q.objectives:
                # Hidden incomplete objectives stay invisible.
                if obj.hidden and not obj.completed:
                    continue
                color = (self.theme.accent_warm if obj.completed
                         else self.theme.text_mute)
                prefix = "[完成] " if obj.completed else "[ ]  "
                optional_tag = "（選擇性）" if obj.optional else ""
                line = self.fonts.render(
                    f"{prefix}{obj.text}{optional_tag}", 15, color,
                )
                surface.blit(line, (8, y))
                y += line.get_height() + 4

            y += line_gap

        # Rewards
        if q.rewards_text and q.status == "completed":
            pygame.draw.line(surface, (*self.theme.border_soft[:3], 120),
                             (0, y), (w, y))
            y += line_gap + 4
            rew_label = self.fonts.render("獎勵", 15, self.theme.text, bold=True)
            surface.blit(rew_label, (0, y))
            y += rew_label.get_height() + line_gap
            rew = self.fonts.render(q.rewards_text, 15, self.theme.accent_warm)
            surface.blit(rew, (8, y))
            y += rew.get_height() + line_gap

        return y

    # ------------------------------------------------------------------
    # Hit-testing for list selection

    def _list_hit_test(self, mouse_pos: tuple[int, int]) -> str | None:
        """Return the quest id at the given mouse position, or None."""
        list_rect = self._list_scroll.rect
        if not list_rect.collidepoint(mouse_pos):
            return None
        quests = self._visible_quests()
        row_h = 54 + 4
        rel_y = mouse_pos[1] - list_rect.y + self._list_scroll.scroll_y
        idx = int(rel_y // row_h)
        if 0 <= idx < len(quests):
            return quests[idx].id
        return None

    # ------------------------------------------------------------------
    # Lifecycle

    def update(self, dt: float, inp) -> None:
        # Check for list click before passing to scroll areas.
        if inp.click_pos is not None:
            hit = self._list_hit_test(inp.click_pos)
            if hit is not None:
                self._selected_id = hit
                self._detail_scroll.scroll_y = 0

        self._list_scroll.update(dt, inp)
        self._detail_scroll.update(dt, inp)

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        # Thin separator between the two panes.
        mid_x = self._detail_scroll.rect.x - 4
        pygame.draw.line(
            surface,
            (*self.theme.border_soft[:3], 120),
            (mid_x, self.rect.y),
            (mid_x, self.rect.bottom),
        )
        self._list_scroll.draw(surface)
        self._detail_scroll.draw(surface)
