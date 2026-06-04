"""Settings overlay: text speed, volumes, playback, fullscreen toggle.

The body scrolls (mouse wheel), so the panel never overflows regardless of
window size or how many characters the per-character voice section lists.
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
        self._scroll_y = 0

    # ---- layout helpers ------------------------------------------------------

    def _mk(self, w: int, h: int, label: str, on_click, *,
            style: str = "ghost", font_size: int = 15) -> Button:
        return Button(pygame.Rect(0, 0, w, h), label,
                      fonts=self.ctx.fonts, theme=self.ctx.theme,
                      font_size=font_size, style=style, on_click=on_click)

    def enter(self, *, on_close=None, **_) -> None:
        self.on_close = on_close
        cfg = self.ctx.config
        theme = self.ctx.theme
        sw, sh = self.ctx.screen_size

        pw = min(980, sw - 100)
        ph = min(720, sh - 70)
        self._panel_rect = pygame.Rect((sw - pw) // 2, (sh - ph) // 2, pw, ph)
        self._panel = Panel(self._panel_rect, theme,
                            fill=(*theme.bg_overlay[:3], 255),
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
            pygame.Rect(self._panel_rect.right - 116 - 20,
                        self._panel_rect.y + 20, 116, 40),
            self.ctx.localization.t("close", "關閉"),
            fonts=self.ctx.fonts, theme=theme,
            font_size=15, style="ghost",
            on_click=(lambda: on_close() if on_close else None),
        )

        # Content laid out in body-relative coords (x from body.left, y from
        # the content top); rendered with a scroll offset + clip each frame.
        self._labels: list[tuple] = []   # (text, size, bold, color, cx, cy)
        self._values: list[tuple] = []   # (fn->str, size, color, cx, cy)
        self._buttons: list[tuple] = []  # (Button, cx, cy)
        cy = 4

        def section(title: str) -> None:
            nonlocal cy
            cy += 10
            self._labels.append((title, 18, True, theme.accent_warm, 4, cy))
            cy += 34

        # --- text speed ----------------------------------------------------
        section("文字速度")
        bx = 4
        for v, label in [(0.0, "瞬間"), (20.0, "慢"), (45.0, "中"), (80.0, "快")]:
            sel = abs(cfg.text_speed - v) < 0.1
            b = self._mk(120, 42, label,
                         (lambda v=v: self._set_text_speed(v)),
                         style="primary" if sel else "ghost")
            self._buttons.append((b, bx, cy))
            bx += 130
        cy += 42 + 14

        # --- auto-play speed ----------------------------------------------
        section("自動播放速度")
        bx = 4
        for v, label in [(0.5, "慢"), (1.0, "中"), (1.5, "快"), (2.0, "極快")]:
            sel = abs(cfg.auto_play_speed - v) < 0.05
            b = self._mk(120, 42, label,
                         (lambda v=v: self._set_auto_play_speed(v)),
                         style="primary" if sel else "ghost")
            self._buttons.append((b, bx, cy))
            bx += 130
        cy += 42 + 14

        # --- volumes -------------------------------------------------------
        section("音量")
        # BGM row
        self._labels.append(("BGM", 16, False, theme.text, 4, cy + 8))
        self._values.append((self._bgm_pct_str, 16, theme.text_mute, 96, cy + 8))
        bgm_minus = self._mk(40, 40, "-", lambda: self._adjust_volume(-0.1),
                             font_size=20)
        bgm_plus = self._mk(40, 40, "+", lambda: self._adjust_volume(0.1),
                            font_size=20)
        self._buttons.append((bgm_minus, 200, cy))
        self._buttons.append((bgm_plus, 250, cy))
        cy += 52
        # Voice row
        self._labels.append(("語音", 16, False, theme.text, 4, cy + 8))
        self._values.append((self._voice_pct_str, 16, theme.text_mute, 96, cy + 8))
        v_minus = self._mk(40, 40, "-", lambda: self._adjust_voice_volume(-0.1),
                           font_size=20)
        v_plus = self._mk(40, 40, "+", lambda: self._adjust_voice_volume(0.1),
                          font_size=20)
        self._buttons.append((v_minus, 200, cy))
        self._buttons.append((v_plus, 250, cy))
        cy += 52
        # SFX row
        self._labels.append(("音效", 16, False, theme.text, 4, cy + 8))
        self._values.append((self._sfx_pct_str, 16, theme.text_mute, 96, cy + 8))
        se_minus = self._mk(40, 40, "-", lambda: self._adjust_sfx_volume(-0.1),
                            font_size=20)
        se_plus = self._mk(40, 40, "+", lambda: self._adjust_sfx_volume(0.1),
                           font_size=20)
        self._buttons.append((se_minus, 200, cy))
        self._buttons.append((se_plus, 250, cy))
        cy += 52 + 14

        # --- playback toggles ---------------------------------------------
        section("播放")
        for label, attr, handler in [
            ("等待語音", "auto_play_wait_voice", self._toggle_wait_voice),
            ("僅快進已讀", "skip_unread_only", self._toggle_skip_unread),
            ("立繪壓暗非說話者", "dim_inactive_speakers",
             self._toggle_dim_speakers),
            ("立繪情緒反應", "auto_emote_on_emotion", self._toggle_auto_emote),
            ("NVL 模式", "nvl_mode", self._toggle_nvl),
        ]:
            on = bool(getattr(cfg, attr))
            b = self._mk(300, 42, self._toggle_label(label, on), handler,
                         style="primary" if on else "ghost")
            self._buttons.append((b, 4, cy))
            cy += 50

        # --- per-character voice volume -----------------------------------
        try:
            npcs = self.ctx.npcs.all() if self.ctx.npcs is not None else []
        except Exception:
            npcs = []
        if npcs:
            cy += 4
            section("角色語音音量")
            for npc in npcs:
                nid = npc.id
                self._labels.append((npc.name[:6], 15, False, theme.text, 4, cy + 8))
                self._values.append(
                    ((lambda nid=nid: f"{int(self._char_voice_volume(nid) * 100)}%"),
                     14, theme.text_mute, 110, cy + 9))
                minus = self._mk(38, 38, "-",
                                 (lambda nid=nid: self._adjust_char_voice(nid, -0.1)),
                                 font_size=18)
                plus = self._mk(38, 38, "+",
                                (lambda nid=nid: self._adjust_char_voice(nid, 0.1)),
                                font_size=18)
                self._buttons.append((minus, 170, cy))
                self._buttons.append((plus, 218, cy))
                cy += 46

        # --- display -------------------------------------------------------
        cy += 4
        section("顯示")
        fs = self._mk(220, 42, "切換全螢幕 (F)", self._toggle_fullscreen)
        self._buttons.append((fs, 4, cy))
        cy += 50 + 10

        # --- keyboard shortcuts (listed inline) ---------------------------
        section("快捷鍵")
        for keys, desc in [
            ("Space / Enter / Z", "推進對話"),
            ("Ctrl（按住）", "快進"),
            ("A", "自動播放"),
            ("Esc / X", "關閉 overlay"),
            ("M / A / L / I", "地圖 / 好感 / 事件 / 物品"),
            ("S", "存檔"),
            ("F6 / F9", "快速存檔 / 快速讀取"),
            ("F12", "截圖"),
        ]:
            self._labels.append((keys, 14, False, theme.text, 4, cy))
            self._labels.append((desc, 14, False, theme.text_mute, 230, cy))
            cy += 26
        cy += 8

        self._content_height = cy

    # ---- value formatters ----------------------------------------------------

    def _bgm_pct_str(self) -> str:
        return f"{int(self.ctx.config.bgm_volume * 100)}%"

    def _voice_pct_str(self) -> str:
        return f"{int(self.ctx.config.voice_volume * 100)}%"

    def _sfx_pct_str(self) -> str:
        return f"{int(self.ctx.config.sfx_volume * 100)}%"

    # ---- handlers ------------------------------------------------------------

    def _rebuild(self) -> None:
        """Re-run layout after a value change, keeping the scroll position."""
        keep = self._scroll_y
        self.enter(on_close=self.on_close)
        self._scroll_y = keep

    def _set_text_speed(self, v: float) -> None:
        self.ctx.config.text_speed = v
        self.ctx.config.save_to_disk()
        self._rebuild()

    def _adjust_volume(self, delta: float) -> None:
        new_v = max(0.0, min(1.0, round(self.ctx.config.bgm_volume + delta, 3)))
        self.ctx.config.bgm_volume = new_v
        self.ctx.assets.set_music_volume(new_v)
        self.ctx.config.save_to_disk()

    def _adjust_voice_volume(self, delta: float) -> None:
        new_v = max(0.0, min(1.0, round(self.ctx.config.voice_volume + delta, 3)))
        self.ctx.config.voice_volume = new_v
        self.ctx.assets._voice_volume = new_v
        self.ctx.config.save_to_disk()

    def _char_voice_volume(self, npc_id: str) -> float:
        """Effective per-character voice volume (falls back to the global)."""
        return self.ctx.config.per_character_voice_volume.get(
            npc_id, self.ctx.config.voice_volume)

    def _adjust_sfx_volume(self, delta: float) -> None:
        new_v = max(0.0, min(1.0, round(self.ctx.config.sfx_volume + delta, 3)))
        self.ctx.config.sfx_volume = new_v
        self.ctx.config.save_to_disk()

    def _adjust_char_voice(self, npc_id: str, delta: float) -> None:
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
        self._rebuild()

    def _toggle_wait_voice(self) -> None:
        cfg = self.ctx.config
        cfg.auto_play_wait_voice = not cfg.auto_play_wait_voice
        cfg.save_to_disk()
        self._rebuild()

    def _toggle_skip_unread(self) -> None:
        cfg = self.ctx.config
        cfg.skip_unread_only = not cfg.skip_unread_only
        cfg.save_to_disk()
        self._rebuild()

    def _toggle_dim_speakers(self) -> None:
        cfg = self.ctx.config
        cfg.dim_inactive_speakers = not cfg.dim_inactive_speakers
        cfg.save_to_disk()
        self._rebuild()

    def _toggle_auto_emote(self) -> None:
        cfg = self.ctx.config
        cfg.auto_emote_on_emotion = not cfg.auto_emote_on_emotion
        cfg.save_to_disk()
        self._rebuild()

    def _toggle_nvl(self) -> None:
        cfg = self.ctx.config
        cfg.nvl_mode = not cfg.nvl_mode
        cfg.save_to_disk()
        self._rebuild()

    def _toggle_fullscreen(self) -> None:
        try:
            pygame.display.toggle_fullscreen()
        except pygame.error:
            pass

    # ---- scroll plumbing -----------------------------------------------------

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
        for e in inp.events:
            if e.type == pygame.KEYDOWN and e.key == pygame.K_f:
                self._toggle_fullscreen()
        if self._body_rect.collidepoint(inp.mouse_pos):
            self._scroll_y -= int(inp.mouse_wheel) * 44
        self._scroll_y = max(0, min(self._scroll_y, self._max_scroll()))
        self._reposition()
        self.close_btn.update(dt, inp)
        body = self._body_rect
        for b, _cx, _cy in self._buttons:
            # Only clickable while fully inside the scroll body (so a control
            # scrolled under the header/edge can't be hit).
            if body.y <= b.rect.y and b.rect.bottom <= body.bottom:
                b.update(dt, inp)
            else:
                b._hover = False

    def draw(self, surface: pygame.Surface) -> None:
        theme = self.ctx.theme
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 236))
        surface.blit(veil, (0, 0))
        self._panel.draw(surface)
        title = self.ctx.fonts.render("設定", self.ctx.config.font_size_header,
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
        for fn, size, color, cx, cy in self._values:
            surface.blit(self.ctx.fonts.render(fn(), size, color),
                         (body.x + cx, oy + cy))
        for b, _cx, _cy in self._buttons:
            if b.rect.bottom >= body.y and b.rect.y <= body.bottom:
                b.draw(surface)
        surface.set_clip(prev_clip)

        # scrollbar
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
        return {"scene": "SettingsScene",
                "text_speed": self.ctx.config.text_speed,
                "content_height": getattr(self, "_content_height", 0),
                "scroll_y": self._scroll_y}
