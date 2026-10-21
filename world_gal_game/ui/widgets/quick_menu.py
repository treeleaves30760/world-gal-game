"""Persistent, progressively disclosed quick menu for dialogue.

Only the actions used during ordinary reading stay on the primary rail.
Less-frequent and potentially destructive actions (notably quick-load) live
behind a ``More`` button on a second rail.  This keeps the signature VN
controls one click away without presenting ten equal-priority choices on
every line.
"""
from __future__ import annotations

from typing import Callable, TYPE_CHECKING

import pygame

from .base import Widget
from ..input_bindings import InputAction
from .button import Button
from ..fonts import FontRegistry
from ..motion import MotionPolicy, coerce_motion_policy
from ..system_chrome import ChromeRequest
from ..theme import Theme

if TYPE_CHECKING:
    from ..system_chrome import SystemChromeHost


QuickMenuItem = tuple[
    str,
    Callable[[], None] | None,
    Callable[[], bool] | None,
]


class QuickMenuBar(Widget):
    def __init__(self, right: int, y: int, *, fonts: FontRegistry, theme: Theme,
                 items: list[QuickMenuItem],
                 overflow_items: list[QuickMenuItem] | None = None,
                 overflow_label: str = "更多",
                 primary_glyphs: list[str] | tuple[str, ...] | None = None,
                 height: int = 34, font_size: int = 14, gap: int = 6,
                 left: int | None = None,
                 chrome: "SystemChromeHost | None" = None,
                 density: float | None = None,
                 motion: MotionPolicy | None = None):
        self.fonts = fonts
        self.theme = theme
        self.chrome = chrome
        self.motion = coerce_motion_policy(motion)
        self.density = max(
            0.01,
            float(
                getattr(fonts, "scale", 1.0)
                if density is None
                else density
            ),
        )
        self._right = right
        self._left = theme.pad_l if left is None else left
        self._y = y
        self._height = height
        self._font_size = font_size
        self._gap = gap
        self._overflow_open = False
        self._consumed_input = False
        self._focus_button: Button | None = None
        self._hovered_tooltip: tuple[Button, str] | None = None
        self._tooltip_rect: pygame.Rect | None = None
        self._entries: list[tuple[Button, Callable[[], bool] | None]] = []
        self._overflow_entries: list[
            tuple[Button, Callable[[], bool] | None]
        ] = []

        primary_items = list(items)
        if overflow_items:
            primary_items.append(
                (overflow_label, self.toggle_overflow,
                 lambda: self._overflow_open),
            )
        compact_labels = (
            list(primary_glyphs) if primary_glyphs is not None else None
        )
        if (
            compact_labels is not None
            and len(compact_labels) != len(primary_items)
        ):
            raise ValueError(
                "primary_glyphs must provide one glyph for every primary "
                "quick-menu action",
            )
        self._entries = self._layout_row(
            primary_items,
            y,
            role="quick_primary",
            display_labels=compact_labels,
        )
        primary_rect = self._row_rect(self._entries)
        super().__init__(primary_rect)

        if overflow_items:
            overflow_y = y - height - gap
            self._overflow_entries = self._layout_row(
                list(overflow_items),
                overflow_y,
                role="quick_overflow",
            )
        self._overflow_rect = self._row_rect(self._overflow_entries)

    def _sp(self, value: int | float, *, minimum: int = 1) -> int:
        return max(minimum, int(round(float(value) * self.density)))

    def _layout_row(
        self,
        items: list[QuickMenuItem],
        y: int,
        *,
        role: str,
        display_labels: list[str] | None = None,
    ) -> list[tuple[Button, Callable[[], bool] | None]]:
        entries: list[tuple[Button, Callable[[], bool] | None]] = []
        x = self._right
        font = self.fonts.get(self._font_size, bold=True)
        compact = display_labels is not None
        visible_labels = (
            list(display_labels)
            if display_labels is not None
            else [label for label, *_ in items]
        )
        compact_pad = self._sp(4, minimum=4)
        if compact:
            # A fixed logical floor keeps the six-action rail near 320 px at
            # both 720p and 1080p while retaining full 44 px pointer/touch hit
            # rectangles. Only the painted glyph scales; target geometry does
            # not swell with translated semantic names.
            natural_widths = [
                max(48, font.size(label)[0] + compact_pad * 2)
                for label in visible_labels
            ]
        else:
            natural_widths = [
                font.size(label)[0] + self._sp(26)
                for label in visible_labels
            ]
        widths = list(natural_widths)
        available = max(1, self._right - self._left)
        gaps_width = self._gap * max(0, len(items) - 1)
        if sum(widths) + gaps_width > available and widths:
            # Translation strings are pack-controlled and can be much longer
            # than the compact Chinese defaults. Allocate a bounded row, first
            # preserving short labels and then sharing the remainder between
            # long labels. Text that still does not fit receives an ellipsis
            # and retains the full string as its tooltip.
            text_padding = compact_pad * 2 if compact else self._sp(16)
            natural_widths = [
                font.size(label)[0] + text_padding
                for label in visible_labels
            ]
            row_budget = max(len(items), available - gaps_width)
            cap = max(1, row_budget // len(items))
            widths = [min(width, cap) for width in natural_widths]
            remaining = row_budget - sum(widths)
            while remaining > 0:
                expandable = [
                    i for i, width in enumerate(widths)
                    if width < natural_widths[i]
                ]
                if not expandable:
                    break
                share = max(1, remaining // len(expandable))
                for i in expandable:
                    delta = min(share, natural_widths[i] - widths[i], remaining)
                    widths[i] += delta
                    remaining -= delta
                    if remaining <= 0:
                        break

        laid_out = list(zip(items, visible_labels, widths, strict=True))
        for (label, cb, active_fn), visible_label, width in reversed(
            laid_out,
        ):
            display_label = visible_label
            max_text_width = max(
                1,
                width - (compact_pad * 2 if compact else self._sp(12)),
            )
            if font.size(display_label)[0] > max_text_width:
                ellipsis = "…"
                trimmed = display_label
                while trimmed and font.size(trimmed + ellipsis)[0] \
                        > max_text_width:
                    trimmed = trimmed[:-1]
                display_label = trimmed + ellipsis if trimmed else ellipsis
            button = Button(
                pygame.Rect(x - width, y, width, self._height),
                label,
                fonts=self.fonts,
                theme=self.theme,
                font_size=self._font_size,
                display_label=display_label,
                content_padding=compact_pad if compact else None,
                style="ghost",
                on_click=cb,
                enabled=cb is not None,
                tooltip=label if display_label != label else None,
                chrome=self.chrome,
                screen="dialogue",
                role=role,
                motion=self.motion,
            )
            entries.insert(0, (button, active_fn))
            x -= width + self._gap
        return entries

    @staticmethod
    def _row_rect(
        entries: list[tuple[Button, Callable[[], bool] | None]],
    ) -> pygame.Rect:
        if not entries:
            return pygame.Rect(0, 0, 0, 0)
        rect = entries[0][0].rect.copy()
        for button, _active in entries[1:]:
            rect.union_ip(button.rect)
        return rect

    def _rail_rect(self, rect: pygame.Rect) -> pygame.Rect:
        """Return the complete painted rail, including its visible padding."""
        rail_pad = self._sp(self.theme.pad_s)
        return rect.inflate(rail_pad, rail_pad)

    @property
    def overflow_open(self) -> bool:
        return self._overflow_open

    @property
    def focused_label(self) -> str | None:
        return self._focus_button.label if self._focus_button is not None else None

    def toggle_overflow(self) -> None:
        self._overflow_open = not self._overflow_open

    def close_overflow(self) -> None:
        overflow_buttons = {
            button for button, _active in self._overflow_entries
        }
        if self._focus_button in overflow_buttons:
            self._focus_button = (
                self._entries[-1][0] if self._entries else None
            )
        self._overflow_open = False
        self._sync_focus()

    def visible_buttons(self) -> list[Button]:
        """Buttons that are currently visible and available to inspection."""
        if not self.visible:
            return []
        buttons = [button for button, _active in self._entries]
        if self._overflow_open:
            buttons.extend(
                button for button, _active in self._overflow_entries
            )
        return buttons

    def _draw_rail(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        *,
        role: str,
    ) -> None:
        if not rect.width or not rect.height:
            return
        rail_rect = self._rail_rect(rect)
        if self.chrome is not None and self.chrome.paint(
            surface,
            rail_rect,
            ChromeRequest(
                part="panel",
                screen="dialogue",
                variant=role,
                size=rail_rect.size,
                density=self.density,
            ),
        ):
            return
        rail = pygame.Surface(rail_rect.size, pygame.SRCALPHA)
        high_contrast = (
            getattr(self.theme, "visual_profile", "standard")
            == "high_contrast"
        )
        if high_contrast:
            fill = (*self.theme.bg_panel[:3], 255)
            border = self.theme.border_strong
            border_width = self._sp(2)
        else:
            # Reading chrome should remain subordinate to the authored art.
            # Hover/focus and active toggles supply the stronger states.
            fill = (*self.theme.bg_panel[:3], 176)
            border = self.theme.border_soft
            border_width = self._sp(1)
        pygame.draw.rect(
            rail,
            fill,
            rail.get_rect(),
            border_radius=self._sp(self.theme.radius_m),
        )
        pygame.draw.rect(
            rail,
            border,
            rail.get_rect(),
            width=border_width,
            border_radius=self._sp(self.theme.radius_m),
        )
        surface.blit(rail, rail_rect.topleft)

    def update(self, dt: float, inp) -> None:
        self._consumed_input = False
        self._hovered_tooltip = None
        if not self.visible:
            self._focus_button = None
            self._sync_focus()
            return
        # Escape/controller-cancel closes the disclosure layer first.  The
        # owning DialogueScene can then reserve a second Escape for the full
        # menu instead of unexpectedly doing both actions at once.
        if self._overflow_open and bool(getattr(inp, "cancel", False)):
            self.close_overflow()
            self._consumed_input = True
            return

        was_open = self._overflow_open
        visible_before = self.visible_buttons()
        if any(
            event.type == pygame.MOUSEMOTION
            for event in getattr(inp, "events", ())
        ):
            self._focus_button = next(
                (
                    button for button in visible_before
                    if button.enabled and button.rect.collidepoint(inp.mouse_pos)
                ),
                None,
            )

        focus_step = self._focus_step(inp)
        if focus_step:
            self._move_focus(focus_step, visible_before)
            self._consumed_input = True

        self._sync_focus()
        if (
            bool(getattr(inp, "confirm", False))
            and self._focus_button is not None
            and self._focus_button in visible_before
        ):
            focused = self._focus_button
            if focused.activate():
                self._consumed_input = True
                if any(
                    focused is button
                    for button, _active in self._overflow_entries
                ):
                    self.close_overflow()
                else:
                    self._sync_focus()
                return

        if was_open:
            for btn, _active in self._overflow_entries:
                btn.update(dt, inp)
        for btn, _active in self._entries:
            btn.update(dt, inp)
        hover_entries = list(self._entries)
        if was_open:
            hover_entries.extend(self._overflow_entries)
        self._hovered_tooltip = next(
            (
                (button, button.tooltip)
                for button, _active in hover_entries
                if (button._pointer_over or button.focused) and button.tooltip
            ),
            None,
        )

        if not bool(getattr(inp, "mouse_clicked", False)):
            return
        # The rail paints a padded panel around its buttons. That visible
        # surface is part of the control and must consume clicks too; otherwise
        # a click one pixel above a button can leak through and turn an ADV
        # page underneath it.
        in_primary = self._rail_rect(self.rect).collidepoint(inp.mouse_pos)
        in_overflow = (
            was_open
            and self._rail_rect(self._overflow_rect).collidepoint(
                inp.mouse_pos,
            )
        )
        if in_primary or in_overflow:
            self._consumed_input = True
            # A secondary action is a completed choice, so collapse the layer.
            if in_overflow:
                self.close_overflow()
                self._hovered_tooltip = None
        elif was_open:
            # Clicking elsewhere dismisses More but must not leak through and
            # advance the dialogue underneath.
            self.close_overflow()
            self._hovered_tooltip = None
            self._consumed_input = True

    @staticmethod
    def _focus_step(inp) -> int:
        """Return one keyboard/controller focus step for the reading rail."""
        if inp.pressed(InputAction.FOCUS_PREVIOUS):
            return -1
        if inp.pressed(InputAction.FOCUS_NEXT):
            return 1
        if (
            inp.pressed(InputAction.NAV_LEFT)
            or inp.pressed(InputAction.NAV_UP)
        ):
            return -1
        if (
            inp.pressed(InputAction.NAV_RIGHT)
            or inp.pressed(InputAction.NAV_DOWN)
        ):
            return 1
        return 0

    def _move_focus(self, step: int, buttons: list[Button] | None = None) -> None:
        enabled = [
            button
            for button in (buttons if buttons is not None else self.visible_buttons())
            if button.enabled
        ]
        if not enabled:
            self._focus_button = None
            self._sync_focus()
            return
        if self._focus_button not in enabled:
            self._focus_button = enabled[0] if step >= 0 else enabled[-1]
        else:
            index = enabled.index(self._focus_button)
            self._focus_button = enabled[
                (index + (1 if step >= 0 else -1)) % len(enabled)
            ]
        self._sync_focus()

    def _sync_focus(self) -> None:
        visible = set(self.visible_buttons())
        if self._focus_button not in visible:
            self._focus_button = None
        for button, _active in self._entries + self._overflow_entries:
            button.focused = button is self._focus_button

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        self._draw_rail(
            surface,
            self.rect,
            role="quick_primary",
        )
        if self._overflow_open:
            self._draw_rail(
                surface,
                self._overflow_rect,
                role="quick_overflow",
            )
        for btn, active_fn in self._entries:
            # Reflect toggle state: an active toggle reads as a filled button.
            active = bool(active_fn and active_fn())
            btn.style = "primary" if active else "ghost"
            btn.selected = active
            btn.draw(surface)
        if self._overflow_open:
            for btn, active_fn in self._overflow_entries:
                active = bool(active_fn and active_fn())
                btn.style = "primary" if active else "ghost"
                btn.selected = active
                btn.draw(surface)
        self._draw_tooltip(surface)

    @staticmethod
    def _wrap_tooltip(
        text: str,
        font: pygame.font.Font,
        max_width: int,
    ) -> list[str]:
        """Greedily wrap both spaced and CJK text without losing characters."""
        lines: list[str] = []
        current = ""
        for char in text:
            candidate = current + char
            if current and font.size(candidate)[0] > max_width:
                lines.append(current.rstrip())
                current = char.lstrip()
            else:
                current = candidate
        if current or not lines:
            lines.append(current.rstrip())
        return [line for line in lines if line] or [text]

    def _draw_tooltip(self, surface: pygame.Surface) -> None:
        self._tooltip_rect = None
        if self._hovered_tooltip is None:
            return
        button, text = self._hovered_tooltip
        sw, sh = surface.get_size()
        edge = self._sp(self.theme.pad_s)
        pad_x, pad_y = self._sp(12), self._sp(9)
        font_size = 16
        font = self.fonts.get(font_size)
        max_text_width = max(
            1,
            min(self._sp(360), sw - edge * 2 - pad_x * 2),
        )
        lines = self._wrap_tooltip(text, font, max_text_width)
        line_height = font.get_linesize()
        text_width = max(font.size(line)[0] for line in lines)
        width = text_width + pad_x * 2
        height = line_height * len(lines) + pad_y * 2
        rect = pygame.Rect(0, 0, width, height)
        rect.centerx = button.rect.centerx
        rect.bottom = button.rect.top - self._sp(self.theme.pad_s)
        if rect.top < edge:
            rect.top = button.rect.bottom + self._sp(self.theme.pad_s)
        rect.x = min(max(edge, rect.x), max(edge, sw - edge - rect.width))
        rect.y = min(max(edge, rect.y), max(edge, sh - edge - rect.height))

        bubble = pygame.Surface(rect.size, pygame.SRCALPHA)
        fill = self.theme.bg_overlay
        pygame.draw.rect(
            bubble,
            (*fill[:3], 248),
            bubble.get_rect(),
            border_radius=self._sp(self.theme.radius_s),
        )
        pygame.draw.rect(
            bubble,
            self.theme.accent_warm,
            bubble.get_rect(),
            width=self._sp(1),
            border_radius=self._sp(self.theme.radius_s),
        )
        surface.blit(bubble, rect.topleft)
        y = rect.y + pad_y
        for line in lines:
            rendered = self.fonts.render(line, font_size, self.theme.text)
            surface.blit(rendered, (rect.x + pad_x, y))
            y += line_height
        self._tooltip_rect = rect

    def consumed(self, inp) -> bool:
        """Whether this frame's click/cancel belonged to the quick menu.

        Merely parking the pointer over the quick menu must not swallow Space,
        Enter, or gamepad advance. This matters after a choice: the next
        dialogue's bar can appear underneath the position of the choice the
        player just clicked.
        """
        if not self.visible:
            return False
        if hasattr(self, "_consumed_input"):
            return self._consumed_input
        # Compatibility for lightweight test/plugin doubles created without
        # running this class's constructor.
        return (
            bool(getattr(inp, "mouse_clicked", False))
            and self.rect.collidepoint(inp.mouse_pos)
        )
