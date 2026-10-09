"""Vapor pressure deficit helpers (FAO-56 / Tetens)."""

from __future__ import annotations

import math
from typing import Optional


def saturation_vapor_pressure_kpa(temp_c: float) -> float:
    """Saturation vapor pressure in kPa (Tetens / FAO-56)."""
    return 0.6108 * math.exp((17.27 * temp_c) / (temp_c + 237.3))


def dew_point_c(temp_c: Optional[float], rh_pct: Optional[float]) -> Optional[float]:
    """Calculate dew point (°C) from air temperature and relative humidity."""
    if temp_c is None or rh_pct is None:
        return None
    try:
        temp = float(temp_c)
        rh = float(rh_pct)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(temp) or not math.isfinite(rh):
        return None
    rh = max(0.0, min(100.0, rh))
    if rh <= 0.0:
        return None
    alpha = math.log(rh / 100.0) + (17.27 * temp) / (237.3 + temp)
    return (237.3 * alpha) / (17.27 - alpha)


def vpd_kpa(temp_c: Optional[float], rh_pct: Optional[float]) -> Optional[float]:
    """Air VPD in kPa from temperature (°C) and relative humidity (%)."""
    if temp_c is None or rh_pct is None:
        return None
    try:
        t = float(temp_c)
        rh = float(rh_pct)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(t) or not math.isfinite(rh):
        return None
    rh = max(0.0, min(100.0, rh))
    svp = saturation_vapor_pressure_kpa(t)
    return svp * (1.0 - rh / 100.0)


def vpd_from_dew_point_kpa(
    temp_c: Optional[float], dew_point_c: Optional[float]
) -> Optional[float]:
    """Calculate air VPD from inside temperature and dew point in °C."""
    if temp_c is None or dew_point_c is None:
        return None
    try:
        temp = float(temp_c)
        dew_point = float(dew_point_c)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(temp) or not math.isfinite(dew_point):
        return None
    actual_vapor_pressure = saturation_vapor_pressure_kpa(dew_point)
    return max(0.0, saturation_vapor_pressure_kpa(temp) - actual_vapor_pressure)
