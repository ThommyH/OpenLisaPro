from copy import deepcopy
import time

from lisa_pro_ui.app import create_app
from lisa_pro_ui.vpd import vpd_kpa


class FakeLisaProClient:
    phases = []
    current_status = {}

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def status(self):
        return deepcopy(type(self).current_status)

    def get_phases(self):
        return {"phases": deepcopy(type(self).phases)}

    def set_phases(self, payload):
        type(self).phases = deepcopy(payload["phases"])


def test_saved_settings_update_device_and_drive_fan_calculation(tmp_path, monkeypatch):
    from lisa_pro_ui import app as app_module
    from lisa_pro_ui import controller as controller_module

    FakeLisaProClient.phases = [
        {
            "id": 0,
            "name": "Seedling",
            "schedule": {"on": "08:00", "off": "20:00"},
            "settings": {
                mode: {
                    "fan_min": 5,
                    "fan_max": 80,
                    "vpd_min": 0.4,
                    "vpd_max": 1.2,
                    "led": 35,
                }
                for mode in ("day", "day_silent", "night", "night_silent")
            },
        }
    ]
    FakeLisaProClient.current_status = {
        "temp_c": 27.0,
        "humi_rh": 70.0,
        "dew_c": None,
        "vpd_kpa": vpd_kpa(27.0, 70.0),
        "temp_out_c": 18.0,
        "humi_out_rh": 50.0,
        "dew_out_c": None,
        "fan_pct": 10.0,
        "light_on": True,
        "grow": {"phase": "Seedling"},
    }
    monkeypatch.setattr(app_module, "LisaProClient", FakeLisaProClient)
    monkeypatch.setattr(controller_module, "LisaProClient", FakeLisaProClient)
    app = create_app(device_url="http://fake-lisa", data_dir=tmp_path, start_controller=False)
    client = app.test_client()
    controller = app.config["FAN_CONTROLLER"]

    def save_settings(*, fan_min, fan_max, vpd_min, soft_max, boost, enabled=True):
        return client.post(
            "/api/control",
            json={
                "enabled": enabled,
                "fan_limits": {
                    "0": {"day": {"fan_min": fan_min, "fan_max": fan_max}}
                },
                "vpd_targets": {
                    "0": {"day": {"vpd_min": vpd_min, "vpd_max": 1.5}}
                },
                "led": {"0": {"day": 48, "day_silent": 42}},
                "phase_schedules": {"0": {"on": "06:30", "off": "19:30"}},
                "temperature_limits": {"0": {"max_temp_c": 31}},
                "temperature_soft_limits": {
                    "0": {
                        "enabled": True,
                        "max_temp_c": soft_max,
                        "max_boost_pct": boost,
                    }
                },
                "pid": {
                    "kp": 70,
                    "ki": 0,
                    "kd": 0,
                    "ramp_pct_per_min": 30,
                    "interval_s": 5,
                    "deadband_kpa": 0.03,
                    "temperature_weight_pct_per_c": 15,
                },
            },
        )

    saved = save_settings(fan_min=10, fan_max=70, vpd_min=0.4, soft_max=24, boost=30)
    assert saved.status_code == 200
    device_phase = FakeLisaProClient.phases[0]
    assert device_phase["settings"]["day"]["fan_min"] == 10.0
    assert device_phase["settings"]["day"]["fan_max"] == 70.0
    assert device_phase["settings"]["day"]["vpd_min"] == 0.4
    assert device_phase["settings"]["day"]["led"] == 48.0
    assert device_phase["schedule"] == {"on": "06:30", "off": "19:30"}

    first = client.get("/api/control").get_json()["config"]
    controller._tick(first)
    hot_state = client.get("/api/control").get_json()["state"]
    assert hot_state["temperature_soft_triggered"] is True
    assert hot_state["temperature_adjustment_pct"] == 5.0
    assert hot_state["fan_ramp_target"] > hot_state["fan_vpd_request"]

    for expected_boost in (15.0, 25.0):
        controller._soft_temperature_observed_at = time.time() - 61
        controller._soft_temperature_observed_c = 26.0
        controller._tick(controller.store.get())
        rising_state = client.get("/api/control").get_json()["state"]
        assert rising_state["temperature_soft_triggered"] is True
        assert rising_state["temperature_adjustment_pct"] == expected_boost

    # Flat temperature means the current boost has not cooled the growbox
    # below the soft limit yet, so continue increasing until capped.
    for expected_boost in (30.0, 30.0, 30.0):
        controller._soft_temperature_observed_at = time.time() - 61
        controller._soft_temperature_observed_c = 27.0
        controller._tick(controller.store.get())
        steady_state = client.get("/api/control").get_json()["state"]
        assert steady_state["temperature_soft_triggered"] is True
        assert steady_state["temperature_adjustment_pct"] == expected_boost
        assert steady_state["fan_ramp_target"] > steady_state["fan_vpd_request"]

    # Back off only after a measured decrease across the observation window.
    FakeLisaProClient.current_status["temp_c"] = 26.7
    controller._soft_temperature_observed_at = time.time() - 61
    controller._soft_temperature_observed_c = 27.0
    controller._tick(controller.store.get())
    falling_state = client.get("/api/control").get_json()["state"]
    assert falling_state["temperature_adjustment_pct"] == 20.0

    # Lowering the soft maximum creates a fresh attempt immediately, even
    # though the prior temperature observation was flat.
    saved = save_settings(fan_min=10, fan_max=70, vpd_min=0.4, soft_max=22, boost=30)
    assert saved.status_code == 200
    controller._tick(saved.get_json()["config"])
    retried = client.get("/api/control").get_json()["state"]
    assert retried["temperature_soft_triggered"] is True
    assert retried["temperature_adjustment_pct"] == 5.0

    # Raising the VPD target changes the fan request; raising the soft ceiling
    # turns off soft-temperature adjustment without disturbing the settings save.
    saved = save_settings(fan_min=10, fan_max=70, vpd_min=1.3, soft_max=30, boost=30)
    assert saved.status_code == 200
    controller._tick(saved.get_json()["config"])
    vpd_state = client.get("/api/control").get_json()["state"]
    assert vpd_state["temperature_soft_triggered"] is False
    assert vpd_state["temperature_adjustment_pct"] == 0.0
    assert vpd_state["fan_vpd_request"] > retried["fan_vpd_request"]
    assert FakeLisaProClient.phases[0]["settings"]["day"]["vpd_min"] == 1.3

    # Change the configured band and thresholds back and forth; device values
    # and the computed temperature correction follow each new saved version.
    saved = save_settings(fan_min=20, fan_max=60, vpd_min=0.4, soft_max=24, boost=30)
    assert saved.status_code == 200
    controller._tick(saved.get_json()["config"])
    restored = client.get("/api/control").get_json()["state"]
    device_settings = FakeLisaProClient.phases[0]["settings"]["day"]
    config = client.get("/api/control").get_json()["config"]
    assert config["fan_limits"]["0"]["day"] == {"fan_min": 20.0, "fan_max": 60.0}
    # PID owns the device fan fields while enabled and writes its current
    # command as fan_min=fan_max; the saved bounds remain in local config.
    assert device_settings["fan_min"] == device_settings["fan_max"] == restored["fan_command"]
    assert device_settings["vpd_min"] == 0.4
    assert restored["temperature_soft_triggered"] is True
    assert restored["temperature_adjustment_pct"] == 5.0
    assert restored["fan_vpd_request"] == 20.0
    assert restored["fan_ramp_target"] > restored["fan_vpd_request"]

    saved = save_settings(fan_min=10, fan_max=70, vpd_min=0.4, soft_max=24, boost=30)
    controller._tick(saved.get_json()["config"])
    back_again = client.get("/api/control").get_json()["state"]
    back_config = client.get("/api/control").get_json()["config"]
    assert back_config["fan_limits"]["0"]["day"] == {"fan_min": 10.0, "fan_max": 70.0}
    assert back_again["temperature_soft_triggered"] is True
    assert back_again["fan_vpd_request"] == 10.0
    assert back_again["fan_ramp_target"] > back_again["fan_vpd_request"]


