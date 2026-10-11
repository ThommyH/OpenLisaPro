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


def test_soft_temperature_high_adds_small_fan_correction_inside_vpd_band(tmp_path):
    controller = make_temperature_controller(tmp_path)
    controller.store.update(
        {
            "temperature_limits": {},
            "temperature_soft_limits": {"0": {"min_temp_c": 18, "max_temp_c": 26}},
            "vpd_targets": {"0": {"day": {"vpd_min": 0.8, "vpd_max": 1.2}}},
            "pid": {"kp": 0, "ki": 0, "kd": 0, "temperature_weight_pct_per_c": 2},
        }
    )
    TemperatureProtectionClient.status_data.update({"temp_c": 27.0, "humi_rh": 70.0})

    controller._tick(controller.store.get())

    state = controller.snapshot()["state"]
    assert 0.8 <= state["vpd_inside"] <= 1.2
    assert state["temperature_adjustment_pct"] == 2.0
    assert state["temperature_soft_active"] is True
    assert state["fan_ramp_target"] == 22


def test_soft_temperature_low_reduces_request_to_configured_fan_minimum(tmp_path):
    controller = make_temperature_controller(tmp_path)
    controller.store.update(
        {
            "temperature_limits": {},
            "temperature_soft_limits": {"0": {"min_temp_c": 18}},
            "vpd_targets": {"0": {"day": {"vpd_min": 0.8, "vpd_max": 1.2}}},
            "pid": {"kp": 0, "ki": 0, "kd": 0, "temperature_weight_pct_per_c": 2},
        }
    )
    TemperatureProtectionClient.status_data.update({"temp_c": 17.0, "humi_rh": 55.0})

    controller._tick(controller.store.get())

    state = controller.snapshot()["state"]
    assert 0.8 <= state["vpd_inside"] <= 1.2
    assert state["temperature_adjustment_pct"] == -2.0
    assert state["fan_ramp_target"] == 20


def test_soft_temperature_waits_when_vpd_outside_band_and_respects_hard_limit(tmp_path):
    controller = make_temperature_controller(tmp_path)
    controller.store.update(
        {
            "temperature_limits": {},
            "temperature_soft_limits": {"0": {"max_temp_c": 26}},
            "vpd_targets": {"0": {"day": {"vpd_min": 0.8, "vpd_max": 1.2}}},
            "pid": {"kp": 0, "ki": 0, "kd": 0, "temperature_weight_pct_per_c": 2},
        }
    )
    TemperatureProtectionClient.status_data.update({"temp_c": 27.0, "humi_rh": 35.0})
    controller._tick(controller.store.get())
    state = controller.snapshot()["state"]
    assert state["temperature_soft_active"] is False
    assert state["temperature_adjustment_pct"] == 0.0

    controller.store.update({"temperature_limits": {"0": {"max_temp_c": 25}}})
    TemperatureProtectionClient.status_data.update({"humi_rh": 70.0})
    controller._tick(controller.store.get())
    state = controller.snapshot()["state"]
    assert state["temperature_active"] is True
    assert state["temperature_adjustment_pct"] == 0.0
    assert state["fan_ramp_target"] == 80


def test_hot_outside_warns_dims_led_and_restores_after_cooling(tmp_path):
    controller = make_temperature_controller(tmp_path)
    TemperatureProtectionClient.status_data["temp_out_c"] = 31.0
    controller._tick(controller.store.get())

    state = controller.snapshot()["state"]
    assert state["temperature_warning"].startswith("Outside air is 31.0°C")
    assert "cooling below the limit may not be possible" in state["temperature_warning"]
    assert state["led_override_pct"] == 20.0
    assert TemperatureProtectionClient.phase_data[0]["settings"]["day"]["led"] == 20.0
    assert controller.store.get()["temperature_led_override"]["current_pct"] == 20.0

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


def test_temperature_protection_halves_led_output(tmp_path):
    for index, initial_pct in enumerate((50.0, 100.0)):
        controller = make_temperature_controller(tmp_path / str(index))
        controller.store.update({"led": {"0": {"day": initial_pct}}})
        TemperatureProtectionClient.phase_data[0]["settings"]["day"]["led"] = initial_pct
        TemperatureProtectionClient.status_data["temp_out_c"] = 31.0

        controller._tick(controller.store.get())

        assert TemperatureProtectionClient.phase_data[0]["settings"]["day"]["led"] == initial_pct / 2


