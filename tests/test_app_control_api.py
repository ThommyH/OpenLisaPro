import json
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
    assert body["config"]["enabled"] is True
    assert "kp" in body["config"]["pid"]

    saved = client.post(
        "/api/control",
        json={
            "enabled": False,
            "vpd_targets": {
                "0": {
                    "day": {"vpd_min": 0.95, "vpd_max": 1.15},
                    "night": {"vpd_min": 0.9, "vpd_max": 1.1},
                    "night_silent": {"vpd_min": 0.9, "vpd_max": 1.1},
                }
            },
            "pid": {"kp": 42, "ki": 0.2, "kd": 5, "ramp_pct_per_min": 3, "interval_s": 4, "deadband_kpa": 0.02},
            "fan_limits": {
                "0": {
                    "day": {"fan_min": 20, "fan_max": 60},
                    "night": {"fan_min": 15, "fan_max": 40},
                    "night_silent": {"fan_min": 10, "fan_max": 30},
                }
            },
            "led": {"0": {"day": 45, "night": 0}},
            "temperature_limits": {"0": {"max_temp_c": 29.5}},
        },
    )
    assert saved.status_code == 200
    cfg = saved.get_json()["config"]
    assert cfg["vpd_targets"]["0"]["day"]["vpd_min"] == 0.95
    assert cfg["pid"]["kp"] == 42.0
    assert cfg["fan_limits"]["0"]["day"]["fan_max"] == 60.0
    assert cfg["led"]["0"]["day"] == 45.0
    assert cfg["temperature_limits"]["0"]["max_temp_c"] == 29.5

    # Persisted on disk
    assert (tmp_path / "control.json").exists()
    again = client.get("/api/control").get_json()["config"]
    assert again["pid"]["kp"] == 42.0
    assert again["led"]["0"]["night"] == 0.0
    assert again["vpd_targets"]["0"]["night"]["vpd_max"] == 1.1
    assert again["temperature_limits"]["0"]["max_temp_c"] == 29.5


def test_saving_day_led_forces_night_and_night_silent_to_zero(tmp_path, monkeypatch):
    from lisa_pro_ui import app as app_module

    class FakeClient:
        phases = [
            {
                "id": 0,
                "settings": {
                    "day": {"led": 20},
                    "night": {"led": 50},
                    "night_silent": {"led": 60},
                },
            }
        ]

        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get_phases(self):
            return {"phases": self.phases}

        def set_phases(self, payload):
            type(self).phases = payload["phases"]

    monkeypatch.setattr(app_module, "LisaProClient", FakeClient)
    app = create_app(data_dir=tmp_path, start_controller=False)
    response = app.test_client().post("/api/control", json={"led": {"0": {"day": 42}}})

    assert response.status_code == 200
    settings = FakeClient.phases[0]["settings"]
    assert settings["day"]["led"] == 42.0
    assert settings["night"]["led"] == 0.0
    assert settings["night_silent"]["led"] == 0.0
    persisted = json.loads((tmp_path / "control.json").read_text())
    assert persisted["led"]["0"]["day"] == 42.0


def test_reset_stage_restores_factory_phase_and_keeps_other_phase(tmp_path, monkeypatch):
    from copy import deepcopy
    from lisa_pro_ui import app as app_module

    factory = [
        {"id": 0, "settings": {"day": {"fan_min": 20, "fan_max": 70, "vpd_min": 0.9, "vpd_max": 1.3, "led": 55}}},
        {"id": 1, "settings": {"day": {"fan_min": 10, "fan_max": 40, "led": 25}}},
    ]

    class FakeClient:
        phases = [
            {"id": 0, "settings": {"day": {"fan_min": 2, "fan_max": 5, "led": 10}}},
            {"id": 1, "settings": {"day": {"fan_min": 12, "fan_max": 35, "led": 30}}},
        ]

        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get_phases(self):
            return {"phases": deepcopy(type(self).phases)}

        def reset_phases(self):
            type(self).phases = deepcopy(factory)

        def set_phases(self, payload):
            type(self).phases = deepcopy(payload["phases"])

    monkeypatch.setattr(app_module, "LisaProClient", FakeClient)
    app = create_app(data_dir=tmp_path, start_controller=False)
    store = app.config["CONTROL_STORE"]
    store.update({"fan_limits": {"0": {"day": {"fan_min": 2, "fan_max": 5}}}, "temperature_limits": {"0": {"max_temp_c": 28}}})

    response = app.test_client().post("/api/control/reset-stage", json={"phase_id": 0})

    assert response.status_code == 200
    body = response.get_json()
    assert FakeClient.phases[0] == factory[0]
    assert FakeClient.phases[1] == {"id": 1, "settings": {"day": {"fan_min": 12, "fan_max": 35, "led": 30}}}
    assert body["config"]["fan_limits"]["0"]["day"] == {"fan_min": 20.0, "fan_max": 70.0}
    assert "0" not in body["config"]["temperature_limits"]
