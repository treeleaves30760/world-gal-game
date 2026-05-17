"""In-game main menu overlay.

The exploration top bar used to carry seven action buttons + a time
label. That was both visually cramped and overlapped the time text. This
overlay is the consolidated home for everything that isn't day-to-day
gameplay:

- View screens:        map, affection, event log, achievements, inventory
- Save / load:         opens the existing SaveScene
- Game settings:       text speed, BGM volume, fullscreen toggle
- Exit:                back to title, quit to desktop

Keyboard shortcuts (M / A / L / T / I / S) keep working from the
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
        self.on_close: Callable[[], None] | None = None
        self.on_map: Callable[[], None] | None = None
        self.on_affection: Callable[[], None] | None = None
        self.on_log: Callable[[], None] | None = None
        self.on_achievements: Callable[[], None] | None = None
        self.on_inventory: Callable[[], None] | None = None
        self.on_quest_log: Callable[[], None] | None = None
        self.on_save: Callable[[], None] | None = None
        self.on_load: Callable[[], None] | None = None
        self.on_quit_to_title: Callable[[], None] | None = None
        self.on_quit_app: Callable[[], None] | None = None
        self._show_kb_hint = False

    def enter(self, *, on_close=None, on_map=None, on_affection=None,
              on_log=None, on_achievements=None, on_inventory=None,
              on_quest_log=None,
              on_save=None, on_load=None,
              on_quit_to_title=None, on_quit_app=None, **_) -> None:
        self.on_close = on_close
        self.on_map = on_map
        self.on_affection = on_affection
        self.on_log = on_log
        self.on_achievements = on_achievements
        self.on_inventory = on_inventory
        self.on_quest_log = on_quest_log
        self.on_save = on_save
        self.on_load = on_load
        self.on_quit_to_title = on_quit_to_title
        self.on_quit_app = on_quit_app
        self._build()

    def _build(self) -> None:
        sw, sh = self.ctx.screen_size
        panel_w = min(760, sw - 120)
        panel_h = min(560, sh - 80)
        self._panel_rect = pygame.Rect(0, 0, panel_w, panel_h)
        self._panel_rect.center = (sw // 2, sh // 2)
        self._panel = Panel(self._panel_rect, self.ctx.theme,
                            fill=(*self.ctx.theme.bg_overlay[:3], 240),
                            border=self.ctx.theme.border_strong,
                            radius=self.ctx.theme.radius_l, border_width=2)
        self.close_btn = Button(
            pygame.Rect(self._panel_rect.right - 120 - 16,
                        self._panel_rect.y + 16, 120, 36),
            self.ctx.localization.t("close", "關閉 (Esc)"),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=15, style="ghost",
            on_click=(lambda: self.on_close() if self.on_close else None),
        )

        # Each row holds two buttons of equal width.
        col_w = (panel_w - 80) // 2
        col_h = 52
        gap = 12
        # Two-column grid of action buttons.
        actions: list[tuple[str, Callable[[], None] | None, str]] = [
            ("地圖 (M)",       self.on_map,          "view"),
            ("好感 (A)",       self.on_affection,    "view"),
            ("事件 (L)",       self.on_log,          "view"),
            ("成就 (T)",       self.on_achievements, "view"),
            ("物品 (I)",       self.on_inventory,    "view"),
            ("任務記錄",       self.on_quest_log,    "view"),
            ("存檔",           self.on_save,         "save"),
            ("載入存檔",       self.on_load,         "save"),
            ("回標題畫面",     self.on_quit_to_title,"exit"),
        ]
        start_x = self._panel_rect.x + 40
        start_y = self._panel_rect.y + 80
        self._action_buttons: list[Button] = []
        for i, (label, cb, group) in enumerate(actions):
            col = i % 2
            row = i // 2
            r = pygame.Rect(start_x + col * (col_w + gap),
                            start_y + row * (col_h + gap),
                            col_w, col_h)
            style = "primary"
            if group == "exit":
                style = "danger"
            elif group == "save":
                style = "ghost"
            b = Button(r, label, fonts=self.ctx.fonts, theme=self.ctx.theme,
                       font_size=16, style=style, on_click=cb,
                       enabled=cb is not None)
            self._action_buttons.append(b)

        # Settings panel below the grid.
        settings_y = start_y + ((len(actions) + 1) // 2) * (col_h + gap) + 12
        # Text-speed row.
        self._text_speed_label_y = settings_y
        self._text_speed_buttons: list[tuple[Button, float]] = []
        sx = start_x + 130
        for v, label in [(0.0, "瞬間"), (20.0, "慢"), (45.0, "中"), (80.0, "快")]:
            btn = Button(
                pygame.Rect(sx, settings_y - 6, 80, 36),
                label, fonts=self.ctx.fonts, theme=self.ctx.theme,
                font_size=14,
                style="primary" if abs(self.ctx.config.text_speed - v) < 0.1
                                    else "ghost",
                on_click=(lambda v=v: self._set_text_speed(v)),
            )
            self._text_speed_buttons.append((btn, v))
            sx += 88

        # BGM volume row
        self._vol_label_y = settings_y + 48
        self._vol_minus = Button(
            pygame.Rect(start_x + 130, self._vol_label_y - 6, 36, 36),
            "−", fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=18, on_click=lambda: self._adjust_volume(-0.1),
        )
        self._vol_plus = Button(
            pygame.Rect(start_x + 220, self._vol_label_y - 6, 36, 36),
            "+", fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=18, on_click=lambda: self._adjust_volume(0.1),
        )

        # Fullscreen
        self._fs_label_y = settings_y + 96
        self._fs_btn = Button(
            pygame.Rect(start_x + 130, self._fs_label_y - 6, 160, 36),
            "切換全螢幕", fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=14, style="ghost", on_click=self._toggle_fullscreen,
        )
        self._kb_btn = Button(
            pygame.Rect(start_x + 300, self._fs_label_y - 6, 160, 36),
            "顯示快捷鍵說明", fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=14, style="ghost",
            on_click=lambda: setattr(self, "_show_kb_hint",
                                      not self._show_kb_hint),
        )
        # Quit at the very bottom right of the panel.
        self._quit_btn = Button(
            pygame.Rect(self._panel_rect.right - 200,
                        self._panel_rect.bottom - 60, 160, 40),
            "離開遊戲", fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=15, style="danger",
            on_click=(lambda: self.on_quit_app() if self.on_quit_app else None),
        )

    # ----- settings actions -------------------------------------------------

    def _set_text_speed(self, v: float) -> None:
        self.ctx.config.text_speed = v
        self._build()  # rebuild so the "current" button highlights update

    def _adjust_volume(self, delta: float) -> None:
        try:
            cur = pygame.mixer.music.get_volume()
            new_v = max(0.0, min(1.0, cur + delta))
            pygame.mixer.music.set_volume(new_v)
        except pygame.error:
            pass

    def _toggle_fullscreen(self) -> None:
        try:
            pygame.display.toggle_fullscreen()
        except pygame.error:
            pass

    # ----- lifecycle --------------------------------------------------------

    def update(self, dt: float, inp) -> None:
        if inp.cancel and self.on_close:
            self.on_close()
            return
        self.close_btn.update(dt, inp)
        for b in self._action_buttons:
            b.update(dt, inp)
        for b, _ in self._text_speed_buttons:
            b.update(dt, inp)
        self._vol_minus.update(dt, inp)
        self._vol_plus.update(dt, inp)
        self._fs_btn.update(dt, inp)
        self._kb_btn.update(dt, inp)
        self._quit_btn.update(dt, inp)

    def draw(self, surface: pygame.Surface) -> None:
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 170))
        surface.blit(veil, (0, 0))
        self._panel.draw(surface)
        title = self.ctx.fonts.render("選單",
                                      self.ctx.config.font_size_header,
                                      self.ctx.theme.accent, bold=True)
        surface.blit(title, (self._panel_rect.x + 32,
                             self._panel_rect.y + 28))
        self.close_btn.draw(surface)
        for b in self._action_buttons:
            b.draw(surface)
        # Section labels for the settings rows.
        for y, text in [
            (self._text_speed_label_y, "文字速度"),
            (self._vol_label_y, "音量"),
            (self._fs_label_y, "顯示 / 說明"),
        ]:
            lbl = self.ctx.fonts.render(text, 16,
                                         self.ctx.theme.text_mute, bold=True)
            surface.blit(lbl, (self._panel_rect.x + 40, y))
        for b, _ in self._text_speed_buttons:
            b.draw(surface)
        try:
            vol = int(pygame.mixer.music.get_volume() * 100)
        except pygame.error:
            vol = 0
        vol_text = self.ctx.fonts.render(f"{vol}%", 16,
                                          self.ctx.theme.text)
        surface.blit(vol_text, (self._panel_rect.x + 174,
                                 self._vol_label_y))
        self._vol_minus.draw(surface)
        self._vol_plus.draw(surface)
        self._fs_btn.draw(surface)
        self._kb_btn.draw(surface)
        self._quit_btn.draw(surface)
        if self._show_kb_hint:
            tips = [
                "Space / Enter / Z   推進對話",
                "Ctrl                按住快進",
                "Esc / X             關閉這個 overlay",
                "M / A / L / T / I / S   地圖 / 好感 / 事件 / 成就 / 物品 / 存檔",
                "B / 滾輪上          對話 scrollback",
                "F12                 截圖",
                "F11                 印當前狀態 (debug)",
            ]
            y = self._panel_rect.bottom + 12
            for t in tips:
                ts = self.ctx.fonts.render(t, 14, self.ctx.theme.text_mute)
                surface.blit(ts, (self._panel_rect.x, y))
                y += ts.get_height() + 2

    def describe(self) -> dict:
        return {"scene": "MenuScene"}
