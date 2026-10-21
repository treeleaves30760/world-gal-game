"""Strict, pack-authored presentation contract for the title screen.

The engine keeps title-screen composition generic.  A game pack may select a
layout and provide its own logo/text treatment through ``content/meta.yaml``::

    title_screen:
      layout: left_rail
      panel_style: rail
      name_entry: on_new_game
      logo_image: assets/ui/title-logo.png
      wordmark_style: editorial
      tagline: "A story about..."

Packs without the block receive the legacy-compatible defaults.  Present but
malformed blocks are deliberately rejected so authoring tools and the pack
validator can report mistakes instead of silently choosing a different design.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TitleScreenSpec(BaseModel):
    """Pack-driven title-screen presentation, independent of any one game."""

    model_config = ConfigDict(extra="forbid", strict=True)

    layout: Literal["centered", "left_rail", "right_rail"] = "centered"
    panel_style: Literal["glass", "rail"] = "glass"
    name_entry: Literal["inline", "on_new_game"] = "inline"
    logo_image: str | None = None
    wordmark_style: Literal["classic", "editorial"] = "classic"
    tagline: str = ""
    background_opacity: float = Field(default=205 / 255, ge=0.0, le=1.0)
    show_engine_credit: bool = True
    footer_text: str = ""


def parse_title_screen_spec(
    meta: Mapping[str, Any] | None,
) -> TitleScreenSpec:
    """Return the strict title-screen specification declared by ``meta``.

    Missing metadata (or a metadata mapping without ``title_screen``) maps to
    :class:`TitleScreenSpec` defaults, preserving every existing pack.  An
    explicitly present block, including ``null``, is validated normally and
    may raise :class:`pydantic.ValidationError`.
    """
    if not isinstance(meta, Mapping) or "title_screen" not in meta:
        return TitleScreenSpec()
    return TitleScreenSpec.model_validate(meta["title_screen"])


__all__ = ["TitleScreenSpec", "parse_title_screen_spec"]
