"""Approximate DLI estimates for the FluxShield Habibi 150 Enhanced."""

from __future__ import annotations

import math

# Datasheet PPFD table (µmol m⁻² s⁻¹), measured at the stated distance and dimmer.
_DIMMING_PCT = (10, 20, 30, 40, 50, 60, 70, 80, 90, 100)
_PPFD_BY_DISTANCE = {
    200: (71, 156, 234, 311, 386, 460, 533, 605, 676, 746),
    300: (65, 131, 195, 260, 322, 384, 446, 506, 565, 624),
    400: (55, 109, 163, 217, 269, 321, 372, 423, 472, 520),
}


def _linear(value: float, points: tuple[float, ...], values: tuple[float, ...]) -> float:
    if value <= points[0]:
        return values[0]
    for index in range(1, len(points)):
        if value <= points[index]:
            fraction = (value - points[index - 1]) / (points[index] - points[index - 1])
            return values[index - 1] + fraction * (values[index] - values[index - 1])
    return values[-1]


def day_length_hours(on: object, off: object) -> float | None:
    """Return scheduled light hours, including overnight schedules; equal times means 24 h."""
    def minutes(value: object) -> int | None:
        if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
            return None
        try:
            hour, minute = (int(part) for part in value.split(":"))
        except ValueError:
            return None
        if not (0 <= hour < 24 and 0 <= minute < 60):
            return None
        return hour * 60 + minute

    start, end = minutes(on), minutes(off)
    if start is None or end is None:
        return None
    return ((end - start) % 1440 or 1440) / 60


def estimate_dli(tof_mm: object, led_pct: object, on: object, off: object) -> float | None:
    """Estimate mol m⁻² day⁻¹ within the datasheet's measured ranges only."""
    hours = day_length_hours(on, off)
    try:
        distance = float(tof_mm)
        dimming = float(led_pct)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(distance) or not math.isfinite(dimming):
        return None
    if not (200 <= distance <= 400 and 0 <= dimming <= 100) or hours is None:
        return None
    if dimming == 0:
        return 0.0

    lower = max(measured_distance for measured_distance in _PPFD_BY_DISTANCE if measured_distance <= distance)
    upper = min(measured_distance for measured_distance in _PPFD_BY_DISTANCE if measured_distance >= distance)
    dimming_points = (0, *_DIMMING_PCT)
    lower_ppfd = _linear(dimming, dimming_points, (0, *_PPFD_BY_DISTANCE[lower]))
    upper_ppfd = _linear(dimming, dimming_points, (0, *_PPFD_BY_DISTANCE[upper]))
    ppfd = lower_ppfd if lower == upper else _linear(distance, (lower, upper), (lower_ppfd, upper_ppfd))
    return ppfd * hours * 3600 / 1_000_000
