"""Rich-text markup parser (pygame-free).

The single source of truth for the engine's inline text styling vocabulary.
The validator and the capability manifest import :data:`RICHTEXT_TAGS` and
:func:`parse` from here without pulling in pygame; the rendering widget
(:mod:`world_gal_game.ui.widgets.rich_text_view`) is the only consumer that
touches surfaces.

Syntax = BBCode ``[tag]...[/tag]`` (not Ren'Py ``{...}``). This avoids the two
syntaxes already in use on a line's text: ``{token}`` for state interpolation
and ``[[op:arg]]`` for dialogue ops. The rule is deliberately conservative: a
``[`` is only markup when immediately followed by a *known* tag name (and a
matching ``]``); anything else — including ``[[`` and an unknown ``[tag]`` —
is left as literal text. A backslash escape ``\\[`` also forces a literal
``[``. Malformed attributes degrade to literal/ignored, never raise. This
mirrors the engine's "unknown directive shows verbatim" philosophy.

A3 ships ``b`` / ``color`` / ``size``. Later phases extend :data:`RICHTEXT_TAGS`
and :class:`SegmentStyle` (``w``/``s`` wait+speed control tokens, ``ruby``,
``fx`` per-glyph effects); the parser structure is built to grow.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

# Known tags. Tags carry style (``b``/``color``/``size``/``speed``/``ruby``/
# ``fx``) or control behaviour (``w`` wait). Kept as a frozenset so the
# validator / manifest can list it cheaply and so membership tests are O(1).
#
# - ``b`` / ``color`` / ``size``  : visual style (A3)
# - ``w``                          : inline wait control token (A4)
# - ``s``                          : per-span reveal speed, chars/sec (A4)
# - ``ruby``                       : furigana annotation over base text (A5)
# - ``fx``                         : per-glyph animated effect (A6)
RICHTEXT_TAGS: frozenset[str] = frozenset(
    {"b", "color", "size", "w", "s", "ruby", "fx"})

# Default clamp range for absolute / relative font sizes so a typo can't blow
# up layout. (Relative deltas are applied then clamped against the same range.)
_MIN_SIZE = 6
_MAX_SIZE = 200

# Default pause for a bare ``[w]`` (seconds).
_DEFAULT_WAIT = 0.4

# Known animated-effect names for ``[fx=...]``. Anything else degrades to
# ``"none"`` (the static fast path), mirroring the unknown-tag philosophy.
RICHTEXT_FX: frozenset[str] = frozenset({"shake", "wave", "fadein"})


Color = tuple[int, int, int]


@dataclass(frozen=True)
class SegmentStyle:
    """The resolved styling for a run of characters.

    ``color`` is an explicit override or ``None`` (use the renderer's base).
    ``size_abs`` pins an absolute pt size; ``size_delta`` adds to the base.
    ``speed`` (A4) is a per-span reveal rate in chars/sec, or ``None`` to
    inherit the renderer's base typewriter speed. ``ruby`` (A5) is a furigana
    reading rendered as small text over the base run, or ``None``. ``effect``
    (A6) is one of :data:`RICHTEXT_FX` (``"shake"``/``"wave"``/``"fadein"``) or
    ``None``/``"none"`` for the static fast path.
    """

    color: Color | None = None
    bold: bool = False
    size_delta: int = 0
    size_abs: int | None = None
    effect: str | None = None
    ruby: str | None = None
    speed: float | None = None

    def resolved_size(self, base_size: int) -> int:
        size = self.size_abs if self.size_abs is not None else base_size + self.size_delta
        return max(_MIN_SIZE, min(_MAX_SIZE, size))


@dataclass
class Segment:
    """A run of text sharing one :class:`SegmentStyle`."""

    text: str
    style: SegmentStyle = field(default_factory=SegmentStyle)


@dataclass
class RichDocument:
    """Parsed markup: an ordered list of styled segments + control metadata.

    ``controls`` carries non-text behaviour. A4 populates ``controls["waits"]``
    as a ``{plain_char_index: duration_seconds}`` map: the renderer pauses the
    typewriter just before revealing the char at that index. ``plain_text()``
    is the markup-stripped string used by headless / history; ``total_chars()``
    is its length (the reveal contract).
    """

    segments: list[Segment] = field(default_factory=list)
    controls: dict[str, Any] = field(default_factory=dict)

    def plain_text(self) -> str:
        return "".join(seg.text for seg in self.segments)

    def total_chars(self) -> int:
        return sum(len(seg.text) for seg in self.segments)

    def waits(self) -> dict[int, float]:
        """Wait controls as ``{plain_char_index: duration_seconds}`` (or {})."""
        return self.controls.get("waits", {})


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

# Matches a *candidate* tag: open ``[name]`` / ``[name=attr]`` or close
# ``[/name]``. Whether it's actually treated as markup is decided after the
# match (the name must be in RICHTEXT_TAGS); otherwise it's literal.
_TAG_RE = re.compile(r"\[(/?)([a-zA-Z][a-zA-Z0-9_]*)(?:=([^\]]*))?\]")


def _parse_color(attr: str) -> Color | None:
    """Parse ``#rgb`` / ``#rrggbb`` (or a few named colors) -> RGB, else None."""
    s = attr.strip()
    named = {
        "red": (220, 60, 60), "green": (60, 200, 90), "blue": (80, 130, 230),
        "yellow": (240, 220, 80), "white": (255, 255, 255), "black": (0, 0, 0),
        "cyan": (80, 220, 220), "magenta": (220, 80, 220), "gray": (160, 160, 160),
        "grey": (160, 160, 160), "orange": (240, 150, 60),
    }
    if s.lower() in named:
        return named[s.lower()]
    if not s.startswith("#"):
        return None
    hexpart = s[1:]
    if len(hexpart) == 3 and all(c in "0123456789abcdefABCDEF" for c in hexpart):
        r, g, b = (int(c * 2, 16) for c in hexpart)
        return (r, g, b)
    if len(hexpart) == 6 and all(c in "0123456789abcdefABCDEF" for c in hexpart):
        return (int(hexpart[0:2], 16), int(hexpart[2:4], 16), int(hexpart[4:6], 16))
    return None


def _apply_tag(style: SegmentStyle, name: str, attr: str | None) -> SegmentStyle:
    """Return a new style with ``name``/``attr`` applied. Bad attrs are ignored."""
    if name == "b":
        return replace(style, bold=True)
    if name == "color":
        col = _parse_color(attr or "")
        return replace(style, color=col) if col is not None else style
    if name == "size":
        raw = (attr or "").strip()
        if not raw:
            return style
        try:
            if raw[0] in "+-":
                return replace(style, size_delta=int(raw))
            return replace(style, size_abs=int(raw))
        except ValueError:
            return style   # malformed -> ignore, keep prior style
    if name == "s":
        # Per-span reveal speed (chars/sec). ``[s]`` (no attr) resets to the
        # inherited base speed; ``[s=0]`` or a bad value also degrades to
        # inherited (None) rather than freezing the reveal.
        raw = (attr or "").strip()
        if not raw:
            return replace(style, speed=None)
        try:
            val = float(raw)
        except ValueError:
            return replace(style, speed=None)
        return replace(style, speed=val) if val > 0 else replace(style, speed=None)
    if name == "ruby":
        # The attribute is the reading; the inner text is the base (set when
        # the segment is flushed). An empty reading just renders the base.
        reading = (attr or "").strip()
        return replace(style, ruby=reading or None)
    if name == "fx":
        val = (attr or "").strip().lower()
        return replace(style, effect=val if val in RICHTEXT_FX else "none")
    return style


def parse(markup: str, *, base_color: Color = (255, 255, 255),
          base_size: int = 24) -> RichDocument:
    """Parse BBCode-ish markup into a :class:`RichDocument`.

    ``base_color`` / ``base_size`` aren't baked into the segments (segments
    only record *overrides*); they're accepted for API symmetry with the
    renderer and to validate that callers thread their theme defaults through.
    Unknown tags, stray brackets and ``\\[`` escapes become literal text.
    Unbalanced opens auto-close at end; a close with no matching open is
    ignored.
    """
    segments: list[Segment] = []
    # Style stack: each entry is (tag_name, style_after_applying_this_tag).
    stack: list[tuple[str, SegmentStyle]] = []
    buf: list[str] = []
    # Wait controls keyed by the plain-text char index they fire *before*.
    waits: dict[int, float] = {}
    # Running count of base (plain) characters emitted so far. ``[w]`` keys its
    # wait by this so the renderer can pause just before revealing that char.
    char_index = 0

    def cur_style() -> SegmentStyle:
        return stack[-1][1] if stack else SegmentStyle()

    def flush() -> None:
        nonlocal char_index
        if buf:
            text = "".join(buf)
            segments.append(Segment(text, cur_style()))
            char_index += len(text)
            buf.clear()

    i = 0
    n = len(markup)
    while i < n:
        ch = markup[i]
        # Backslash escape: "\[" -> literal "[" (consume both).
        if ch == "\\" and i + 1 < n and markup[i + 1] == "[":
            buf.append("[")
            i += 2
            continue
        if ch == "[":
            m = _TAG_RE.match(markup, i)
            name = m.group(2) if m else None
            if m and name in RICHTEXT_TAGS:
                closing = m.group(1) == "/"
                attr = m.group(3)
                if name == "w":
                    # Inline wait control token (self-contained, no body). A
                    # ``[/w]`` close is meaningless -> ignore. Flush first so
                    # the wait's char index counts the buffered text.
                    if not closing:
                        flush()
                        dur = _DEFAULT_WAIT
                        raw = (attr or "").strip()
                        if raw:
                            try:
                                parsed = float(raw)
                                if parsed > 0:
                                    dur = parsed
                            except ValueError:
                                pass   # malformed -> default pause
                        # Multiple waits at the same point accumulate.
                        waits[char_index] = waits.get(char_index, 0.0) + dur
                    i = m.end()
                    continue
                if closing:
                    # Pop the nearest matching open; ignore if none.
                    for j in range(len(stack) - 1, -1, -1):
                        if stack[j][0] == name:
                            flush()
                            del stack[j:]
                            break
                    # else: mismatched/extra close -> ignore silently.
                else:
                    flush()
                    new_style = _apply_tag(cur_style(), name, attr)
                    stack.append((name, new_style))
                i = m.end()
                continue
            # Not a known tag (incl. "[[…", "[unknown]") -> literal "[".
            buf.append("[")
            i += 1
            continue
        buf.append(ch)
        i += 1

    flush()
    controls: dict[str, Any] = {}
    if waits:
        controls["waits"] = waits
    return RichDocument(segments=segments, controls=controls)


def strip_markup(markup: str) -> str:
    """Return ``markup`` with all known rich-text tags removed.

    Equivalent to ``parse(markup).plain_text()`` but cheaper and the canonical
    entry point for headless / dialogue-history clean text. Unknown tags and
    escaped brackets are normalised the same way the parser would.
    """
    return parse(markup).plain_text()


# Self-contained tags that take no body (and therefore never need a close).
_SELF_CONTAINED_TAGS: frozenset[str] = frozenset({"w"})

# A candidate that *looks like* a rich-text tag but uses an unknown name. We
# only lint a stray ``[name]`` as a typo when it isn't an intentional ``[[``
# directive escape and isn't an interpolation ``{token}``. The shape mirrors
# ``_TAG_RE`` so the lint stays in lockstep with the parser.
_LINT_TAG_RE = _TAG_RE


def lint_markup(markup: str) -> list[str]:
    """Return human-readable problems with ``markup`` (empty list = clean).

    The parser itself degrades gracefully and never raises, so this is the
    diagnostic counterpart used by the validator. It reports:

    - **unknown tag names** — a ``[name]`` / ``[/name]`` whose ``name`` is not
      in :data:`RICHTEXT_TAGS` (skips the ``[[`` directive-escape case);
    - **unbalanced tags** — an open tag with no matching close, or a close
      with no matching open;
    - **malformed attributes** — a ``[color=...]`` that doesn't parse to a
      colour, or a ``[size=...]`` whose value isn't an integer.

    Each problem is one string. The validator pairs unknown-tag problems with
    a closest-match suggestion against :data:`RICHTEXT_TAGS`.
    """
    issues: list[str] = []
    # Track open style tags for balance checking. ``w`` is self-contained.
    open_stack: list[str] = []

    i = 0
    n = len(markup)
    while i < n:
        ch = markup[i]
        # Backslash escape "\[" -> literal, skip both.
        if ch == "\\" and i + 1 < n and markup[i + 1] == "[":
            i += 2
            continue
        if ch == "[":
            # "[[" is an intentional directive escape / dialogue-op prefix —
            # never a rich-text tag, so don't lint it as a typo.
            if i + 1 < n and markup[i + 1] == "[":
                i += 2
                continue
            m = _LINT_TAG_RE.match(markup, i)
            if m:
                closing = m.group(1) == "/"
                name = m.group(2)
                attr = m.group(3)
                if name not in RICHTEXT_TAGS:
                    issues.append(f"未知標記 [{name}]。")
                    i = m.end()
                    continue
                # Known tag: attribute + balance checks.
                if not closing:
                    if name == "color":
                        if _parse_color(attr or "") is None:
                            issues.append(
                                f"標記 [color={attr or ''}] 的色彩值無法解析。")
                    elif name == "size":
                        raw = (attr or "").strip()
                        if raw:
                            try:
                                int(raw)
                            except ValueError:
                                issues.append(
                                    f"標記 [size={attr}] 的尺寸不是整數。")
                    if name not in _SELF_CONTAINED_TAGS:
                        open_stack.append(name)
                else:
                    # Closing tag: must match the nearest open of same name.
                    if name in _SELF_CONTAINED_TAGS:
                        # [/w] is meaningless but harmless — flag gently.
                        issues.append(f"標記 [/{name}] 沒有對應的開啟標記。")
                    elif name in open_stack:
                        # Pop nearest matching open.
                        for j in range(len(open_stack) - 1, -1, -1):
                            if open_stack[j] == name:
                                del open_stack[j:]
                                break
                    else:
                        issues.append(
                            f"標記 [/{name}] 沒有對應的開啟標記。")
                i = m.end()
                continue
            # A "[" that isn't a tag at all — literal, ignore.
            i += 1
            continue
        i += 1

    # Any tags still open at end are unbalanced.
    for name in open_stack:
        issues.append(f"標記 [{name}] 沒有對應的結束標記 [/{name}]。")
    return issues
