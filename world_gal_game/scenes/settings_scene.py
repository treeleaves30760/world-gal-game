"""Settings overlay: text speed, volumes, playback, fullscreen toggle.

User-tunable settings persist to ``settings.json`` under
``config.writable_root(app_data_name)``: any control change calls
``ctx.config.save_to_disk()`` so the choice survives the next launch
(loaded at boot via ``config.load_from_disk()``).
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
        self._panel_rect = pygame.Rect(sw // 2 - 360, sh // 2 - 300, 720, 600)
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
        # Voice volume controls (mirrors the BGM row).
        self._voice_minus = Button(
            pygame.Rect(self._panel_rect.x + 200, self._panel_rect.y + 230,
                        38, 38),
            "-", fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=18, on_click=lambda: self._adjust_voice_volume(-0.1),
        )
        self._voice_plus = Button(
            pygame.Rect(self._panel_rect.x + 320, self._panel_rect.y + 230,
                        38, 38),
            "+", fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=18, on_click=lambda: self._adjust_voice_volume(0.1),
        )
        self._fullscreen_btn = Button(
            pygame.Rect(self._panel_rect.x + 200, self._panel_rect.y + 300,
                        180, 38),
            "切換全螢幕 (F)", fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=15, on_click=self._toggle_fullscreen,
        )
        self._shortcuts_btn = Button(
            pygame.Rect(self._panel_rect.x + 200, self._panel_rect.y + 370,
                        180, 38),
            "看快捷鍵說明", fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=15, on_click=self._show_shortcuts,
        )
        # --- Playback column (right side): auto-play + skip + NVL -----------
        col_x = self._panel_rect.x + 460
        # Auto-play speed presets (scales auto_play_delay; higher = faster).
        self._auto_speed_buttons: list[tuple[Button, float]] = []
        auto_speeds = [(0.5, "慢"), (1.0, "中"), (1.5, "快"), (2.0, "極快")]
        abw = 56
        abx = col_x
        aby = self._panel_rect.y + 110
        for v, label in auto_speeds:
            current = "*" if abs(self.ctx.config.auto_play_speed - v) < 0.05 else ""
            b = Button(
                pygame.Rect(abx, aby, abw, 38),
                f"{label}{current}",
                fonts=self.ctx.fonts, theme=self.ctx.theme,
                font_size=14,
                on_click=(lambda v=v: self._set_auto_play_speed(v)),
            )
            self._auto_speed_buttons.append((b, v))
            abx += abw + 6
        # Toggles: wait-for-voice, skip-unread-only, NVL mode.
        self._wait_voice_btn = Button(
            pygame.Rect(col_x, self._panel_rect.y + 180, 200, 38),
            self._toggle_label("等待語音", self.ctx.config.auto_play_wait_voice),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=15, on_click=self._toggle_wait_voice,
        )
        self._skip_unread_btn = Button(
            pygame.Rect(col_x, self._panel_rect.y + 240, 200, 38),
            self._toggle_label("僅快進已讀", self.ctx.config.skip_unread_only),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=15, on_click=self._toggle_skip_unread,
        )
        self._nvl_btn = Button(
            pygame.Rect(col_x, self._panel_rect.y + 300, 200, 38),
            self._toggle_label("NVL 模式", self.ctx.config.nvl_mode),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=15, on_click=self._toggle_nvl,
        )
        # --- Per-character voice volume (right column, below the toggles) ---
        # Each entry: (npc_id, display_name, minus_button, plus_button). We
        # list characters from the NPC registry; absent volumes fall back to
        # the global voice_volume. Capped so a large cast still fits the panel
        # (overflow is dropped from the UI but its config values are untouched).
        self._voice_char_rows: list[tuple] = []
        self._voice_char_col_x = col_x
        self._voice_char_top = self._panel_rect.y + 388
        try:
            npcs = self.ctx.npcs.all() if self.ctx.npcs is not None else []
        except Exception:
            npcs = []
        row_h = 34
        max_rows = 5
        for i, npc in enumerate(npcs[:max_rows]):
            ry = self._voice_char_top + i * row_h
            minus = Button(
                pygame.Rect(col_x + 130, ry, 30, 30),
                "-", fonts=self.ctx.fonts, theme=self.ctx.theme,
                font_size=16,
                on_click=(lambda nid=npc.id: self._adjust_char_voice(nid, -0.1)),
            )
            plus = Button(
                pygame.Rect(col_x + 200, ry, 30, 30),
                "+", fonts=self.ctx.fonts, theme=self.ctx.theme,
                font_size=16,
                on_click=(lambda nid=npc.id: self._adjust_char_voice(nid, 0.1)),
            )
            self._voice_char_rows.append((npc.id, npc.name, minus, plus))
        self._show_shortcut_hint = False

    def _set_text_speed(self, v: float) -> None:
        self.ctx.config.text_speed = v
        self.ctx.config.save_to_disk()
        self.enter(on_close=self.on_close)  # rebuild labels

    def _adjust_volume(self, delta: float) -> None:
        try:
            cur = pygame.mixer.music.get_volume()
            new_v = max(0.0, min(1.0, cur + delta))
            pygame.mixer.music.set_volume(new_v)
        except pygame.error:
            pass

    def _adjust_voice_volume(self, delta: float) -> None:
        new_v = max(0.0, min(1.0, self.ctx.config.voice_volume + delta))
        self.ctx.config.voice_volume = new_v
        self.ctx.assets._voice_volume = new_v
        self.ctx.config.save_to_disk()

    def _char_voice_volume(self, npc_id: str) -> float:
        """Effective per-character voice volume (falls back to the global)."""
        return self.ctx.config.per_character_voice_volume.get(
            npc_id, self.ctx.config.voice_volume)

    def _adjust_char_voice(self, npc_id: str, delta: float) -> None:
        """Nudge one character's voice volume, seeding from the global default
        the first time it's touched, then persist."""
        cur = self._char_voice_volume(npc_id)
        new_v = max(0.0, min(1.0, round(cur + delta, 3)))
        self.ctx.config.per_character_voice_volume[npc_id] = new_v
        self.ctx.config.save_to_disk()

    @staticmethod
    def _toggle_label(label: str, on: bool) -> str:
        return f"{label}：{'開' if on else '關'}"

    def _set_auto_play_speed(self, v: float) -> None:
        self.ctx.config.auto_play_speed = v
        self.ctx.config.save_to_disk()
        self.enter(on_close=self.on_close)  # rebuild labels

    def _toggle_wait_voice(self) -> None:
        cfg = self.ctx.config
        cfg.auto_play_wait_voice = not cfg.auto_play_wait_voice
        cfg.save_to_disk()
        self.enter(on_close=self.on_close)

    def _toggle_skip_unread(self) -> None:
        cfg = self.ctx.config
        cfg.skip_unread_only = not cfg.skip_unread_only
        cfg.save_to_disk()
        self.enter(on_close=self.on_close)

    def _toggle_nvl(self) -> None:
        cfg = self.ctx.config
        cfg.nvl_mode = not cfg.nvl_mode
        cfg.save_to_disk()
        self.enter(on_close=self.on_close)

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
        self._voice_minus.update(dt, inp)
        self._voice_plus.update(dt, inp)
        self._fullscreen_btn.update(dt, inp)
        self._shortcuts_btn.update(dt, inp)
        for b, _ in self._auto_speed_buttons:
            b.update(dt, inp)
        self._wait_voice_btn.update(dt, inp)
        self._skip_unread_btn.update(dt, inp)
        self._nvl_btn.update(dt, inp)
        for _nid, _name, minus, plus in self._voice_char_rows:
            minus.update(dt, inp)
            plus.update(dt, inp)

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
        voice_lbl = self.ctx.fonts.render("語音音量", 18,
                                          self.ctx.theme.text_mute, bold=True)
        surface.blit(voice_lbl, (self._panel_rect.x + 40,
                                 self._panel_rect.y + 235))
        voice_pct = int(self.ctx.config.voice_volume * 100)
        voice_show = self.ctx.fonts.render(f"{voice_pct}%", 18,
                                           self.ctx.theme.text)
        surface.blit(voice_show, (self._panel_rect.x + 260,
                                  self._panel_rect.y + 235))
        self._voice_minus.draw(surface)
        self._voice_plus.draw(surface)
        fs_lbl = self.ctx.fonts.render("顯示", 18,
                                       self.ctx.theme.text_mute, bold=True)
        surface.blit(fs_lbl, (self._panel_rect.x + 40,
                              self._panel_rect.y + 305))
        self._fullscreen_btn.draw(surface)
        kb_lbl = self.ctx.fonts.render("快捷鍵", 18,
                                       self.ctx.theme.text_mute, bold=True)
        surface.blit(kb_lbl, (self._panel_rect.x + 40,
                              self._panel_rect.y + 375))
        self._shortcuts_btn.draw(surface)
        # --- Playback column (right side) ----------------------------------
        col_x = self._panel_rect.x + 460
        auto_lbl = self.ctx.fonts.render("自動播放速度", 18,
                                         self.ctx.theme.text_mute, bold=True)
        surface.blit(auto_lbl, (col_x, self._panel_rect.y + 88))
        for b, _ in self._auto_speed_buttons:
            b.draw(surface)
        self._wait_voice_btn.draw(surface)
        self._skip_unread_btn.draw(surface)
        self._nvl_btn.draw(surface)
        # Per-character voice volume section.
        pc_lbl = self.ctx.fonts.render("角色語音音量", 18,
                                       self.ctx.theme.text_mute, bold=True)
        surface.blit(pc_lbl, (col_x, self._voice_char_top - 26))
        if self._voice_char_rows:
            for nid, name, minus, plus in self._voice_char_rows:
                row_y = minus.rect.y
                name_show = self.ctx.fonts.render(
                    name[:5], 14, self.ctx.theme.text)
                surface.blit(name_show, (col_x, row_y + 6))
                pct = int(self._char_voice_volume(nid) * 100)
                pct_show = self.ctx.fonts.render(
                    f"{pct}%", 13, self.ctx.theme.text_mute)
                surface.blit(pct_show, (col_x + 86, row_y + 8))
                minus.draw(surface)
                plus.draw(surface)
        else:
            none_show = self.ctx.fonts.render(
                "（無角色）", 14, self.ctx.theme.text_mute)
            surface.blit(none_show, (col_x, self._voice_char_top + 2))
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
