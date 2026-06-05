"""In-game system menu overlay.

Following mainstream visual-novel convention, entries are split into
labelled groups rather than one flat list, and live settings controls do
NOT live here — "設定" opens the dedicated config screen. The body scrolls,
so the menu never overflows regardless of how many records/extras a pack
exposes.

- 紀錄 (records):  map, affection, clues, events, achievements, items, quests
- 鑑賞 (extras):   CG gallery, music room, endings, scene replay
- 系統 (system):   save, load, settings, back to title, quit

Keyboard shortcuts (M / A / L / T / I / S / J) keep working from the
exploration scene so power users don't have to open this menu every time.
"""
from __future__ import annotations

from typing import Callable

import pygame

from .base import Scene, SceneContext
from ..ui.widgets import Button, Panel


class MenuScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        self._scroll_y = 0
        self.on_close: Callable[[], None] | None = None
        self.on_map = None
        self.on_affection = None
        self.on_log = None
        self.on_achievements = None
        self.on_inventory = None
        self.on_cg_gallery = None
        self.on_music_room = None
        self.on_endings = None
        self.on_scene_replay = None
        self.on_flowchart = None
        self.on_character_profiles = None
        self.on_quest_log = None
        self.on_clues = None
        self.on_save = None
        self.on_load = None
        self.on_settings = None
        self.on_quit_to_title = None
        self.on_quit_app = None

    def enter(self, *, on_close=None, on_map=None, on_affection=None,
              on_log=None, on_achievements=None, on_inventory=None,
              on_cg_gallery=None, on_music_room=None, on_endings=None,
              on_scene_replay=None, on_flowchart=None,
              on_character_profiles=None, on_quest_log=None,
              on_clues=None, on_save=None, on_load=None, on_settings=None,
              on_quit_to_title=None, on_quit_app=None, **_) -> None:
        self.on_close = on_close
        self.on_map = on_map
        self.on_affection = on_affection
        self.on_log = on_log
        self.on_achievements = on_achievements
        self.on_inventory = on_inventory
        self.on_cg_gallery = on_cg_gallery
        self.on_music_room = on_music_room
        self.on_endings = on_endings
        self.on_scene_replay = on_scene_replay
        self.on_flowchart = on_flowchart
        self.on_character_profiles = on_character_profiles
        self.on_quest_log = on_quest_log
        self.on_clues = on_clues
        self.on_save = on_save
        self.on_load = on_load
        self.on_settings = on_settings
        self.on_quit_to_title = on_quit_to_title
        self.on_quit_app = on_quit_app
        self._build()

    def _build(self) -> None:
        theme = self.ctx.theme
        sw, sh = self.ctx.screen_size
        pw = min(860, sw - 100)
        ph = min(680, sh - 70)
        self._panel_rect = pygame.Rect(0, 0, pw, ph)
        self._panel_rect.center = (sw // 2, sh // 2)
        self._panel = Panel(self._panel_rect, theme,
                            fill=(*theme.bg_overlay[:3], 240),
                            border=theme.border_strong,
                            radius=theme.radius_l, border_width=2)
        self._header_h = 78
        self._body_rect = pygame.Rect(
            self._panel_rect.x + 30,
            self._panel_rect.y + self._header_h,
            self._panel_rect.width - 60,
            self._panel_rect.height - self._header_h - 22,
        )
        self.close_btn = Button(
            # Standard system-overlay close button (120x36, inset 16).
            pygame.Rect(self._panel_rect.right - 120 - 16,
                        self._panel_rect.y + 16, 120, 36),
            self.ctx.localization.t("close", "關閉 (Esc)"),
            fonts=self.ctx.fonts, theme=theme,
            font_size=15, style="ghost",
            on_click=(lambda: self.on_close() if self.on_close else None),
        )

        clue_unread = self.ctx.state.clues.unread_count()
        clue_label = "線索筆記 (J)"
        if clue_unread > 0:
            clue_label += f" · 新 {clue_unread}"

        groups: list[tuple[str, list[tuple]]] = [
            ("紀錄", [
                ("地圖 (M)", self.on_map, "primary"),
                ("好感 (A)", self.on_affection, "primary"),
                ("角色檔案", self.on_character_profiles, "primary"),
                (clue_label, self.on_clues, "primary"),
                ("事件 (L)", self.on_log, "primary"),
                ("成就 (T)", self.on_achievements, "primary"),
                ("物品 (I)", self.on_inventory, "primary"),
                ("任務記錄", self.on_quest_log, "primary"),
            ]),
            ("鑑賞", [
                (self.ctx.t("flowchart", "流程圖"), self.on_flowchart, "primary"),
                (self.ctx.t("cg_gallery", "CG鑑賞"), self.on_cg_gallery, "primary"),
                (self.ctx.t("music_room", "音樂室"), self.on_music_room, "primary"),
                (self.ctx.t("endings", "結局"), self.on_endings, "primary"),
                (self.ctx.t("scene_replay", "場景重溫"), self.on_scene_replay, "primary"),
            ]),
            ("系統", [
                ("存檔", self.on_save, "ghost"),
                ("載入存檔", self.on_load, "ghost"),
                (self.ctx.t("settings", "設定"), self.on_settings, "ghost"),
                ("回標題畫面", self.on_quit_to_title, "ghost"),
                ("離開遊戲", self.on_quit_app, "danger"),
            ]),
        ]

        self._buttons: list[tuple] = []   # (Button, cx, cy)
        self._focus: int = -1             # gamepad/keyboard focus; -1 = none
        self._labels: list[tuple] = []    # (text, size, bold, color, cx, cy)
        gap = 14
        col_w = (self._body_rect.width - gap) // 2
        bh = 54
        cy = 4
        for title, items in groups:
            cy += 8
            self._labels.append((title, 17, True, theme.accent_warm, 2, cy))
            cy += 34
            for i, (label, cb, style) in enumerate(items):
                col = i % 2
                row = i // 2
                cx = col * (col_w + gap)
                ry = cy + row * (bh + gap)
                b = Button(pygame.Rect(0, 0, col_w, bh), label,
                           fonts=self.ctx.fonts, theme=theme,
                           font_size=16, style=style, on_click=cb,
                           enabled=cb is not None)
                self._buttons.append((b, cx, ry))
            rows = (len(items) + 1) // 2
            cy += rows * (bh + gap) + 8
        self._content_height = cy

    # ----- scroll plumbing ----------------------------------------------------

    def _max_scroll(self) -> int:
        return max(0, self._content_height - self._body_rect.height)

    def _reposition(self) -> None:
        body = self._body_rect
        for b, cx, cy in self._buttons:
            b.rect.topleft = (body.x + cx, body.y + cy - self._scroll_y)

    def update(self, dt: float, inp) -> None:
        if inp.cancel and self.on_close:
            self.on_close()
            return
        if self._body_rect.collidepoint(inp.mouse_pos):
            self._scroll_y -= int(inp.mouse_wheel) * 44
        # Gamepad / keyboard focus: D-pad (inp.nav) walks the enabled buttons,
        # A/Enter (inp.confirm) fires the focused one. Mouse users never touch
        # _focus (stays -1), so the pointer path is unchanged.
        enabled = [i for i, (b, _, _) in enumerate(self._buttons) if b.enabled]
        if inp.nav and enabled:
            if self._focus not in enabled:
                self._focus = enabled[0] if inp.nav > 0 else enabled[-1]
            else:
                pos = (enabled.index(self._focus) + inp.nav) % len(enabled)
                self._focus = enabled[pos]
            # Scroll the focused row into view.
            _b, _cx, fcy = self._buttons[self._focus]
            bh = _b.rect.height
            vis = self._body_rect.height
            if fcy < self._scroll_y:
                self._scroll_y = fcy
            elif fcy + bh > self._scroll_y + vis:
                self._scroll_y = fcy + bh - vis
        if inp.confirm and 0 <= self._focus < len(self._buttons):
            b = self._buttons[self._focus][0]
            if b.enabled and b.on_click:
                b.on_click()
                return
        self._scroll_y = max(0, min(self._scroll_y, self._max_scroll()))
        self._reposition()
        self.close_btn.update(dt, inp)
        body = self._body_rect
        for i, (b, _cx, _cy) in enumerate(self._buttons):
            if body.y <= b.rect.y and b.rect.bottom <= body.bottom:
                b.update(dt, inp)
            else:
                b._hover = False
            if i == self._focus:
                b._hover = True      # focus highlight (overrides mouse)

    def draw(self, surface: pygame.Surface) -> None:
        theme = self.ctx.theme
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 236))
        surface.blit(veil, (0, 0))
        self._panel.draw(surface)
        title = self.ctx.fonts.render("選單", self.ctx.config.font_size_header,
                                      theme.accent, bold=True)
        surface.blit(title, (self._panel_rect.x + 32, self._panel_rect.y + 24))
        self.close_btn.draw(surface)
        pygame.draw.line(
            surface, theme.border_soft,
            (self._panel_rect.x + 24, self._panel_rect.y + self._header_h - 10),
            (self._panel_rect.right - 24, self._panel_rect.y + self._header_h - 10),
        )

        self._reposition()
        body = self._body_rect
        prev_clip = surface.get_clip()
        surface.set_clip(body)
        oy = body.y - self._scroll_y
        for text, size, bold, color, cx, cy in self._labels:
            surface.blit(self.ctx.fonts.render(text, size, color, bold=bold),
                         (body.x + cx, oy + cy))
        for b, _cx, _cy in self._buttons:
            if b.rect.bottom >= body.y and b.rect.y <= body.bottom:
                b.draw(surface)
        surface.set_clip(prev_clip)

        max_scroll = self._max_scroll()
        if max_scroll > 0:
            track_h = body.height
            knob_h = max(32, int(track_h * body.height / self._content_height))
            knob_y = int(self._scroll_y / max_scroll * (track_h - knob_h))
            x = self._panel_rect.right - 14
            pygame.draw.rect(surface, (*theme.border_soft[:3], 70),
                             (x, body.y, 4, track_h), border_radius=2)
            pygame.draw.rect(surface, (*theme.accent[:3], 190),
                             (x, body.y + knob_y, 4, knob_h), border_radius=2)

    def describe(self) -> dict:
        return {"scene": "MenuScene",
                "content_height": getattr(self, "_content_height", 0),
                "scroll_y": self._scroll_y}
