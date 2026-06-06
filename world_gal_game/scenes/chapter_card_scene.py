"""Chapter title-card overlay — the eyecatch shown when a chapter begins.

A :class:`ChapterCardScene` is pushed as an overlay (by the app's
``on_chapter_card`` callback, itself triggered by the ``chapter_card``
visual-fx directive that ``set_chapter`` / ``advance_chapter`` queue). It paints
a dark veil over the frozen scene beneath, then the chapter title (large, themed
accent) and a muted subtitle (the route / act), with a thin rule between them —
the same frosted, themed look as the other system overlays.

It mirrors :class:`MoviePlayerScene`: it eats input while up, dismisses on any
advance / cancel key or click (or after a short auto-timeout), and pops itself
via ``on_done`` exactly once (guarded by ``_ended``). It never touches game
state, so it is safe over any scene; an empty title degrades to a generic
"new chapter" label rather than a blank card.
"""
from __future__ import annotations

import pygame

from .base import Scene, SceneContext


class ChapterCardScene(Scene):
    # Auto-dismiss after this many seconds even with no input, so the card never
    # traps the player (matches the "eyecatch then continue" galgame beat).
    AUTO_DISMISS_S = 4.0
    # A small grace window so a key still held from the previous line does not
    # instantly dismiss the card on the very first frame(s).
    GRACE_S = 0.25

    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        self._title = ""
        self._subtitle = ""
        self._on_done = None
        self._ended = False
        self._elapsed = 0.0

    def enter(self, *, title: str = "", subtitle: str = "", on_done=None,
              **_) -> None:
        self._title = (title or "").strip()
        self._subtitle = (subtitle or "").strip()
        self._on_done = on_done
        self._ended = False
        self._elapsed = 0.0

    def _finish(self) -> None:
        if self._ended:
            return
        self._ended = True
        if self._on_done is not None:
            cb, self._on_done = self._on_done, None
            cb()

    def update(self, dt: float, inp) -> None:
        self._elapsed += dt
        # Grace window: ignore input for the first GRACE_S so a key still held
        # from the line that fired set_chapter doesn't instantly dismiss.
        if self._elapsed < self.GRACE_S:
            return
        # Any advance / cancel key or click dismisses; otherwise auto-dismiss.
        if inp.advance_dialogue or inp.cancel or inp.mouse_clicked:
            self._finish()
            return
        if self._elapsed >= self.AUTO_DISMISS_S:
            self._finish()

    def draw(self, surface: pygame.Surface) -> None:
        theme = self.ctx.theme
        sw, sh = surface.get_size()
        # Dark veil over the frozen scene beneath (matches the other overlays).
        veil = pygame.Surface((sw, sh), pygame.SRCALPHA)
        veil.fill((*theme.bg_deep[:3], 224))
        surface.blit(veil, (0, 0))

        cx = sw // 2
        cy = sh // 2
        title = self._title or self.ctx.t("chapter_card_new", "新章節")
        tsurf = self.ctx.fonts.render(
            title, self.ctx.config.font_size_header + 8,
            theme.accent, bold=True)
        # A soft drop-shadow keeps the title legible over a busy frozen scene.
        shadow = self.ctx.fonts.render(
            title, self.ctx.config.font_size_header + 8, (0, 0, 0), bold=True)
        shadow.set_alpha(170)
        ty = cy - tsurf.get_height() - 6
        surface.blit(shadow, (cx - tsurf.get_width() // 2 + 2, ty + 3))
        surface.blit(tsurf, (cx - tsurf.get_width() // 2, ty))

        # Thin rule under the title.
        rule_y = cy + 2
        rule_w = max(120, tsurf.get_width())
        pygame.draw.line(surface, (*theme.accent[:3], 200),
                         (cx - rule_w // 2, rule_y),
                         (cx + rule_w // 2, rule_y), 2)

        if self._subtitle:
            ssurf = self.ctx.fonts.render(
                self._subtitle, self.ctx.config.font_size_menu,
                theme.text_mute)
            surface.blit(ssurf, (cx - ssurf.get_width() // 2, rule_y + 16))

        # Dismiss hint, bottom — same wording the movie overlay uses.
        if not self._ended:
            hint = self.ctx.fonts.render(
                self.ctx.t("chapter_card_hint", "點擊 / 空白鍵 繼續"),
                self.ctx.config.font_size_small,
                (*theme.text_mute[:3], 170))
            surface.blit(hint, (cx - hint.get_width() // 2,
                                sh - hint.get_height() - 28))

    def describe(self) -> dict:
        return {"scene": "ChapterCardScene", "title": self._title}
