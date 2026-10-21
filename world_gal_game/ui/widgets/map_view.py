"""A keyboard-accessible spatial destination map.

The legacy mode auto-scales graph nodes inside a themed frame. Packs may opt
into an illustrated, fixed-coordinate canvas and place selected off-campus
locations in directional thumbnail cards around its perimeter. Both modes use
the same focus, preview callback, and travel-confirmation flow.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Callable, Literal

import pygame

from .base import Widget
from ...core.spatial_map import EdgeDestination, SpatialMapPresentation
from ..fonts import FontRegistry
from ..input_bindings import InputAction
from ..motion import MotionPolicy, coerce_motion_policy
from ..system_chrome import ChromeRequest
from ..theme import Theme

if TYPE_CHECKING:
    from ..assets import AssetManager
    from ..system_chrome import SystemChromeHost


class MapView(Widget):
    NODE_WIDTH = 160
    NODE_HEIGHT = 44
    EDGE_CARD_WIDTH = 176
    EDGE_CARD_HEIGHT = 96
    MIN_HIT_SIZE = 44
    FEEDBACK_SPEED = 12.0
    LEGEND_FONT_SIZE = 16
    LEGEND_KINDS = (
        "story",
        "current",
        "ordinary",
        "off_campus",
        "focus",
    )

    def __init__(self, rect: pygame.Rect, *, fonts: FontRegistry, theme: Theme,
                 on_click: Callable[[str], None] | None = None,
                 assets: "AssetManager | None" = None,
                 chrome: "SystemChromeHost | None" = None,
                 reduce_motion: bool = False,
                 motion: MotionPolicy | None = None,
                 density: float | None = None,
                 legend_labels: Mapping[str, str] | None = None):
        super().__init__(rect)
        self.fonts = fonts
        self.theme = theme
        self.on_click = on_click
        self.assets = assets
        self.chrome = chrome
        self._legend_labels = {
            kind: str(legend_labels[kind])
            for kind in self.LEGEND_KINDS
            if legend_labels is not None and kind in legend_labels
        }
        inferred_density = getattr(fonts, "scale", 1.0)
        # Keep hit geometry, cards, portraits, spacing, and FontRegistry glyphs
        # on one density authority.  An old 1.5 cap made 4K text scale to 3x
        # while its containers stayed at 1.5x.
        self.density = max(
            0.75,
            float(inferred_density if density is None else density),
        )
        self._hit_size = max(
            self.MIN_HIT_SIZE,
            int(round(self.MIN_HIT_SIZE * self.density)),
        )
        self._labeled_width = max(
            self._hit_size,
            int(round(self.NODE_WIDTH * self.density)),
        )
        self._label_size = 16
        self._sub_label_size = 13
        label_h = self.fonts.get(
            self._label_size,
            bold=True,
        ).get_linesize()
        sub_label_h = self.fonts.get(
            self._sub_label_size,
        ).get_linesize()
        self._labeled_height = max(
            self._hit_size,
            label_h + sub_label_h + int(round(10 * self.density)),
        )
        # Story markers may carry the lead character's existing portrait.
        # The slightly wider chip keeps the same two-line information density
        # while making heroine identity readable before the player opens the
        # full travel sheet.
        self._portrait_size = max(
            36,
            int(round(42 * self.density)),
        )
        self._portrait_labeled_width = max(
            self._labeled_width,
            int(round(196 * self.density)),
        )
        self._portrait_labeled_height = max(
            self._labeled_height,
            self._portrait_size + int(round(10 * self.density)),
        )
        self._edge_card_width = max(
            self._hit_size,
            int(round(self.EDGE_CARD_WIDTH * self.density)),
        )
        self._edge_card_height = max(
            self._hit_size,
            int(round(self.EDGE_CARD_HEIGHT * self.density)),
        )
        self._ordinary_pin_radius = max(
            6,
            int(round(7 * self.density)),
        )
        self._radius_m = max(
            1,
            int(round(self.theme.radius_m * self.density)),
        )
        self._radius_l = max(
            1,
            int(round(self.theme.radius_l * self.density)),
        )
        info_name_h = self.fonts.get(16, bold=True).get_linesize()
        info_desc_h = self.fonts.get(13).get_linesize()
        self._information_strip_height = max(
            self._hit_size,
            info_name_h + info_desc_h + int(round(12 * self.density)),
        )
        # The illustrated map intentionally has no ambient pan, marker pulse,
        # or other continuous decoration. Its only motion is finite
        # hover/focus feedback, governed by the live accessibility preference.
        self.motion = coerce_motion_policy(
            motion,
            reduce_motion=reduce_motion,
        )
        self.nodes: list[dict] = []        # {id, name, region, x, y, visited, accessible, reachable}
        self.current_id: str | None = None
        self.reachable_ids: set[str] = set()
        self.presentation: SpatialMapPresentation | None = None
        self._node_rects: dict[str, pygame.Rect] = {}
        self._edge_specs: dict[str, EdgeDestination] = {}
        self._show_legend = False
        self._legend_rect = pygame.Rect(0, 0, 0, 0)
        self._legend_item_rects: dict[str, pygame.Rect] = {}
        self._canvas_rect = pygame.Rect(self.rect)
        self._marker_area_rect = pygame.Rect(self.rect)
        self._information_strip_rect = pygame.Rect(0, 0, 0, 0)
        self._hover_id: str | None = None
        # Pointer hover is transient; keyboard/gamepad focus must remain
        # visible after the pointer moves away or stops moving.
        self._focused_id: str | None = None
        # One finite [0, 1] visual feedback value per visible destination.
        # Geometry and hit targets never depend on it. The semantic focus ring
        # remains immediate; this value only eases decorative emphasis.
        self._interaction_progress: dict[str, float] = {}

    @property
    def reduce_motion(self) -> bool:
        return self.motion.reduced

    @property
    def focused_id(self) -> str | None:
        """Destination currently targeted by keyboard/gamepad input."""
        return self._focused_id

    def interaction_progress(self, node_id: str) -> float:
        """Finite decorative feedback for deterministic tests and capture."""
        return max(
            0.0,
            min(1.0, float(self._interaction_progress.get(node_id, 0.0))),
        )

    def set_data(self, *, nodes: list[dict], current_id: str | None,
                 reachable_ids: set[str]) -> None:
        self.nodes = nodes
        self.current_id = current_id
        self.reachable_ids = reachable_ids
        self._recompute()
        visible_ids = {
            node["id"]
            for node in self.nodes
            if node["id"] in self._node_rects
        }
        self._interaction_progress = {
            node_id: self._interaction_progress.get(node_id, 0.0)
            for node_id in visible_ids
        }
        if self._focused_id not in self._focusable_ids():
            self._focused_id = None

    def set_presentation(
        self,
        presentation: SpatialMapPresentation | None,
    ) -> None:
        """Apply optional pack-authored art without changing map semantics."""
        self.presentation = presentation
        self._recompute()
        if self._focused_id not in self._focusable_ids():
            self._focused_id = None

    def set_legend_visible(
        self,
        visible: bool,
        *,
        rect: pygame.Rect | None = None,
    ) -> None:
        """Place passive explanatory chrome outside destination hit geometry."""
        self._show_legend = bool(visible and self._legend_labels)
        self._legend_rect = (
            pygame.Rect(rect)
            if self._show_legend and rect is not None
            else pygame.Rect(0, 0, 0, 0)
        )

    @property
    def legend_visible(self) -> bool:
        return self._show_legend

    @property
    def legend_rect(self) -> pygame.Rect:
        """Passive legend geometry; it is never part of destination hit tests."""
        return pygame.Rect(self._legend_rect)

    @property
    def legend_labels(self) -> dict[str, str]:
        """Resolved passive copy injected by the owning scene."""
        return dict(self._legend_labels)

    @property
    def legend_font_size(self) -> int:
        """The passive legend never drops below the readable 16px contract."""
        return self.LEGEND_FONT_SIZE

    @property
    def legend_item_rects(self) -> dict[str, pygame.Rect]:
        """Last painted legend entries, retained for geometry verification."""
        return {
            kind: pygame.Rect(rect)
            for kind, rect in self._legend_item_rects.items()
        }

    @classmethod
    def legend_metrics(
        cls,
        *,
        fonts: FontRegistry,
        labels: Mapping[str, str],
        density: float,
        max_width: int | None = None,
    ) -> tuple[int, int, int]:
        """Return ``(one-line width, required height, row count)``.

        The owning scene uses this before constructing the view so a localized
        legend receives a real layout row instead of losing meaning to
        ellipses. Items wrap as whole icon/label pairs; labels are never
        shortened.
        """
        entries = tuple(
            str(labels[kind])
            for kind in cls.LEGEND_KINDS
            if kind in labels
        )
        if not entries:
            return (0, 0, 0)
        scale = max(0.75, float(density))
        pad_x = max(8, int(round(12 * scale)))
        pad_y = max(6, int(round(8 * scale)))
        item_gap = max(8, int(round(12 * scale)))
        icon_w = max(14, int(round(18 * scale)))
        icon_gap = max(4, int(round(6 * scale)))
        row_gap = max(4, int(round(6 * scale)))
        font = fonts.get(cls.LEGEND_FONT_SIZE, bold=True)
        item_widths = tuple(
            icon_w + icon_gap + font.size(label)[0]
            for label in entries
        )
        one_line_width = (
            pad_x * 2
            + sum(item_widths)
            + item_gap * (len(item_widths) - 1)
        )
        rows = 1
        if max_width is not None and one_line_width > max_width:
            inner_w = max(1, int(max_width) - pad_x * 2)
            rows = 1
            used = 0
            for item_w in item_widths:
                next_w = item_w if used == 0 else used + item_gap + item_w
                if used and next_w > inner_w:
                    rows += 1
                    used = item_w
                else:
                    used = next_w
        row_h = max(icon_w, font.get_linesize())
        required_h = pad_y * 2 + rows * row_h + (rows - 1) * row_gap
        return (one_line_width, required_h, rows)

    @property
    def canvas_rect(self) -> pygame.Rect:
        """The central authored-coordinate canvas (copy, safe for callers)."""
        return pygame.Rect(self._canvas_rect)

    @property
    def marker_area_rect(self) -> pygame.Rect:
        """Canvas area reserved for markers, excluding the detail strip."""
        return pygame.Rect(self._marker_area_rect)

    @property
    def information_strip_rect(self) -> pygame.Rect:
        """Stable bounded area used to reveal one ordinary destination."""
        return pygame.Rect(self._information_strip_rect)

    @property
    def detail_node_id(self) -> str | None:
        """Ordinary node whose full label is currently disclosed."""
        if self._hover_id is not None:
            hovered = self._node(self._hover_id)
            return (
                self._hover_id
                if hovered is not None and self._is_ordinary(hovered)
                else None
            )
        focused = self._node(self._focused_id)
        if focused is not None and self._is_ordinary(focused):
            return self._focused_id
        return None

    @property
    def edge_destination_ids(self) -> frozenset[str]:
        return frozenset(self._edge_specs)

    @property
    def background_available(self) -> bool:
        return bool(
            self.presentation
            and self.presentation.background
            and self.assets is not None
            and self.assets.has_image(self.presentation.background)
        )

    def _recompute(self) -> None:
        if not self.nodes:
            self._node_rects = {}
            self._edge_specs = {}
            self._canvas_rect = pygame.Rect(self.rect)
            self._marker_area_rect = pygame.Rect(self.rect)
            self._information_strip_rect = pygame.Rect(0, 0, 0, 0)
            return

        configured_edges = (
            self.presentation.edge_destinations
            if self.presentation is not None else {}
        )
        visible_ids = {node["id"] for node in self.nodes}
        self._edge_specs = {
            node_id: spec
            for node_id, spec in configured_edges.items()
            if node_id in visible_ids
        }
        self._canvas_rect = self._layout_canvas()
        self._layout_information_strip()
        self._node_rects = self._layout_edge_cards()

        canvas_nodes = [
            node for node in self.nodes
            if node["id"] not in self._edge_specs
        ]
        if not canvas_nodes:
            return
        if self.presentation is not None:
            minx, miny, maxx, maxy = self.presentation.bounds
        else:
            xs = [n["map_x"] for n in canvas_nodes]
            ys = [n["map_y"] for n in canvas_nodes]
            minx, maxx = min(xs), max(xs)
            miny, maxy = min(ys), max(ys)
        dx = max(1, maxx - minx)
        dy = max(1, maxy - miny)
        # Keep every complete hit target inside the illustrated area,
        # including a small focus-ring gutter. Ordinary destinations use
        # compact pins but retain a 44px-or-larger invisible target.
        sizes = [self._node_size(node) for node in canvas_nodes]
        gutter = max(4, int(round(8 * self.density)))
        margin_x = max(width for width, _height in sizes) // 2 + gutter
        margin_y = max(height for _width, height in sizes) // 2 + gutter
        inner = pygame.Rect(
            self._marker_area_rect.x + margin_x,
            self._marker_area_rect.y + margin_y,
            max(1, self._marker_area_rect.width - margin_x * 2),
            max(1, self._marker_area_rect.height - margin_y * 2),
        )
        for n in canvas_nodes:
            norm_x = max(0.0, min(1.0, (n["map_x"] - minx) / dx))
            norm_y = max(0.0, min(1.0, (n["map_y"] - miny) / dy))
            px = inner.x + int(norm_x * inner.width)
            py = inner.y + int(norm_y * inner.height)
            rect = pygame.Rect(0, 0, *self._node_size(n))
            rect.center = (px, py)
            self._node_rects[n["id"]] = rect

    def _node(self, node_id: str | None) -> dict | None:
        if node_id is None:
            return None
        return next(
            (node for node in self.nodes if node["id"] == node_id),
            None,
        )

    @staticmethod
    def _is_ordinary(node: dict) -> bool:
        return node.get("marker_kind") == "ordinary"

    def _node_size(self, node: dict) -> tuple[int, int]:
        if self._is_ordinary(node):
            return self._hit_size, self._hit_size
        if node.get("portrait"):
            return (
                self._portrait_labeled_width,
                self._portrait_labeled_height,
            )
        return self._labeled_width, self._labeled_height

    def _layout_information_strip(self) -> None:
        """Reserve one fixed strip without moving it as focus changes."""
        margin = max(6, int(round(8 * self.density)))
        available_w = max(1, self._canvas_rect.width - margin * 2)
        preferred_w = int(round(560 * self.density))
        minimum_w = min(
            available_w,
            int(round(280 * self.density)),
        )
        strip_w = max(minimum_w, min(preferred_w, available_w))
        strip_h = min(
            self._information_strip_height,
            max(1, self._canvas_rect.height - margin * 2),
        )
        self._information_strip_rect = pygame.Rect(
            0,
            0,
            strip_w,
            strip_h,
        )
        self._information_strip_rect.midbottom = (
            self._canvas_rect.centerx,
            self._canvas_rect.bottom - margin,
        )

        marker_area = pygame.Rect(self._canvas_rect)
        marker_bottom = (
            self._information_strip_rect.top
            - max(4, int(round(6 * self.density)))
        )
        marker_area.height = max(1, marker_bottom - marker_area.y)
        marker_area.inflate_ip(-margin * 2, -margin * 2)
        minimum_h = self._labeled_height + margin * 2
        if marker_area.height < minimum_h:
            # Extremely small hosts keep the strip as an overlay, but retain a
            # usable marker area instead of generating negative geometry.
            marker_area = self._canvas_rect.inflate(-margin * 2, -margin * 2)
        self._marker_area_rect = marker_area

    def _layout_canvas(self) -> pygame.Rect:
        """Reserve only the perimeter used by currently visible edge cards.

        Switching between a guided story layer and the explicit ordinary
        exploration layer may reflow the canvas once.  Hover and focus never
        alter that geometry.  Reserving rails for authored-but-hidden cards
        left a conspicuous empty column in onboarding maps and made the
        interface look as if a thumbnail had failed to load.
        """
        if not self._edge_specs:
            return pygame.Rect(self.rect)

        directions = {
            spec.direction
            for spec in self._edge_specs.values()
        }
        gap = max(8, int(round(12 * self.density)))
        reserve_x = self._edge_card_width + gap
        reserve_y = self._edge_card_height + gap
        # A corner card occupies one side rail, not both a side rail and a
        # top/bottom rail. Reserving both axes turned a 16:9 campus plate into
        # a ~2.4:1 slot and made fixed map coordinates diverge from the
        # cover-cropped artwork.
        left = reserve_x if directions.intersection({"w", "nw", "sw"}) else gap
        right = reserve_x if directions.intersection({"e", "ne", "se"}) else gap
        top = reserve_y if "n" in directions else gap
        bottom = reserve_y if "s" in directions else gap
        canvas = pygame.Rect(
            self.rect.x + left,
            self.rect.y + top,
            self.rect.width - left - right,
            self.rect.height - top - bottom,
        )
        # Small windows cannot support a distinct perimeter. Retain every
        # target by letting cards overlap the procedural canvas rather than
        # generating a negative/zero authored coordinate area.
        minimum_canvas_w = self._labeled_width + int(
            round(32 * self.density),
        )
        minimum_canvas_h = (
            self._labeled_height
            + self._information_strip_height
            + int(round(40 * self.density))
        )
        if canvas.width < minimum_canvas_w:
            canvas.x = self.rect.x + gap
            canvas.width = max(1, self.rect.width - gap * 2)
        if canvas.height < minimum_canvas_h:
            canvas.y = self.rect.y + gap
            canvas.height = max(1, self.rect.height - gap * 2)
        canvas = self._fit_canvas_to_background(
            canvas,
            minimum_width=minimum_canvas_w,
            minimum_height=minimum_canvas_h,
        )
        return canvas

    def _fit_canvas_to_background(
        self,
        canvas: pygame.Rect,
        *,
        minimum_width: int,
        minimum_height: int,
    ) -> pygame.Rect:
        """Keep authored coordinates aligned with the complete source plate.

        The marker transform maps authored bounds to the whole canvas. When a
        cover-fit image is cropped to a different aspect ratio, those two
        coordinate spaces no longer match. If real art is available, shrink
        the canvas inside its reserved area to the image ratio so cover and
        contain share the same complete-image transform.
        """
        if (
            self.presentation is None
            or self.presentation.background_fit == "stretch"
            or not self.presentation.background
            or self.assets is None
            or not self.assets.has_image(self.presentation.background)
        ):
            return canvas
        source = self.assets.image(self.presentation.background)
        source_w, source_h = source.get_size()
        if source_w <= 0 or source_h <= 0:
            return canvas
        ratio = source_w / source_h
        current_ratio = canvas.width / max(1, canvas.height)
        fitted = pygame.Rect(canvas)
        if current_ratio > ratio:
            fitted.width = max(1, int(round(fitted.height * ratio)))
            fitted.centerx = canvas.centerx
        else:
            fitted.height = max(1, int(round(fitted.width / ratio)))
            fitted.centery = canvas.centery
        if (
            fitted.width < minimum_width
            or fitted.height < minimum_height
        ):
            return canvas
        return fitted

    def _layout_edge_cards(self) -> dict[str, pygame.Rect]:
        """Place edge cards at deterministic compass anchors."""
        by_direction: dict[str, list[str]] = {}
        for node_id, spec in self._edge_specs.items():
            by_direction.setdefault(spec.direction, []).append(node_id)

        frame_margin = max(8, int(round(16 * self.density)))
        card_w = min(
            self._edge_card_width,
            max(self.MIN_HIT_SIZE, self.rect.width - frame_margin),
        )
        card_h = min(
            self._edge_card_height,
            max(self.MIN_HIT_SIZE, self.rect.height - frame_margin),
        )
        gap = max(6, int(round(8 * self.density)))
        anchors = {
            "n": (
                self.rect.centerx - card_w // 2,
                self.rect.top + gap,
            ),
            "ne": (
                self.rect.right - card_w - gap,
                self.rect.top + gap,
            ),
            "e": (
                self.rect.right - card_w - gap,
                self.rect.centery - card_h // 2,
            ),
            "se": (
                self.rect.right - card_w - gap,
                self.rect.bottom - card_h - gap,
            ),
            "s": (
                self.rect.centerx - card_w // 2,
                self.rect.bottom - card_h - gap,
            ),
            "sw": (
                self.rect.left + gap,
                self.rect.bottom - card_h - gap,
            ),
            "w": (
                self.rect.left + gap,
                self.rect.centery - card_h // 2,
            ),
            "nw": (
                self.rect.left + gap,
                self.rect.top + gap,
            ),
        }
        rects: dict[str, pygame.Rect] = {}
        for direction, node_ids in by_direction.items():
            base_x, base_y = anchors[direction]
            count = len(node_ids)
            for index, node_id in enumerate(node_ids):
                if direction in {"n", "s"}:
                    offset_x = int(
                        (index - (count - 1) / 2) * (card_w + gap),
                    )
                    offset_y = 0
                elif direction in {"e", "w"}:
                    offset_x = 0
                    offset_y = int(
                        (index - (count - 1) / 2) * (card_h + gap),
                    )
                else:
                    # Multiple cards at one corner fan toward the centre.
                    offset_x = 0
                    sign = 1 if direction in {"ne", "nw"} else -1
                    offset_y = sign * index * (card_h + gap)
                rect = pygame.Rect(
                    base_x + offset_x,
                    base_y + offset_y,
                    card_w,
                    card_h,
                )
                rect.clamp_ip(self.rect)
                rects[node_id] = rect
        return rects

    def _focusable_ids(self) -> list[str]:
        """Map destinations in authored order (the current node is not one)."""
        return [
            node["id"] for node in self.nodes
            if node["id"] != self.current_id
            and node["id"] in self._node_rects
        ]

    def _draw_order(self) -> list[dict]:
        """Return bottom-to-top marker order shared by paint and hit tests."""
        focused_id = (
            self._focused_id
            if self._focused_id in self._node_rects
            else None
        )
        central_nodes = [
            node
            for node in self.nodes
            if node["id"] not in self._edge_specs
        ]
        ordinary_nodes = [
            node for node in central_nodes
            if self._is_ordinary(node) and node["id"] != focused_id
        ]
        edge_nodes = [
            node for node in self.nodes
            if node["id"] in self._edge_specs
            and node["id"] != focused_id
        ]
        prominent_nodes = [
            node for node in central_nodes
            if not self._is_ordinary(node) and node["id"] != focused_id
        ]
        ordered = ordinary_nodes + edge_nodes + prominent_nodes
        focused = self._node(focused_id)
        if focused is not None:
            ordered.append(focused)
        return ordered

    @staticmethod
    def _navigation_direction(inp) -> tuple[int, int] | None:
        """Extract one semantic keyboard/controller navigation direction."""
        if inp.pressed(InputAction.NAV_LEFT):
            return (-1, 0)
        if inp.pressed(InputAction.NAV_RIGHT):
            return (1, 0)
        if inp.pressed(InputAction.NAV_UP):
            return (0, -1)
        if inp.pressed(InputAction.NAV_DOWN):
            return (0, 1)
        return None

    def _directional_target(
        self,
        origin_id: str,
        direction: tuple[int, int],
        candidate_ids: list[str],
    ) -> str | None:
        """Nearest spatial candidate, with deterministic edge wrapping."""
        origin = self._node_rects.get(origin_id)
        if origin is None or not candidate_ids:
            return None
        cx, cy = origin.center
        dx, dy = direction
        candidates: list[tuple[int, int, int, str]] = []
        for node_id in candidate_ids:
            rect = self._node_rects[node_id]
            delta_x = rect.centerx - cx
            delta_y = rect.centery - cy
            if dx and delta_x * dx > 0:
                primary, orthogonal = abs(delta_x), abs(delta_y)
            elif dy and delta_y * dy > 0:
                primary, orthogonal = abs(delta_y), abs(delta_x)
            else:
                continue
            # Penalise off-axis candidates without making a slightly diagonal
            # nearby node impossible to reach.
            candidates.append((
                primary + orthogonal * 2,
                orthogonal,
                primary,
                node_id,
            ))
        if candidates:
            return min(candidates)[-1]

        # No node remains in that half-plane: wrap to the opposite edge,
        # favouring a node aligned with the current row/column.
        def wrap_key(node_id: str) -> tuple[int, int, str]:
            rect = self._node_rects[node_id]
            if dx > 0:
                return (rect.centerx, abs(rect.centery - cy), node_id)
            if dx < 0:
                return (-rect.centerx, abs(rect.centery - cy), node_id)
            if dy > 0:
                return (rect.centery, abs(rect.centerx - cx), node_id)
            return (-rect.centery, abs(rect.centerx - cx), node_id)

        return min(candidate_ids, key=wrap_key)

    def move_focus(self, direction: tuple[int, int]) -> None:
        """Move focus spatially; use the current location as initial anchor."""
        focusable = self._focusable_ids()
        if not focusable:
            self._focused_id = None
            return

        origin_id = self._focused_id
        if origin_id not in self._node_rects:
            origin_id = self.current_id
        if origin_id in self._node_rects:
            candidates = [
                node_id for node_id in focusable if node_id != origin_id
            ]
            target = self._directional_target(
                origin_id,
                direction,
                candidates,
            )
            # With exactly one destination there is nowhere else to wrap;
            # keeping focus is both deterministic and less surprising than
            # clearing it.
            if target is None:
                target = (
                    origin_id if origin_id in focusable else focusable[0]
                )
        else:
            # A map without a current location still has a stable entry point:
            # choose the edge the player is moving into.
            dx, dy = direction

            def edge_key(node_id: str) -> tuple[int, int, str]:
                rect = self._node_rects[node_id]
                if dx > 0:
                    return (rect.centerx, rect.centery, node_id)
                if dx < 0:
                    return (-rect.centerx, rect.centery, node_id)
                if dy > 0:
                    return (rect.centery, rect.centerx, node_id)
                return (-rect.centery, rect.centerx, node_id)

            target = min(focusable, key=edge_key)
        self._focused_id = target

    def focus_first(self, candidate_ids: Iterable[str]) -> str | None:
        """Focus the first visible candidate in authored map order.

        This is the deterministic entry point for a scene that discloses a
        new marker layer.  It uses the same persistent focus as keyboard and
        controller navigation, without promoting every compact pin to a label.
        """
        candidates = set(candidate_ids)
        self._focused_id = next(
            (
                node_id
                for node_id in self._focusable_ids()
                if node_id in candidates
            ),
            None,
        )
        return self._focused_id

    def update(self, dt: float, inp) -> None:
        direction = self._navigation_direction(inp)
        if direction is not None:
            self.move_focus(direction)

        self._hover_id = None
        # Reverse paint order makes the visible top-most marker own an
        # overlapping hit target (focused > story/current > edge > ordinary).
        for node in reversed(self._draw_order()):
            nid = node["id"]
            rect = self._node_rects[nid]
            if rect.collidepoint(inp.mouse_pos):
                self._hover_id = nid
                if inp.mouse_clicked and self.on_click is not None:
                    if nid != self.current_id:
                        self._focused_id = nid
                    self.on_click(nid)
                break

        # Only real pointer motion changes keyboard focus. Merely leaving the
        # mouse parked over the map must not erase D-pad navigation.
        if any(event.type == pygame.MOUSEMOTION
               for event in getattr(inp, "events", ())):
            self._focused_id = (
                self._hover_id
                if self._hover_id != self.current_id
                else None
            )

        if (inp.confirm
                and self._focused_id in self._focusable_ids()
                and self.on_click is not None):
            # MapScene wires this to the destination preview sheet. Travel is
            # committed only after that sheet's separate confirmation.
            self.on_click(self._focused_id)

        self._advance_interaction_progress(dt)

    def _advance_interaction_progress(self, dt: float) -> None:
        """Ease finite hover/focus decoration without moving any geometry."""
        factor = self.motion.feedback_factor(dt, self.FEEDBACK_SPEED)
        visible_ids = {
            node["id"]
            for node in self.nodes
            if node["id"] in self._node_rects
        }
        for node_id in visible_ids:
            target = 1.0 if node_id in {
                self._hover_id,
                self._focused_id,
            } else 0.0
            current = self._interaction_progress.get(node_id, 0.0)
            value = current + (target - current) * factor
            if abs(target - value) < 1e-4:
                value = target
            self._interaction_progress[node_id] = max(
                0.0,
                min(1.0, value),
            )
        for node_id in set(self._interaction_progress) - visible_ids:
            del self._interaction_progress[node_id]

    def draw(self, surface: pygame.Surface) -> None:
        if not self.visible:
            return
        self._draw_background(surface)

        # The legacy graph needs connection lines to communicate topology.
        # Illustrated maps already encode spatial relationships, and routes
        # are explained truthfully in the confirmation sheet; lines across
        # artwork and directional edge cards only add visual noise.
        if self.presentation is None:
            for n in self.nodes:
                for exit_id in n.get("exits", []):
                    if exit_id not in self._node_rects:
                        continue
                    a = self._node_rects[n["id"]].center
                    b = self._node_rects[exit_id].center
                    pygame.draw.line(
                        surface,
                        (*self.theme.border_soft[:3], 80),
                        a,
                        b,
                        1,
                    )

        # Dense ordinary pins form the lowest marker layer. Edge cards occupy
        # their own perimeter, then story/current labels take precedence.
        # Whichever target owns keyboard focus is always painted last.
        for node in self._draw_order():
            node_id = node["id"]
            rect = self._node_rects[node_id]
            if node_id in self._edge_specs:
                self._draw_edge_card(surface, node, rect)
            elif self._is_ordinary(node):
                self._draw_ordinary_pin(
                    surface,
                    node,
                    rect,
                )
            else:
                self._draw_labeled_marker(
                    surface,
                    node,
                    rect,
                )

        self._draw_information_strip(surface)
        self._draw_legend(surface)

    def _draw_legend(self, surface: pygame.Surface) -> None:
        """Explain the five visual states without covering map hit targets."""
        if self._legend_rect.width <= 0 or self._legend_rect.height <= 0:
            self._legend_item_rects = {}
            return

        rect = self._legend_rect
        legend = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.rect(
            legend,
            (*self.theme.bg_overlay[:3], 232),
            legend.get_rect(),
            border_radius=self._radius_m,
        )
        pygame.draw.rect(
            legend,
            (*self.theme.border_strong[:3], 180),
            legend.get_rect(),
            width=max(1, int(round(self.density))),
            border_radius=self._radius_m,
        )

        entries = tuple(
            (kind, self._legend_labels[kind])
            for kind in self.LEGEND_KINDS
            if kind in self._legend_labels
        )
        pad_x = max(8, int(round(12 * self.density)))
        pad_y = max(6, int(round(8 * self.density)))
        item_gap = max(8, int(round(12 * self.density)))
        icon_w = max(14, int(round(18 * self.density)))
        icon_gap = max(4, int(round(6 * self.density)))
        row_gap = max(4, int(round(6 * self.density)))
        font = self.fonts.get(self.LEGEND_FONT_SIZE, bold=True)
        row_h = max(icon_w, font.get_linesize())
        item_x = pad_x
        item_y = pad_y
        inner_right = max(pad_x + 1, rect.width - pad_x)
        item_rects: dict[str, pygame.Rect] = {}

        for kind, label_text in entries:
            label_w = font.size(label_text)[0]
            item_w = icon_w + icon_gap + label_w
            if item_x > pad_x and item_x + item_w > inner_right:
                item_x = pad_x
                item_y += row_h + row_gap
            item_rect = pygame.Rect(item_x, item_y, item_w, row_h)
            item_rects[kind] = item_rect.move(rect.topleft)
            icon_rect = pygame.Rect(
                item_x,
                item_y,
                icon_w,
                icon_w,
            )
            icon_rect.centery = item_rect.centery
            self._draw_legend_icon(legend, kind, icon_rect)
            rendered = self.fonts.render(
                label_text,
                self.LEGEND_FONT_SIZE,
                self.theme.text,
                bold=True,
            )
            legend.blit(
                rendered,
                (
                    icon_rect.right + icon_gap,
                    item_rect.y
                    + (item_rect.height - rendered.get_height()) // 2,
                ),
            )
            item_x += item_w + item_gap
        self._legend_item_rects = item_rects
        surface.blit(legend, rect.topleft)

    def _draw_legend_icon(
        self,
        surface: pygame.Surface,
        kind: str,
        rect: pygame.Rect,
    ) -> None:
        """Small shape samples matching map semantics without icon glyphs."""
        center = rect.center
        line_w = max(1, int(round(2 * self.density)))
        if kind == "story":
            chip = pygame.Rect(
                rect.x,
                rect.centery - max(4, rect.height // 3),
                rect.width,
                max(8, rect.height * 2 // 3),
            )
            pygame.draw.rect(
                surface,
                self.theme.accent,
                chip,
                width=line_w,
                border_radius=max(2, self._radius_m // 2),
            )
            pygame.draw.circle(
                surface,
                self.theme.accent,
                center,
                max(2, int(round(2 * self.density))),
            )
            return
        if kind == "current":
            radius = max(5, rect.height // 3)
            pygame.draw.circle(
                surface,
                self.theme.accent_warm,
                center,
                radius,
                width=line_w,
            )
            pygame.draw.circle(
                surface,
                self.theme.text,
                center,
                max(2, int(round(2 * self.density))),
            )
            return
        if kind == "ordinary":
            pygame.draw.circle(
                surface,
                self.theme.accent,
                center,
                max(4, rect.height // 4),
            )
            pygame.draw.circle(
                surface,
                self.theme.text,
                center,
                max(1, int(round(self.density))),
            )
            return
        if kind == "off_campus":
            card = rect.inflate(
                -max(1, int(round(2 * self.density))),
                -max(4, int(round(6 * self.density))),
            )
            pygame.draw.rect(
                surface,
                self.theme.accent_alt,
                card,
                width=line_w,
                border_radius=max(1, self._radius_m // 3),
            )
            pygame.draw.lines(
                surface,
                self.theme.accent_alt,
                False,
                [
                    (card.left + line_w, card.bottom - line_w),
                    (card.centerx, card.centery),
                ],
                width=line_w,
            )
            return

        # Focus is deliberately indicated by shape as well as colour.
        inset = max(1, int(round(2 * self.density)))
        length = max(4, rect.width // 3)
        x, y = rect.left + inset, rect.top + inset
        pygame.draw.lines(
            surface,
            self.theme.accent_warm,
            False,
            [(x, y + length), (x, y), (x + length, y)],
            width=line_w,
        )
        x, y = rect.right - inset, rect.bottom - inset
        pygame.draw.lines(
            surface,
            self.theme.accent_warm,
            False,
            [(x - length, y), (x, y), (x, y - length)],
            width=line_w,
        )

    def _node_palette(
        self,
        node: dict,
    ) -> tuple[tuple, tuple, tuple]:
        is_current = node["id"] == self.current_id
        is_reachable = node["id"] in self.reachable_ids
        is_locked = not node.get("accessible", True)
        is_visited = node.get("visited", False)
        region_color: tuple[int, int, int] | None = node.get("region_color")
        marker_color = self._marker_color(node)
        if is_locked:
            return (
                (*self.theme.bg_overlay[:3], 205),
                self.theme.border_soft,
                self.theme.text_dim,
            )
        if is_current:
            return (
                (*self.theme.bg_overlay[:3], 225),
                self.theme.accent_warm,
                self.theme.text,
            )
        if node.get("offer_status") == "future":
            return (
                (*self.theme.bg_overlay[:3], 218),
                marker_color or self.theme.accent_alt,
                self.theme.text,
            )
        if node.get("offer_status") == "locked":
            return (
                (*self.theme.bg_overlay[:3], 205),
                self.theme.border,
                self.theme.text_mute,
            )
        if is_reachable:
            return (
                (*self.theme.bg_overlay[:3], 218),
                marker_color or self.theme.accent,
                self.theme.text,
            )
        if is_visited and region_color:
            return (
                (*self.theme.bg_overlay[:3], 195),
                (*region_color, 180),
                self.theme.text_mute,
            )
        if is_visited:
            return (
                (*self.theme.bg_overlay[:3], 190),
                (*self.theme.accent_alt[:3], 145),
                self.theme.text_mute,
            )
        return (
            (50, 50, 60, 155),
            (80, 80, 90, 140),
            self.theme.text_dim,
        )

    @staticmethod
    def _marker_color(node: dict) -> tuple[int, int, int] | None:
        """Return a safe RGB route accent from pack-authored character data."""
        value = node.get("marker_color")
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            return None
        if not all(
            isinstance(channel, int)
            and not isinstance(channel, bool)
            and 0 <= channel <= 255
            for channel in value
        ):
            return None
        return tuple(value)

    def _chrome_variant(self, node: dict) -> str:
        """Stable semantic variants understood by pack-owned map chrome."""
        edge = self._edge_specs.get(node["id"])
        if edge is not None:
            return f"edge:off_campus:{edge.direction}"
        marker_kind = str(node.get("marker_kind") or "legacy").lower()
        return f"marker:{marker_kind}"

    def _chrome_enabled(self, node: dict) -> bool:
        if node["id"] == self.current_id:
            return True
        return bool(
            node.get("accessible", True)
            and node.get("offer_status") != "locked"
        )

    def _paint_node_chrome(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        node: dict,
        *,
        part: Literal["card", "focus"],
    ) -> bool:
        """Ask the optional pack renderer for paint-only map decoration."""
        if self.chrome is None or rect.width <= 0 or rect.height <= 0:
            return False
        progress = self.interaction_progress(node["id"])
        return self.chrome.paint(
            surface,
            rect,
            ChromeRequest(
                part=part,
                screen="map",
                variant=self._chrome_variant(node),
                size=rect.size,
                density=self.density,
                enabled=self._chrome_enabled(node),
                hovered=node["id"] == self._hover_id,
                focused=node["id"] == self._focused_id,
                selected=node["id"] == self.current_id,
                emphasis=max(
                    0,
                    min(3, int(round(progress * 3))),
                ),
            ),
        )

    def _draw_node_focus(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        node: dict,
    ) -> None:
        """Layer optional authored brackets under the engine-owned ring."""
        if node["id"] != self._focused_id:
            return
        self._paint_node_chrome(
            surface,
            rect,
            node,
            part="focus",
        )
        self._draw_focus_ring(surface, rect)

    def _draw_ordinary_pin(
        self,
        surface: pygame.Surface,
        node: dict,
        hit_rect: pygame.Rect,
    ) -> None:
        """Draw a compact visual pin inside a touch-safe hit rectangle."""
        fill, border, _text_color = self._node_palette(node)
        is_focused = node["id"] == self._focused_id
        progress = self.interaction_progress(node["id"])
        radius = self._ordinary_pin_radius
        chrome_layer = pygame.Surface(hit_rect.size, pygame.SRCALPHA)
        self._paint_node_chrome(
            chrome_layer,
            chrome_layer.get_rect(),
            node,
            part="card",
        )
        surface.blit(chrome_layer, hit_rect.topleft)
        if progress > 0.01:
            pygame.draw.circle(
                surface,
                (
                    *self.theme.accent[:3],
                    max(1, int(round(55 * progress))),
                ),
                hit_rect.center,
                radius + max(5, int(round(5 * self.density))),
            )
        pygame.draw.circle(
            surface,
            border,
            hit_rect.center,
            radius + max(2, int(round(2 * self.density))),
        )
        pygame.draw.circle(
            surface,
            fill,
            hit_rect.center,
            radius,
        )
        pygame.draw.circle(
            surface,
            self.theme.text,
            hit_rect.center,
            max(2, int(round(2 * self.density))),
        )
        if is_focused:
            self._draw_node_focus(surface, hit_rect, node)

    def ordinary_pin_rect(self, node_id: str) -> pygame.Rect | None:
        """Return compact visual geometry without weakening the hit target."""
        node = self._node(node_id)
        hit_rect = self._node_rects.get(node_id)
        if (
            node is None
            or hit_rect is None
            or not self._is_ordinary(node)
        ):
            return None
        radius = self._ordinary_pin_radius + max(
            2,
            int(round(2 * self.density)),
        )
        visual = pygame.Rect(0, 0, radius * 2, radius * 2)
        visual.center = hit_rect.center
        return visual

    def _draw_labeled_marker(
        self,
        surface: pygame.Surface,
        node: dict,
        rect: pygame.Rect,
    ) -> None:
        """Draw compact story/current/legacy labels above ordinary pins."""
        fill, border, text_color = self._node_palette(node)
        is_current = node["id"] == self.current_id
        is_reachable = node["id"] in self.reachable_ids
        is_focused = node["id"] == self._focused_id
        progress = self.interaction_progress(node["id"])
        if (
            progress > 0.0
            and (
                is_reachable
                or is_current
                or node.get("offer_status") is not None
            )
        ):
            fill = tuple(
                min(255, int(round(channel + 35 * progress)))
                for channel in fill[:3]
            ) + (fill[3],)

        chip = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.rect(
            chip,
            fill,
            chip.get_rect(),
            border_radius=self._radius_m,
        )
        self._paint_node_chrome(
            chip,
            chip.get_rect(),
            node,
            part="card",
        )
        pygame.draw.rect(
            chip,
            border,
            chip.get_rect(),
            width=2 if is_current else 1,
            border_radius=self._radius_m,
        )
        marker_color = self._marker_color(node)
        if marker_color is not None:
            rail_w = max(3, int(round(4 * self.density)))
            rail_h = max(1, rect.height - int(round(12 * self.density)))
            pygame.draw.rect(
                chip,
                (*marker_color, 225),
                pygame.Rect(
                    0,
                    (rect.height - rail_h) // 2,
                    rail_w,
                    rail_h,
                ),
                border_radius=max(1, rail_w // 2),
            )
        surface.blit(chip, rect.topleft)

        visible = (
            node.get("visited", False)
            or is_current
            or is_reachable
            or node.get("offer_status") is not None
        )
        horizontal_pad = max(6, int(round(10 * self.density)))
        content_rect = pygame.Rect(
            rect.x + horizontal_pad,
            rect.y,
            max(1, rect.width - horizontal_pad * 2),
            rect.height,
        )
        portrait_path = node.get("portrait")
        has_portrait = bool(
            isinstance(portrait_path, str)
            and portrait_path
            and self.assets is not None
            and self.assets.has_image(portrait_path)
        )
        if has_portrait:
            assert self.assets is not None
            avatar_pad = max(4, int(round(5 * self.density)))
            avatar_size = min(
                self._portrait_size,
                max(1, rect.height - avatar_pad * 2),
            )
            avatar_rect = pygame.Rect(
                rect.x + horizontal_pad,
                rect.y + (rect.height - avatar_size) // 2,
                avatar_size,
                avatar_size,
            )
            avatar = self.assets.scaled(
                portrait_path,
                avatar_rect.size,
                fit="cover",
            ).copy()
            avatar_radius = max(2, int(round(5 * self.density)))
            self._apply_rounded_mask(avatar, avatar_radius)
            surface.blit(avatar, avatar_rect.topleft)
            pygame.draw.rect(
                surface,
                marker_color or border,
                avatar_rect,
                width=max(1, int(round(self.density))),
                border_radius=avatar_radius,
            )
            text_gap = max(5, int(round(7 * self.density)))
            content_rect.x = avatar_rect.right + text_gap
            content_rect.width = max(
                1,
                rect.right - horizontal_pad - content_rect.x,
            )
        if visible:
            label_text = self._fit_text(
                node.get("name", ""),
                self._label_size,
                content_rect.width,
                bold=True,
            )
            label = self.fonts.render(
                label_text,
                self._label_size,
                text_color,
                bold=True,
            )
            sub_text = (
                node.get("marker_label")
                or node.get("region_name")
                or node.get("region")
                or ""
            )
            sub = None
            if sub_text:
                sub = self.fonts.render(
                    self._fit_text(
                        sub_text,
                        self._sub_label_size,
                        content_rect.width,
                    ),
                    self._sub_label_size,
                    self.theme.text_mute,
                )
            gap = max(1, int(round(1 * self.density)))
            content_h = label.get_height() + (
                sub.get_height() + gap if sub is not None else 0
            )
            content_y = rect.y + (rect.height - content_h) // 2
            surface.blit(
                label,
                (
                    content_rect.x
                    + (content_rect.width - label.get_width()) // 2,
                    content_y,
                ),
            )
            if sub is not None:
                surface.blit(
                    sub,
                    (
                        content_rect.x
                        + (content_rect.width - sub.get_width()) // 2,
                        content_y + label.get_height() + gap,
                    ),
                )
        else:
            unknown = self.fonts.render(
                "?",
                20,
                text_color,
                bold=True,
            )
            surface.blit(
                unknown,
                (
                    content_rect.x
                    + (content_rect.width - unknown.get_width()) // 2,
                    rect.y + (rect.height - unknown.get_height()) // 2,
                ),
            )

        if is_focused:
            self._draw_node_focus(surface, rect, node)

    def _draw_information_strip(self, surface: pygame.Surface) -> None:
        """Reveal one ordinary label in a stable, bounded information strip."""
        detail_node = self._node(self.detail_node_id)
        if detail_node is None or self._information_strip_rect.width <= 0:
            return
        rect = self._information_strip_rect
        strip = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.rect(
            strip,
            (*self.theme.bg_overlay[:3], 232),
            strip.get_rect(),
            border_radius=self._radius_m,
        )
        pygame.draw.rect(
            strip,
            (*self.theme.accent[:3], 185),
            strip.get_rect(),
            width=max(1, int(round(self.density))),
            border_radius=self._radius_m,
        )
        accent_w = max(3, int(round(4 * self.density)))
        pygame.draw.rect(
            strip,
            self.theme.accent,
            pygame.Rect(0, 0, accent_w, rect.height),
            border_top_left_radius=self._radius_m,
            border_bottom_left_radius=self._radius_m,
        )

        pad_x = max(10, int(round(14 * self.density)))
        pad_y = max(5, int(round(6 * self.density)))
        available_w = max(1, rect.width - pad_x * 2)
        name = self.fonts.render(
            self._fit_text(
                detail_node.get("name", ""),
                16,
                available_w,
                bold=True,
            ),
            16,
            self.theme.text,
            bold=True,
        )
        # The compact pin itself stays label-free; its single detail row
        # carries the context a player needs to decide whether to inspect it:
        # area, purpose, then authored description. Preserve order while
        # avoiding repeated values in lightly-authored legacy packs.
        description_parts = list(dict.fromkeys(
            str(value)
            for value in (
                detail_node.get("region_name"),
                detail_node.get("marker_label"),
                detail_node.get("description"),
            )
            if value
        ))
        description_text = " · ".join(description_parts)
        description = self.fonts.render(
            self._fit_text(description_text, 13, available_w),
            13,
            self.theme.text_mute,
        )
        line_gap = max(1, int(round(1 * self.density)))
        content_h = name.get_height() + line_gap + description.get_height()
        content_y = max(
            pad_y,
            (rect.height - content_h) // 2,
        )
        strip.blit(name, (pad_x, content_y))
        strip.blit(
            description,
            (pad_x, content_y + name.get_height() + line_gap),
        )
        surface.blit(strip, rect.topleft)

    def _draw_background(self, surface: pygame.Surface) -> None:
        """Draw the illustrated campus canvas or the legacy map frame."""
        frame = pygame.Surface(self.rect.size, pygame.SRCALPHA)
        pygame.draw.rect(
            frame,
            (*self.theme.accent[:3], 12),
            frame.get_rect(),
            border_radius=self._radius_l,
        )
        pygame.draw.rect(
            frame,
            (*self.theme.accent[:3], 80),
            frame.get_rect(),
            width=1,
            border_radius=self._radius_l,
        )
        surface.blit(frame, self.rect.topleft)

        if self.presentation is None:
            return

        canvas = pygame.Surface(self._canvas_rect.size, pygame.SRCALPHA)
        canvas.fill((*self.theme.bg_deep[:3], 255))
        if self.background_available:
            assert self.assets is not None
            background = self.assets.scaled(
                self.presentation.background,
                self._canvas_rect.size,
                fit=self.presentation.background_fit,
            )
            canvas.blit(background, (0, 0))
            # A constant scrim keeps white labels readable over both bright
            # photography and dark illustrations. It does not animate.
            scrim = pygame.Surface(canvas.get_size(), pygame.SRCALPHA)
            scrim.fill((*self.theme.bg_overlay[:3], 58))
            canvas.blit(scrim, (0, 0))
        else:
            # Missing optional art is a polished neutral canvas, not the
            # AssetManager's filename placeholder.
            canvas.fill((*self.theme.bg_deep[:3], 205))
            grid = (*self.theme.accent[:3], 16)
            for x in range(0, canvas.get_width(), 40):
                pygame.draw.line(canvas, grid, (x, 0), (x, canvas.get_height()))
            for y in range(0, canvas.get_height(), 40):
                pygame.draw.line(canvas, grid, (0, y), (canvas.get_width(), y))

        pygame.draw.rect(
            canvas,
            (*self.theme.border_strong[:3], 150),
            canvas.get_rect(),
            width=1,
            border_radius=self._radius_l,
        )
        self._apply_rounded_mask(canvas, self._radius_l)
        surface.blit(canvas, self._canvas_rect.topleft)

        if self.presentation.attribution:
            self._draw_attribution(surface, self.presentation.attribution)

    def _draw_attribution(self, surface: pygame.Surface, text: str) -> None:
        font_size = 12
        horizontal_margin = max(10, int(round(20 * self.density)))
        label = self._fit_text(
            text,
            font_size,
            self._canvas_rect.width - horizontal_margin,
        )
        if not label:
            return
        rendered = self.fonts.render(
            label,
            font_size,
            self.theme.text_mute,
        )
        pad_x = max(5, int(round(7 * self.density)))
        pad_y = max(3, int(round(4 * self.density)))
        box = pygame.Surface(
            (rendered.get_width() + pad_x * 2,
             rendered.get_height() + pad_y * 2),
            pygame.SRCALPHA,
        )
        box.fill((*self.theme.bg_overlay[:3], 205))
        box.blit(rendered, (pad_x, pad_y))
        margin = max(4, int(round(6 * self.density)))
        preferred_y = (
            self._information_strip_rect.top
            - box.get_height()
            - margin
        )
        pos = (
            self._canvas_rect.right - box.get_width() - margin,
            max(self._canvas_rect.y + margin, preferred_y),
        )
        surface.blit(box, pos)

    def _draw_edge_card(
        self,
        surface: pygame.Surface,
        node: dict,
        rect: pygame.Rect,
    ) -> None:
        """Draw one directional off-campus card using the same hit target."""
        spec = self._edge_specs[node["id"]]
        card = pygame.Surface(rect.size, pygame.SRCALPHA)
        has_thumbnail = bool(
            spec.thumbnail
            and self.assets is not None
            and self.assets.has_image(spec.thumbnail)
        )
        if has_thumbnail:
            assert self.assets is not None
            art = self.assets.scaled(spec.thumbnail, rect.size, fit="cover")
            card.blit(art, (0, 0))
        else:
            card.fill((*self.theme.bg_deep[:3], 245))
            pygame.draw.line(
                card,
                (*self.theme.accent_alt[:3], 85),
                (12, 14),
                (rect.width - 12, rect.height - 14),
                width=2,
            )
            pygame.draw.line(
                card,
                (*self.theme.accent[:3], 65),
                (rect.width - 12, 14),
                (12, rect.height - 14),
                width=1,
            )

        is_current = node["id"] == self.current_id
        is_reachable = node["id"] in self.reachable_ids
        is_locked = not node.get("accessible", True)
        status = node.get("offer_status")
        focused = node["id"] == self._focused_id
        progress = self.interaction_progress(node["id"])
        if is_locked or status == "locked":
            border = self.theme.border_soft
        elif is_current:
            border = self.theme.accent_warm
        elif (
            self._marker_color(node) is not None
            and (is_reachable or status is not None)
        ):
            border = self._marker_color(node)
        elif is_reachable or status is not None:
            border = self.theme.accent
        else:
            border = self.theme.border

        # Bottom veil carries both the destination and optional authored
        # caption without baking labels into downloadable map art. Its height
        # comes from real glyph metrics, so scaled 1080p text cannot spill.
        detail_parts = [
            str(value)
            for value in (node.get("marker_label"), spec.caption)
            if value
        ]
        detail_text = " · ".join(detail_parts)
        attribution_text = spec.attribution or ""
        name_size = 16
        caption_size = 12
        attribution_size = 10
        name_line_h = self.fonts.get(
            name_size,
            bold=True,
        ).get_linesize()
        caption_line_h = (
            self.fonts.get(caption_size).get_linesize()
            if detail_text else 0
        )
        attribution_line_h = (
            self.fonts.get(attribution_size).get_linesize()
            if attribution_text else 0
        )
        vertical_pad = max(4, int(round(5 * self.density)))
        line_gap = max(1, int(round(1 * self.density)))
        secondary_lines = int(bool(detail_text)) + int(
            bool(attribution_text)
        )
        veil_height = (
            name_line_h
            + caption_line_h
            + attribution_line_h
            + vertical_pad * 2
            + line_gap * secondary_lines
        )
        veil_height = min(rect.height, max(name_line_h, veil_height))
        veil = pygame.Surface((rect.width, veil_height), pygame.SRCALPHA)
        veil.fill((*self.theme.bg_overlay[:3], 225))
        card.blit(veil, (0, rect.height - veil_height))
        if is_locked or status == "locked":
            disabled = pygame.Surface(rect.size, pygame.SRCALPHA)
            disabled.fill((*self.theme.bg_deep[:3], 105))
            card.blit(disabled, (0, 0))
        if progress > 0.01 and (
            is_reachable or is_current or status is not None
        ):
            glow = pygame.Surface(rect.size, pygame.SRCALPHA)
            glow.fill((
                *self.theme.accent[:3],
                max(1, int(round(24 * progress))),
            ))
            card.blit(glow, (0, 0))

        self._paint_node_chrome(
            card,
            card.get_rect(),
            node,
            part="card",
        )
        pygame.draw.rect(
            card,
            border,
            card.get_rect(),
            width=2 if is_current else 1,
            border_radius=self._radius_m,
        )

        horizontal_pad = max(8, int(round(10 * self.density)))
        max_text_width = max(1, rect.width - horizontal_pad * 2)
        name = self._fit_text(
            node.get("name", ""),
            name_size,
            max_text_width,
            bold=True,
        )
        label = self.fonts.render(
            name,
            name_size,
            self.theme.text,
            bold=True,
        )
        text_y = rect.height - veil_height + vertical_pad
        card.blit(label, (horizontal_pad, text_y))
        if detail_text:
            caption = self._fit_text(
                detail_text,
                caption_size,
                max_text_width,
            )
            rendered_caption = self.fonts.render(
                caption,
                caption_size,
                self.theme.text_mute,
            )
            card.blit(
                rendered_caption,
                (
                    horizontal_pad,
                    text_y + label.get_height() + line_gap,
                ),
            )
        if attribution_text:
            attribution = self._fit_text(
                attribution_text,
                attribution_size,
                max_text_width,
            )
            rendered_attribution = self.fonts.render(
                attribution,
                attribution_size,
                self.theme.text_mute,
            )
            attribution_y = (
                text_y
                + label.get_height()
                + line_gap
                + (
                    rendered_caption.get_height() + line_gap
                    if detail_text
                    else 0
                )
            )
            card.blit(
                rendered_attribution,
                (horizontal_pad, attribution_y),
            )

        self._apply_rounded_mask(card, self._radius_m)
        surface.blit(card, rect.topleft)
        if focused:
            self._draw_node_focus(surface, rect, node)

    def _fit_text(
        self,
        text: str,
        size: int,
        max_width: int,
        *,
        bold: bool = False,
    ) -> str:
        """Single-line ellipsis used by small cards and attribution."""
        # YAML ``|`` descriptions commonly contain authored line breaks.
        # These compact surfaces are deliberately single-line; collapse all
        # whitespace before measuring so pygame does not silently stop at the
        # first newline and leave a misleading dangling comma.
        value = " ".join(str(text).split())
        if max_width <= 0:
            return ""
        if (
            self.fonts.render(
                value,
                size,
                self.theme.text,
                bold=bold,
            ).get_width()
            <= max_width
        ):
            return value
        ellipsis = "…"
        if (
            self.fonts.render(
                ellipsis,
                size,
                self.theme.text,
                bold=bold,
            ).get_width()
            > max_width
        ):
            return ""
        while value:
            value = value[:-1]
            candidate = value.rstrip() + ellipsis
            if (
                self.fonts.render(
                    candidate,
                    size,
                    self.theme.text,
                    bold=bold,
                ).get_width()
                <= max_width
            ):
                return candidate
        return ""

    @staticmethod
    def _apply_rounded_mask(surface: pygame.Surface, radius: int) -> None:
        """Clear square image corners while preserving the composed alpha."""
        mask = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        mask.fill((255, 255, 255, 0))
        pygame.draw.rect(
            mask,
            (255, 255, 255, 255),
            mask.get_rect(),
            border_radius=max(0, int(radius)),
        )
        surface.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

    def _draw_focus_ring(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
    ) -> None:
        """Persistent focus treatment identifiable by colour and shape."""
        edge_inset = max(2, int(round(2 * self.density)))
        ring = rect.inflate(-edge_inset, -edge_inset)
        ring_width = max(3, int(round(4 * self.density)))
        pygame.draw.rect(
            surface,
            self.theme.accent_warm,
            ring,
            width=ring_width,
            border_radius=self._radius_m,
        )
        inset = max(5, int(round(7 * self.density)))
        length = max(8, int(round(11 * self.density)))
        corner_width = max(2, int(round(2 * self.density)))
        x, y = ring.x + inset, ring.y + inset
        pygame.draw.lines(
            surface,
            self.theme.text,
            False,
            [(x, y + length), (x, y), (x + length, y)],
            width=corner_width,
        )
        x, y = ring.right - inset, ring.bottom - inset
        pygame.draw.lines(
            surface,
            self.theme.text,
            False,
            [(x - length, y), (x, y), (x, y - length)],
            width=corner_width,
        )
