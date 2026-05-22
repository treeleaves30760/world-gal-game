"""Tests for the easing function library.

Guarantees the contract callers rely on: every easing maps 0->0 and 1->1,
in_out_* curves are symmetric through (0.5, 0.5), non-overshooting curves are
monotonic, input is clamped, and resolve() degrades to linear.
"""
from __future__ import annotations

import pytest

from world_gal_game.ui import easing


ALL_NAMES = list(easing.EASINGS)
IN_OUT_NAMES = [n for n in ALL_NAMES if n.startswith("in_out_")]
# Non-overshooting curves: monotonic non-decreasing on [0, 1].
MONOTONIC_NAMES = [
    "linear",
    "in_quad", "out_quad", "in_out_quad",
    "in_cubic", "out_cubic", "in_out_cubic",
    "in_sine", "out_sine", "in_out_sine",
]


@pytest.mark.parametrize("name", ALL_NAMES)
def test_endpoints_anchored(name: str) -> None:
    fn = easing.EASINGS[name]
    assert fn(0.0) == pytest.approx(0.0, abs=1e-9)
    assert fn(1.0) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.parametrize("name", IN_OUT_NAMES)
def test_in_out_symmetric_midpoint(name: str) -> None:
    fn = easing.EASINGS[name]
    assert fn(0.5) == pytest.approx(0.5, abs=1e-9)


@pytest.mark.parametrize("name", MONOTONIC_NAMES)
def test_monotonic_non_decreasing(name: str) -> None:
    fn = easing.EASINGS[name]
    prev = fn(0.0)
    for i in range(1, 101):
        cur = fn(i / 100.0)
        assert cur >= prev - 1e-9, f"{name} decreased at t={i/100.0}"
        prev = cur


@pytest.mark.parametrize("name", ALL_NAMES)
def test_input_is_clamped(name: str) -> None:
    fn = easing.EASINGS[name]
    assert fn(-0.5) == pytest.approx(fn(0.0), abs=1e-9)
    assert fn(1.5) == pytest.approx(fn(1.0), abs=1e-9)


def test_linear_is_identity() -> None:
    for t in (0.0, 0.25, 0.5, 0.75, 1.0):
        assert easing.linear(t) == pytest.approx(t)


def test_resolve_none_is_linear() -> None:
    assert easing.resolve(None) is easing.linear


def test_resolve_unknown_name_is_linear() -> None:
    assert easing.resolve("does_not_exist") is easing.linear


def test_resolve_known_name() -> None:
    assert easing.resolve("out_cubic") is easing.out_cubic


def test_resolve_passthrough_callable() -> None:
    f = lambda t: t  # noqa: E731
    assert easing.resolve(f) is f


def test_easing_names_matches_registry() -> None:
    assert easing.EASING_NAMES == tuple(easing.EASINGS)
    assert "linear" in easing.EASING_NAMES
    assert "out_cubic" in easing.EASING_NAMES
