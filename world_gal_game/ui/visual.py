"""Live visual-accessibility policy and its engine-owned semantic palette.

The policy deliberately operates on :class:`~world_gal_game.ui.theme.Theme`
only.  It does not inspect, transform, tint, or otherwise replace authored
backgrounds, CGs, portraits, or pack assets.  Standard mode is also an identity
operation so introducing the policy cannot change existing pixels.
"""
from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Any

from .theme import Theme


class VisualMode(str, Enum):
    """Persisted visual-accessibility profiles understood by the engine."""

    STANDARD = "standard"
    HIGH_CONTRAST = "high_contrast"


def coerce_visual_mode(value: object) -> VisualMode:
    """Return a supported mode, falling back to standard for every other value.

    Values are intentionally not stripped, case-folded, or truth-coerced.  The
    settings document has one canonical representation and malformed external
    values cannot silently enable a different presentation profile.
    """
    if isinstance(value, VisualMode):
        return value
    if type(value) is str:
        try:
            return VisualMode(value)
        except ValueError:
            pass
    return VisualMode.STANDARD


class VisualPolicy:
    """Live interpretation of the visual-accessibility preference.

    ``source`` may be a mode value, a callable returning one, or an object with
    a ``visual_accessibility_mode`` attribute (normally ``EngineConfig``).
    Keeping that source instead of copying its current value lets an existing
    scene observe a Settings change on its next frame.
    """

    def __init__(
        self,
        source: VisualMode | str | Callable[[], object] | Any = (
            VisualMode.STANDARD
        ),
    ) -> None:
        self._source = source

    @classmethod
    def from_config(cls, config: Any) -> "VisualPolicy":
        """Bind a policy to a live configuration object."""
        return cls(config)

    @property
    def mode(self) -> VisualMode:
        """Current canonical mode, evaluated from the source on every access."""
        source = self._source
        try:
            if callable(source):
                value = source()
            elif isinstance(source, (VisualMode, str)):
                value = source
            else:
                value = getattr(
                    source,
                    "visual_accessibility_mode",
                    VisualMode.STANDARD.value,
                )
        except Exception:
            # A faulty embedding/source must never prevent a frame from
            # rendering.  Standard mode is the fail-safe, pixel-preserving path.
            return VisualMode.STANDARD
        return coerce_visual_mode(value)

    @property
    def profile(self) -> str:
        """Stable string identity suitable for renderer/cache keys."""
        return self.mode.value

    @property
    def high_contrast(self) -> bool:
        """Whether the high-contrast semantic palette is currently active."""
        return self.mode is VisualMode.HIGH_CONTRAST

    def theme(self, base_theme: Theme) -> Theme:
        """Return the live policy theme layered over ``base_theme``.

        Standard mode returns ``base_theme`` itself.  High contrast keeps every
        non-colour metric from the pack theme while replacing semantic UI
        colours with opaque, contrast-measurable engine values.
        """
        if not self.high_contrast:
            return base_theme
        return base_theme.overridden(
            bg_deep=(0, 0, 0),
            bg_panel=(0, 0, 0, 255),
            bg_overlay=(0, 0, 0, 255),
            accent=(0, 229, 255),
            accent_alt=(127, 219, 255),
            accent_warm=(255, 214, 0),
            text=(255, 255, 255),
            text_mute=(211, 211, 211),
            text_dim=(176, 176, 176),
            good=(96, 255, 160),
            warn=(255, 135, 135),
            border=(0, 229, 255, 255),
            border_soft=(166, 166, 166, 255),
            border_strong=(255, 214, 0, 255),
        )


class VisualTheme:
    """Live, cache-light view of an authored :class:`Theme`.

    Widgets retain theme objects for the life of a scene.  Wrapping the pack's
    authored theme lets those already-constructed widgets observe a Settings
    switch on their next draw without mutating the pack palette.  Standard
    mode resolves to the exact authored object; the high-contrast replacement
    contains UI colours only and therefore cannot filter world art.
    """

    __slots__ = ("_base", "_policy", "_profile", "_resolved")

    def __init__(self, base: Theme, policy: VisualPolicy) -> None:
        self._base = base
        self._policy = policy
        self._profile = ""
        self._resolved = base

    @property
    def base_theme(self) -> Theme:
        """The unchanged pack-authored theme."""
        return self._base

    @property
    def visual_profile(self) -> str:
        """Current canonical profile for stock-widget fallback decisions."""
        return self._policy.profile

    @property
    def resolved_theme(self) -> Theme:
        """Current concrete theme, recomputed only when the profile changes."""
        profile = self._policy.profile
        if profile != self._profile:
            self._resolved = self._policy.theme(self._base)
            self._profile = profile
        return self._resolved

    def __getattr__(self, name: str) -> Any:
        return getattr(self.resolved_theme, name)

    def overridden(self, **kwargs: Any) -> "VisualTheme":
        """Preserve liveness when a consumer derives a metric/theme variant."""
        return VisualTheme(self._base.overridden(**kwargs), self._policy)


__all__ = [
    "VisualMode",
    "VisualPolicy",
    "VisualTheme",
    "coerce_visual_mode",
]
