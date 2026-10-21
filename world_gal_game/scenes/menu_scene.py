"""Category-first in-game system menu.

The menu exposes many engine capabilities, but never presents all of them as
one long scroll.  Records, extras, and system actions are separate pages behind
three persistent tabs.  The layout is measured from the live font metrics so
the same hierarchy remains intact on both 720p and 1080p canvases.

Semantic keyboard/controller actions (factory defaults shown):

* Up / Down selects an action on the active page.
* Left / Right or Q / E changes page.
* Enter / Space / Z activates the visibly focused action.
* Escape / X closes the overlay.

The exploration scene's direct actions remain available through the player's
live binding profile; this scene only reorganises their discoverable entries.
"""
from __future__ import annotations

from typing import Callable

import pygame

from .base import Scene, SceneContext
from ..ui.input_bindings import BindingProfile, InputAction
from ..ui.system_chrome import ChromeRequest
from ..ui.widgets import Button, Panel


class MenuScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        self._scroll_y = 0
        self._category_index = 0
        self._focus = -1
        self._focus_ring_rect: pygame.Rect | None = None
        self.on_close: Callable[[], None] | None = None
        self.on_map = None
        self.on_affection = None
        self.on_log = None
        self.on_achievements = None
        self.on_inventory = None
        self.on_cg_gallery = None
        self.on_music_room = None
        self.on_endings = None
        self.on_scene_replay = None
        self.on_flowchart = None
        self.on_relationships = None
        self.on_character_profiles = None
        self.on_quest_log = None
        self.on_clues = None
        self.on_credits = None
        self.on_save = None
        self.on_load = None
        self.on_settings = None
        self.on_quit_to_title = None
        self.on_quit_app = None
        self.get_map_availability = None

    def enter(self, *, on_close=None, on_map=None, on_affection=None,
              on_log=None, on_achievements=None, on_inventory=None,
              on_cg_gallery=None, on_music_room=None, on_endings=None,
              on_scene_replay=None, on_flowchart=None, on_relationships=None,
              on_character_profiles=None, on_quest_log=None,
              on_clues=None, on_credits=None, on_save=None, on_load=None,
              on_settings=None,
              on_quit_to_title=None, on_quit_app=None,
              get_map_availability=None, **_) -> None:
        self.on_close = on_close
        self.on_map = on_map
        self.on_affection = on_affection
        self.on_log = on_log
        self.on_achievements = on_achievements
        self.on_inventory = on_inventory
        self.on_cg_gallery = on_cg_gallery
        self.on_music_room = on_music_room
        self.on_endings = on_endings
        self.on_scene_replay = on_scene_replay
        self.on_flowchart = on_flowchart
        self.on_relationships = on_relationships
        self.on_character_profiles = on_character_profiles
        self.on_quest_log = on_quest_log
        self.on_clues = on_clues
        self.on_credits = on_credits
        self.on_save = on_save
        self.on_load = on_load
        self.on_settings = on_settings
        self.on_quit_to_title = on_quit_to_title
        self.on_quit_app = on_quit_app
        self.get_map_availability = get_map_availability
        self._build()

    def _category_specs(self) -> list[tuple[str, str, list[tuple]]]:
        """Return generic menu pages while preserving every existing callback."""
        bindings = BindingProfile(
            getattr(self.ctx.config, "input_bindings", {}),
        )

        def bound_label(key: str, source: str, action: str) -> str:
            return self.ctx.t(
                "menu_item_with_binding",
                "{label} ({binding})",
                label=self.ctx.t(key, source),
                binding=bindings.primary_display(action),
            )

        clue_unread = self.ctx.state.clues.unread_count()
        clue_label = self.ctx.t(
            (
                "menu_item_with_binding_unread"
                if clue_unread > 0
                else "menu_item_with_binding"
            ),
            (
                "{label} ({binding}) · 新 {count}"
                if clue_unread > 0
                else "{label} ({binding})"
            ),
            label=self.ctx.t("clue_journal", "線索筆記"),
            binding=bindings.primary_display(InputAction.OPEN_CLUES),
            count=clue_unread,
        )

        return [
            ("records", self.ctx.t("menu_records", "紀錄"), [
                (
                    bound_label("map", "地圖", InputAction.OPEN_MAP),
                    self.on_map,
                    "primary",
                ),
                (
                    bound_label(
                        "affection",
                        "好感",
                        InputAction.OPEN_AFFECTION,
                    ),
                    self.on_affection,
                    "primary",
                ),
                (
                    self.ctx.t("characters", "角色檔案"),
                    self.on_character_profiles,
                    "primary",
                ),
                (clue_label, self.on_clues, "primary"),
                (
                    bound_label(
                        "log",
                        "事件",
                        InputAction.OPEN_EVENT_LOG,
                    ),
                    self.on_log,
                    "primary",
                ),
                (
                    bound_label(
                        "achievements",
                        "成就",
                        InputAction.OPEN_ACHIEVEMENTS,
                    ),
                    self.on_achievements,
                    "primary",
                ),
                (
                    bound_label(
                        "inventory",
                        "物品",
                        InputAction.OPEN_INVENTORY,
                    ),
                    self.on_inventory,
                    "primary",
                ),
                (
                    self.ctx.t("quest_log", "任務記錄"),
                    self.on_quest_log,
                    "primary",
                ),
            ]),
            ("extras", self.ctx.t("menu_extras", "鑑賞"), [
                (self.ctx.t("flowchart", "流程圖"), self.on_flowchart, "primary"),
                (
                    self.ctx.t("relationships", "關係狀態"),
                    self.on_relationships,
                    "primary",
                ),
                (
                    self.ctx.t("cg_gallery", "CG鑑賞"),
                    self.on_cg_gallery,
                    "primary",
                ),
                (
                    self.ctx.t("music_room", "音樂室"),
                    self.on_music_room,
                    "primary",
                ),
                (self.ctx.t("endings", "結局"), self.on_endings, "primary"),
                (
                    self.ctx.t("scene_replay", "場景重溫"),
                    self.on_scene_replay,
                    "primary",
                ),
                (self.ctx.t("credits", "鳴謝"), self.on_credits, "primary"),
            ]),
            ("system", self.ctx.t("menu_system", "系統"), [
                (self.ctx.t("save", "存檔"), self.on_save, "ghost"),
                (self.ctx.t("load_game", "載入存檔"), self.on_load, "ghost"),
                (
                    self.ctx.t("settings", "設定"),
                    self.on_settings,
                    "ghost",
                ),
                (
                    self.ctx.t("return_to_title", "回標題畫面"),
                    self.on_quit_to_title,
                    "ghost",
                ),
                (self.ctx.t("quit", "離開遊戲"), self.on_quit_app, "danger"),
            ]),
        ]

    def _build(self) -> None:
        theme = self.ctx.theme
        sw, sh = self.ctx.screen_size
        # FontRegistry scales requested sizes with the canvas height.  Geometry
        # must follow that exact scale instead of scaling only the glyphs.
        density = max(1.0, float(getattr(self.ctx.fonts, "scale", 1.0)))
        self._density = density
        chrome = self.ctx.ui_chrome
        self._skin_active = bool(
            chrome is not None and getattr(chrome, "active", False)
        )

        def scale(value: int) -> int:
            return max(1, int(round(value * density)))

        minimum_target = max(44, scale(44))

        self._title_font_size = self.ctx.config.font_size_header
        self._tab_font_size = 16
        self._item_font_size = 17
        self._close_font_size = 15
        self._footer_font_size = 12
        title_line_h = self.ctx.fonts.get(
            self._title_font_size, bold=True,
        ).get_linesize()
        tab_line_h = self.ctx.fonts.get(
            self._tab_font_size, bold=True,
        ).get_linesize()
        item_line_h = self.ctx.fonts.get(
            self._item_font_size, bold=True,
        ).get_linesize()
        close_line_h = self.ctx.fonts.get(
            self._close_font_size, bold=True,
        ).get_linesize()
        footer_line_h = self.ctx.fonts.get(
            self._footer_font_size, bold=True,
        ).get_linesize()

        edge_x = max(16, scale(36))
        edge_y = max(16, scale(28))
        pw = min(scale(760), max(1, sw - edge_x * 2))
        max_panel_h = max(1, sh - edge_y * 2)
        inner_pad = max(16, scale(28))
        header_pad = max(12, scale(16))
        close_h = max(minimum_target, close_line_h + scale(14))
        self._header_h = max(
            title_line_h + header_pad * 2,
            close_h + header_pad * 2,
        )

        self._categories = self._category_specs()
        self._category_index %= len(self._categories)
        max_items = max(len(items) for _cid, _label, items in self._categories)

        tab_top = scale(10)
        tab_h = max(minimum_target, tab_line_h + scale(16))
        body_top = scale(12)
        item_h = max(minimum_target, item_line_h + scale(16), scale(48))
        item_gap = max(8, scale(8))
        footer_gap = scale(8)
        footer_pad = scale(8)
        footer_h = max(scale(34), footer_line_h + scale(12))
        columns = 1
        rows = max_items
        preferred_h = (
            self._header_h + tab_top + tab_h + body_top
            + rows * item_h + max(0, rows - 1) * item_gap
            + footer_gap + footer_h + footer_pad
        )

        # Very short developer/mobile canvases cannot fit eight full-width rows.
        # Keep the category model but add only as many compact columns as the
        # measured height requires.  Items fill each column top-to-bottom, so
        # Up/Down still follows the player's visual reading order.
        if preferred_h > max_panel_h:
            header_pad = max(10, scale(10))
            close_h = max(44, close_line_h + 12)
            self._header_h = max(
                title_line_h + header_pad * 2,
                close_h + header_pad * 2,
            )
            tab_top = 8
            tab_h = max(44, tab_line_h + 12)
            body_top = 8
            item_h = max(44, item_line_h + 12)
            item_gap = 6
            footer_gap = 6
            footer_pad = 6
            footer_h = max(30, footer_line_h + 8)
            available_w = max(1, pw - inner_pad * 2)
            max_columns = min(
                4,
                max_items,
                max(
                    2,
                    (available_w + item_gap)
                    // (minimum_target + item_gap),
                ),
            )
            for candidate in range(2, max_columns + 1):
                candidate_rows = (
                    max_items + candidate - 1
                ) // candidate
                candidate_h = (
                    self._header_h + tab_top + tab_h + body_top
                    + candidate_rows * item_h
                    + max(0, candidate_rows - 1) * item_gap
                    + footer_gap + footer_h + footer_pad
                )
                columns = candidate
                rows = candidate_rows
                preferred_h = candidate_h
                if candidate_h <= max_panel_h:
                    break

        ph = min(preferred_h, max_panel_h)
        self._columns = columns
        self._item_height = item_h
        self._item_gap = item_gap
        self._panel_rect = pygame.Rect(0, 0, pw, ph)
        self._panel_rect.center = (sw // 2, sh // 2)
        self._panel = Panel(self._panel_rect, theme,
                            fill=(*theme.bg_overlay[:3], 240),
                            border=theme.border_strong,
                            radius=theme.radius_l, border_width=2,
                            chrome=chrome, screen="menu", role="main",
                            density=density)

        title = self.ctx.fonts.render(
            self.ctx.t("menu", "選單"),
            self._title_font_size,
            theme.accent,
            bold=True,
        )
        title_x = self._panel_rect.x + inner_pad
        title_y = self._panel_rect.y + (self._header_h - title.get_height()) // 2
        self._title_surface = title
        self._title_rect = title.get_rect(topleft=(title_x, title_y))

        bindings = BindingProfile(
            getattr(self.ctx.config, "input_bindings", {}),
        )
        close_label = (
            f"{self.ctx.localization.t('close', '關閉')} "
            f"({bindings.primary_display(InputAction.BACK)})"
        )
        close_text_w = self.ctx.fonts.get(
            self._close_font_size, bold=True,
        ).size(close_label)[0]
        close_w = max(scale(120), close_text_w + scale(24), minimum_target)
        close_rect = pygame.Rect(
            self._panel_rect.right - inner_pad - close_w,
            self._panel_rect.y + (self._header_h - close_h) // 2,
            close_w,
            close_h,
        )
        self._body_rect = pygame.Rect(
            self._panel_rect.x + inner_pad,
            0,
            self._panel_rect.width - inner_pad * 2,
            0,
        )
        self.close_btn = Button(
            close_rect,
            close_label,
            fonts=self.ctx.fonts, theme=theme,
            font_size=self._close_font_size, style="ghost",
            on_click=(lambda: self.on_close() if self.on_close else None),
            chrome=chrome,
            screen="menu",
            role="close",
            motion=self.ctx.motion,
        )

        self._header_divider_y = self._panel_rect.y + self._header_h - 1
        tab_y = self._header_divider_y + tab_top
        tab_gap = max(8, scale(8))
        tab_w = (
            self._body_rect.width - tab_gap * (len(self._categories) - 1)
        ) // len(self._categories)
        self._tab_buttons: list[Button] = []
        x = self._body_rect.x
        for index, (_cid, label, _items) in enumerate(self._categories):
            width = (
                self._body_rect.right - x
                if index == len(self._categories) - 1
                else tab_w
            )
            self._tab_buttons.append(Button(
                pygame.Rect(x, tab_y, width, tab_h),
                label,
                fonts=self.ctx.fonts,
                theme=theme,
                font_size=self._tab_font_size,
                style="primary" if index == self._category_index else "ghost",
                on_click=lambda index=index: self._set_category(index),
                chrome=chrome,
                screen="menu",
                role="tab",
                selected=index == self._category_index,
                motion=self.ctx.motion,
            ))
            x += width + tab_gap

        body_y = tab_y + tab_h + body_top
        self._footer_rect = pygame.Rect(
            self._body_rect.x,
            self._panel_rect.bottom - footer_pad - footer_h,
            self._body_rect.width,
            footer_h,
        )
        self._body_rect.y = body_y
        self._body_rect.height = max(
            1, self._footer_rect.top - footer_gap - body_y,
        )
        self._build_category_buttons()

    def _build_category_buttons(self) -> None:
        """Lay out only the active category; no hidden long-scroll controls."""
        category_id, _label, items = self._categories[self._category_index]
        columns = self._columns
        gap = self._item_gap
        col_w = (
            self._body_rect.width - gap * (columns - 1)
        ) // columns
        self._buttons: list[Button] = []
        rows = (len(items) + columns - 1) // columns
        for index, (label, callback, style) in enumerate(items):
            enabled = callback is not None
            tooltip = None
            if category_id == "records" and index == 0:
                map_available, map_reason = self._map_availability()
                enabled = enabled and map_available
                tooltip = map_reason if not map_available else None
            col = index // rows
            row = index % rows
            x = self._body_rect.x + col * (col_w + gap)
            width = (
                self._body_rect.right - x
                if col == columns - 1
                else col_w
            )
            y = self._body_rect.y + row * (self._item_height + gap)
            self._buttons.append(Button(
                pygame.Rect(x, y, width, self._item_height),
                label,
                fonts=self.ctx.fonts,
                theme=self.ctx.theme,
                font_size=self._item_font_size,
                style=style,
                on_click=callback,
                enabled=enabled,
                tooltip=tooltip,
                chrome=self.ctx.ui_chrome,
                screen="menu",
                role="action",
                selected=False,
                text_align="left" if self._skin_active else "center",
                motion=self.ctx.motion,
            ))
        self._focus = next(
            (
                i for i, button in enumerate(self._buttons)
                if button.enabled or button.tooltip
            ),
            -1,
        )
        self._content_height = (
            rows * self._item_height + max(0, rows - 1) * gap
        )
        self._scroll_y = 0
        self._focus_ring_rect = None

    def _set_category(self, index: int) -> None:
        if not self._categories:
            return
        self._category_index = index % len(self._categories)
        for i, tab in enumerate(self._tab_buttons):
            tab.style = "primary" if i == self._category_index else "ghost"
            tab.selected = i == self._category_index
        self._build_category_buttons()

    def _chrome_is_active(self) -> bool:
        chrome = self.ctx.ui_chrome
        return bool(
            chrome is not None and getattr(chrome, "active", False)
        )

    def _move_focus(self, delta: int) -> None:
        focusable = [
            i for i, button in enumerate(self._buttons)
            if button.enabled or button.tooltip
        ]
        if not focusable or not delta:
            return
        if self._focus not in focusable:
            self._focus = focusable[0] if delta > 0 else focusable[-1]
            return
        position = focusable.index(self._focus)
        self._focus = focusable[
            (position + delta) % len(focusable)
        ]

    def _map_availability(self) -> tuple[bool, str | None]:
        if self.get_map_availability is None:
            return True, None
        try:
            return self.get_map_availability()
        except Exception:
            # Optional presentation guidance cannot remove the stock menu.
            return True, None

    def _notify_blocked(self, button: Button) -> None:
        if not button.tooltip:
            return
        queue = self.ctx.state.meta.setdefault("__pending_toasts__", [])
        queue.append(("notice", button.label, button.tooltip))

    def _active_explanation(self) -> str | None:
        """Return the locked-action reason selected by pointer or keyboard."""
        hovered = next(
            (
                button for button in self._buttons
                if button._pointer_over and button.tooltip
            ),
            None,
        )
        if hovered is not None:
            return hovered.tooltip
        if 0 <= self._focus < len(self._buttons):
            return self._buttons[self._focus].tooltip
        return None

    @staticmethod
    def _keyboard_deltas(inp) -> tuple[int, int]:
        """Return ``(category_delta, item_delta)`` without double-counting nav."""
        category_delta = (
            -1
            if (
                inp.pressed(InputAction.MENU_SECTION_PREVIOUS)
                or inp.pressed(InputAction.NAV_LEFT)
            )
            else 1
            if (
                inp.pressed(InputAction.MENU_SECTION_NEXT)
                or inp.pressed(InputAction.NAV_RIGHT)
            )
            else 0
        )
        item_delta = (
            -1
            if inp.pressed(InputAction.NAV_UP)
            else 1
            if inp.pressed(InputAction.NAV_DOWN)
            else 0
        )
        return category_delta, item_delta

    def update(self, dt: float, inp) -> None:
        if inp.cancel and self.on_close:
            self.on_close()
            return

        category_delta, item_delta = self._keyboard_deltas(inp)
        if category_delta:
            self._set_category(self._category_index + category_delta)
        if item_delta:
            self._move_focus(item_delta)
        if inp.confirm and 0 <= self._focus < len(self._buttons):
            button = self._buttons[self._focus]
            if not button.enabled and button.tooltip:
                self._notify_blocked(button)
                return
            if button.activate():
                return

        self.close_btn.update(dt, inp)
        for tab in self._tab_buttons:
            tab.update(dt, inp)
        for button in self._buttons:
            button.update(dt, inp)
            if (
                inp.mouse_clicked
                and button._pointer_over
                and not button.enabled
                and button.tooltip
            ):
                self._notify_blocked(button)

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
                    screen="menu",
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
        divider_left = self._panel_rect.x + 18
        divider_right = self._panel_rect.right - 18
        divider_h = max(1, int(round(6 * self._density)))
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
                    screen="menu",
                    variant="header",
                    size=divider_rect.size,
                    density=self._density,
                ),
            )
        )
        if not divider_painted:
            pygame.draw.line(
                surface, theme.border_soft,
                (divider_left, self._header_divider_y),
                (divider_right, self._header_divider_y),
            )

        for index, tab in enumerate(self._tab_buttons):
            tab.draw(surface)
            if index == self._category_index:
                underline_h = max(2, int(round(2 * self._density)))
                pygame.draw.rect(
                    surface,
                    theme.accent_warm,
                    (
                        tab.rect.x + underline_h,
                        tab.rect.bottom - underline_h,
                        max(1, tab.rect.width - underline_h * 2),
                        underline_h,
                    ),
                    border_radius=underline_h,
                )

        for index, button in enumerate(self._buttons):
            # A renderer can fault-isolate itself during any preceding paint.
            # Re-read ``active`` so the remainder of this frame, and every
            # later frame, converges immediately to stock alignment/focus.
            self._skin_active = self._chrome_is_active()
            button.text_align = (
                "left" if self._skin_active else "center"
            )
            button.focused = bool(
                self._skin_active
                and index == self._focus
                and (button.enabled or button.tooltip)
            )
            button.draw(surface)

        self._focus_ring_rect = None
        self._skin_active = self._chrome_is_active()
        if 0 <= self._focus < len(self._buttons):
            focused = self._buttons[self._focus]
            if focused.enabled or focused.tooltip:
                if self._skin_active and focused.enabled:
                    # Button.draw already layered optional plugin focus chrome
                    # and the engine's warm-gold accessibility ring.
                    ring = focused.rect.copy()
                else:
                    inflate = max(4, int(round(6 * self._density)))
                    ring = focused.rect.inflate(inflate, inflate)
                    ring.clamp_ip(self._panel_rect)
                    width = max(2, int(round(2 * self._density)))
                    pygame.draw.rect(
                        surface,
                        theme.accent_warm,
                        ring,
                        width=width,
                        border_radius=self.ctx.theme.radius_m + width,
                    )
                self._focus_ring_rect = ring

        footer_painted = (
            chrome is not None
            and chrome.paint(
                surface,
                self._footer_rect,
                ChromeRequest(
                    part="footer",
                    screen="menu",
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
        bindings = BindingProfile(
            getattr(self.ctx.config, "input_bindings", {}),
        )
        footer_text = self.ctx.t(
            "menu_footer",
            (
                "{left}／{right} 分頁　{up}／{down} 選擇　"
                "{confirm} 確認　{back} 返回"
            ),
            left=bindings.primary_display(InputAction.NAV_LEFT),
            right=bindings.primary_display(InputAction.NAV_RIGHT),
            up=bindings.primary_display(InputAction.NAV_UP),
            down=bindings.primary_display(InputAction.NAV_DOWN),
            confirm=bindings.primary_display(InputAction.CONFIRM_ADVANCE),
            back=bindings.primary_display(InputAction.BACK),
        )
        explanation = self._active_explanation()
        if explanation:
            footer_text = explanation
        while (
            footer_text
            and footer_font.size(footer_text)[0]
            > self._footer_rect.width - max(12, int(round(24 * self._density)))
        ):
            footer_text = footer_text[:-1].rstrip()
        hint = footer_font.render(
            footer_text,
            True,
            theme.text if explanation else theme.text_mute,
        )
        surface.blit(
            hint,
            (
                self._footer_rect.x + max(6, int(round(10 * self._density))),
                self._footer_rect.centery - hint.get_height() // 2,
            ),
        )

    def describe(self) -> dict:
        category_id, category_label, _items = self._categories[
            self._category_index
        ]
        focused = (
            self._buttons[self._focus].label
            if 0 <= self._focus < len(self._buttons)
            else None
        )
        return {
            "scene": "MenuScene",
            "active_category": category_id,
            "active_category_label": category_label,
            "categories": [cid for cid, _label, _items in self._categories],
            "visible_items": [button.label for button in self._buttons],
            "visible_item_states": [
                {
                    "label": button.label,
                    "enabled": button.enabled,
                    "reason": button.tooltip,
                }
                for button in self._buttons
            ],
            "focused": focused,
            "content_height": getattr(self, "_content_height", 0),
            "scroll_y": 0,
        }