def test_manual_mode_writes_direct_fan_and_led_ignoring_pid_and_temperature_limits(tmp_path, monkeypatch):
    from lisa_pro_ui import app as app_module
    from lisa_pro_ui import controller as controller_module

    FakeLisaProClient.phases = [
        {
            "id": 0,
            "name": "Seedling",
            "schedule": {"on": "00:00", "off": "23:59"},
            "settings": {
                mode: {"fan_min": 10, "fan_max": 80, "vpd_min": 0.5, "vpd_max": 1.3, "led": 20}
                for mode in ("day", "day_silent", "night", "night_silent")
            },
        }
    ]
    FakeLisaProClient.current_status = {
        "temp_c": 40.0,
        "humi_rh": 60.0,
        "dew_c": None,
        "temp_out_c": 25.0,
        "humi_out_rh": 50.0,
        "fan_pct": 80.0,
        "light_on": True,
        "grow": {"phase": "Seedling"},
    }
    monkeypatch.setattr(app_module, "LisaProClient", FakeLisaProClient)
    monkeypatch.setattr(controller_module, "LisaProClient", FakeLisaProClient)
    app = create_app(device_url="http://fake-lisa", data_dir=tmp_path, start_controller=False)
    client = app.test_client()

    saved = client.post(
        "/api/control",
        json={
            "enabled": True,
            "manual_mode": True,
            "manual_fan_pct": 37.5,
            "manual_led_pct": 63.2,
            "temperature_limits": {"0": {"max_temp_c": 25}},
        },
    )

    assert saved.status_code == 200
    cfg = saved.get_json()["config"]
    state = saved.get_json()["state"]
    device_settings = FakeLisaProClient.phases[0]["settings"][state["mode"]]
    assert cfg["manual_mode"] is True
    assert state["reason"] == "manual"
    assert state["enabled"] is False
    assert state["temperature_active"] is False
    assert state["temperature_warning"] is None
    assert state["fan_command"] == 37.5
    assert device_settings["fan_min"] == device_settings["fan_max"] == 37.5
    assert device_settings["led"] == 63.2

    changed = client.post("/api/control", json={"manual_fan_pct": 55, "manual_led_pct": 44})
    assert changed.status_code == 200
    updated_mode = changed.get_json()["state"]["mode"]
    updated_settings = FakeLisaProClient.phases[0]["settings"][updated_mode]
    assert updated_settings["fan_min"] == updated_settings["fan_max"] == 55
    assert updated_settings["led"] == 44

    automatic = client.post("/api/control", json={"manual_mode": False})
    assert automatic.status_code == 200
    app.config["FAN_CONTROLLER"]._tick(automatic.get_json()["config"])
    auto_state = client.get("/api/control").get_json()["state"]
    assert auto_state["manual_mode"] is False
    assert auto_state["temperature_active"] is True
