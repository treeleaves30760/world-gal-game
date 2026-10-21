"""Foundation contract for the live visual-accessibility policy."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from world_gal_game.ui.theme import Theme
from world_gal_game.ui.visual import (
    VisualMode,
    VisualPolicy,
    coerce_visual_mode,
)


def _linear_channel(channel: int) -> float:
    value = channel / 255.0
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def _luminance(color: tuple[int, ...]) -> float:
    red, green, blue = (_linear_channel(value) for value in color[:3])
    return (0.2126 * red) + (0.7152 * green) + (0.0722 * blue)


def _contrast_ratio(
    foreground: tuple[int, ...],
    background: tuple[int, ...],
) -> float:
    lighter = max(_luminance(foreground), _luminance(background))
    darker = min(_luminance(foreground), _luminance(background))
    return (lighter + 0.05) / (darker + 0.05)


def _composite_over(
    foreground: tuple[int, ...],
    background: tuple[int, ...],
) -> tuple[int, int, int]:
    if len(foreground) == 3:
        return foreground
    alpha = foreground[3] / 255.0
    return tuple(
        round((foreground[channel] * alpha) + (background[channel] * (1 - alpha)))
        for channel in range(3)
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (VisualMode.STANDARD, VisualMode.STANDARD),
        (VisualMode.HIGH_CONTRAST, VisualMode.HIGH_CONTRAST),
        ("standard", VisualMode.STANDARD),
        ("high_contrast", VisualMode.HIGH_CONTRAST),
    ],
)
def test_coerce_visual_mode_accepts_only_canonical_values(
    raw: object,
    expected: VisualMode,
) -> None:
    assert coerce_visual_mode(raw) is expected


@pytest.mark.parametrize(
    "malformed",
    [None, False, True, 0, 1, "", "HIGH_CONTRAST", " high_contrast", b"standard"],
)
def test_coerce_visual_mode_strictly_falls_back_to_standard(
    malformed: object,
) -> None:
    assert coerce_visual_mode(malformed) is VisualMode.STANDARD


def test_policy_observes_live_config_switches() -> None:
    config = SimpleNamespace(visual_accessibility_mode="standard")
    policy = VisualPolicy.from_config(config)

    assert policy.mode is VisualMode.STANDARD
    assert policy.profile == "standard"
    assert policy.high_contrast is False

    config.visual_accessibility_mode = "high_contrast"

    assert policy.mode is VisualMode.HIGH_CONTRAST
    assert policy.profile == "high_contrast"
    assert policy.high_contrast is True

    config.visual_accessibility_mode = "unsupported"

    assert policy.mode is VisualMode.STANDARD
    assert policy.profile == "standard"
    assert policy.high_contrast is False


def test_broken_live_source_falls_back_without_disrupting_standard_theme() -> None:
    class BrokenConfig:
        @property
        def visual_accessibility_mode(self) -> str:
            raise RuntimeError("unavailable source")

    base = Theme(accent=(1, 2, 3), pad_l=37)
    policy = VisualPolicy.from_config(BrokenConfig())

    assert policy.mode is VisualMode.STANDARD
    assert policy.profile == "standard"
    assert policy.high_contrast is False
    assert policy.theme(base) is base


def test_standard_mode_is_a_pixel_preserving_identity_operation() -> None:
    base = Theme(
        bg_deep=(12, 34, 56),
        accent=(78, 90, 123),
        text=(222, 210, 200),
        pad_m=27,
        radius_l=31,
    )

    assert VisualPolicy("standard").theme(base) is base


def test_theme_follows_a_live_switch_and_preserves_non_colour_metrics() -> None:
    config = SimpleNamespace(visual_accessibility_mode="standard")
    policy = VisualPolicy.from_config(config)
    base = Theme(pad_xs=3, pad_s=7, pad_m=19, pad_l=29, pad_xl=41,
                 radius_s=4, radius_m=13, radius_l=25)

    assert policy.theme(base) is base

    config.visual_accessibility_mode = "high_contrast"
    high_contrast = policy.theme(base)

    assert high_contrast is not base
    for field in (
        "pad_xs",
        "pad_s",
        "pad_m",
        "pad_l",
        "pad_xl",
        "radius_s",
        "radius_m",
        "radius_l",
    ):
        assert getattr(high_contrast, field) == getattr(base, field)

    config.visual_accessibility_mode = "standard"
    assert policy.theme(base) is base


def test_high_contrast_palette_meets_semantic_contrast_contract() -> None:
    palette = VisualPolicy(VisualMode.HIGH_CONTRAST).theme(Theme())
    backgrounds = (palette.bg_deep, palette.bg_panel, palette.bg_overlay)

    assert backgrounds == (
        (0, 0, 0),
        (0, 0, 0, 255),
        (0, 0, 0, 255),
    )
    assert palette.text == (255, 255, 255)

    for foreground in (palette.text, palette.text_mute, palette.text_dim):
        for background in backgrounds:
            assert _contrast_ratio(foreground, background) >= 7.0

    semantic_colors = (
        palette.accent,
        palette.accent_warm,
        palette.good,
        palette.warn,
    )
    assert len(set(semantic_colors)) == len(semantic_colors)
    for foreground in semantic_colors:
        assert _contrast_ratio(foreground, palette.bg_deep) >= 7.0

    # Cyan / amber / green / red semantics remain visibly differentiated even
    # before downstream widgets add the required text, shape, line, or pattern.
    assert palette.accent[1] > palette.accent[0]
    assert palette.accent[2] > palette.accent[0]
    assert palette.accent_warm[0] > palette.accent_warm[2]
    assert palette.accent_warm[1] > palette.accent_warm[2]
    assert palette.good[1] > palette.good[0]
    assert palette.good[1] > palette.good[2]
    assert palette.warn[0] > palette.warn[1]
    assert palette.warn[0] > palette.warn[2]

    for border in (palette.border, palette.border_soft, palette.border_strong):
        for background in backgrounds:
            composite = _composite_over(border, background)
            assert _contrast_ratio(composite, background) >= 3.0
