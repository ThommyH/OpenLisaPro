import json
from pathlib import Path

from lisa_pro_ui.config_store import DEFAULT_PID, ConfigStore


def test_defaults_and_persist(tmp_path: Path):
    store = ConfigStore(tmp_path / "control.json")
    cfg = store.get()
    assert cfg["enabled"] is True
    assert cfg["pid"]["kp"] == DEFAULT_PID["kp"]
    assert cfg["pid"]["ramp_pct_per_min"] == 15.0

    store.update(
        {
            "enabled": True,
            "vpd_targets": {
                "2": {
                    "day": {"vpd_min": 0.9, "vpd_max": 1.1},
                    "night": {"vpd_min": 0.8, "vpd_max": 1.0},
                    "night_silent": {"vpd_min": 0.8, "vpd_max": 1.0},
                }
            },
            "pid": {"kp": 40, "ki": 0.2},
            "fan_limits": {
                "2": {
                    "day": {"fan_min": 15, "fan_max": 45},
                    "night": {"fan_min": 10, "fan_max": 30},
                    "night_silent": {"fan_min": 10, "fan_max": 25},
                }
            },
        }
    )
    reloaded = ConfigStore(tmp_path / "control.json").get()
    assert reloaded["enabled"] is True
    assert reloaded["vpd_targets"]["2"]["day"]["vpd_min"] == 0.9
    assert reloaded["pid"]["kp"] == 40.0
    assert reloaded["pid"]["ki"] == 0.2
    assert reloaded["pid"]["kd"] == DEFAULT_PID["kd"]
    assert reloaded["fan_limits"]["2"]["day"]["fan_max"] == 45.0


def test_fan_min_max_swapped_and_clamped(tmp_path: Path):
    store = ConfigStore(tmp_path / "c.json")
    store.update(
        {
            "fan_limits": {
                "0": {
                    "day": {"fan_min": 90, "fan_max": 10},
                    "night": {"fan_min": -5, "fan_max": 140},
                    "night_silent": {"fan_min": 5, "fan_max": 5},
                }
            }
        }
    )
    limits = store.get()["fan_limits"]["0"]
    assert limits["day"]["fan_min"] == 10.0
    assert limits["day"]["fan_max"] == 90.0
    assert limits["night"]["fan_min"] == 0.0
    assert limits["night"]["fan_max"] == 100.0


def test_manual_control_settings_persist_and_percentages_are_clamped(tmp_path: Path):
    store = ConfigStore(tmp_path / "control.json")
    store.update({"manual_mode": True, "manual_fan_pct": 125, "manual_led_pct": -5})

    config = ConfigStore(tmp_path / "control.json").get()

    assert config["manual_mode"] is True
    assert config["manual_fan_pct"] == 100.0
    assert config["manual_led_pct"] == 0.0


def test_seed_fan_limits_from_phases(tmp_path: Path):
    store = ConfigStore(tmp_path / "c.json")
    phases = [
        {
            "id": 0,
            "name": "Seedling",
            "settings": {
                "day": {"fan_min": 22, "fan_max": 70},
                "night": {"fan_min": 18, "fan_max": 50},
                "night_silent": {"fan_min": 8, "fan_max": 30},
            },
        }
    ]
    store.ensure_fan_limits_from_phases(phases)
    assert store.get()["fan_limits"]["0"]["day"]["fan_min"] == 22.0
    # second call must not overwrite existing
    phases[0]["settings"]["day"]["fan_min"] = 99
    store.ensure_fan_limits_from_phases(phases)
    assert store.get()["fan_limits"]["0"]["day"]["fan_min"] == 22.0


