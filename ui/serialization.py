"""Helpers for values sent through Dash JSON props/stores."""

from __future__ import annotations

import math
from collections.abc import Mapping
from numbers import Integral, Real
from typing import Any


def sanitize_for_dash_json(value: Any, *, zero_tolerance: float = 1e-9) -> Any:
    """
    Recursively normalize values before sending them to Dash/React.

    Dash store payloads eventually become JavaScript props. Tiny floating-point
    residuals can round to negative zero (``-0``) in the browser, and Dash/React
    can get stuck repeatedly diffing ``0`` versus ``-0`` for some payloads.
    """
    if value is None or isinstance(value, (str, bool)):
        return value

    if isinstance(value, Integral):
        return int(value)

    if isinstance(value, Real):
        number = float(value)
        if not math.isfinite(number):
            return None
        if abs(number) <= zero_tolerance:
            return 0.0
        return number

    if isinstance(value, Mapping):
        return {
            key: sanitize_for_dash_json(item, zero_tolerance=zero_tolerance)
            for key, item in value.items()
        }

    if isinstance(value, tuple):
        return [
            sanitize_for_dash_json(item, zero_tolerance=zero_tolerance)
            for item in value
        ]

    if isinstance(value, list):
        return [
            sanitize_for_dash_json(item, zero_tolerance=zero_tolerance)
            for item in value
        ]

    return value
