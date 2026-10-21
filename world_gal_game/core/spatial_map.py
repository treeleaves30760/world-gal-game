"""Strict pack contract for the spatial destination-map presentation.

World-graph reachability and travel mutation remain in :mod:`map_system` and
:mod:`travel`.  This module only describes presentation: an illustrated
canvas, optional per-location display metadata, and destinations rendered as
cards around the canvas edge.

Authoring tools should use :func:`parse_spatial_map_spec`, which validates the
explicit ``travel.spatial_map`` block with Pydantic v2.  Runtime UI continues
to use :func:`spatial_map_from_meta`; that compatibility adapter deliberately
degrades malformed art to the legacy procedural canvas so presentation data
can never crash travel.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import math
from types import MappingProxyType
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)


MapDirection = Literal["n", "ne", "e", "se", "s", "sw", "w", "nw"]
MapLayer = Literal["campus", "interior", "story_only"]
BackgroundFit = Literal["cover", "contain", "stretch"]
MapBounds = tuple[float, float, float, float]

DEFAULT_MAP_BOUNDS: MapBounds = (0.0, 0.0, 100.0, 100.0)

_DIRECTION_ALIASES: dict[str, MapDirection] = {
    "n": "n",
    "north": "n",
    "ne": "ne",
    "north_east": "ne",
    "northeast": "ne",
    "e": "e",
    "east": "e",
    "se": "se",
    "south_east": "se",
    "southeast": "se",
    "s": "s",
    "south": "s",
    "sw": "sw",
    "south_west": "sw",
    "southwest": "sw",
    "w": "w",
    "west": "w",
    "nw": "nw",
    "north_west": "nw",
    "northwest": "nw",
}


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _direction(value: Any) -> MapDirection | None:
    if not isinstance(value, str):
        return None
    key = value.strip().lower().replace("-", "_").replace(" ", "_")
    return _DIRECTION_ALIASES.get(key)


def _bounds(value: Any) -> MapBounds:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return DEFAULT_MAP_BOUNDS
    try:
        min_x, min_y, max_x, max_y = (float(item) for item in value)
    except (TypeError, ValueError):
        return DEFAULT_MAP_BOUNDS
    if not all(math.isfinite(item) for item in (min_x, min_y, max_x, max_y)):
        return DEFAULT_MAP_BOUNDS
    if max_x <= min_x or max_y <= min_y:
        return DEFAULT_MAP_BOUNDS
    return (min_x, min_y, max_x, max_y)


class _StrictSpatialModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )


class EdgeDestinationSpec(_StrictSpatialModel):
    """Authored off-canvas card for one id-keyed location."""

    direction: MapDirection
    thumbnail: str | None = None
    caption: str | None = None
    attribution: str | None = None

    @field_validator("direction", mode="before")
    @classmethod
    def _normalise_direction(cls, value: Any) -> MapDirection:
        direction = _direction(value)
        if direction is None:
            raise ValueError(
                "direction must be N/NE/E/SE/S/SW/W/NW "
                "or a documented English compass alias"
            )
        return direction

    @field_validator("thumbnail", "caption", "attribution")
    @classmethod
    def _normalise_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            raise ValueError("value must not be blank")
        return text


class SpatialMapNodeSpec(_StrictSpatialModel):
    """Optional presentation metadata for an existing location.

    Unlisted locations retain the legacy ``campus`` treatment. ``parent`` is
    presentation hierarchy only and never grants reachability. An ``interior``
    node should name the campus/interior location that contains it.

    The current legacy MapScene carries this metadata through
    :class:`SpatialMapPresentation` but does not yet switch layers or override
    marker labels from it. Renderers may opt into those behaviours without
    changing the schema, underlying tags, hooks, or travel rules.
    """

    layer: MapLayer = "campus"
    parent: str | None = None
    label: str | None = None

    @field_validator("parent", "label")
    @classmethod
    def _normalise_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            raise ValueError("value must not be blank")
        return text


class SpatialMapSpec(_StrictSpatialModel):
    """Strict authoring schema for ``meta.travel.spatial_map``."""

    background: str | None = None
    attribution: str | None = None
    bounds: MapBounds = DEFAULT_MAP_BOUNDS
    background_fit: BackgroundFit = "cover"
    auto_open_after_scenes: list[str] = Field(default_factory=list)
    edge_destinations: dict[str, EdgeDestinationSpec] = Field(
        default_factory=dict,
    )
    nodes: dict[str, SpatialMapNodeSpec] = Field(default_factory=dict)

    @field_validator("background", "attribution")
    @classmethod
    def _normalise_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            raise ValueError("value must not be blank")
        return text

    @field_validator("bounds", mode="before")
    @classmethod
    def _accept_yaml_bounds(cls, value: Any) -> Any:
        # YAML represents the documented four-tuple as a list. Convert only
        # that container shape; strict scalar validation still rejects strings
        # and booleans.
        return tuple(value) if isinstance(value, list) else value

    @field_validator("bounds")
    @classmethod
    def _validate_bounds(cls, value: MapBounds) -> MapBounds:
        if not all(math.isfinite(item) for item in value):
            raise ValueError("bounds values must be finite")
        min_x, min_y, max_x, max_y = value
        if max_x <= min_x or max_y <= min_y:
            raise ValueError(
                "bounds must satisfy max_x > min_x and max_y > min_y",
            )
        return value

    @field_validator("edge_destinations", "nodes", mode="before")
    @classmethod
    def _validate_location_keys(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        for location_id in value:
            if (
                not isinstance(location_id, str)
                or not location_id
                or location_id != location_id.strip()
            ):
                raise ValueError(
                    "location ids must be non-empty strings without "
                    "surrounding whitespace",
                )
        return dict(value)

    @field_validator("auto_open_after_scenes")
    @classmethod
    def _validate_auto_open_scene_ids(
        cls,
        value: list[str],
    ) -> list[str]:
        normalised: list[str] = []
        for scene_id in value:
            text = scene_id.strip()
            if not text:
                raise ValueError("scene ids must not be blank")
            normalised.append(text)
        if len(set(normalised)) != len(normalised):
            raise ValueError("scene ids must not contain duplicates")
        return normalised


@dataclass(frozen=True)
class EdgeDestination:
    """Runtime-compatible visual treatment for one off-canvas destination."""

    location_id: str
    direction: MapDirection
    thumbnail: str | None = None
    caption: str | None = None
    attribution: str | None = None


@dataclass(frozen=True)
class SpatialMapPresentation:
    """Parsed, immutable runtime presentation consumed by MapScene/MapView."""

    background: str | None = None
    attribution: str | None = None
    bounds: MapBounds = DEFAULT_MAP_BOUNDS
    background_fit: BackgroundFit = "cover"
    edge_destinations: Mapping[str, EdgeDestination] = field(
        default_factory=lambda: MappingProxyType({}),
    )
    nodes: Mapping[str, SpatialMapNodeSpec] = field(
        default_factory=lambda: MappingProxyType({}),
    )


def parse_spatial_map_spec(
    meta: Mapping[str, Any] | None,
) -> SpatialMapSpec | None:
    """Strictly parse the optional ``travel.spatial_map`` authoring block.

    Missing metadata returns ``None`` and preserves legacy map rendering.  An
    explicitly present block is validated normally and may raise
    :class:`pydantic.ValidationError`.
    """
    if not isinstance(meta, Mapping):
        return None
    travel = meta.get("travel")
    if not isinstance(travel, Mapping) or "spatial_map" not in travel:
        return None
    return SpatialMapSpec.model_validate(travel["spatial_map"])


def _presentation_from_spec(spec: SpatialMapSpec) -> SpatialMapPresentation:
    edges = {
        location_id: EdgeDestination(
            location_id=location_id,
            direction=edge.direction,
            thumbnail=edge.thumbnail,
            caption=edge.caption,
            attribution=edge.attribution,
        )
        for location_id, edge in spec.edge_destinations.items()
    }
    return SpatialMapPresentation(
        background=spec.background,
        attribution=spec.attribution,
        bounds=spec.bounds,
        background_fit=spec.background_fit,
        edge_destinations=MappingProxyType(edges),
        nodes=MappingProxyType(dict(spec.nodes)),
    )


def _edge_items(raw: Any) -> list[tuple[str, Mapping[str, Any]]]:
    """Accept the documented id-keyed map plus the historical list form."""
    if isinstance(raw, Mapping):
        return [
            (str(location_id).strip(), value)
            for location_id, value in raw.items()
            if str(location_id).strip() and isinstance(value, Mapping)
        ]
    if not isinstance(raw, list):
        return []
    items: list[tuple[str, Mapping[str, Any]]] = []
    for value in raw:
        if not isinstance(value, Mapping):
            continue
        location_id = _optional_text(
            value.get("location_id", value.get("location")),
        )
        if location_id:
            items.append((location_id, value))
    return items


def _legacy_presentation(raw: Mapping[str, Any]) -> SpatialMapPresentation:
    """Best-effort adapter retained for runtime and old malformed metadata."""
    edge_destinations: dict[str, EdgeDestination] = {}
    for location_id, edge_raw in _edge_items(
        raw.get("edge_destinations", {}),
    ):
        direction = _direction(edge_raw.get("direction"))
        if direction is None:
            continue
        edge_destinations[location_id] = EdgeDestination(
            location_id=location_id,
            direction=direction,
            thumbnail=_optional_text(edge_raw.get("thumbnail")),
            caption=_optional_text(edge_raw.get("caption")),
            attribution=_optional_text(edge_raw.get("attribution")),
        )

    nodes: dict[str, SpatialMapNodeSpec] = {}
    raw_nodes = raw.get("nodes")
    if isinstance(raw_nodes, Mapping):
        for raw_id, raw_node in raw_nodes.items():
            location_id = _optional_text(raw_id)
            if location_id is None or not isinstance(raw_node, Mapping):
                continue
            try:
                nodes[location_id] = SpatialMapNodeSpec.model_validate(
                    raw_node,
                )
            except ValidationError:
                continue

    fit = _optional_text(raw.get("background_fit")) or "cover"
    if fit not in {"cover", "contain", "stretch"}:
        fit = "cover"

    return SpatialMapPresentation(
        background=_optional_text(raw.get("background")),
        attribution=_optional_text(raw.get("attribution")),
        bounds=_bounds(raw.get("bounds")),
        background_fit=fit,
        edge_destinations=MappingProxyType(edge_destinations),
        nodes=MappingProxyType(nodes),
    )


def spatial_map_from_meta(
    meta: Mapping[str, Any] | None,
) -> SpatialMapPresentation | None:
    """Return the immutable runtime presentation without risking travel.

    ``None`` means the pack did not opt in, preserving the legacy auto-scaled
    node graph exactly. A present mapping opts in even when its image or an
    optional field is malformed; validator diagnostics remain strict while the
    renderer uses the valid subset and a procedural canvas.
    """
    if not isinstance(meta, Mapping):
        return None
    travel = meta.get("travel")
    if not isinstance(travel, Mapping):
        return None
    raw = travel.get("spatial_map")
    if not isinstance(raw, Mapping):
        return None
    try:
        return _presentation_from_spec(SpatialMapSpec.model_validate(raw))
    except ValidationError:
        return _legacy_presentation(raw)


__all__ = [
    "BackgroundFit",
    "DEFAULT_MAP_BOUNDS",
    "EdgeDestination",
    "EdgeDestinationSpec",
    "MapBounds",
    "MapDirection",
    "MapLayer",
    "SpatialMapNodeSpec",
    "SpatialMapPresentation",
    "SpatialMapSpec",
    "parse_spatial_map_spec",
    "spatial_map_from_meta",
]
