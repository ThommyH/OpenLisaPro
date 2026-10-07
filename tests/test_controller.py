from lisa_pro_ui.controller import FanPidController


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


def test_vpd_targets_overwrite_vs_phase():
    phase = {
        "settings": {
            "day": {"vpd_min": 0.4, "vpd_max": 0.8},
        }
    }
    cfg = {"vpd_overwrite": {"enabled": False}}
    assert FanPidController._vpd_targets(cfg, phase, "day") == (0.4, 0.8)

    cfg = {"vpd_overwrite": {"enabled": True, "vpd_min": 1.0, "vpd_max": 1.2}}
    assert FanPidController._vpd_targets(cfg, phase, "day") == (1.0, 1.2)


def test_vpd_targets_swaps_inverted_range():
    cfg = {"vpd_overwrite": {"enabled": True, "vpd_min": 1.4, "vpd_max": 1.0}}
    assert FanPidController._vpd_targets(cfg, None, "day") == (1.0, 1.4)


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


def test_map_pid_to_fan_clamps_to_band():
    assert FanPidController._map_pid_to_fan(0, 20, 80, 0) == 50
    assert FanPidController._map_pid_to_fan(100, 20, 80, 1) == 80
    assert FanPidController._map_pid_to_fan(-100, 20, 80, -1) == 20
    assert FanPidController._map_pid_to_fan(10, 40, 40, 1) == 40
