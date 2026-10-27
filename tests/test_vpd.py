from lisa_pro_ui.vpd import (
    dew_point_c,
    mixed_air_vpd_kpa,
    saturation_vapor_pressure_kpa,
    vpd_from_dew_point_kpa,
    vpd_kpa,
)


def test_svp_known_value():
    # FAO-56 reference around 25°C is ~3.17 kPa
    assert abs(saturation_vapor_pressure_kpa(25.0) - 3.168) < 0.02


def test_vpd_25c_60rh():
    assert abs(vpd_kpa(25.0, 60.0) - 1.267) < 0.02


def test_vpd_rejects_missing():
    assert vpd_kpa(None, 50) is None
    assert vpd_kpa(20, None) is None


def test_vpd_clamps_rh():
    assert vpd_kpa(20.0, 150.0) == 0.0
    assert abs(vpd_kpa(20.0, -10.0) - saturation_vapor_pressure_kpa(20.0)) < 1e-9


def test_dew_point_from_temperature_and_humidity():
    assert abs(dew_point_c(22.0, 59.0) - 13.62) < 0.05


def test_dew_point_rejects_missing_or_zero_humidity():
    assert dew_point_c(None, 50.0) is None
    assert dew_point_c(22.0, None) is None
    assert dew_point_c(22.0, 0.0) is None


def test_outside_dew_point_converted_to_inside_temperature_vpd():
    outside_dew_point = dew_point_c(22.0, 59.0)
    ventilation_limit = vpd_from_dew_point_kpa(23.0, outside_dew_point)
    assert abs(ventilation_limit - 1.25) < 0.02


def test_mixed_air_vpd_includes_temperature_and_moisture_changes():
    inside_vapor = saturation_vapor_pressure_kpa(25.0) * 0.48
    cooler_outside_vapor = saturation_vapor_pressure_kpa(22.0) * 0.50
    cooler_mix_vpd = mixed_air_vpd_kpa(
        25.0, inside_vapor, 22.0, cooler_outside_vapor, 0.10
    )
    assert cooler_mix_vpd < vpd_kpa(25.0, 48.0) - 0.02

    warm_dry_outside_vapor = saturation_vapor_pressure_kpa(25.0) * 0.30
    warm_dry_mix_vpd = mixed_air_vpd_kpa(
        25.0, inside_vapor, 25.0, warm_dry_outside_vapor, 0.10
    )
    assert warm_dry_mix_vpd > vpd_kpa(25.0, 48.0) + 0.02


def test_full_air_exchange_approaches_outside_vpd():
    vapor_inside = saturation_vapor_pressure_kpa(25.0) * 0.48
    vapor_outside = saturation_vapor_pressure_kpa(22.0) * 0.50
    mixed_vpd = mixed_air_vpd_kpa(25.0, vapor_inside, 22.0, vapor_outside, 1.0)
    assert abs(mixed_vpd - vpd_kpa(22.0, 50.0)) < 0.01
