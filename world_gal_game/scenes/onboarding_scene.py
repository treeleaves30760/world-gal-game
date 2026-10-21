"""First-run, progressively disclosed reading onboarding.

Shown once over the first scene of a new game (gated by
``config.seen_intro``).  The opening card teaches only the three actions a
player needs immediately: advance, choose, and open the menu.  Auto/skip,
history, rollback, and the rest remain discoverable in the persistent reading
toolbar and Settings instead of competing for attention before the first line.
Closing it sets the persisted flag so it never appears again.
"""
from __future__ import annotations

import pygame

from .base import Scene, SceneContext
from ..ui.input_bindings import BindingProfile, InputAction
from ..ui.system_chrome import ChromeRequest
from ..ui.widgets import Button, Panel


class OnboardingScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True

    def enter(self, *, on_close=None, **_) -> None:
        self.on_close = on_close
        bindings = BindingProfile(
            getattr(self.ctx.config, "input_bindings", {}),
        )
        confirm = bindings.primary_display(InputAction.CONFIRM_ADVANCE)
        nav = "／".join((
            bindings.primary_display(InputAction.NAV_UP),
            bindings.primary_display(InputAction.NAV_DOWN),
            bindings.primary_display(InputAction.NAV_LEFT),
            bindings.primary_display(InputAction.NAV_RIGHT),
        ))
        menu = bindings.primary_display(InputAction.OPEN_MENU)
        self._rows = [
            (
                "01",
                self.ctx.t("onboarding_advance_title", "推進文字"),
                self.ctx.t(
                    "onboarding_advance_desc",
                    "{confirm}／點擊：顯示全文，再前往下一句",
                    confirm=confirm,
                ),
            ),
            (
                "02",
                self.ctx.t("onboarding_choice_title", "做出選擇"),
                self.ctx.t(
                    "onboarding_choice_desc",
                    "{nav} 選取；{confirm} 確認",
                    nav=nav,
                    confirm=confirm,
                ),
            ),
            (
                "03",
                self.ctx.t("onboarding_menu_title", "開啟選單"),
                self.ctx.t(
                    "onboarding_menu_desc",
                    "{menu} 開啟；其餘操作可在設定查看",
                    menu=menu,
                ),
            ),
        ]
        # Compatibility alias retained for developer tooling and pack tests
        # that inspected the original class-level onboarding rows.
        self._ROWS = self._rows
        sw, sh = self.ctx.screen_size
        # FontRegistry never shrinks below 1x on the supported compact
        # canvases. Geometry must follow that same floor; scaling a 48 px
        # button to 24 px at 360p left normal-sized text overflowing tiny
        # rows and broke the 44 px input-target contract.
        self._density = max(
            1.0,
            min(
                3.0,
                float(getattr(self.ctx.fonts, "scale", 1.0)),
            ),
        )

        def sp(value: int) -> int:
            return max(1, int(round(value * self._density)))

        margin = min(sp(24), max(8, min(sw, sh) // 20))
        pw = min(sp(660), max(1, sw - margin * 2))
        ph = min(sp(408), max(1, sh - margin * 2))
        self._panel_rect = pygame.Rect(0, 0, pw, ph)
        self._panel_rect.center = (sw // 2, sh // 2)
        self._panel = Panel(
            self._panel_rect,
            self.ctx.theme,
            fill=(*self.ctx.theme.bg_overlay[:3], 240),
            border=self.ctx.theme.border_strong,
            radius=self.ctx.theme.radius_l,
            border_width=2,
            chrome=self.ctx.ui_chrome,
            screen="onboarding",
            role="main",
            density=self._density,
        )
        bw = min(sp(220), max(1, pw - sp(64)))
        bh = max(44, sp(48))
        button_bottom_margin = min(sp(20), max(10, ph // 16))
        self.start_btn = Button(
            pygame.Rect(self._panel_rect.centerx - bw // 2,
                        self._panel_rect.bottom - button_bottom_margin - bh,
                        bw, bh),
            self.ctx.t("onboarding_start", "開始閱讀"),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=18, style="primary",
            on_click=(lambda: on_close() if on_close else None),
            chrome=self.ctx.ui_chrome,
            screen="onboarding",
            role="start",
            motion=self.ctx.motion,
        )
        # There is only one action on this modal. Showing focus immediately
        # makes the just-taught Enter/Space path visibly actionable without a
        # preliminary Down press.
        self.start_btn.focused = True
        row_x = self._panel_rect.x + sp(40)
        intro_clearance = (
            sp(25)
            + sp(42)
            + self.ctx.fonts.get(16).get_linesize()
            + sp(6)
        )
        row_y = self._panel_rect.y + min(
            sp(104),
            max(intro_clearance, ph // 4),
        )
        row_w = self._panel_rect.width - sp(80)
        row_area_bottom = self.start_btn.rect.top - sp(8)
        row_h = max(
            48,
            (row_area_bottom - row_y) // len(self._rows),
        )
        self._row_rects = [
            pygame.Rect(row_x, row_y + index * row_h, row_w, row_h)
            for index in range(len(self._rows))
        ]

    @staticmethod
    def _fit_text(
        text: str,
        font: pygame.font.Font,
        max_width: int,
    ) -> str:
        """Ellipsize one onboarding hint without painting outside its row."""
        value = str(text)
        if max_width <= 0:
            return ""
        if font.size(value)[0] <= max_width:
            return value
        while value:
            value = value[:-1]
            candidate = value.rstrip() + "…"
            if font.size(candidate)[0] <= max_width:
                return candidate
        return "…"

    def update(self, dt: float, inp) -> None:
        # Esc or advance both dismiss — a player who just hits Space should not
        # be trapped behind the card.
        if (inp.cancel or inp.advance_dialogue) and self.on_close:
            self.on_close()
            return
        self.start_btn.update(dt, inp)

    def draw(self, surface: pygame.Surface) -> None:
        theme = self.ctx.theme
        density = self._density

        def sp(value: int) -> int:
            return max(1, int(round(value * density)))

        handled = (
            self.ctx.ui_chrome is not None
            and self.ctx.ui_chrome.paint(
                surface,
                surface.get_rect(),
                ChromeRequest(
                    part="backdrop",
                    screen="onboarding",
                    variant="overlay",
                    size=surface.get_size(),
                    density=density,
                ),
            )
        )
        if not handled:
            veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
            veil.fill((0, 0, 0, 168))
            surface.blit(veil, (0, 0))
        self._panel.draw(surface)
        x = self._panel_rect.x + sp(40)
        y = self._panel_rect.y + sp(25)
        surface.blit(self.ctx.fonts.render(
            self.ctx.t("onboarding_title", "閱讀入門"),
            30,
            theme.accent_warm,
                                           bold=True), (x, y))
        surface.blit(self.ctx.fonts.render(
            self.ctx.t(
                "onboarding_intro",
                "現在只要記住三件事；完整操作隨時可在設定查看。",
            ),
            16,
            theme.text_mute), (x, y + sp(42)))
        for rect, (number, title, desc) in zip(
            self._row_rects,
            self._rows,
            strict=True,
        ):
            number_surf = self.ctx.fonts.render(
                number, 15, theme.accent_alt, bold=True,
            )
            title_surf = self.ctx.fonts.render(
                title, 18, theme.text, bold=True,
            )
            text_x = rect.x + sp(48)
            desc_font = self.ctx.fonts.get(16)
            desc_surf = desc_font.render(
                self._fit_text(
                    desc,
                    desc_font,
                    max(1, rect.right - text_x),
                ),
                True,
                theme.text_mute,
            )
            surface.blit(number_surf, (rect.x, rect.y + sp(3)))
            surface.blit(title_surf, (text_x, rect.y))
            surface.blit(desc_surf, (text_x, rect.y + sp(27)))
            pygame.draw.line(
                surface,
                (*theme.border_soft[:3], 110),
                (text_x, rect.bottom - 1),
                (rect.right, rect.bottom - 1),
                width=1,
            )
        self.start_btn.draw(surface)

    def describe(self) -> dict:
        return {
            "scene": "OnboardingScene",
            "steps": len(self._rows),
            "primary_action": self.start_btn.label,
        }
