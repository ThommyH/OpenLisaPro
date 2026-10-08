import json
from pathlib import Path

from lisa_pro_ui.config_store import DEFAULT_PID, ConfigStore


def test_defaults_and_persist(tmp_path: Path):
    store = ConfigStore(tmp_path / "control.json")
    cfg = store.get()
    assert cfg["enabled"] is False
    assert cfg["pid"]["kp"] == DEFAULT_PID["kp"]

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
    store.update({"led": {"0": {"day": 120, "night": -5, "ignored": 50}}})
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
                    "night": {"led": 0, "fan_min": 5, "fan_max": 20, "vpd_min": 0.6, "vpd_max": 0.9},
                    "night_silent": {"led": 0, "fan_min": 5, "fan_max": 15, "vpd_min": 0.6, "vpd_max": 0.9},
                },
            }
        ]
    )
    assert store2.get()["led"]["1"]["day"] == 35.0
    assert store2.get()["led"]["1"]["night"] == 0.0
    assert "night_silent" not in store2.get()["led"]["1"]
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
