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
from typing import TYPE_CHECKING

import pygame

from .widgets.base import Widget
from .widgets.rich_text_view import RichText
from .widgets.panel import Panel
from .fonts import FontRegistry
from .motion import MotionKind, MotionPolicy, coerce_motion_policy
from .system_chrome import ChromeRequest
from .theme import Theme

if TYPE_CHECKING:
    from .system_chrome import SystemChromeHost


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
                 text_speed: float = 45.0,
                 chrome: "SystemChromeHost | None" = None,
                 density: float = 1.0,
                 text_scale: float = 1.0,
                 body_font_size: int = 22,
                 speaker_font_size: int = 28,
                 continue_hint: str = "按 Space / 點擊 繼續",
                 motion: MotionPolicy | None = None):
        super().__init__(rect)
        self.fonts = fonts
        self.theme = theme
        self.text_speed = text_speed   # chars/sec; 0 = instant
        self.chrome = chrome
        self.density = max(0.01, float(density))
        self.text_scale = max(1.0, min(1.4, float(text_scale)))
        self.continue_hint = continue_hint
        self.motion = coerce_motion_policy(motion)
        scale = self.density
        # A large translucent panel fills most of the screen in NVL mode.
        self.panel = Panel(rect, theme,
                           fill=(*theme.bg_panel[:3], 225),
                           border=theme.border_strong,
                           radius=max(
                               1,
                               int(round(theme.radius_l * scale)),
                           ),
                           border_width=max(1, int(round(2 * scale))),
                           chrome=chrome,
                           screen="dialogue",
                           role="main_nvl",
                           density=self.density)
        self._entries: list[_Entry] = []
        self._hint_t = 0.0
        # Body geometry: a single column inset inside the panel; each entry is
        # laid out into a slice of it at draw time.
        self._pad = max(1, int(round(theme.pad_l * scale)))
        self._line_gap = max(
            1,
            int(round(14 * scale)),
        )                              # gap between transcript entries
        self._speaker_size = max(
            1,
            int(round(speaker_font_size * self.text_scale)),
        )
        self._body_size = max(
            1,
            int(round(body_font_size * self.text_scale)),
        )
        self._body_line_spacing = max(1, int(round(6 * scale)))
        # DialogueScene may reserve a measured header for semantic sound
        # captions and persistent chrome. The panel stays fixed; only transcript
        # content moves, avoiding private rect mutation and preserving teardown.
        self._header_inset = 0

    # ------------------------------------------------------------------
    # DialogueBox-compatible API
    # ------------------------------------------------------------------

    def set_line(self, speaker: str | None, text: str,
                 *, speaker_color: tuple | None = None) -> None:
        """Append a new line to the transcript and start typing it.

        Any previous newest entry is snapped to full reveal first so the
        transcript above the active line is always complete. ``speaker_color``
        is accepted for call-site parity with the ADV dialogue box; the NVL
        transcript keeps a uniform speaker style, so it is currently unused.
        """
        if self._entries:
            self._entries[-1].body.force_reveal()
        body = RichText(self._body_rect(), text,
                        fonts=self.fonts, size=self._body_size,
                        color=self.theme.text,
                        line_spacing=self._body_line_spacing,
                        text_speed=self.text_speed,
                        motion=self.motion)
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

    def _hint_alpha(self) -> int:
        if not self.motion.allows(MotionKind.CONTINUOUS):
            return 200
        t = self._hint_t * 2
        return 120 + int(80 * abs((t % 2) - 1))

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        high_contrast = (
            getattr(self.theme, "visual_profile", "standard")
            == "high_contrast"
        )
        self.panel.draw(surface)
        pad = self._pad
        content = self.content_rect
        x = content.x
        col_w = content.width
        y = content.y
        bottom = content.bottom
        # Lay out entries top→bottom. Each entry: optional speaker label, then a
        # wrapped body sized to its content. We measure body height by laying it
        # out at the column width. When the stack would overflow the panel the
        # oldest entries scroll off the top (we still draw newest at the bottom).
        rendered: list[tuple[str | None, RichText, int]] = []
        total_h = 0
        for entry in self._entries:
            entry.body.rect = pygame.Rect(x, 0, col_w, content.height)
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
            body.rect = pygame.Rect(x, y, col_w, content.height)
            # Accumulated entries may have been created before the player
            # toggled the live visual profile.  Re-resolve only inherited text
            # colour; explicit rich-text colours and reading progress remain.
            body.set_color_policy(
                self.theme.text,
                allow_overrides=not high_contrast,
            )
            body.draw(surface)
            y += self._body_height(body) + self._line_gap

        # Advance hint on the active (last) entry, like the ADV box.
        if self._entries and self._entries[-1].body.fully_revealed():
            alpha = self._hint_alpha()
            hint = self.fonts.render(self.continue_hint, 16,
                                     (*self.theme.text_mute[:3], alpha))
            hint_pos = (
                self.rect.right - hint.get_width() - pad,
                self.rect.bottom - hint.get_height()
                - max(1, int(round(8 * self.density))),
            )
            if self.chrome is not None:
                hint_pad_x = max(4, int(round(6 * self.density)))
                hint_pad_y = max(2, int(round(3 * self.density)))
                hint_rect = pygame.Rect(hint_pos, hint.get_size()).inflate(
                    hint_pad_x * 2,
                    hint_pad_y * 2,
                )
                self.chrome.paint(
                    surface,
                    hint_rect,
                    ChromeRequest(
                        part="footer",
                        screen="dialogue",
                        variant="advance",
                        size=hint_rect.size,
                        density=self.density,
                    ),
                )
            # Functional copy is always engine-owned and painted after chrome.
            surface.blit(hint, hint_pos)

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

    @property
    def header_inset(self) -> int:
        return self._header_inset

    @property
    def base_content_top(self) -> int:
        """Top of the transcript column before any reserved header."""
        return self.rect.y + self._pad

    @property
    def minimum_content_height(self) -> int:
        """Smallest transcript viewport that retains one readable body row."""
        return max(1, self.fonts.get(self._body_size).get_linesize())

    @property
    def content_rect(self) -> pygame.Rect:
        pad = self._pad
        top = self.rect.y + pad + self._header_inset
        bottom = self.rect.bottom - pad
        return pygame.Rect(
            self.rect.x + pad,
            top,
            max(1, self.rect.width - pad * 2),
            max(1, bottom - top),
        )

    def set_header_inset(self, value: int) -> None:
        """Reserve panel-top space while keeping one readable body line."""
        maximum = max(
            0,
            self.rect.height - self._pad * 2 - self.minimum_content_height,
        )
        inset = max(0, min(int(value), maximum))
        if inset == self._header_inset:
            return
        self._header_inset = inset
        body_rect = self._body_rect()
        for entry in self._entries:
            entry.body.rect = body_rect.copy()

    def _body_rect(self) -> pygame.Rect:
        # Use the same density-scaled inset as draw(). A mismatched width can
        # leave RichText's cached 4K wrapping based on a wider column than the
        # one actually painted.
        return self.content_rect.copy()

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
