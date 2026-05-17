"""Debug overlay widget for dev mode.

Translucent top-right panel showing live game state. Toggle with F1.
Only used when WGG_DEV=1; zero overhead in production (never instantiated).
"""
from __future__ import annotations

import pygame

from .base import Widget
from ..fonts import FontRegistry
from ..theme import Theme


# Number of flag entries shown before truncation.
_MAX_FLAGS = 20
# Monospace font size used throughout the overlay.
_FONT_SIZE = 14


class DebugOverlay(Widget):
    """Translucent top-right overlay showing live game state.

    Toggle visibility with F1. Only rendered when ctx.config.dev_mode.
    Updates every frame; data pulled from state passed to set_state().
    """

    def __init__(self, rect: pygame.Rect, *, fonts: FontRegistry,
                 theme: Theme) -> None:
        super().__init__(rect)
        self.fonts = fonts
        self.theme = theme
        self.visible = False   # starts hidden; F1 toggles
        self._lines: list[str] = []
        self._fps: float = 0.0
        self._clock = pygame.time.Clock()

    # ------------------------------------------------------------------

    def set_state(self, state) -> None:
        """Rebuild the text lines from current GameState each frame."""
        lines: list[str] = []

        # --- FPS ---
        lines.append(f"FPS: {self._fps:.1f}")

        # --- Scene stack: best-effort; state doesn't own the manager ---
        # Caller may pass a richer container; we just show what we can.
        lines.append("")

        # --- Location ---
        loc = getattr(getattr(state, "map", None), "current", None)
        if loc is not None:
            lines.append(f"Location: {loc.id}  {loc.name}")
        else:
            lines.append("Location: —")

        # --- Time ---
        t = getattr(state, "time", None)
        if t is not None:
            try:
                lines.append(
                    f"Time: Day {t.day} {t.day_of_week.label} {t.time_of_day.label}"
                )
            except Exception:
                lines.append(f"Time: {t!r}")
        else:
            lines.append("Time: —")

        lines.append("")

        # --- Flags (top 20 by name) ---
        flags = dict(getattr(getattr(state, "events", None), "flags", {}))
        if flags:
            lines.append(f"Flags ({len(flags)}):")
            for k in sorted(flags)[:_MAX_FLAGS]:
                lines.append(f"  {k}: {flags[k]}")
            if len(flags) > _MAX_FLAGS:
                lines.append(f"  ... +{len(flags) - _MAX_FLAGS} more")
        else:
            lines.append("Flags: (none)")

        lines.append("")

        # --- Resources ---
        res_vals = dict(getattr(getattr(state, "resources", None), "values", {}))
        if res_vals:
            lines.append("Resources:")
            for rid, val in sorted(res_vals.items()):
                lines.append(f"  {rid}: {val}")
        else:
            lines.append("Resources: (none)")

        lines.append("")

        # --- Affection ---
        aff_chars = getattr(getattr(state, "affection", None), "characters", {})
        if aff_chars:
            lines.append("Affection:")
            for cid, ca in sorted(aff_chars.items()):
                primary = ca.stats.get("affection", 0)
                label = state.affection.level_label(cid)
                lines.append(f"  {cid}: {primary} ({label})")
        else:
            lines.append("Affection: (none)")

        self._lines = lines

    def toggle(self) -> None:
        """Flip visibility."""
        self.visible = not self.visible

    def update(self, dt: float, inp) -> None:
        # Track FPS using elapsed dt; avoid division by zero.
        if dt > 0:
            self._fps = 1.0 / dt

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return

        font = self.fonts.get(_FONT_SIZE)
        line_h = font.get_linesize()
        pad = 8

        # Height: enough for all lines.
        content_h = len(self._lines) * line_h + pad * 2
        panel_h = min(content_h, self.rect.height)

        # Draw translucent background.
        panel = pygame.Surface((self.rect.width, panel_h), pygame.SRCALPHA)
        panel.fill((0, 0, 0, 200))
        pygame.draw.rect(panel, (255, 255, 255, 40), panel.get_rect(),
                         width=1, border_radius=4)

        # Render lines clipped to panel height.
        y = pad
        for line in self._lines:
            if y + line_h > panel_h - pad:
                # Draw ellipsis to signal truncation.
                surf = font.render("...", True, (180, 180, 180))
                panel.blit(surf, (pad, y))
                break
            color = (200, 255, 200) if line.startswith("FPS") else (220, 220, 220)
            if line.endswith(":") and not line.startswith(" "):
                color = (255, 220, 100)
            surf = font.render(line, True, color)
            panel.blit(surf, (pad, y))
            y += line_h

        surface.blit(panel, self.rect.topleft)
