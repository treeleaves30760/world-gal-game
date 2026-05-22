"""NVL-mode dialogue surface.

NVL ("novel") mode renders the scene's dialogue as a stacked, full-screen
transcript — every line of the current scene accumulates in a tall panel and
the newest line types out at the bottom — instead of the ADV single-line box.

:class:`NVLBox` is a **drop-in replacement** for
:class:`~world_gal_game.ui.widgets.dialogue_box.DialogueBox`: it exposes the
exact methods :class:`~world_gal_game.scenes.dialogue_scene.DialogueScene`
drives — ``set_line(speaker, text)``, ``force_reveal()``, ``fully_revealed()``,
``update(dt, inp)``, ``draw(surface)`` — so the scene can swap one for the other
with no other code changes.

Two behaviours differ from the ADV box:

- **Accumulation.** Each ``set_line`` appends to a transcript; prior lines stay
  on screen fully revealed and only the newest types out.
- **Scene reset.** The transcript is per-scene, so the scene clears it on a
  scene-id change via :meth:`reset`. (The ADV box has no equivalent because it
  only ever shows one line.)

Older entries reuse the same rich-text parsing as the ADV box but are forced to
full reveal; only the last entry runs the typewriter, mirroring the ADV box's
reveal contract exactly so auto/skip behave identically.
"""
from __future__ import annotations

from dataclasses import dataclass

import pygame

from .widgets.base import Widget
from .widgets.rich_text_view import RichText
from .widgets.panel import Panel
from .fonts import FontRegistry
from .theme import Theme


@dataclass
class _Entry:
    speaker: str | None
    body: RichText


