"""Easing functions for animations and transitions.

Every easing is a pure function ``f(t: float) -> float`` mapping a normalized
time ``t`` in ``[0, 1]`` to an eased progress value. ``linear`` is the identity.
Most curves stay within ``[0, 1]``; ``back``/``elastic`` deliberately overshoot
slightly (that overshoot is the effect). All functions clamp their input so a
frame-rate spike that pushes ``t`` past 1.0 never produces garbage.

The module is dependency-free (``math`` only) so it can be imported anywhere,
including from the validator and capability manifest. ``EASINGS`` is the
name -> function registry; ``EASING_NAMES`` is the discoverable name list used
by tooling; ``resolve()`` turns a name (or callable, or None) into a function,
defaulting to ``linear`` so callers that pass nothing keep linear behavior.
"""
from __future__ import annotations

import math
from typing import Callable

EasingFn = Callable[[float], float]


def _clamp01(t: float) -> float:
    if t < 0.0:
        return 0.0
    if t > 1.0:
        return 1.0
    return t


# ---------- linear ----------------------------------------------------------


def linear(t: float) -> float:
    return _clamp01(t)


# ---------- quadratic -------------------------------------------------------


def in_quad(t: float) -> float:
    t = _clamp01(t)
    return t * t


def out_quad(t: float) -> float:
    t = _clamp01(t)
    return 1.0 - (1.0 - t) * (1.0 - t)


def in_out_quad(t: float) -> float:
    t = _clamp01(t)
    if t < 0.5:
        return 2.0 * t * t
    return 1.0 - ((-2.0 * t + 2.0) ** 2) / 2.0


# ---------- cubic -----------------------------------------------------------


def in_cubic(t: float) -> float:
    t = _clamp01(t)
    return t * t * t


def out_cubic(t: float) -> float:
    t = _clamp01(t)
    return 1.0 - (1.0 - t) ** 3


def in_out_cubic(t: float) -> float:
    t = _clamp01(t)
    if t < 0.5:
        return 4.0 * t * t * t
    return 1.0 - ((-2.0 * t + 2.0) ** 3) / 2.0


# ---------- sine ------------------------------------------------------------


def in_sine(t: float) -> float:
    t = _clamp01(t)
    return 1.0 - math.cos((t * math.pi) / 2.0)


def out_sine(t: float) -> float:
    t = _clamp01(t)
    return math.sin((t * math.pi) / 2.0)


def in_out_sine(t: float) -> float:
    t = _clamp01(t)
    return -(math.cos(math.pi * t) - 1.0) / 2.0


# ---------- back (slight overshoot) -----------------------------------------

_BACK_C1 = 1.70158
_BACK_C2 = _BACK_C1 * 1.525
_BACK_C3 = _BACK_C1 + 1.0


def in_back(t: float) -> float:
    t = _clamp01(t)
    return _BACK_C3 * t * t * t - _BACK_C1 * t * t


def out_back(t: float) -> float:
    t = _clamp01(t)
    return 1.0 + _BACK_C3 * ((t - 1.0) ** 3) + _BACK_C1 * ((t - 1.0) ** 2)


def in_out_back(t: float) -> float:
    t = _clamp01(t)
    if t < 0.5:
        return ((2.0 * t) ** 2 * ((_BACK_C2 + 1.0) * 2.0 * t - _BACK_C2)) / 2.0
    return ((2.0 * t - 2.0) ** 2 * ((_BACK_C2 + 1.0) * (t * 2.0 - 2.0) + _BACK_C2) + 2.0) / 2.0


# ---------- elastic (oscillating overshoot) ---------------------------------

_ELASTIC_C4 = (2.0 * math.pi) / 3.0
_ELASTIC_C5 = (2.0 * math.pi) / 4.5


def in_elastic(t: float) -> float:
    t = _clamp01(t)
    if t == 0.0 or t == 1.0:
        return t
    return -(2.0 ** (10.0 * t - 10.0)) * math.sin((t * 10.0 - 10.75) * _ELASTIC_C4)


def out_elastic(t: float) -> float:
    t = _clamp01(t)
    if t == 0.0 or t == 1.0:
        return t
    return (2.0 ** (-10.0 * t)) * math.sin((t * 10.0 - 0.75) * _ELASTIC_C4) + 1.0


def in_out_elastic(t: float) -> float:
    t = _clamp01(t)
    if t == 0.0 or t == 1.0:
        return t
    if t < 0.5:
        return -((2.0 ** (20.0 * t - 10.0)) * math.sin((20.0 * t - 11.125) * _ELASTIC_C5)) / 2.0
    return ((2.0 ** (-20.0 * t + 10.0)) * math.sin((20.0 * t - 11.125) * _ELASTIC_C5)) / 2.0 + 1.0


# ---------- bounce ----------------------------------------------------------

_BOUNCE_N1 = 7.5625
_BOUNCE_D1 = 2.75


def out_bounce(t: float) -> float:
    t = _clamp01(t)
    if t < 1.0 / _BOUNCE_D1:
        return _BOUNCE_N1 * t * t
    if t < 2.0 / _BOUNCE_D1:
        t -= 1.5 / _BOUNCE_D1
        return _BOUNCE_N1 * t * t + 0.75
    if t < 2.5 / _BOUNCE_D1:
        t -= 2.25 / _BOUNCE_D1
        return _BOUNCE_N1 * t * t + 0.9375
    t -= 2.625 / _BOUNCE_D1
    return _BOUNCE_N1 * t * t + 0.984375


def in_bounce(t: float) -> float:
    return 1.0 - out_bounce(1.0 - _clamp01(t))


def in_out_bounce(t: float) -> float:
    t = _clamp01(t)
    if t < 0.5:
        return (1.0 - out_bounce(1.0 - 2.0 * t)) / 2.0
    return (1.0 + out_bounce(2.0 * t - 1.0)) / 2.0


# ---------- registry --------------------------------------------------------

EASINGS: dict[str, EasingFn] = {
    "linear": linear,
    "in_quad": in_quad,
    "out_quad": out_quad,
    "in_out_quad": in_out_quad,
    "in_cubic": in_cubic,
    "out_cubic": out_cubic,
    "in_out_cubic": in_out_cubic,
    "in_sine": in_sine,
    "out_sine": out_sine,
    "in_out_sine": in_out_sine,
    "in_back": in_back,
    "out_back": out_back,
    "in_out_back": in_out_back,
    "in_elastic": in_elastic,
    "out_elastic": out_elastic,
    "in_out_elastic": in_out_elastic,
    "in_bounce": in_bounce,
    "out_bounce": out_bounce,
    "in_out_bounce": in_out_bounce,
}

# Discoverable, stable-ordered list for tooling (capability manifest, validator).
EASING_NAMES: tuple[str, ...] = tuple(EASINGS)


def resolve(easing: str | EasingFn | None) -> EasingFn:
    """Turn a name / callable / None into an easing function.

    - ``None`` -> ``linear`` (so callers that pass nothing are unchanged).
    - a callable -> returned as-is.
    - a known name -> the registered function.
    - an unknown name -> ``linear`` (degrade gracefully, never raise).
    """
    if easing is None:
        return linear
    if callable(easing):
        return easing
    return EASINGS.get(easing, linear)