def test_temperature_protection_dims_day_silent_led_independently(tmp_path):
    controller = make_temperature_controller(tmp_path)
    phase = TemperatureProtectionClient.phase_data[0]
    phase["settings"]["day_silent"] = {"fan_min": 20, "fan_max": 80, "led": 60}
    controller.store.update({"led": {"0": {"day": 40, "day_silent": 60}}})
    TemperatureProtectionClient.status_data.update(
        {"silent": {"active": True}, "temp_out_c": 31.0}
    )

    controller._tick(controller.store.get())

    settings = TemperatureProtectionClient.phase_data[0]["settings"]
    assert controller.snapshot()["state"]["mode"] == "day_silent"
    assert settings["day"]["led"] == 40
    assert settings["day_silent"]["led"] == 30.0


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


def test_temperature_dimming_stops_after_removing_75_percent_of_original_led(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("lisa_pro_ui.controller.time.time", lambda: now[0])
    controller = make_temperature_controller(tmp_path)
    controller.store.update({"led": {"0": {"day": 100}}})
    TemperatureProtectionClient.phase_data[0]["settings"]["day"]["led"] = 100
    TemperatureProtectionClient.status_data["temp_out_c"] = 20.0
    TemperatureProtectionClient.status_data["fan_pct"] = 80.0

    for _ in range(10):
        controller._tick(controller.store.get())
        now[0] += 91.0

    assert controller.snapshot()["state"]["led_override_pct"] == 25.0
    assert TemperatureProtectionClient.phase_data[0]["settings"]["day"]["led"] == 25.0


def test_outside_limited_led_halving_respects_75_percent_reduction_cap(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("lisa_pro_ui.controller.time.time", lambda: now[0])
    controller = make_temperature_controller(tmp_path)
    controller.store.update({"led": {"0": {"day": 100}}})
    TemperatureProtectionClient.phase_data[0]["settings"]["day"]["led"] = 100
    TemperatureProtectionClient.status_data["temp_out_c"] = 31.0

    for _ in range(4):
        controller._tick(controller.store.get())
        now[0] += 91.0

    assert controller.snapshot()["state"]["led_override_pct"] == 25.0
    assert TemperatureProtectionClient.phase_data[0]["settings"]["day"]["led"] == 25.0


def test_manual_led_change_restarts_temperature_reduction_from_new_level(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("lisa_pro_ui.controller.time.time", lambda: now[0])
    controller = make_temperature_controller(tmp_path)
    TemperatureProtectionClient.status_data["temp_out_c"] = 31.0

    controller._tick(controller.store.get())
    now[0] += 91.0
    controller._tick(controller.store.get())
    assert TemperatureProtectionClient.phase_data[0]["settings"]["day"]["led"] == 20.0

    # A manual save establishes a new maximum while the temperature limit remains active.
    controller.store.update({"led": {"0": {"day": 45.0}}})
    TemperatureProtectionClient.phase_data[0]["settings"]["day"]["led"] = 45.0
    now[0] += 91.0
    controller._tick(controller.store.get())

    assert TemperatureProtectionClient.phase_data[0]["settings"]["day"]["led"] == 22.5
    override = controller.store.get()["temperature_led_override"]
    assert override["base_pct"] == 45.0
    assert override["current_pct"] == 22.5


def test_controller_enforces_zero_led_in_both_night_modes(tmp_path):
    controller = make_temperature_controller(tmp_path)
    controller.store.update({"enabled": False, "temperature_limits": {}})
    TemperatureProtectionClient.phase_data[0]["settings"]["night"]["led"] = 35
    TemperatureProtectionClient.phase_data[0]["settings"]["night_silent"]["led"] = 45

    controller._tick(controller.store.get())

    settings = TemperatureProtectionClient.phase_data[0]["settings"]
    assert settings["night"]["led"] == 0.0
    assert settings["night_silent"]["led"] == 0.0


def test_fan_command_preserves_newer_manual_led_setting(tmp_path):
    controller = make_temperature_controller(tmp_path)
    stale_phases = deepcopy(TemperatureProtectionClient.phase_data)
    # A UI LED save can land after the controller read its phase snapshot.
    TemperatureProtectionClient.phase_data[0]["settings"]["day"]["led"] = 23.0

    controller._apply_fan_band(stale_phases, 0, "day", 25, 70)

    settings = TemperatureProtectionClient.phase_data[0]["settings"]["day"]
    assert settings["led"] == 23.0
    assert settings["fan_min"] == 25
    assert settings["fan_max"] == 70
