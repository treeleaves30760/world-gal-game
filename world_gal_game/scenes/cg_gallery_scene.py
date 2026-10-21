"""CG gallery overlay.

A scrollable thumbnail grid of the pack's event illustrations (CGs).
Unlocked CGs (recorded in ``state.cg_gallery``) render their thumbnail;
the rest show a dark "locked" placeholder. Clicking an unlocked thumbnail
opens a fullscreen view of that CG; the bound back action or a click returns.

The full CG set is enumerated from the pack's ``assets/cgs/`` directory,
resolved through the asset manager (which knows the pack root and applies
the same precedence the engine uses to load images). If that directory
can't be located, the scene degrades gracefully to showing only the CGs
the player has already unlocked.

Mirrors ``achievements_scene.py``: Panel + close Button +
``ScrollArea.set_drawer``.
"""
from __future__ import annotations

from pathlib import Path

import pygame

from .base import Scene, SceneContext
from ..core.cg_gallery_spec import CGGalleryEntrySpec, CGGalleryManifest
from ..core.media_authority import MediaAuthority
from ..ui.input_bindings import BindingProfile, InputAction
from ..ui.system_chrome import ChromePart, ChromeRequest
from ..ui.widgets import Button, Panel, ScrollArea

# Image suffixes we treat as candidate CGs when enumerating the directory.
_CG_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")


class CGGalleryScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        self.on_close = None
        self._fullscreen: str | None = None
        self._focus = -1
        self._hover_index = -1
        self._cells: list[tuple[pygame.Rect, str, bool]] = []
        self._catalog_by_asset: dict[str, CGGalleryEntrySpec] = {}

    def _sp(self, value: int) -> int:
        return max(1, int(round(value * self._density)))

    def _paint_chrome(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        *,
        part: ChromePart,
        variant: str,
        enabled: bool = True,
        hovered: bool = False,
        focused: bool = False,
        selected: bool = False,
        emphasis: int = 0,
    ) -> bool:
        """Let the optional pack renderer paint one finite visual role."""
        chrome = getattr(self.ctx, "ui_chrome", None)
        if chrome is None:
            return False
        return chrome.paint(
            surface,
            rect,
            ChromeRequest(
                part=part,
                screen="gallery",
                variant=variant,
                size=rect.size,
                density=self._density,
                enabled=enabled,
                hovered=hovered,
                focused=focused,
                selected=selected,
                emphasis=emphasis,
            ),
        )

    def enter(self, *, on_close=None, **_) -> None:
        self.on_close = on_close
        # Path of the CG currently shown fullscreen, or None for the grid.
        self._fullscreen = None
        self._hover_index = -1
        self._cgs = self._discover_cgs()

        sw, sh = self.ctx.screen_size
        theme = self.ctx.theme
        self._density = max(
            1.0,
            float(getattr(self.ctx.fonts, "scale", 1.0)),
        )
        self._minimum_target = max(44, self._sp(44))
        self._title_font_size = (
            30
            if sh <= 450
            else self.ctx.config.font_size_header
        )
        self._counter_font_size = 18
        self._caption_font_size = 14
        self._caption_subtitle_font_size = 12
        self._hint_font_size = 15
        self._button_font_size = 15
        self._footer_font_size = 12
        self._fullscreen_title_font_size = 18
        self._fullscreen_subtitle_font_size = 14
        self._fullscreen_meta_font_size = 13

        title_text = self.ctx.localization.t("cg_gallery", "CG鑑賞")
        self._title_surface = self.ctx.fonts.render(
            title_text,
            self._title_font_size,
            theme.accent,
            bold=True,
        )
        self._title_line_h = self.ctx.fonts.get(
            self._title_font_size,
            bold=True,
        ).get_linesize()
        self._counter_line_h = self.ctx.fonts.get(
            self._counter_font_size,
        ).get_linesize()
        self._caption_line_h = self.ctx.fonts.get(
            self._caption_font_size,
        ).get_linesize()
        self._caption_subtitle_line_h = self.ctx.fonts.get(
            self._caption_subtitle_font_size,
        ).get_linesize()
        self._hint_line_h = self.ctx.fonts.get(
            self._hint_font_size,
        ).get_linesize()
        self._footer_line_h = self.ctx.fonts.get(
            self._footer_font_size,
            bold=True,
        ).get_linesize()

        edge_x = max(16, self._sp(34))
        edge_y = max(16, self._sp(24))
        panel_w = min(self._sp(1040), max(1, sw - edge_x * 2))
        panel_h = min(self._sp(720), max(1, sh - edge_y * 2))
        self._panel_rect = pygame.Rect(0, 0, panel_w, panel_h)
        self._panel_rect.center = (sw // 2, sh // 2)
        self._panel = Panel(
            self._panel_rect,
            theme,
            fill=(*theme.bg_overlay[:3], 240),
            border=theme.border_strong,
            radius=theme.radius_l,
            border_width=2,
            chrome=getattr(self.ctx, "ui_chrome", None),
            screen="gallery",
            role="main",
            density=self._density,
        )

        inner_pad = max(16, self._sp(30))
        header_pad = max(10, self._sp(14))
        bindings = BindingProfile(
            getattr(self.ctx.config, "input_bindings", {}),
        )
        close_label = (
            f"{self.ctx.localization.t('close', '關閉')} "
            f"({bindings.primary_display(InputAction.BACK)})"
        )
        close_font = self.ctx.fonts.get(
            self._button_font_size,
            bold=True,
        )
        close_h = max(
            self._minimum_target,
            close_font.get_linesize() + self._sp(14),
        )
        close_w = max(
            self._minimum_target,
            self._sp(120),
            close_font.size(close_label)[0] + self._sp(24),
        )
        unlocked_n = sum(
            1
            for path in self._cgs
            if self.ctx.state.cg_gallery.is_unlocked(path)
        )
        self._counter_text = f"{unlocked_n} / {len(self._cgs)}"
        self._counter_surface = self.ctx.fonts.render(
            self._counter_text,
            self._counter_font_size,
            theme.accent_warm,
        )

        title_counter_gap = self._sp(18)
        close_gap = self._sp(18)
        same_row_width = (
            inner_pad
            + self._title_surface.get_width()
            + title_counter_gap
            + self._counter_surface.get_width()
            + close_gap
            + close_w
            + inner_pad
        )
        self._header_stacked = same_row_width > self._panel_rect.width
        if self._header_stacked:
            text_block_h = (
                self._title_surface.get_height()
                + self._sp(4)
                + self._counter_surface.get_height()
            )
            self._header_h = max(text_block_h, close_h) + header_pad * 2
            title_y = self._panel_rect.y + header_pad
            counter_y = (
                title_y
                + self._title_surface.get_height()
                + self._sp(4)
            )
        else:
            self._header_h = max(
                self._title_surface.get_height(),
                self._counter_surface.get_height(),
                close_h,
            ) + header_pad * 2
            title_y = (
                self._panel_rect.y
                + (self._header_h - self._title_surface.get_height()) // 2
            )
            counter_y = (
                self._panel_rect.y
                + (self._header_h - self._counter_surface.get_height()) // 2
            )
        title_x = self._panel_rect.x + inner_pad
        self._title_rect = self._title_surface.get_rect(
            topleft=(title_x, title_y),
        )
        counter_x = (
            title_x
            if self._header_stacked
            else self._title_rect.right + title_counter_gap
        )
        self._counter_rect = self._counter_surface.get_rect(
            topleft=(counter_x, counter_y),
        )
        close_rect = pygame.Rect(
            self._panel_rect.right - inner_pad - close_w,
            self._panel_rect.y + (self._header_h - close_h) // 2,
            close_w,
            close_h,
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
            chrome=getattr(self.ctx, "ui_chrome", None),
            screen="gallery",
            role="close",
            motion=self.ctx.motion,
        )
        self._header_divider_y = self._panel_rect.y + self._header_h - 1
        body_top = self._header_divider_y + self._sp(12)
        footer_pad = self._sp(8)
        footer_h = max(
            self._sp(34),
            self._footer_line_h + self._sp(12),
        )
        self._footer_rect = pygame.Rect(
            self._panel_rect.x + inner_pad,
            self._panel_rect.bottom - footer_pad - footer_h,
            max(1, self._panel_rect.width - inner_pad * 2),
            footer_h,
        )
        body_bottom = self._footer_rect.top - self._sp(8)
        self._scroll = ScrollArea(
            pygame.Rect(
                self._panel_rect.x + inner_pad,
                body_top,
                max(1, self._panel_rect.width - inner_pad * 2),
                max(1, body_bottom - body_top),
            ),
            fonts=self.ctx.fonts,
            theme=theme,
        )
        self._scroll.set_drawer(self._draw_content)
        self._layout_cells()
        self._focus = 0 if self._cells else -1
        self._scroll.content_height = self._content_height
        self._ensure_focus_visible()

    # ---------- CG discovery -------------------------------------------------

    def _discover_cgs(self) -> list[str]:
        """Return the visible CG roster in authored or legacy order.

        An authored ``content/cg_gallery.yaml`` is authoritative for locked
        entries and ordering, including when its roster is intentionally
        empty. Hidden entries join the visible roster only after unlock.
        Uncatalogued paths already present in an old save (or unlocked by a
        plugin) are appended as filename-based fallback entries so adopting a
        catalog never removes player history.

        Without an authored manifest, this retains the historical behaviour:
        enumerate ``assets/cgs/`` and union every unlocked path.
        """
        unlocked = set(self.ctx.state.cg_gallery.unlocked)
        authority = self.ctx.state.meta.get("__pack_media_authority__")
        if isinstance(authority, MediaAuthority):
            # Loader rebinding is the primary boundary. Repeat the immutable
            # intersection here so a malformed runtime/plugin mutation still
            # cannot make the gallery hand a new path to the image decoder.
            unlocked = authority.filter_cg_ids(unlocked)
        manifest = self.ctx.state.meta.get("__cg_gallery_manifest__")
        if isinstance(manifest, CGGalleryManifest) and manifest.authored:
            ordered = manifest.ordered()
            self._catalog_by_asset = manifest.by_asset()
            catalog_assets = set(self._catalog_by_asset)
            visible = [
                entry.asset
                for entry in ordered
                if not entry.hidden or entry.asset in unlocked
            ]
            visible.extend(sorted(unlocked - catalog_assets))
            return visible

        self._catalog_by_asset = {}
        found: set[str] = set()
        cg_dir = self._resolve_cg_dir()
        if cg_dir is not None:
            try:
                for entry in cg_dir.iterdir():
                    if (entry.is_file()
                            and entry.suffix.lower() in _CG_SUFFIXES):
                        # Store the pack-relative form so it matches the
                        # paths the dialogue engine records on unlock.
                        found.add(f"assets/cgs/{entry.name}")
            except OSError:
                # Unreadable directory — fall back to the unlocked set only.
                found = set()
        return sorted(found | unlocked)

    def _catalog_entry(self, asset: str) -> CGGalleryEntrySpec | None:
        return self._catalog_by_asset.get(asset)

    def _resolve_cg_dir(self) -> Path | None:
        """Best-effort absolute path to the pack's ``assets/cgs`` directory."""
        resolve = getattr(self.ctx.assets, "_resolve", None)
        if resolve is None:
            return None
        try:
            resolved = resolve("assets/cgs")
        except Exception:
            return None
        if resolved is None:
            return None
        path = Path(resolved)
        return path if path.is_dir() else None

    # ---------- grid content -------------------------------------------------

    @staticmethod
    def _ellipsize(text: str, font, max_width: int) -> str:
        """Keep authored captions intact when possible and contain overflow."""
        if max_width <= 0:
            return ""
        if font.size(text)[0] <= max_width:
            return text
        ellipsis = "…"
        if font.size(ellipsis)[0] > max_width:
            return ""
        low = 0
        high = len(text)
        while low < high:
            middle = (low + high + 1) // 2
            candidate = text[:middle].rstrip() + ellipsis
            if font.size(candidate)[0] <= max_width:
                low = middle
            else:
                high = middle - 1
        return text[:low].rstrip() + ellipsis

    @classmethod
    def _wrap_ellipsized(
        cls,
        text: str,
        font,
        max_width: int,
        max_lines: int,
    ) -> list[str]:
        """Pixel-wrap CJK or unbroken text into a bounded line count."""
        remaining = text.strip()
        if not remaining or max_width <= 0 or max_lines <= 0:
            return []
        lines: list[str] = []
        while remaining and len(lines) < max_lines:
            if font.size(remaining)[0] <= max_width:
                lines.append(remaining)
                break
            if len(lines) == max_lines - 1:
                lines.append(cls._ellipsize(remaining, font, max_width))
                break

            low = 1
            high = len(remaining)
            while low < high:
                middle = (low + high + 1) // 2
                if font.size(remaining[:middle])[0] <= max_width:
                    low = middle
                else:
                    high = middle - 1
            cut = max(1, low)
            prefix = remaining[:cut]
            # Prefer a natural Latin-word boundary when it does not discard
            # most of the available row; CJK and long tokens safely fall back
            # to the exact pixel cut.
            word_break = prefix.rstrip().rfind(" ")
            if word_break >= max(1, cut // 2):
                cut = word_break
                prefix = remaining[:cut]
            lines.append(prefix.rstrip())
            remaining = remaining[cut:].lstrip()
        return lines

    def _layout_cells(self) -> None:
        """Build a responsive, keyboard-addressable 16:9 grid."""
        self._gap = max(8, self._sp(16))
        scrollbar_reserve = max(10, self._sp(14))
        available_w = max(1, self._scroll.rect.width - scrollbar_reserve)
        minimum_cell_w = max(self._minimum_target, self._sp(160))
        self._grid_columns = min(
            4,
            max(
                1,
                (available_w + self._gap)
                // (minimum_cell_w + self._gap),
            ),
        )
        self._cell_w = max(
            self._minimum_target,
            (
                available_w
                - self._gap * (self._grid_columns - 1)
            ) // self._grid_columns,
        )
        self._cell_h = max(
            self._minimum_target,
            int(round(self._cell_w * 9 / 16)),
        )
        self._metadata_captions = any(
            self._catalog_entry(path) is not None
            for path in self._cgs
        )
        caption_lines_h = self._caption_line_h
        if self._metadata_captions:
            caption_lines_h += self._caption_subtitle_line_h + self._sp(2)
        self._caption_h = max(
            caption_lines_h + self._sp(10),
            self._sp(40 if self._metadata_captions else 26),
        )
        self._card_h = self._cell_h + self._caption_h

        tracker = self.ctx.state.cg_gallery
        self._cells = []
        for index, path in enumerate(self._cgs):
            row = index // self._grid_columns
            col = index % self._grid_columns
            x = col * (self._cell_w + self._gap)
            y = row * (self._card_h + self._gap)
            self._cells.append((
                pygame.Rect(x, y, self._cell_w, self._cell_h),
                path,
                tracker.is_unlocked(path),
            ))
        if self._cells:
            rows = (
                len(self._cells) + self._grid_columns - 1
            ) // self._grid_columns
            self._content_height = (
                rows * self._card_h
                + max(0, rows - 1) * self._gap
            )
        else:
            self._content_height = self._caption_line_h

    def _card_rect(self, index: int) -> pygame.Rect:
        image_rect = self._cells[index][0]
        return pygame.Rect(
            image_rect.x,
            image_rect.y,
            image_rect.width,
            self._card_h,
        )

    @staticmethod
    def _rounded_thumbnail(
        image: pygame.Surface,
        size: tuple[int, int],
        radius: int,
    ) -> pygame.Surface:
        """Mask image pixels to the card radius before compositing."""
        rounded = pygame.Surface(size, pygame.SRCALPHA)
        rounded.blit(image, (0, 0))
        mask = pygame.Surface(size, pygame.SRCALPHA)
        pygame.draw.rect(
            mask,
            (255, 255, 255, 255),
            mask.get_rect(),
            border_radius=max(0, radius),
        )
        rounded.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        return rounded

    def _draw_content(self, surface: pygame.Surface) -> int:
        if not self._cgs:
            font = self.ctx.fonts.get(18)
            text = self._ellipsize(
                self.ctx.localization.t(
                    "cg_gallery_empty",
                    "（這個遊戲沒有設定 CG。）",
                ),
                font,
                max(1, self._scroll.rect.width - self._sp(18)),
            )
            empty = self.ctx.fonts.render(
                text,
                18,
                self.ctx.theme.text_mute,
            )
            surface.blit(empty, (0, 0))
            return empty.get_height()

        for index, (rect, path, unlocked) in enumerate(self._cells):
            self._draw_cell(
                surface,
                rect,
                path,
                unlocked,
                focused=index == self._focus,
                hovered=index == self._hover_index,
            )
        return self._content_height

    def _draw_cell(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        path: str,
        unlocked: bool,
        *,
        focused: bool,
        hovered: bool,
    ) -> None:
        radius = self.ctx.theme.radius_m
        card_rect = pygame.Rect(0, 0, rect.width, self._card_h)
        card = pygame.Surface(card_rect.size, pygame.SRCALPHA)
        card_painted = self._paint_chrome(
            card,
            card_rect,
            part="card",
            variant="unlocked" if unlocked else "locked",
            enabled=unlocked,
            hovered=hovered,
            focused=focused,
            emphasis=1 if unlocked else 0,
        )
        if not card_painted:
            pygame.draw.rect(
                card,
                (255, 255, 255, 12),
                card_rect,
                border_radius=radius,
            )
        elif not unlocked:
            disabled_veil = pygame.Surface(card_rect.size, pygame.SRCALPHA)
            disabled_veil.fill((*self.ctx.theme.bg_panel[:3], 96))
            card.blit(disabled_veil, (0, 0))
        thumb_rect = pygame.Rect(0, 0, rect.width, rect.height)
        if unlocked:
            entry = self._catalog_entry(path)
            thumbnail_path = (
                entry.thumbnail
                if entry is not None and entry.thumbnail
                else path
            )
            thumb = self.ctx.assets.scaled(
                thumbnail_path,
                (rect.width, rect.height),
                fit="cover",
            )
            card.blit(
                self._rounded_thumbnail(
                    thumb,
                    thumb_rect.size,
                    radius,
                ),
                thumb_rect.topleft,
            )
            caption = entry.title if entry is not None else Path(path).stem
            subtitle = entry.subtitle if entry is not None else ""
            caption_color = (
                self.ctx.theme.text
                if entry is not None
                else self.ctx.theme.text_mute
            )
        else:
            pygame.draw.rect(
                card,
                (*self.ctx.theme.text_dim[:3], 40),
                thumb_rect,
                border_radius=radius,
            )
            question = self.ctx.fonts.render(
                "?",
                40,
                self.ctx.theme.text_dim,
                bold=True,
            )
            card.blit(
                question,
                (
                    (thumb_rect.width - question.get_width()) // 2,
                    (thumb_rect.height - question.get_height()) // 2,
                ),
            )
            caption = self.ctx.localization.t("cg_locked", "未解鎖")
            subtitle = ""
            caption_color = self.ctx.theme.text_mute

        pygame.draw.rect(
            card,
            (
                self.ctx.theme.border
                if unlocked
                else self.ctx.theme.border_soft
            ),
            thumb_rect,
            width=max(1, self._sp(2)),
            border_radius=radius,
        )
        entry = self._catalog_entry(path)
        caption_bold = unlocked and entry is not None
        caption_font = self.ctx.fonts.get(
            self._caption_font_size,
            bold=caption_bold,
        )
        caption_pad = self._sp(8)
        caption = self._ellipsize(
            caption,
            caption_font,
            max(1, rect.width - caption_pad * 2),
        )
        caption_surface = self.ctx.fonts.render(
            caption,
            self._caption_font_size,
            caption_color,
            bold=caption_bold,
        )
        if subtitle:
            subtitle_font = self.ctx.fonts.get(
                self._caption_subtitle_font_size,
            )
            subtitle = self._ellipsize(
                subtitle,
                subtitle_font,
                max(1, rect.width - caption_pad * 2),
            )
            subtitle_surface = self.ctx.fonts.render(
                subtitle,
                self._caption_subtitle_font_size,
                self.ctx.theme.text_mute,
            )
            lines_h = (
                caption_surface.get_height()
                + self._sp(2)
                + subtitle_surface.get_height()
            )
            caption_y = rect.height + (self._caption_h - lines_h) // 2
        else:
            subtitle_surface = None
            caption_y = (
                rect.height
                + (self._caption_h - caption_surface.get_height()) // 2
            )
        card.blit(caption_surface, (caption_pad, caption_y))
        if subtitle_surface is not None:
            card.blit(
                subtitle_surface,
                (
                    caption_pad,
                    caption_y + caption_surface.get_height() + self._sp(2),
                ),
            )

        border_color = None
        border_width = 0
        if focused:
            border_color = self.ctx.theme.accent_warm
            border_width = max(3, self._sp(3))
        elif hovered:
            border_color = (
                self.ctx.theme.accent
                if unlocked
                else self.ctx.theme.border_strong
            )
            border_width = max(2, self._sp(2))
        if border_color is not None:
            pygame.draw.rect(
                card,
                border_color,
                card_rect,
                width=border_width,
                border_radius=radius,
            )
        surface.blit(card, rect.topleft)

    # ---------- interaction --------------------------------------------------

    @staticmethod
    def _navigation_vector(inp) -> tuple[int, int]:
        horizontal = (
            -1
            if inp.pressed(InputAction.NAV_LEFT)
            else 1
            if inp.pressed(InputAction.NAV_RIGHT)
            else 0
        )
        vertical = (
            -1
            if inp.pressed(InputAction.NAV_UP)
            else 1
            if inp.pressed(InputAction.NAV_DOWN)
            else 0
        )
        return horizontal, vertical

    def _move_focus(self, horizontal: int, vertical: int) -> None:
        if not self._cells:
            self._focus = -1
            return
        if not (0 <= self._focus < len(self._cells)):
            self._focus = 0
            self._ensure_focus_visible()
            return

        index = self._focus
        row, col = divmod(index, self._grid_columns)
        target = index
        if horizontal < 0 and col > 0:
            target = index - 1
        elif horizontal > 0:
            candidate = index + 1
            if (
                col + 1 < self._grid_columns
                and candidate < len(self._cells)
            ):
                target = candidate
        elif vertical < 0 and row > 0:
            target = index - self._grid_columns
        elif vertical > 0:
            candidate = index + self._grid_columns
            if candidate < len(self._cells):
                target = candidate
            elif row < (
                (len(self._cells) - 1) // self._grid_columns
            ):
                target = len(self._cells) - 1
        self._focus = target
        self._ensure_focus_visible()

    def _ensure_focus_visible(self) -> None:
        if not (0 <= self._focus < len(self._cells)):
            self._scroll.scroll_y = max(
                0,
                min(
                    self._scroll.scroll_y,
                    max(0, self._content_height - self._scroll.rect.height),
                ),
            )
            return
        card = self._card_rect(self._focus)
        padding = self._sp(6)
        top = max(0, card.top - padding)
        bottom = card.bottom + padding
        if top < self._scroll.scroll_y:
            self._scroll.scroll_y = top
        elif bottom > self._scroll.scroll_y + self._scroll.rect.height:
            self._scroll.scroll_y = bottom - self._scroll.rect.height
        self._scroll.scroll_y = max(
            0,
            min(
                self._scroll.scroll_y,
                max(0, self._content_height - self._scroll.rect.height),
            ),
        )

    def _cell_index_at(self, mouse_pos: tuple[int, int]) -> int | None:
        if not self._scroll.rect.collidepoint(mouse_pos):
            return None
        content_point = (
            mouse_pos[0] - self._scroll.rect.x,
            mouse_pos[1] - self._scroll.rect.y + self._scroll.scroll_y,
        )
        for index, (_rect, _path, _unlocked) in enumerate(self._cells):
            if self._card_rect(index).collidepoint(content_point):
                return index
        return None

    def _open_focused(self) -> bool:
        if not (0 <= self._focus < len(self._cells)):
            return False
        _rect, path, unlocked = self._cells[self._focus]
        if not unlocked:
            return False
        self._fullscreen = path
        return True

    def update(self, dt: float, inp) -> None:
        if self._fullscreen is not None:
            # Confirm/A, cancel/B, or a click all return to the focused card.
            if inp.cancel or inp.confirm or inp.mouse_clicked:
                self._fullscreen = None
            return
        if inp.cancel:
            self.close_btn.activate()
            return

        horizontal, vertical = self._navigation_vector(inp)
        if horizontal or vertical:
            self._move_focus(horizontal, vertical)
        if inp.confirm and self._open_focused():
            return

        close_clicked = (
            inp.mouse_clicked
            and self.close_btn.rect.collidepoint(inp.mouse_pos)
        )
        self.close_btn.update(dt, inp)
        if close_clicked:
            return

        self._scroll.update(dt, inp)
        hovered = self._cell_index_at(inp.mouse_pos)
        self._hover_index = hovered if hovered is not None else -1
        if inp.mouse_clicked:
            self._handle_grid_click(inp.mouse_pos)

    def _handle_grid_click(self, mouse_pos: tuple[int, int]) -> None:
        """Translate a viewport click into a CG cell and open it fullscreen."""
        index = self._cell_index_at(mouse_pos)
        if index is None:
            return
        self._focus = index
        self._ensure_focus_visible()
        self._open_focused()

    # ---------- drawing ------------------------------------------------------

    def draw(self, surface: pygame.Surface) -> None:
        if self._fullscreen is not None:
            self._draw_fullscreen(surface)
            return
        viewport = surface.get_rect()
        if not self._paint_chrome(
            surface,
            viewport,
            part="backdrop",
            variant="overlay",
        ):
            veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
            veil.fill((0, 0, 0, 160))
            surface.blit(veil, (0, 0))
        self._panel.draw(surface)
        surface.blit(self._title_surface, self._title_rect.topleft)
        surface.blit(self._counter_surface, self._counter_rect.topleft)
        divider_h = max(1, self._sp(6))
        divider_rect = pygame.Rect(
            self._panel_rect.x + self._sp(24),
            self._header_divider_y - divider_h // 2,
            max(1, self._panel_rect.width - self._sp(48)),
            divider_h,
        )
        if not self._paint_chrome(
            surface,
            divider_rect,
            part="divider",
            variant="header",
        ):
            pygame.draw.line(
                surface,
                self.ctx.theme.border_soft,
                (
                    self._panel_rect.x + self._sp(24),
                    self._header_divider_y,
                ),
                (
                    self._panel_rect.right - self._sp(24),
                    self._header_divider_y,
                ),
            )
        self.close_btn.draw(surface)
        self._scroll.draw(surface)
        footer_painted = self._paint_chrome(
            surface,
            self._footer_rect,
            part="footer",
            variant="hint",
        )
        if not footer_painted:
            pygame.draw.line(
                surface,
                self.ctx.theme.border_soft,
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
        footer_text = (
            f"{bindings.primary_display(InputAction.NAV_LEFT)}／"
            f"{bindings.primary_display(InputAction.NAV_RIGHT)}／"
            f"{bindings.primary_display(InputAction.NAV_UP)}／"
            f"{bindings.primary_display(InputAction.NAV_DOWN)} 選擇　"
            f"{bindings.primary_display(InputAction.CONFIRM_ADVANCE)} 檢視　"
            f"滑鼠滾輪捲動　{bindings.primary_display(InputAction.BACK)} 返回"
        )
        max_footer_w = self._footer_rect.width - self._sp(36)
        if footer_font.size(footer_text)[0] > max_footer_w:
            suffix = "…"
            while (
                footer_text
                and footer_font.size(
                    footer_text.rstrip() + suffix,
                )[0] > max_footer_w
            ):
                footer_text = footer_text[:-1]
            footer_text = footer_text.rstrip() + suffix
        hint = footer_font.render(
            footer_text,
            True,
            self.ctx.theme.text_mute,
        )
        surface.blit(
            hint,
            (
                self._footer_rect.x + self._sp(10),
                self._footer_rect.centery - hint.get_height() // 2,
            ),
        )

    def _draw_fullscreen(self, surface: pygame.Surface) -> None:
        """Contain the CG above an optional authored memory caption."""
        sw, sh = surface.get_size()
        backdrop = pygame.Surface((sw, sh), pygame.SRCALPHA)
        backdrop.fill((0, 0, 0, 255))
        surface.blit(backdrop, (0, 0))

        entry = self._catalog_entry(self._fullscreen)
        edge = max(12, self._sp(20))
        hint_pad_x = max(10, self._sp(14))
        hint_pad_y = max(6, self._sp(8))
        hint_box_h = max(
            self._minimum_target,
            self._hint_line_h + hint_pad_y * 2,
        )
        info_pad_x = max(10, self._sp(16))
        info_pad_y = max(
            6,
            self._sp(6 if sh <= 450 else 8),
        )
        text_hint_gap = max(8, self._sp(14))
        bottom_width = max(1, sw - edge * 2)

        hint_font = self.ctx.fonts.get(self._hint_font_size)
        max_hint_box_w = (
            max(
                self._minimum_target,
                int(round(bottom_width * 0.38)),
            )
            if entry is not None
            else bottom_width
        )
        max_hint_text_w = max(
            1,
            max_hint_box_w - hint_pad_x * 2,
        )
        bindings = BindingProfile(
            getattr(self.ctx.config, "input_bindings", {}),
        )
        hint_text = self._ellipsize(
            f"點擊或按 {bindings.primary_display(InputAction.BACK)} 返回",
            hint_font,
            max_hint_text_w,
        )
        hint = self.ctx.fonts.render(
            hint_text,
            self._hint_font_size,
            self.ctx.theme.text_mute,
        )
        hint_box_w = min(
            max_hint_box_w,
            max(
                self._minimum_target,
                hint.get_width() + hint_pad_x * 2,
            ),
        )

        title_line_h = self.ctx.fonts.get(
            self._fullscreen_title_font_size,
            bold=True,
        ).get_linesize()
        subtitle_line_h = self.ctx.fonts.get(
            self._fullscreen_subtitle_font_size,
        ).get_linesize()
        description_font = self.ctx.fonts.get(
            self._fullscreen_meta_font_size,
        )
        description_line_h = description_font.get_linesize()
        text_width = max(
            1,
            bottom_width
            - info_pad_x * 2
            - hint_box_w
            - text_hint_gap,
        )
        description_limit = (
            0
            if sh <= 450
            else (1 if sh < 900 else 2)
        )
        description_lines = (
            self._wrap_ellipsized(
                entry.description,
                description_font,
                text_width,
                description_limit,
            )
            if entry is not None
            else []
        )
        metadata_h = 0
        if entry is not None:
            metadata_h = title_line_h
            if entry.subtitle:
                metadata_h += self._sp(2) + subtitle_line_h
            if description_lines:
                metadata_h += (
                    self._sp(3)
                    + len(description_lines) * description_line_h
                )
            metadata_h += info_pad_y * 2
        bottom_h = max(hint_box_h, metadata_h)
        hint_gap = max(8, self._sp(10))
        image_bottom = sh - edge - bottom_h - hint_gap
        self._fullscreen_image_rect = pygame.Rect(
            edge,
            edge,
            max(1, sw - edge * 2),
            max(1, image_bottom - edge),
        )
        image = self.ctx.assets.scaled(
            self._fullscreen,
            self._fullscreen_image_rect.size,
            fit="contain",
        )
        surface.blit(image, self._fullscreen_image_rect.topleft)

        if entry is not None:
            self._fullscreen_info_rect = pygame.Rect(
                edge,
                sh - edge - bottom_h,
                bottom_width,
                bottom_h,
            )
            info_backdrop = pygame.Surface(
                self._fullscreen_info_rect.size,
                pygame.SRCALPHA,
            )
            pygame.draw.rect(
                info_backdrop,
                (0, 0, 0, 208),
                info_backdrop.get_rect(),
                border_radius=self.ctx.theme.radius_m,
            )
            pygame.draw.rect(
                info_backdrop,
                self.ctx.theme.border_soft,
                info_backdrop.get_rect(),
                width=max(1, self._sp(1)),
                border_radius=self.ctx.theme.radius_m,
            )
            surface.blit(info_backdrop, self._fullscreen_info_rect.topleft)
            self._fullscreen_hint_rect = pygame.Rect(
                self._fullscreen_info_rect.right - info_pad_x - hint_box_w,
                self._fullscreen_info_rect.centery - hint_box_h // 2,
                hint_box_w,
                hint_box_h,
            )

            text_x = self._fullscreen_info_rect.x + info_pad_x
            text_y = self._fullscreen_info_rect.y + info_pad_y
            title_font = self.ctx.fonts.get(
                self._fullscreen_title_font_size,
                bold=True,
            )
            title_text = self._ellipsize(
                entry.title,
                title_font,
                text_width,
            )
            title = self.ctx.fonts.render(
                title_text,
                self._fullscreen_title_font_size,
                self.ctx.theme.text,
                bold=True,
            )
            surface.blit(title, (text_x, text_y))
            text_y += title.get_height()

            if entry.subtitle:
                text_y += self._sp(2)
                subtitle_font = self.ctx.fonts.get(
                    self._fullscreen_subtitle_font_size,
                )
                subtitle_text = self._ellipsize(
                    entry.subtitle,
                    subtitle_font,
                    text_width,
                )
                subtitle = self.ctx.fonts.render(
                    subtitle_text,
                    self._fullscreen_subtitle_font_size,
                    self.ctx.theme.accent_warm,
                )
                surface.blit(subtitle, (text_x, text_y))
                text_y += subtitle.get_height()
            if description_lines:
                text_y += self._sp(3)
                for line in description_lines:
                    description = self.ctx.fonts.render(
                        line,
                        self._fullscreen_meta_font_size,
                        self.ctx.theme.text_mute,
                    )
                    surface.blit(description, (text_x, text_y))
                    text_y += description.get_height()
        else:
            self._fullscreen_info_rect = None
            self._fullscreen_hint_rect = pygame.Rect(
                sw - edge - hint_box_w,
                sh - edge - hint_box_h,
                hint_box_w,
                hint_box_h,
            )
        hint_backdrop = pygame.Surface(
            self._fullscreen_hint_rect.size,
            pygame.SRCALPHA,
        )
        pygame.draw.rect(
            hint_backdrop,
            (0, 0, 0, 180),
            hint_backdrop.get_rect(),
            border_radius=self.ctx.theme.radius_m,
        )
        pygame.draw.rect(
            hint_backdrop,
            self.ctx.theme.border_soft,
            hint_backdrop.get_rect(),
            width=max(1, self._sp(1)),
            border_radius=self.ctx.theme.radius_m,
        )
        surface.blit(hint_backdrop, self._fullscreen_hint_rect.topleft)
        hint_x = (
            self._fullscreen_hint_rect.x
            + (self._fullscreen_hint_rect.width - hint.get_width()) // 2
        )
        hint_y = (
            self._fullscreen_hint_rect.y
            + (self._fullscreen_hint_rect.height - hint.get_height()) // 2
        )
        surface.blit(hint, (hint_x, hint_y))

    # ---------- headless inspection -----------------------------------------

    def _describe_entry(self, asset: str) -> dict:
        """Return player-visible metadata without leaking locked spoilers."""
        unlocked = self.ctx.state.cg_gallery.is_unlocked(asset)
        row: dict = {"unlocked": unlocked}
        if not unlocked:
            # Filenames frequently contain route, climax, or ending names.
            # The list position is enough for deterministic navigation; do
            # not reveal the canonical path until the memory is unlocked.
            return row
        row["asset"] = asset
        entry = self._catalog_entry(asset)
        if entry is not None:
            row.update({
                "title": entry.title,
                "subtitle": entry.subtitle,
                "description": entry.description,
                "character_ids": list(entry.character_ids),
                "chapter_id": entry.chapter_id,
                "order": entry.order,
                "hidden": entry.hidden,
                "thumbnail": entry.thumbnail,
            })
        else:
            row["title"] = Path(asset).stem
        return row

    def describe(self) -> dict:
        focused_asset = (
            self._cells[self._focus][1]
            if 0 <= self._focus < len(self._cells)
            else None
        )
        fullscreen_asset = getattr(self, "_fullscreen", None)
        focused_unlocked = (
            focused_asset is not None
            and self.ctx.state.cg_gallery.is_unlocked(focused_asset)
        )
        fullscreen_unlocked = (
            fullscreen_asset is not None
            and self.ctx.state.cg_gallery.is_unlocked(fullscreen_asset)
        )
        return {
            "scene": "CGGalleryScene",
            "unlocked": sorted(self.ctx.state.cg_gallery.unlocked),
            "unlocked_count": len(self.ctx.state.cg_gallery.unlocked),
            "total": len(getattr(self, "_cgs", [])),
            "fullscreen": (
                fullscreen_asset if fullscreen_unlocked else None
            ),
            "fullscreen_entry": (
                self._describe_entry(fullscreen_asset)
                if fullscreen_asset is not None
                else None
            ),
            "focused_index": (
                self._focus
                if 0 <= self._focus < len(self._cells)
                else None
            ),
            "focused": focused_asset if focused_unlocked else None,
            "focused_entry": (
                self._describe_entry(focused_asset)
                if focused_asset is not None
                else None
            ),
            "entries": [
                self._describe_entry(asset)
                for asset in getattr(self, "_cgs", [])
            ],
            "columns": getattr(self, "_grid_columns", 0),
        }
