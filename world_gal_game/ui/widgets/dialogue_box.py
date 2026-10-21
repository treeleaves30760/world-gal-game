"""Dialogue text box.

Displays a speaker name (top-left of the panel) plus a wrapped text body
with typewriter reveal. Pressing space / clicking advances:
- first press: reveal the current visual page instantly
- next press: advance to the next page, or the next authored line
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pygame

from .base import Widget
from .rich_text_view import RichText, RichTextPage
from .panel import Panel
from ..fonts import FontRegistry
from ..motion import MotionKind, MotionPolicy, coerce_motion_policy
from ..system_chrome import ChromeRequest
from ..theme import Theme

if TYPE_CHECKING:
    from ..system_chrome import SystemChromeHost


@dataclass(frozen=True)
class DialogueBoxMetrics:
    """Measured ADV reading geometry for one canvas/text-size profile."""

    density: float
    body_lines: int
    body_font_size: int
    speaker_font_size: int
    hint_font_size: int
    body_inset_x: int
    body_top: int
    body_height: int
    line_spacing: int
    footer_gap: int
    footer_height: int
    footer_bottom: int
    nameplate_height: int
    preferred_height: int


class DialogueBox(Widget):
    def __init__(self, rect: pygame.Rect, *, fonts: FontRegistry, theme: Theme,
                 text_speed: float = 45.0, text_scale: float = 1.0,
                 chrome: "SystemChromeHost | None" = None,
                 density: float = 1.0,
                 metrics: DialogueBoxMetrics | None = None,
                 body_font_size: int = 24,
                 speaker_font_size: int = 28,
                 body_lines: int = 3,
                 continue_hint: str = "按 Space / 點擊 繼續",
                 next_page_hint: str = "按 Space / 點擊 下一頁",
                 motion: MotionPolicy | None = None):
        super().__init__(rect)
        self.fonts = fonts
        self.theme = theme
        self.text_speed = text_speed   # chars/sec; 0 = instant
        self.text_scale = text_scale   # accessibility text-size multiplier
        self.chrome = chrome
        self.density = max(0.01, float(density))
        self.metrics = metrics or self.measure(
            fonts,
            theme,
            text_scale=text_scale,
            density=self.density,
            body_font_size=body_font_size,
            speaker_font_size=speaker_font_size,
            body_lines=body_lines,
        )
        self.continue_hint = continue_hint
        self.next_page_hint = next_page_hint
        self.motion = coerce_motion_policy(motion)
        layout_density = self.metrics.density
        self.panel = Panel(rect, theme,
                           fill=(*theme.bg_panel[:3], 240),
                           border=theme.border_strong,
                           radius=max(
                               1,
                               int(round(theme.radius_l * layout_density)),
                           ),
                           border_width=max(
                               1,
                               int(round(2 * layout_density)),
                           ),
                           chrome=chrome,
                           screen="dialogue",
                           role="main_adv",
                           density=self.density)
        # The footer owns its own measured row. Body text can never collide
        # with the continue/page hint, even when the authored line spans pages.
        body_rect = pygame.Rect(
            rect.x + self.metrics.body_inset_x,
            rect.y + self.metrics.body_top,
            max(1, rect.width - self.metrics.body_inset_x * 2),
            max(1, self.metrics.body_height),
        )
        self.body = RichText(body_rect, "",
                             fonts=fonts,
                             size=self.metrics.body_font_size,
                             color=theme.text,
                             line_spacing=self.metrics.line_spacing,
                             text_speed=text_speed,
                             motion=self.motion)
        self.footer_rect = pygame.Rect(
            body_rect.x,
            body_rect.bottom + self.metrics.footer_gap,
            body_rect.width,
            self.metrics.footer_height,
        )
        self.speaker: str | None = None
        # Optional per-speaker name-plate colour (RGB tuple); None = theme accent.
        self.speaker_color: tuple | None = None
        self._hint_t = 0.0
        self._pages: tuple[RichTextPage, ...] = ()
        self._page_index = 0

    @classmethod
    def measure(
        cls,
        fonts: FontRegistry,
        theme: Theme,
        *,
        text_scale: float = 1.0,
        density: float = 1.0,
        body_font_size: int = 24,
        speaker_font_size: int = 28,
        body_lines: int = 3,
    ) -> DialogueBoxMetrics:
        """Measure a stable reading plane from live font metrics.

        FontRegistry owns glyph scaling; ``density`` scales the surrounding
        geometry. Keeping these inputs explicit lets a short canvas retain
        readable 1× text while compacting non-text spacing to 0.8×.
        """
        scale = max(1.0, min(1.4, float(text_scale)))
        layout_density = max(0.01, float(density))
        lines = max(1, int(body_lines))
        body_size = max(1, int(round(body_font_size * scale)))
        speaker_size = max(1, int(round(speaker_font_size * scale)))
        hint_size = 16
        line_spacing = max(
            1,
            int(round(10 * scale * layout_density)),
        )
        body_line_height = fonts.get(body_size).get_linesize()
        body_height = (
            body_line_height * lines
            + line_spacing * max(0, lines - 1)
        )
        body_inset_x = max(
            1,
            int(round((theme.pad_l + 6) * layout_density)),
        )
        body_top = body_inset_x
        footer_gap = max(1, int(round(8 * layout_density)))
        footer_height = fonts.get(hint_size).get_linesize()
        footer_bottom = max(1, int(round(8 * layout_density)))
        name_line_height = fonts.get(speaker_size, bold=True).get_linesize()
        nameplate_height = (
            name_line_height
            + max(2, int(round(16 * layout_density)))
        )
        preferred_height = (
            body_top
            + body_height
            + footer_gap
            + footer_height
            + footer_bottom
        )
        return DialogueBoxMetrics(
            density=layout_density,
            body_lines=lines,
            body_font_size=body_size,
            speaker_font_size=speaker_size,
            hint_font_size=hint_size,
            body_inset_x=body_inset_x,
            body_top=body_top,
            body_height=body_height,
            line_spacing=line_spacing,
            footer_gap=footer_gap,
            footer_height=footer_height,
            footer_bottom=footer_bottom,
            nameplate_height=nameplate_height,
            preferred_height=preferred_height,
        )

    def set_line(self, speaker: str | None, text: str,
                 *, speaker_color: tuple | None = None) -> None:
        self.speaker = speaker
        self.speaker_color = speaker_color
        # Reveal timing now flows through RichText.update(dt): the body drives
        # its own cursor honouring per-span speed and inline waits.
        self.body.text_speed = self.text_speed
        self.body.set_text(text, reveal_chars=0)
        self._pages = self.body.paginate(self.body.rect.height)
        self._page_index = 0
        self._sync_page_window()
        self._hint_t = 0.0

    @property
    def page_index(self) -> int:
        return self._page_index

    @property
    def page_count(self) -> int:
        return max(1, len(self._pages))

    @property
    def current_page(self) -> RichTextPage:
        if not self._pages:
            self._pages = self.body.paginate(self.body.rect.height)
        return self._pages[self._page_index]

    def _sync_page_window(self) -> None:
        page = self.current_page
        self.body.set_row_window(page.start_row, page.end_row)

    def has_more_pages(self) -> bool:
        return self._page_index + 1 < len(self._pages)

    def fully_revealed(self) -> bool:
        if self.body.reveal_chars is None:
            return True
        if self.body.reveal_chars >= self.current_page.end_reveal:
            return True
        # Once the last painted glyph is visible, terminal spaces, tabs and
        # newline reveal units must not consume an otherwise valid advance.
        # RichText keeps pending/active waits authoritative and leaves the raw
        # authored cursor untouched for save/resume.
        return (
            not self.has_more_pages()
            and self.body.terminal_tail_ready(
                self.body.reveal_chars,
                self.current_page.end_reveal,
            )
        )

    def force_reveal(self) -> None:
        # A click / advance during a wait completes the current page and
        # clears waits crossed on this page, without consuming a wait that
        # begins exactly at the following page boundary.
        self.body.force_reveal_to(self.current_page.end_reveal)

    def advance_page(self) -> bool:
        """Move to the next visual page without advancing narrative state."""
        if not self.fully_revealed() or not self.has_more_pages():
            return False
        self._page_index += 1
        self._sync_page_window()
        self.body.set_reveal(self.current_page.start_reveal)
        if self.text_speed <= 0:
            self.force_reveal()
        self._hint_t = 0.0
        return True

    def reveal_last_page(self) -> bool:
        """Collapse remaining visual pages for skip without losing text state."""
        if not self.has_more_pages():
            return False
        self._page_index = len(self._pages) - 1
        self._sync_page_window()
        self.body.force_reveal_to(self.current_page.end_reveal)
        self._hint_t = 0.0
        return True

    def restore_page(
        self,
        page_index: int | None,
        reveal_cursor: int | None,
        plain_cursor: int | None = None,
        *,
        wait_timeline: dict[str, object] | None = None,
    ) -> None:
        """Restore an additive save's page, cursor, and inline-wait clock."""
        if not self._pages:
            self._pages = self.body.paginate(self.body.rect.height)
        raw_cursor = (
            max(0, min(int(reveal_cursor), self.body.total_chars()))
            if reveal_cursor is not None
            else None
        )
        if plain_cursor is not None:
            reflowed_cursor = self.body.reveal_cursor_for_plain(plain_cursor)
            # The raw cursor preserves the exact side of an implicit wrap on
            # the same layout.  Across a resolution/text-size reflow it is
            # retained only when it still maps to the saved authored cursor.
            cursor = (
                raw_cursor
                if (
                    raw_cursor is not None
                    and self.body.plain_cursor_for_reveal(raw_cursor)
                    == plain_cursor
                )
                else reflowed_cursor
            )
        else:
            cursor = raw_cursor or 0
        stored_page = (
            self._pages[page_index]
            if page_index is not None and 0 <= page_index < len(self._pages)
            else None
        )
        timeline_wait_boundary = (
            isinstance(wait_timeline, dict)
            and type(plain_cursor) is int
            and self.body.has_wait_at_plain(plain_cursor)
        )
        if (
            stored_page is not None
            and not timeline_wait_boundary
            and stored_page.start_reveal <= cursor <= stored_page.end_reveal
        ):
            # Preserve the saved side of a page boundary when the current
            # layout still agrees with that ordinal.
            self._page_index = int(page_index)
        else:
            # Derive the page from the cursor when density/wrapping changed.
            # At an exact boundary, legacy saves remain on the previous page;
            # current saves carry an explicit ordinal to disambiguate.
            inferred = 0
            for index, page in enumerate(self._pages):
                if index == 0 or cursor > page.start_reveal:
                    inferred = index
            self._page_index = inferred
        consumed = (
            wait_timeline.get("consumed_plain_positions", [])
            if isinstance(wait_timeline, dict)
            else []
        )
        wait_consumed_at_cursor = (
            type(plain_cursor) is int
            and isinstance(consumed, list)
            and plain_cursor in consumed
        )
        if (
            wait_consumed_at_cursor
            and self._page_index + 1 < len(self._pages)
            and cursor == self.current_page.end_reveal
        ):
            # A wait exactly at a page boundary belongs to the following page.
            # The active/just-completed wait's consumed marker disambiguates it
            # from a pending boundary wait on the previous page after reflow.
            self._page_index += 1
        self._sync_page_window()
        page = self.current_page
        bounded_cursor = (
            page.start_reveal
            if reveal_cursor is None and plain_cursor is None
            else max(page.start_reveal, min(cursor, page.end_reveal))
        )
        self.body.restore_reveal(
            bounded_cursor,
            wait_timeline=wait_timeline,
        )
        self._hint_t = 0.0

    def update(self, dt: float, inp) -> None:
        self._hint_t += dt
        if self.text_speed <= 0:
            self.force_reveal()
            return
        # The limit is enforced inside RichText's wait/speed loop. A large dt
        # therefore cannot cross this page and consume a future page's inline
        # wait before the viewport catches up.
        self.body.update(
            dt,
            inp,
            reveal_limit=self.current_page.end_reveal,
        )

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
        # Speaker-coloured accent bar along the left inner edge — a VN textbox
        # motif that ties the box to the active speaker's colour.
        accent = self.speaker_color or self.theme.accent
        scale = self.metrics.density
        bar_w = max(2, int(round(4 * scale)))
        bar_margin = max(2, int(round(14 * scale)))
        bar = pygame.Surface(
            (bar_w, max(1, self.rect.height - bar_margin * 2)),
            pygame.SRCALPHA,
        )
        bar.fill((*accent[:3], 175))
        surface.blit(
            bar,
            (
                self.rect.x + max(2, int(round(16 * scale))),
                self.rect.y + bar_margin,
            ),
        )
        if self.speaker:
            # Name-plate: a filled accent tab straddling the box's top edge —
            # the classic VN speaker label. Name in light text for contrast.
            name = self.fonts.render(self.speaker,
                                     self.metrics.speaker_font_size,
                                     self.theme.text, bold=True)
            plate_pad_x = max(8, int(round(20 * scale)))
            plate_w = name.get_width() + plate_pad_x * 2
            plate_h = self.metrics.nameplate_height
            px = self.rect.x + max(10, int(round(30 * scale)))
            py = self.rect.y - plate_h // 2
            plate = pygame.Surface((plate_w, plate_h), pygame.SRCALPHA)
            plate_col = self.speaker_color or self.theme.accent
            painted = (
                self.chrome is not None
                and self.chrome.paint(
                    plate,
                    plate.get_rect(),
                    ChromeRequest(
                        part="card",
                        screen="dialogue",
                        variant="nameplate",
                        size=plate.get_size(),
                        density=self.density,
                        emphasis=2,
                    ),
                )
            )
            if not painted:
                plate_fill = (
                    self.theme.bg_panel
                    if high_contrast
                    else (*plate_col[:3], 240)
                )
                pygame.draw.rect(plate, plate_fill,
                                 plate.get_rect(),
                                 border_radius=max(
                                     1,
                                     int(round(self.theme.radius_m * scale)),
                                 ))
                plate_border = (
                    self.theme.border_strong
                    if high_contrast
                    else (*self.theme.text[:3], 50)
                )
                pygame.draw.rect(plate, plate_border,
                                 plate.get_rect(), 2 if high_contrast else 1,
                                 border_radius=max(
                                     1,
                                     int(round(self.theme.radius_m * scale)),
                                 ))
            else:
                # Character identity remains engine-owned even when a pack
                # supplies the neutral nameplate geometry. Shape + colour keep
                # the cue redundant, while the speaker text is drawn last.
                rail_w = max(3, int(round(4 * self.density)))
                pygame.draw.rect(
                    plate,
                    (*plate_col[:3], 230),
                    (0, 0, min(rail_w, plate_w), plate_h),
                    border_radius=min(self.theme.radius_m, rail_w),
                )
            surface.blit(plate, (px, py))
            surface.blit(name, (px + plate_pad_x,
                                py + (plate_h - name.get_height()) // 2))
        # Runtime containment is a last-resort guard for a single authored row
        # taller than the viewport. Normal long lines use lossless pagination.
        # The body outlives the Settings overlay, so refresh its inherited
        # colour from the live theme without reparsing or resetting reveal /
        # inline-wait progress.
        self.body.set_color_policy(
            self.theme.text,
            allow_overrides=not high_contrast,
        )
        previous_clip = surface.get_clip()
        surface.set_clip(previous_clip.clip(self.body.rect))
        self.body.draw(surface)
        surface.set_clip(previous_clip)
        # advance hint
        if self.fully_revealed():
            alpha = self._hint_alpha()
            label = (
                self.next_page_hint
                if self.has_more_pages()
                else self.continue_hint
            )
            if self.page_count > 1:
                label = f"{self.page_index + 1} / {self.page_count} · {label}"
            hint = self.fonts.render(label,
                                     self.metrics.hint_font_size,
                                     (*self.theme.text_mute[:3], alpha))
            hint_pos = (
                self.footer_rect.right - hint.get_width(),
                self.footer_rect.y
                + (self.footer_rect.height - hint.get_height()) // 2,
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
