"""Settings overlay: text speed, fullscreen toggle, BGM volume.

Settings persist for the current process; they aren't written to disk
yet. If you need persistence, hook this up to a user-config JSON in
engine.config.writable_root().
"""
from __future__ import annotations

from typing import Callable

import pygame

from .base import Scene, SceneContext
from ..ui.widgets import Button, Panel


class SettingsScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        self.on_close: Callable[[], None] | None = None

    def enter(self, *, on_close=None, **_) -> None:
        self.on_close = on_close
        sw, sh = self.ctx.screen_size
        self._panel_rect = pygame.Rect(sw // 2 - 320, sh // 2 - 220, 640, 440)
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
        # Text speed presets
        self._text_buttons: list[tuple[Button, float]] = []
        speeds = [(0.0, "瞬間"), (20.0, "慢"), (45.0, "中"), (80.0, "快")]
        bw = 110
        bx = self._panel_rect.x + 200
        by = self._panel_rect.y + 110
        for v, label in speeds:
            current = "（目前）" if abs(self.ctx.config.text_speed - v) < 0.1 else ""
            b = Button(
                pygame.Rect(bx, by, bw, 38),
                f"{label}{current}",
                fonts=self.ctx.fonts, theme=self.ctx.theme,
                font_size=14,
                on_click=(lambda v=v: self._set_text_speed(v)),
            )
            self._text_buttons.append((b, v))
            bx += bw + 8
        # BGM volume controls
        self._vol_minus = Button(
            pygame.Rect(self._panel_rect.x + 200, self._panel_rect.y + 180,
                        38, 38),
            "-", fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=18, on_click=lambda: self._adjust_volume(-0.1),
        )
        self._vol_plus = Button(
            pygame.Rect(self._panel_rect.x + 320, self._panel_rect.y + 180,
                        38, 38),
            "+", fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=18, on_click=lambda: self._adjust_volume(0.1),
        )
        self._fullscreen_btn = Button(
            pygame.Rect(self._panel_rect.x + 200, self._panel_rect.y + 250,
                        180, 38),
            "切換全螢幕 (F)", fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=15, on_click=self._toggle_fullscreen,
        )
        self._shortcuts_btn = Button(
            pygame.Rect(self._panel_rect.x + 200, self._panel_rect.y + 320,
                        180, 38),
            "看快捷鍵說明", fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=15, on_click=self._show_shortcuts,
        )
        self._show_shortcut_hint = False

    def _set_text_speed(self, v: float) -> None:
        self.ctx.config.text_speed = v
        self.enter(on_close=self.on_close)  # rebuild labels

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

    def _show_shortcuts(self) -> None:
        self._show_shortcut_hint = not self._show_shortcut_hint

    def update(self, dt: float, inp) -> None:
        if inp.cancel and self.on_close:
            self.on_close()
            return
        for e in inp.events:
            if e.type == pygame.KEYDOWN and e.key == pygame.K_f:
                self._toggle_fullscreen()
        self.close_btn.update(dt, inp)
        for b, _ in self._text_buttons:
            b.update(dt, inp)
        self._vol_minus.update(dt, inp)
        self._vol_plus.update(dt, inp)
        self._fullscreen_btn.update(dt, inp)
        self._shortcuts_btn.update(dt, inp)

    def draw(self, surface: pygame.Surface) -> None:
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 170))
        surface.blit(veil, (0, 0))
        self._panel.draw(surface)
        title = self.ctx.fonts.render("設定",
                                      self.ctx.config.font_size_header,
                                      self.ctx.theme.accent, bold=True)
        surface.blit(title, (self._panel_rect.x + 32, self._panel_rect.y + 28))
        self.close_btn.draw(surface)
        # Section labels
        lbl = self.ctx.fonts.render("文字速度", 18,
                                    self.ctx.theme.text_mute, bold=True)
        surface.blit(lbl, (self._panel_rect.x + 40, self._panel_rect.y + 115))
        for b, _ in self._text_buttons:
            b.draw(surface)
        vol_lbl = self.ctx.fonts.render("BGM 音量", 18,
                                        self.ctx.theme.text_mute, bold=True)
        surface.blit(vol_lbl, (self._panel_rect.x + 40,
                               self._panel_rect.y + 185))
        try:
            vol = int(pygame.mixer.music.get_volume() * 100)
        except pygame.error:
            vol = 0
        v_show = self.ctx.fonts.render(f"{vol}%", 18,
                                       self.ctx.theme.text)
        surface.blit(v_show, (self._panel_rect.x + 260,
                              self._panel_rect.y + 185))
        self._vol_minus.draw(surface)
        self._vol_plus.draw(surface)
        fs_lbl = self.ctx.fonts.render("顯示", 18,
                                       self.ctx.theme.text_mute, bold=True)
        surface.blit(fs_lbl, (self._panel_rect.x + 40,
                              self._panel_rect.y + 255))
        self._fullscreen_btn.draw(surface)
        kb_lbl = self.ctx.fonts.render("快捷鍵", 18,
                                       self.ctx.theme.text_mute, bold=True)
        surface.blit(kb_lbl, (self._panel_rect.x + 40,
                              self._panel_rect.y + 325))
        self._shortcuts_btn.draw(surface)
        if self._show_shortcut_hint:
            tips = [
                "Space / Enter / Z   推進對話",
                "Ctrl                  按住快進",
                "Esc / X               關閉 overlay",
                "M                     地圖",
                "A                     好感度",
                "L                     事件記錄",
                "S                     存檔",
                "F12                   截圖",
                "F11                   印當前狀態 (debug)",
            ]
            y = self._panel_rect.bottom + 12
            for t in tips:
                ts = self.ctx.fonts.render(t, 14, self.ctx.theme.text_mute)
                surface.blit(ts, (self._panel_rect.x, y))
                y += ts.get_height() + 2

    def describe(self) -> dict:
        return {"scene": "SettingsScene",
                "text_speed": self.ctx.config.text_speed}
