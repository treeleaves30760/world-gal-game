"""Relationship-status overlay (關係狀態).

A read-only legibility panel that makes the otherwise-invisible affection state
explicit: affection drives route lock-in, content gates and endings, but the
player never sees the number. This panel lists each tracked character — heroines
(``NPC.is_heroine``) first, emphasised — with:

- the character's display name rendered in its ``name_color`` (the per-character
  name-plate colour commercial VNs use), falling back to the theme accent;
- an affection bar (0..``_BAR_MAX``);
- the current tier label (``AffectionTracker.level_label``, which honours the
  pack's localized affection bands); and, when the character declares named
  ``AffectionThreshold``s, the *next* unreached named threshold ("下一階段
  «在意你» · 還差 N") so the player can read where the relationship is heading.

It is a sibling of the simpler "好感" :class:`AffectionScene` record (kept as-is):
this one is the relationship *status* surface reached from the pause menu next to
the flowchart entry. Pure display — it never mutates state — and degrades to a
plain id + theme accent when no NPC / colour / threshold data is present, so a
bare pack still renders.
"""
from __future__ import annotations

import pygame

from .base import Scene, SceneContext
from ..ui.widgets import Button, Panel, ScrollArea


# Affection bar full-scale. Matches the 0..150 "戀人" top band shared with
# localization.affection_label / AffectionScene so the bar agrees with the tier.
_BAR_MAX = 150.0


class RelationshipsScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        self.on_close = None

    # ---- lifecycle ------------------------------------------------------
    def enter(self, *, on_close=None, **_) -> None:
        self.on_close = on_close
        sw, sh = self.ctx.screen_size
        self._panel_rect = pygame.Rect(120, 80, sw - 240, sh - 160)
        self._panel = Panel(self._panel_rect, self.ctx.theme,
                            fill=(*self.ctx.theme.bg_overlay[:3], 240),
                            border=self.ctx.theme.border_strong,
                            radius=self.ctx.theme.radius_l, border_width=2)
        self.close_btn = Button(
            # Standard system-overlay close button (120x36, inset 16).
            pygame.Rect(self._panel_rect.right - 120 - 16,
                        self._panel_rect.y + 16, 120, 36),
            self.ctx.localization.t("close", "關閉"),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=15, style="ghost",
            on_click=(lambda: self.on_close() if self.on_close else None),
        )
        self._scroll = ScrollArea(
            pygame.Rect(self._panel_rect.x + 30, self._panel_rect.y + 78,
                        self._panel_rect.width - 60,
                        self._panel_rect.height - 108),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
        )
        self._scroll.set_drawer(self._draw_content)

    # ---- ordering / data helpers ---------------------------------------
    def _ordered_characters(self):
        """Tracked characters, heroines first, each as ``(CharacterAffection,
        NPC|None)``. Heroines sort ahead so the relationship surface reads as
        the romance roster; non-heroine tracked NPCs follow."""
        npcs = getattr(self.ctx, "npcs", None)
        rows = []
        for ca in self.ctx.state.affection.characters.values():
            npc = npcs.get(ca.character_id) if npcs is not None else None
            rows.append((ca, npc))
        rows.sort(key=lambda r: (not bool(getattr(r[1], "is_heroine", False)),
                                 (r[1].name if r[1] else r[0].character_id)))
        return rows

    def _name_color(self, npc) -> tuple:
        """RGB for a character's name: parsed ``name_color`` or theme accent."""
        raw = getattr(npc, "name_color", None) if npc is not None else None
        if raw:
            try:
                from ..dialogue.richtext import _parse_color
                col = _parse_color(raw)
                if col:
                    return col
            except Exception:
                pass
        return self.ctx.theme.accent

    def _next_threshold(self, ca, value: int):
        """The lowest NAMED threshold the character has not yet reached, or
        None if all named thresholds are met / none are declared."""
        unreached = [th for th in ca.thresholds
                     if th.name and value < th.value]
        if not unreached:
            return None
        return min(unreached, key=lambda th: th.value)

    # ---- rendering ------------------------------------------------------
    def _draw_content(self, surface: pygame.Surface) -> int:
        theme = self.ctx.theme
        rows = self._ordered_characters()
        y = 0
        card_h = 104
        width = self._scroll.rect.width - 14
        for ca, npc in rows:
            aff = ca.get("affection")
            card = pygame.Surface((width, card_h), pygame.SRCALPHA)
            is_heroine = bool(getattr(npc, "is_heroine", False))
            # Heroines get a warmer card tint + accent border so the romance
            # roster stands out from incidental tracked NPCs.
            fill = (*theme.accent[:3], 26) if is_heroine else (255, 255, 255, 16)
            pygame.draw.rect(card, fill, card.get_rect(),
                             border_radius=theme.radius_m)
            pygame.draw.rect(card,
                             theme.border if is_heroine else theme.border_soft,
                             card.get_rect(), width=1,
                             border_radius=theme.radius_m)
            # portrait chip
            if npc is not None and getattr(npc, "portrait", None):
                try:
                    img = self.ctx.assets.scaled(npc.portrait, (76, 76),
                                                 fit="cover")
                    card.blit(img, (12, 14))
                except Exception:
                    pass
            text_x = 104
            # name in its name_color
            name = npc.name if npc is not None else ca.character_id
            name_surf = self.ctx.fonts.render(name, 23, self._name_color(npc),
                                              bold=True)
            card.blit(name_surf, (text_x, 12))
            # heroine marker + role
            sub_bits = []
            if is_heroine:
                sub_bits.append("女主角")
            role = getattr(npc, "role", "") if npc is not None else ""
            if role:
                sub_bits.append(role)
            if sub_bits:
                sub = self.ctx.fonts.render(" · ".join(sub_bits), 13,
                                            theme.text_mute)
                card.blit(sub, (text_x + name_surf.get_width() + 14, 20))
            # tier label + value
            tier = self.ctx.state.affection.level_label(ca.character_id)
            tlabel = self.ctx.fonts.render(f"{tier} · 好感 {aff}", 16,
                                           theme.accent_warm)
            card.blit(tlabel, (text_x, 44))
            # affection bar
            bar_x, bar_y = text_x, 72
            bar_w, bar_h = width - text_x - 26, 9
            pygame.draw.rect(card, (255, 255, 255, 30),
                             (bar_x, bar_y, bar_w, bar_h), border_radius=4)
            frac = max(0.0, min(1.0, aff / _BAR_MAX))
            fill_w = int(bar_w * frac)
            if fill_w > 0:
                pygame.draw.rect(card, theme.accent,
                                 (bar_x, bar_y, fill_w, bar_h), border_radius=4)
            # next named threshold marker on the bar + caption
            nxt = self._next_threshold(ca, aff)
            if nxt is not None:
                mark_x = bar_x + int(bar_w * max(0.0, min(1.0,
                                                          nxt.value / _BAR_MAX)))
                pygame.draw.rect(card, (*theme.text[:3], 150),
                                 (mark_x, bar_y - 2, 2, bar_h + 4))
                gap = max(0, nxt.value - aff)
                cap = self.ctx.fonts.render(
                    f"下一階段「{nxt.name}」· 還差 {gap}", 13, theme.text_mute)
                card.blit(cap, (text_x, 86))
            else:
                cap = self.ctx.fonts.render("關係已圓滿", 13, theme.good)
                card.blit(cap, (text_x, 86))
            surface.blit(card, (0, y))
            y += card_h + 10
        if not rows:
            empty = self.ctx.fonts.render(
                "（還沒有任何角色被記錄。先在校園裡認識她們吧。）",
                18, theme.text_mute)
            surface.blit(empty, (0, 0))
            return empty.get_height()
        return y

    # ---- input / draw ---------------------------------------------------
    def update(self, dt: float, inp) -> None:
        if inp.cancel and self.on_close:
            self.on_close()
            return
        self.close_btn.update(dt, inp)
        self._scroll.update(dt, inp)

    def draw(self, surface: pygame.Surface) -> None:
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 160))
        surface.blit(veil, (0, 0))
        self._panel.draw(surface)
        title = self.ctx.fonts.render(
            self.ctx.localization.t("relationships", "關係狀態"),
            self.ctx.config.font_size_header,
            self.ctx.theme.accent, bold=True)
        surface.blit(title, (self._panel_rect.x + 32, self._panel_rect.y + 28))
        self.close_btn.draw(surface)
        self._scroll.draw(surface)

    def describe(self) -> dict:
        """Headless dump: per-character tier + next named threshold."""
        out = []
        for ca, npc in self._ordered_characters():
            aff = ca.get("affection")
            nxt = self._next_threshold(ca, aff)
            out.append({
                "character_id": ca.character_id,
                "name": (npc.name if npc is not None else ca.character_id),
                "is_heroine": bool(getattr(npc, "is_heroine", False)),
                "affection": aff,
                "tier": self.ctx.state.affection.level_label(ca.character_id),
                "next_threshold": (
                    {"name": nxt.name, "value": nxt.value} if nxt else None),
            })
        return {"scene": "RelationshipsScene", "characters": out}
