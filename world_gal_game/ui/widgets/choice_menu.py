"""Choice menu shown at a decision point in a scene."""
from __future__ import annotations

from typing import Callable

import pygame

from .base import Widget
from .button import Button
from .panel import Panel
from ..fonts import FontRegistry
from ..theme import Theme


class ChoiceMenu(Widget):
    def __init__(self, rect: pygame.Rect, *, fonts: FontRegistry, theme: Theme,
                 on_choose: Callable[[str], None]):
        super().__init__(rect)
        self.fonts = fonts
        self.theme = theme
        self.on_choose = on_choose
        self.buttons: list[Button] = []
        self.panel: Panel | None = None
        self._prompt_pos = (0, 0)
        # Parallel to self.buttons: the lock-reason string for each row ("" when
        # the choice is enabled) and where to draw its caption (None = no
        # caption). Populated by set_choices.
        self._reasons: list[str] = []
        self._reason_anchors: list[tuple[int, int] | None] = []

    def set_choices(self, choices: list[tuple]) -> None:
        """Set the choice rows.

        Each row is ``(choice_id, label, enabled)`` or, to show *why* a locked
        choice is locked, ``(choice_id, label, enabled, reason)`` — a concise
        phrase (e.g. "需要 與林青衣的好感度 ≥ 40") rendered under the greyed
        button. A 3-tuple (no reason) is accepted unchanged, so callers that
        don't supply reasons are byte-compatible.
        """
        self.buttons = []
        # Normalise to (cid, label, enabled, reason); a locked row with a reason
        # reserves extra height for its caption line so rows never overlap.
        rows: list[tuple[str, str, bool, str]] = []
        for row in choices:
            cid, label, enabled = row[0], row[1], row[2]
            reason = row[3] if len(row) > 3 else ""
            rows.append((cid, label, bool(enabled), reason or ""))
        self._reasons = [r[3] for r in rows]

        n = max(1, len(rows))
        btn_w = min(760, self.rect.width - 120)
        btn_h = 60
        gap = 14
        reason_h = 22   # extra space under a locked button for its reason line
        title_h = 50
        # Per-row vertical extent (button + optional reason caption).
        row_heights = [btn_h + (reason_h if r[3] else 0) for r in rows] or [btn_h]
        total_h = title_h + sum(row_heights) + (n - 1) * gap + 36
        panel_rect = pygame.Rect(0, 0, btn_w + 100, total_h)
        panel_rect.center = self.rect.center
        self.panel = Panel(panel_rect, self.theme,
                           fill=(*self.theme.bg_overlay[:3], 240),
                           border=self.theme.border_strong,
                           radius=self.theme.radius_l, border_width=2)
        self._prompt_pos = (panel_rect.centerx, panel_rect.y + 26)
        # Reason captions are positioned in draw(); remember their anchors here.
        self._reason_anchors: list[tuple[int, int] | None] = []
        y = panel_rect.y + title_h
        for i, (cid, label, enabled, reason) in enumerate(rows):
            r = pygame.Rect(panel_rect.centerx - btn_w // 2, y, btn_w, btn_h)
            b = Button(r, label, fonts=self.fonts, theme=self.theme,
                       font_size=18,
                       on_click=(lambda cid=cid: self.on_choose(cid))
                                if enabled else None,
                       enabled=enabled,
                       style="primary" if enabled else "ghost")
            self.buttons.append(b)
            if reason:
                self._reason_anchors.append((r.centerx, r.bottom + 3))
                y += btn_h + reason_h + gap
            else:
                self._reason_anchors.append(None)
                y += btn_h + gap

    def update(self, dt: float, inp) -> None:
        for b in self.buttons:
            b.update(dt, inp)

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        # darken background to focus the decision (softer now that the stale
        # textbox is hidden under choices — a CG/scene behind it still reads)
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 130))
        surface.blit(veil, (0, 0))
        if self.panel:
            self.panel.draw(surface)
            prompt = self.fonts.render("請選擇", 18,
                                       self.theme.accent_warm, bold=True)
            cx, py = self._prompt_pos
            surface.blit(prompt, (cx - prompt.get_width() // 2, py))
        for b in self.buttons:
            b.draw(surface)
        # Lock reasons: a small muted caption centred under each locked button,
        # so the player reads WHY a choice is unavailable (and how close they
        # are) instead of seeing a silent greyed row.
        for i, anchor in enumerate(self._reason_anchors):
            if anchor is None:
                continue
            reason = self._reasons[i] if i < len(self._reasons) else ""
            if not reason:
                continue
            cap = self.fonts.render(reason, 14, self.theme.text_mute)
            cx, cy = anchor
            surface.blit(cap, (cx - cap.get_width() // 2, cy))
