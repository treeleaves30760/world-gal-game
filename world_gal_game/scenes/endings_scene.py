"""Endings & completion overlay.

Lists every ending the player can see, grouped by the story ``route_id``
it belongs to (heroine routes are labelled with the heroine's name via
``ctx.npcs.heroines()``; ungrouped endings fall into a "其他" bucket).
Unlocked endings show their title, description and unlock timestamp;
locked-but-visible endings are greyed out; hidden+locked endings never
reach the list (``EndingTracker.visible_to_player`` filters them) and any
that slip through render as "???".

A completion summary sits at the top: scenes read, endings unlocked, CGs
unlocked, plus an overall percentage. The arithmetic lives in the
module-level :func:`compute_completion` so it can be unit-tested without
pygame.
"""
from __future__ import annotations

import pygame

from .base import Scene, SceneContext
from ..ui.widgets import Button, Panel, ScrollArea


_UNGROUPED_KEY = "__ungrouped__"
_UNGROUPED_LABEL = "其他"
_LOCKED_TITLE = "？？？"


def _pct(done: int, total: int) -> float:
    """Percentage 0..100; 0.0 when ``total`` is 0 (never divide by zero)."""
    if total <= 0:
        return 0.0
    return round(100.0 * done / total, 1)


def compute_completion(state, *, total_cgs: int | None = None) -> dict:
    """Completion metrics for a :class:`GameState`-like object.

    Returns a JSON-able dict::

        {
          "scenes":  {"done": int, "total": int, "pct": float},
          "endings": {"done": int, "total": int, "pct": float},
          "cgs":     {"done": int, "total": int, "pct": float} | None,
          "overall_pct": float,
        }

    Each category's ``pct`` is ``100 * done / total`` (0.0 when its total
    is 0, so empty packs never raise ``ZeroDivisionError``). ``cgs`` is
    ``None`` when ``total_cgs`` is unknown (``None``) — there is no
    reliable way to count a pack's full CG roster from state alone, so the
    caller passes it in when it can; otherwise the category is omitted.

    ``overall_pct`` is the mean of the *countable* categories — those with
    a positive total. With nothing countable it is 0.0.
    """
    scenes_done = len(getattr(state.read_log, "scenes", ()) or ())
    scenes_total = len(getattr(state.story, "scenes", ()) or ())

    endings_done = len(getattr(state.endings, "unlocked", ()) or ())
    endings_total = len(state.endings.all())

    cgs_done = len(getattr(state.cg_gallery, "unlocked", ()) or ())

    result: dict = {
        "scenes": {"done": scenes_done, "total": scenes_total,
                   "pct": _pct(scenes_done, scenes_total)},
        "endings": {"done": endings_done, "total": endings_total,
                    "pct": _pct(endings_done, endings_total)},
    }

    countable = [result["scenes"]["pct"]] if scenes_total > 0 else []
    if endings_total > 0:
        countable.append(result["endings"]["pct"])

    if total_cgs is None:
        result["cgs"] = None
    else:
        result["cgs"] = {"done": cgs_done, "total": total_cgs,
                         "pct": _pct(cgs_done, total_cgs)}
        if total_cgs > 0:
            countable.append(result["cgs"]["pct"])

    result["overall_pct"] = (round(sum(countable) / len(countable), 1)
                             if countable else 0.0)
    return result


class EndingsScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True

    # ---- route grouping -------------------------------------------------
    def _route_labels(self) -> dict[str, str]:
        """Map ``route_id`` -> display name using heroine NPCs."""
        labels: dict[str, str] = {}
        npcs = getattr(self.ctx, "npcs", None)
        if npcs is None:
            return labels
        try:
            heroines = npcs.heroines()
        except Exception:
            return labels
        for npc in heroines:
            if npc.route_id:
                labels[npc.route_id] = npc.name
        return labels

    def _grouped_endings(self):
        """Ordered list of ``(label, [endings])`` groups.

        Visible endings are bucketed by ``route_id``. Heroine routes come
        first (sorted by label) and the ungrouped "其他" bucket last.
        """
        tracker = self.ctx.state.endings
        labels = self._route_labels()
        buckets: dict[str, list] = {}
        for ending in tracker.visible_to_player():
            key = ending.route_id or _UNGROUPED_KEY
            buckets.setdefault(key, []).append(ending)

        def _sort_endings(items):
            items.sort(key=lambda e: (e.id not in tracker.unlocked, e.title))
            return items

        groups: list[tuple[str, list]] = []
        named = [k for k in buckets if k != _UNGROUPED_KEY]
        named.sort(key=lambda k: labels.get(k, k))
        for key in named:
            label = labels.get(key, key)
            groups.append((label, _sort_endings(buckets[key])))
        if _UNGROUPED_KEY in buckets:
            groups.append((_UNGROUPED_LABEL,
                           _sort_endings(buckets[_UNGROUPED_KEY])))
        return groups

    # ---- lifecycle ------------------------------------------------------
    def enter(self, *, on_close=None, **_) -> None:
        self.on_close = on_close
        sw, sh = self.ctx.screen_size
        self._panel_rect = pygame.Rect(120, 60, sw - 240, sh - 120)
        self._panel = Panel(self._panel_rect, self.ctx.theme,
                            fill=(*self.ctx.theme.bg_overlay[:3], 235),
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
        # Summary band sits below the title; the scroll list fills the rest.
        summary_top = self._panel_rect.y + 70
        self._summary_h = 56
        self._scroll = ScrollArea(
            pygame.Rect(self._panel_rect.x + 30,
                        summary_top + self._summary_h + 8,
                        self._panel_rect.width - 60,
                        self._panel_rect.height
                        - (self._summary_h + 8) - 100),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
        )
        self._scroll.set_drawer(self._draw_content)
        # Acknowledge any freshly-unlocked endings so toasts clear.
        for ending in self.ctx.state.endings.newly_unlocked():
            self.ctx.state.endings.mark_seen(ending.id)

    # ---- list rendering -------------------------------------------------
    def _draw_content(self, surface: pygame.Surface) -> int:
        tracker = self.ctx.state.endings
        theme = self.ctx.theme
        width = self._scroll.rect.width - 14
        y = 0
        groups = self._grouped_endings()
        if not groups:
            empty = self.ctx.fonts.render(
                "尚無結局", 18, theme.text_mute)
            surface.blit(empty, (0, 0))
            return empty.get_height()

        card_h = 78
        for label, endings in groups:
            header = self.ctx.fonts.render(
                label, 18, theme.accent_warm, bold=True)
            surface.blit(header, (2, y))
            y += header.get_height() + 6
            for ending in endings:
                unlocked = ending.id in tracker.unlocked
                card = pygame.Surface((width, card_h), pygame.SRCALPHA)
                tint = (*theme.accent_warm[:3], 60) if unlocked \
                    else (*theme.text_dim[:3], 30)
                pygame.draw.rect(card, tint, card.get_rect(),
                                 border_radius=theme.radius_m)
                pygame.draw.rect(card,
                                 theme.border if unlocked else theme.border_soft,
                                 card.get_rect(), width=1,
                                 border_radius=theme.radius_m)
                # icon (or sigil chip)
                if unlocked and ending.icon:
                    img = self.ctx.assets.scaled(ending.icon, (54, 54),
                                                 fit="cover")
                    card.blit(img, (12, 12))
                else:
                    chip = pygame.Surface((54, 54), pygame.SRCALPHA)
                    chip_color = (theme.accent_warm if unlocked
                                  else theme.text_dim)
                    pygame.draw.rect(chip, (*chip_color[:3], 60),
                                     chip.get_rect(),
                                     border_radius=theme.radius_s)
                    pygame.draw.rect(chip, (*chip_color[:3], 220),
                                     chip.get_rect(), width=2,
                                     border_radius=theme.radius_s)
                    letter = self.ctx.fonts.render(
                        "終" if unlocked else "?", 28, chip_color, bold=True)
                    chip.blit(letter, ((54 - letter.get_width()) // 2,
                                       (54 - letter.get_height()) // 2))
                    card.blit(chip, (12, 12))
                # title: real when unlocked; hidden+locked → ???; otherwise grey
                if unlocked:
                    title = ending.title
                    title_color = theme.text
                elif ending.hidden:
                    title = _LOCKED_TITLE
                    title_color = theme.text_dim
                else:
                    title = ending.title
                    title_color = theme.text_dim
                t = self.ctx.fonts.render(title, 22, title_color, bold=True)
                card.blit(t, (80, 10))
                desc = ending.description if unlocked else "（尚未解鎖）"
                d = self.ctx.fonts.render(
                    desc[:60], 15,
                    theme.text_mute if unlocked else theme.text_dim)
                card.blit(d, (80, 38))
                if unlocked:
                    ts = tracker.unlocked.get(ending.id, "")[:19] \
                        .replace("T", " ")
                    ts_surf = self.ctx.fonts.render(ts, 12, theme.text_dim)
                    card.blit(ts_surf, (80, 58))
                surface.blit(card, (0, y))
                y += card_h + 8
            y += 6
        return y

    # ---- summary band ---------------------------------------------------
    def _draw_summary(self, surface: pygame.Surface) -> None:
        theme = self.ctx.theme
        comp = compute_completion(self.ctx.state, total_cgs=None)
        x = self._panel_rect.x + 32
        y = self._panel_rect.y + 70
        parts = [
            ("劇情", comp["scenes"]),
            ("結局", comp["endings"]),
        ]
        if comp["cgs"] is not None:
            parts.append(("CG", comp["cgs"]))
        seg = self.ctx.fonts
        cur_x = x
        for name, data in parts:
            label = f"{name} {data['done']}/{data['total']} ({data['pct']}%)"
            surf = seg.render(label, 16, theme.text_mute)
            surface.blit(surf, (cur_x, y))
            cur_x += surf.get_width() + 28
        overall = seg.render(
            f"完成度 {comp['overall_pct']}%", 18, theme.accent_warm, bold=True)
        surface.blit(overall, (x, y + 26))

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
            self.ctx.localization.t("endings", "結局"),
            self.ctx.config.font_size_header,
            self.ctx.theme.accent, bold=True,
        )
        surface.blit(title, (self._panel_rect.x + 32, self._panel_rect.y + 28))
        self._draw_summary(surface)
        self.close_btn.draw(surface)
        self._scroll.draw(surface)

    def describe(self) -> dict:
        comp = compute_completion(self.ctx.state, total_cgs=None)
        return {
            "scene": "EndingsScene",
            "unlocked": list(self.ctx.state.endings.unlocked.keys()),
            "groups": [
                {"label": label, "endings": [e.id for e in endings]}
                for label, endings in self._grouped_endings()
            ],
            "completion": comp,
        }
