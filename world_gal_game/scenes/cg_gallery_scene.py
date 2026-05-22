"""CG gallery overlay (WP-A).

A scrollable thumbnail grid of the pack's event illustrations (CGs).
Unlocked CGs (recorded in ``state.cg_gallery``) render their thumbnail;
the rest show a dark "locked" placeholder. Clicking an unlocked thumbnail
opens a fullscreen view of that CG; Esc or a click returns to the grid.

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
from ..ui.widgets import Button, Panel, ScrollArea

# Image suffixes we treat as candidate CGs when enumerating the directory.
_CG_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")


class CGGalleryScene(Scene):
    def __init__(self, ctx: SceneContext):
        super().__init__(ctx)
        self.is_overlay = True
        # Layout constants for the thumbnail grid.
        self._cell_w = 224
        self._cell_h = 126
        self._gap = 16

    def enter(self, *, on_close=None, **_) -> None:
        self.on_close = on_close
        # Path of the CG currently shown fullscreen, or None for the grid.
        self._fullscreen: str | None = None
        # (rect-in-content, cg_path, unlocked) computed each draw, consumed
        # by update() for click hit-testing inside the scroll viewport.
        self._cells: list[tuple[pygame.Rect, str, bool]] = []
        self._cgs = self._discover_cgs()

        sw, sh = self.ctx.screen_size
        self._panel_rect = pygame.Rect(120, 60, sw - 240, sh - 120)
        self._panel = Panel(self._panel_rect, self.ctx.theme,
                            fill=(*self.ctx.theme.bg_overlay[:3], 235),
                            border=self.ctx.theme.border_strong,
                            radius=self.ctx.theme.radius_l, border_width=2)
        self.close_btn = Button(
            pygame.Rect(self._panel_rect.right - 120 - 16,
                        self._panel_rect.y + 16, 120, 36),
            self.ctx.localization.t("close", "關閉"),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
            font_size=15, style="ghost",
            on_click=(lambda: on_close() if on_close else None),
        )
        self._scroll = ScrollArea(
            pygame.Rect(self._panel_rect.x + 30, self._panel_rect.y + 70,
                        self._panel_rect.width - 60,
                        self._panel_rect.height - 100),
            fonts=self.ctx.fonts, theme=self.ctx.theme,
        )
        self._scroll.set_drawer(self._draw_content)

    # ---------- CG discovery -------------------------------------------------

    def _discover_cgs(self) -> list[str]:
        """Return the full set of CG asset paths, sorted.

        Enumerates ``assets/cgs/`` (resolved via the asset manager so it
        works against the pack root and the legacy fallback), then unions
        with everything the player has already unlocked — so a CG stored
        elsewhere still shows up. Degrades to just the unlocked set if the
        directory can't be found or read.
        """
        unlocked = set(self.ctx.state.cg_gallery.unlocked)
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

    def _draw_content(self, surface: pygame.Surface) -> int:
        self._cells = []
        tracker = self.ctx.state.cg_gallery
        if not self._cgs:
            empty = self.ctx.fonts.render(
                self.ctx.localization.t(
                    "cg_gallery_empty", "（這個遊戲沒有設定 CG。）"),
                18, self.ctx.theme.text_mute)
            surface.blit(empty, (0, 0))
            return empty.get_height()

        avail_w = self._scroll.rect.width - 14  # leave room for scrollbar
        cols = max(1, (avail_w + self._gap) // (self._cell_w + self._gap))
        x = 0
        y = 0
        col = 0
        for path in self._cgs:
            unlocked = tracker.is_unlocked(path)
            cell_rect = pygame.Rect(x, y, self._cell_w, self._cell_h)
            self._draw_cell(surface, cell_rect, path, unlocked)
            # Record in content-space for click hit-testing in update().
            self._cells.append((cell_rect.copy(), path, unlocked))
            col += 1
            if col >= cols:
                col = 0
                x = 0
                y += self._cell_h + self._gap + 18  # +caption row
            else:
                x += self._cell_w + self._gap
        # Final row height if the last row was partial.
        rows = (len(self._cgs) + cols - 1) // cols
        total_h = rows * (self._cell_h + self._gap + 18)
        return total_h

    def _draw_cell(self, surface: pygame.Surface, rect: pygame.Rect,
                   path: str, unlocked: bool) -> None:
        radius = self.ctx.theme.radius_m
        if unlocked:
            thumb = self.ctx.assets.scaled(path, (rect.width, rect.height),
                                           fit="cover")
            surface.blit(thumb, rect.topleft)
            pygame.draw.rect(surface, self.ctx.theme.border, rect, width=2,
                             border_radius=radius)
            caption = Path(path).stem
            cap = self.ctx.fonts.render(caption[:28], 13,
                                        self.ctx.theme.text_mute)
            surface.blit(cap, (rect.x + 2, rect.bottom + 4))
        else:
            cell = pygame.Surface(rect.size, pygame.SRCALPHA)
            pygame.draw.rect(cell, (*self.ctx.theme.text_dim[:3], 40),
                             cell.get_rect(), border_radius=radius)
            pygame.draw.rect(cell, (*self.ctx.theme.border_soft[:3], 160),
                             cell.get_rect(), width=2, border_radius=radius)
            q = self.ctx.fonts.render("?", 40, self.ctx.theme.text_dim,
                                      bold=True)
            cell.blit(q, ((rect.width - q.get_width()) // 2,
                          (rect.height - q.get_height()) // 2))
            surface.blit(cell, rect.topleft)
            locked_lbl = self.ctx.fonts.render(
                self.ctx.localization.t("cg_locked", "未解鎖"), 13,
                self.ctx.theme.text_dim)
            surface.blit(locked_lbl, (rect.x + 2, rect.bottom + 4))

    # ---------- interaction --------------------------------------------------

    def update(self, dt: float, inp) -> None:
        if self._fullscreen is not None:
            # Any cancel or click returns to the grid.
            if inp.cancel or inp.mouse_clicked:
                self._fullscreen = None
            return
        if inp.cancel and self.on_close:
            self.on_close()
            return
        self.close_btn.update(dt, inp)
        self._scroll.update(dt, inp)
        if inp.mouse_clicked:
            self._handle_grid_click(inp.mouse_pos)

    def _handle_grid_click(self, mouse_pos: tuple[int, int]) -> None:
        """Translate a viewport click into a CG cell and open it fullscreen."""
        if not self._scroll.rect.collidepoint(mouse_pos):
            return
        # Map screen point into scroll content coordinates.
        cx = mouse_pos[0] - self._scroll.rect.x
        cy = mouse_pos[1] - self._scroll.rect.y + self._scroll.scroll_y
        for rect, path, unlocked in self._cells:
            if unlocked and rect.collidepoint((cx, cy)):
                self._fullscreen = path
                return

    # ---------- drawing ------------------------------------------------------

    def draw(self, surface: pygame.Surface) -> None:
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 160))
        surface.blit(veil, (0, 0))
        if self._fullscreen is not None:
            self._draw_fullscreen(surface)
            return
        self._panel.draw(surface)
        title = self.ctx.fonts.render(
            self.ctx.localization.t("cg_gallery", "CG鑑賞"),
            self.ctx.config.font_size_header,
            self.ctx.theme.accent, bold=True,
        )
        surface.blit(title, (self._panel_rect.x + 32, self._panel_rect.y + 28))
        # unlocked / total counter
        unlocked_n = sum(1 for p in self._cgs
                         if self.ctx.state.cg_gallery.is_unlocked(p))
        total_n = len(self._cgs)
        if total_n:
            cnt = self.ctx.fonts.render(
                f"{unlocked_n} / {total_n}", 18, self.ctx.theme.accent_warm,
            )
            surface.blit(cnt, (self._panel_rect.x + 32,
                               self._panel_rect.y + 70))
        self.close_btn.draw(surface)
        self._scroll.draw(surface)

    def _draw_fullscreen(self, surface: pygame.Surface) -> None:
        """Fill the screen with the selected CG (contain-fit) + a hint."""
        sw, sh = surface.get_size()
        backdrop = pygame.Surface((sw, sh), pygame.SRCALPHA)
        backdrop.fill((0, 0, 0, 235))
        surface.blit(backdrop, (0, 0))
        img = self.ctx.assets.scaled(self._fullscreen, (sw, sh), fit="contain")
        surface.blit(img, (0, 0))
        hint = self.ctx.fonts.render(
            self.ctx.localization.t("cg_close_hint", "點擊或按 Esc 返回"),
            15, self.ctx.theme.text_mute)
        surface.blit(hint, (sw - hint.get_width() - 24, sh - 36))

    # ---------- headless inspection -----------------------------------------

    def describe(self) -> dict:
        return {
            "scene": "CGGalleryScene",
            "unlocked": sorted(self.ctx.state.cg_gallery.unlocked),
            "unlocked_count": len(self.ctx.state.cg_gallery.unlocked),
            "total": len(getattr(self, "_cgs", [])),
            "fullscreen": getattr(self, "_fullscreen", None),
        }
