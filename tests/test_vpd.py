from lisa_pro_ui.vpd import saturation_vapor_pressure_kpa, vpd_kpa


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
