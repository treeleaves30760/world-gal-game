"""Central accessibility policy for presentation motion.

``EngineConfig.reduce_motion`` is a live player preference.  UI code should not
copy that boolean at construction time: Settings can be opened over an already
running dialogue or map, and the underlying widgets must react as soon as the
player returns.  :class:`MotionPolicy` therefore keeps a reference to the
configuration (or another callable source) and evaluates it on demand.

The policy classifies motion by purpose rather than by game-specific effect
names.  Scenes remain responsible for mapping an authored effect to a
:class:`MotionKind`; plugins and widgets can then share the same rules without
putting pack logic in the engine core.
"""
from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Any


class MotionKind(str, Enum):
    """Semantic motion categories understood by :class:`MotionPolicy`."""

    # Immediate focus/hover/selection state is essential, but its interpolation
    # is not.  Reduced mode keeps the state change and settles it immediately.
    FEEDBACK = "feedback"
    # A short dissolve can make a scene/image hand-off easier to parse without
    # moving geometry through the viewport.
    OPACITY = "opacity"
    # Camera movement, slides, rises, pops, bounces, wipes and parallax.
    SPATIAL = "spatial"
    # Flashes and other photosensitivity hazards.
    FLASH = "flash"
    # Breathing, particles, pulses, blinking and other indefinite decoration.
    CONTINUOUS = "continuous"
    # Authored time-based content: typewriter progression, voice, and movies.
    # Reduced motion does not silently delete narrative content.
    CONTENT = "content"


class MotionPolicy:
    """Live, engine-owned interpretation of the reduce-motion preference.

    ``source`` may be a bool, a callable returning a bool, or any object with a
    ``reduce_motion`` attribute (normally :class:`EngineConfig`).  Keeping the
    source live prevents stale widgets after the setting is toggled in an
    overlay.
    """

    REDUCED_OPACITY_CAP = 0.12

    def __init__(self, source: bool | Callable[[], bool] | Any = False) -> None:
        self._source = source

    @classmethod
    def from_config(cls, config: Any) -> "MotionPolicy":
        return cls(config)

    @property
    def reduced(self) -> bool:
        source = self._source
        try:
            if callable(source):
                return bool(source())
            if isinstance(source, bool):
                return source
            return bool(getattr(source, "reduce_motion", False))
        except Exception:
            # Accessibility plumbing must never make a frame fail.  A broken
            # external source degrades to the historical full-motion default.
            return False

    def allows(self, kind: MotionKind) -> bool:
        """Whether ``kind`` may remain present in the current preference."""
        if not self.reduced:
            return True
        return kind in {
            MotionKind.FEEDBACK,
            MotionKind.OPACITY,
            MotionKind.CONTENT,
        }

    @property
    def continuous_allowed(self) -> bool:
        return self.allows(MotionKind.CONTINUOUS)

    def duration(self, seconds: float, kind: MotionKind) -> float:
        """Return a policy-safe duration for one presentation operation."""
        try:
            value = max(0.0, float(seconds))
        except (TypeError, ValueError):
            value = 0.0
        if not self.reduced or kind is MotionKind.CONTENT:
            return value
        if kind is MotionKind.OPACITY:
            return min(value, self.REDUCED_OPACITY_CAP)
        # Feedback remains visible but snaps to its final state; spatial,
        # flashing and continuous animation are disabled.
        return 0.0

    def feedback_factor(self, dt: float, speed: float) -> float:
        """Interpolation factor for finite UI feedback such as hover colour."""
        if self.reduced:
            return 1.0
        try:
            return min(1.0, max(0.0, float(dt)) * max(0.0, float(speed)))
        except (TypeError, ValueError):
            return 1.0

    def transition(self, style: str, duration: float) -> tuple[str, float]:
        """Return a scene-transition style/duration compliant with the policy.

        Reduced mode keeps explicit cuts, and turns every animated transition
        (including wipes, slides, masks and pixellation) into one short
        dissolve.  The authored state change still occurs; only its motion-heavy
        hand-off is replaced.
        """
        name = str(style or "dissolve")
        if not self.reduced:
            return name, self.duration(duration, MotionKind.OPACITY)
        if name == "cut":
            return "cut", 0.0
        return (
            "dissolve",
            self.duration(duration, MotionKind.OPACITY),
        )


FULL_MOTION = MotionPolicy(False)


def coerce_motion_policy(
    motion: MotionPolicy | None = None,
    *,
    reduce_motion: bool = False,
) -> MotionPolicy:
    """Compatibility helper for widgets that historically accepted a bool."""
    return motion if motion is not None else MotionPolicy(bool(reduce_motion))


__all__ = [
    "FULL_MOTION",
    "MotionKind",
    "MotionPolicy",
    "coerce_motion_policy",
]
