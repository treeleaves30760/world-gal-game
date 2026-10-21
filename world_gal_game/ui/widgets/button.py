"""Clickable button with hover state."""
from __future__ import annotations

from typing import Callable, Literal, TYPE_CHECKING

import pygame

from .base import Widget
from .label import _wrap_lines
from ..fonts import FontRegistry
from ..motion import MotionPolicy, coerce_motion_policy
from ..system_chrome import ChromeRequest
from ..theme import Theme

if TYPE_CHECKING:
    from ..system_chrome import SystemChromeHost


# Module-level UI click hook. The app installs one callback at init (playing the
# pack's ui_sound on the SFX bus); every Button calls it on activation, so we get
# clickable feedback without threading the AssetManager through every widget.
# Stays None (silent) in headless / tests / packs without a UI sound.
_click_sound_hook: "Callable[[], None] | None" = None


def set_click_sound_hook(fn: "Callable[[], None] | None") -> None:
    global _click_sound_hook
    _click_sound_hook = fn


class Button(Widget):
    def __init__(self, rect: pygame.Rect, label: str, *,
                 fonts: FontRegistry, theme: Theme,
                 font_size: int | None = None,
                 display_label: str | None = None,
                 content_padding: int | None = None,
                 on_click: Callable[[], None] | None = None,
                 style: str = "primary",   # primary | ghost | danger
                 enabled: bool = True,
                 tooltip: str | None = None,
                 chrome: "SystemChromeHost | None" = None,
                 screen: str = "system",
                 role: str = "action",
                 selected: bool = False,
                 text_align: Literal["left", "center", "right"] = "center",
                 wrap_text: bool = False,
                 motion: MotionPolicy | None = None):
        super().__init__(rect)
        self.label = label
        # ``label`` is the semantic/action name exposed to automation and
        # accessibility adapters. Dense controls may render a conventional
        # abbreviation or glyph without sacrificing that full name. Keeping
        # the override optional also means buttons whose labels change at
        # runtime continue to render the new value automatically.
        self._display_label = display_label
        self.content_padding = (
            None if content_padding is None else max(0, int(content_padding))
        )
        self.fonts = fonts
        self.theme = theme
        self.font_size = font_size or theme.pad_m + 12
        self.on_click = on_click
        self.style = style
        self.enabled = enabled
        # Optional author-facing explanation, especially useful for disabled
        # actions.  Rendering stays with the owning scene so the tooltip can
        # be placed inside that scene's safe area.
        self.tooltip = tooltip
        self.chrome = chrome
        self.screen = screen
        self.role = role
        self.selected = bool(selected)
        self.motion = coerce_motion_policy(motion)
        if text_align not in {"left", "center", "right"}:
            raise ValueError("text_align must be left, center, or right")
        self.text_align = text_align
        self.wrap_text = bool(wrap_text)
        self._pointer_over = False
        self._hover = False
        # Owning scenes set this for keyboard/gamepad navigation.  It is
        # intentionally distinct from pointer hover: focus must remain
        # visible even when the mouse is parked elsewhere, and must not make a
        # keyboard-focused button react to an unrelated mouse click.
        self.focused = False
        # Eased highlight [0,1]: lerps toward the hover state each frame so
        # the brighten fades in/out (~120ms) instead of snapping.
        self._hover_t = 0.0
        self._text_overflow = False

    @property
    def display_label(self) -> str:
        """Text painted inside the hit rectangle, distinct from its name."""
        return self.label if self._display_label is None else self._display_label

    def _text_layout(
        self,
    ) -> tuple[list[pygame.Surface], list[pygame.Rect], bool]:
        """Measure the complete label inside the padded button content box."""
        font = self.fonts.get(self.font_size, bold=True)
        text_pad = self.content_padding
        if text_pad is None:
            text_pad = max(
                self.theme.pad_m,
                int(round(
                    self.theme.pad_m * getattr(self.fonts, "scale", 1.0)
                )),
            )
        inner = pygame.Rect(
            self.rect.x + text_pad,
            self.rect.y,
            max(0, self.rect.width - text_pad * 2),
            self.rect.height,
        )
        if self.wrap_text:
            lines = _wrap_lines(self.display_label, font, max(1, inner.width))
        else:
            lines = [self.display_label]
        if not lines:
            lines = [""]
        rendered = [
            self.fonts.render(
                line,
                self.font_size,
                self.theme.text if self.enabled else self.theme.text_dim,
                bold=True,
            )
            for line in lines
        ]
        line_h = font.get_linesize()
        block_h = line_h * len(rendered)
        top = self.rect.centery - block_h // 2
        rects: list[pygame.Rect] = []
        for index, text_surf in enumerate(rendered):
            if self.text_align == "left":
                x = inner.x
            elif self.text_align == "right":
                x = inner.right - text_surf.get_width()
            else:
                x = inner.centerx - text_surf.get_width() // 2
            y = top + index * line_h + max(
                0,
                (line_h - text_surf.get_height()) // 2,
            )
            rects.append(text_surf.get_rect(topleft=(x, y)))
        overflow = (
            block_h > inner.height
            or any(not inner.contains(rect) for rect in rects)
        )
        return rendered, rects, overflow

    @property
    def text_overflow(self) -> bool:
        """Whether the full label would escape its padded content bounds."""
        _surfaces, _rects, overflow = self._text_layout()
        return overflow

    @property
    def text_line_rects(self) -> tuple[pygame.Rect, ...]:
        """Measured line boxes exposed for deterministic layout audits."""
        _surfaces, rects, _overflow = self._text_layout()
        return tuple(rects)

    def update(self, dt: float, inp) -> None:
        # Track pointer presence even while disabled.  A disabled control must
        # never activate, but its owning scene can still explain *why* it is
        # unavailable when the player hovers or clicks it.
        self._pointer_over = self.rect.collidepoint(inp.mouse_pos)
        self._hover = self.enabled and self._pointer_over
        target = 1.0 if self.enabled and (self._hover or self.focused) else 0.0
        self._hover_t += (
            (target - self._hover_t)
            * self.motion.feedback_factor(dt, 12.0)
        )
        if self._hover and inp.mouse_clicked:
            self.activate()

    def activate(self) -> bool:
        """Activate through the same audible path for every input device."""
        if not self.enabled or self.on_click is None:
            return False
        if _click_sound_hook is not None:
            _click_sound_hook()
        self.on_click()
        return True

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        high_contrast = (
            getattr(self.theme, "visual_profile", "standard")
            == "high_contrast"
        )
        if self.style == "primary":
            base = (*self.theme.accent[:3], 90)
            border = self.theme.border
        elif self.style == "ghost":
            base = (255, 255, 255, 16)
            border = self.theme.border_soft
        elif self.style == "danger":
            base = (*self.theme.warn[:3], 90)
            border = self.theme.warn
        else:
            base = self.theme.bg_panel
            border = self.theme.border_soft

        stock_border_width = 1
        if high_contrast:
            # A stock fallback can be drawn over arbitrary pack art.  Keep its
            # text plane opaque instead of alpha-blending semantic colours into
            # an unknown background; style remains redundant in the border,
            # label, selection inset, and disabled hatch.
            base = self.theme.bg_panel
            border = (
                self.theme.warn
                if self.style == "danger"
                else self.theme.border_strong
            )
            stock_border_width = 2

        if self.enabled and self._hover_t > 0.01:
            amt = self._hover_t
            base = (min(255, int(base[0] + 30 * amt)),
                    min(255, int(base[1] + 25 * amt)),
                    min(255, int(base[2] + 30 * amt)),
                    base[3] if len(base) > 3 else 220)
        if not self.enabled:
            base = self.theme.bg_panel if high_contrast else (60, 60, 60, 160)
            if high_contrast:
                border = self.theme.border_soft

        # Keep the renderer's emphasis tied to the finite interaction state,
        # not to ``style``.  Menu pages commonly contain many ``primary``
        # actions; treating all of them as highlighted would recreate the
        # exact "everything competes for attention" problem the skin is meant
        # to solve.  The semantic style remains available in ``variant``.
        emphasis = (
            max(0, min(3, int(round(self._hover_t * 3))))
            if self.enabled else 0
        )
        chrome_variant = f"{self.role}:{self.style}"
        density = 1.0
        painted = False
        if self.chrome is not None:
            density = max(
                0.01,
                float(getattr(self.fonts, "scale", 1.0)),
            )
            painted = self.chrome.paint(
                surface,
                self.rect,
                ChromeRequest(
                    part="button",
                    screen=self.screen,
                    variant=chrome_variant,
                    size=self.rect.size,
                    density=density,
                    enabled=self.enabled,
                    hovered=self._hover,
                    focused=self.focused,
                    selected=self.selected,
                    emphasis=emphasis,
                ),
            )
        if not painted:
            bg = pygame.Surface(self.rect.size, pygame.SRCALPHA)
            pygame.draw.rect(bg, base, bg.get_rect(),
                             border_radius=self.theme.radius_m)
            pygame.draw.rect(bg, border, bg.get_rect(),
                             width=stock_border_width,
                             border_radius=self.theme.radius_m)
            surface.blit(bg, self.rect.topleft)

        # Disabled affordance is engine-owned even when a plugin supplied the
        # background.  The stock path already baked this state into ``base``;
        # a handled chrome path receives a neutral veil so it cannot render an
        # unavailable action as active.
        if painted and not self.enabled:
            disabled = pygame.Surface(self.rect.size, pygame.SRCALPHA)
            pygame.draw.rect(
                disabled,
                (*self.theme.bg_panel[:3], 190),
                disabled.get_rect(),
                border_radius=self.theme.radius_m,
            )
            surface.blit(disabled, self.rect.topleft)

        if high_contrast and self.selected:
            # A second inset boundary makes selection identifiable without
            # relying on the primary/ghost hue.  It remains distinct from the
            # outer focus ring, so "selected" and "keyboard focus" can coexist.
            inset = self.rect.inflate(-8, -8)
            if inset.width > 0 and inset.height > 0:
                pygame.draw.rect(
                    surface,
                    self.theme.accent_alt,
                    inset,
                    width=2,
                    border_radius=max(1, self.theme.radius_m - 3),
                )
        if high_contrast and not self.enabled:
            # Short corner hatches are a non-colour disabled cue that avoids
            # crossing the functional label or changing the hit rectangle.
            for offset in (0, 6, 12):
                start = (self.rect.right - 18 + offset, self.rect.y + 3)
                end = (self.rect.right - 3, self.rect.y + 18 - offset)
                pygame.draw.line(
                    surface,
                    self.theme.text_dim,
                    start,
                    end,
                    width=2,
                )

        # A skin may add a halo, brackets, or other background-only focus
        # detail.  It is deliberately underneath the engine focus ring and
        # text, so accessibility never depends on plugin behaviour.
        if self.chrome is not None and self.enabled and self.focused:
            self.chrome.paint(
                surface,
                self.rect,
                ChromeRequest(
                    part="focus",
                    screen=self.screen,
                    variant=chrome_variant,
                    size=self.rect.size,
                    density=density,
                    enabled=True,
                    hovered=self._hover,
                    focused=True,
                    selected=self.selected,
                    emphasis=emphasis,
                ),
            )

        # A persistent, high-contrast inner ring is more reliable than
        # borrowing the pointer-hover animation: it appears on the very first
        # keyboard frame and survives reduced-motion / zero-dt test paths.
        if self.enabled and self.focused:
            pygame.draw.rect(
                surface,
                self.theme.accent_warm,
                self.rect,
                width=3,
                border_radius=self.theme.radius_m,
            )

        text_surfaces, text_rects, overflow = self._text_layout()
        self._text_overflow = overflow
        # The owning scene must size controls until ``text_overflow`` is false.
        # This clip is only the engine's last safety boundary for third-party
        # callers, preventing a malformed label from painting over neighbours.
        previous_clip = surface.get_clip()
        surface.set_clip(previous_clip.clip(self.rect))
        for text_surf, text_rect in zip(
            text_surfaces,
            text_rects,
            strict=True,
        ):
            surface.blit(text_surf, text_rect)
        surface.set_clip(previous_clip)
