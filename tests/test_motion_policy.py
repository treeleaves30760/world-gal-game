"""Central reduced-motion policy and live preference propagation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from world_gal_game.ui.motion import MotionKind, MotionPolicy


def test_policy_observes_live_config_changes() -> None:
    config = SimpleNamespace(reduce_motion=False)
    policy = MotionPolicy.from_config(config)

    assert policy.reduced is False
    assert policy.continuous_allowed is True

    config.reduce_motion = True

    assert policy.reduced is True
    assert policy.continuous_allowed is False


@pytest.mark.parametrize(
    ("kind", "allowed", "duration"),
    [
        (MotionKind.FEEDBACK, True, 0.0),
        (MotionKind.OPACITY, True, MotionPolicy.REDUCED_OPACITY_CAP),
        (MotionKind.SPATIAL, False, 0.0),
        (MotionKind.FLASH, False, 0.0),
        (MotionKind.CONTINUOUS, False, 0.0),
        (MotionKind.CONTENT, True, 0.8),
    ],
)
def test_reduced_policy_preserves_state_and_content_but_removes_motion(
    kind: MotionKind,
    allowed: bool,
    duration: float,
) -> None:
    policy = MotionPolicy(True)

    assert policy.allows(kind) is allowed
    assert policy.duration(0.8, kind) == pytest.approx(duration)


@pytest.mark.parametrize(
    "style",
    [
        "dissolve",
        "fade",
        "wipe_left",
        "slide_right",
        "iris_out",
        "blinds_h",
        "pixellate",
        "mask",
    ],
)
def test_reduced_transition_degrades_to_short_dissolve(style: str) -> None:
    assert MotionPolicy(True).transition(style, 0.9) == (
        "dissolve",
        MotionPolicy.REDUCED_OPACITY_CAP,
    )


def test_explicit_cut_stays_instant_and_full_motion_stays_authored() -> None:
    assert MotionPolicy(True).transition("cut", 0.9) == ("cut", 0.0)
    assert MotionPolicy(False).transition("wipe_left", 0.9) == (
        "wipe_left",
        0.9,
    )


def test_feedback_interpolation_snaps_only_in_reduced_mode() -> None:
    assert MotionPolicy(True).feedback_factor(1 / 60, 12) == 1.0
    assert MotionPolicy(False).feedback_factor(1 / 60, 12) == pytest.approx(
        0.2,
    )
