"""Stable, non-interactive semantic sound captions for dialogue.

The overlay deliberately owns only presentation.  It never queues captions,
handles input, or advances dialogue; a new authored line replaces its complete
caption set atomically through :meth:`set_captions`.
"""
from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pygame

from .base import Widget
from ..system_chrome import ChromeRequest

if TYPE_CHECKING:
    from ..fonts import FontRegistry
    from ..system_chrome import SystemChromeHost
    from ..theme import Theme


_KINDS = frozenset({"ambient", "sfx", "music", "silence"})
_FALLBACK_LABELS = {
    "ambient": "環境音",
    "sfx": "音效",
    "music": "音樂",
    "silence": "寂靜",
}
_LOCALIZATION_KEYS = {
    kind: f"sound_caption_{kind}" for kind in _FALLBACK_LABELS
}
_MAX_CAPTIONS = 4
_MAX_TEXT_CHARS = 240
_ZWJ = "\u200d"


def _is_variation_selector(char: str) -> bool:
    codepoint = ord(char)
    return (
        0xFE00 <= codepoint <= 0xFE0F
        or 0xE0100 <= codepoint <= 0xE01EF
    )


def _is_emoji_modifier(char: str) -> bool:
    return 0x1F3FB <= ord(char) <= 0x1F3FF


def _grapheme_clusters(text: str) -> tuple[str, ...]:
    """Small dependency-free grapheme approximation for safe line breaks.

    Combining marks, variation selectors, emoji modifiers, and ZWJ sequences
    stay attached to their base.  Regional indicators are paired so flags are
    not split either.  Logical order is retained for RTL text; bidi shaping is
    left to pygame/the selected font backend.
    """
    clusters: list[str] = []
    current = ""
    join_next = False
    regional_count = 0
    for char in text:
        category = unicodedata.category(char)
        regional = 0x1F1E6 <= ord(char) <= 0x1F1FF
        attach = bool(current) and (
            join_next
            or char == _ZWJ
            or category.startswith("M")
            or _is_variation_selector(char)
            or _is_emoji_modifier(char)
            or (regional and regional_count == 1)
        )
        if not current:
            current = char
        elif attach:
            current += char
        else:
            clusters.append(current)
            current = char
        join_next = char == _ZWJ
        if regional:
            regional_count = (regional_count + 1) % 2
        else:
            regional_count = 0
    if current:
        clusters.append(current)
    return tuple(clusters)


@dataclass(frozen=True, slots=True)
class SoundCaptionCue:
    """One normalized, localized caption ready for UI presentation."""

    kind: str
    text: str
    label: str

    @property
    def display_text(self) -> str:
        return f"［{self.label}］ {self.text}"


def _localized_label(kind: str, localization: object | None) -> str:
    fallback = _FALLBACK_LABELS[kind]
    translate = getattr(localization, "t", None)
    if callable(translate):
        try:
            value = translate(_LOCALIZATION_KEYS[kind], fallback)
            if (
                isinstance(value, str)
                and value.strip()
                and not any(
                    unicodedata.category(char).startswith("C")
                    for char in value
                )
            ):
                return " ".join(value.split())[:32]
        except Exception:
            pass
    return fallback


def normalize_sound_captions(
    value: object,
    localization: object | None = None,
) -> tuple[SoundCaptionCue, ...]:
    """Normalize trusted models or JSON-native dictionaries for UI use.

    Persisted history is an untrusted compatibility boundary, so malformed
    entries fail closed instead of reaching pygame's text renderer.  Valid
    input is capped to the authored contract's four captions.
    """
    if not isinstance(value, (list, tuple)):
        return ()
    cues: list[SoundCaptionCue] = []
    for raw in value:
        if len(cues) >= _MAX_CAPTIONS:
            break
        if isinstance(raw, Mapping):
            kind = raw.get("kind")
            text = raw.get("text")
        else:
            kind = getattr(raw, "kind", None)
            text = getattr(raw, "text", None)
        if kind not in _KINDS or not isinstance(text, str):
            continue
        if any(
            unicodedata.category(char).startswith("C")
            for char in text
        ):
            continue
        clean = " ".join(text.split())
        if not clean or len(clean) > _MAX_TEXT_CHARS:
            continue
        cues.append(SoundCaptionCue(
            kind=kind,
            text=clean,
            label=_localized_label(kind, localization),
        ))
    return tuple(cues)


