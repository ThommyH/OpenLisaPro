from copy import deepcopy

from lisa_pro_ui.controller import FanPidController
from lisa_pro_ui.config_store import ConfigStore
from lisa_pro_ui.vpd import vpd_kpa


def test_resolve_mode_day_night_silent():
    assert FanPidController._resolve_mode({"light_on": True, "silent": {}}) == "day"
    assert FanPidController._resolve_mode({"light_on": False, "silent": {"active": False}}) == "night"
    assert FanPidController._resolve_mode({"light_on": False, "silent": {"active": True}}) == "night_silent"


def test_resolve_phase_by_german_flower_label():
    phases = [
        {"id": 0, "name": "Seedling"},
        {"id": 1, "name": "Vegetative"},
        {"id": 2, "name": "Flowering"},
    ]
    phase, pid = FanPidController._resolve_phase({"grow": {"phase": "Blütephase"}}, phases)
    assert pid == 2
    assert phase["name"] == "Flowering"


def test_resolve_phase_by_name_substring():
    phases = [
        {"id": 0, "name": "Seedling"},
        {"id": 1, "name": "Vegetative"},
    ]
    phase, pid = FanPidController._resolve_phase({"grow": {"phase": "Vegetative"}}, phases)
    assert pid == 1
    assert phase["name"] == "Vegetative"


def test_vpd_targets_prefer_per_stage_override():
    phase = {
        "settings": {
            "day": {"vpd_min": 0.4, "vpd_max": 0.8},
        }
    }
    cfg = {"vpd_targets": {}}
    assert FanPidController._vpd_targets(cfg, 0, phase, "day") == (0.4, 0.8)

    cfg = {
        "vpd_targets": {
            "0": {"day": {"vpd_min": 1.0, "vpd_max": 1.2}},
        }
    }
    assert FanPidController._vpd_targets(cfg, 0, phase, "day") == (1.0, 1.2)


def test_vpd_targets_swaps_inverted_range():
    cfg = {
        "vpd_targets": {
            "1": {"night": {"vpd_min": 1.4, "vpd_max": 1.0}},
        }
    }
    assert FanPidController._vpd_targets(cfg, 1, None, "night") == (1.0, 1.4)


def test_fan_limits_prefer_override():
    cfg = {
        "fan_limits": {
            "2": {
                "night": {"fan_min": 12, "fan_max": 28},
            }
        }
    }
    phase = {"settings": {"night": {"fan_min": 10, "fan_max": 30}}}
    lim = FanPidController._fan_limits(cfg, 2, "night", phase)
    assert lim == {"fan_min": 12.0, "fan_max": 28.0}


def test_fan_limits_fallback_to_device():
    cfg = {"fan_limits": {}}
    phase = {"settings": {"day": {"fan_min": 21, "fan_max": 66}}}
    lim = FanPidController._fan_limits(cfg, 0, "day", phase)
    assert lim == {"fan_min": 21.0, "fan_max": 66.0}


def test_map_pid_to_fan_prefers_minimum_until_more_fan_is_needed():
    assert FanPidController._map_pid_to_fan(0, 20, 80, 0) == 20
    assert FanPidController._map_pid_to_fan(10, 20, 80, 1) == 30
    assert FanPidController._map_pid_to_fan(100, 20, 80, 1) == 80
    assert FanPidController._map_pid_to_fan(-100, 20, 80, -1) == 20
    assert FanPidController._map_pid_to_fan(10, 40, 40, 1) == 40


def test_controller_caps_target_using_outside_dew_point_at_inside_temperature(tmp_path):
    phase = {
        "id": 0,
        "name": "Flowering",
        "settings": {"day": {"fan_min": 0, "fan_max": 100}},
    }

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def status(self):
            return {
                "temp_c": 23.0,
                "humi_rh": 61.0,
                "dew_c": None,
                "vpd_kpa": vpd_kpa(23.0, 61.0),
                "temp_out_c": 22.0,
                "humi_out_rh": 59.0,
                "dew_out_c": None,
                "fan_pct": 46.0,
                "light_on": True,
            }

        def get_phases(self):
            return {"phases": [phase]}

        def set_phases(self, payload):
            return None

    store = ConfigStore(tmp_path / "control.json")
    store.update(
        {
            "enabled": True,
            "vpd_targets": {"0": {"day": {"vpd_min": 1.5, "vpd_max": 1.7}}},
            "fan_limits": {"0": {"day": {"fan_min": 0, "fan_max": 100}}},
            "pid": {
                "kp": 70,
                "ki": 0,
                "kd": 0,
                "ramp_pct_per_min": 20,
                "interval_s": 5,
                "deadband_kpa": 0.03,
            },
        }
    )
    controller = FanPidController(
        device_url="http://unused",
        store=store,
        client_factory=FakeClient,
    )

    controller._tick(store.get())
    state = controller.snapshot()["state"]

    assert state["vpd_target_raw"] == 1.5
    assert abs(state["vpd_target"] - 1.25) < 0.02
    assert state["vpd_reachable"] is False
    assert state["fan_ramp_target"] < 15
    assert state["fan_command"] < 46


