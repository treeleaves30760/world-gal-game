"""Character profiles (the Yuzusoft "キャラ" tab).

A left rail of heroines + a scrolling detail panel for the selected one, driven
entirely by the data already in ``content/characters.yaml`` (role, age,
description, persona, voice, likes/dislikes, associated ghost story, secrets).

Secrets are affection-gated: a heroine's ``secrets`` unlock one at a time as the
player crosses her affection thresholds, so the profile deepens as you romance
her — locked secrets show as ``？？？``. Read-only; never mutates state.
"""
from __future__ import annotations

import pygame

from .base import Scene, SceneContext
from ..ui.widgets import Button, Panel, ScrollArea
from ..ui.widgets.label import _wrap_lines


class CharacterProfileScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        self._selected = 0
        self._rail_hits: list[tuple[pygame.Rect, int]] = []

    # ---- data ----------------------------------------------------------
    def _characters(self) -> list:
        npcs = getattr(self.ctx, "npcs", None)
        if npcs is None:
            return []
        try:
            heroines = npcs.heroines()
        except Exception:
            heroines = []
        return heroines

    def _name_color(self, npc) -> tuple:
        raw = getattr(npc, "name_color", None)
        if raw:
            try:
                from ..dialogue.richtext import _parse_color
                c = _parse_color(raw)
                if c:
                    return tuple(c[:3])
            except Exception:
                pass
        return tuple(self.ctx.theme.accent[:3])

    def _affection(self, npc) -> int:
        try:
            return int(self.ctx.state.affection.get(npc.id))
        except Exception:
            return 0

    def _unlocked_secret_count(self, npc) -> int:
        """How many of ``npc.secrets`` are revealed: one fewer than the number
        of affection thresholds reached (so friendship shows none, the route
        bond shows the first, lover shows the rest)."""
        secrets = getattr(npc, "secrets", []) or []
        if not secrets:
            return 0
        aff = self._affection(npc)
        try:
            ca = self.ctx.state.affection.characters.get(npc.id)
            ths = sorted(t.value for t in ca.thresholds) if ca else []
        except Exception:
            ths = []
        reached = sum(1 for v in ths if aff >= v)
        return max(0, min(len(secrets), reached - 1))

    # ---- lifecycle -----------------------------------------------------
    def enter(self, *, on_close=None, **_) -> None:
        self.on_close = on_close
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
        self._rail_w = 230
        detail_x = self._panel_rect.x + self._rail_w + 40
        self._scroll = ScrollArea(
            pygame.Rect(detail_x, self._panel_rect.y + 84,
                        self._panel_rect.right - detail_x - 24,
                        self._panel_rect.height - 84 - 26),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
        )
        self._scroll.set_drawer(self._draw_detail)

    # ---- left rail -----------------------------------------------------
    def _draw_rail(self, surface: pygame.Surface) -> None:
        theme = self.ctx.theme
        self._rail_hits = []
        chars = self._characters()
        x = self._panel_rect.x + 24
        y = self._panel_rect.y + 92
        w = self._rail_w
        for i, npc in enumerate(chars):
            rect = pygame.Rect(x, y, w, 64)
            color = self._name_color(npc)
            selected = (i == self._selected)
            chip = pygame.Surface((w, 64), pygame.SRCALPHA)
            fill = (*color, 70) if selected else (*theme.text_dim[:3], 22)
            pygame.draw.rect(chip, fill, chip.get_rect(),
                             border_radius=theme.radius_m)
            pygame.draw.rect(chip, (*color, 235) if selected
                             else (*theme.border_soft[:3], 120),
                             chip.get_rect(), width=2,
                             border_radius=theme.radius_m)
            # portrait thumb
            if getattr(npc, "portrait", None):
                img = self.ctx.assets.scaled(npc.portrait, (48, 48), fit="cover")
                chip.blit(img, (8, 8))
            name = self.ctx.fonts.render(npc.name, 19, color, bold=True)
            chip.blit(name, (64, 10))
            role = self.ctx.fonts.render((npc.role or "")[:10], 12,
                                         theme.text_mute)
            chip.blit(role, (64, 36))
            surface.blit(chip, (x, y))
            self._rail_hits.append((rect, i))
            y += 72

    # ---- right detail --------------------------------------------------
    def _draw_detail(self, surface: pygame.Surface) -> int:
        theme = self.ctx.theme
        chars = self._characters()
        if not chars:
            msg = self.ctx.fonts.render("沒有角色資料。", 18, theme.text_mute)
            surface.blit(msg, (0, 8))
            return msg.get_height()
        npc = chars[max(0, min(self._selected, len(chars) - 1))]
        color = self._name_color(npc)
        width = surface.get_width() - 16
        y = 0

        # Header: big portrait + name/role/age + affection.
        if getattr(npc, "portrait", None):
            img = self.ctx.assets.scaled(npc.portrait, (132, 132), fit="cover")
            surface.blit(img, (0, 0))
        hx = 150
        surface.blit(self.ctx.fonts.render(npc.name, 32, color, bold=True),
                     (hx, 2))
        meta = npc.role or ""
        if getattr(npc, "age", None):
            meta += f"　·　{npc.age} 歲"
        surface.blit(self.ctx.fonts.render(meta, 15, theme.text_mute), (hx, 46))
        aff = self._affection(npc)
        try:
            lvl = self.ctx.state.affection.level_label(npc.id)
        except Exception:
            lvl = ""
        surface.blit(self.ctx.fonts.render(f"好感度 {aff}　{lvl}", 16,
                                           theme.accent_warm), (hx, 74))
        # affection bar
        bar = pygame.Rect(hx, 102, 240, 10)
        pygame.draw.rect(surface, (*theme.text_dim[:3], 80), bar,
                         border_radius=5)
        frac = max(0.0, min(1.0, aff / 100.0))
        if frac > 0:
            pygame.draw.rect(surface, color,
                             (bar.x, bar.y, int(bar.width * frac), bar.height),
                             border_radius=5)
        y = 150

        def section(title: str, body: str, body_color=None):
            nonlocal y
            if not body:
                return
            surface.blit(self.ctx.fonts.render(title, 16, color, bold=True),
                         (0, y))
            y += 26
            for line in _wrap_lines(body.strip(), self.ctx.fonts.get(16), width):
                surface.blit(self.ctx.fonts.render(
                    line, 16, body_color or theme.text), (0, y))
                y += 24
            y += 12

        section("簡介", getattr(npc, "description", ""))
        section("說話風格", getattr(npc, "voice", "") or getattr(npc, "persona", ""))
        likes = "、".join(getattr(npc, "likes", []) or [])
        dislikes = "、".join(getattr(npc, "dislikes", []) or [])
        if likes:
            section("喜歡", likes, theme.text_mute)
        if dislikes:
            section("討厭", dislikes, theme.text_mute)
        if getattr(npc, "associated_ghost_story", None):
            section("相關異聞", npc.associated_ghost_story, theme.text_mute)

        # Affection-gated secrets.
        secrets = getattr(npc, "secrets", []) or []
        if secrets:
            n = self._unlocked_secret_count(npc)
            surface.blit(self.ctx.fonts.render("祕密", 16, color, bold=True),
                         (0, y))
            y += 26
            for i, sec in enumerate(secrets):
                if i < n:
                    for line in _wrap_lines("· " + sec.strip(),
                                            self.ctx.fonts.get(16), width):
                        surface.blit(self.ctx.fonts.render(
                            line, 16, theme.text), (0, y))
                        y += 24
                else:
                    surface.blit(self.ctx.fonts.render(
                        "· ？？？（提升好感度以解鎖）", 16, theme.text_dim),
                        (0, y))
                    y += 24
            y += 12
        return y

    # ---- input / draw --------------------------------------------------
    def update(self, dt: float, inp) -> None:
        if inp.cancel and self.on_close:
            self.on_close()
            return
        self.close_btn.update(dt, inp)
        self._scroll.update(dt, inp)
        if inp.mouse_clicked:
            for rect, idx in self._rail_hits:
                if rect.collidepoint(inp.mouse_pos):
                    if idx != self._selected:
                        self._selected = idx
                        self._scroll.scroll_y = 0
                    return

    def draw(self, surface: pygame.Surface) -> None:
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 210))
        surface.blit(veil, (0, 0))
        self._panel.draw(surface)
        title = self.ctx.fonts.render(
            self.ctx.localization.t("characters", "角色檔案"),
            self.ctx.config.font_size_header, self.ctx.theme.accent, bold=True)
        surface.blit(title, (self._panel_rect.x + 32, self._panel_rect.y + 26))
        self.close_btn.draw(surface)
        # divider between rail and detail
        dx = self._panel_rect.x + self._rail_w + 32
        pygame.draw.line(surface, self.ctx.theme.border_soft,
                         (dx, self._panel_rect.y + 84),
                         (dx, self._panel_rect.bottom - 24))
        self._draw_rail(surface)
        self._scroll.draw(surface)

    def describe(self) -> dict:
        chars = self._characters()
        return {
            "scene": "CharacterProfileScene",
            "selected": self._selected,
            "characters": [c.id for c in chars],
        }