class NVLBox(Widget):
    """Stacked transcript of a scene's dialogue lines (NVL presentation).

    Mirrors :class:`DialogueBox`'s public surface so it is interchangeable in
    :class:`DialogueScene`. The newest entry runs the typewriter; older entries
    are pinned to full reveal.
    """

    def __init__(self, rect: pygame.Rect, *, fonts: FontRegistry, theme: Theme,
                 text_speed: float = 45.0):
        super().__init__(rect)
        self.fonts = fonts
        self.theme = theme
        self.text_speed = text_speed   # chars/sec; 0 = instant
        # A large translucent panel fills most of the screen in NVL mode.
        self.panel = Panel(rect, theme,
                           fill=(*theme.bg_panel[:3], 225),
                           border=theme.border_strong,
                           radius=theme.radius_l,
                           border_width=2)
        self._entries: list[_Entry] = []
        self._hint_t = 0.0
        # Body geometry: a single column inset inside the panel; each entry is
        # laid out into a slice of it at draw time.
        self._pad = theme.pad_l
        self._line_gap = 14            # vertical gap between transcript entries
        self._speaker_size = theme.pad_l + 6
        self._body_size = 22

    # ------------------------------------------------------------------
    # DialogueBox-compatible API
    # ------------------------------------------------------------------

    def set_line(self, speaker: str | None, text: str) -> None:
        """Append a new line to the transcript and start typing it.

        Any previous newest entry is snapped to full reveal first so the
        transcript above the active line is always complete.
        """
        if self._entries:
            self._entries[-1].body.force_reveal()
        body = RichText(self._body_rect(), text,
                        fonts=self.fonts, size=self._body_size,
                        color=self.theme.text, line_spacing=6,
                        text_speed=self.text_speed)
        body.text_speed = self.text_speed
        body.set_text(text, reveal_chars=0)
        self._entries.append(_Entry(speaker=speaker, body=body))
        self._hint_t = 0.0

    def fully_revealed(self) -> bool:
        if not self._entries:
            return True
        return self._entries[-1].body.fully_revealed()

    def force_reveal(self) -> None:
        if self._entries:
            self._entries[-1].body.force_reveal()

    def update(self, dt: float, inp) -> None:
        self._hint_t += dt
        if not self._entries:
            return
        body = self._entries[-1].body
        if self.text_speed <= 0:
            body.force_reveal()
            return
        body.update(dt, inp)

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        self.panel.draw(surface)
        pad = self._pad
        x = self.rect.x + pad
        col_w = self.rect.width - pad * 2
        y = self.rect.y + pad
        bottom = self.rect.bottom - pad
        # Lay out entries top→bottom. Each entry: optional speaker label, then a
        # wrapped body sized to its content. We measure body height by laying it
        # out at the column width. When the stack would overflow the panel the
        # oldest entries scroll off the top (we still draw newest at the bottom).
        rendered: list[tuple[str | None, RichText, int]] = []
        total_h = 0
        for entry in self._entries:
            entry.body.rect = pygame.Rect(x, 0, col_w, self.rect.height)
            speaker_h = (self._speaker_line_height() if entry.speaker else 0)
            body_h = self._body_height(entry.body)
            h = speaker_h + body_h + self._line_gap
            rendered.append((entry.speaker, entry.body, speaker_h))
            total_h += h

        # If the transcript is taller than the panel, drop oldest entries until
        # it fits (keep the active line visible).
        avail = bottom - y
        start = 0
        if total_h > avail:
            acc = 0
            # Walk from the newest backward, keeping as many as fit.
            keep: list[int] = []
            for idx in range(len(rendered) - 1, -1, -1):
                _sp, body, sp_h = rendered[idx]
                h = sp_h + self._body_height(body) + self._line_gap
                if acc + h > avail and keep:
                    break
                acc += h
                keep.append(idx)
            start = min(keep) if keep else len(rendered) - 1

        for idx in range(start, len(rendered)):
            speaker, body, sp_h = rendered[idx]
            if speaker:
                sp = self.fonts.render(speaker, self._speaker_size,
                                       self.theme.accent, bold=True)
                surface.blit(sp, (x, y))
                y += sp_h
            body.rect = pygame.Rect(x, y, col_w, self.rect.height)
            body.draw(surface)
            y += self._body_height(body) + self._line_gap

        # Advance hint on the active (last) entry, like the ADV box.
        if self._entries and self._entries[-1].body.fully_revealed():
            t = self._hint_t * 2
            alpha = 120 + int(80 * abs((t % 2) - 1))
            hint = self.fonts.render("按 Space / 點擊 繼續", 16,
                                     (*self.theme.text_mute[:3], alpha))
            surface.blit(hint, (self.rect.right - hint.get_width() - pad,
                                self.rect.bottom - hint.get_height() - 8))

    # ------------------------------------------------------------------
    # NVL-specific
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear the whole transcript (called on scene change)."""
        self._entries.clear()
        self._hint_t = 0.0

    # Alias so callers reaching for a DialogueBox-style "clear" still work.
    clear = reset

    @property
    def entries(self) -> list[_Entry]:
        return self._entries

    @property
    def line_count(self) -> int:
        return len(self._entries)

    def speakers(self) -> list[str | None]:
        return [e.speaker for e in self._entries]

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------

    def _body_rect(self) -> pygame.Rect:
        pad = self.theme.pad_l
        return pygame.Rect(self.rect.x + pad, self.rect.y + pad,
                           self.rect.width - pad * 2, self.rect.height)

    def _speaker_line_height(self) -> int:
        return self.fonts.get(self._speaker_size, bold=True).get_linesize() + 2

    def _body_height(self, body: RichText) -> int:
        """Total laid-out pixel height of a body's wrapped rows."""
        # RichText lays out lazily; trigger it then sum row heights + spacing.
        body._ensure_layout()
        default_h = self.fonts.get(body.base_size).get_linesize()
        rows = body._rows
        if not rows:
            return default_h
        h = 0
        for ri, row in enumerate(rows):
            band = body._row_bands[ri] if ri < len(body._row_bands) else 0
            row_h = max((r.height for r in row), default=default_h)
            h += band + row_h + body.line_spacing
        return h