class TemperatureProtectionClient:
    status_data = {}
    phase_data = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def status(self):
        return deepcopy(self.status_data)

    def get_phases(self):
        return {"phases": deepcopy(self.phase_data)}

    def set_phases(self, payload):
        type(self).phase_data = deepcopy(payload["phases"])


def make_temperature_controller(tmp_path):
    TemperatureProtectionClient.phase_data = [
        {
            "id": 0,
            "name": "Flowering",
            "settings": {
                "day": {"fan_min": 20, "fan_max": 80, "led": 40},
                "night": {"fan_min": 10, "fan_max": 60, "led": 0},
                "night_silent": {"fan_min": 10, "fan_max": 50, "led": 0},
            },
        }
    ]
    TemperatureProtectionClient.status_data = {
        "temp_c": 32.0,
        "humi_rh": 60.0,
        "dew_c": None,
        "temp_out_c": 20.0,
        "humi_out_rh": 50.0,
        "fan_pct": 20.0,
        "light_on": True,
        "grow": {"phase": "Flowering"},
    }
    store = ConfigStore(tmp_path / "control.json")
    store.update(
        {
            "enabled": True,
            "temperature_limits": {"0": {"max_temp_c": 30.0}},
            "fan_limits": {"0": {"day": {"fan_min": 20, "fan_max": 80}}},
            "led": {"0": {"day": 40, "night": 0}},
            "pid": {"kp": 70, "ki": 0, "kd": 0, "ramp_pct_per_min": 60, "interval_s": 5},
        }
    )
    return FanPidController(
        device_url="http://unused",
        store=store,
        client_factory=TemperatureProtectionClient,
    )


def test_over_temperature_commands_stage_max_fan(tmp_path):
    controller = make_temperature_controller(tmp_path)
    controller._tick(controller.store.get())

    state = controller.snapshot()["state"]
    assert state["temperature_active"] is True
    assert state["temperature_limit_c"] == 30.0
    assert state["fan_ramp_target"] == 80
    assert state["fan_command"] == 80
    assert state["temperature_warning"].startswith("Temperature is above")
    assert TemperatureProtectionClient.phase_data[0]["settings"]["day"]["led"] == 40


def test_hot_outside_warns_dims_led_and_restores_after_cooling(tmp_path):
    controller = make_temperature_controller(tmp_path)
    TemperatureProtectionClient.status_data["temp_out_c"] = 31.0
    controller._tick(controller.store.get())

    state = controller.snapshot()["state"]
    assert state["temperature_warning"].startswith("Outside air is 31.0°C")
    assert "cooling below the limit may not be possible" in state["temperature_warning"]
    assert state["led_override_pct"] == 30.0
    assert TemperatureProtectionClient.phase_data[0]["settings"]["day"]["led"] == 30.0
    assert controller.store.get()["temperature_led_override"]["current_pct"] == 30.0

    TemperatureProtectionClient.status_data["temp_c"] = 29.4
    # A new controller instance must recover the saved LED level after a restart.
    reloaded_store = ConfigStore(controller.store.path)
    controller = FanPidController(
        device_url="http://unused",
        store=reloaded_store,
        client_factory=TemperatureProtectionClient,
    )
    controller._tick(reloaded_store.get())
    state = controller.snapshot()["state"]
    assert state["temperature_active"] is False
    assert state["led_override_pct"] is None
    assert TemperatureProtectionClient.phase_data[0]["settings"]["day"]["led"] == 40


def test_led_dims_after_fan_max_hold_when_outside_is_cool(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("lisa_pro_ui.controller.time.time", lambda: now[0])
    controller = make_temperature_controller(tmp_path)
    TemperatureProtectionClient.status_data["fan_pct"] = 80.0
    controller._tick(controller.store.get())
    assert TemperatureProtectionClient.phase_data[0]["settings"]["day"]["led"] == 40

    now[0] += 91.0
    controller._tick(controller.store.get())
    state = controller.snapshot()["state"]
    assert state["fan_command"] == 80
    assert state["led_override_pct"] == 30.0
    assert "temporarily reduced" in state["temperature_warning"]
    assert TemperatureProtectionClient.phase_data[0]["settings"]["day"]["led"] == 30.0


def test_temperature_protection_never_dims_led_below_20_percent(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("lisa_pro_ui.controller.time.time", lambda: now[0])
    controller = make_temperature_controller(tmp_path)
    TemperatureProtectionClient.status_data["temp_out_c"] = 31.0

    for _ in range(5):
        controller._tick(controller.store.get())
        now[0] += 91.0

    assert controller.snapshot()["state"]["led_override_pct"] == 20.0
    assert TemperatureProtectionClient.phase_data[0]["settings"]["day"]["led"] == 20.0
    assert controller.store.get()["temperature_led_override"]["current_pct"] == 20.0


def test_controller_enforces_zero_led_in_both_night_modes(tmp_path):
    controller = make_temperature_controller(tmp_path)
    controller.store.update({"enabled": False, "temperature_limits": {}})
    TemperatureProtectionClient.phase_data[0]["settings"]["night"]["led"] = 35
    TemperatureProtectionClient.phase_data[0]["settings"]["night_silent"]["led"] = 45

    controller._tick(controller.store.get())

    settings = TemperatureProtectionClient.phase_data[0]["settings"]
    assert settings["night"]["led"] == 0.0
    assert settings["night_silent"]["led"] == 0.0
