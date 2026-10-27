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


def mixed_air_vpd_kpa(
    temp_inside_c: float,
    vapor_pressure_inside_kpa: float,
    temp_outside_c: float,
    vapor_pressure_outside_kpa: float,
    outside_fraction: float,
    *,
    pressure_kpa: float = 101.325,
) -> Optional[float]:
    """Estimate VPD after adiabatically mixing indoor and outdoor air.

    Humidity ratio and moist-air enthalpy are mixed by dry-air mass. This
    captures both moisture and temperature changes; it is a direction estimate,
    not an airflow or steady-state greenhouse model.
    """
    try:
        temp_inside = float(temp_inside_c)
        vapor_inside = float(vapor_pressure_inside_kpa)
        temp_outside = float(temp_outside_c)
        vapor_outside = float(vapor_pressure_outside_kpa)
        fraction = float(outside_fraction)
        pressure = float(pressure_kpa)
    except (TypeError, ValueError):
        return None
    values = (temp_inside, vapor_inside, temp_outside, vapor_outside, fraction, pressure)
    if not all(math.isfinite(value) for value in values):
        return None
    if not 0.0 <= fraction <= 1.0 or pressure <= 0.0:
        return None
    if not 0.0 <= vapor_inside < pressure or not 0.0 <= vapor_outside < pressure:
        return None

    molecular_ratio = 0.621945
    humidity_inside = molecular_ratio * vapor_inside / (pressure - vapor_inside)
    humidity_outside = molecular_ratio * vapor_outside / (pressure - vapor_outside)
    enthalpy_inside = (
        1.006 * temp_inside + humidity_inside * (2501.0 + 1.86 * temp_inside)
    )
    enthalpy_outside = (
        1.006 * temp_outside + humidity_outside * (2501.0 + 1.86 * temp_outside)
    )

    humidity_mixed = (1.0 - fraction) * humidity_inside + fraction * humidity_outside
    enthalpy_mixed = (1.0 - fraction) * enthalpy_inside + fraction * enthalpy_outside
    temp_mixed = (enthalpy_mixed - 2501.0 * humidity_mixed) / (
        1.006 + 1.86 * humidity_mixed
    )
    vapor_mixed = pressure * humidity_mixed / (molecular_ratio + humidity_mixed)
    return max(0.0, saturation_vapor_pressure_kpa(temp_mixed) - vapor_mixed)
