"""Choice menu shown at a decision point in a scene."""
from __future__ import annotations

import math
from typing import Callable, TYPE_CHECKING

import pygame

from ...core.story_graph import ChoiceCardSpec
from .base import Widget
from ..input_bindings import InputAction
from .button import Button
from .label import _wrap_lines
from .panel import Panel
from ..fonts import FontRegistry
from ..motion import MotionPolicy, coerce_motion_policy
from ..system_chrome import ChromeRequest
from ..theme import Theme

if TYPE_CHECKING:
    from ..assets import AssetManager
    from ..system_chrome import SystemChromeHost


class ChoiceMenu(Widget):
    def __init__(self, rect: pygame.Rect, *, fonts: FontRegistry, theme: Theme,
                 on_choose: Callable[[str], None],
                 chrome: "SystemChromeHost | None" = None,
                 assets: "AssetManager | None" = None,
                 controls_hint: str = "方向鍵／滑鼠選擇 · Enter 確認",
                 prompt: str = "請選擇",
                 card_prompt: str = "請選擇接下來的方向",
                 locked_label: str = "尚未開放",
                 scroll_hint: str = "↑／↓／滾輪閱讀",
                 text_scale: float = 1.0,
                 motion: MotionPolicy | None = None):
        super().__init__(rect)
        self.fonts = fonts
        self.theme = theme
        self.on_choose = on_choose
        self.chrome = chrome
        self.assets = assets
        self.controls_hint = controls_hint
        self.prompt = prompt
        self.card_prompt = card_prompt
        self.locked_label = locked_label
        self.scroll_hint = scroll_hint
        self._text_scale = self._clamp_text_scale(text_scale)
        self.motion = coerce_motion_policy(motion)
        self.buttons: list[Button] = []
        self.panel: Panel | None = None
        self._prompt_pos = (0, 0)
        self._prompt_surface: pygame.Surface | None = None
        self._prompt_rect = pygame.Rect(0, 0, 0, 0)
        # Live layout metrics are retained for visual-regression tests and for
        # future scene-level safe-area checks.  FontRegistry scales glyphs with
        # the canvas; every surrounding measurement must follow that same
        # density or a tall CJK font will collide with the first choice.
        self._density = 1.0
        self._prompt_line_h = 0
        self._button_line_h = 0
        self._reason_line_h = 0
        self._panel_pad_x = 0
        self._panel_pad_y = 0
        self._title_gap = 0
        self._title_h = 0
        self._button_h = 0
        self._row_gap = 0
        self._reason_gap = 0
        # Keyboard/gamepad focus.  Keep ``-1`` as "no focus" to match the
        # MenuScene/SettingsScene convention: the first up/down press chooses
        # the last/first enabled row without moving past it.
        self._focus: int = -1
        self._rows: list[
            tuple[str, str, bool, str, ChoiceCardSpec | None]
        ] = []
        # Parallel to self.buttons: the lock-reason string for each row ("" when
        # the choice is enabled) and where to draw its caption (None = no
        # caption). Populated by set_choices.
        self._reasons: list[str] = []
        self._reason_anchors: list[tuple[int, int] | None] = []
        # Card mode is an explicit all-options contract. A normal three-choice
        # scene remains the established vertical list unless every visible
        # option supplies strict ``ChoiceCardSpec`` metadata.
        self._layout_mode = "list"
        self._cards: list[ChoiceCardSpec | None] = []
        self._card_regions: list[dict[str, pygame.Rect]] = []
        self._card_compact = False
        self._help_surface: pygame.Surface | None = None
        self._help_pos = (0, 0)
        # Large-text comparison cards use a measured tab + full-card page
        # layout. Every option remains a real Button target, while one
        # complete card document is shown below the tabs and may scroll rather
        # than dropping fields that no longer fit a three-column grid.
        self._accessible_preview_index = 0
        self._accessible_preview_rect = pygame.Rect(0, 0, 0, 0)
        self._accessible_scroll_y = 0
        self._accessible_scroll_max = 0
        self._accessible_document_height = 0
        self._accessible_action_button: Button | None = None
        self._accessible_lock_reason_rect = pygame.Rect(0, 0, 0, 0)
        self._accessible_touch_dragging = False
        self._accessible_touch_moved = False
        # The full comparison document can exceed 16 MiB at 4K. It is static
        # while the selected preview and render inputs are unchanged, so keep
        # exactly one page surface and scroll by source rect instead of
        # rebuilding/wrapping it every frame.
        self._accessible_document_cache_key: tuple[object, ...] | None = None
        self._accessible_document_cache: pygame.Surface | None = None
        # Stock chrome is also static between layout/theme changes.  Retaining
        # these exact alpha surfaces removes three large per-frame allocations
        # at 4K while preserving the established blit order over a live scene.
        # Pack chrome remains owned by SystemChromeHost, never these fallbacks.
        self._fallback_backdrop_cache_key: tuple[object, ...] | None = None
        self._fallback_backdrop_cache: pygame.Surface | None = None
        self._accessible_page_cache_key: tuple[object, ...] | None = None
        self._accessible_page_cache: pygame.Surface | None = None
        # Prompt/help glyphs are small but profile-dependent. They are laid
        # out once with the choice, then refreshed lazily when a live visual
        # profile changes or when a departed choice releases render memory.
        self._prerendered_text_cache_key: tuple[object, ...] | None = None

    @staticmethod
    def _clamp_text_scale(value: float) -> float:
        try:
            scale = float(value)
        except (TypeError, ValueError):
            return 1.0
        if not math.isfinite(scale):
            return 1.0
        return max(1.0, min(1.4, scale))

    def _font_size(self, logical_size: int) -> int:
        return max(1, int(round(logical_size * self._text_scale)))

    def _prerendered_text_key(self) -> tuple[object, ...]:
        card_layout = self._layout_mode in {"cards", "cards_accessible"}
        help_text = (
            f"{self.controls_hint} · {self.scroll_hint}"
            if self._layout_mode == "cards_accessible"
            else (self.controls_hint if card_layout else None)
        )
        return (
            self._layout_mode,
            self.card_prompt if card_layout else self.prompt,
            help_text,
            self._font_size(18),
            self._font_size(13),
            getattr(self.theme, "visual_profile", "standard"),
            tuple(self.theme.accent_warm),
            tuple(self.theme.text_mute),
            id(self.fonts),
            getattr(self.fonts, "revision", None),
        )

    def _sync_prerendered_text(self) -> None:
        """Refresh prompt/help glyphs from the live resolved theme."""
        key = self._prerendered_text_key()
        card_layout = self._layout_mode in {"cards", "cards_accessible"}
        needs_help = card_layout
        if (
            key == self._prerendered_text_cache_key
            and self._prompt_surface is not None
            and (not needs_help or self._help_surface is not None)
        ):
            return

        self._prompt_surface = self.fonts.render(
            self.card_prompt if card_layout else self.prompt,
            self._font_size(18),
            self.theme.accent_warm,
            bold=True,
        )
        if self.panel is not None:
            self._prompt_rect = self._prompt_surface.get_rect()
            if card_layout:
                self._prompt_rect.midtop = (
                    self.panel.rect.centerx,
                    self.panel.rect.y + self._panel_pad_y,
                )
            else:
                self._prompt_rect.centerx = self.panel.rect.centerx
                self._prompt_rect.y = (
                    self.panel.rect.y
                    + self._panel_pad_y
                    + (
                        self._prompt_line_h
                        - self._prompt_rect.height
                    ) // 2
                )
            self._prompt_pos = self._prompt_rect.topleft

        if self._layout_mode == "cards_accessible":
            help_text = f"{self.controls_hint} · {self.scroll_hint}"
        elif self._layout_mode == "cards":
            help_text = self.controls_hint
        else:
            help_text = None
        if help_text is None:
            self._help_surface = None
        else:
            self._help_surface = self.fonts.render(
                help_text,
                self._font_size(13),
                self.theme.text_mute,
            )
            if self.panel is not None:
                help_rect = self._help_surface.get_rect()
                help_rect.midbottom = (
                    self.panel.rect.centerx,
                    self.panel.rect.bottom - self._panel_pad_y,
                )
                self._help_pos = help_rect.topleft
        self._prerendered_text_cache_key = key

    def release_render_cache(self) -> None:
        """Release heavy choice paint caches while retaining interaction state.

        Focus, selected preview, scroll position, rows, and callbacks survive,
        so rollback or another genuine presentation of this decision can
        reconstruct identical pixels on demand. Modal overlays deliberately do
        not call this method: a paused choice remains warm underneath them.
        """
        self._accessible_document_cache_key = None
        self._accessible_document_cache = None
        self._accessible_page_cache_key = None
        self._accessible_page_cache = None
        self._fallback_backdrop_cache_key = None
        self._fallback_backdrop_cache = None
        self._prompt_surface = None
        self._help_surface = None
        self._prerendered_text_cache_key = None
        if self.panel is not None:
            self.panel.release_render_cache()
        if self.chrome is not None:
            self.chrome.clear_cache(screen="choice")

    @property
    def layout_mode(self) -> str:
        return self._layout_mode

    @property
    def text_scale(self) -> float:
        return self._text_scale

    @property
    def focused_index(self) -> int | None:
        """Index of the keyboard/gamepad-focused choice, if any."""
        if 0 <= self._focus < len(self.buttons):
            return self._focus
        return None

    @property
    def focused_choice_id(self) -> str | None:
        index = self.focused_index
        if index is None or index >= len(self._rows):
            return None
        return self._rows[index][0]

    def set_text_scale(self, value: float) -> bool:
        """Reflow the current decision while preserving focus by choice id.

        Settings can cover a live choice scene. Rebuilding by numeric index
        would risk arming a different option if a renderer changed its layout;
        the stable authored choice id is therefore the preservation key.
        """
        scale = self._clamp_text_scale(value)
        if math.isclose(scale, self._text_scale, abs_tol=1e-9):
            return False
        focused_id = self.focused_choice_id
        preview_id = (
            self._rows[self._accessible_preview_index][0]
            if self._rows
            and 0 <= self._accessible_preview_index < len(self._rows)
            else None
        )
        rows = list(self._rows)
        self._text_scale = scale
        if not rows:
            return True
        self.set_choices(rows)
        if preview_id is not None:
            self._accessible_preview_index = next(
                (
                    index
                    for index, row in enumerate(self._rows)
                    if row[0] == preview_id
                ),
                self._accessible_preview_index,
            )
        if focused_id is None:
            # A genuine multi-option decision must stay unarmed. A single
            # enabled onboarding row retains its established auto-focus.
            if sum(button.enabled for button in self.buttons) != 1:
                self._focus = -1
        else:
            self._focus = next(
                (
                    index
                    for index, row in enumerate(self._rows)
                    if (
                        row[0] == focused_id
                        and (
                            self._layout_mode == "cards_accessible"
                            or self.buttons[index].enabled
                        )
                    )
                ),
                -1,
            )
            if self._focus >= 0:
                self._accessible_preview_index = self._focus
        for index, button in enumerate(self.buttons):
            button.focused = (
                index == self._focus
                and (
                    self._layout_mode == "cards_accessible"
                    or button.enabled
                )
            )
        if self._layout_mode == "cards_accessible":
            self._sync_accessible_action_button()
        return True

    def set_choices(self, choices: list[tuple]) -> None:
        """Set the choice rows.

        Each row is ``(choice_id, label, enabled)``, optionally followed by a
        lock ``reason`` and a typed ``ChoiceCardSpec``. The fifth value is an
        additive presentation contract; legacy 3/4-tuples retain the original
        vertical list and pixels.
        """
        self.buttons = []
        self._layout_mode = "list"
        self._cards = []
        self._card_regions = []
        self._help_surface = None
        self._accessible_preview_rect = pygame.Rect(0, 0, 0, 0)
        self._accessible_scroll_y = 0
        self._accessible_scroll_max = 0
        self._accessible_document_height = 0
        self._accessible_action_button = None
        self._accessible_lock_reason_rect = pygame.Rect(0, 0, 0, 0)
        self._accessible_touch_dragging = False
        self._accessible_touch_moved = False
        self._accessible_document_cache_key = None
        self._accessible_document_cache = None
        self._accessible_page_cache_key = None
        self._accessible_page_cache = None
        self._prerendered_text_cache_key = None
        # A new decision starts unfocused. This prevents a focus index from the
        # previous decision from selecting an unrelated row after the menu is
        # rebuilt.
        self._focus = -1
        # Normalise to (cid, label, enabled, reason, card).
        rows: list[
            tuple[str, str, bool, str, ChoiceCardSpec | None]
        ] = []
        for row in choices:
            cid, label, enabled = row[0], row[1], row[2]
            reason = row[3] if len(row) > 3 else ""
            raw_card = row[4] if len(row) > 4 else None
            card = (
                raw_card
                if isinstance(raw_card, ChoiceCardSpec)
                else (
                    ChoiceCardSpec.model_validate(raw_card)
                    if raw_card is not None
                    else None
                )
            )
            rows.append((cid, label, bool(enabled), reason or "", card))
        self._rows = list(rows)
        self._reasons = [r[3] for r in rows]
        self._cards = [r[4] for r in rows]

        if (
            2 <= len(rows) <= 4
            and all(card is not None for card in self._cards)
        ):
            self._layout_mode = "cards"
            self._set_card_choices(rows)
            self._prerendered_text_cache_key = self._prerendered_text_key()
            return

        n = max(1, len(rows))
        self._density = max(
            1.0,
            float(getattr(self.fonts, "scale", 1.0)),
        )

        def scaled(logical_px: int, *, minimum: int = 1) -> int:
            return max(minimum, int(round(logical_px * self._density)))

        prompt_font_size = self._font_size(18)
        button_font_size = self._font_size(18)
        reason_font_size = self._font_size(16)
        prompt_font = self.fonts.get(prompt_font_size, bold=True)
        button_font = self.fonts.get(button_font_size, bold=True)
        reason_font = self.fonts.get(reason_font_size)
        self._prompt_surface = self.fonts.render(
            self.prompt,
            prompt_font_size,
            self.theme.accent_warm,
            bold=True,
        )
        self._prompt_line_h = max(
            1,
            prompt_font.get_linesize(),
            self._prompt_surface.get_height(),
        )
        self._button_line_h = max(1, button_font.get_linesize())
        self._reason_line_h = max(1, reason_font.get_linesize())

        # Derive the old 720p rhythm from theme tokens, then scale it with the
        # same density as typography.  This keeps the established composition
        # while removing resolution-specific fixed-pixel assumptions.
        desired_panel_pad_x = scaled(
            self.theme.pad_xl + self.theme.pad_m + self.theme.pad_xs,
        )
        self._panel_pad_y = scaled(self.theme.pad_l + self.theme.pad_xs)
        self._title_gap = scaled(self.theme.pad_m)
        self._row_gap = scaled(self.theme.pad_m)
        self._reason_gap = scaled(self.theme.pad_xs)
        button_pad_y = scaled(self.theme.pad_m)
        minimum_target = scaled(44)
        self._button_h = max(
            minimum_target,
            self._button_line_h + button_pad_y * 2,
        )
        self._title_h = (
            self._panel_pad_y
            + self._prompt_line_h
            + self._title_gap
        )

        # 760 is a logical 720p design width, not a device-pixel width.  Scale
        # it with typography and retain a density-scaled safe edge on narrower
        # canvases.
        safe_edge = scaled(
            max(1, self.theme.pad_m - self.theme.pad_xs),
        )
        desired_btn_w = scaled(760)
        max_panel_w = max(1, self.rect.width - safe_edge * 2)
        desired_panel_w = desired_btn_w + desired_panel_pad_x * 2
        panel_w = min(desired_panel_w, max_panel_w)
        self._panel_pad_x = min(
            desired_panel_pad_x,
            max(0, (panel_w - 1) // 2),
        )
        btn_w = max(1, panel_w - self._panel_pad_x * 2)

        # Extra space under a locked button is based on that font's live line
        # height.  A fixed 28 px block clipped at 1080p when the caption grew to
        # roughly 35 px.
        reason_h = self._reason_gap + self._reason_line_h
        # Per-row vertical extent (button + optional reason caption).
        row_heights = [
            self._button_h + (reason_h if row[3] else 0)
            for row in rows
        ] or [self._button_h]
        total_h = (
            self._title_h
            + sum(row_heights)
            + (n - 1) * self._row_gap
            + self._panel_pad_y
        )
        panel_rect = pygame.Rect(0, 0, panel_w, total_h)
        panel_rect.center = self.rect.center
        self.panel = Panel(panel_rect, self.theme,
                           fill=(*self.theme.bg_overlay[:3], 240),
                           border=self.theme.border_strong,
                           radius=self.theme.radius_l, border_width=2,
                           chrome=self.chrome,
                           screen="choice",
                           role="main",
                           density=self._density)
        prompt_line_top = panel_rect.y + self._panel_pad_y
        self._prompt_rect = self._prompt_surface.get_rect()
        self._prompt_rect.centerx = panel_rect.centerx
        self._prompt_rect.y = (
            prompt_line_top
            + (self._prompt_line_h - self._prompt_rect.height) // 2
        )
        self._prompt_pos = self._prompt_rect.topleft
        # Reason captions are positioned in draw(); remember their anchors here.
        self._reason_anchors: list[tuple[int, int] | None] = []
        y = panel_rect.y + self._title_h
        for i, (cid, label, enabled, reason, _card) in enumerate(rows):
            r = pygame.Rect(
                panel_rect.centerx - btn_w // 2,
                y,
                btn_w,
                self._button_h,
            )
            b = Button(r, label, fonts=self.fonts, theme=self.theme,
                       font_size=button_font_size,
                       on_click=(lambda cid=cid: self.on_choose(cid))
                                if enabled else None,
                       enabled=enabled,
                       style="primary" if enabled else "ghost",
                       chrome=self.chrome,
                       screen="choice",
                       role="option",
                       motion=self.motion)
            self.buttons.append(b)
            if reason:
                self._reason_anchors.append(
                    (r.centerx, r.bottom + self._reason_gap),
                )
                y += self._button_h + reason_h + self._row_gap
            else:
                self._reason_anchors.append(None)
                y += self._button_h + self._row_gap
        # A single actionable row is not a meaningful "which option?" decision:
        # it is usually an onboarding/continue gate. Give it immediate
        # keyboard/gamepad focus so the Space/Enter controls just taught to the
        # player work. Genuine multi-option decisions remain deliberately
        # unarmed until navigation or pointer movement selects one.
        enabled_indices = [
            index for index, button in enumerate(self.buttons)
            if button.enabled
        ]
        if len(enabled_indices) == 1:
            self._focus = enabled_indices[0]
        self._prerendered_text_cache_key = self._prerendered_text_key()

    def _card_grid_fits(
        self,
        rows: list[
            tuple[str, str, bool, str, ChoiceCardSpec | None]
        ],
    ) -> bool:
        """Whether the comparison grid can show every authored text field.

        This is deliberately a measurement, not a resolution or scene-id
        special case. If any card would ellipsize or clip a field, large-text
        users receive the generic tabbed full-card document instead.
        """
        tolerance = max(1, int(round(2 * self._density)))
        compact = self._card_compact

        def line_fits(
            text: str,
            *,
            size: int,
            rect: pygame.Rect,
            bold: bool = False,
        ) -> bool:
            if not text:
                return True
            font = self.fonts.get(size, bold=bold)
            width, height = font.size(" ".join(text.split()))
            return (
                width <= rect.width + tolerance
                and height <= rect.height + tolerance
            )

        for row, regions in zip(rows, self._card_regions, strict=True):
            card = row[4]
            if card is None:
                return False
            header = regions["header"]
            meta_h = max(
                12,
                int(round((14 if compact else 16) * self._density)),
            )
            meta = pygame.Rect(header.x, header.y, header.width, meta_h)
            number_font = self.fonts.get(
                self._font_size(10 if compact else 11),
                bold=True,
            )
            badge_font = number_font
            number_w = number_font.size("00")[0]
            badge_w = badge_font.size(card.badge)[0] if card.badge else 0
            if number_w + badge_w + tolerance * 2 > meta.width:
                return False
            if max(number_font.get_height(), badge_font.get_height()) > (
                meta.height + tolerance
            ):
                return False

            title_y = meta.bottom
            title_h = max(
                18,
                int(round((24 if compact else 30) * self._density)),
            )
            title_rect = pygame.Rect(
                header.x,
                title_y,
                header.width,
                min(title_h, max(1, header.bottom - title_y)),
            )
            if not line_fits(
                card.title,
                size=self._font_size(19 if compact else 24),
                rect=title_rect,
                bold=True,
            ):
                return False
            subtitle_rect = pygame.Rect(
                header.x,
                title_rect.bottom,
                header.width,
                max(0, header.bottom - title_rect.bottom),
            )
            if card.subtitle and not line_fits(
                card.subtitle,
                size=self._font_size(11 if compact else 14),
                rect=subtitle_rect,
            ):
                return False

            details_rect = regions["details"]
            if card.details:
                if details_rect.height <= 0:
                    return False
                row_h = max(1, details_rect.height // len(card.details))
                label_size = self._font_size(10 if compact else 13)
                body_size = self._font_size(11 if compact else 15)
                label_font = self.fonts.get(label_size, bold=True)
                body_font = self.fonts.get(body_size)
                for detail in card.details:
                    label_h = min(
                        row_h,
                        max(12, label_font.get_linesize()),
                    )
                    if (
                        label_font.size(detail.label)[0]
                        > details_rect.width + tolerance
                    ):
                        return False
                    body_h = max(0, row_h - label_h)
                    lines = self._complete_wrap_lines(
                        detail.text,
                        body_font,
                        details_rect.width,
                    )
                    max_lines = 1 if compact else 2
                    if (
                        not lines
                        or len(lines) > max_lines
                        or len(lines) * body_font.get_linesize()
                        > body_h + tolerance
                    ):
                        return False

            tags_rect = regions["tags"]
            if card.tags:
                if tags_rect.height <= 0:
                    return False
                tag_gap = max(3, int(round(5 * self._density)))
                tag_w = max(
                    1,
                    (
                        tags_rect.width
                        - tag_gap * (len(card.tags) - 1)
                    ) // len(card.tags),
                )
                tag_font = self.fonts.get(
                    self._font_size(11 if compact else 12),
                )
                for tag in card.tags:
                    width, height = tag_font.size(tag)
                    if (
                        width > max(0, tag_w - 6) + tolerance
                        or height > tags_rect.height + tolerance
                    ):
                        return False

            action = regions["action"]
            action_text = (
                card.action or row[1]
                if row[2]
                else (row[3] or self.locked_label)
            )
            if not line_fits(
                action_text,
                size=self._font_size(11 if compact else 14),
                rect=action.inflate(-8, 0),
                bold=True,
            ):
                return False
        return True

    def _set_accessible_card_choices(
        self,
        rows: list[
            tuple[str, str, bool, str, ChoiceCardSpec | None]
        ],
        *,
        panel_rect: pygame.Rect,
        card_top: int,
        card_bottom: int,
        inner_w: int,
    ) -> None:
        """Reflow cards into preview tabs, a document, and a real CTA.

        Tabs deliberately have no route callback. They only select which
        authored card is being inspected, including locked cards. Selection is
        committed through the fixed action button (or an explicit confirm
        after keyboard/gamepad focus has been established).
        """

        def scaled(logical_px: int, *, minimum: int = 1) -> int:
            return max(minimum, int(round(logical_px * self._density)))

        self._layout_mode = "cards_accessible"
        help_size = self._font_size(13)
        self._help_surface = self.fonts.render(
            f"{self.controls_hint} · {self.scroll_hint}",
            help_size,
            self.theme.text_mute,
        )
        help_rect = self._help_surface.get_rect()
        help_rect.midbottom = (
            panel_rect.centerx,
            panel_rect.bottom - self._panel_pad_y,
        )
        self._help_pos = help_rect.topleft
        card_bottom = min(
            card_bottom,
            help_rect.top - scaled(10),
        )

        tab_gap = scaled(8)
        tab_font_size = self._font_size(17)
        tab_h = max(
            scaled(44),
            self.fonts.get(tab_font_size, bold=True).get_linesize()
            + scaled(12),
        )
        tab_w = max(
            scaled(44),
            (
                inner_w - tab_gap * (len(rows) - 1)
            ) // max(1, len(rows)),
        )
        x = panel_rect.x + self._panel_pad_x
        self.buttons = []
        self._card_regions = []
        for cid, label, enabled, _reason, _card in rows:
            rect = pygame.Rect(x, card_top, tab_w, tab_h)
            self.buttons.append(Button(
                rect,
                label,
                fonts=self.fonts,
                theme=self.theme,
                font_size=tab_font_size,
                on_click=None,
                enabled=enabled,
                style="primary" if enabled else "ghost",
                chrome=self.chrome,
                screen="choice",
                role="option_card_tab",
                motion=self.motion,
            ))
            x += tab_w + tab_gap

        preview_top = card_top + tab_h + scaled(10)
        action_font_size = self._font_size(16)
        action_font = self.fonts.get(action_font_size, bold=True)
        action_text_width = max(1, inner_w - scaled(28))
        action_lines = max(
            (
                len(self._complete_wrap_lines(
                    (
                        card.action
                        if card is not None and card.action
                        else label
                    ),
                    action_font,
                    action_text_width,
                ))
                for _cid, label, _enabled, _reason, card in rows
            ),
            default=1,
        )
        action_h = max(
            scaled(44),
            action_lines * action_font.get_linesize() + scaled(12),
        )
        action_gap = scaled(8)
        action_rect = pygame.Rect(
            panel_rect.x + self._panel_pad_x,
            max(preview_top, card_bottom - action_h),
            inner_w,
            action_h,
        )

        reason_size = self._font_size(13)
        reason_font = self.fonts.get(reason_size, bold=True)
        reason_lines = max(
            (
                len(self._complete_wrap_lines(
                    reason,
                    reason_font,
                    inner_w,
                ))
                for _cid, _label, _enabled, reason, _card in rows
                if reason
            ),
            default=0,
        )
        reason_h = reason_lines * reason_font.get_linesize()
        if reason_h:
            self._accessible_lock_reason_rect = pygame.Rect(
                action_rect.x,
                max(preview_top, action_rect.y - action_gap - reason_h),
                action_rect.width,
                reason_h,
            )
            preview_bottom = (
                self._accessible_lock_reason_rect.y - action_gap
            )
        else:
            self._accessible_lock_reason_rect = pygame.Rect(
                action_rect.x,
                action_rect.y,
                action_rect.width,
                0,
            )
            preview_bottom = action_rect.y - action_gap
        self._accessible_preview_rect = pygame.Rect(
            panel_rect.x + self._panel_pad_x,
            preview_top,
            inner_w,
            max(1, preview_bottom - preview_top),
        )
        self._accessible_preview_index = 0
        self._accessible_scroll_y = 0
        self._accessible_scroll_max = 0
        self._accessible_document_height = 0
        self._accessible_action_button = Button(
            action_rect,
            "",
            fonts=self.fonts,
            theme=self.theme,
            font_size=action_font_size,
            on_click=None,
            enabled=False,
            style="primary",
            chrome=self.chrome,
            screen="choice",
            role="option_card_action",
            motion=self.motion,
        )
        self._sync_accessible_action_button()
        self._button_h = tab_h
        self._row_gap = tab_gap
        self._card_compact = False

    def _sync_accessible_action_button(self) -> None:
        """Bind the fixed CTA to the currently previewed authored choice."""
        button = self._accessible_action_button
        if (
            button is None
            or not self._rows
            or not 0 <= self._accessible_preview_index < len(self._rows)
        ):
            return
        index = self._accessible_preview_index
        cid, label, enabled, reason, card = self._rows[index]
        action = card.action if card is not None and card.action else label
        button.label = action
        button.enabled = enabled
        button.style = "primary" if enabled else "ghost"
        button.tooltip = reason or None
        button.on_click = (
            (lambda cid=cid: self.on_choose(cid))
            if enabled
            else None
        )
        button.focused = enabled and self._focus == index

    def _set_accessible_preview(
        self,
        index: int,
        *,
        reset_scroll: bool = True,
    ) -> None:
        if not self._rows:
            return
        target = max(0, min(len(self._rows) - 1, int(index)))
        changed = target != self._accessible_preview_index
        self._accessible_preview_index = target
        if reset_scroll and changed:
            self._accessible_scroll_y = 0
        self._sync_accessible_action_button()

    def _set_card_choices(
        self,
        rows: list[
            tuple[str, str, bool, str, ChoiceCardSpec | None]
        ],
    ) -> None:
        """Lay out a bounded 2–4 option comparison grid."""
        self._density = max(
            1.0,
            float(getattr(self.fonts, "scale", 1.0)),
        )

        def scaled(logical_px: int, *, minimum: int = 1) -> int:
            return max(minimum, int(round(logical_px * self._density)))

        prompt_size = self._font_size(18)
        help_size = self._font_size(13)
        prompt_font = self.fonts.get(prompt_size, bold=True)
        help_font = self.fonts.get(help_size)
        self._prompt_surface = self.fonts.render(
            self.card_prompt,
            prompt_size,
            self.theme.accent_warm,
            bold=True,
        )
        self._help_surface = self.fonts.render(
            self.controls_hint,
            help_size,
            self.theme.text_mute,
        )
        self._prompt_line_h = max(
            prompt_font.get_linesize(),
            self._prompt_surface.get_height(),
        )
        self._button_line_h = self.fonts.get(
            self._font_size(14),
            bold=True,
        ).get_linesize()
        self._reason_line_h = self.fonts.get(
            self._font_size(12),
        ).get_linesize()

        safe_x = scaled(24)
        safe_y = scaled(18)
        panel_w = max(
            1,
            min(
                self.rect.width - safe_x * 2,
                scaled(1200),
            ),
        )
        panel_h = max(
            1,
            min(
                self.rect.height - safe_y * 2,
                scaled(650),
            ),
        )
        panel_rect = pygame.Rect(0, 0, panel_w, panel_h)
        panel_rect.center = self.rect.center
        self.panel = Panel(
            panel_rect,
            self.theme,
            fill=(*self.theme.bg_overlay[:3], 240),
            border=self.theme.border_strong,
            radius=self.theme.radius_l,
            border_width=2,
            chrome=self.chrome,
            screen="choice",
            role="route_cards",
            density=self._density,
        )

        self._panel_pad_x = scaled(24)
        self._panel_pad_y = scaled(18)
        self._title_gap = scaled(10)
        header_h = self._prompt_line_h + self._title_gap
        footer_h = max(
            self._help_surface.get_height(),
            help_font.get_linesize(),
        ) + scaled(10)
        inner_w = max(1, panel_rect.width - self._panel_pad_x * 2)
        card_gap = scaled(14)
        card_top = panel_rect.y + self._panel_pad_y + header_h
        card_bottom = panel_rect.bottom - self._panel_pad_y - footer_h
        card_h = max(1, card_bottom - card_top)
        card_w = max(
            1,
            (inner_w - card_gap * (len(rows) - 1)) // len(rows),
        )
        self._card_compact = (
            card_w < scaled(280)
            or card_h < scaled(430)
        )
        self._button_h = card_h
        self._row_gap = card_gap
        self._reason_gap = scaled(4)
        self._title_h = header_h

        self._prompt_rect = self._prompt_surface.get_rect()
        self._prompt_rect.midtop = (
            panel_rect.centerx,
            panel_rect.y + self._panel_pad_y,
        )
        self._prompt_pos = self._prompt_rect.topleft
        help_rect = self._help_surface.get_rect()
        help_rect.midbottom = (
            panel_rect.centerx,
            panel_rect.bottom - self._panel_pad_y,
        )
        self._help_pos = help_rect.topleft

        self.buttons = []
        self._card_regions = []
        self._reason_anchors = [None for _row in rows]
        x = panel_rect.x + self._panel_pad_x
        for cid, label, enabled, _reason, _card in rows:
            rect = pygame.Rect(x, card_top, card_w, card_h)
            button = Button(
                rect,
                label,
                fonts=self.fonts,
                theme=self.theme,
                font_size=self._font_size(14),
                on_click=(lambda cid=cid: self.on_choose(cid))
                if enabled else None,
                enabled=enabled,
                style="primary" if enabled else "ghost",
                chrome=self.chrome,
                screen="choice",
                role="option_card",
                motion=self.motion,
            )
            self.buttons.append(button)
            self._card_regions.append(self._make_card_regions(rect))
            x += card_w + card_gap

        # Localized copy can outgrow the comparison grid at the default text
        # scale just as enlarged text can. Whenever any authored field would be
        # clipped or ellipsized, retain the complete copy in the measured,
        # scrollable card document instead.
        if not self._card_grid_fits(rows):
            self._set_accessible_card_choices(
                rows,
                panel_rect=panel_rect,
                card_top=card_top,
                card_bottom=card_bottom,
                inner_w=inner_w,
            )

        enabled_indices = [
            index for index, button in enumerate(self.buttons)
            if button.enabled
        ]
        if len(enabled_indices) == 1:
            self._focus = enabled_indices[0]
            if self._layout_mode == "cards_accessible":
                self._set_accessible_preview(self._focus)

    def _make_card_regions(
        self,
        rect: pygame.Rect,
    ) -> dict[str, pygame.Rect]:
        """Partition one card into non-overlapping, testable content regions."""

        def scaled(logical_px: int, *, minimum: int = 1) -> int:
            return max(minimum, int(round(logical_px * self._density)))

        pad = scaled(10 if self._card_compact else 14)
        gap = scaled(6 if self._card_compact else 8)
        header_h = scaled(50 if self._card_compact else 62)
        action_h = scaled(36 if self._card_compact else 44)
        tags_h = 0 if self._card_compact else scaled(26)
        image_h = min(
            scaled(92 if self._card_compact else 168),
            max(
                scaled(72),
                int(rect.height * (0.27 if self._card_compact else 0.31)),
            ),
        )

        content = rect.inflate(-pad * 2, -pad * 2)
        header = pygame.Rect(
            content.x,
            content.y,
            content.width,
            min(header_h, content.height),
        )
        image_y = min(content.bottom, header.bottom + gap)
        image = pygame.Rect(
            content.x,
            image_y,
            content.width,
            max(1, min(image_h, content.bottom - image_y)),
        )
        action = pygame.Rect(
            content.x,
            max(content.y, content.bottom - action_h),
            content.width,
            min(action_h, content.height),
        )
        tags = pygame.Rect(
            content.x,
            max(image.bottom, action.y - gap - tags_h),
            content.width,
            max(0, tags_h),
        )
        details_top = min(action.y, image.bottom + gap)
        details_bottom = (
            max(details_top, tags.y - gap)
            if tags_h
            else max(details_top, action.y - gap)
        )
        details = pygame.Rect(
            content.x,
            details_top,
            content.width,
            max(0, details_bottom - details_top),
        )
        return {
            "header": header,
            "image": image,
            "details": details,
            "tags": tags,
            "action": action,
        }

    def update(self, dt: float, inp) -> None:
        enabled = [i for i, b in enumerate(self.buttons) if b.enabled]
        previous_focus = self._focus
        direction = inp.nav
        accessible_cards = self._layout_mode == "cards_accessible"
        navigable = (
            list(range(len(self.buttons)))
            if accessible_cards
            else enabled
        )
        if self._layout_mode in {"cards", "cards_accessible"}:
            if inp.pressed(InputAction.NAV_LEFT):
                direction = -1
            elif inp.pressed(InputAction.NAV_RIGHT):
                direction = 1
            elif accessible_cards and self._focus in navigable and inp.nav:
                # Once a card tab is focused, vertical input belongs to the
                # complete card document. Left/right keep their stable role as
                # option navigation and Enter remains the only confirmation.
                scroll_step = max(
                    44,
                    int(round(52 * self._density * self._text_scale)),
                )
                self._accessible_scroll_y = max(
                    0,
                    min(
                        self._accessible_scroll_max,
                        self._accessible_scroll_y
                        + (scroll_step if inp.nav > 0 else -scroll_step),
                    ),
                )
                direction = 0
        if direction and navigable:
            if self._focus not in navigable:
                # First navigation press establishes focus. Down chooses the
                # first row; up chooses the last. Accessible preview tabs also
                # include locked rows so their explanation is reachable.
                self._focus = (
                    navigable[0] if direction > 0 else navigable[-1]
                )
            else:
                step = 1 if direction > 0 else -1
                pos = (
                    navigable.index(self._focus) + step
                ) % len(navigable)
                self._focus = navigable[pos]

        if accessible_cards and self._focus != previous_focus and self._focus >= 0:
            self._set_accessible_preview(self._focus)

        # Pointer movement changes the active input modality. Keep the visible
        # hover and confirm target on the same row; moving onto a locked row or
        # outside the choices clears keyboard focus rather than leaving a
        # different, stale choice armed for Enter/gamepad confirm.
        pointer_moved = any(
            event.type == pygame.MOUSEMOTION for event in inp.events
        )
        pointer_pressed = bool(
            getattr(inp, "mouse_clicked", False)
            or getattr(inp, "touch_started", False)
        )
        if pointer_moved or pointer_pressed:
            hovered = next(
                (i for i, button in enumerate(self.buttons)
                 if button.rect.collidepoint(inp.mouse_pos)),
                None,
            )
            if accessible_cards:
                if hovered is not None:
                    self._focus = hovered
                    self._set_accessible_preview(hovered)
                elif pointer_moved:
                    self._focus = -1
                    self._sync_accessible_action_button()
            else:
                self._focus = hovered if hovered in enabled else -1

        touch_capture_this_frame = False
        if accessible_cards:
            if getattr(inp, "touch_started", False):
                self._accessible_touch_dragging = (
                    self._accessible_preview_rect.collidepoint(inp.mouse_pos)
                )
                self._accessible_touch_moved = False
            if self._accessible_touch_dragging:
                touch_capture_this_frame = True
                delta_y = int(
                    getattr(inp, "touch_delta", (0, 0))[1]
                )
                if delta_y:
                    self._accessible_touch_moved = True
                    self._accessible_scroll_y = max(
                        0,
                        min(
                            self._accessible_scroll_max,
                            self._accessible_scroll_y - delta_y,
                        ),
                    )
                if (
                    getattr(inp, "touch_released", False)
                    or not getattr(inp, "touch_active", False)
                ):
                    self._accessible_touch_dragging = False

        if (
            accessible_cards
            and int(getattr(inp, "mouse_wheel", 0) or 0)
            and self._accessible_preview_rect.collidepoint(inp.mouse_pos)
        ):
            scroll_step = max(
                44,
                int(round(52 * self._density * self._text_scale)),
            )
            self._accessible_scroll_y = max(
                0,
                min(
                    self._accessible_scroll_max,
                    self._accessible_scroll_y
                    - int(inp.mouse_wheel) * scroll_step,
                ),
            )

        if inp.confirm and self._focus in enabled:
            button = (
                self._accessible_action_button
                if accessible_cards
                and self._accessible_preview_index == self._focus
                else self.buttons[self._focus]
            )
            if button is not None and button.activate():
                return

        for i, b in enumerate(self.buttons):
            b.focused = (
                i == self._focus
                and (accessible_cards or b.enabled)
            )
            b.update(dt, inp)
        if accessible_cards and self._accessible_action_button is not None:
            self._accessible_action_button.focused = (
                self._accessible_action_button.enabled
                and self._focus == self._accessible_preview_index
            )
            # A touch captured by the document must stay owned by that
            # document through release, even when the finger ends over CTA.
            if not touch_capture_this_frame:
                self._accessible_action_button.update(dt, inp)

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        self._sync_prerendered_text()
        # darken background to focus the decision (softer now that the stale
        # textbox is hidden under choices — a CG/scene behind it still reads)
        painted_backdrop = (
            self.chrome is not None
            and self.chrome.paint(
                surface,
                surface.get_rect(),
                ChromeRequest(
                    part="backdrop",
                    screen="choice",
                    variant="overlay",
                    size=surface.get_size(),
                    density=self._density,
                ),
            )
        )
        if not painted_backdrop:
            size = surface.get_size()
            cache_key: tuple[object, ...] = (size, (0, 0, 0, 130))
            if (
                cache_key != self._fallback_backdrop_cache_key
                or self._fallback_backdrop_cache is None
            ):
                veil = pygame.Surface(size, pygame.SRCALPHA)
                veil.fill((0, 0, 0, 130))
                self._fallback_backdrop_cache_key = cache_key
                self._fallback_backdrop_cache = veil
            veil = self._fallback_backdrop_cache
            assert veil is not None
            surface.blit(veil, (0, 0))
        if self.panel:
            self.panel.draw(surface)
            if self._prompt_surface is not None:
                surface.blit(self._prompt_surface, self._prompt_pos)
            if (
                self._layout_mode in {"cards", "cards_accessible"}
                and self._help_surface is not None
            ):
                surface.blit(self._help_surface, self._help_pos)
        if self._layout_mode == "cards_accessible":
            self._draw_accessible_cards(surface)
            return
        if self._layout_mode == "cards":
            for i, (button, card, regions) in enumerate(zip(
                self.buttons,
                self._cards,
                self._card_regions,
                strict=True,
            )):
                if card is None:  # guarded by the all-card layout contract
                    continue
                button.focused = i == self._focus and button.enabled
                self._draw_choice_card(
                    surface,
                    index=i,
                    button=button,
                    card=card,
                    regions=regions,
                )
            return
        for i, b in enumerate(self.buttons):
            # Button sends the same focus state to pack chrome, then retains its
            # engine-owned high-contrast ring and label above that paint.
            b.focused = i == self._focus and b.enabled
            b.draw(surface)
        # Lock reasons: a small muted caption centred under each locked button,
        # so the player reads WHY a choice is unavailable (and how close they
        # are) instead of seeing a silent greyed row.
        for i, anchor in enumerate(self._reason_anchors):
            if anchor is None:
                continue
            reason = self._reasons[i] if i < len(self._reasons) else ""
            if not reason:
                continue
            # Larger + warmer than before: the old size-14 muted-violet caption
            # was nearly illegible. Amber (the same attention colour the prompt
            # uses) reads as a "why-not" hint with strong contrast on the dark
            # panel, and size 16 clears the readability bar.
            cap = self.fonts.render(
                reason,
                self._font_size(16),
                self.theme.accent_warm,
            )
            cx, cy = anchor
            surface.blit(cap, (cx - cap.get_width() // 2, cy))

    @staticmethod
    def _ellipsize(
        text: str,
        font: pygame.font.Font,
        max_width: int,
    ) -> str:
        value = " ".join(text.split())
        if not value or max_width <= 0:
            return ""
        if font.size(value)[0] <= max_width:
            return value
        ellipsis = "…"
        if font.size(ellipsis)[0] > max_width:
            return ""
        low, high = 0, len(value)
        while low < high:
            middle = (low + high + 1) // 2
            candidate = value[:middle].rstrip() + ellipsis
            if font.size(candidate)[0] <= max_width:
                low = middle
            else:
                high = middle - 1
        return value[:low].rstrip() + ellipsis

    @staticmethod
    def _complete_wrap_lines(
        text: str,
        font: pygame.font.Font,
        max_width: int,
    ) -> list[str]:
        """Wrap without losing an overlong no-whitespace token.

        The shared label wrapper normally handles CJK character-by-character.
        Keep that established result for text that already fits, and only
        apply the defensive character fallback when a returned line is still
        wider than the requested viewport (for example with an unusual font
        backend or an indivisible mixed-script token).
        """
        value = " ".join(text.split())
        if not value:
            return []
        if max_width <= 0:
            return [value]
        wrapped = _wrap_lines(value, font, max_width)
        if all(font.size(line)[0] <= max_width for line in wrapped):
            return wrapped

        complete: list[str] = []
        for line in wrapped:
            if font.size(line)[0] <= max_width:
                complete.append(line)
                continue
            fragment = ""
            for character in line:
                candidate = fragment + character
                if fragment and font.size(candidate)[0] > max_width:
                    complete.append(fragment)
                    fragment = character
                else:
                    fragment = candidate
            if fragment:
                complete.append(fragment)
        return complete

    def _draw_line(
        self,
        surface: pygame.Surface,
        text: str,
        *,
        rect: pygame.Rect,
        size: int,
        color: tuple,
        bold: bool = False,
        align: str = "left",
    ) -> pygame.Rect:
        font = self.fonts.get(size, bold=bold)
        value = self._ellipsize(text, font, rect.width)
        rendered = self.fonts.render(
            value,
            size,
            color,
            bold=bold,
        )
        if align == "right":
            x = rect.right - rendered.get_width()
        elif align == "center":
            x = rect.centerx - rendered.get_width() // 2
        else:
            x = rect.x
        y = rect.y + max(0, (rect.height - rendered.get_height()) // 2)
        surface.blit(rendered, (x, y))
        return rendered.get_rect(topleft=(x, y))

    def _draw_wrapped(
        self,
        surface: pygame.Surface,
        text: str,
        *,
        rect: pygame.Rect,
        size: int,
        color: tuple,
        max_lines: int,
        bold: bool = False,
    ) -> None:
        if rect.width <= 0 or rect.height <= 0 or max_lines <= 0:
            return
        font = self.fonts.get(size, bold=bold)
        lines = self._complete_wrap_lines(text, font, rect.width)
        if not lines:
            return
        clipped = lines[:max_lines]
        if len(lines) > max_lines:
            clipped[-1] = self._ellipsize(
                clipped[-1] + "…",
                font,
                rect.width,
            )
        line_h = font.get_linesize()
        y = rect.y
        for line in clipped:
            if y + line_h > rect.bottom:
                break
            rendered = self.fonts.render(
                line,
                size,
                color,
                bold=bold,
            )
            surface.blit(rendered, (rect.x, y))
            y += line_h

    def _draw_full_wrapped(
        self,
        surface: pygame.Surface,
        text: str,
        *,
        rect: pygame.Rect,
        size: int,
        color: tuple,
        bold: bool = False,
        align: str = "left",
    ) -> int:
        """Draw every wrapped line and return the consumed height."""
        if rect.width <= 0 or rect.height <= 0:
            return 0
        if not " ".join(text.split()):
            return 0
        font = self.fonts.get(size, bold=bold)
        lines = self._complete_wrap_lines(text, font, rect.width)
        line_h = font.get_linesize()
        y = rect.y
        for line in lines:
            rendered = self.fonts.render(
                line,
                size,
                color,
                bold=bold,
            )
            if align == "center":
                x = rect.centerx - rendered.get_width() // 2
            elif align == "right":
                x = rect.right - rendered.get_width()
            else:
                x = rect.x
            surface.blit(rendered, (x, y))
            y += line_h
        return len(lines) * line_h

    def _draw_accessible_cards(self, surface: pygame.Surface) -> None:
        if not self._rows:
            return
        for index, (button, card) in enumerate(zip(
            self.buttons,
            self._cards,
            strict=True,
        )):
            if card is None:
                continue
            button.focused = index == self._focus
            self._draw_accessible_tab(
                surface,
                index=index,
                button=button,
                card=card,
            )

        self._accessible_preview_index = max(
            0,
            min(len(self._rows) - 1, self._accessible_preview_index),
        )
        card = self._cards[self._accessible_preview_index]
        if card is None:
            return
        self._draw_accessible_card_page(
            surface,
            index=self._accessible_preview_index,
            button=self.buttons[self._accessible_preview_index],
            card=card,
        )
        row = self._rows[self._accessible_preview_index]
        reason = row[3]
        if reason and self._accessible_lock_reason_rect.height > 0:
            self._draw_full_wrapped(
                surface,
                reason,
                rect=self._accessible_lock_reason_rect,
                size=self._font_size(13),
                color=self.theme.warn,
                bold=True,
                align="center",
            )
        if self._accessible_action_button is not None:
            self._accessible_action_button.focused = (
                self._accessible_action_button.enabled
                and self._focus == self._accessible_preview_index
            )
            self._draw_accessible_action_button(
                surface,
                self._accessible_action_button,
            )

    def _draw_accessible_action_button(
        self,
        surface: pygame.Surface,
        button: Button,
    ) -> None:
        """Draw the real CTA with complete, wrapped authored action text."""
        label = button.label
        # Button remains the hit-tested, focusable control. Suppress only its
        # one-line label pass, then draw the same authored label losslessly in
        # the measured multi-line rectangle.
        button.label = ""
        try:
            button.draw(surface)
        finally:
            button.label = label
        pad = max(8, int(round(14 * self._density)))
        self._draw_full_wrapped(
            surface,
            label,
            rect=button.rect.inflate(-pad * 2, -max(4, pad // 2)),
            size=button.font_size,
            color=(
                self.theme.text
                if button.enabled
                else self.theme.text_dim
            ),
            bold=True,
            align="center",
        )

    def _draw_accessible_tab(
        self,
        surface: pygame.Surface,
        *,
        index: int,
        button: Button,
        card: ChoiceCardSpec,
    ) -> None:
        rect = button.rect
        selected = index == self._accessible_preview_index
        high_contrast = (
            getattr(self.theme, "visual_profile", "standard")
            == "high_contrast"
        )
        emphasis = (
            max(0, min(3, int(round(button._hover_t * 3))))
            if button.enabled else 0
        )
        painted = (
            self.chrome is not None
            and self.chrome.paint(
                surface,
                rect,
                ChromeRequest(
                    part="button",
                    screen="choice",
                    variant="option_card_tab:primary",
                    size=rect.size,
                    density=self._density,
                    enabled=button.enabled,
                    hovered=button._hover,
                    focused=button.focused,
                    selected=selected,
                    emphasis=emphasis,
                ),
            )
        )
        accent = card.accent or self.theme.accent_alt[:3]
        semantic_border = (
            self.theme.border_strong
            if high_contrast
            else accent
        )
        if not painted:
            layer = pygame.Surface(rect.size, pygame.SRCALPHA)
            fill_alpha = 236 if selected else 205
            pygame.draw.rect(
                layer,
                (*self.theme.bg_panel[:3], fill_alpha),
                layer.get_rect(),
                border_radius=self.theme.radius_m,
            )
            pygame.draw.rect(
                layer,
                semantic_border if selected else self.theme.border_soft,
                layer.get_rect(),
                width=2 if selected else 1,
                border_radius=self.theme.radius_m,
            )
            surface.blit(layer, rect.topleft)
        if not button.enabled:
            veil = pygame.Surface(rect.size, pygame.SRCALPHA)
            veil.fill((*self.theme.bg_overlay[:3], 148))
            surface.blit(veil, rect.topleft)
        if high_contrast and selected:
            pygame.draw.rect(
                surface,
                self.theme.border_strong,
                rect,
                width=2,
                border_radius=self.theme.radius_m,
            )

        label = f"{index + 1:02d}　{card.title}"
        self._draw_line(
            surface,
            label,
            rect=rect.inflate(-max(8, int(round(12 * self._density))), 0),
            size=self._font_size(17),
            color=self.theme.text if button.enabled else self.theme.text_dim,
            bold=True,
            align="center",
        )
        if button.focused:
            pygame.draw.rect(
                surface,
                self.theme.accent_warm,
                rect,
                width=max(3, int(round(3 * self._density))),
                border_radius=self.theme.radius_m,
            )

    def _accessible_wrapped_height(
        self,
        text: str,
        *,
        size: int,
        width: int,
        bold: bool = False,
    ) -> int:
        if not " ".join(text.split()) or width <= 0:
            return 0
        font = self.fonts.get(size, bold=bold)
        return len(self._complete_wrap_lines(text, font, width)) * (
            font.get_linesize()
        )

    def _accessible_image_frame_size(
        self,
        card: ChoiceCardSpec,
        *,
        content_width: int,
    ) -> tuple[int, int]:
        """Return an aspect-aware comparison-page image frame.

        The complete-copy layout is usually much wider than a character
        portrait.  Stretching its media slot across that whole document used
        to turn a vertical heroine illustration into a roughly 10:1 strip of
        eyes.  Keep portrait sources in a portrait identity frame, while
        landscape and square artwork retain frames suited to their source
        orientation.  Authored fit/focus/zoom still decide how the source is
        rendered *inside* this bounded frame.
        """

        if not card.image or self.assets is None or content_width <= 0:
            return (0, 0)

        def scaled(logical_px: int, *, minimum: int = 1) -> int:
            return max(minimum, int(round(logical_px * self._density)))

        source = self.assets.image(
            card.image,
            fallback_color=(card.accent or self.theme.accent_alt[:3]),
        )
        source_w, source_h = source.get_size()
        source_ratio = source_w / max(1, source_h)
        if source_ratio < 0.9:
            # A 3:4 frame keeps a face, silhouette, and costume cue visible.
            frame_h = scaled(280)
            frame_w = int(round(frame_h * 0.75))
        elif source_ratio > 1.2:
            # Landscape CGs remain cinematic without becoming a letterbox.
            frame_h = scaled(210)
            frame_w = int(round(frame_h * (16.0 / 9.0)))
        else:
            frame_h = scaled(250)
            frame_w = frame_h

        if frame_w > content_width:
            shrink = content_width / max(1, frame_w)
            frame_w = content_width
            frame_h = max(1, int(round(frame_h * shrink)))
        return (max(1, frame_w), max(1, frame_h))

    def _build_accessible_card_document(
        self,
        *,
        index: int,
        card: ChoiceCardSpec,
        width: int,
    ) -> pygame.Surface:
        """Build one lossless, vertically scrollable card document."""

        def scaled(logical_px: int, *, minimum: int = 1) -> int:
            return max(minimum, int(round(logical_px * self._density)))

        width = max(1, width)
        pad = scaled(16)
        gap = scaled(8)
        small_gap = scaled(4)
        content_w = max(1, width - pad * 2)
        meta_size = self._font_size(11)
        title_size = self._font_size(24)
        subtitle_size = self._font_size(14)
        label_size = self._font_size(13)
        body_size = self._font_size(15)
        tag_size = self._font_size(12)

        image_w, image_h = self._accessible_image_frame_size(
            card,
            content_width=content_w,
        )
        column_gap = scaled(20) if image_h else 0
        split_layout = bool(
            image_h
            and content_w >= scaled(640)
            and content_w - image_w - column_gap >= scaled(320)
        )
        text_w = (
            max(1, content_w - image_w - column_gap)
            if split_layout
            else content_w
        )

        meta_text = f"{index + 1:02d}"
        sections: list[tuple[str, str, int, bool, tuple, int]] = []

        def add_text(
            kind: str,
            text: str,
            size: int,
            *,
            bold: bool = False,
            color: tuple | None = None,
            extra: int = 0,
        ) -> None:
            if not " ".join(text.split()):
                return
            height = self._accessible_wrapped_height(
                text,
                size=size,
                width=text_w,
                bold=bold,
            )
            sections.append((
                kind,
                text,
                size,
                bold,
                color or self.theme.text,
                height + extra,
            ))

        add_text("meta", meta_text, meta_size, bold=True)
        if card.badge:
            add_text(
                "badge",
                card.badge,
                meta_size,
                bold=True,
                color=self.theme.accent_warm,
            )
        add_text("title", card.title, title_size, bold=True)
        if card.subtitle:
            add_text(
                "subtitle",
                card.subtitle,
                subtitle_size,
                color=self.theme.text_mute,
            )

        detail_sections: list[tuple[str, str, int, bool, tuple, int]] = []
        for detail in card.details:
            label_h = self._accessible_wrapped_height(
                detail.label,
                size=label_size,
                width=text_w,
                bold=True,
            )
            detail_sections.append((
                "detail_label",
                detail.label,
                label_size,
                True,
                self.theme.accent_alt,
                label_h,
            ))
            body_h = self._accessible_wrapped_height(
                detail.text,
                size=body_size,
                width=text_w,
            )
            detail_sections.append((
                "detail_body",
                detail.text,
                body_size,
                False,
                self.theme.text,
                body_h,
            ))

        tag_columns = max(1, min(3, len(card.tags)))
        tag_gap = small_gap
        tag_w = max(
            1,
            (
                content_w - tag_gap * max(0, tag_columns - 1)
            ) // tag_columns,
        )
        tag_sections: list[tuple[str, str, int, bool, tuple, int]] = []
        for tag in card.tags:
            text_h = self._accessible_wrapped_height(
                tag,
                size=tag_size,
                width=max(1, tag_w - scaled(16)),
            )
            tag_sections.append((
                "tag",
                tag,
                tag_size,
                False,
                self.theme.text_mute,
                max(scaled(32), text_h + scaled(10)),
            ))

        text_height = 0
        for _kind, _text, _size, _bold, _color, section_h in sections:
            text_height += section_h + small_gap
        for detail_index in range(0, len(detail_sections), 2):
            text_height += detail_sections[detail_index][5]
            text_height += small_gap
            text_height += detail_sections[detail_index + 1][5]
            text_height += gap

        if split_layout:
            profile_height = max(text_height, image_h)
        else:
            profile_height = text_height
            if image_h:
                profile_height += image_h + gap

        tag_height = 0
        for start in range(0, len(tag_sections), tag_columns):
            row = tag_sections[start:start + tag_columns]
            tag_height += max(tag[5] for tag in row) + small_gap
        height = pad + profile_height
        if tag_sections:
            height += gap + tag_height
        height += pad

        document = pygame.Surface((width, max(1, height)), pygame.SRCALPHA)
        accent = card.accent or self.theme.accent_alt[:3]
        high_contrast = (
            getattr(self.theme, "visual_profile", "standard")
            == "high_contrast"
        )
        semantic_accent = (
            self.theme.accent_alt if high_contrast else accent
        )
        text_x = pad
        y = pad
        for kind, text, size, bold, color, section_h in sections:
            text_h = section_h
            self._draw_full_wrapped(
                document,
                text,
                rect=pygame.Rect(text_x, y, text_w, text_h),
                size=size,
                color=(semantic_accent if kind == "meta" else color),
                bold=bold,
            )
            y += section_h + small_gap

        if split_layout:
            image_rect = pygame.Rect(
                pad + text_w + column_gap,
                pad,
                image_w,
                image_h,
            )
        elif image_h:
            image_rect = pygame.Rect(
                pad + max(0, (content_w - image_w) // 2),
                y,
                image_w,
                image_h,
            )
        else:
            image_rect = pygame.Rect(0, 0, 0, 0)

        if image_h:
            pygame.draw.rect(
                document,
                (*accent, 24),
                image_rect,
                border_radius=max(2, scaled(4)),
            )
            image = self.assets.scaled(
                card.image,
                image_rect.size,
                fit=card.image_fit,
                fallback_color=accent,
                focus=(0.5, card.image_focus_y),
                zoom=card.image_zoom,
            )
            document.blit(image, image_rect.topleft)
            pygame.draw.rect(
                document,
                (*semantic_accent, 180),
                image_rect,
                width=max(1, int(round(self._density))),
                border_radius=max(2, scaled(4)),
            )
            if not split_layout:
                y += image_h + gap

        for detail_index in range(0, len(detail_sections), 2):
            label = detail_sections[detail_index]
            body = detail_sections[detail_index + 1]
            self._draw_full_wrapped(
                document,
                label[1],
                rect=pygame.Rect(text_x, y, text_w, label[5]),
                size=label[2],
                color=label[4],
                bold=True,
            )
            y += label[5] + small_gap
            self._draw_full_wrapped(
                document,
                body[1],
                rect=pygame.Rect(text_x, y, text_w, body[5]),
                size=body[2],
                color=body[4],
            )
            y += body[5] + gap
            pygame.draw.line(
                document,
                self.theme.border_soft,
                (pad, y - max(1, gap // 2)),
                (pad + text_w, y - max(1, gap // 2)),
                width=1,
            )

        if split_layout:
            y = pad + max(text_height, image_h)
        if tag_sections:
            y += gap
        for start in range(0, len(tag_sections), tag_columns):
            row = tag_sections[start:start + tag_columns]
            row_h = max(tag[5] for tag in row)
            x = pad
            for tag in row:
                pill = pygame.Rect(x, y, tag_w, row_h)
                pygame.draw.rect(
                    document,
                    (*semantic_accent, 30),
                    pill,
                    border_radius=max(2, pill.height // 2),
                )
                pygame.draw.rect(
                    document,
                    (*semantic_accent, 120),
                    pill,
                    width=1,
                    border_radius=max(2, pill.height // 2),
                )
                text_rect = pill.inflate(-scaled(8), -scaled(5))
                self._draw_full_wrapped(
                    document,
                    tag[1],
                    rect=text_rect,
                    size=tag[2],
                    color=tag[4],
                    align="center",
                )
                x += tag_w + tag_gap
            y += row_h + small_gap
        return document

    def _cached_accessible_card_document(
        self,
        *,
        index: int,
        card: ChoiceCardSpec,
        width: int,
    ) -> pygame.Surface:
        """Reuse one immutable comparison document until an input changes."""
        card_identity = (
            card.title,
            card.subtitle,
            card.badge,
            card.image,
            card.image_fit,
            card.image_focus_y,
            card.image_zoom,
            card.accent,
            tuple((detail.label, detail.text) for detail in card.details),
            tuple(card.tags),
            card.action,
        )
        theme_identity = (
            getattr(self.theme, "visual_profile", "standard"),
            self.theme.text,
            self.theme.text_mute,
            self.theme.accent_alt,
            self.theme.accent_warm,
            self.theme.border_soft,
        )
        asset_identity = (
            id(self.assets),
            getattr(self.assets, "image_cache_revision", None),
        )
        key: tuple[object, ...] = (
            index,
            card_identity,
            max(1, int(width)),
            self._density,
            self._text_scale,
            id(self.fonts),
            getattr(self.fonts, "revision", None),
            theme_identity,
            asset_identity,
        )
        if (
            key == self._accessible_document_cache_key
            and self._accessible_document_cache is not None
        ):
            return self._accessible_document_cache

        document = self._build_accessible_card_document(
            index=index,
            card=card,
            width=width,
        )
        self._accessible_document_cache_key = key
        self._accessible_document_cache = document
        return document

    def _cached_accessible_page_background(
        self,
        *,
        size: tuple[int, int],
        accent: tuple[int, int, int],
        high_contrast: bool,
    ) -> pygame.Surface:
        """Return the exact stock page chrome for the current render inputs."""
        border = self.theme.border_strong if high_contrast else accent
        key: tuple[object, ...] = (
            size,
            self._density,
            self._text_scale,
            getattr(self.theme, "visual_profile", "standard"),
            self.theme.bg_panel,
            border,
            self.theme.radius_m,
        )
        if (
            key != self._accessible_page_cache_key
            or self._accessible_page_cache is None
        ):
            layer = pygame.Surface(size, pygame.SRCALPHA)
            pygame.draw.rect(
                layer,
                (*self.theme.bg_panel[:3], 242),
                layer.get_rect(),
                border_radius=self.theme.radius_m,
            )
            pygame.draw.rect(
                layer,
                border,
                layer.get_rect(),
                width=2,
                border_radius=self.theme.radius_m,
            )
            self._accessible_page_cache_key = key
            self._accessible_page_cache = layer
        layer = self._accessible_page_cache
        assert layer is not None
        return layer

    def _draw_accessible_card_page(
        self,
        surface: pygame.Surface,
        *,
        index: int,
        button: Button,
        card: ChoiceCardSpec,
    ) -> None:
        rect = self._accessible_preview_rect
        if rect.width <= 0 or rect.height <= 0:
            return
        painted = (
            self.chrome is not None
            and self.chrome.paint(
                surface,
                rect,
                ChromeRequest(
                    part="card",
                    screen="choice",
                    variant="option_card_page",
                    size=rect.size,
                    density=self._density,
                    enabled=button.enabled,
                    focused=button.focused,
                    selected=True,
                    emphasis=0,
                ),
            )
        )
        accent = card.accent or self.theme.accent_alt[:3]
        high_contrast = (
            getattr(self.theme, "visual_profile", "standard")
            == "high_contrast"
        )
        if not painted:
            layer = self._cached_accessible_page_background(
                size=rect.size,
                accent=accent,
                high_contrast=high_contrast,
            )
            surface.blit(layer, rect.topleft)

        inset = max(3, int(round(4 * self._density)))
        viewport = rect.inflate(-inset * 2, -inset * 2)
        scrollbar_w = max(8, int(round(10 * self._density)))
        document_w = max(1, viewport.width - scrollbar_w)
        document = self._cached_accessible_card_document(
            index=index,
            card=card,
            width=document_w,
        )
        self._accessible_document_height = document.get_height()
        self._accessible_scroll_max = max(
            0,
            document.get_height() - viewport.height,
        )
        self._accessible_scroll_y = max(
            0,
            min(self._accessible_scroll_max, self._accessible_scroll_y),
        )
        source = pygame.Rect(
            0,
            self._accessible_scroll_y,
            document_w,
            min(viewport.height, document.get_height()),
        )
        surface.blit(document, viewport.topleft, source)

        if self._accessible_scroll_max > 0:
            track = pygame.Rect(
                viewport.right - scrollbar_w + max(2, scrollbar_w // 3),
                viewport.y,
                max(2, scrollbar_w // 3),
                viewport.height,
            )
            pygame.draw.rect(
                surface,
                self.theme.border_soft,
                track,
                border_radius=max(1, track.width // 2),
            )
            thumb_h = max(
                24,
                int(round(
                    track.height * viewport.height / document.get_height(),
                )),
            )
            travel = max(0, track.height - thumb_h)
            ratio = self._accessible_scroll_y / self._accessible_scroll_max
            thumb = pygame.Rect(
                track.x,
                track.y + int(round(travel * ratio)),
                track.width,
                min(track.height, thumb_h),
            )
            pygame.draw.rect(
                surface,
                self.theme.accent_warm,
                thumb,
                border_radius=max(1, thumb.width // 2),
            )

    def _draw_choice_card(
        self,
        surface: pygame.Surface,
        *,
        index: int,
        button: Button,
        card: ChoiceCardSpec,
        regions: dict[str, pygame.Rect],
    ) -> None:
        rect = button.rect
        emphasis = (
            max(0, min(3, int(round(button._hover_t * 3))))
            if button.enabled else 0
        )
        painted = (
            self.chrome is not None
            and self.chrome.paint(
                surface,
                rect,
                ChromeRequest(
                    part="card",
                    screen="choice",
                    variant="option_card",
                    size=rect.size,
                    density=self._density,
                    enabled=button.enabled,
                    hovered=button._hover,
                    focused=button.focused,
                    emphasis=emphasis,
                ),
            )
        )
        if not painted:
            layer = pygame.Surface(rect.size, pygame.SRCALPHA)
            fill = (
                (*self.theme.bg_panel[:3], 242)
                if button.enabled
                else (*self.theme.bg_panel[:3], 185)
            )
            pygame.draw.rect(
                layer,
                fill,
                layer.get_rect(),
                border_radius=self.theme.radius_m,
            )
            pygame.draw.rect(
                layer,
                self.theme.border if button.enabled else self.theme.border_soft,
                layer.get_rect(),
                width=2 if button.focused else 1,
                border_radius=self.theme.radius_m,
            )
            surface.blit(layer, rect.topleft)

        if self.chrome is not None and button.enabled and button.focused:
            self.chrome.paint(
                surface,
                rect,
                ChromeRequest(
                    part="focus",
                    screen="choice",
                    variant="option_card",
                    size=rect.size,
                    density=self._density,
                    enabled=True,
                    hovered=button._hover,
                    focused=True,
                    emphasis=emphasis,
                ),
            )

        accent = card.accent or self.theme.accent_alt[:3]
        high_contrast = (
            getattr(self.theme, "visual_profile", "standard")
            == "high_contrast"
        )
        # Character identity may keep its authored rail/image accent, while
        # text uses a contrast-certified semantic colour in accessibility mode.
        # Titles, route numbers, and labels also make identity independent of
        # hue for colour-vision users.
        text_accent = self.theme.accent_alt if high_contrast else accent
        rail_w = max(3, int(round(4 * self._density)))
        pygame.draw.rect(
            surface,
            accent,
            (
                rect.x,
                rect.y + max(8, int(round(10 * self._density))),
                rail_w,
                max(1, rect.height - max(16, int(round(20 * self._density)))),
            ),
            border_radius=max(1, rail_w // 2),
        )

        header = regions["header"]
        compact = self._card_compact
        meta_h = max(12, int(round((14 if compact else 16) * self._density)))
        meta = pygame.Rect(header.x, header.y, header.width, meta_h)
        self._draw_line(
            surface,
            f"{index + 1:02d}",
            rect=pygame.Rect(meta.x, meta.y, meta.width // 3, meta.height),
            size=self._font_size(10 if compact else 11),
            color=text_accent,
            bold=True,
        )
        if card.badge:
            self._draw_line(
                surface,
                card.badge,
                rect=meta,
                size=self._font_size(10 if compact else 11),
                color=self.theme.accent_warm,
                bold=True,
                align="right",
            )
        title_y = meta.bottom
        title_h = max(
            18,
            int(round((24 if compact else 30) * self._density)),
        )
        title_rect = pygame.Rect(
            header.x,
            title_y,
            header.width,
            min(title_h, max(1, header.bottom - title_y)),
        )
        self._draw_line(
            surface,
            card.title,
            rect=title_rect,
            size=self._font_size(19 if compact else 24),
            color=self.theme.text,
            bold=True,
        )
        subtitle_rect = pygame.Rect(
            header.x,
            title_rect.bottom,
            header.width,
            max(0, header.bottom - title_rect.bottom),
        )
        if card.subtitle:
            self._draw_line(
                surface,
                card.subtitle,
                rect=subtitle_rect,
                size=self._font_size(11 if compact else 14),
                color=self.theme.text_mute,
            )

        image_rect = regions["image"]
        image_bg = pygame.Surface(image_rect.size, pygame.SRCALPHA)
        image_bg.fill((*accent, 24))
        surface.blit(image_bg, image_rect.topleft)
        if card.image and self.assets is not None:
            image = self.assets.scaled(
                card.image,
                image_rect.size,
                fit=card.image_fit,
                fallback_color=accent,
                focus=(0.5, card.image_focus_y),
                zoom=card.image_zoom,
            )
            surface.blit(image, image_rect.topleft)
        else:
            pygame.draw.line(
                surface,
                (*accent, 95),
                image_rect.topleft,
                image_rect.bottomright,
                width=max(1, int(round(self._density))),
            )
        pygame.draw.rect(
            surface,
            (*accent, 150),
            image_rect,
            width=max(1, int(round(self._density))),
        )

        details_rect = regions["details"]
        if card.details and details_rect.height > 0:
            row_h = max(1, details_rect.height // len(card.details))
            for detail_index, detail in enumerate(card.details):
                row = pygame.Rect(
                    details_rect.x,
                    details_rect.y + detail_index * row_h,
                    details_rect.width,
                    (
                        details_rect.bottom
                        - (details_rect.y + detail_index * row_h)
                        if detail_index == len(card.details) - 1
                        else row_h
                    ),
                )
                if detail_index:
                    pygame.draw.line(
                        surface,
                        self.theme.border_soft,
                        (row.x, row.y),
                        (row.right, row.y),
                        width=1,
                    )
                label_h = min(
                    row.height,
                    max(
                        12,
                        self.fonts.get(
                            self._font_size(10 if compact else 13),
                            bold=True,
                        ).get_linesize(),
                    ),
                )
                self._draw_line(
                    surface,
                    detail.label,
                    rect=pygame.Rect(row.x, row.y, row.width, label_h),
                    size=self._font_size(10 if compact else 13),
                    color=text_accent,
                    bold=True,
                )
                body = pygame.Rect(
                    row.x,
                    row.y + label_h,
                    row.width,
                    max(0, row.height - label_h),
                )
                body_size = self._font_size(11 if compact else 15)
                line_h = self.fonts.get(body_size).get_linesize()
                max_lines = max(1, min(
                    1 if compact else 2,
                    body.height // max(1, line_h),
                ))
                self._draw_wrapped(
                    surface,
                    detail.text,
                    rect=body,
                    size=body_size,
                    color=self.theme.text,
                    max_lines=max_lines,
                )

        tags_rect = regions["tags"]
        if card.tags and tags_rect.height > 0:
            tag_gap = max(3, int(round(5 * self._density)))
            tag_w = max(
                1,
                (
                    tags_rect.width
                    - tag_gap * (len(card.tags) - 1)
                ) // len(card.tags),
            )
            x = tags_rect.x
            for tag in card.tags:
                pill = pygame.Rect(x, tags_rect.y, tag_w, tags_rect.height)
                pill_layer = pygame.Surface(pill.size, pygame.SRCALPHA)
                pygame.draw.rect(
                    pill_layer,
                    (*accent, 26),
                    pill_layer.get_rect(),
                    border_radius=max(2, pill.height // 2),
                )
                pygame.draw.rect(
                    pill_layer,
                    (*accent, 105),
                    pill_layer.get_rect(),
                    width=1,
                    border_radius=max(2, pill.height // 2),
                )
                surface.blit(pill_layer, pill.topleft)
                self._draw_line(
                    surface,
                    tag,
                    rect=pill.inflate(-6, 0),
                    size=self._font_size(11 if compact else 12),
                    color=self.theme.text_mute,
                    align="center",
                )
                x += tag_w + tag_gap

        action = regions["action"]
        action_layer = pygame.Surface(action.size, pygame.SRCALPHA)
        pygame.draw.rect(
            action_layer,
            (*accent, 48 if button.enabled else 18),
            action_layer.get_rect(),
            border_radius=max(3, self.theme.radius_m - 2),
        )
        pygame.draw.rect(
            action_layer,
            (*accent, 145 if button.enabled else 62),
            action_layer.get_rect(),
            width=1,
            border_radius=max(3, self.theme.radius_m - 2),
        )
        surface.blit(action_layer, action.topleft)
        action_text = (
            card.action or button.label
            if button.enabled
            else (
                self._reasons[index]
                if index < len(self._reasons) and self._reasons[index]
                else self.locked_label
            )
        )
        self._draw_line(
            surface,
            action_text,
            rect=action.inflate(-8, 0),
            size=self._font_size(11 if compact else 14),
            color=(
                self.theme.text
                if button.enabled
                else self.theme.accent_warm
            ),
            bold=True,
            align="center",
        )

        if not button.enabled:
            disabled = pygame.Surface(rect.size, pygame.SRCALPHA)
            disabled.fill((*self.theme.bg_overlay[:3], 118))
            surface.blit(disabled, rect.topleft)
            # Repeat the reason over the veil so unavailable cards remain
            # understandable instead of becoming silent grey rectangles.
            reason = (
                self._reasons[index]
                if index < len(self._reasons)
                else ""
            )
            self._draw_line(
                surface,
                reason or self.locked_label,
                rect=action.inflate(-8, 0),
                size=self._font_size(11 if compact else 14),
                color=self.theme.accent_warm,
                bold=True,
                align="center",
            )

        if button.enabled and button.focused:
            pygame.draw.rect(
                surface,
                self.theme.accent_warm,
                rect,
                width=max(3, int(round(3 * self._density))),
                border_radius=self.theme.radius_m,
            )