def test_migrate_per_stage_pid_to_global(tmp_path: Path):
    path = tmp_path / "control.json"
    path.write_text(
        json.dumps(
            {
                "enabled": False,
                "pid": {
                    "0": {"kp": 11, "ki": 0.5, "kd": 2},
                    "1": {"kp": 99, "ki": 1, "kd": 3},
                },
                "fan_limits": {},
                "vpd_overwrite": {"enabled": False, "vpd_min": 1.0, "vpd_max": 1.2},
            }
        ),
        encoding="utf-8",
    )
    cfg = ConfigStore(path).get()
    assert cfg["pid"]["kp"] == 11.0
    assert "0" not in cfg["pid"]


def test_update_accepts_flat_global_pid(tmp_path: Path):
    store = ConfigStore(tmp_path / "c.json")
    store.update({"pid": {"kp": 12.5, "ramp_pct_per_min": 2.5}})
    pid = store.get()["pid"]
    assert pid["kp"] == 12.5
    assert pid["ramp_pct_per_min"] == 2.5
    assert isinstance(pid.get("ki"), float)


def test_led_normalize_and_seed(tmp_path: Path):
    store = ConfigStore(tmp_path / "c.json")
    store.update({"led": {"0": {"day": 120, "night": 75, "ignored": 50}}})
    led = store.get()["led"]["0"]
    assert led["day"] == 100.0
    assert led["night"] == 0.0
    assert "ignored" not in led

    store2 = ConfigStore(tmp_path / "c2.json")
    store2.ensure_fan_limits_from_phases(
        [
            {
                "id": 1,
                "settings": {
                    "day": {"led": 35, "fan_min": 10, "fan_max": 40, "vpd_min": 0.7, "vpd_max": 1.0},
                    "night": {"led": 35, "fan_min": 5, "fan_max": 20, "vpd_min": 0.6, "vpd_max": 0.9},
                    "night_silent": {"led": 45, "fan_min": 5, "fan_max": 15, "vpd_min": 0.6, "vpd_max": 0.9},
                },
            }
        ]
    )
    assert store2.get()["led"]["1"]["day"] == 35.0
    assert store2.get()["led"]["1"]["day_silent"] == 35.0
    assert store2.get()["led"]["1"]["night"] == 0.0
    assert store2.get()["led"]["1"]["night_silent"] == 0.0
    assert store2.get()["vpd_targets"]["1"]["day"]["vpd_max"] == 1.0


def test_vpd_targets_clamp_and_swap(tmp_path: Path):
    store = ConfigStore(tmp_path / "c.json")
    store.update(
        {
            "vpd_targets": {
                "0": {
                    "day": {"vpd_min": 1.5, "vpd_max": 0.5},
                    "night": {"vpd_min": -1, "vpd_max": 9},
                }
            }
        }
    )
    day = store.get()["vpd_targets"]["0"]["day"]
    night = store.get()["vpd_targets"]["0"]["night"]
    assert day == {"vpd_min": 0.5, "vpd_max": 1.5}
    assert night["vpd_min"] == 0.0
    assert night["vpd_max"] == 2.5


def test_stage_temperature_limits_normalize_and_persist(tmp_path: Path):
    store = ConfigStore(tmp_path / "c.json")
    store.update({"temperature_limits": {"0": {"max_temp_c": 31.5}, "1": {"max_temp_c": 80}}})
    config = ConfigStore(tmp_path / "c.json").get()
    assert config["temperature_limits"] == {
        "0": {"max_temp_c": 31.5},
        "1": {"max_temp_c": 50.0},
    }

    store.update({"temperature_limits": {"0": {"max_temp_c": ""}}})
    assert store.get()["temperature_limits"] == {}


def test_soft_temperature_limits_normalize_optional_bounds_and_order(tmp_path: Path):
    store = ConfigStore(tmp_path / "c.json")
    store.update(
        {
            "temperature_soft_limits": {
                "0": {"min_temp_c": 27, "max_temp_c": 18},
                "1": {"max_temp_c": 29},
                "2": {"min_temp_c": 100},
            }
        }
    )
    assert store.get()["temperature_soft_limits"] == {
        "0": {"min_temp_c": 18.0, "max_temp_c": 27.0, "enabled": True},
        "1": {"max_temp_c": 29.0, "enabled": True},
        "2": {"min_temp_c": 50.0, "enabled": True},
    }


