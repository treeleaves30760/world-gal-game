"""Strict selector for a pack-owned UI chrome renderer.

The engine deliberately does not contain game-specific themes.  A pack may
select a widget registered by one of its plugins and pass that renderer an
opaque options mapping::

    ui_skin:
      renderer: my_pack_system_chrome
      options:
        frame_style: field_guide

Only the selector is engine-owned.  The actual art direction, option schema,
and drawing code remain inside the plugin.  Packs without ``ui_skin`` keep the
legacy engine rendering byte-for-byte.
"""
from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


_RENDERER_RE = re.compile(r"^[a-z][a-z0-9_]{1,127}$")


class UISkinSpec(BaseModel):
    """Select one ``@widget`` implementing the ``ui_chrome/v1`` protocol."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )

    renderer: str = Field(
        min_length=2,
        max_length=128,
        description=(
            "Registered @widget name whose signature declares "
            "protocol=ui_chrome/v1."
        ),
    )
    options: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Renderer-owned options. A renderer may expose OptionsModel for "
            "validator-backed, plugin-specific validation."
        ),
    )

    @field_validator("renderer")
    @classmethod
    def _validate_renderer(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("renderer must not contain surrounding whitespace")
        if not _RENDERER_RE.fullmatch(value):
            raise ValueError(
                "renderer must match [a-z][a-z0-9_]{1,127}",
            )
        return value

    @field_validator("options")
    @classmethod
    def _validate_option_keys(cls, value: dict[str, Any]) -> dict[str, Any]:
        for key in value:
            if not key or key != key.strip():
                raise ValueError(
                    "option keys must be non-empty strings without "
                    "surrounding whitespace",
                )
        return value


def parse_ui_skin_spec(
    meta: Mapping[str, Any] | None,
) -> UISkinSpec | None:
    """Parse ``meta.ui_skin`` or return ``None`` when the block is absent.

    An explicitly present malformed block, including ``null``, raises a normal
    Pydantic validation error.  This makes authoring mistakes visible while
    preserving exact legacy behaviour for every existing pack.
    """
    if not isinstance(meta, Mapping) or "ui_skin" not in meta:
        return None
    return UISkinSpec.model_validate(meta["ui_skin"])


__all__ = ["UISkinSpec", "parse_ui_skin_spec"]
