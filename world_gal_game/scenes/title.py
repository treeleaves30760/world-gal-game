"""Pack-branded title screen with a legacy-compatible default layout."""
from __future__ import annotations

from pathlib import Path

import pygame

from .base import Scene, SceneContext
from ..core.media_authority import MediaAuthority
from ..core.music_room import canonical_music_path
from ..core.title_screen_spec import TitleScreenSpec
from ..ui.input_bindings import InputAction
from ..ui.widgets import TextInput, MenuList
from ..ui.widgets.menu_list import MenuItem


class _TitleMenuList(MenuList):
    """Title menu with a bounded, focus-following vertical viewport.

    Most title menus fit without scrolling. The extras menu can contain seven
    entries, however, and shrinking every row on a 360p/450p canvas makes the
    interaction targets unusable. This variant keeps the authored item order
    and the familiar Up/Down/Confirm contract while drawing only the rows that
    fit at a minimum 44-pixel height.
    """

    def __init__(self, *args, visible_rows: int, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.visible_rows = max(1, min(len(self.items), int(visible_rows)))
        self.scroll_offset = 0

    def _ensure_selected_visible(self) -> None:
        if self.selected < self.scroll_offset:
            self.scroll_offset = self.selected
        elif self.selected >= self.scroll_offset + self.visible_rows:
            self.scroll_offset = self.selected - self.visible_rows + 1
        self.scroll_offset = max(
            0,
            min(
                self.scroll_offset,
                max(0, len(self.items) - self.visible_rows),
            ),
        )

    def _move(self, delta: int) -> None:
        if not self.items:
            return
        candidate = self.selected
        for _ in self.items:
            candidate = (candidate + delta) % len(self.items)
            if self.items[candidate].enabled:
                self.selected = candidate
                self._ensure_selected_visible()
                return

    def update(self, dt: float, inp) -> None:
        if not self.items:
            return
        pointer_active = bool(
            inp.mouse_clicked
            or getattr(inp, "touch_active", False)
            or any(
                event.type == pygame.MOUSEMOTION
                for event in getattr(inp, "events", ())
            )
        )
        if pointer_active and self.rect.collidepoint(inp.mouse_pos):
            local_y = inp.mouse_pos[1] - self.rect.y
            visible_index = local_y // self.row_h
            index = self.scroll_offset + visible_index
            if (
                0 <= visible_index < self.visible_rows
                and 0 <= index < len(self.items)
            ):
                self.selected = index
                if inp.mouse_clicked and self.items[index].enabled:
                    self.items[index].on_select()
                    return
        if not self.keyboard_nav:
            return
        if inp.pressed(InputAction.NAV_UP):
            self._move(-1)
        elif inp.pressed(InputAction.NAV_DOWN):
            self._move(1)
        elif inp.pressed(InputAction.PAGE_UP):
            for _ in range(self.visible_rows):
                self._move(-1)
        elif inp.pressed(InputAction.PAGE_DOWN):
            for _ in range(self.visible_rows):
                self._move(1)
        elif inp.confirm and self.items[self.selected].enabled:
            self.items[self.selected].on_select()

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        previous_clip = surface.get_clip()
        surface.set_clip(previous_clip.clip(self.rect))
        first = self.scroll_offset
        last = min(len(self.items), first + self.visible_rows)
        for visible_index, item_index in enumerate(range(first, last)):
            item = self.items[item_index]
            row = pygame.Rect(
                self.rect.x,
                self.rect.y + visible_index * self.row_h,
                self.rect.width,
                max(1, self.row_h - 6),
            )
            if item_index == self.selected:
                highlight = pygame.Surface(row.size, pygame.SRCALPHA)
                pygame.draw.rect(
                    highlight,
                    (*self.theme.accent[:3], 60),
                    highlight.get_rect(),
                    border_radius=self.theme.radius_m,
                )
                pygame.draw.rect(
                    highlight,
                    self.theme.border_strong,
                    highlight.get_rect(),
                    width=1,
                    border_radius=self.theme.radius_m,
                )
                surface.blit(highlight, row.topleft)
            color = self.theme.text if item.enabled else self.theme.text_dim
            label = self.fonts.render(
                item.label,
                self.font_size,
                color,
                bold=(item_index == self.selected),
            )
            surface.blit(
                label,
                (
                    row.x + 24,
                    row.y + (row.height - label.get_height()) // 2,
                ),
            )
        surface.set_clip(previous_clip)

        if self.visible_rows < len(self.items):
            track = pygame.Rect(
                self.rect.right - 4,
                self.rect.y + 4,
                2,
                max(1, self.rect.height - 8),
            )
            pygame.draw.rect(surface, self.theme.border_soft, track)
            thumb_h = max(
                12,
                round(track.height * self.visible_rows / len(self.items)),
            )
            travel = max(0, track.height - thumb_h)
            maximum_offset = max(1, len(self.items) - self.visible_rows)
            thumb_y = track.y + round(
                travel * self.scroll_offset / maximum_offset,
            )
            pygame.draw.rect(
                surface,
                self.theme.accent,
                pygame.Rect(track.x, thumb_y, track.width, thumb_h),
            )


class TitleScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = False
        self.name_input: TextInput | None = None
        self.menu: MenuList | None = None
        self.bg_path: str | None = None
        self.title_text: str = ctx.config.title
        self.subtitle_text: str = ctx.config.subtitle
        self.version_text: str | None = None
        self.presentation: TitleScreenSpec | None = None
        self.on_scene_replay = None
        self.on_credits = None
        self.on_settings = None
        self._extras_mode = False
        self._name_prompt_mode = False
        self._new_game_committed = False
        self._wordmark_source = "text"
        self._logo_image: pygame.Surface | None = None
        self._logo_image_rect: pygame.Rect | None = None
        self._footer_display_text = ""
        self._footer_render_text = ""
        self._wordmark_title_text = self.title_text
        self._wordmark_tagline_text = self.subtitle_text
        self._chrome_density = 1.0
        self._title_bgm: str | None = None

    def enter(self, *, bg: str | None = None, title: str | None = None,
              subtitle: str | None = None, version: str | None = None,
              bgm: str | None = None, on_continue=None, on_new_game=None,
              on_load=None, on_quit=None, on_cg_gallery=None,
              on_music_room=None, on_endings=None, on_flowchart=None,
              on_scene_replay=None, on_credits=None, on_settings=None,
              presentation: TitleScreenSpec | None = None, **_) -> None:
        self.bg_path = bg
        self.title_text = title or self.title_text
        self.subtitle_text = subtitle or self.subtitle_text
        self.version_text = version
        self.presentation = presentation
        self._title_bgm = bgm
        # Stop any in-game ambient bed so a scene's room-tone/hum doesn't bleed
        # under the title screen.
        self.ctx.assets.stop_ambient(fade_ms=600)
        # Title BGM: play the pack's title track if one is set. A missing file
        # or an uninitialised mixer (headless) degrades to silence inside
        # play_music, so this is always safe to call.
        if bgm:
            self.ctx.assets.play_music(bgm, volume=self.ctx.config.bgm_volume)
            # Extras progress follows what the player actually encountered,
            # including a title cue outside the story graph.  Confirm that the
            # audio backend accepted ownership, and never persist a path beyond
            # the current pack's immutable media authority.
            current_music = getattr(self.ctx.assets, "_current_music", None)
            canonical_bgm = canonical_music_path(bgm)
            if (
                canonical_bgm
                and isinstance(current_music, str)
                and canonical_music_path(current_music) == canonical_bgm
            ):
                authority = self.ctx.state.meta.get(
                    "__pack_media_authority__",
                )
                if (
                    not isinstance(authority, MediaAuthority)
                    or canonical_bgm in authority.music_ids
                ):
                    self.ctx.state.music_room.unlock(canonical_bgm)
        self.on_continue = on_continue
        self.on_new_game = on_new_game
        self.on_load = on_load
        self.on_quit = on_quit
        self.on_cg_gallery = on_cg_gallery
        self.on_music_room = on_music_room
        self.on_endings = on_endings
        self.on_flowchart = on_flowchart
        self.on_scene_replay = on_scene_replay
        self.on_credits = on_credits
        self.on_settings = on_settings
        # The panel is laid out after the menu items are known: the main menu
        # and extras submenu can have different row counts.  Keeping the
        # existing input object across submenu swaps preserves the typed name.
        self.name_input = None
        self._initial_name = (
            self.ctx.state.player.name
            if self.ctx.state.player.name != "玩家" else ""
        )
        self._extras_mode = False
        self._name_prompt_mode = False
        self._new_game_committed = False
        self._prepare_logo_image()
        self._build_menu()

    def _start_new(self) -> None:
        if (
            self.presentation is not None
            and self.presentation.name_entry == "on_new_game"
        ):
            if self._name_prompt_mode:
                self._confirm_new_game()
                return
            self._extras_mode = False
            self._name_prompt_mode = True
            self._new_game_committed = False
            self.name_input = None
            self._build_menu()
            if self.name_input is not None:
                self.name_input.focus()
            return
        self._confirm_new_game()

    def refresh_localized_copy(
        self,
        *,
        title: str,
        subtitle: str,
        presentation: TitleScreenSpec | None,
    ) -> None:
        """Rebuild title-owned copy without restarting music or game state."""
        self.title_text = title
        self.subtitle_text = subtitle
        self.presentation = presentation
        self.name_input = None
        self._initial_name = (
            self.ctx.state.player.name
            if self.ctx.state.player.name != "玩家" else ""
        )
        self._extras_mode = False
        self._name_prompt_mode = False
        self._new_game_committed = False
        self._prepare_logo_image()
        self._build_menu()

    def _confirm_new_game(self) -> None:
        """Commit the entered name exactly once, then start a fresh game."""
        if self._new_game_committed:
            return
        entered = self.name_input.text if self.name_input is not None else ""
        default_name = self.ctx.t("default_player_name", "玩家")
        name = (entered or default_name).strip()
        self._new_game_committed = True
        self.ctx.state.player.name = name or default_name
        if self.name_input is not None:
            self.name_input.unfocus()
        self._name_prompt_mode = False
        if self.on_new_game:
            self.on_new_game()
        elif (
            self.presentation is not None
            and self.presentation.name_entry == "on_new_game"
        ):
            # A callback-less scene is still useful in focused rendering and
            # authoring tools. Return it to a coherent main-menu state.
            self.name_input = None
            self._build_menu()

    def _cancel_name_prompt(self) -> None:
        """Discard an uncommitted name and return to the main title menu."""
        if not self._name_prompt_mode:
            return
        if self.name_input is not None:
            self.name_input.unfocus()
        self._name_prompt_mode = False
        self._new_game_committed = False
        self.name_input = None
        self._build_menu()

    def _prepare_logo_image(self) -> None:
        """Load an optional pack logo, falling back to the text wordmark.

        ``AssetManager.image`` intentionally returns a visible placeholder
        for missing or corrupt files. A title logo is different: displaying a
        placeholder as a brand mark is misleading, so probe and validate the
        source before asking the manager for its cached surface.
        """
        self._logo_image = None
        self._logo_image_rect = None
        self._wordmark_source = "text"
        spec = self.presentation
        if spec is None or not spec.logo_image:
            return

        path = spec.logo_image
        has_image = getattr(self.ctx.assets, "has_image", None)
        if callable(has_image):
            try:
                if not has_image(path):
                    return
            except (OSError, pygame.error):
                return

        # AssetManager's private resolver is the only generic way to tell a
        # corrupt file from its procedural placeholder without changing the
        # engine-wide asset API. This is a validation read only; drawing still
        # goes through AssetManager so its normal cache and pack roots apply.
        resolve = getattr(self.ctx.assets, "_resolve", None)
        if callable(resolve):
            try:
                resolved = resolve(path)
                if resolved is None or not Path(resolved).is_file():
                    return
                probe = pygame.image.load(str(resolved))
                if probe.get_width() <= 0 or probe.get_height() <= 0:
                    return
            except (OSError, pygame.error, TypeError, ValueError):
                return

        image = getattr(self.ctx.assets, "image", None)
        if not callable(image):
            return
        try:
            logo = image(path, fallback_size=(640, 240))
        except (OSError, pygame.error, TypeError, ValueError):
            return
        if not isinstance(logo, pygame.Surface):
            return
        if logo.get_width() <= 0 or logo.get_height() <= 0:
            return
        self._logo_image = logo
        self._wordmark_source = "image"

    def exit(self) -> None:
        if self.name_input is not None:
            self.name_input.unfocus()
        # Title music belongs to this scene.  A pack whose opening line is
        # intentionally silent must not inherit the title track until some
        # later authored BGM change happens to replace it.  A dialogue may
        # also have been pushed directly above the title by authoring/tests;
        # once story state declares a BGM, title no longer owns the active
        # stream and must not cut that track while the stale base is cleared.
        story_bgm = self.ctx.state.meta.get("current_bgm")
        if story_bgm not in (None, "", "none", "silence", "-"):
            return
        stop_music = getattr(self.ctx.assets, "stop_music", None)
        if callable(stop_music):
            try:
                stop_music(fade_ms=350)
            except TypeError:
                # Lightweight/custom asset backends may expose the historical
                # no-argument shape; title exit must remain compatible.
                stop_music()

    def _build_menu(
        self,
        canvas_size: tuple[int, int] | None = None,
    ) -> None:
        """(Re)build the title menu for the current mode (main vs extras)."""
        items = []
        if self._name_prompt_mode:
            items.extend([
                MenuItem(
                    self.ctx.t("confirm_enrollment", "確認入學"),
                    self._confirm_new_game,
                ),
                MenuItem(self.ctx.t("cancel", "取消"), self._cancel_name_prompt),
            ])
        elif self._extras_mode:
            if self.on_cg_gallery:
                items.append(MenuItem(self.ctx.t("cg_gallery", "CG鑑賞"),
                                      self.on_cg_gallery))
            if self.on_music_room:
                items.append(MenuItem(self.ctx.t("music_room", "音樂室"),
                                      self.on_music_room))
            if self.on_endings:
                items.append(MenuItem(self.ctx.t("endings", "結局"),
                                      self.on_endings))
            if self.on_scene_replay:
                items.append(MenuItem(
                    self.ctx.t("scene_replay", "場景重溫"),
                    self.on_scene_replay,
                ))
            if self.on_flowchart:
                items.append(MenuItem(
                    self.ctx.t("flowchart_extras", "篇章 / 流程圖"),
                    self.on_flowchart,
                ))
            if self.on_credits:
                items.append(MenuItem(self.ctx.t("credits", "鳴謝"),
                                      self.on_credits))
            items.append(MenuItem(
                self.ctx.t("back_arrow", "← 返回"),
                self._close_extras,
            ))
        else:
            if self.on_continue:
                items.append(MenuItem(
                    self.ctx.t("continue_game", "繼續遊戲"),
                    lambda: self.on_continue(),
                ))
            items.append(MenuItem(self.ctx.t("new_game", "開始新遊戲"), self._start_new))
            items.append(MenuItem(self.ctx.t("load_game", "載入存檔"),
                                  lambda: self.on_load and self.on_load()))
            if any((self.on_cg_gallery, self.on_music_room, self.on_endings,
                    self.on_flowchart, self.on_scene_replay,
                    self.on_credits)):
                items.append(MenuItem(self.ctx.t("extras", "鑑賞模式"),
                                      self._open_extras))
            if self.on_settings:
                items.append(MenuItem(self.ctx.t("settings", "設定"),
                                      lambda: self.on_settings()))
            items.append(MenuItem(self.ctx.t("quit", "離開遊戲"),
                                  lambda: self.on_quit and self.on_quit()))
        layout_size = canvas_size or self.ctx.screen_size
        self._layout_for_items(items, layout_size)
        visible_rows = len(items)
        if self._extras_mode and items and self._menu_row_h < 44:
            # Reflow around a smaller viewport until each visible row reaches
            # the platform-independent minimum interaction target. Logical
            # items remain intact in _TitleMenuList and scroll with focus.
            for candidate in range(len(items) - 1, 0, -1):
                self._layout_for_items(items[:candidate], layout_size)
                if self._menu_row_h >= 44:
                    visible_rows = candidate
                    break
        ox, oy, pw = self._menu_origin
        h = visible_rows * self._menu_row_h
        self.menu = _TitleMenuList(
            pygame.Rect(ox, oy, pw, h), items,
            fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=self._menu_font_size, row_h=self._menu_row_h,
            visible_rows=visible_rows,
        )

    def _layout_for_items(
        self,
        items: list[MenuItem],
        canvas_size: tuple[int, int],
    ) -> None:
        """Place title chrome and a content-sized menu panel on ``canvas``.

        The old title panel was always 484 px tall and started at 33% of the
        screen.  At 720p that put its bottom outside the canvas, underneath the
        byline.  This layout reserves the measured logo and footer first, then
        compresses panel padding/row spacing only as much as the available
        height requires.  Font metrics, rather than assumed glyph heights,
        keep the result safe for CJK fonts and the engine's 1080p UI scaling.
        """
        if self.presentation is not None:
            self._layout_branded_for_items(items, canvas_size)
            return

        sw, sh = canvas_size
        self._layout_size = canvas_size
        density = max(0.65, sh / 720.0)
        self._chrome_density = density
        compact_scale = min(1.0, density)

        self._measure_logo(sw, sh, compact_scale)
        self._measure_footer(sw, sh, compact_scale)

        safe_gap = max(8, int(round(14 * density)))
        panel_top_limit = max(
            self._logo_rect.bottom + safe_gap,
            int(round(sh * 0.29)),
        )
        panel_bottom_limit = self._footer_top - safe_gap
        available_h = max(1, panel_bottom_limit - panel_top_limit)

        side_margin = max(12, int(round(24 * density)))
        preferred_w = max(300, int(round(480 * density)))
        panel_w = max(1, min(preferred_w, sw - side_margin * 2))
        panel_x = (sw - panel_w) // 2
        pad_x = min(
            max(12, int(round(32 * density))),
            max(1, panel_w // 5),
        )
        inner_w = max(1, panel_w - pad_x * 2)

        self._label_font_size = max(
            12, int(round(self.ctx.config.font_size_small * compact_scale)),
        )
        self._menu_font_size = max(
            16, int(round((self.ctx.theme.pad_m + 12) * compact_scale)),
        )
        self._input_font_size = max(
            16, int(round((self.ctx.theme.pad_m + 14) * compact_scale)),
        )
        label_h = self.ctx.fonts.get(self._label_font_size).get_linesize()
        menu_line_h = self.ctx.fonts.get(
            self._menu_font_size,
        ).get_linesize()
        input_line_h = self.ctx.fonts.get(
            self._input_font_size,
        ).get_linesize()

        preferred = {
            "top": max(16, int(round(32 * density))),
            "label_gap": max(4, int(round(6 * density))),
            "input_h": max(
                input_line_h + max(8, int(round(12 * density))),
                int(round(50 * density)),
            ),
            "divider_gap": max(12, int(round(24 * density))),
            "menu_gap": max(10, int(round(22 * density))),
            "row_h": max(
                menu_line_h + max(8, int(round(12 * density))),
                int(round(52 * density)),
                44 if self._extras_mode else 1,
            ),
            "bottom": max(10, int(round(18 * density))),
        }
        minimum = {
            "top": max(8, int(round(18 * density))),
            "label_gap": max(2, int(round(4 * density))),
            "input_h": max(
                input_line_h + 8,
                int(round(38 * density)),
            ),
            "divider_gap": max(6, int(round(12 * density))),
            "menu_gap": max(6, int(round(10 * density))),
            "row_h": max(
                menu_line_h + 6,
                int(round(36 * density)),
                44 if self._extras_mode else 1,
            ),
            "bottom": max(6, int(round(10 * density))),
        }
        row_count = len(items)

        def total(parts: dict[str, int]) -> int:
            return (
                parts["top"] + label_h + parts["label_gap"]
                + parts["input_h"] + parts["divider_gap"] + 1
                + parts["menu_gap"] + row_count * parts["row_h"]
                + parts["bottom"]
            )

        preferred_h = total(preferred)
        minimum_h = total(minimum)
        if preferred_h <= available_h:
            parts = preferred
        elif minimum_h >= available_h:
            parts = minimum
        else:
            compression = min(
                1.0,
                max(
                    0.0,
                    (preferred_h - available_h)
                    / (preferred_h - minimum_h),
                ),
            )
            parts = {
                key: int(round(
                    preferred[key]
                    - (preferred[key] - minimum[key]) * compression
                ))
                for key in preferred
            }

        # Rounding can leave a few excess pixels. Remove those from repeated
        # row spacing first, then from decorative gaps, never below the
        # measured-text minima above.
        excess = total(parts) - available_h
        if excess > 0 and row_count:
            shrink = min(
                parts["row_h"] - minimum["row_h"],
                (excess + row_count - 1) // row_count,
            )
            parts["row_h"] -= max(0, shrink)
            excess = total(parts) - available_h
        for key in ("top", "divider_gap", "menu_gap", "bottom", "label_gap"):
            if excess <= 0:
                break
            shrink = min(parts[key] - minimum[key], excess)
            parts[key] -= max(0, shrink)
            excess = total(parts) - available_h

        # Extremely short custom canvases may not have enough room even for
        # the compact target. Keep every interaction rect on-canvas by using
        # the remaining row budget; normal 16:9 sizes never take this branch.
        if excess > 0 and row_count:
            non_rows = total(parts) - row_count * parts["row_h"]
            parts["row_h"] = max(
                1, (available_h - non_rows) // row_count,
            )

        panel_h = min(available_h, total(parts))
        panel_y = panel_top_limit + max(0, (available_h - panel_h) // 2)
        self._panel_rect = pygame.Rect(
            panel_x, panel_y, panel_w, panel_h,
        )
        self._panel_pad_x = pad_x
        self._menu_row_h = parts["row_h"]

        label_y = panel_y + parts["top"]
        input_y = label_y + label_h + parts["label_gap"]
        input_rect = pygame.Rect(
            panel_x + pad_x, input_y, inner_w, parts["input_h"],
        )
        self._prompt_rect = pygame.Rect(
            input_rect.x, label_y, inner_w, label_h,
        )
        self._prompt_heading_rect = pygame.Rect(0, 0, 0, 0)
        if self.name_input is None:
            self.name_input = TextInput(
                input_rect,
                fonts=self.ctx.fonts,
                theme=self.ctx.theme,
                placeholder=self.ctx.t(
                    "name_placeholder",
                    "輸入主角的名字…",
                ),
                initial=self._initial_name,
                max_length=12,
                font_size=self._input_font_size,
                motion=self.ctx.motion,
            )
        else:
            self.name_input.rect = input_rect
            self.name_input.font_size = self._input_font_size
            if self.name_input.focused:
                pygame.key.set_text_input_rect(input_rect)

        self._divider_y = input_rect.bottom + parts["divider_gap"]
        menu_y = self._divider_y + 1 + parts["menu_gap"]
        self._menu_origin = (panel_x + pad_x, menu_y, inner_w)

    def _layout_branded_for_items(
        self,
        items: list[MenuItem],
        canvas_size: tuple[int, int],
    ) -> None:
        """Lay out a pack-authored centered panel or side rail.

        Rails use the full safe height and move their footer into the
        unobstructed art region. This preserves a 44-pixel interaction target
        for six-row menus even on the supported 800×450 canvas.
        """
        spec = self.presentation
        assert spec is not None
        sw, sh = canvas_size
        self._layout_size = canvas_size
        # FontRegistry already scales typography with the full canvas height.
        # Keep image wordmarks, rail width, padding, and interaction rows on
        # that same scale instead of capping their geometry at 1080p. The old
        # cap made a 4K logo and rail half-size beside correctly scaled text.
        density = max(0.625, sh / 720.0)
        self._chrome_density = density
        compact_scale = min(1.0, density)
        outer = max(8, int(round(18 * density)))

        if spec.layout in ("left_rail", "right_rail"):
            min_width = min(
                sw - outer * 2,
                max(280, int(round(300 * compact_scale))),
            )
            preferred_width = int(round(sw * 0.34))
            max_width = min(
                sw - outer * 2,
                max(min_width, int(round(520 * density))),
            )
            panel_w = max(min_width, min(preferred_width, max_width))
            panel_x = (
                outer
                if spec.layout == "left_rail"
                else sw - outer - panel_w
            )
            self._panel_rect = pygame.Rect(
                panel_x, outer, panel_w, max(1, sh - outer * 2),
            )

            if spec.layout == "left_rail":
                footer_area = pygame.Rect(
                    self._panel_rect.right + outer,
                    0,
                    max(1, sw - self._panel_rect.right - outer * 2),
                    sh,
                )
            else:
                footer_area = pygame.Rect(
                    outer,
                    0,
                    max(1, self._panel_rect.left - outer * 2),
                    sh,
                )
            self._measure_branded_footer(
                footer_area, sh, compact_scale, outer,
            )

            pad_x = max(14, int(round(30 * density)))
            pad_y = max(10, int(round(20 * density)))
            inner = self._panel_rect.inflate(-pad_x * 2, -pad_y * 2)
            if inner.width <= 0:
                inner.width = 1
            if inner.height <= 0:
                inner.height = 1
            logo_h_limit = max(42, min(
                int(round(112 * density)),
                max(42, inner.height // 4),
            ))
            self._measure_branded_wordmark(
                pygame.Rect(inner.x, inner.y, inner.width, logo_h_limit),
                compact_scale,
                align=(
                    "left"
                    if spec.wordmark_style == "editorial"
                    else "center"
                ),
            )
            wordmark_gap = max(6, int(round(14 * density)))
            content_top = min(
                inner.bottom,
                self._logo_rect.bottom + wordmark_gap,
            )
            self._layout_branded_content(
                items,
                pygame.Rect(
                    inner.x,
                    content_top,
                    inner.width,
                    max(1, inner.bottom - content_top),
                ),
                density,
                require_touch_rows=True,
            )
            self._panel_pad_x = pad_x
            return

        # Centered composition: wordmark above a compact, centered panel and
        # footer below it. The panel may compress at 450p, matching the legacy
        # screen's supported minimum without allowing any rect off-canvas.
        self._measure_branded_footer(
            pygame.Rect(outer, 0, max(1, sw - outer * 2), sh),
            sh,
            compact_scale,
            outer,
        )
        logo_area = pygame.Rect(
            outer,
            max(8, int(round(sh * 0.065))),
            max(1, sw - outer * 2),
            max(44, int(round(118 * density))),
        )
        self._measure_branded_wordmark(
            logo_area,
            compact_scale,
            align="center",
        )
        safe_gap = max(7, int(round(13 * density)))
        panel_top = self._logo_rect.bottom + safe_gap
        panel_bottom = self._footer_top - safe_gap
        available_h = max(1, panel_bottom - panel_top)
        side_margin = max(12, int(round(24 * density)))
        preferred_w = max(300, int(round(480 * density)))
        panel_w = max(1, min(preferred_w, sw - side_margin * 2))
        panel_x = (sw - panel_w) // 2
        self._panel_rect = pygame.Rect(
            panel_x, panel_top, panel_w, available_h,
        )
        pad_x = min(
            max(12, int(round(32 * density))),
            max(1, panel_w // 5),
        )
        pad_y = max(7, int(round(16 * density)))
        inner = self._panel_rect.inflate(-pad_x * 2, -pad_y * 2)
        if inner.width <= 0:
            inner.width = 1
        if inner.height <= 0:
            inner.height = 1
        self._layout_branded_content(
            items, inner, density, require_touch_rows=False,
        )
        self._panel_pad_x = pad_x

    def _layout_branded_content(
        self,
        items: list[MenuItem],
        area: pygame.Rect,
        density: float,
        *,
        require_touch_rows: bool,
    ) -> None:
        """Place an optional name form followed by menu interaction rows."""
        compact_scale = min(1.0, density)
        self._label_font_size = max(
            12, int(round(self.ctx.config.font_size_small * compact_scale)),
        )
        self._menu_font_size = max(
            16, int(round((self.ctx.theme.pad_m + 12) * compact_scale)),
        )
        self._input_font_size = max(
            16, int(round((self.ctx.theme.pad_m + 14) * compact_scale)),
        )
        label_h = self.ctx.fonts.get(self._label_font_size).get_linesize()
        menu_line_h = self.ctx.fonts.get(
            self._menu_font_size,
        ).get_linesize()
        input_line_h = self.ctx.fonts.get(
            self._input_font_size,
        ).get_linesize()
        show_name = self._shows_name_input()

        heading_h = 0
        heading_gap = 0
        if self._name_prompt_mode:
            heading_size = max(
                16,
                int(round(self.ctx.config.font_size_menu * compact_scale)),
            )
            heading_h = self.ctx.fonts.get(heading_size).get_linesize()
            self._prompt_heading_font_size = heading_size
            heading_gap = max(4, int(round(8 * density)))
        else:
            self._prompt_heading_font_size = self._menu_font_size

        label_gap = max(2, int(round(4 * density)))
        input_h = max(
            input_line_h + 8,
            int(round(38 * density)),
        )
        divider_gap = max(5, int(round(9 * density)))
        menu_gap = max(4, int(round(8 * density)))
        form_h = 0
        if show_name:
            form_h = (
                heading_h + heading_gap
                + label_h + label_gap + input_h
                + divider_gap + 1 + menu_gap
            )

        row_count = len(items)
        preferred_row_h = max(
            menu_line_h + 8,
            int(round(52 * density)),
            44,
        )
        row_budget = max(1, area.height - form_h)
        if row_count:
            fit_row_h = max(1, row_budget // row_count)
            row_h = min(preferred_row_h, fit_row_h)
            # A rail only drops below 44 when the complete form and all rows
            # mathematically cannot fit its safe content area.
            if (
                (require_touch_rows or self._extras_mode)
                and row_budget >= row_count * 44
            ):
                row_h = max(44, row_h)
        else:
            row_h = preferred_row_h
        self._menu_row_h = row_h

        block_h = min(area.height, form_h + row_count * row_h)
        cursor_y = area.y + max(0, (area.height - block_h) // 2)
        self._prompt_heading_rect = pygame.Rect(
            area.x, cursor_y, area.width, heading_h,
        )
        if self._name_prompt_mode:
            cursor_y += heading_h + heading_gap

        self._prompt_rect = pygame.Rect(
            area.x, cursor_y, area.width, label_h if show_name else 0,
        )
        if show_name:
            cursor_y += label_h + label_gap
            input_rect = pygame.Rect(
                area.x, cursor_y, area.width, input_h,
            )
            self._configure_name_input(input_rect)
            cursor_y = input_rect.bottom + divider_gap
            self._divider_y = cursor_y
            cursor_y += 1 + menu_gap
        else:
            if self.name_input is not None:
                self.name_input.unfocus()
            self.name_input = None
            self._divider_y = cursor_y

        menu_h = row_count * row_h
        # Integer rounding on very small custom surfaces can leave one pixel
        # too much. Clamp the origin instead of shortening interaction rows.
        menu_y = min(cursor_y, max(area.y, area.bottom - menu_h))
        self._menu_origin = (area.x, menu_y, area.width)

    def _shows_name_input(self) -> bool:
        if self.presentation is None:
            return True
        return (
            self.presentation.name_entry == "inline"
            or self._name_prompt_mode
        )

    def _configure_name_input(self, rect: pygame.Rect) -> None:
        on_submit = (
            (lambda _text: self._confirm_new_game())
            if self._name_prompt_mode else None
        )
        if self.name_input is None:
            self.name_input = TextInput(
                rect,
                fonts=self.ctx.fonts,
                theme=self.ctx.theme,
                placeholder=self.ctx.t(
                    "name_placeholder",
                    "輸入主角的名字…",
                ),
                initial=self._initial_name,
                max_length=12,
                font_size=self._input_font_size,
                on_submit=on_submit,
                motion=self.ctx.motion,
            )
            return
        self.name_input.rect = rect
        self.name_input.font_size = self._input_font_size
        self.name_input.on_submit = on_submit
        if self.name_input.focused:
            pygame.key.set_text_input_rect(rect)

    def _measure_branded_wordmark(
        self,
        area: pygame.Rect,
        compact_scale: float,
        *,
        align: str,
    ) -> None:
        """Measure either a validated logo image or a text wordmark."""
        spec = self.presentation
        assert spec is not None
        tagline = spec.tagline.strip() or self.subtitle_text
        tagline_size = max(
            12,
            int(round(self.ctx.config.font_size_menu * compact_scale)),
        )
        tagline_h = (
            self.ctx.fonts.get(tagline_size).get_linesize()
            if tagline else 0
        )
        tagline_gap = max(3, int(round(7 * compact_scale))) if tagline else 0
        mark_h = max(1, area.height - tagline_h - tagline_gap)

        self._logo_subtitle_rect = None
        self._logo_image_rect = None
        self._wordmark_title_text = self.title_text
        self._wordmark_tagline_text = tagline
        if self._logo_image is not None:
            iw, ih = self._logo_image.get_size()
            scale = min(
                area.width / max(1, iw),
                mark_h / max(1, ih),
                1.0 if compact_scale <= 1.0 else compact_scale,
            )
            logo_w = max(1, int(round(iw * scale)))
            logo_h = max(1, int(round(ih * scale)))
            logo_x = (
                area.x
                if align == "left"
                else area.centerx - logo_w // 2
            )
            self._logo_image_rect = pygame.Rect(
                logo_x, area.y, logo_w, logo_h,
            )
            self._logo_title_rect = self._logo_image_rect.copy()
            self._logo_title_size = 0
        else:
            preferred = max(
                26,
                int(round(
                    (
                        self.ctx.config.font_size_header
                        + (14 if spec.wordmark_style == "editorial" else 20)
                    )
                    * compact_scale
                )),
            )
            self._logo_title_size = self._fit_text_size(
                self.title_text,
                preferred,
                area.width,
                minimum=14,
                bold=(spec.wordmark_style == "classic"),
            )
            self._wordmark_title_text = self._elide_text(
                self.title_text,
                self._logo_title_size,
                area.width,
                bold=(spec.wordmark_style == "classic"),
            )
            title = self.ctx.fonts.render(
                self._wordmark_title_text,
                self._logo_title_size,
                self.ctx.theme.accent,
                bold=(spec.wordmark_style == "classic"),
            )
            title_x = (
                area.x
                if align == "left"
                else area.centerx - title.get_width() // 2
            )
            self._logo_title_rect = title.get_rect(
                left=title_x, top=area.y,
            )

        mark_rect = (
            self._logo_image_rect
            if self._logo_image_rect is not None
            else self._logo_title_rect
        )
        if tagline:
            self._logo_subtitle_size = self._fit_text_size(
                tagline,
                tagline_size,
                area.width,
                minimum=9,
            )
            self._wordmark_tagline_text = self._elide_text(
                tagline,
                self._logo_subtitle_size,
                area.width,
            )
            tagline_surface = self.ctx.fonts.render(
                self._wordmark_tagline_text,
                self._logo_subtitle_size,
                self.ctx.theme.text_mute,
            )
            tagline_x = (
                area.x
                if align == "left"
                else area.centerx - tagline_surface.get_width() // 2
            )
            tagline_y = min(
                area.bottom - tagline_surface.get_height(),
                mark_rect.bottom + tagline_gap,
            )
            self._logo_subtitle_rect = tagline_surface.get_rect(
                left=tagline_x, top=max(area.y, tagline_y),
            )
            self._logo_rect = mark_rect.union(self._logo_subtitle_rect)
        else:
            self._logo_subtitle_size = tagline_size
            self._logo_rect = mark_rect.copy()

    def _measure_branded_footer(
        self,
        area: pygame.Rect,
        sh: int,
        compact_scale: float,
        margin: int,
    ) -> None:
        """Measure footer copy inside a collision-free horizontal region."""
        spec = self.presentation
        assert spec is not None
        parts = []
        if spec.footer_text.strip():
            parts.append(spec.footer_text.strip())
        if spec.show_engine_credit:
            parts.append("Powered by World Gal-Game")
        self._footer_display_text = "  /  ".join(parts)
        self._footer_font_size = max(
            10,
            int(round(self.ctx.config.font_size_small * compact_scale)),
        )

        version_text = f"v{self.version_text}" if self.version_text else ""
        version = self.ctx.fonts.render(
            version_text,
            self._footer_font_size,
            (*self.ctx.theme.text_dim, 220),
        )
        version_gap = max(6, int(round(12 * compact_scale)))
        version_w = version.get_width() if version_text else 0
        footer_max_w = max(
            1,
            area.width - version_w - (version_gap if version_w else 0),
        )
        if self._footer_display_text:
            self._footer_font_size = self._fit_text_size(
                self._footer_display_text,
                self._footer_font_size,
                footer_max_w,
                minimum=9,
            )
            self._footer_render_text = self._elide_text(
                self._footer_display_text,
                self._footer_font_size,
                footer_max_w,
            )
            byline = self.ctx.fonts.render(
                self._footer_render_text,
                self._footer_font_size,
                (*self.ctx.theme.text_dim, 220),
            )
        else:
            self._footer_render_text = ""
            byline = pygame.Surface((0, 0), pygame.SRCALPHA)

        baseline_bottom = sh - margin
        self._version_rect = None
        if version_text:
            # Re-render after fitting the shared footer font so both pieces
            # sit on the same typographic baseline.
            version = self.ctx.fonts.render(
                version_text,
                self._footer_font_size,
                (*self.ctx.theme.text_dim, 220),
            )
            self._version_rect = version.get_rect(
                right=area.right, bottom=baseline_bottom,
            )
        text_right = (
            self._version_rect.left - version_gap
            if self._version_rect is not None
            else area.right
        )
        text_area = pygame.Rect(
            area.x,
            0,
            max(1, text_right - area.x),
            sh,
        )
        if self._footer_display_text:
            self._byline_rect = byline.get_rect(
                centerx=text_area.centerx,
                bottom=baseline_bottom,
            )
        else:
            self._byline_rect = pygame.Rect(
                text_area.centerx, baseline_bottom, 0, 0,
            )
        footer_rects = [
            rect for rect in (self._byline_rect, self._version_rect)
            if rect is not None and (rect.width or rect.height)
        ]
        self._footer_top = (
            min(rect.top for rect in footer_rects)
            if footer_rects else baseline_bottom
        )

    def _fit_text_size(
        self,
        text: str,
        preferred: int,
        maximum_width: int,
        *,
        minimum: int,
        bold: bool = False,
    ) -> int:
        """Return the largest requested font size that fits ``maximum_width``."""
        size = max(minimum, preferred)
        while size > minimum:
            rendered = self.ctx.fonts.render(text, size, (255, 255, 255),
                                             bold=bold)
            if rendered.get_width() <= maximum_width:
                break
            ratio = maximum_width / max(1, rendered.get_width())
            next_size = max(minimum, min(size - 1, int(size * ratio)))
            size = next_size
        return size

    def _elide_text(
        self,
        text: str,
        size: int,
        maximum_width: int,
        *,
        bold: bool = False,
    ) -> str:
        """Return a single-line rendering that cannot escape its safe area."""
        if not text or maximum_width <= 0:
            return ""
        if self.ctx.fonts.render(
            text, size, (255, 255, 255), bold=bold,
        ).get_width() <= maximum_width:
            return text
        ellipsis = "…"
        if self.ctx.fonts.render(
            ellipsis, size, (255, 255, 255), bold=bold,
        ).get_width() > maximum_width:
            return ""
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            candidate = text[:mid].rstrip() + ellipsis
            width = self.ctx.fonts.render(
                candidate, size, (255, 255, 255), bold=bold,
            ).get_width()
            if width <= maximum_width:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo].rstrip() + ellipsis

    def _measure_logo(
        self,
        sw: int,
        sh: int,
        compact_scale: float,
    ) -> None:
        max_width = max(1, sw - max(24, int(sw * 0.05)))
        preferred_title = max(
            28,
            int(round(
                (self.ctx.config.font_size_header + 20) * compact_scale
            )),
        )
        self._logo_title_size = self._fit_text_size(
            self.title_text,
            preferred_title,
            max_width,
            minimum=20,
            bold=True,
        )
        title = self.ctx.fonts.render(
            self.title_text, self._logo_title_size,
            self.ctx.theme.accent, bold=True,
        )
        title_y = max(12, sh // 7)
        self._logo_title_rect = title.get_rect(
            centerx=sw // 2, top=title_y,
        )
        self._logo_subtitle_rect = None
        self._logo_subtitle_size = max(
            14,
            int(round(self.ctx.config.font_size_menu * compact_scale)),
        )
        if self.subtitle_text:
            self._logo_subtitle_size = self._fit_text_size(
                self.subtitle_text,
                self._logo_subtitle_size,
                max_width,
                minimum=12,
            )
            subtitle = self.ctx.fonts.render(
                self.subtitle_text,
                self._logo_subtitle_size,
                self.ctx.theme.text_mute,
            )
            sub_gap = max(4, int(round(8 * compact_scale)))
            self._logo_subtitle_rect = subtitle.get_rect(
                centerx=sw // 2,
                top=self._logo_title_rect.bottom + sub_gap,
            )
            self._logo_rect = self._logo_title_rect.union(
                self._logo_subtitle_rect,
            )
        else:
            self._logo_rect = self._logo_title_rect.copy()

    def _measure_footer(
        self,
        sw: int,
        sh: int,
        compact_scale: float,
    ) -> None:
        self._footer_font_size = max(
            12,
            int(round(self.ctx.config.font_size_small * compact_scale)),
        )
        byline = self.ctx.fonts.render(
            "Powered by World Gal-Game",
            self._footer_font_size,
            (*self.ctx.theme.text_dim, 200),
        )
        margin = max(8, int(round(14 * compact_scale)))
        self._byline_rect = byline.get_rect(
            centerx=sw // 2, bottom=sh - margin,
        )
        self._version_rect = None
        if self.version_text:
            version = self.ctx.fonts.render(
                f"v{self.version_text}",
                self._footer_font_size,
                (*self.ctx.theme.text_dim, 200),
            )
            self._version_rect = version.get_rect(
                right=sw - margin, bottom=sh - margin,
            )
        footer_rects = [self._byline_rect]
        if self._version_rect is not None:
            footer_rects.append(self._version_rect)
        self._footer_top = min(rect.top for rect in footer_rects)

    def _open_extras(self) -> None:
        """Swap the title menu to the extras (鑑賞模式) submenu."""
        self._name_prompt_mode = False
        self._extras_mode = True
        self._build_menu()

    def _close_extras(self) -> None:
        self._extras_mode = False
        self._build_menu()

    def update(self, dt: float, inp) -> None:
        name_focused = bool(
            self._name_prompt_mode
            and self.name_input
            and self.name_input.focused
        )
        # Text/IME entry is an explicit input context. A player may bind the
        # general Back action to a printable key; typing that character must
        # not dismiss the name form. Escape (and the gamepad-B compatibility
        # event) remains the form-local rescue action.
        input_events = tuple(getattr(inp, "events", ()))
        if name_focused:
            keyboard_edges = tuple(
                event
                for event in input_events
                if event.type == pygame.KEYDOWN
            )
            cancel_requested = any(
                event.key == pygame.K_ESCAPE
                for event in keyboard_edges
            ) or (
                bool(getattr(inp, "cancel", False))
                and not keyboard_edges
            )
        else:
            cancel_requested = bool(getattr(inp, "cancel", False))
        if cancel_requested:
            if self._name_prompt_mode:
                self._cancel_name_prompt()
                return
            if self._extras_mode:
                self._close_extras()
                return
        if self.name_input:
            self.name_input.update(dt, inp)
            # Some tests and embedding clients construct InputState directly
            # with ``confirm=True`` and no synthetic KEYDOWN event. Honour the
            # semantic action just like a keyboard/gamepad Enter.
            if (
                self._name_prompt_mode
                and self.name_input.focused
                and inp.confirm
                and not any(
                    event.type == pygame.KEYDOWN
                    for event in getattr(inp, "events", ())
                )
            ):
                self._confirm_new_game()
                return
        if self.menu:
            # While the player is typing their name, keyboard nav on the
            # menu would consume W/S/Enter and either jump the selection
            # or fire a menu item. Disable keyboard nav for the menu in
            # that case; mouse clicks still work.
            self.menu.keyboard_nav = not (self.name_input
                                          and self.name_input.focused)
            self.menu.update(dt, inp)

    def draw(self, surface: pygame.Surface) -> None:
        sw, sh = surface.get_size()
        if surface.get_size() != getattr(self, "_layout_size", None):
            # The normal app draws to a fixed logical canvas, but embedders and
            # focused scene tests may resize that canvas between frames.
            self._build_menu(surface.get_size())
        # Background art (kept brighter than before — the frosted panel below
        # carries readability instead of washing the whole photo out), a cold
        # radial glow, and a bottom darkening band for the byline/version.
        bg = pygame.Surface(surface.get_size())
        bg.fill(self.ctx.theme.bg_deep)
        if self.bg_path:
            img = self.ctx.assets.scaled(self.bg_path, surface.get_size(),
                                         fit="cover")
            # ``scaled`` surfaces belong to AssetManager's cache. Per-screen
            # alpha must be applied to a copy or the title will permanently
            # dim that asset for every later scene.
            composed = img.copy()
            opacity = (
                self.presentation.background_opacity
                if self.presentation is not None
                else 205 / 255
            )
            composed.set_alpha(max(0, min(255, round(opacity * 255))))
            bg.blit(composed, (0, 0))
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        for r, alpha in [(460, 9), (340, 15), (240, 22)]:
            pygame.draw.circle(veil,
                               (*self.ctx.theme.accent[:3], alpha),
                               (sw // 2, sh // 3), r)
        bg.blit(veil, (0, 0))
        grad = pygame.Surface((sw, 170), pygame.SRCALPHA)
        for i in range(170):
            pygame.draw.line(grad, (*self.ctx.theme.bg_deep[:3],
                                    int(160 * (i / 170))), (0, i), (sw, i))
        bg.blit(grad, (0, sh - 170))
        surface.blit(bg, (0, 0))

        # The side-rail wordmark lives inside its panel, so panel chrome must
        # be painted first. The same order is harmless for centered layouts
        # where the two rects are disjoint.
        self._draw_panel(surface)

        # Wordmark: logo image or text treatment, with the subtitle/tagline.
        self._draw_logo(surface, sw, sh)

        # Byline + version, bottom.
        footer_text = (
            self._footer_render_text
            if self.presentation is not None
            else "Powered by World Gal-Game"
        )
        footer_alpha = 220 if self.presentation is not None else 200
        if footer_text:
            byline = self.ctx.fonts.render(
                footer_text,
                self._footer_font_size,
                (*self.ctx.theme.text_dim, footer_alpha),
            )
            surface.blit(byline, self._byline_rect)
        if self.version_text and self._version_rect is not None:
            ver = self.ctx.fonts.render(f"v{self.version_text}",
                                        self._footer_font_size,
                                        (*self.ctx.theme.text_dim,
                                          footer_alpha))
            surface.blit(ver, self._version_rect)

        # Name form and menu sit above the panel and wordmark.
        if self.name_input:
            if self._name_prompt_mode:
                heading = self.ctx.fonts.render(
                    self.ctx.t(
                        "enrollment_heading",
                        "新生指南 · 入學登記",
                    ),
                    self._prompt_heading_font_size,
                    self.ctx.theme.accent,
                    bold=True,
                )
                surface.blit(heading, self._prompt_heading_rect.topleft)
            prompt = self.ctx.fonts.render(
                (
                    self.ctx.t("name_prompt_instruction", "請輸入姓名")
                    if self._name_prompt_mode
                    else self.ctx.t("name_prompt", "姓名")
                ),
                self._label_font_size,
                self.ctx.theme.text_mute,
            )
            surface.blit(prompt, self._prompt_rect.topleft)
            self.name_input.draw(surface)
            pygame.draw.line(
                surface,
                self.ctx.theme.border_soft,
                (self._panel_rect.x + self._panel_pad_x, self._divider_y),
                (self._panel_rect.right - self._panel_pad_x, self._divider_y),
                self._chrome_stroke(),
            )
        if self.menu:
            self.menu.draw(surface)

    def _draw_panel(self, surface: pygame.Surface) -> None:
        """Draw the selected glass/rail chrome without covering its content."""
        pr = self._panel_rect
        panel = pygame.Surface(pr.size, pygame.SRCALPHA)
        if (
            self.presentation is not None
            and self.presentation.panel_style == "rail"
        ):
            pygame.draw.rect(
                panel,
                (*self.ctx.theme.bg_deep[:3], 232),
                panel.get_rect(),
            )
            pygame.draw.rect(
                panel,
                (*self.ctx.theme.border_strong[:3], 220),
                panel.get_rect(),
                width=self._chrome_stroke(),
            )
            edge_x = (
                panel.get_width() - self._chrome_stroke(3)
                if self.presentation.layout == "left_rail" else 0
            )
            pygame.draw.line(
                panel,
                self.ctx.theme.accent,
                (edge_x, 0),
                (edge_x, panel.get_height()),
                self._chrome_stroke(3),
            )
        else:
            pygame.draw.rect(
                panel,
                self.ctx.theme.bg_overlay,
                panel.get_rect(),
                border_radius=self.ctx.theme.radius_l,
            )
            pygame.draw.rect(
                panel,
                self.ctx.theme.border_strong,
                panel.get_rect(),
                width=self._chrome_stroke(),
                border_radius=self.ctx.theme.radius_l,
            )
        surface.blit(panel, pr.topleft)

    def _chrome_stroke(self, base_width: int = 1) -> int:
        """Scale structural title chrome with the rendered canvas density."""
        return max(
            1,
            int(base_width * self._chrome_density + 0.5),
        )

    def _draw_logo(self, surface: pygame.Surface, sw: int, sh: int) -> None:
        if self.presentation is not None:
            self._draw_branded_wordmark(surface)
            return
        title = self.ctx.fonts.render(self.title_text, self._logo_title_size,
                                      self.ctx.theme.accent, bold=True)
        tx, ty = self._logo_title_rect.topleft
        glow = self.ctx.fonts.render(self.title_text, self._logo_title_size,
                                     self.ctx.theme.accent_alt, bold=True)
        glow.set_alpha(45)
        for ox, oy in [(-3, 0), (3, 0), (0, -3), (0, 3), (0, 5)]:
            surface.blit(glow, (tx + ox, ty + oy))
        shadow = self.ctx.fonts.render(self.title_text, self._logo_title_size,
                                       (0, 0, 0),
                                       bold=True)
        shadow.set_alpha(160)
        surface.blit(shadow, (tx + 3, ty + 4))
        surface.blit(title, (tx, ty))
        if self.subtitle_text and self._logo_subtitle_rect is not None:
            sub = self.ctx.fonts.render(self.subtitle_text,
                                        self._logo_subtitle_size,
                                        self.ctx.theme.text_mute)
            surface.blit(sub, self._logo_subtitle_rect)

    def _draw_branded_wordmark(self, surface: pygame.Surface) -> None:
        spec = self.presentation
        assert spec is not None
        if self._logo_image is not None and self._logo_image_rect is not None:
            scaled = pygame.transform.smoothscale(
                self._logo_image,
                self._logo_image_rect.size,
            )
            surface.blit(scaled, self._logo_image_rect)
        elif spec.wordmark_style == "editorial":
            title = self.ctx.fonts.render(
                self._wordmark_title_text,
                self._logo_title_size,
                self.ctx.theme.text,
            )
            shadow = self.ctx.fonts.render(
                self._wordmark_title_text,
                self._logo_title_size,
                (0, 0, 0),
            )
            shadow.set_alpha(145)
            surface.blit(
                shadow,
                self._logo_title_rect.move(2, 3),
            )
            surface.blit(title, self._logo_title_rect)
            rule_x = max(
                0,
                self._logo_title_rect.left
                - max(5, self.ctx.theme.pad_s),
            )
            pygame.draw.line(
                surface,
                self.ctx.theme.accent,
                (rule_x, self._logo_title_rect.top),
                (rule_x, self._logo_title_rect.bottom),
                max(
                    self._chrome_stroke(2),
                    self._chrome_stroke(self.ctx.theme.pad_xs // 2),
                ),
            )
        else:
            title = self.ctx.fonts.render(
                self._wordmark_title_text,
                self._logo_title_size,
                self.ctx.theme.accent,
                bold=True,
            )
            tx, ty = self._logo_title_rect.topleft
            glow = self.ctx.fonts.render(
                self._wordmark_title_text,
                self._logo_title_size,
                self.ctx.theme.accent_alt,
                bold=True,
            )
            glow.set_alpha(45)
            for ox, oy in [(-3, 0), (3, 0), (0, -3), (0, 3), (0, 5)]:
                surface.blit(glow, (tx + ox, ty + oy))
            shadow = self.ctx.fonts.render(
                self._wordmark_title_text,
                self._logo_title_size,
                (0, 0, 0),
                bold=True,
            )
            shadow.set_alpha(160)
            surface.blit(shadow, (tx + 3, ty + 4))
            surface.blit(title, (tx, ty))

        if self._wordmark_tagline_text and self._logo_subtitle_rect is not None:
            sub = self.ctx.fonts.render(
                self._wordmark_tagline_text,
                self._logo_subtitle_size,
                self.ctx.theme.text_mute,
            )
            surface.blit(sub, self._logo_subtitle_rect)

    def describe(self) -> dict:
        labels = [item.label for item in self.menu.items] if self.menu else []
        selected_index = (
            self.menu.selected
            if self.menu is not None and self.menu.items else None
        )
        selected = (
            labels[selected_index]
            if selected_index is not None
            and 0 <= selected_index < len(labels)
            else None
        )
        mode = (
            "name_prompt"
            if self._name_prompt_mode
            else "extras" if self._extras_mode else "main"
        )
        return {
            "scene": "TitleScene",
            "name": self.name_input.text if self.name_input else "",
            "mode": mode,
            "menu": labels,
            "menu_labels": labels,
            "selected": selected,
            "selected_index": selected_index,
            "name_prompt": self._name_prompt_mode,
            "name_prompt_title": (
                self.ctx.t("enrollment_heading", "新生指南 · 入學登記")
                if self._name_prompt_mode else None
            ),
            "wordmark_source": self._wordmark_source,
        }