def _ellipsize(text: str, font: pygame.font.Font, width: int) -> str:
    if not text or width <= 0:
        return ""
    if font.size(text)[0] <= width:
        return text
    suffix = "…"
    if font.size(suffix)[0] > width:
        return ""
    clusters = _grapheme_clusters(text.rstrip("…"))
    lo, hi = 0, len(clusters)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.size("".join(clusters[:mid]) + suffix)[0] <= width:
            lo = mid
        else:
            hi = mid - 1
    return "".join(clusters[:lo]).rstrip() + suffix


def wrap_sound_caption(
    text: str,
    font: pygame.font.Font,
    width: int,
) -> tuple[str, ...]:
    """Measured CJK/Latin wrapping, including hard breaks for long tokens."""
    value = " ".join(str(text or "").split())
    if not value:
        return ()
    if width <= 0:
        return (value,)
    lines: list[str] = []
    remaining = list(_grapheme_clusters(value))
    while remaining:
        joined = "".join(remaining)
        if font.size(joined)[0] <= width:
            lines.append(joined)
            break
        lo, hi = 0, len(remaining)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if font.size("".join(remaining[:mid]))[0] <= width:
                lo = mid
            else:
                hi = mid - 1
        fit = max(1, lo)
        split = next(
            (
                index
                for index in range(fit - 1, -1, -1)
                if remaining[index].isspace()
            ),
            -1,
        )
        if split >= max(1, fit // 2):
            line = "".join(remaining[:split]).rstrip()
            remaining = remaining[split + 1 :]
            while remaining and remaining[0].isspace():
                remaining.pop(0)
        else:
            line = "".join(remaining[:fit])
            remaining = remaining[fit:]
        lines.append(line)
    return tuple(lines)


class SoundCaptionOverlay(Widget):
    """High-contrast top-centre caption panel with a finite safe area."""

    STOCK_BACKGROUND = (7, 9, 15, 244)
    STOCK_FOREGROUND = (250, 250, 252)
    STOCK_BORDER = (255, 255, 255, 150)

    def __init__(
        self,
        screen_size: tuple[int, int],
        *,
        fonts: "FontRegistry",
        theme: "Theme",
        localization: object | None = None,
        chrome: "SystemChromeHost | None" = None,
        text_scale: float = 1.0,
    ) -> None:
        super().__init__(pygame.Rect(0, 0, 0, 0))
        self.enabled = False
        self.fonts = fonts
        self.theme = theme
        self.localization = localization
        self.chrome = chrome
        self.text_scale = max(1.0, min(1.4, float(text_scale)))
        self._screen_size = tuple(max(1, int(v)) for v in screen_size)
        self._safe_rect = pygame.Rect((0, 0), self._screen_size)
        self._captions: tuple[SoundCaptionCue, ...] = ()
        self._caption_lines: tuple[tuple[str, ...], ...] = ()
        self._font_size = 18
        self._line_height = 1
        self._pad_x = self._pad_y = self._cue_gap = 1
        self._content_rect = pygame.Rect(0, 0, 0, 0)
        self._layout_key: tuple | None = None

    @property
    def captions(self) -> tuple[SoundCaptionCue, ...]:
        return self._captions

    @property
    def caption_lines(self) -> tuple[tuple[str, ...], ...]:
        return self._caption_lines

    @property
    def content_rect(self) -> pygame.Rect:
        return self._content_rect.copy()

    @property
    def safe_rect(self) -> pygame.Rect:
        return self._safe_rect.copy()

    def hit(self, pos: tuple[int, int]) -> bool:
        return False

    def update(self, dt: float, inp) -> None:
        return None

    def set_captions(self, captions: object) -> None:
        normalized = normalize_sound_captions(captions, self.localization)
        if normalized != self._captions:
            self._captions = normalized
            self._layout_key = None

    def set_layout(
        self,
        screen_size: tuple[int, int],
        *,
        text_scale: float | None = None,
        safe_rect: pygame.Rect | tuple[int, int, int, int] | None = None,
    ) -> None:
        size = tuple(max(1, int(v)) for v in screen_size)
        scale = self.text_scale if text_scale is None else max(
            1.0,
            min(1.4, float(text_scale)),
        )
        canvas = pygame.Rect((0, 0), size)
        safe = (
            canvas
            if safe_rect is None
            else pygame.Rect(safe_rect).clip(canvas)
        )
        if safe.width <= 0 or safe.height <= 0:
            safe = canvas
        if (
            size != self._screen_size
            or scale != self.text_scale
            or safe != self._safe_rect
        ):
            self._screen_size = size
            self.text_scale = scale
            self._safe_rect = safe
            self._layout_key = None

    def layout(
        self,
        screen_size: tuple[int, int] | None = None,
        *,
        text_scale: float | None = None,
        safe_rect: pygame.Rect | tuple[int, int, int, int] | None = None,
    ) -> pygame.Rect:
        """Measure now so scenes can reserve real, non-overlapping geometry."""
        size = screen_size or self._screen_size
        self.set_layout(size, text_scale=text_scale, safe_rect=safe_rect)
        self._layout(size)
        return self.rect.copy()

    def _layout(self, screen_size: tuple[int, int]) -> None:
        self.set_layout(screen_size, safe_rect=self._safe_rect)
        key = (
            self._screen_size,
            self.text_scale,
            tuple(self._safe_rect),
            self._captions,
        )
        if key == self._layout_key:
            return
        self._layout_key = key
        sw, sh = self._screen_size
        safe = self._safe_rect.clip(pygame.Rect(0, 0, sw, sh))
        if not self._captions:
            self.rect = pygame.Rect(sw // 2, 0, 0, 0)
            self._content_rect = pygame.Rect(self.rect)
            self._caption_lines = ()
            return

        density = max(0.5, sh / 720.0)
        self._pad_x = max(8, int(round(18 * density)))
        self._pad_y = max(6, int(round(10 * density)))
        self._cue_gap = max(2, int(round(5 * density)))
        edge_x = max(8, int(round(20 * density)))
        edge_top = max(safe.top, max(8, int(round(14 * density))))
        max_panel_w = max(
            1,
            min(
                safe.width,
                sw - edge_x * 2,
                int(round(sw * 0.72)),
                int(round(840 * density)),
            ),
        )
        min_panel_w = min(max_panel_w, max(1, int(round(320 * density))))
        # A deliberately narrow scene safe area must still leave a positive
        # measured text column. Compact padding before wrapping instead of
        # producing a negative ``content_rect`` that merely clips paint later.
        self._pad_x = min(self._pad_x, max(0, (max_panel_w - 1) // 2))
        content_w = max(1, max_panel_w - self._pad_x * 2)
        max_panel_h = max(
            1,
            min(
                max(1, safe.bottom - edge_top),
                int(round(sh * 0.44)),
            ),
        )
        self._pad_y = min(self._pad_y, max(0, (max_panel_h - 1) // 2))

        # Preserve at least one visible line per cue in a compact 360p safe
        # area. Text scale remains meaningful: both the desired and minimum
        # font sizes grow with the player's 1.0–1.4 preference.
        desired_size = max(14, int(round(18 * self.text_scale)))
        minimum_size = max(10, int(round(12 * self.text_scale)))
        min_pad_y = max(2, int(round(4 * density)))
        min_cue_gap = max(1, int(round(2 * density)))
        self._font_size = desired_size
        while self._font_size > minimum_size:
            probe = self.fonts.get(self._font_size, bold=True)
            minimum_height = (
                probe.get_linesize() * len(self._captions)
                + min_pad_y * 2
                + min_cue_gap * max(0, len(self._captions) - 1)
            )
            if minimum_height <= max_panel_h:
                break
            self._font_size -= 1
        font = self.fonts.get(self._font_size, bold=True)
        self._line_height = max(1, font.get_linesize())
        desired_minimum = (
            self._line_height * len(self._captions)
            + self._pad_y * 2
            + self._cue_gap * max(0, len(self._captions) - 1)
        )
        if desired_minimum > max_panel_h:
            self._pad_y = min_pad_y
            self._cue_gap = min_cue_gap
        wrapped = [
            wrap_sound_caption(cue.display_text, font, content_w)
            for cue in self._captions
        ]

        # Give every cue one line, then distribute spare lines round-robin.
        available_for_lines = max(
            self._line_height,
            max_panel_h
            - self._pad_y * 2
            - self._cue_gap * max(0, len(wrapped) - 1),
        )
        line_budget = max(len(wrapped), available_for_lines // self._line_height)
        per_cue_cap = 2 if sh <= 450 else 3
        allocations = [1] * len(wrapped)
        spare = max(0, line_budget - len(wrapped))
        while spare:
            changed = False
            for index, lines in enumerate(wrapped):
                wanted = min(per_cue_cap, len(lines))
                if allocations[index] < wanted:
                    allocations[index] += 1
                    spare -= 1
                    changed = True
                    if not spare:
                        break
            if not changed:
                break

        clipped: list[tuple[str, ...]] = []
        for lines, limit in zip(wrapped, allocations):
            visible = list(lines[:limit])
            if len(lines) > limit and visible:
                visible[-1] = _ellipsize(
                    visible[-1].rstrip("…") + "…",
                    font,
                    content_w,
                )
            clipped.append(tuple(visible))
        self._caption_lines = tuple(clipped)

        widest = max(
            (font.size(line)[0] for lines in clipped for line in lines),
            default=1,
        )
        panel_w = min(max_panel_w, max(min_panel_w, widest + self._pad_x * 2))
        # Match canvas parity where there is room, so the panel has an exact
        # integer centre instead of appearing one pixel left on large canvases.
        if panel_w % 2 != sw % 2:
            if panel_w < max_panel_w:
                panel_w += 1
            elif panel_w > 1:
                panel_w -= 1
        panel_h = (
            self._pad_y * 2
            + sum(len(lines) * self._line_height for lines in clipped)
            + self._cue_gap * max(0, len(clipped) - 1)
        )
        panel_h = min(max_panel_h, max(1, panel_h))
        self.rect = pygame.Rect(
            safe.centerx - panel_w // 2,
            edge_top,
            panel_w,
            panel_h,
        )
        self.rect.clamp_ip(safe)
        self._content_rect = self.rect.inflate(-self._pad_x * 2, -self._pad_y * 2)

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible or not self._captions:
            return
        self._layout(surface.get_size())
        if self.rect.width <= 0 or self.rect.height <= 0:
            return
        density = max(
            0.01,
            float(getattr(self.fonts, "scale", 1.0)),
        )
        painted = (
            self.chrome is not None
            and self.chrome.paint(
                surface,
                self.rect,
                ChromeRequest(
                    part="panel",
                    screen="dialogue",
                    variant="sound_caption",
                    size=self.rect.size,
                    density=density,
                    emphasis=3,
                ),
            )
        )
        if not painted:
            panel = pygame.Surface(self.rect.size, pygame.SRCALPHA)
            radius = max(4, int(round(self.theme.radius_m * density)))
            border = max(1, int(round(density)))
            pygame.draw.rect(
                panel,
                self.STOCK_BACKGROUND,
                panel.get_rect(),
                border_radius=radius,
            )
            pygame.draw.rect(
                panel,
                self.STOCK_BORDER,
                panel.get_rect(),
                width=border,
                border_radius=radius,
            )
            surface.blit(panel, self.rect.topleft)

        font = self.fonts.get(self._font_size, bold=True)
        old_clip = surface.get_clip()
        surface.set_clip(self.rect.clip(old_clip))
        x = self.rect.x + self._pad_x
        y = self.rect.y + self._pad_y
        for index, lines in enumerate(self._caption_lines):
            for line in lines:
                rendered = font.render(line, True, self.STOCK_FOREGROUND)
                surface.blit(rendered, (x, y))
                y += self._line_height
            if index + 1 < len(self._caption_lines):
                y += self._cue_gap
        surface.set_clip(old_clip)

    def describe(self) -> dict:
        return {
            "captions": [
                {"kind": cue.kind, "text": cue.text, "label": cue.label}
                for cue in self._captions
            ],
            "rect": list(self.rect),
            "lines": [list(lines) for lines in self._caption_lines],
            "interactive": False,
        }


__all__ = [
    "SoundCaptionCue",
    "SoundCaptionOverlay",
    "normalize_sound_captions",
    "wrap_sound_caption",
]
