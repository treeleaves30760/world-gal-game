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


class RichText(Widget):
    def __init__(self, rect: pygame.Rect, markup: str = "", *,
                 fonts: FontRegistry, size: int,
                 color=(255, 255, 255), line_spacing: int = 6,
                 reveal_chars: int | None = None,
                 text_speed: float = 0.0):
        super().__init__(rect)
        self.fonts = fonts
        self.base_size = size
        self.base_color = color
        self.line_spacing = line_spacing
        self.reveal_chars = reveal_chars
        # Base typewriter rate (chars/sec) used by update(); 0 disables the
        # internal cursor (caller drives set_reveal directly, e.g. tests).
        self.text_speed = text_speed
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

    # ----- text + parsing ------------------------------------------------

    def set_markup(self, markup: str, *, reveal_chars: int | None = None) -> None:
        self._doc = parse(markup, base_color=self.base_color,
                          base_size=self.base_size)
        self.reveal_chars = reveal_chars
        self._dirty = True
        self._reset_reveal_state(reveal_chars)

    # WrappedText-compatible alias: DialogueBox historically called set_text.
    def set_text(self, markup: str, *, reveal_chars: int | None = None) -> None:
        self.set_markup(markup, reveal_chars=reveal_chars)

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

    def plain_text(self) -> str:
        return self._doc.plain_text()

    # ----- layout --------------------------------------------------------

    def _font_for(self, style: SegmentStyle) -> pygame.font.Font:
        return self.fonts.get(style.resolved_size(self.base_size),
                              bold=style.bold)

    def _ensure_layout(self) -> None:
        if not self._dirty:
            return
        max_width = self.rect.width
        rows: list[list[_Run]] = []
        # Current row state.
        row: list[_Run] = []
        # The run currently being filled (same style as the tail char).
        cur_text = ""
        cur_style: SegmentStyle | None = None
        cur_font: pygame.font.Font | None = None
        cur_color: tuple = self.base_color
        # Plain-char index where the current run started.
        cur_start_plain = 0
        # Running plain-char index (counts every base char incl. newlines).
        plain_idx = 0
        # Running reveal-unit index assigned to runs as they are committed.
        reveal_idx = 0
        # Accumulated pixel width of the row so far (committed runs + cur run).
        committed_w = 0

        def finish_run() -> None:
            nonlocal cur_text, committed_w, reveal_idx
            if cur_text and cur_style is not None and cur_font is not None:
                w, _ = cur_font.size(cur_text)
                run = _Run(cur_text, cur_style, cur_font, cur_color,
                           cur_font.get_linesize(),
                           start_plain=cur_start_plain,
                           start_reveal=reveal_idx)
                row.append(run)
                committed_w += w
                reveal_idx += len(cur_text)
            cur_text = ""

        def finish_row(*, implicit_break: bool) -> None:
            nonlocal row, committed_w, reveal_idx
            finish_run()
            rows.append(row)
            row = []
            committed_w = 0
            # Every row boundary (explicit \n or wrap) costs one reveal unit,
            # matching WrappedText's glyphs + (rows-1) accounting.
            if implicit_break:
                reveal_idx += 1

        for seg in self._doc.segments:
            style = seg.style
            font = self._font_for(style)
            color = style.color if style.color is not None else self.base_color
            for ch in seg.text:
                if ch == "\n":
                    finish_row(implicit_break=True)
                    cur_style, cur_font, cur_color = None, None, color
                    plain_idx += 1   # the newline is a plain char
                    cur_start_plain = plain_idx
                    continue
                # Style change within a row -> close the current run.
                if cur_style is not None and (style != cur_style):
                    finish_run()
                    cur_start_plain = plain_idx
                if cur_style is None:
                    cur_start_plain = plain_idx
                cur_style, cur_font, cur_color = style, font, color
                test = cur_text + ch
                # Width of committed runs + this run's tentative width.
                run_w, _ = font.size(test)
                if max_width > 0 and (committed_w + run_w) > max_width \
                        and (row or cur_text):
                    # Wrap: commit what we have, start a fresh row with ch.
                    finish_row(implicit_break=True)
                    cur_style, cur_font, cur_color = style, font, color
                    cur_start_plain = plain_idx
                    cur_text = ch
                else:
                    cur_text = test
                plain_idx += 1
        # Final row never adds a trailing break (matches WrappedText [""]).
        finish_row(implicit_break=False)
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
        result = plain   # default for the tagless / no-wrap case (they match)
        for row in self._rows:
            for run in row:
                end_plain = run.start_plain + len(run.text)
                if run.start_plain <= plain < end_plain:
                    return run.start_reveal + (plain - run.start_plain)
                if plain == end_plain:
                    # Sits exactly at the run boundary; the reveal position is
                    # just past this run (a following break, if any, is handled
                    # by the next run's start_reveal).
                    result = run.start_reveal + len(run.text)
        return result

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

    def update(self, dt: float, inp=None) -> None:
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
        total = self.total_chars()
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
        self._ensure_layout()
        total = self.total_chars()
        self.reveal_chars = total
        self._reveal_f = float(total)
        self._wait_left = 0.0
        # Mark every wait position as done.
        for p in self._doc.waits():
            self._waits_done.add(self._plain_to_reveal(p))
        # A forced reveal completes instantly: stamp every unit's reveal time
        # in the past so any fadein is already fully opaque (no lingering ramp).
        stamp = self._clock - _FADEIN_DUR
        for i in range(total):
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
        for ri, row in enumerate(self._rows):
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
