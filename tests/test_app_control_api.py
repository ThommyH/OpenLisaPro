from pathlib import Path

from lisa_pro_ui.app import create_app


def test_control_get_and_post(tmp_path: Path):
    app = create_app(data_dir=tmp_path, start_controller=False)
    client = app.test_client()

    got = client.get("/api/control")
    assert got.status_code == 200
    body = got.get_json()
    assert "config" in body
    assert "state" in body
    assert body["config"]["enabled"] is False
    assert "kp" in body["config"]["pid"]

    saved = client.post(
        "/api/control",
        json={
            "enabled": False,
            "vpd_overwrite": {"enabled": True, "vpd_min": 0.95, "vpd_max": 1.15},
            "pid": {"kp": 42, "ki": 0.2, "kd": 5, "ramp_pct_per_min": 3, "interval_s": 4, "deadband_kpa": 0.02},
            "fan_limits": {
                "0": {
                    "day": {"fan_min": 20, "fan_max": 60},
                    "night": {"fan_min": 15, "fan_max": 40},
                    "night_silent": {"fan_min": 10, "fan_max": 30},
                }
            },
            "led": {"0": {"day": 45, "night": 0}},
        },
    )
    assert saved.status_code == 200
    cfg = saved.get_json()["config"]
    assert cfg["vpd_overwrite"]["vpd_min"] == 0.95
    assert cfg["pid"]["kp"] == 42.0
    assert cfg["fan_limits"]["0"]["day"]["fan_max"] == 60.0
    assert cfg["led"]["0"]["day"] == 45.0

    # Persisted on disk
    assert (tmp_path / "control.json").exists()
    again = client.get("/api/control").get_json()["config"]
    assert again["pid"]["kp"] == 42.0
    assert again["led"]["0"]["night"] == 0.0
