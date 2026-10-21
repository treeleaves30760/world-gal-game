"""Floating notification toasts.

Used for achievements and other in-line state changes. A toast slides in
from the top-right, holds for a few seconds, then slides out. Multiple
toasts stack vertically.  The widget's ``rect`` is a hard safe area:
animation, text, and cards are clipped to it.
"""

from __future__ import annotations

from dataclasses import dataclass

import pygame

from .base import Widget
from ..fonts import FontRegistry
from ..motion import MotionKind, MotionPolicy, coerce_motion_policy
from ..theme import Theme


@dataclass
class Toast:
    title: str
    detail: str = ""
    icon: str | None = None
    duration: float = 4.0
    age: float = 0.0
    # state populated by ToastStack each frame
    visible_alpha: int = 0


class ToastStack(Widget):
    """A non-interactive widget that renders a stack of toasts.

    Place an instance high in the scene stack so it's drawn over
    everything else, and feed it toasts via .push().

    Only the first two toasts are active.  Additional messages stay in the
    public ``toasts`` list as a FIFO queue and do not begin ageing until a
    visible slot opens.
    """

    MAX_VISIBLE = 2

    def __init__(
        self,
        rect: pygame.Rect,
        *,
        fonts: FontRegistry,
        theme: Theme,
        assets=None,
        slide_in: float = 0.35,
        hold: float | None = None,
        reduce_motion: bool = False,
        motion: MotionPolicy | None = None,
    ):
        super().__init__(rect)
        self.fonts = fonts
        self.theme = theme
        self.assets = assets
        self.slide_in = max(0.0, float(slide_in))
        # ``hold`` is retained for API compatibility.  Individual Toast
        # durations remain authoritative, as they were before.
        self.hold = hold
        self.motion = coerce_motion_policy(
            motion,
            reduce_motion=reduce_motion,
        )
        self.toasts: list[Toast] = []

    @property
    def reduce_motion(self) -> bool:
        """Backward-compatible view of the live central policy."""
        return self.motion.reduced

    def push(self, toast: Toast) -> None:
        self.toasts.append(toast)

    def update(self, dt: float, inp) -> None:
        # A queued notification should remain readable for its full duration
        # after promotion.  Consequently, only the two active entries age.
        for toast in self.toasts[: self.MAX_VISIBLE]:
            toast.age += max(0.0, float(dt))
            toast.visible_alpha = self._alpha_for(toast)
        self.toasts = [t for t in self.toasts if t.age < t.duration]

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible or not self.toasts:
            return

        # Draw into a clipped intermediate surface.  A slide-in offset may
        # approach the safe area's right edge, but can never paint beyond it.
        safe = self.rect.clip(surface.get_rect())
        if safe.width <= 0 or safe.height <= 0:
            return
        layer = pygame.Surface(safe.size, pygame.SRCALPHA)
        active = self.toasts[: self.MAX_VISIBLE]
        scale = max(0.1, float(getattr(self.fonts, "scale", 1.0)))
        title_font = self.fonts.get(20, bold=True)
        detail_font = self.fonts.get(14)
        title_line_h = max(1, title_font.get_linesize())
        detail_line_h = max(1, detail_font.get_linesize())

        edge_x = min(
            max(1, int(round(self.theme.pad_l * scale))),
            max(1, safe.width // 4),
        )
        edge_y = min(
            max(1, int(round(self.theme.pad_l * scale))),
            max(1, safe.height // 4),
        )
        available_w = max(1, safe.width - edge_x * 2)
        available_h = max(1, safe.height - edge_y * 2)
        gap = max(0, int(round(self.theme.pad_s * scale)))
        if len(active) > 1:
            gap = min(gap, max(0, available_h - len(active)))
        max_card_h = max(
            1,
            (available_h - gap * (len(active) - 1)) // len(active),
        )

        y = edge_y
        for toast in active:
            layout = self._layout_toast(
                toast,
                available_w=available_w,
                max_card_h=max_card_h,
                title_font=title_font,
                detail_font=detail_font,
                title_line_h=title_line_h,
                detail_line_h=detail_line_h,
                scale=scale,
            )
            card_w, card_h, title_text, detail_lines, use_icon = layout
            alpha = max(0, min(255, int(toast.visible_alpha)))
            if alpha:
                card = self._render_card(
                    toast,
                    card_w=card_w,
                    card_h=card_h,
                    title_text=title_text,
                    detail_lines=detail_lines,
                    use_icon=use_icon,
                    title_font=title_font,
                    detail_font=detail_font,
                    title_line_h=title_line_h,
                    detail_line_h=detail_line_h,
                    scale=scale,
                    alpha=alpha,
                )
                x = safe.width - edge_x - card_w
                x += self._slide_offset(toast, card_w, scale)
                layer.blit(card, (x, y))
            y += card_h + gap
        surface.blit(layer, safe.topleft)

    def _alpha_for(self, toast: Toast) -> int:
        """Return a stable fade value, including for very short durations."""
        duration = max(0.0, float(toast.duration))
        if toast.age >= duration:
            return 0
        # Opacity is the non-geometric fallback.  The hold/read duration stays
        # authored, while reduced motion caps only the transient hand-off.
        fade = self.motion.duration(
            min(self.slide_in, duration / 2.0),
            MotionKind.OPACITY,
        )
        if fade <= 0.0:
            return 255
        if toast.age < fade:
            ratio = toast.age / fade
        elif toast.age > duration - fade:
            ratio = (duration - toast.age) / fade
        else:
            ratio = 1.0
        return int(round(255 * max(0.0, min(1.0, ratio))))

    def _slide_offset(self, toast: Toast, card_w: int, scale: float) -> int:
        """Geometric motion is optional; opacity still conveys state."""
        if not self.motion.allows(MotionKind.SPATIAL) or self.slide_in <= 0.0:
            return 0
        if toast.age >= self.slide_in:
            return 0
        distance = min(max(1, int(round(40 * scale))), max(1, card_w // 4))
        progress = max(0.0, min(1.0, toast.age / self.slide_in))
        return int(round(distance * (1.0 - progress)))

    def _layout_toast(
        self,
        toast: Toast,
        *,
        available_w: int,
        max_card_h: int,
        title_font: pygame.font.Font,
        detail_font: pygame.font.Font,
        title_line_h: int,
        detail_line_h: int,
        scale: float,
    ) -> tuple[int, int, str, tuple[str, ...], bool]:
        """Measure a toast from live font metrics and its safe-area budget."""
        pad_x = max(2, int(round(self.theme.pad_m * scale)))
        pad_y = max(2, int(round(self.theme.pad_s * scale)))
        line_gap = max(1, int(round(self.theme.pad_xs * scale)))
        bar_w = max(2, int(round(6 * scale)))
        icon_size = max(
            title_line_h,
            min(
                title_line_h + detail_line_h + line_gap,
                max(1, int(round(52 * scale))),
            ),
        )
        # On a narrow safe area an icon would consume the reading column, so
        # gracefully fall back to the accent bar.
        use_icon = bool(
            toast.icon
            and self.assets is not None
            and available_w >= icon_size + pad_x * 4 + title_line_h * 3
        )
        leading_w = icon_size if use_icon else bar_w
        chrome_w = pad_x * 3 + leading_w

        max_card_w = min(
            available_w,
            max(
                int(round(420 * scale)),
                title_font.size("M" * 20)[0] + chrome_w,
            ),
        )
        max_text_w = max(1, max_card_w - chrome_w)
        normalized_title = " ".join(str(toast.title).split())
        normalized_detail = " ".join(str(toast.detail).split())
        measured_text_w = max(
            title_font.size(normalized_title)[0],
            detail_font.size(normalized_detail)[0] if normalized_detail else 0,
        )
        minimum_text_w = max(
            title_font.size("Notice")[0],
            detail_font.size("M" * 18)[0],
        )
        desired_text_w = min(
            max_text_w,
            max(minimum_text_w, measured_text_w),
        )
        card_w = max(1, min(available_w, chrome_w + desired_text_w))
        text_w = max(1, card_w - chrome_w)

        title_text = self._ellipsize(
            normalized_title,
            title_font,
            text_w,
        )
        detail_lines = self._wrap_text(
            normalized_detail,
            detail_font,
            text_w,
            max_lines=2,
        )
        natural_text_h = pad_y * 2 + title_line_h
        if detail_lines:
            natural_text_h += line_gap + detail_line_h * len(detail_lines)
        natural_h = natural_text_h
        if use_icon:
            natural_h = max(natural_h, pad_y * 2 + icon_size)
        card_h = max(1, min(max_card_h, natural_h))

        # Re-evaluate how many body lines fit after the vertical safe-area cap.
        effective_pad_y = min(pad_y, max(0, (card_h - title_line_h) // 2))
        detail_budget = card_h - effective_pad_y * 2 - title_line_h - line_gap
        fitting_lines = max(0, detail_budget // detail_line_h)
        if fitting_lines < len(detail_lines):
            detail_lines = self._wrap_text(
                normalized_detail,
                detail_font,
                text_w,
                max_lines=min(2, fitting_lines),
            )
        return card_w, card_h, title_text, detail_lines, use_icon

    def _render_card(
        self,
        toast: Toast,
        *,
        card_w: int,
        card_h: int,
        title_text: str,
        detail_lines: tuple[str, ...],
        use_icon: bool,
        title_font: pygame.font.Font,
        detail_font: pygame.font.Font,
        title_line_h: int,
        detail_line_h: int,
        scale: float,
        alpha: int,
    ) -> pygame.Surface:
        card = pygame.Surface((card_w, card_h), pygame.SRCALPHA)
        radius = min(
            max(0, int(round(self.theme.radius_m * scale))),
            card_w // 2,
            card_h // 2,
        )
        border_w = max(1, int(round(2 * scale)))
        pygame.draw.rect(
            card,
            (*self.theme.bg_overlay[:3], min(alpha, 235)),
            card.get_rect(),
            border_radius=radius,
        )
        pygame.draw.rect(
            card,
            (*self.theme.accent_warm[:3], alpha),
            card.get_rect(),
            width=min(border_w, max(1, min(card_w, card_h))),
            border_radius=radius,
        )

        pad_x = max(2, int(round(self.theme.pad_m * scale)))
        pad_y = max(2, int(round(self.theme.pad_s * scale)))
        line_gap = max(1, int(round(self.theme.pad_xs * scale)))
        bar_w = max(2, int(round(6 * scale)))
        icon_size = max(
            title_line_h,
            min(
                title_line_h + detail_line_h + line_gap,
                max(1, int(round(52 * scale))),
            ),
        )
        if use_icon:
            image = self.assets.scaled(
                toast.icon,
                (icon_size, icon_size),
                fit="contain",
            ).copy()
            image.set_alpha(alpha)
            card.blit(image, (pad_x, max(0, (card_h - icon_size) // 2)))
            text_x = pad_x * 2 + icon_size
        else:
            bar_h = max(1, card_h - pad_y * 2)
            bar = pygame.Surface((bar_w, bar_h), pygame.SRCALPHA)
            bar.fill((*self.theme.accent_warm[:3], alpha))
            card.blit(bar, (pad_x, min(pad_y, max(0, card_h - bar_h))))
            text_x = pad_x * 2 + bar_w

        effective_pad_y = min(pad_y, max(0, (card_h - title_line_h) // 2))
        if title_text and title_line_h <= card_h:
            title = title_font.render(
                title_text,
                True,
                self.theme.text[:3],
            )
            title.set_alpha(alpha)
            card.blit(title, (text_x, effective_pad_y))
        detail_y = effective_pad_y + title_line_h + line_gap
        for line in detail_lines:
            if detail_y + detail_line_h > card_h:
                break
            detail = detail_font.render(
                line,
                True,
                self.theme.text_mute[:3],
            )
            detail.set_alpha(alpha)
            card.blit(detail, (text_x, detail_y))
            detail_y += detail_line_h
        return card

    @classmethod
    def _ellipsize(
        cls,
        text: str,
        font: pygame.font.Font,
        max_width: int,
    ) -> str:
        if not text or max_width <= 0:
            return ""
        if font.size(text)[0] <= max_width:
            return text
        ellipsis = "…"
        if font.size(ellipsis)[0] > max_width:
            return ""
        low, high = 0, len(text)
        while low < high:
            mid = (low + high + 1) // 2
            if font.size(text[:mid] + ellipsis)[0] <= max_width:
                low = mid
            else:
                high = mid - 1
        return text[:low].rstrip() + ellipsis

    @classmethod
    def _wrap_text(
        cls,
        text: str,
        font: pygame.font.Font,
        max_width: int,
        *,
        max_lines: int,
    ) -> tuple[str, ...]:
        """Wrap Latin/CJK text using measured glyph widths, then ellipsize."""
        if not text or max_width <= 0 or max_lines <= 0:
            return ()
        remaining = " ".join(text.split())
        lines: list[str] = []
        while remaining and len(lines) < max_lines:
            if len(lines) == max_lines - 1:
                lines.append(cls._ellipsize(remaining, font, max_width))
                break
            if font.size(remaining)[0] <= max_width:
                lines.append(remaining)
                break
            fit = cls._fitting_prefix(remaining, font, max_width)
            if fit <= 0:
                break
            split = remaining.rfind(" ", 0, fit + 1)
            if split > 0 and split >= fit // 2:
                line = remaining[:split].rstrip()
                remaining = remaining[split + 1 :].lstrip()
            else:
                line = remaining[:fit].rstrip()
                remaining = remaining[fit:].lstrip()
            if line:
                lines.append(line)
        return tuple(line for line in lines if line)

    @staticmethod
    def _fitting_prefix(
        text: str,
        font: pygame.font.Font,
        max_width: int,
    ) -> int:
        low, high = 0, len(text)
        while low < high:
            mid = (low + high + 1) // 2
            if font.size(text[:mid])[0] <= max_width:
                low = mid
            else:
                high = mid - 1
        return low
