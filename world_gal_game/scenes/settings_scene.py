"""Settings overlay: text speed, volumes, playback, fullscreen toggle.

The body scrolls by wheel, touch drag, or semantic navigation, so the panel
never overflows regardless of window size or how many characters the
per-character voice section lists.
User-tunable settings persist to ``settings.json`` under
``config.writable_root(app_data_name)``: any control change calls
``ctx.config.save_to_disk()`` so the choice survives the next launch
(loaded at boot via ``config.load_from_disk()``).
"""
from __future__ import annotations

import math
from typing import Callable

import pygame

from .base import Scene, SceneContext
from ..ui.input_bindings import BindingProfile, InputAction
from ..ui.system_chrome import ChromeRequest
from ..ui.widgets import Button, Panel
from ..ui.widgets.label import _wrap_lines
from ..ui.widgets.slider import Slider


class SettingsScene(Scene):
    """Categorised, fully semantic settings surface.

    Accessibility opens first on purpose: the three controls that can make the
    rest of the interface usable are reachable in one, two, and three semantic
    operations respectively.  The visual tab order still follows the familiar
    Reading / Audio / Accessibility / Controls & Display information
    architecture.
    """

    CATEGORY_ORDER = (
        "reading",
        "audio",
        "accessibility",
        "controls_display",
    )
    DEFAULT_CATEGORY = "accessibility"

    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        self.on_close: Callable[[], None] | None = None
        self._scroll_y = 0
        # Runtime widgets still use an index, but persistence across rebuilds
        # and tab round-trips is keyed by the stable ids in ``_button_ids``.
        self._focus: int = -1
        self.on_controls: Callable[[], None] | None = None
        self.on_fullscreen_change: Callable[[bool], None] | None = None
        self._touch_dragging = False
        self._touch_drag_moved = False
        self._touch_drag_distance = 0
        self._touch_drag_pending_y = 0
        self._category = self.DEFAULT_CATEGORY
        self._category_focus_ids: dict[str, str] = {}
        self._category_scroll: dict[str, int] = {}
        self._button_ids: list[str] = []
        self._tab_buttons: list[tuple[str, Button]] = []

    # ---- layout helpers ------------------------------------------------------

    def _sp(self, value: int) -> int:
        return max(1, int(round(value * self._density)))

    def _chrome_is_active(self) -> bool:
        chrome = self.ctx.ui_chrome
        return bool(
            chrome is not None and getattr(chrome, "active", False)
        )

    def _binding_profile(self) -> BindingProfile:
        return BindingProfile(
            getattr(self.ctx.config, "input_bindings", {}) or {},
        )

    def _mk(self, w: int, h: int, label: str, on_click, *,
            style: str = "ghost", font_size: int = 16,
            role: str = "action", selected: bool = False,
            wrap_text: bool = False) -> Button:
        line_h = self.ctx.fonts.get(font_size, bold=True).get_linesize()
        width = max(self._minimum_target, w)
        height = max(self._minimum_target, h, line_h + self._sp(14))
        return Button(
            pygame.Rect(0, 0, width, height),
            label,
            fonts=self.ctx.fonts,
            theme=self.ctx.theme,
            font_size=font_size,
            style=style,
            on_click=on_click,
            chrome=self.ctx.ui_chrome,
            screen="settings",
            role=role,
            selected=selected,
            text_align=(
                "left"
                if self._skin_active and role in {"action", "toggle"}
                else "center"
            ),
            wrap_text=wrap_text,
            motion=self.ctx.motion,
        )

    def _button_text_padding(self) -> int:
        return max(
            self.ctx.theme.pad_m,
            int(round(
                self.ctx.theme.pad_m
                * float(getattr(self.ctx.fonts, "scale", 1.0))
            )),
        )

    def _button_required_width(self, label: str, font_size: int) -> int:
        font = self.ctx.fonts.get(font_size, bold=True)
        return font.size(label)[0] + self._button_text_padding() * 2

    def _button_required_height(
        self,
        label: str,
        width: int,
        font_size: int,
    ) -> int:
        font = self.ctx.fonts.get(font_size, bold=True)
        inner_width = max(1, width - self._button_text_padding() * 2)
        lines = _wrap_lines(label, font, inner_width) or [""]
        return max(
            self._minimum_target,
            len(lines) * font.get_linesize() + self._sp(14),
        )

    @staticmethod
    def _ellipsize(text: str, font, max_width: int) -> str:
        if font.size(text)[0] <= max_width:
            return text
        suffix = "…"
        candidate = text
        while candidate and font.size(candidate.rstrip() + suffix)[0] > max_width:
            candidate = candidate[:-1]
        return candidate.rstrip() + suffix if candidate else suffix

    def _add_volume_slider(
        self,
        cy: int,
        on_change,
        getter,
        *,
        height: int | None = None,
    ) -> None:
        slider = Slider(
            pygame.Rect(
                0,
                0,
                self._slider_width,
                height if height is not None else self._volume_row_h,
            ),
            getter(),
            fonts=self.ctx.fonts,
            theme=self.ctx.theme,
            on_change=on_change,
        )
        self._sliders.append((slider, self._slider_x, cy, getter))

    def enter(
        self,
        *,
        on_close=None,
        on_controls=None,
        on_fullscreen_change=None,
        on_locale_change=None,
        locale_change_allowed: bool = False,
        **_,
    ) -> None:
        self.on_close = on_close
        self.on_controls = on_controls
        self.on_fullscreen_change = on_fullscreen_change
        self.on_locale_change = on_locale_change
        self.locale_change_allowed = bool(locale_change_allowed)
        if self._category not in self.CATEGORY_ORDER:
            self._category = self.DEFAULT_CATEGORY
        self._reset_touch_drag()
        cfg = self.ctx.config
        theme = self.ctx.theme
        sw, sh = self.ctx.screen_size

        # FontRegistry scales requested point sizes from the canvas height.
        # Every container and gap below follows the same density, while short
        # canvases keep the 720p baseline rather than shrinking below 44 px.
        self._density = max(
            1.0,
            float(getattr(self.ctx.fonts, "scale", 1.0)),
        )
        chrome = self.ctx.ui_chrome
        self._skin_active = bool(
            chrome is not None and getattr(chrome, "active", False)
        )
        self._minimum_target = max(44, self._sp(44))
        self._title_font_size = self.ctx.config.font_size_header
        self._section_font_size = 18
        self._body_font_size = 16
        self._help_font_size = 16
        self._shortcut_font_size = 14
        self._button_font_size = 16
        self._stepper_font_size = 20
        self._footer_font_size = 12

        self._title_line_h = self.ctx.fonts.get(
            self._title_font_size,
            bold=True,
        ).get_linesize()
        self._section_line_h = self.ctx.fonts.get(
            self._section_font_size,
            bold=True,
        ).get_linesize()
        self._body_line_h = self.ctx.fonts.get(
            self._body_font_size,
        ).get_linesize()
        self._help_line_h = self.ctx.fonts.get(
            self._help_font_size,
        ).get_linesize()
        self._shortcut_line_h = self.ctx.fonts.get(
            self._shortcut_font_size,
        ).get_linesize()
        self._footer_line_h = self.ctx.fonts.get(
            self._footer_font_size,
            bold=True,
        ).get_linesize()
        button_line_h = self.ctx.fonts.get(
            self._button_font_size,
            bold=True,
        ).get_linesize()
        self._button_h = max(
            self._minimum_target,
            button_line_h + self._sp(14),
        )

        edge_x = max(16, self._sp(34))
        edge_y = (
            max(12, self._sp(12))
            if sh <= 450
            else max(16, self._sp(24))
        )
        pw = min(self._sp(980), max(1, sw - edge_x * 2))
        ph = min(self._sp(720), max(1, sh - edge_y * 2))
        self._panel_rect = pygame.Rect((sw - pw) // 2, (sh - ph) // 2, pw, ph)
        self._panel = Panel(
            self._panel_rect,
            theme,
            fill=(*theme.bg_overlay[:3], 240),
            border=theme.border_strong,
            radius=theme.radius_l,
            border_width=2,
            chrome=chrome,
            screen="settings",
            role="main",
            density=self._density,
        )

        inner_pad = max(16, self._sp(30))
        header_pad = max(10, self._sp(14))
        self._title_surface = self.ctx.fonts.render(
            self.ctx.t("settings", "設定"),
            self._title_font_size,
            theme.accent,
            bold=True,
        )
        close_text = self.ctx.localization.t("close", "關閉")
        back_binding = self._binding_profile().primary_display(
            InputAction.BACK
        )
        close_label = f"{close_text}　{back_binding}"
        close_line_h = self.ctx.fonts.get(
            self._button_font_size,
            bold=True,
        ).get_linesize()
        close_h = max(
            self._minimum_target,
            close_line_h + self._sp(14),
        )
        close_font = self.ctx.fonts.get(
            self._button_font_size,
            bold=True,
        )
        desired_close_w = max(
            self._minimum_target,
            self._sp(120),
            close_font.size(close_label)[0] + self._sp(24),
        )
        title_x = self._panel_rect.x + inner_pad
        title_right = title_x + self._title_surface.get_width()
        max_close_w = max(
            self._minimum_target,
            self._panel_rect.right
            - inner_pad
            - title_right
            - self._sp(12),
        )
        close_w = min(desired_close_w, max_close_w)
        if close_font.size(close_label)[0] + self._sp(24) > close_w:
            close_label = close_text
        self._header_h = max(
            self._title_surface.get_height(),
            close_h,
        ) + header_pad * 2
        title_y = (
            self._panel_rect.y
            + (self._header_h - self._title_surface.get_height()) // 2
        )
        self._title_rect = self._title_surface.get_rect(
            topleft=(title_x, title_y),
        )
        close_rect = pygame.Rect(
            self._panel_rect.right - inner_pad - close_w,
            self._panel_rect.y + (self._header_h - close_h) // 2,
            close_w,
            close_h,
        )
        self._header_divider_y = self._panel_rect.y + self._header_h - 1

        # Tabs are fixed outside the scrolling body.  Landscape canvases use a
        # single row; narrow portrait canvases use a 2 x 2 grid so translated
        # labels retain a 44 px target and never become clipped abbreviations.
        tab_gap = max(4, self._sp(6))
        tab_top = self._header_divider_y + self._sp(8)
        tab_width = max(1, self._panel_rect.width - inner_pad * 2)
        tab_columns = 4 if tab_width >= self._sp(460) else 2
        tab_rows = (len(self.CATEGORY_ORDER) + tab_columns - 1) // tab_columns
        tab_button_w = max(
            self._minimum_target,
            (tab_width - tab_gap * (tab_columns - 1)) // tab_columns,
        )
        def tab_label(
            key: str,
            source: str,
            legacy_key: str,
            legacy_source: str,
        ) -> str:
            legacy = self.ctx.t(legacy_key, legacy_source)
            localized = self.ctx.t(key, legacy if legacy != legacy_source else source)
            # Legacy inline-locale packs predate the tab keys.  Honour an
            # explicit old-key translation instead of exposing Chinese default
            # copy in an otherwise translated settings screen.
            if (
                not str(getattr(self.ctx.localization, "effective", "") or "")
                and legacy != legacy_source
            ):
                return legacy
            return localized

        legacy_controls_display = (
            f"{self.ctx.t('controls', '操作')} / "
            f"{self.ctx.t('display', '顯示')}"
        )
        category_labels = {
            "reading": tab_label(
                "settings_tab_reading",
                "閱讀",
                "playback",
                "播放",
            ),
            "audio": tab_label(
                "settings_tab_audio",
                "音訊",
                "volume",
                "音量",
            ),
            "accessibility": tab_label(
                "settings_tab_accessibility",
                "輔助使用",
                "accessibility",
                "輔助使用",
            ),
            "controls_display": self.ctx.t(
                "settings_tab_controls_display",
                legacy_controls_display,
            ),
        }
        tab_button_h = max(
            self._button_required_height(
                category_labels[category],
                tab_button_w,
                self._button_font_size,
            )
            for category in self.CATEGORY_ORDER
        )
        self._tab_buttons = []
        for index, category in enumerate(self.CATEGORY_ORDER):
            row, column = divmod(index, tab_columns)
            selected = category == self._category
            button = self._mk(
                tab_button_w,
                tab_button_h,
                category_labels[category],
                lambda category=category: self._set_category(category),
                style="primary" if selected else "ghost",
                font_size=self._button_font_size,
                role="tab",
                selected=selected,
                wrap_text=True,
            )
            button.rect.topleft = (
                self._panel_rect.x
                + inner_pad
                + column * (tab_button_w + tab_gap),
                tab_top + row * (tab_button_h + tab_gap),
            )
            self._tab_buttons.append((category, button))
        tabs_height = tab_rows * tab_button_h + max(0, tab_rows - 1) * tab_gap
        self._tabs_rect = pygame.Rect(
            self._panel_rect.x + inner_pad,
            tab_top,
            tab_width,
            tabs_height,
        )
        body_top = self._tabs_rect.bottom + self._sp(8)
        footer_pad = self._sp(8)
        self._footer_line_capacity = 2 if tab_columns == 2 else 1
        footer_h = max(
            self._sp(34),
            self._footer_line_h * self._footer_line_capacity + self._sp(12),
        )
        self._footer_rect = pygame.Rect(
            self._panel_rect.x + inner_pad,
            self._panel_rect.bottom - footer_pad - footer_h,
            max(1, self._panel_rect.width - inner_pad * 2),
            footer_h,
        )
        body_bottom = self._footer_rect.top - self._sp(8)
        self._body_rect = pygame.Rect(
            self._panel_rect.x + inner_pad,
            body_top,
            max(1, self._panel_rect.width - inner_pad * 2),
            max(1, body_bottom - body_top),
        )
        self.close_btn = Button(
            close_rect,
            close_label,
            fonts=self.ctx.fonts,
            theme=theme,
            font_size=self._button_font_size,
            style="ghost",
            on_click=(
                lambda: self.on_close()
                if self.on_close
                else None
            ),
            chrome=chrome,
            screen="settings",
            role="close",
            tooltip=f"{close_text}：{back_binding}",
            motion=self.ctx.motion,
        )

        # Content laid out in body-relative coords (x from body.left, y from
        # the content top); rendered with a scroll offset + clip each frame.
        self._labels: list[tuple] = []   # (text, size, bold, color, cx, cy)
        # Full authored strings stay in ``_labels`` for inspection. Wrapped
        # visual lines are indexed separately so localization never changes
        # the semantic text exposed to tools and tests.
        self._label_lines: dict[int, tuple[str, ...]] = {}
        self._values: list[tuple] = []   # (fn->str, size, color, cx, cy)
        self._buttons: list[tuple] = []  # (Button, cx, cy)
        self._button_ids = []
        # (Slider, cx, cy, getter): a draggable level bar beside each volume's
        # -/+ steppers. getter re-syncs the knob when a stepper moves the value.
        self._sliders: list[tuple] = []
        self._content_regions: list[tuple[str, pygame.Rect]] = []
        self._was_dragging = False

        try:
            npcs = self.ctx.npcs.all() if self.ctx.npcs is not None else []
        except Exception:
            npcs = []

        row_gap = (
            max(4, self._sp(4))
            if sh <= 450
            else max(6, self._sp(8))
        )
        section_before = max(8, self._sp(14))
        section_after = max(6, self._sp(10))
        content_edge = self._sp(4)
        # Settings are easier to scan as one deliberate form column than as
        # controls stranded against the left edge of a very wide panel.  Short
        # canvases still consume the full body width; 720p/1080p centre a
        # bounded column while leaving the scroll viewport itself unchanged.
        content_width = max(
            1,
            min(
                self._sp(760),
                self._body_rect.width - content_edge * 2,
            ),
        )
        content_x = max(
            content_edge,
            (self._body_rect.width - content_width) // 2,
        )
        self._content_column_rect = pygame.Rect(
            content_x,
            0,
            content_width,
            1,
        )
        cy = self._sp(2)

        def region(name: str, y: int, height: int) -> None:
            self._content_regions.append((
                name,
                pygame.Rect(content_x, y, content_width, max(1, height)),
            ))

        def add_button(
            control_id: str,
            button: Button,
            x: int,
            y: int,
        ) -> None:
            """Append one focusable control with stable semantic identity."""
            self._buttons.append((button, x, y))
            self._button_ids.append(control_id)

        def wrapped_label(
            text: str,
            size: int,
            bold: bool,
            color: tuple,
            x: int,
            y: int,
            width: int,
        ) -> int:
            font = self.ctx.fonts.get(size, bold=bold)
            lines = tuple(_wrap_lines(text, font, max(1, width))) or ("",)
            index = len(self._labels)
            self._labels.append((text, size, bold, color, x, y))
            self._label_lines[index] = lines
            return len(lines) * font.get_linesize()

        def section(title: str) -> None:
            nonlocal cy
            cy += section_before
            section_y = cy
            self._labels.append((
                title,
                self._section_font_size,
                True,
                theme.accent_warm,
                content_x,
                cy,
            ))
            region(f"section:{title}", section_y, self._section_line_h)
            cy += self._section_line_h + section_after

        choice_gap = max(6, self._sp(10))
        choice_target_w = self._sp(120)

        def choice_grid(options, selected, handler, name: str) -> None:
            nonlocal cy
            count = len(options)
            required_w = max(
                self._button_required_width(
                    label,
                    self._button_font_size,
                )
                for _value, label in options
            )
            desired_w = min(
                content_width,
                max(choice_target_w, required_w),
            )
            max_columns = min(
                count,
                max(
                    1,
                    (content_width + choice_gap)
                    // (desired_w + choice_gap),
                ),
            )
            columns = min(4, max_columns)
            button_w = (
                content_width - choice_gap * (columns - 1)
            ) // columns
            button_w = max(
                min(desired_w, content_width),
                button_w,
            )
            button_w = max(self._minimum_target, button_w)
            start_y = cy
            row_count = (count + columns - 1) // columns
            row_heights = [self._button_h for _row in range(row_count)]
            for index, (_value, label) in enumerate(options):
                row = index // columns
                row_heights[row] = max(
                    row_heights[row],
                    self._button_required_height(
                        label,
                        button_w,
                        self._button_font_size,
                    ),
                )
            row_offsets: list[int] = []
            offset = 0
            for row_height in row_heights:
                row_offsets.append(offset)
                offset += row_height + row_gap
            for index, (value, label) in enumerate(options):
                col = index % columns
                row = index // columns
                x = content_x + col * (button_w + choice_gap)
                y = cy + row_offsets[row]
                is_selected = selected(value)
                button = self._mk(
                    button_w,
                    row_heights[row],
                    label,
                    lambda value=value: handler(value),
                    style="primary" if is_selected else "ghost",
                    font_size=self._button_font_size,
                    role="choice",
                    selected=is_selected,
                    wrap_text=True,
                )
                add_button(f"{name}:{value}", button, x, y)
            grid_h = (
                sum(row_heights)
                + max(0, row_count - 1) * row_gap
            )
            region(name, start_y, grid_h)
            cy += grid_h + row_gap

        def fitted_button(
            label: str,
            on_click,
            *,
            preferred_width: int,
            style: str = "ghost",
            role: str = "action",
            selected: bool = False,
        ) -> Button:
            width = content_width
            height = self._button_required_height(
                label,
                width,
                self._button_font_size,
            )
            return self._mk(
                width,
                height,
                label,
                on_click,
                style=style,
                font_size=self._button_font_size,
                role=role,
                selected=selected,
                wrap_text=True,
            )

        # Derive shared volume columns from the widest live label.  Wide
        # canvases use one row.  Narrow portrait canvases put the slider on a
        # second row, avoiding the old one-pixel slider / off-canvas stepper
        # failure while keeping every target at least 44 px.
        volume_names = [
            "BGM",
            self.ctx.t("voice", "語音"),
            self.ctx.t("sfx", "音效"),
            *(npc.name[:6] for npc in npcs),
        ]
        max_name_w = max(
            self.ctx.fonts.get(self._body_font_size).size(name)[0]
            for name in volume_names
        )
        pct_w = self.ctx.fonts.get(self._body_font_size).size("100%")[0]
        self._stepper_width = self._minimum_target
        stepper_gap = max(6, self._sp(8))
        slider_edge_pad = max(10, self._sp(8))
        steppers_w = self._stepper_width * 2 + stepper_gap
        minimum_wide_width = (
            max_name_w
            + self._sp(12)
            + pct_w
            + self._sp(18)
            + steppers_w
            + self._sp(14)
            + self._sp(72)
            + slider_edge_pad
        )
        self._volume_is_compact = content_width < minimum_wide_width
        self._volume_label_x = content_x
        if self._volume_is_compact:
            self._stepper_x = content_x + content_width - steppers_w
            self._volume_value_x = min(
                self._stepper_x - pct_w - self._sp(8),
                content_x + max_name_w + self._sp(10),
            )
            self._slider_x = content_x + slider_edge_pad
            self._slider_width = max(
                self._minimum_target,
                content_width - slider_edge_pad * 2,
            )
            self._volume_slider_y_offset = self._button_h + row_gap
            self._volume_slider_h = self._button_h
            self._volume_row_h = (
                self._button_h + row_gap + self._volume_slider_h
            )
        else:
            self._volume_value_x = (
                content_x + max_name_w + self._sp(12)
            )
            self._stepper_x = (
                self._volume_value_x + pct_w + self._sp(18)
            )
            self._slider_x = (
                self._stepper_x + steppers_w + self._sp(14)
            )
            available_slider_w = (
                content_x + content_width - slider_edge_pad - self._slider_x
            )
            self._slider_width = min(self._sp(360), available_slider_w)
            self._volume_slider_y_offset = 0
            self._volume_slider_h = self._button_h
            self._volume_row_h = max(
                self._button_h,
                self._body_line_h + self._sp(12),
            )

        def volume_row(
            label: str,
            value_fn,
            minus_handler,
            plus_handler,
            slider_handler,
            getter,
            name: str,
        ) -> None:
            nonlocal cy
            row_y = cy
            text_y = cy + (self._button_h - self._body_line_h) // 2
            label_width = max(
                1,
                self._volume_value_x - self._volume_label_x - self._sp(8),
            )
            display_label = self._ellipsize(
                label,
                self.ctx.fonts.get(self._body_font_size),
                label_width,
            )
            self._labels.append((
                display_label,
                self._body_font_size,
                False,
                theme.text,
                self._volume_label_x,
                text_y,
            ))
            self._values.append((
                value_fn,
                self._body_font_size,
                theme.text_mute,
                self._volume_value_x,
                text_y,
            ))
            minus = self._mk(
                self._stepper_width,
                self._volume_row_h,
                "-",
                minus_handler,
                font_size=self._stepper_font_size,
                role="stepper",
            )
            plus = self._mk(
                self._stepper_width,
                self._volume_row_h,
                "+",
                plus_handler,
                font_size=self._stepper_font_size,
                role="stepper",
            )
            add_button(f"{name}:decrease", minus, self._stepper_x, cy)
            add_button(
                f"{name}:increase",
                plus,
                self._stepper_x + self._stepper_width + stepper_gap,
                cy,
            )
            self._add_volume_slider(
                cy + self._volume_slider_y_offset,
                slider_handler,
                getter,
                height=self._volume_slider_h,
            )
            region(name, row_y, self._volume_row_h)
            cy += self._volume_row_h + row_gap

        def add_toggle(
            label: str,
            attr: str,
            handler,
            *,
            prefix: str,
        ) -> None:
            nonlocal cy
            on = bool(getattr(cfg, attr))
            button = fitted_button(
                self._toggle_label(label, on),
                handler,
                preferred_width=self._sp(360),
                style="primary" if on else "ghost",
                role="toggle",
                selected=on,
            )
            control_id = f"{prefix}:{attr}"
            add_button(control_id, button, content_x, cy)
            region(control_id, cy, button.rect.height)
            cy += button.rect.height + row_gap

        def build_reading() -> None:
            nonlocal cy
            locale_options = []
            for option in getattr(self.ctx.localization, "options", ()) or ():
                if isinstance(option, dict):
                    locale_id = option.get("id")
                    label = option.get("label")
                else:
                    locale_id = getattr(option, "id", None)
                    label = getattr(option, "label", None)
                if isinstance(locale_id, str) and isinstance(label, str):
                    locale_options.append((locale_id, label))
            if len(locale_options) > 1:
                section(self.ctx.t("language", "語言"))
                effective_locale = str(
                    getattr(self.ctx.localization, "effective", "") or ""
                )
                if (
                    self.locale_change_allowed
                    and self.on_locale_change is not None
                ):
                    choice_grid(
                        locale_options,
                        lambda value: value == effective_locale,
                        self._set_locale,
                        "language",
                    )
                else:
                    current_label = next(
                        (
                            label
                            for locale_id, label in locale_options
                            if locale_id == effective_locale
                        ),
                        effective_locale,
                    )
                    current_h = wrapped_label(
                        current_label,
                        self._body_font_size,
                        True,
                        theme.text,
                        content_x,
                        cy,
                        content_width,
                    )
                    region("language:current", cy, current_h)
                    cy += current_h + self._sp(6)
                    language_help = self.ctx.t(
                        "language_title_only_help",
                        "為保留目前台詞與存檔狀態，請回到標題畫面切換語言。",
                    )
                    help_h = wrapped_label(
                        language_help,
                        self._help_font_size,
                        False,
                        theme.text_mute,
                        content_x,
                        cy,
                        content_width,
                    )
                    region("language:help", cy, help_h)
                    cy += help_h + row_gap

            section(self.ctx.t("text_speed", "文字速度"))
            choice_grid(
                [
                    (0.0, self.ctx.t("instant", "瞬間")),
                    (20.0, self.ctx.t("slow", "慢")),
                    (45.0, self.ctx.t("medium", "中")),
                    (80.0, self.ctx.t("fast", "快")),
                ],
                lambda value: abs(cfg.text_speed - value) < 0.1,
                self._set_text_speed,
                "text_speed",
            )
            section(self.ctx.t("auto_play_speed", "自動播放速度"))
            choice_grid(
                [
                    (0.5, self.ctx.t("slow", "慢")),
                    (1.0, self.ctx.t("medium", "中")),
                    (1.5, self.ctx.t("fast", "快")),
                    (2.0, self.ctx.t("very_fast", "極快")),
                ],
                lambda value: abs(cfg.auto_play_speed - value) < 0.05,
                self._set_auto_play_speed,
                "auto_speed",
            )
            section(self.ctx.t("playback", "播放"))
            for label, attr, handler in [
                (
                    self.ctx.t("wait_for_voice", "等待語音"),
                    "auto_play_wait_voice",
                    self._toggle_wait_voice,
                ),
                (
                    self.ctx.t("skip_read_only", "僅快進已讀"),
                    "skip_unread_only",
                    self._toggle_skip_unread,
                ),
                (
                    self.ctx.t("nvl_mode", "NVL 模式"),
                    "nvl_mode",
                    self._toggle_nvl,
                ),
            ]:
                add_toggle(label, attr, handler, prefix="playback")

        def build_audio() -> None:
            nonlocal cy
            section(self.ctx.t("volume", "音量"))
            volume_row(
                "BGM",
                self._bgm_pct_str,
                lambda: self._adjust_volume(-0.1),
                lambda: self._adjust_volume(0.1),
                self._apply_bgm,
                lambda: self.ctx.config.bgm_volume,
                "volume:bgm",
            )
            volume_row(
                self.ctx.t("voice", "語音"),
                self._voice_pct_str,
                lambda: self._adjust_voice_volume(-0.1),
                lambda: self._adjust_voice_volume(0.1),
                self._apply_voice,
                lambda: self.ctx.config.voice_volume,
                "volume:voice",
            )
            volume_row(
                self.ctx.t("sfx", "音效"),
                self._sfx_pct_str,
                lambda: self._adjust_sfx_volume(-0.1),
                lambda: self._adjust_sfx_volume(0.1),
                self._apply_sfx,
                lambda: self.ctx.config.sfx_volume,
                "volume:sfx",
            )
            section(self.ctx.t("playback", "播放"))
            for label, attr, handler in [
                (
                    self.ctx.t("typewriter_sound", "打字音效"),
                    "typewriter_sound",
                    self._toggle_typewriter,
                ),
                (
                    self.ctx.t("ui_sound", "介面音效"),
                    "ui_sound_enabled",
                    self._toggle_ui_sound,
                ),
            ]:
                add_toggle(label, attr, handler, prefix="audio")

            voiced_npcs = self._voiced_npcs(npcs)
            if voiced_npcs:
                section(self.ctx.t(
                    "character_voice_volume",
                    "角色語音音量",
                ))
                for npc in voiced_npcs:
                    nid = npc.id
                    volume_row(
                        npc.name,
                        (
                            lambda nid=nid:
                            f"{int(self._char_voice_volume(nid) * 100)}%"
                        ),
                        lambda nid=nid: self._adjust_char_voice(nid, -0.1),
                        lambda nid=nid: self._adjust_char_voice(nid, 0.1),
                        lambda value, nid=nid: self._set_char_voice(nid, value),
                        lambda nid=nid: self._char_voice_volume(nid),
                        f"character_voice:{nid}",
                    )

        def build_accessibility() -> None:
            nonlocal cy
            high_contrast_on = bool(
                self.ctx.visual is not None
                and self.ctx.visual.high_contrast
            )
            high_contrast_btn = fitted_button(
                self._toggle_label(
                    self.ctx.t("high_contrast", "高對比介面"),
                    high_contrast_on,
                ),
                self._toggle_high_contrast,
                preferred_width=self._sp(360),
                style="primary" if high_contrast_on else "ghost",
                role="toggle",
                selected=high_contrast_on,
            )
            add_button(
                "accessibility:visual_accessibility_mode",
                high_contrast_btn,
                content_x,
                cy,
            )
            region(
                "accessibility:visual_accessibility_mode",
                cy,
                high_contrast_btn.rect.height,
            )
            cy += high_contrast_btn.rect.height + row_gap

            rm_on = bool(cfg.reduce_motion)
            rm_btn = fitted_button(
                self._toggle_label(
                    self.ctx.t("reduce_motion", "減少動態效果"),
                    rm_on,
                ),
                self._toggle_reduce_motion,
                preferred_width=self._sp(360),
                style="primary" if rm_on else "ghost",
                role="toggle",
                selected=rm_on,
            )
            add_button(
                "accessibility:reduce_motion",
                rm_btn,
                content_x,
                cy,
            )
            region("accessibility:reduce_motion", cy, rm_btn.rect.height)
            cy += rm_btn.rect.height + row_gap

            text_scale_btn = fitted_button(
                self._text_scale_button_label(),
                self._cycle_text_scale,
                preferred_width=self._sp(360),
                style="primary",
                role="choice",
                selected=True,
            )
            add_button(
                "accessibility:text_scale",
                text_scale_btn,
                content_x,
                cy,
            )
            region("accessibility:text_scale", cy, text_scale_btn.rect.height)
            cy += text_scale_btn.rect.height + row_gap

            captions_on = cfg.show_sound_captions is True
            captions_btn = fitted_button(
                self._toggle_label(
                    self.ctx.t("sound_captions", "聲音字幕"),
                    captions_on,
                ),
                self._toggle_sound_captions,
                preferred_width=self._sp(360),
                style="primary" if captions_on else "ghost",
                role="toggle",
                selected=captions_on,
            )
            add_button(
                "accessibility:show_sound_captions",
                captions_btn,
                content_x,
                cy,
            )
            region(
                "accessibility:show_sound_captions",
                cy,
                captions_btn.rect.height,
            )
            cy += captions_btn.rect.height + section_after

            # Engine-owned neutral preview.  Rebuilding after a selection
            # changes this measured font immediately without touching story
            # state, artwork, or audio ownership.
            preview_text = self.ctx.t(
                "text_size_help",
                "這段文字會立即依所選大小顯示。",
            )
            preview_size = max(
                self._help_font_size,
                int(round(
                    self._body_font_size * self._effective_text_scale()
                )),
            )
            preview_h = wrapped_label(
                preview_text,
                preview_size,
                False,
                theme.text,
                content_x,
                cy,
                content_width,
            )
            region("preview:text_scale", cy, preview_h)
            cy += preview_h + row_gap

            for name, copy in [
                (
                    "help:visual_accessibility_mode",
                    self.ctx.t(
                        "high_contrast_help",
                        "提高文字、控制邊界與焦點對比；不改變背景、CG 或角色立繪",
                    ),
                ),
                (
                    "help:reduce_motion",
                    self.ctx.t(
                        "reduce_motion_help",
                        "停止位移／閃光／脈動與待機動畫；保留靜態狀態提示",
                    ),
                ),
                (
                    "help:show_sound_captions",
                    self.ctx.t(
                        "sound_captions_help",
                        "以文字顯示環境音、音效、音樂與重要的寂靜",
                    ),
                ),
            ]:
                help_h = wrapped_label(
                    copy,
                    self._help_font_size,
                    False,
                    theme.text_mute,
                    content_x,
                    cy,
                    content_width,
                )
                region(name, cy, help_h)
                cy += help_h + row_gap

        def build_controls_display() -> None:
            nonlocal cy
            section(self.ctx.t("controls", "操作"))
            if callable(self.on_controls):
                controls_button = fitted_button(
                    self.ctx.t("control_settings", "操作設定"),
                    self._open_controls,
                    preferred_width=self._sp(360),
                    role="action",
                )
                add_button(
                    "controls:open",
                    controls_button,
                    content_x,
                    cy,
                )
                region("controls:open", cy, controls_button.rect.height)
                cy += controls_button.rect.height + row_gap
            binding_label = self.ctx.t("current_bindings", "目前配置")
            summary = self._controls_summary()
            summary_h = wrapped_label(
                f"{binding_label}：{summary}",
                self._shortcut_font_size,
                False,
                theme.text_mute,
                content_x,
                cy,
                content_width,
            )
            region("controls:summary", cy, summary_h)
            cy += summary_h + row_gap

            section(self.ctx.t("display", "顯示"))
            fullscreen_binding = self._binding_profile().display(
                InputAction.TOGGLE_FULLSCREEN,
            )
            fullscreen = fitted_button(
                self.ctx.t(
                    "toggle_fullscreen",
                    "切換全螢幕　{binding}",
                    binding=fullscreen_binding,
                ),
                self._toggle_fullscreen,
                preferred_width=self._sp(360),
                role="action",
            )
            add_button(
                "display:fullscreen",
                fullscreen,
                content_x,
                cy,
            )
            region("display:fullscreen", cy, fullscreen.rect.height)
            cy += fullscreen.rect.height + row_gap
            for label, attr, handler in [
                (
                    self.ctx.t("status_hud", "章節／日期提示"),
                    "show_status_hud",
                    self._toggle_status_hud,
                ),
                (
                    self.ctx.t("dim_inactive_speakers", "立繪壓暗非說話者"),
                    "dim_inactive_speakers",
                    self._toggle_dim_speakers,
                ),
                (
                    self.ctx.t("auto_emote", "立繪情緒反應"),
                    "auto_emote_on_emotion",
                    self._toggle_auto_emote,
                ),
                (
                    self.ctx.t("affection_feedback", "好感變化提示"),
                    "show_affection_feedback",
                    self._toggle_affection_feedback,
                ),
            ]:
                add_toggle(label, attr, handler, prefix="display")

            controls_help = self.ctx.t(
                "control_settings_help",
                "完整清單、衝突處理與重設請進入「操作設定」。",
            )
            controls_help_h = wrapped_label(
                controls_help,
                self._help_font_size,
                False,
                theme.text_mute,
                content_x,
                cy,
                content_width,
            )
            region("controls:help", cy, controls_help_h)
            cy += controls_help_h + row_gap

        builders = {
            "reading": build_reading,
            "audio": build_audio,
            "accessibility": build_accessibility,
            "controls_display": build_controls_display,
        }
        builders.get(self._category, build_accessibility)()

        self._content_height = cy + self._sp(10)
        self._content_column_rect.height = self._content_height
        enabled = [
            index
            for index, (button, _cx, _cy) in enumerate(self._buttons)
            if button.enabled
        ]
        remembered_id = self._category_focus_ids.get(self._category)
        if remembered_id in self._button_ids:
            remembered_index = self._button_ids.index(remembered_id)
            self._focus = (
                remembered_index
                if remembered_index in enabled
                else (enabled[0] if enabled else -1)
            )
        elif self._focus not in enabled:
            self._focus = enabled[0] if enabled else -1
        requested_scroll = self._category_scroll.get(
            self._category,
            self._scroll_y,
        )
        self._scroll_y = max(0, min(requested_scroll, self._max_scroll()))
        self._ensure_focus_visible()
        self._sync_focus_state()
        self._remember_category_state()

    # ---- value formatters ----------------------------------------------------

    def _bgm_pct_str(self) -> str:
        return f"{int(self.ctx.config.bgm_volume * 100)}%"

    def _voice_pct_str(self) -> str:
        return f"{int(self.ctx.config.voice_volume * 100)}%"

    def _sfx_pct_str(self) -> str:
        return f"{int(self.ctx.config.sfx_volume * 100)}%"

    def _voiced_npcs(self, npcs: list) -> list:
        """Return only characters backed by an authored voice cue.

        A large NPC registry must not turn the Audio tab into a wall of inert
        sliders.  Existing explicit user overrides stay visible for backward
        compatibility even when a later pack revision removes the cue.
        """
        ids = set(
            getattr(
                self.ctx.config,
                "per_character_voice_volume",
                {},
            )
            or {}
        )
        story = getattr(self.ctx.state, "story", None)
        for scene in getattr(story, "scenes", {}).values():
            for line in getattr(scene, "lines", ()):
                voice = getattr(line, "voice", None)
                if not isinstance(voice, str) or not voice.strip():
                    continue
                if voice.strip().lower() in {"none", "silence", "-"}:
                    continue
                speaker = getattr(line, "speaker", None)
                npc = (
                    self.ctx.npcs.by_name(speaker)
                    if self.ctx.npcs is not None and speaker
                    else None
                )
                if npc is not None:
                    ids.add(npc.id)
        return [npc for npc in npcs if npc.id in ids]

    def _text_scale_options(self) -> tuple[tuple[float, str], ...]:
        return (
            (1.0, self.ctx.t("standard", "標準")),
            (1.2, self.ctx.t("large", "大")),
            (1.4, self.ctx.t("extra_large", "特大")),
        )

    def _effective_text_scale(self) -> float:
        try:
            value = float(self.ctx.config.text_scale)
        except (TypeError, ValueError):
            return 1.0
        if not math.isfinite(value):
            return 1.0
        return min((1.0, 1.2, 1.4), key=lambda item: abs(item - value))

    def _text_scale_button_label(self) -> str:
        options = self._text_scale_options()
        _value, choice = min(
            options,
            key=lambda option: abs(self._effective_text_scale() - option[0]),
        )
        return f"{self.ctx.t('text_size', '文字大小')}：{choice}"

    def _cycle_text_scale(self) -> None:
        options = self._text_scale_options()
        current = min(
            range(len(options)),
            key=lambda index: abs(
                self._effective_text_scale() - options[index][0]
            ),
        )
        self._set_text_scale(options[(current + 1) % len(options)][0])

    def _focused_control_id(self) -> str | None:
        if 0 <= self._focus < len(self._button_ids):
            return self._button_ids[self._focus]
        return None

    def _remember_category_state(self) -> None:
        focus_id = self._focused_control_id()
        if focus_id is not None:
            self._category_focus_ids[self._category] = focus_id
        self._category_scroll[self._category] = int(self._scroll_y)

    def _set_category(self, category: str) -> None:
        if category not in self.CATEGORY_ORDER or category == self._category:
            return
        self._remember_category_state()
        self._category = category
        self._focus = -1
        self._scroll_y = self._category_scroll.get(category, 0)
        self.enter(
            on_close=self.on_close,
            on_controls=self.on_controls,
            on_fullscreen_change=self.on_fullscreen_change,
            on_locale_change=self.on_locale_change,
            locale_change_allowed=self.locale_change_allowed,
        )

    def _switch_category(self, delta: int) -> None:
        if not delta:
            return
        current = self.CATEGORY_ORDER.index(self._category)
        self._set_category(
            self.CATEGORY_ORDER[(current + delta) % len(self.CATEGORY_ORDER)]
        )

    def _controls_summary(self) -> str:
        custom_count = len(self._binding_profile().overrides)
        if custom_count:
            return self.ctx.t(
                "custom_bindings_count",
                "已自訂 {count} 項操作",
                count=custom_count,
            )
        return self.ctx.t("default_bindings", "使用預設按鍵")

    # ---- handlers ------------------------------------------------------------

    def _rebuild(self) -> None:
        """Re-run layout while preserving semantic focus and page scroll."""
        self._remember_category_state()
        self.enter(
            on_close=self.on_close,
            on_controls=self.on_controls,
            on_fullscreen_change=self.on_fullscreen_change,
            on_locale_change=self.on_locale_change,
            locale_change_allowed=self.locale_change_allowed,
        )

    def resume(self) -> None:
        """Refresh binding labels after returning from ControlsScene."""
        self._rebuild()

    def _set_text_speed(self, v: float) -> None:
        self.ctx.config.text_speed = v
        self.ctx.config.save_to_disk()
        self._rebuild()

    def _set_locale(self, locale_id: str) -> None:
        if not self.locale_change_allowed or self.on_locale_change is None:
            return
        self.on_locale_change(locale_id)
        self._rebuild()

    def _set_text_scale(self, v: float) -> None:
        self.ctx.config.text_scale = v
        self.ctx.config.save_to_disk()
        self._rebuild()

    def _adjust_volume(self, delta: float) -> None:
        new_v = max(0.0, min(1.0, round(self.ctx.config.bgm_volume + delta, 3)))
        self.ctx.config.bgm_volume = new_v
        self.ctx.assets.set_music_volume(new_v)
        self.ctx.config.save_to_disk()

    def _adjust_voice_volume(self, delta: float) -> None:
        new_v = max(0.0, min(1.0, round(self.ctx.config.voice_volume + delta, 3)))
        self.ctx.config.voice_volume = new_v
        self.ctx.assets._voice_volume = new_v
        self.ctx.config.save_to_disk()

    def _char_voice_volume(self, npc_id: str) -> float:
        """Effective per-character voice volume (falls back to the global)."""
        return self.ctx.config.per_character_voice_volume.get(
            npc_id, self.ctx.config.voice_volume)

    def _adjust_sfx_volume(self, delta: float) -> None:
        new_v = max(0.0, min(1.0, round(self.ctx.config.sfx_volume + delta, 3)))
        self.ctx.config.sfx_volume = new_v
        # Live-update the looping ambient bed (it rides the SFX bus at 0.55).
        self.ctx.assets.set_ambient_volume(new_v * 0.55)
        self.ctx.config.save_to_disk()

    # Absolute live-apply for the sliders (no disk write per drag frame — the
    # scene saves once when the drag ends, see update()).
    def _apply_bgm(self, v: float) -> None:
        self.ctx.config.bgm_volume = max(0.0, min(1.0, round(v, 3)))
        self.ctx.assets.set_music_volume(self.ctx.config.bgm_volume)

    def _apply_voice(self, v: float) -> None:
        self.ctx.config.voice_volume = max(0.0, min(1.0, round(v, 3)))
        self.ctx.assets._voice_volume = self.ctx.config.voice_volume

    def _apply_sfx(self, v: float) -> None:
        self.ctx.config.sfx_volume = max(0.0, min(1.0, round(v, 3)))
        self.ctx.assets.set_ambient_volume(self.ctx.config.sfx_volume * 0.55)

    def _adjust_char_voice(self, npc_id: str, delta: float) -> None:
        cur = self._char_voice_volume(npc_id)
        new_v = max(0.0, min(1.0, round(cur + delta, 3)))
        self.ctx.config.per_character_voice_volume[npc_id] = new_v
        self.ctx.config.save_to_disk()

    def _set_char_voice(self, npc_id: str, v: float) -> None:
        # Absolute live-apply for the slider (the scene saves on drag-release).
        self.ctx.config.per_character_voice_volume[npc_id] = max(
            0.0, min(1.0, round(v, 3)))

    def _toggle_label(self, label: str, on: bool) -> str:
        state = self.ctx.t("on", "開") if on else self.ctx.t("off", "關")
        return self.ctx.t(
            "toggle_format",
            "{label}：{state}",
            label=label,
            state=state,
        )

    def _set_auto_play_speed(self, v: float) -> None:
        self.ctx.config.auto_play_speed = v
        self.ctx.config.save_to_disk()
        self._rebuild()

    def _toggle_wait_voice(self) -> None:
        cfg = self.ctx.config
        cfg.auto_play_wait_voice = not cfg.auto_play_wait_voice
        cfg.save_to_disk()
        self._rebuild()

    def _toggle_skip_unread(self) -> None:
        cfg = self.ctx.config
        cfg.skip_unread_only = not cfg.skip_unread_only
        cfg.save_to_disk()
        self._rebuild()

    def _toggle_dim_speakers(self) -> None:
        cfg = self.ctx.config
        cfg.dim_inactive_speakers = not cfg.dim_inactive_speakers
        cfg.save_to_disk()
        self._rebuild()

    def _toggle_reduce_motion(self) -> None:
        cfg = self.ctx.config
        cfg.reduce_motion = not cfg.reduce_motion
        cfg.save_to_disk()
        self._rebuild()

    def _toggle_high_contrast(self) -> None:
        cfg = self.ctx.config
        is_high_contrast = bool(
            self.ctx.visual is not None
            and self.ctx.visual.high_contrast
        )
        cfg.visual_accessibility_mode = (
            "standard" if is_high_contrast else "high_contrast"
        )
        cfg.save_to_disk()
        self._rebuild()

    def _toggle_sound_captions(self) -> None:
        cfg = self.ctx.config
        cfg.show_sound_captions = cfg.show_sound_captions is not True
        cfg.save_to_disk()
        self._rebuild()

    def _toggle_auto_emote(self) -> None:
        cfg = self.ctx.config
        cfg.auto_emote_on_emotion = not cfg.auto_emote_on_emotion
        cfg.save_to_disk()
        self._rebuild()

    def _toggle_typewriter(self) -> None:
        cfg = self.ctx.config
        cfg.typewriter_sound = not cfg.typewriter_sound
        cfg.save_to_disk()
        self._rebuild()

    def _toggle_ui_sound(self) -> None:
        cfg = self.ctx.config
        cfg.ui_sound_enabled = not cfg.ui_sound_enabled
        cfg.save_to_disk()
        self._rebuild()

    def _toggle_nvl(self) -> None:
        cfg = self.ctx.config
        cfg.nvl_mode = not cfg.nvl_mode
        cfg.save_to_disk()
        self._rebuild()

    def _toggle_status_hud(self) -> None:
        cfg = self.ctx.config
        cfg.show_status_hud = not cfg.show_status_hud
        cfg.save_to_disk()
        self._rebuild()

    def _toggle_affection_feedback(self) -> None:
        cfg = self.ctx.config
        cfg.show_affection_feedback = not cfg.show_affection_feedback
        cfg.save_to_disk()
        self._rebuild()

    def _toggle_fullscreen(self) -> None:
        enabled = not bool(self.ctx.config.fullscreen)
        self.ctx.config.fullscreen = enabled
        if self.on_fullscreen_change is not None:
            try:
                self.on_fullscreen_change(enabled)
            except pygame.error:
                self.ctx.config.fullscreen = not enabled
                return
        else:
            try:
                pygame.display.toggle_fullscreen()
            except pygame.error:
                # Headless and dummy-video sessions still persist the intended
                # mode for the next real launch.
                pass
        self.ctx.config.save_to_disk()
        self._rebuild()

    def _open_controls(self) -> None:
        if self.on_controls is not None:
            self.on_controls()

    def _footer_text(self) -> str:
        profile = self._binding_profile()
        nav_left = profile.primary_display(InputAction.NAV_LEFT)
        nav_right = profile.primary_display(InputAction.NAV_RIGHT)
        nav_up = profile.primary_display(InputAction.NAV_UP)
        nav_down = profile.primary_display(InputAction.NAV_DOWN)
        confirm = profile.primary_display(InputAction.CONFIRM_ADVANCE)
        back = profile.primary_display(InputAction.BACK)
        return self.ctx.t(
            "settings_footer_tabs",
            (
                "{left} / {right} 分頁　{up} / {down} 選項　"
                "{confirm} 變更　{back} 返回"
            ),
            left=nav_left,
            right=nav_right,
            confirm=confirm,
            back=back,
            up=nav_up,
            down=nav_down,
        )

    # ---- scroll plumbing -----------------------------------------------------

    def _max_scroll(self) -> int:
        return max(0, self._content_height - self._body_rect.height)

    def _reposition(self) -> None:
        body = self._body_rect
        for b, cx, cy in self._buttons:
            b.rect.topleft = (body.x + cx, body.y + cy - self._scroll_y)
        for s, cx, cy, _g in self._sliders:
            s.rect.topleft = (body.x + cx, body.y + cy - self._scroll_y)

    def _sync_focus_state(self) -> None:
        for index, (button, _cx, _cy) in enumerate(self._buttons):
            button.focused = index == self._focus and button.enabled

    def _ensure_focus_visible(self) -> None:
        if not (0 <= self._focus < len(self._buttons)):
            self._scroll_y = max(0, min(self._scroll_y, self._max_scroll()))
            self._reposition()
            return
        button, _cx, content_y = self._buttons[self._focus]
        padding = self._sp(4)
        focus_top = max(0, content_y - padding)
        focus_bottom = content_y + button.rect.height + padding
        if focus_top < self._scroll_y:
            self._scroll_y = focus_top
        elif focus_bottom > self._scroll_y + self._body_rect.height:
            self._scroll_y = focus_bottom - self._body_rect.height
        self._scroll_y = max(0, min(self._scroll_y, self._max_scroll()))
        self._reposition()

    @staticmethod
    def _action_pressed(inp, action: str) -> bool:
        pressed = getattr(inp, "pressed", None)
        return bool(callable(pressed) and pressed(action))

    @classmethod
    def _navigation_delta(cls, inp) -> int:
        if (
            cls._action_pressed(inp, InputAction.NAV_UP)
            or cls._action_pressed(inp, InputAction.FOCUS_PREVIOUS)
        ):
            return -1
        if (
            cls._action_pressed(inp, InputAction.NAV_DOWN)
            or cls._action_pressed(inp, InputAction.FOCUS_NEXT)
        ):
            return 1
        nav = int(getattr(inp, "nav", 0))
        if nav:
            return -1 if nav < 0 else 1
        return 0

    def _move_focus(self, delta: int) -> None:
        enabled = [
            index
            for index, (button, _cx, _cy) in enumerate(self._buttons)
            if button.enabled
        ]
        if not enabled or not delta:
            return
        if self._focus not in enabled:
            self._focus = enabled[0] if delta > 0 else enabled[-1]
        else:
            position = enabled.index(self._focus)
            self._focus = enabled[(position + delta) % len(enabled)]
        self._ensure_focus_visible()
        self._sync_focus_state()
        self._remember_category_state()

    def _reset_touch_drag(self) -> None:
        self._touch_dragging = False
        self._touch_drag_moved = False
        self._touch_drag_distance = 0
        self._touch_drag_pending_y = 0

    def _touch_started_on_slider(self, pos: tuple[int, int]) -> bool:
        """Let a horizontal slider own touches that begin on its hit area."""
        body = self._body_rect
        for slider, _cx, _cy, _getter in self._sliders:
            hit = slider.rect.inflate(self._sp(16), self._sp(20))
            if body.contains(slider.rect) and hit.collidepoint(pos):
                return True
        return False

    def _update_touch_scroll(self, inp) -> bool:
        """Capture vertical list drags and consume their pointer release."""
        started = bool(getattr(inp, "touch_started", False))
        active = bool(getattr(inp, "touch_active", False))
        released = bool(getattr(inp, "touch_released", False))
        pointer = getattr(inp, "mouse_pos", (-1, -1))

        if started:
            self._reset_touch_drag()
            if (
                self._body_rect.collidepoint(pointer)
                and not self._touch_started_on_slider(pointer)
            ):
                self._touch_dragging = True

        if not self._touch_dragging:
            return False

        delta_y = int(getattr(inp, "touch_delta", (0, 0))[1])
        if delta_y:
            self._touch_drag_distance += abs(delta_y)
            self._touch_drag_pending_y += delta_y
            if self._touch_drag_distance >= max(6, self._sp(8)):
                self._touch_drag_moved = True
            if self._touch_drag_moved:
                self._scroll_y -= self._touch_drag_pending_y
                self._touch_drag_pending_y = 0
                self._scroll_y = max(
                    0,
                    min(self._scroll_y, self._max_scroll()),
                )
                self._reposition()
                self._remember_category_state()

        ending = released or (not active and not started)
        consume = self._touch_drag_moved or not ending
        if ending:
            self._reset_touch_drag()
        return consume

    def update(self, dt: float, inp) -> None:
        if (
            self._action_pressed(inp, InputAction.BACK)
            or bool(getattr(inp, "cancel", False))
        ) and self.on_close:
            self.on_close()
            return
        if self._action_pressed(inp, InputAction.TOGGLE_FULLSCREEN):
            self._toggle_fullscreen()

        category_delta = (
            -1
            if (
                self._action_pressed(inp, InputAction.MENU_SECTION_PREVIOUS)
                or self._action_pressed(inp, InputAction.NAV_LEFT)
                or self._action_pressed(inp, InputAction.PAGE_UP)
            )
            else 1
            if (
                self._action_pressed(inp, InputAction.MENU_SECTION_NEXT)
                or self._action_pressed(inp, InputAction.NAV_RIGHT)
                or self._action_pressed(inp, InputAction.PAGE_DOWN)
            )
            else 0
        )
        if category_delta:
            self._switch_category(category_delta)
            return

        for category, tab in tuple(self._tab_buttons):
            clicked = bool(
                inp.mouse_clicked
                and tab.rect.collidepoint(inp.mouse_pos)
            )
            tab.update(dt, inp)
            if clicked:
                if category != self._category:
                    self._set_category(category)
                return

        if self._body_rect.collidepoint(inp.mouse_pos):
            self._scroll_y -= int(inp.mouse_wheel) * self._sp(44)

        if self._update_touch_scroll(inp):
            return

        nav_delta = self._navigation_delta(inp)
        if nav_delta:
            self._move_focus(nav_delta)
        confirm_pressed = (
            self._action_pressed(inp, InputAction.CONFIRM_ADVANCE)
            or bool(getattr(inp, "confirm", False))
        )
        if confirm_pressed and 0 <= self._focus < len(self._buttons):
            button = self._buttons[self._focus][0]
            if button.activate():
                return

        self._scroll_y = max(0, min(self._scroll_y, self._max_scroll()))
        self._reposition()
        self._remember_category_state()

        close_clicked = (
            inp.mouse_clicked
            and self.close_btn.rect.collidepoint(inp.mouse_pos)
        )
        self.close_btn.update(dt, inp)
        if close_clicked:
            return

        body = self._body_rect
        pointer_focus = next(
            (
                index
                for index, (button, _cx, _cy) in enumerate(self._buttons)
                if (
                    button.enabled
                    and body.contains(button.rect)
                    and button.rect.collidepoint(inp.mouse_pos)
                    and inp.mouse_clicked
                )
            ),
            None,
        )
        if pointer_focus is not None:
            self._focus = pointer_focus
        self._sync_focus_state()
        self._remember_category_state()

        for button, _cx, _cy in self._buttons:
            # Only clickable while fully inside the scroll body (so a control
            # scrolled under the header/edge can't be hit).
            if body.contains(button.rect):
                button.update(dt, inp)
            else:
                button._hover = False
                button._hover_t = 0.0

        # Sliders: re-sync the knob to config (so a -/+ press moves it), drive
        # drag, and persist once when a drag releases (not every frame).
        dragging = False
        for slider, _cx, _cy, getter in self._sliders:
            if not slider._dragging:
                slider.set_value(getter())
            if body.contains(slider.rect):
                slider.update(dt, inp)
            dragging = dragging or slider._dragging
        if self._was_dragging and not dragging:
            self.ctx.config.save_to_disk()
        self._was_dragging = dragging

    def draw(self, surface: pygame.Surface) -> None:
        theme = self.ctx.theme
        chrome = self.ctx.ui_chrome
        backdrop_rect = surface.get_rect()
        backdrop_painted = (
            chrome is not None
            and chrome.paint(
                surface,
                backdrop_rect,
                ChromeRequest(
                    part="backdrop",
                    screen="settings",
                    variant="overlay",
                    size=backdrop_rect.size,
                    density=self._density,
                ),
            )
        )
        if not backdrop_painted:
            veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
            veil.fill((0, 0, 0, 236))
            surface.blit(veil, (0, 0))
        self._panel.draw(surface)
        surface.blit(self._title_surface, self._title_rect.topleft)
        self.close_btn.draw(surface)
        divider_left = self._panel_rect.x + self._sp(24)
        divider_right = self._panel_rect.right - self._sp(24)
        divider_h = max(1, self._sp(6))
        divider_rect = pygame.Rect(
            divider_left,
            self._header_divider_y - divider_h // 2,
            max(1, divider_right - divider_left + 1),
            divider_h,
        )
        divider_painted = (
            chrome is not None
            and chrome.paint(
                surface,
                divider_rect,
                ChromeRequest(
                    part="divider",
                    screen="settings",
                    variant="header",
                    size=divider_rect.size,
                    density=self._density,
                ),
            )
        )
        if not divider_painted:
            pygame.draw.line(
                surface,
                theme.border_soft,
                (divider_left, self._header_divider_y),
                (divider_right, self._header_divider_y),
            )

        for _category, tab in self._tab_buttons:
            tab.draw(surface)

        self._reposition()
        self._sync_focus_state()
        body = self._body_rect
        prev_clip = surface.get_clip()
        surface.set_clip(body)
        oy = body.y - self._scroll_y
        for index, (text, size, bold, color, cx, cy) in enumerate(
            self._labels,
        ):
            lines = self._label_lines.get(index, (text,))
            line_h = self.ctx.fonts.get(size, bold=bold).get_linesize()
            for line_index, line in enumerate(lines):
                surface.blit(
                    self.ctx.fonts.render(line, size, color, bold=bold),
                    (body.x + cx, oy + cy + line_index * line_h),
                )
        for fn, size, color, cx, cy in self._values:
            surface.blit(self.ctx.fonts.render(fn(), size, color),
                         (body.x + cx, oy + cy))
        for button, _cx, _cy in self._buttons:
            if button.rect.bottom >= body.y and button.rect.y <= body.bottom:
                self._skin_active = self._chrome_is_active()
                button.text_align = (
                    "left"
                    if (
                        self._skin_active
                        and button.role in {"action", "toggle"}
                    )
                    else "center"
                )
                button.draw(surface)
        for s, _cx, _cy, _g in self._sliders:
            if s.rect.bottom >= body.y and s.rect.y <= body.bottom:
                s.draw(surface)
        surface.set_clip(prev_clip)

        # scrollbar
        max_scroll = self._max_scroll()
        if max_scroll > 0:
            track_h = body.height
            knob_h = max(
                self._sp(32),
                int(track_h * body.height / self._content_height),
            )
            knob_y = int(self._scroll_y / max_scroll * (track_h - knob_h))
            scrollbar_w = max(4, self._sp(4))
            x = self._panel_rect.right - self._sp(14)
            pygame.draw.rect(
                surface,
                (*theme.border_soft[:3], 70),
                (x, body.y, scrollbar_w, track_h),
                border_radius=max(2, scrollbar_w // 2),
            )
            pygame.draw.rect(
                surface,
                (*theme.accent[:3], 190),
                (x, body.y + knob_y, scrollbar_w, knob_h),
                border_radius=max(2, scrollbar_w // 2),
            )

        footer_painted = (
            chrome is not None
            and chrome.paint(
                surface,
                self._footer_rect,
                ChromeRequest(
                    part="footer",
                    screen="settings",
                    variant="hint",
                    size=self._footer_rect.size,
                    density=self._density,
                ),
            )
        )
        if not footer_painted:
            pygame.draw.line(
                surface,
                theme.border_soft,
                self._footer_rect.topleft,
                self._footer_rect.topright,
            )
        footer_font = self.ctx.fonts.get(
            self._footer_font_size,
            bold=True,
        )
        footer_text = self._footer_text()
        max_footer_w = (
            self._footer_rect.width
            - max(16, self._sp(36))
        )
        footer_lines = list(
            _wrap_lines(footer_text, footer_font, max_footer_w)
        ) or [""]
        footer_lines = footer_lines[:self._footer_line_capacity]
        if len(footer_lines) == self._footer_line_capacity:
            visible = "".join(footer_lines).replace(" ", "")
            if len(visible) < len(footer_text.replace(" ", "")):
                footer_lines[-1] = footer_lines[-1].rstrip() + "…"
        for index, line in enumerate(footer_lines):
            while footer_font.size(line)[0] > max_footer_w and line:
                line = line[:-1].rstrip()
            footer_lines[index] = line
        if not footer_lines:
            footer_lines = [""]
        total_footer_h = len(footer_lines) * footer_font.get_linesize()
        footer_y = self._footer_rect.centery - total_footer_h // 2
        for index, line in enumerate(footer_lines):
            hint = footer_font.render(
                line,
                True,
                theme.text_mute,
            )
            surface.blit(
                hint,
                (
                    self._footer_rect.x + self._sp(10),
                    footer_y + index * footer_font.get_linesize(),
                ),
            )
    def describe(self) -> dict:
        focused = (
            self._buttons[self._focus][0].label
            if 0 <= self._focus < len(self._buttons)
            else None
        )
        categories = [
            {
                "id": category,
                "label": button.label,
                "selected": category == self._category,
            }
            for category, button in self._tab_buttons
        ]
        return {
            "scene": "SettingsScene",
            "category_id": self._category,
            "categories": categories,
            "focused_id": self._focused_control_id(),
            "visible_controls": list(self._button_ids),
            "text_speed": self.ctx.config.text_speed,
            "text_scale": self.ctx.config.text_scale,
            "reduce_motion": bool(self.ctx.config.reduce_motion),
            "show_sound_captions": self.ctx.config.show_sound_captions is True,
            "visual_accessibility_mode": (
                self.ctx.visual.profile
                if self.ctx.visual is not None
                else "standard"
            ),
            "locale": str(
                getattr(self.ctx.localization, "effective", "") or ""
            ),
            "locale_change_allowed": self.locale_change_allowed,
            "controls_summary": self._controls_summary(),
            "footer": self._footer_text(),
            "content_height": getattr(self, "_content_height", 0),
            "scroll_y": self._scroll_y,
            "focused": focused,
        }