def test_soft_temperature_limits_keep_explicit_disabled_state(tmp_path: Path):
    store = ConfigStore(tmp_path / "c.json")
    store.update({"temperature_soft_limits": {"0": {"enabled": False, "max_temp_c": 26}}})

    assert store.get()["temperature_soft_limits"]["0"] == {"enabled": False, "max_temp_c": 26.0}


def test_soft_temperature_maximum_fan_boost_is_bounded_per_stage(tmp_path: Path):
    store = ConfigStore(tmp_path / "c.json")
    store.update(
        {
            "temperature_soft_limits": {
                "0": {"max_temp_c": 26, "max_boost_pct": 140},
                "1": {"max_boost_pct": -5},
            }
        }
    )

    limits = store.get()["temperature_soft_limits"]
    assert limits["0"]["max_boost_pct"] == 100.0
    assert limits["1"]["max_boost_pct"] == 0.0


def test_temperature_weight_defaults_and_is_bounded(tmp_path: Path):
    store = ConfigStore(tmp_path / "c.json")
    assert store.get()["pid"]["temperature_weight_pct_per_c"] == 15.0
    store.update({"pid": {"temperature_weight_pct_per_c": 100}})
    assert store.get()["pid"]["temperature_weight_pct_per_c"] == 20.0


def test_pid_default_can_be_disabled_with_environment(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LISA_PRO_PID_ENABLED", "0")
    store = ConfigStore(tmp_path / "control.json")
    assert store.get()["enabled"] is False
    store.reset()
    assert store.get()["enabled"] is False


def test_reset_stage_restores_factory_seeded_local_values_and_preserves_other_stages(tmp_path):
    store = ConfigStore(tmp_path / "control.json")
    store.update(
        {
            "fan_limits": {"0": {"day": {"fan_min": 5, "fan_max": 10}}, "1": {"day": {"fan_min": 15, "fan_max": 25}}},
            "vpd_targets": {"0": {"day": {"vpd_min": 0.2, "vpd_max": 0.3}}, "1": {"day": {"vpd_min": 0.8, "vpd_max": 1.2}}},
            "led": {"0": {"day": 10}, "1": {"day": 60}},
            "temperature_limits": {"0": {"max_temp_c": 25}, "1": {"max_temp_c": 32}},
            "temperature_soft_limits": {"0": {"min_temp_c": 18, "max_temp_c": 28}, "1": {"min_temp_c": 19}},
        }
    )

    result = store.reset_stage_from_phase(
        0,
        {"id": 0, "settings": {"day": {"fan_min": 20, "fan_max": 70, "vpd_min": 0.9, "vpd_max": 1.3, "led": 55}}},
    )

    assert result["fan_limits"]["0"]["day"] == {"fan_min": 20.0, "fan_max": 70.0}
    assert result["vpd_targets"]["0"]["day"] == {"vpd_min": 0.9, "vpd_max": 1.3}
    assert result["led"]["0"]["day"] == 55.0
    assert "0" not in result["temperature_limits"]
    assert "0" not in result["temperature_soft_limits"]
    assert result["temperature_soft_limits"]["1"] == {"min_temp_c": 19.0, "enabled": True}
    assert result["fan_limits"]["1"]["day"] == {"fan_min": 15.0, "fan_max": 25.0}
    assert result["temperature_limits"]["1"]["max_temp_c"] == 32.0


def test_reset_uses_fifteen_percent_ramp_default(tmp_path):
    store = ConfigStore(tmp_path / "control.json")
    store.update({"pid": {"ramp_pct_per_min": 3}})
    assert store.reset()["pid"]["ramp_pct_per_min"] == 15.0
