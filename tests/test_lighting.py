import pytest

from lisa_pro_ui.app import create_app
from lisa_pro_ui.lighting import day_length_hours, estimate_dli


@pytest.mark.parametrize(
    ("on", "off", "expected"),
    [("06:00", "18:00", 12), ("18:00", "06:00", 12), ("06:00", "06:00", 24)],
)
def test_day_length_hours_handles_normal_overnight_and_full_day(on, off, expected):
    assert day_length_hours(on, off) == expected


def test_dli_uses_datasheet_ppfd_and_scheduled_day_length():
    # Datasheet: 746 µmol m⁻² s⁻¹ at 20 cm and 100% dimming; 12 light hours.
    assert estimate_dli(200, 100, "06:00", "18:00") == pytest.approx(32.2272)


def test_dli_interpolates_distance_and_dimming_values():
    # At 50%, the datasheet gives 386 at 20 cm and 322 at 30 cm.
    # At 25 cm the estimate is 354; 12 hours gives 15.2928 mol m⁻² day⁻¹.
    assert estimate_dli(250, 50, "06:00", "18:00") == pytest.approx(15.2928)


@pytest.mark.parametrize(
    ("tof_mm", "led_pct", "on", "off"),
    [
        (199, 50, "06:00", "18:00"),
        (401, 50, "06:00", "18:00"),
        (300, 101, "06:00", "18:00"),
        (300, 50, "invalid", "18:00"),
        (None, 50, "06:00", "18:00"),
    ],
)
def test_dli_is_unavailable_outside_measured_or_valid_inputs(tof_mm, led_pct, on, off):
    assert estimate_dli(tof_mm, led_pct, on, off) is None


def test_zero_led_produces_zero_dli():
    assert estimate_dli(300, 0, "06:00", "18:00") == 0


def test_dli_estimate_api_returns_datasheet_based_value(tmp_path):
    app = create_app(data_dir=tmp_path, start_controller=False)

    response = app.test_client().post(
        "/api/dli-estimate",
        json={"tof_mm": 200, "led_pct": 100, "on": "06:00", "off": "18:00"},
    )

    assert response.status_code == 200
    assert response.get_json()["dli_mol_m2_day"] == pytest.approx(32.2272)
