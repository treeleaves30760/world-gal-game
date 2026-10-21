"""Rich-text rendering widget.

``RichText`` renders a parsed :class:`~world_gal_game.dialogue.richtext.RichDocument`
with per-segment color / bold / size, CJK + space-aware wrapping, and a
char-by-char typewriter reveal. It is a drop-in for :class:`WrappedText` in the
dialogue box: it exposes the same ``set_reveal(n)`` / ``total_chars()`` /
``fully_revealed()`` contract, and a tagless string renders pixel-identically to
the old widget (a single default-styled segment).

Layout follows the WrappedText wrapping rule (split on ``\\n`` first, then break
when the accumulated row width exceeds the box) but does it per *run* of
same-style characters, measuring the growing run text with that run's font so
kerning matches a whole-string ``font.render``. Each visual row is a list of
runs; the row's height is the tallest run's linesize, so a ``[size=+N]`` span
heightens only its row. The typewriter reveals run substrings character by
character. Tagless text collapses to one run per row → identical wrapping,
positioning and rendering to WrappedText.

Beyond A3 styling this widget also drives (A4) the typewriter cursor itself via
:meth:`update`, honouring per-span reveal speed (``SegmentStyle.speed``) and
inline wait controls (``RichDocument.controls["waits"]``); (A5) ruby/furigana
small text laid out over a base run with a reserved top band; and (A6) per-glyph
animated effects (``shake`` / ``wave`` / ``fadein``). The static, tagless path is
kept byte-identical: when no run carries an effect or ruby, ``draw`` renders each
run as one whole-string surface exactly as before.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pygame

from .base import Widget
from ..fonts import FontRegistry
from ..motion import MotionKind, MotionPolicy, coerce_motion_policy
from ..text_breaking import hard_break_before_latest, legal_soft_break
from ...dialogue.richtext import RichDocument, SegmentStyle, parse


# Ruby (furigana) sizing: the reading is rendered at this fraction of the base
# run's resolved size, in a band reserved above the row.
_RUBY_SCALE = 0.55
# Gap between the ruby band and the base glyph top (px, scaled with base size).
_RUBY_GAP = 1
# fadein ramp duration (seconds) for a single glyph, measured from the moment it
# is first revealed.
_FADEIN_DUR = 0.25

@dataclass
class _Run:
    """A maximal stretch of one style within one visual row."""
    text: str
    style: SegmentStyle
    font: pygame.font.Font
    color: tuple
    height: int   # font.get_linesize()
    # Plain-char index of this run's first character (A4: maps wait/speed keys,
    # which live in plain-text space, onto the reveal cursor).
    start_plain: int = 0
    # Reveal-unit index of this run's first character (glyphs + implicit row
    # breaks consumed before it). Lets us translate a plain index into the
    # reveal-cursor units that ``set_reveal``/``total_chars`` speak.
    start_reveal: int = 0
    # Lazily pre-rendered per-glyph surfaces, only built for runs that need
    # them (effect != none, or to compose fadein alpha). Never rendered on the
    # per-frame hot path. ``[]`` means "not built / not needed".
    glyph_surfs: list[pygame.Surface] = field(default_factory=list)
    glyph_x: list[int] = field(default_factory=list)   # x offset of each glyph
    # Ruby reading pre-rendered once (A5), or None.
    ruby_surf: pygame.Surface | None = None


@dataclass(frozen=True)
class RichTextPage:
    """One vertically bounded row window inside a laid-out rich document.

    ``start_reveal`` / ``end_reveal`` use the same global reveal-unit space as
    :meth:`RichText.set_reveal`.  A caller can therefore page a long authored
    line without reparsing markup, losing inline waits, or inventing a second
    typewriter timeline.
    """

    start_row: int
    end_row: int
    start_reveal: int
    end_reveal: int
    height: int


class RichText(Widget):
    def __init__(self, rect: pygame.Rect, markup: str = "", *,
                 fonts: FontRegistry, size: int,
                 color=(255, 255, 255), line_spacing: int = 6,
                 reveal_chars: int | None = None,
                 text_speed: float = 0.0,
                 motion: MotionPolicy | None = None):
        super().__init__(rect)
        self.fonts = fonts
        self.base_size = size
        self.base_color = color
        self._allow_color_overrides = True
        self.line_spacing = line_spacing
        self.reveal_chars = reveal_chars
        # Base typewriter rate (chars/sec) used by update(); 0 disables the
        # internal cursor (caller drives set_reveal directly, e.g. tests).
        self.text_speed = text_speed
        self.motion = coerce_motion_policy(motion)
        self._doc: RichDocument = parse(markup, base_color=color, base_size=size)
        self._rows: list[list[_Run]] = []
        self._dirty = True
        # A4 internal reveal state. ``_reveal_f`` is a float cursor in the same
        # reveal-unit space as set_reveal(); ``_wait_left`` is the remaining
        # hold (seconds) at the wait we're currently parked on; ``_waits_done``
        # tracks which wait indices have already been consumed so we don't
        # re-trigger them when the cursor passes again.
        self._reveal_f: float = float(reveal_chars or 0)
        self._wait_left: float = 0.0
        self._waits_done: set[int] = set()
        # A6 internal clock (seconds, advanced by update) + per-glyph first
        # reveal timestamps (keyed by reveal-unit index) for fadein.
        self._clock: float = 0.0
        self._reveal_time: dict[int, float] = {}
        # Optional vertical row window. None renders the complete document,
        # preserving the historical behaviour for every caller except the ADV
        # dialogue viewport that explicitly opts into pagination.
        self._row_window: tuple[int, int] | None = None

    # ----- text + parsing ------------------------------------------------

    def set_markup(self, markup: str, *, reveal_chars: int | None = None) -> None:
        self._doc = parse(markup, base_color=self.base_color,
                          base_size=self.base_size)
        self.reveal_chars = reveal_chars
        self._dirty = True
        self._row_window = None
        self._reset_reveal_state(reveal_chars)

    # WrappedText-compatible alias: DialogueBox historically called set_text.
    def set_text(self, markup: str, *, reveal_chars: int | None = None) -> None:
        self.set_markup(markup, reveal_chars=reveal_chars)

    def set_base_color(self, color: tuple) -> None:
        """Change inherited text colour without resetting reading progress.

        Rich-text segments store only explicit ``[color]`` overrides; their
        inherited colour is resolved while laying out runs.  Invalidating that
        layout is therefore enough for a live accessibility-profile switch,
        and deliberately leaves reveal cursors, inline waits, row windows, and
        parsed markup untouched.
        """
        if color == self.base_color:
            return
        self.base_color = color
        self._dirty = True

    def set_color_policy(
        self,
        color: tuple,
        *,
        allow_overrides: bool,
    ) -> None:
        """Apply a live reading-colour policy without resetting the document.

        High-contrast reading mode suppresses arbitrary authored ``[color]``
        spans because their contrast against the accessibility text plane is
        unknowable.  Bold, size, ruby, waits, reveal speed, and effects remain
        intact, so the mode changes legibility rather than narrative timing.
        """
        allow = bool(allow_overrides)
        if color == self.base_color and allow == self._allow_color_overrides:
            return
        self.base_color = color
        self._allow_color_overrides = allow
        self._dirty = True

    def _reset_reveal_state(self, reveal_chars: int | None) -> None:
        self._reveal_f = float(reveal_chars or 0)
        self._wait_left = 0.0
        self._waits_done.clear()
        self._clock = 0.0
        self._reveal_time.clear()

    def set_reveal(self, n: int | None) -> None:
        # Clipping uses the same layout; no rewrap needed.
        self.reveal_chars = n
        if n is not None:
            self._reveal_f = float(n)

    def export_wait_timeline(self) -> dict[str, object]:
        """Return a layout-independent snapshot of inline-wait progress.

        Wait controls are authored in plain-text space, while the live widget
        tracks them in layout-dependent reveal space.  Persisting authored
        positions keeps the snapshot valid when a load changes resolution,
        font fallback, or text scale.
        """
        self._ensure_layout()
        plain_length = len(self.plain_text())
        waits = {
            position: duration
            for position, duration in self._doc.waits().items()
            if position < plain_length
        }
        consumed = sorted(
            plain_pos
            for plain_pos in waits
            if self._plain_to_reveal(plain_pos) in self._waits_done
        )
        active = None
        if self._wait_left > 0.0:
            if not math.isfinite(self._wait_left):
                raise ValueError("active rich-text wait must be finite")
            reveal_pos = int(self._reveal_f)
            candidates = [
                plain_pos
                for plain_pos in waits
                if (
                    self._plain_to_reveal(plain_pos) == reveal_pos
                    and reveal_pos in self._waits_done
                )
            ]
            if len(candidates) != 1:
                raise RuntimeError(
                    "active rich-text wait has no unique authored position"
                )
            active = {
                "plain_position": candidates[0],
                "remaining_seconds": float(self._wait_left),
            }
        return {
            "consumed_plain_positions": consumed,
            "active": active,
        }

    def has_wait_at_plain(self, plain_position: int) -> bool:
        """Return whether a wait can fire before this authored character."""
        return (
            type(plain_position) is int
            and 0 <= plain_position < len(self.plain_text())
            and plain_position in self._doc.waits()
        )

    def restore_reveal(
        self,
        n: int,
        *,
        wait_timeline: dict[str, object] | None = None,
    ) -> None:
        """Restore a persisted cursor without replaying elapsed animation.

        A current save supplies ``wait_timeline`` so an in-progress inline
        pause resumes with its exact remaining duration.  Legacy saves omit
        it; for those, waits strictly before the cursor are treated as elapsed
        while a wait exactly at the cursor remains pending, matching the old
        v1 behaviour.  Per-glyph fade clocks are still settled fully opaque.
        """
        self._ensure_layout()
        bounded = max(0, min(int(n), self.total_chars()))
        self.reveal_chars = bounded
        self._reveal_f = float(bounded)
        self._wait_left = 0.0
        self._waits_done.clear()
        plain_length = len(self.plain_text())
        waits = {
            position: duration
            for position, duration in self._doc.waits().items()
            if position < plain_length
        }
        if wait_timeline is None:
            for plain_pos in waits:
                reveal_pos = self._plain_to_reveal(plain_pos)
                # A wait exactly at the cursor may be the first event on the
                # next visual page (or an active wait from a legacy save).
                if reveal_pos < bounded:
                    self._waits_done.add(reveal_pos)
        else:
            if set(wait_timeline) != {"consumed_plain_positions", "active"}:
                raise ValueError("invalid rich-text wait timeline shape")
            consumed = wait_timeline.get("consumed_plain_positions")
            if not isinstance(consumed, list) or any(
                    type(position) is not int for position in consumed):
                raise ValueError("invalid consumed rich-text wait positions")
            if consumed != sorted(set(consumed)):
                raise ValueError(
                    "consumed rich-text wait positions must be canonical"
                )
            known_positions = set(waits)
            if any(position not in known_positions for position in consumed):
                raise ValueError("unknown consumed rich-text wait position")
            plain_cursor = self.plain_cursor_for_reveal(bounded)
            if any(position > plain_cursor for position in consumed):
                raise ValueError("consumed rich-text wait is ahead of cursor")
            if any(
                    position not in consumed
                    for position in waits
                    if position < plain_cursor):
                raise ValueError("elapsed rich-text wait is not consumed")
            self._waits_done.update(
                self._plain_to_reveal(position) for position in consumed
            )

            active = wait_timeline.get("active")
            if active is not None:
                if (
                    not isinstance(active, dict)
                    or set(active) != {"plain_position", "remaining_seconds"}
                ):
                    raise ValueError("invalid active rich-text wait shape")
                plain_position = active.get("plain_position")
                remaining = active.get("remaining_seconds")
                if (
                    type(plain_position) is not int
                    or type(remaining) not in (int, float)
                ):
                    raise ValueError("invalid active rich-text wait values")
                remaining_f = float(remaining)
                duration = waits.get(plain_position)
                if (
                    duration is None
                    or plain_position != plain_cursor
                    or plain_position not in consumed
                    or not math.isfinite(remaining_f)
                    or remaining_f <= 0.0
                    or remaining_f > duration
                ):
                    raise ValueError("active rich-text wait is inconsistent")
                self._wait_left = remaining_f
        stamp = self._clock - _FADEIN_DUR
        for reveal_pos in range(bounded):
            self._reveal_time[reveal_pos] = stamp

    def plain_text(self) -> str:
        return self._doc.plain_text()

    def plain_cursor_for_reveal(self, reveal_pos: int | None = None) -> int:
        """Translate a layout-dependent reveal cursor to plain-text space.

        Implicit wrap breaks are reveal units but not authored characters.
        Persisting this companion cursor lets a save resume at the same glyph
        after switching between 720p, 1080p, 4K, or text-size profiles.
        """
        self._ensure_layout()
        position = (
            self.total_chars()
            if reveal_pos is None and self.reveal_chars is None
            else int(self.reveal_chars or 0)
            if reveal_pos is None
            else int(reveal_pos)
        )
        position = max(0, min(position, self.total_chars()))
        plain_length = len(self.plain_text())
        previous_plain = 0
        previous_reveal = 0
        for row in self._rows:
            for run in row:
                if position <= run.start_reveal:
                    # Gaps between runs are either authored newlines (one
                    # plain and reveal unit each) or an implicit wrap (a
                    # reveal unit with no authored character).  Interpolating
                    # from the previous run avoids jumping across explicit
                    # blank lines while keeping wrap-only gaps on the same
                    # plain-text cursor.
                    gap_plain = max(0, run.start_plain - previous_plain)
                    gap_offset = max(0, position - previous_reveal)
                    return min(
                        run.start_plain,
                        previous_plain + min(gap_offset, gap_plain),
                    )
                run_end_reveal = run.start_reveal + len(run.text)
                if position < run_end_reveal:
                    return run.start_plain + (position - run.start_reveal)
                previous_plain = run.start_plain + len(run.text)
                previous_reveal = run_end_reveal

        # The document tail can consist entirely of explicit newlines and
        # therefore have no run to anchor it (for example ``"甲\n\n"`` or
        # ``"\n"``).  Map those remaining reveal units one-for-one.
        tail_offset = max(0, position - previous_reveal)
        return min(
            plain_length,
            previous_plain
            + min(tail_offset, max(0, plain_length - previous_plain)),
        )

    def reveal_cursor_for_plain(self, plain_pos: int) -> int:
        """Translate a persisted authored-character cursor into this layout."""
        # Public callers may ask immediately after construction/set_text.
        # Wrapping contributes reveal units, so an unlaid-out empty _rows list
        # would silently return the plain cursor and resume too early.
        self._ensure_layout()
        plain_length = len(self.plain_text())
        bounded = max(0, min(int(plain_pos), plain_length))
        if bounded == plain_length:
            return self.total_chars()
        return max(0, min(self._plain_to_reveal(bounded), self.total_chars()))

    # ----- layout --------------------------------------------------------

    def _font_for(self, style: SegmentStyle) -> pygame.font.Font:
        return self.fonts.get(style.resolved_size(self.base_size),
                              bold=style.bold)

    def _ensure_layout(self) -> None:
        if not self._dirty:
            return
        max_width = self.rect.width
        rows: list[list[_Run]] = []
        # Each entry is (glyph, style, font, colour, authored plain index).
        # Keeping glyph identity until a row is committed lets overflow move a
        # complete Latin word across style/control boundaries without split(),
        # strip(), or any loss of save/reveal cursor coordinates.
        glyph_row: list[
            tuple[str, SegmentStyle, pygame.font.Font, tuple, int]
        ] = []
        plain_idx = 0
        reveal_idx = 0

        def row_width(
            glyphs: list[
                tuple[str, SegmentStyle, pygame.font.Font, tuple, int]
            ],
        ) -> int:
            width = 0
            run_text = ""
            run_style: SegmentStyle | None = None
            run_font: pygame.font.Font | None = None
            for char, style, font, _color, _index in glyphs:
                if run_style is not None and style != run_style:
                    assert run_font is not None
                    width += run_font.size(run_text)[0]
                    run_text = ""
                run_style = style
                run_font = font
                run_text += char
            if run_text and run_font is not None:
                width += run_font.size(run_text)[0]
            return width

        def finish_row(
            glyphs: list[
                tuple[str, SegmentStyle, pygame.font.Font, tuple, int]
            ],
            *,
            implicit_break: bool,
        ) -> None:
            nonlocal reveal_idx
            row: list[_Run] = []
            index = 0
            while index < len(glyphs):
                char, style, font, color, start_plain = glyphs[index]
                text = char
                index += 1
                while index < len(glyphs) and glyphs[index][1] == style:
                    text += glyphs[index][0]
                    index += 1
                row.append(
                    _Run(
                        text,
                        style,
                        font,
                        color,
                        font.get_linesize(),
                        start_plain=start_plain,
                        start_reveal=reveal_idx,
                    )
                )
                reveal_idx += len(text)
            rows.append(row)
            # Every row boundary (explicit \n or wrap) costs one reveal unit,
            # matching WrappedText's glyphs + (rows-1) accounting.
            if implicit_break:
                reveal_idx += 1

        def latest_soft_break(
            glyphs: list[
                tuple[str, SegmentStyle, pygame.font.Font, tuple, int]
            ],
        ) -> int | None:
            # Boundary ``index`` divides glyphs[:index] / glyphs[index:].  The
            # full row is already too wide, so its terminal boundary cannot
            # resolve overflow and is deliberately excluded.
            for index in range(len(glyphs) - 1, 0, -1):
                if legal_soft_break(
                    glyphs[index - 1][0],
                    glyphs[index][0],
                ):
                    return index
            return None

        for seg in self._doc.segments:
            style = seg.style
            font = self._font_for(style)
            color = (
                style.color
                if self._allow_color_overrides and style.color is not None
                else self.base_color
            )
            for ch in seg.text:
                if ch == "\n":
                    finish_row(glyph_row, implicit_break=True)
                    glyph_row = []
                    plain_idx += 1   # the newline is a plain char
                    continue
                glyph_row.append((ch, style, font, color, plain_idx))
                plain_idx += 1
                while (
                    max_width > 0
                    and len(glyph_row) > 1
                    and row_width(glyph_row) > max_width
                ):
                    boundary = latest_soft_break(glyph_row)
                    if boundary is None:
                        # One unspaced token is wider than the row.  Preserve
                        # every glyph and fall back to the historical boundary
                        # immediately before the newest glyph.
                        boundary = hard_break_before_latest(
                            "".join(glyph[0] for glyph in glyph_row),
                        )
                    finish_row(
                        glyph_row[:boundary],
                        implicit_break=True,
                    )
                    glyph_row = glyph_row[boundary:]
        # Final row never adds a trailing break (matches WrappedText [""]).
        finish_row(glyph_row, implicit_break=False)
        self._rows = rows
        # reveal_idx now equals glyphs + (rows-1) (the final row added no break);
        # total_chars() recomputes this, so we don't cache it here.
        self._build_ruby_band()
        self._dirty = False

    def _build_ruby_band(self) -> None:
        """Compute, per row, the ruby band height (0 when no run has ruby)."""
        bands: list[int] = []
        for row in self._rows:
            band = 0
            for run in row:
                if run.style.ruby:
                    base_sz = run.style.resolved_size(self.base_size)
                    ruby_sz = max(6, int(base_sz * _RUBY_SCALE))
                    rf = self.fonts.get(ruby_sz)
                    band = max(band, rf.get_linesize() + _RUBY_GAP)
            bands.append(band)
        self._row_bands = bands

    # ----- reveal contract (mirrors WrappedText) -------------------------

    def total_chars(self) -> int:
        self._ensure_layout()
        glyphs = sum(len(r.text) for row in self._rows for r in row)
        # +1 per implicit row break, matching WrappedText's accounting so the
        # typewriter pacing and force_reveal math are unchanged.
        return glyphs + max(0, len(self._rows) - 1)

    def _row_height(self, index: int) -> int:
        """Return one laid-out row's visible height, excluding row spacing."""
        self._ensure_layout()
        default_h = self.fonts.get(self.base_size).get_linesize()
        row = self._rows[index]
        band = self._row_bands[index] if index < len(self._row_bands) else 0
        return band + max((run.height for run in row), default=default_h)

    def _row_reveal_units(self, index: int) -> int:
        """Reveal units owned by one row, including its implicit line break."""
        self._ensure_layout()
        glyphs = sum(len(run.text) for run in self._rows[index])
        return glyphs + (1 if index < len(self._rows) - 1 else 0)

    @staticmethod
    def _row_has_visible_glyph(row: list[_Run]) -> bool:
        """Return whether a row paints anything beyond terminal whitespace.

        Newlines are represented by row boundaries rather than runs. Unicode
        whitespace retains its authored cursor units, but does not make a
        trailing row visible. A ruby annotation is visible even when its base
        happens to be whitespace, so it deliberately keeps the row in page
        geometry.
        """
        return any(
            bool(run.style.ruby)
            or any(
                not char.isspace()
                for char in run.text
            )
            for run in row
        )

    def terminal_visible_end_reveal(self) -> int:
        """Return the cursor immediately after the final visible glyph.

        This does not alter the document's reveal space. Terminal spaces,
        tabs, and newline row boundaries remain addressable for save/resume and
        inline-wait timing; the value only identifies when the player has seen
        all painted content.
        """
        self._ensure_layout()
        for row in reversed(self._rows):
            for run in reversed(row):
                if run.style.ruby and run.text:
                    return run.start_reveal + len(run.text)
                for index in range(len(run.text) - 1, -1, -1):
                    if not run.text[index].isspace():
                        return run.start_reveal + index + 1
        return 0

    def terminal_tail_ready(
        self,
        reveal_cursor: int,
        end_reveal: int,
    ) -> bool:
        """Return whether an invisible terminal tail may stop gating input.

        Pending or active waits in that tail remain authoritative. Consumed
        waits and whitespace reveal units stay in their original cursor space;
        callers merely gain a visual-completion predicate and never rewrite
        authored progress.
        """
        self._ensure_layout()
        document_end = self.total_chars()
        bounded_end = max(0, min(int(end_reveal), document_end))
        if bounded_end != document_end:
            return False
        bounded_cursor = max(0, min(int(reveal_cursor), document_end))
        visible_end = self.terminal_visible_end_reveal()
        if bounded_cursor < visible_end:
            return False
        if self._wait_left > 0.0:
            return False
        for plain_position in self._doc.waits():
            wait_reveal = self._plain_to_reveal(plain_position)
            if (
                visible_end <= wait_reveal < document_end
                and wait_reveal not in self._waits_done
            ):
                return False
        return True

    def paginate(self, max_height: int) -> tuple[RichTextPage, ...]:
        """Partition laid-out rows into stable pages no taller than ``max_height``.

        A single unusually tall ruby/effect row is retained as its own page;
        drawing is still clipped by the owning viewport.  Normal rows include
        spacing only *between* rows, matching their visible composition.
        """
        self._ensure_layout()
        if not self._rows:
            return (
                RichTextPage(
                    start_row=0,
                    end_row=0,
                    start_reveal=0,
                    end_reveal=0,
                    height=0,
                ),
            )

        limit = max(1, int(max_height))
        # A YAML literal block normally contributes one terminal ``\n`` and a
        # hand-edited/localised value may leave spaces or tabs after it. Layout
        # retains every authored character for reveal/save cursors, but those
        # synthetic terminal whitespace rows must not become blank visual
        # pages. Keep matching rows inside the document; only trim the tail
        # from rendered page geometry. Internal blank rows remain untouched.
        render_row_count = len(self._rows)
        while (
            render_row_count > 1
            and not self._row_has_visible_glyph(
                self._rows[render_row_count - 1],
            )
        ):
            render_row_count -= 1
        pages: list[RichTextPage] = []
        reveal_at_row = 0
        start_row = 0
        start_reveal = 0
        page_height = 0

        for index in range(render_row_count):
            row_height = self._row_height(index)
            candidate = (
                row_height
                if index == start_row
                else page_height + self.line_spacing + row_height
            )
            if index > start_row and candidate > limit:
                pages.append(
                    RichTextPage(
                        start_row=start_row,
                        end_row=index,
                        start_reveal=start_reveal,
                        end_reveal=reveal_at_row,
                        height=page_height,
                    ),
                )
                start_row = index
                start_reveal = reveal_at_row
                page_height = row_height
            else:
                page_height = candidate
            reveal_at_row += self._row_reveal_units(index)

        pages.append(
            RichTextPage(
                start_row=start_row,
                end_row=render_row_count,
                start_reveal=start_reveal,
                end_reveal=self.total_chars(),
                height=page_height,
            ),
        )
        return tuple(pages)

    def set_row_window(self, start: int, end: int) -> None:
        """Render only ``[start, end)`` rows while retaining global reveal state."""
        self._ensure_layout()
        row_count = len(self._rows)
        bounded_start = max(0, min(int(start), row_count))
        bounded_end = max(bounded_start, min(int(end), row_count))
        self._row_window = (bounded_start, bounded_end)

    def clear_row_window(self) -> None:
        self._row_window = None

    def fully_revealed(self) -> bool:
        if self.reveal_chars is None:
            return True
        return self.reveal_chars >= self.total_chars()

    # ----- A4: wait/speed-aware typewriter -------------------------------

    def _plain_to_reveal(self, plain: int) -> int:
        """Map a plain-text char index to its reveal-unit position.

        Wait controls are keyed in plain-text space (no implicit row breaks);
        the reveal cursor counts breaks. This walks runs to translate.
        """
        bounded = max(0, min(int(plain), len(self.plain_text())))
        previous_plain = 0
        previous_reveal = 0
        for row in self._rows:
            for run in row:
                if bounded < run.start_plain:
                    # Only authored newlines consume plain positions between
                    # runs.  They also consume one reveal unit each.
                    return previous_reveal + min(
                        bounded - previous_plain,
                        run.start_reveal - previous_reveal,
                    )
                if bounded == run.start_plain:
                    # Prefer the downstream side of an implicit wrap.  Saves
                    # also retain the raw reveal cursor, which disambiguates
                    # the exact side when the current layout is unchanged.
                    return run.start_reveal
                end_plain = run.start_plain + len(run.text)
                if bounded < end_plain:
                    return run.start_reveal + (bounded - run.start_plain)
                previous_plain = end_plain
                previous_reveal = run.start_reveal + len(run.text)

        # As above, a trailing authored newline has no following run.
        return min(
            self.total_chars(),
            previous_reveal + max(0, bounded - previous_plain),
        )

    def _speed_at(self, reveal_pos: int) -> float:
        """Reveal rate (chars/sec) for the glyph at ``reveal_pos``.

        Uses the covering run's per-span speed when set, else the base speed.
        """
        for row in self._rows:
            for run in row:
                if run.start_reveal <= reveal_pos < run.start_reveal + len(run.text):
                    if run.style.speed is not None and run.style.speed > 0:
                        return run.style.speed
                    return self.text_speed
        return self.text_speed

    def update(
        self,
        dt: float,
        inp=None,
        *,
        reveal_limit: int | None = None,
    ) -> None:
        """Advance the internal typewriter clock and reveal cursor.

        Honours per-span speed and inline waits. Disabled (no auto-advance)
        when ``text_speed <= 0`` and no per-span speed is in play — callers can
        still drive reveal explicitly via ``set_reveal``. ``inp`` is accepted
        for Widget.update signature symmetry and ignored.
        """
        self._ensure_layout()
        self._clock += dt
        if self.reveal_chars is None:
            return
        document_total = self.total_chars()
        total = (
            document_total
            if reveal_limit is None
            else max(0, min(int(reveal_limit), document_total))
        )
        if self.reveal_chars >= total:
            self._record_reveal_times(total)
            return
        if self.text_speed <= 0 and not self._any_span_speed():
            return

        remaining = dt
        # Honour an active wait first.
        if self._wait_left > 0.0:
            if remaining < self._wait_left:
                self._wait_left -= remaining
                self._commit_reveal()
                return
            remaining -= self._wait_left
            self._wait_left = 0.0

        waits = self._doc.waits()
        # Advance the float cursor, stopping at each wait boundary.
        guard = 0
        while remaining > 0.0 and self._reveal_f < total and guard < 100000:
            guard += 1
            cur = int(self._reveal_f)
            # Is there an unconsumed wait at the current cursor position?
            wait_dur = self._wait_due_at(cur, waits)
            if wait_dur is not None:
                self._waits_done.add(cur)
                self._wait_left = wait_dur
                if remaining < self._wait_left:
                    self._wait_left -= remaining
                    remaining = 0.0
                    break
                remaining -= self._wait_left
                self._wait_left = 0.0
                continue
            rate = self._speed_at(cur)
            if rate <= 0:
                # No rate available -> reveal instantly to the next wait/end.
                nxt = self._next_wait_reveal_pos(cur, waits, total)
                self._reveal_f = float(nxt)
                continue
            # How far can we advance before the next wait boundary?
            nxt = self._next_wait_reveal_pos(cur, waits, total)
            step = remaining * rate
            new_f = self._reveal_f + step
            if new_f >= nxt:
                # Consume only the time to reach nxt, loop to handle the wait.
                used = (nxt - self._reveal_f) / rate
                remaining -= used
                self._reveal_f = float(nxt)
            else:
                self._reveal_f = new_f
                remaining = 0.0
        self._commit_reveal()

    def _any_span_speed(self) -> bool:
        for row in self._rows:
            for run in row:
                if run.style.speed is not None and run.style.speed > 0:
                    return True
        return False

    def _wait_due_at(self, reveal_pos: int, waits: dict[int, float]) -> float | None:
        """Return the wait duration owed *before* revealing ``reveal_pos``."""
        if reveal_pos in self._waits_done:
            return None
        for p, dur in waits.items():
            if self._plain_to_reveal(p) == reveal_pos:
                return dur
        return None

    def _next_wait_reveal_pos(self, cur: int, waits: dict[int, float],
                              total: int) -> int:
        """Next reveal position (> cur) that carries an unconsumed wait, or total."""
        best = total
        for p, _dur in waits.items():
            rp = self._plain_to_reveal(p)
            if rp > cur and rp not in self._waits_done and rp < best:
                best = rp
        return best

    def _commit_reveal(self) -> None:
        n = int(self._reveal_f)
        total = self.total_chars()
        if n > total:
            n = total
        if n != self.reveal_chars:
            self.reveal_chars = n
        self._record_reveal_times(n)

    def _record_reveal_times(self, up_to: int) -> None:
        """Stamp the clock time at which each newly revealed unit appeared."""
        for i in range(up_to):
            if i not in self._reveal_time:
                self._reveal_time[i] = self._clock

    def force_reveal(self) -> None:
        """Reveal everything immediately and clear any pending waits.

        A click / advance always completes the line instantly: the cursor jumps
        to total, the active wait is dropped, and all waits are marked consumed
        so a subsequent update() won't re-park.
        """
        self.force_reveal_to(self.total_chars())

    def force_reveal_to(self, limit: int) -> None:
        """Reveal through ``limit`` and clear waits already crossed.

        Unlike :meth:`force_reveal`, this keeps waits at the next page boundary
        pending.  It is the primitive used by the ADV dialogue paginator.
        """
        self._ensure_layout()
        total = self.total_chars()
        bounded = max(0, min(int(limit), total))
        self.reveal_chars = bounded
        self._reveal_f = float(bounded)
        self._wait_left = 0.0
        # A wait exactly at a non-final page boundary belongs to the next page.
        for p in self._doc.waits():
            reveal_pos = self._plain_to_reveal(p)
            if reveal_pos < bounded or bounded == total:
                self._waits_done.add(reveal_pos)
        # A forced reveal completes instantly: stamp every unit's reveal time
        # in the past so any fadein is already fully opaque (no lingering ramp).
        stamp = self._clock - _FADEIN_DUR
        for i in range(bounded):
            self._reveal_time[i] = stamp

    # ----- per-glyph rendering helpers (A5/A6) ---------------------------

    def _needs_glyphs(self, run: _Run) -> bool:
        eff = run.style.effect
        return bool((eff and eff != "none") or run.style.ruby)

    def _ensure_glyphs(self, run: _Run) -> None:
        """Pre-render each glyph surface once (layout time, never per-frame)."""
        if run.glyph_surfs:
            return
        surfs: list[pygame.Surface] = []
        xs: list[int] = []
        x = 0
        for ch in run.text:
            s = run.font.render(ch, True, run.color)
            surfs.append(s)
            xs.append(x)
            x += run.font.size(ch)[0]
        run.glyph_surfs = surfs
        run.glyph_x = xs

    def _ensure_ruby(self, run: _Run) -> None:
        if run.ruby_surf is not None or not run.style.ruby:
            return
        base_sz = run.style.resolved_size(self.base_size)
        ruby_sz = max(6, int(base_sz * _RUBY_SCALE))
        rf = self.fonts.get(ruby_sz)
        run.ruby_surf = rf.render(run.style.ruby, True, run.color)

    def _glyph_offset_alpha(self, run: _Run, gi: int, reveal_unit: int
                            ) -> tuple[int, int, int]:
        """Return (dx, dy, alpha) for an animated glyph (already revealed)."""
        eff = run.style.effect
        if (
            eff
            and eff != "none"
            and not self.motion.allows(MotionKind.CONTINUOUS)
        ):
            # Preserve the authored text and reveal cursor, but settle every
            # decorative glyph effect into a fully readable static frame.
            return (0, 0, 255)
        t = self._clock
        dx = dy = 0
        alpha = 255
        if eff == "shake":
            # Deterministic ~1.5px jitter from the clock and glyph index.
            dx = int(round(math.sin(t * 30.0 + gi * 1.3) * 1.5))
            dy = int(round(math.cos(t * 27.0 + gi * 1.7) * 1.5))
        elif eff == "wave":
            dy = int(round(math.sin(t * 4.0 + gi * 0.5) * 4.0))
        elif eff == "fadein":
            t0 = self._reveal_time.get(reveal_unit)
            if t0 is None:
                alpha = 0
            else:
                frac = (self._clock - t0) / _FADEIN_DUR
                if frac < 0.0:
                    frac = 0.0
                elif frac > 1.0:
                    frac = 1.0
                alpha = int(frac * 255)
        return dx, dy, alpha

    # ----- draw ----------------------------------------------------------

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        self._ensure_layout()
        remaining = self.reveal_chars if self.reveal_chars is not None else None
        default_h = self.fonts.get(self.base_size).get_linesize()
        y = self.rect.y
        start_row, end_row = (
            self._row_window
            if self._row_window is not None
            else (0, len(self._rows))
        )
        for ri, row in enumerate(self._rows):
            if ri < start_row:
                if remaining is not None:
                    remaining -= self._row_reveal_units(ri)
                continue
            if ri >= end_row:
                break
            if remaining is not None and remaining <= 0:
                break
            band = self._row_bands[ri] if ri < len(self._row_bands) else 0
            row_h = max((r.height for r in row), default=default_h)
            text_y = y + band   # base glyphs sit below the reserved ruby band
            x = self.rect.x
            for run in row:
                # Clip this run to however many chars remain to reveal.
                if remaining is None:
                    shown_n = len(run.text)
                else:
                    if remaining <= 0:
                        break
                    shown_n = min(len(run.text), remaining)
                    remaining -= shown_n
                run_top = text_y + (row_h - run.height)
                if shown_n > 0:
                    eff = run.style.effect
                    if (eff and eff != "none") or run.style.ruby:
                        self._draw_run_dynamic(surface, run, shown_n, x, run_top)
                    else:
                        # Fast path: byte-identical whole-string render/blit.
                        shown = run.text[:shown_n]
                        surf = run.font.render(shown, True, run.color)
                        surface.blit(surf, (x, run_top))
                # Advance x by the FULL run width (revealed or not) so the next
                # run lands in its final position even mid-typewriter.
                x += run.font.size(run.text)[0]
            if remaining is not None:
                remaining -= 1   # implicit newline between rows
            y += band + row_h + self.line_spacing

    def _draw_run_dynamic(self, surface: pygame.Surface, run: _Run,
                          shown_n: int, x: int, run_top: int) -> None:
        """Per-glyph blit path for runs with fx and/or ruby (A5/A6)."""
        self._ensure_glyphs(run)
        eff = run.style.effect
        animated = bool(eff and eff != "none")
        for gi in range(shown_n):
            gs = run.glyph_surfs[gi]
            gx = x + run.glyph_x[gi]
            if animated:
                reveal_unit = run.start_reveal + gi
                dx, dy, alpha = self._glyph_offset_alpha(run, gi, reveal_unit)
                if alpha < 255:
                    gs = gs.copy()
                    gs.set_alpha(alpha)
                surface.blit(gs, (gx + dx, run_top + dy))
            else:
                surface.blit(gs, (gx, run_top))
        # Ruby reading centered over the whole revealed extent of the base run.
        if run.style.ruby and shown_n > 0:
            self._ensure_ruby(run)
            rs = run.ruby_surf
            if rs is not None:
                full_w = run.font.size(run.text)[0]
                # Center the reading over the full base run width.
                rx = x + (full_w - rs.get_width()) // 2
                ruby_h = rs.get_height()
                ry = run_top - ruby_h - _RUBY_GAP
                surface.blit(rs, (rx, ry))
